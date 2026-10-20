"""Inventor COM worker. One process per call keeps COM apartment ownership explicit."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import gc
import json
import math
from pathlib import Path
import re
import sys
import uuid

import pythoncom
import pywintypes
import win32api
import win32event
import win32com.client as com
from win32com.client import dynamic

ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "models"
PREVIEWS = ROOT / "previews"
PARAMETERS = {
    "length_mm": "BridgeLength",
    "width_mm": "BridgeWidth",
    "thickness_mm": "BridgeThickness",
    "hole_diameter_mm": "BridgeHoleDiameter",
}


def safe_name(name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[\w\-]{1,80}", name):
        raise ValueError("名称只允许 1–80 个字母、数字、中文、下划线或连字符。")
    if re.fullmatch(r"(?i:con|prn|aux|nul|com[0-9]|lpt[0-9])", name):
        raise ValueError("不能使用 Windows 保留文件名。")
    return name


def managed_path(filename: str) -> Path:
    path = (MODELS / filename).resolve()
    if not path.is_relative_to(MODELS.resolve()) or path.suffix.lower() != ".ipt":
        raise ValueError("只能打开或修改本项目 models 文件夹中的 .ipt 文件。")
    if not path.is_file():
        raise FileNotFoundError(f"模型不存在：{path}")
    return path


def managed_assembly_path(filename: str) -> Path:
    if not isinstance(filename, str) or not filename:
        raise ValueError("装配文件名必须是 models 文件夹内的相对 .iam 路径。")
    relative = Path(filename)
    if relative.is_absolute() or relative.drive:
        raise ValueError("装配文件名必须是 models 文件夹内的相对 .iam 路径。")
    path = (MODELS / relative).resolve()
    if not path.is_relative_to(MODELS.resolve()) or path.suffix.lower() != ".iam":
        raise ValueError("只能打开本项目 models 文件夹中的 .iam 装配文件。")
    if not path.is_file():
        raise FileNotFoundError(f"装配模型不存在：{path}")
    return path


def quickchanger_arguments(arguments: dict) -> dict:
    result = {"name": safe_name(arguments["name"])}
    for field, default in (
        ("body_width_mm", 120),
        ("body_depth_mm", 110),
        ("body_height_mm", 40),
    ):
        value = arguments.get(field, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{field} 必须是数字。")
        value = float(value)
        if not math.isfinite(value) or not 0 < value <= 10000:
            raise ValueError(f"{field} 必须大于 0 且不超过 10000 毫米。")
        result[field] = value
    reference = arguments.get("reference_image")
    if reference is not None:
        if not isinstance(reference, str):
            raise ValueError("参考图片路径必须是字符串。")
        image_path = Path(reference).resolve()
        if not image_path.is_file() or image_path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            raise ValueError("参考图片路径必须指向现有 PNG、JPEG 或 WebP 文件。")
        result["reference_image"] = str(image_path)
    return result


def validate_plate(values: dict) -> dict[str, float]:
    required = set(PARAMETERS)
    if set(values) != required:
        raise ValueError(f"尺寸必须包含且仅包含：{', '.join(sorted(required))}")
    result = {}
    for name, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} 必须是数字。")
        value = float(value)
        if not math.isfinite(value) or not 0 < value <= 10000:
            raise ValueError(f"{name} 必须大于 0 且不超过 10000 毫米。")
        result[name] = value
    if result["hole_diameter_mm"] >= min(result["length_mm"], result["width_mm"]):
        raise ValueError("孔径必须小于板的长和宽。")
    return result


@contextmanager
def session(launch: bool = False):
    mutex = win32event.CreateMutex(None, False, "Local\\InventorBridge_COM")
    wait = win32event.WaitForSingleObject(mutex, 0)
    if wait not in (win32event.WAIT_OBJECT_0, win32event.WAIT_ABANDONED):
        win32api.CloseHandle(mutex)
        raise RuntimeError("另一个建模任务正在执行，请等待完成后再调用。")
    pythoncom.CoInitialize()
    app = None
    try:
        try:
            raw = pythoncom.GetActiveObject("Inventor.Application")
        except pywintypes.com_error as error:
            if not launch:
                raise RuntimeError("无法连接运行中的 Inventor。请先打开软件，并确保接口与软件运行在同一用户会话和权限级别。") from error
            raw = pythoncom.CoCreateInstance(
                pywintypes.IID("Inventor.Application"), None,
                pythoncom.CLSCTX_LOCAL_SERVER, pythoncom.IID_IDispatch,
            )
        # Query IDispatch explicitly: the ProgID coclass wrapper can recurse in pywin32.
        app = com.gencache.EnsureDispatch(dynamic.Dispatch(raw.QueryInterface(pythoncom.IID_IDispatch)))
        if launch:
            app.Visible = True
        yield app
    finally:
        app = None
        gc.collect()
        pythoncom.CoUninitialize()
        win32event.ReleaseMutex(mutex)
        win32api.CloseHandle(mutex)


def doc_for_path(app, path: Path):
    for index in range(1, app.Documents.Count + 1):
        doc = app.Documents.Item(index)
        if doc.FullFileName and Path(doc.FullFileName).resolve() == path:
            return com.CastTo(doc, "PartDocument")
    return com.CastTo(app.Documents.Open(str(path), True), "PartDocument")


def inspect_part(doc) -> dict:
    definition = doc.ComponentDefinition
    bounds = definition.RangeBox
    lengths = [
        (bounds.MaxPoint.X - bounds.MinPoint.X) * 10,
        (bounds.MaxPoint.Y - bounds.MinPoint.Y) * 10,
        (bounds.MaxPoint.Z - bounds.MinPoint.Z) * 10,
    ]
    params = {}
    for field, name in PARAMETERS.items():
        try:
            param = definition.Parameters.UserParameters.Item(name)
            params[field] = round(float(param.Value) * 10, 6)
        except pywintypes.com_error:
            pass
    return {
        "filename": Path(doc.FullFileName).name if doc.FullFileName else None,
        "path": doc.FullFileName or None,
        "display_name": doc.DisplayName,
        "dirty": bool(doc.Dirty),
        "parameters_mm": params,
        "bounding_box_mm": dict(zip(("x", "y", "z"), (round(x, 6) for x in lengths))),
        "volume_mm3": round(float(definition.MassProperties.Volume) * 1000, 6),
        "solid_bodies": int(definition.SurfaceBodies.Count),
        "sketches": int(definition.Sketches.Count),
        "extrude_features": int(definition.Features.ExtrudeFeatures.Count),
    }


def verify_plate(doc, values: dict) -> dict:
    info = inspect_part(doc)
    expected_bounds = [values["length_mm"], values["width_mm"], values["thickness_mm"]]
    expected_volume = (
        values["length_mm"] * values["width_mm"]
        - math.pi * (values["hole_diameter_mm"] / 2) ** 2
    ) * values["thickness_mm"]
    passed = (
        info["solid_bodies"] == 1
        and all(math.isclose(actual, expected, abs_tol=0.001)
                for actual, expected in zip(info["bounding_box_mm"].values(), expected_bounds))
        and math.isclose(info["volume_mm3"], expected_volume, rel_tol=1e-6, abs_tol=0.01)
        and all(math.isclose(info["parameters_mm"].get(key, -1), value, abs_tol=0.001)
                for key, value in values.items())
    )
    info["verification"] = {
        "passed": passed,
        "expected_volume_mm3": round(expected_volume, 6),
        "checks": ["bounding_box", "volume_including_through_hole", "one_solid_body", "user_parameters"],
    }
    if not passed:
        raise RuntimeError(f"模型核验失败：{json.dumps(info, ensure_ascii=False)}")
    return info


def capture_preview(app, name: str) -> str:
    PREVIEWS.mkdir(exist_ok=True)
    path = (PREVIEWS / f"{safe_name(name)}_{uuid.uuid4().hex[:8]}.png").resolve()
    if not path.is_relative_to(PREVIEWS.resolve()):
        raise ValueError("预览目录无效。")
    camera = app.ActiveView.Camera
    camera.ViewOrientationType = com.constants.kIsoTopRightViewOrientation
    camera.Fit()
    camera.Apply()
    app.ActiveView.Update()
    app.ActiveView.SaveAsBitmap(str(path), 1200, 800)
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError("Inventor 没有生成预览图。")
    return str(path)


def create_plate(app, name: str, values: dict) -> dict:
    name = safe_name(name)
    values = validate_plate(values)
    MODELS.mkdir(exist_ok=True)
    path = (MODELS / f"{name}.ipt").resolve()
    if not path.is_relative_to(MODELS.resolve()):
        raise ValueError("模型目录无效。")
    if path.exists():
        raise FileExistsError("同名模型已存在，请使用新名称。不会覆盖原文件。")
    template = app.FileManager.GetTemplateFile(com.constants.kPartDocumentObject)
    doc = com.CastTo(app.Documents.Add(com.constants.kPartDocumentObject, template, True), "PartDocument")
    transaction = app.TransactionManager.StartTransaction(doc, "InventorBridge: create parametric plate")
    try:
        doc.UnitsOfMeasure.LengthUnits = com.constants.kMillimeterLengthUnits
        definition = doc.ComponentDefinition
        params = definition.Parameters.UserParameters
        for field, parameter in PARAMETERS.items():
            params.AddByExpression(parameter, f"{values[field]:g} mm", com.constants.kMillimeterLengthUnits)
        geometry = app.TransientGeometry
        length, width, thickness = (values[key] / 10 for key in ("length_mm", "width_mm", "thickness_mm"))
        sketch = definition.Sketches.Add(definition.WorkPlanes.Item(3))
        sketch.Name = "BridgePlateSketch"
        lines = sketch.SketchLines.AddAsTwoPointRectangle(
            geometry.CreatePoint2d(0, 0), geometry.CreatePoint2d(length, width)
        )
        # Ground one corner, then drive the rectangle with named dimensions.
        origin = com.CastTo(lines.Item(1), "SketchLine").StartSketchPoint
        sketch.GeometricConstraints.AddGround(origin)
        for line_index, expression, text_point in (
            (1, "BridgeLength", (length / 2, -0.8)),
            (2, "BridgeWidth", (length + 0.8, width / 2)),
        ):
            line = com.CastTo(lines.Item(line_index), "SketchLine")
            dim = sketch.DimensionConstraints.AddTwoPointDistance(
                line.StartSketchPoint, line.EndSketchPoint, com.constants.kAlignedDim,
                geometry.CreatePoint2d(*text_point), False,
            )
            dim.Parameter.Expression = expression
        extrudes = definition.Features.ExtrudeFeatures
        extrusion = extrudes.CreateExtrudeDefinition(sketch.Profiles.AddForSolid(), com.constants.kJoinOperation)
        extrusion.SetDistanceExtent("BridgeThickness", com.constants.kPositiveExtentDirection)
        feature = extrudes.Add(extrusion)
        feature.Name = "BridgePlateExtrusion"
        sketch.Visible = False

        hole_sketch = definition.Sketches.Add(definition.WorkPlanes.Item(3))
        hole_sketch.Name = "BridgeHoleSketch"
        reference = hole_sketch.SketchPoints.Add(geometry.CreatePoint2d(0, 0), True)
        hole_sketch.GeometricConstraints.AddGround(reference)
        circle = hole_sketch.SketchCircles.AddByCenterRadius(
            geometry.CreatePoint2d(length / 2, width / 2), values["hole_diameter_mm"] / 20
        )
        for orientation, expression, text_point in (
            (com.constants.kHorizontalDim, "BridgeLength / 2", (length / 4, -0.5)),
            (com.constants.kVerticalDim, "BridgeWidth / 2", (-0.5, width / 4)),
        ):
            dim = hole_sketch.DimensionConstraints.AddTwoPointDistance(
                reference, circle.CenterSketchPoint, orientation,
                geometry.CreatePoint2d(*text_point), False,
            )
            dim.Parameter.Expression = expression
        diameter = hole_sketch.DimensionConstraints.AddDiameter(
            circle, geometry.CreatePoint2d(length / 2 + 1, width / 2 + 1), False
        )
        diameter.Parameter.Expression = "BridgeHoleDiameter"
        cut = extrudes.CreateExtrudeDefinition(hole_sketch.Profiles.AddForSolid(), com.constants.kCutOperation)
        cut.SetThroughAllExtent(com.constants.kPositiveExtentDirection)
        hole_feature = extrudes.Add(cut)
        hole_feature.Name = "BridgeCenterThroughHole"
        hole_sketch.Visible = False
        if not doc.Update2(False):
            raise RuntimeError("Inventor 报告模型更新失败。")
        verify_plate(doc, values)
        transaction.End()
    except Exception:
        transaction.Abort()
        # Only the new empty document from this failed call is closed.
        doc.Close(True)
        raise
    doc.SaveAs(str(path), False)
    result = verify_plate(doc, values)
    try:
        result["preview_path"] = capture_preview(app, name)
        doc.Save2(False)
        result["dirty"] = bool(doc.Dirty)
    except Exception as error:
        result["preview_warning"] = str(error)
    return result


def update_plate(app, filename: str, name: str, values: dict) -> dict:
    values = validate_plate(values)
    source = managed_path(filename)
    name = safe_name(name)
    output = (MODELS / f"{name}.ipt").resolve()
    if not output.is_relative_to(MODELS.resolve()):
        raise ValueError("模型目录无效。")
    if output.exists():
        raise FileExistsError("新名称已存在；修改结果必须另存为新文件。")
    doc = doc_for_path(app, source)
    if doc.Dirty:
        raise RuntimeError("原模型有未保存的修改，请先处理后再调用参数修改。")
    params = doc.ComponentDefinition.Parameters.UserParameters
    for name_in_model in PARAMETERS.values():
        params.Item(name_in_model)  # Reject unrelated models before mutation.
    transaction = app.TransactionManager.StartTransaction(doc, "InventorBridge: update plate")
    try:
        for field, name_in_model in PARAMETERS.items():
            params.Item(name_in_model).Expression = f"{values[field]:g} mm"
        if not doc.Update2(False):
            raise RuntimeError("Inventor 报告参数修改失败。")
        verify_plate(doc, values)
        transaction.End()
    except Exception:
        transaction.Abort()
        raise
    doc.SaveAs(str(output), False)
    result = verify_plate(doc, values)
    result["source_path"] = str(source)
    try:
        result["preview_path"] = capture_preview(app, name)
        doc.Save2(False)
        result["dirty"] = bool(doc.Dirty)
    except Exception as error:
        result["preview_warning"] = str(error)
    return result


def dispatch(action: str, arguments: dict) -> dict:
    with session(launch=action == "launch") as app:
        if action in ("status", "launch"):
            documents = []
            for index in range(1, app.Documents.Count + 1):
                doc = app.Documents.Item(index)
                documents.append({"display_name": doc.DisplayName, "path": doc.FullFileName or None, "dirty": bool(doc.Dirty)})
            return {"connected": True, "version": app.SoftwareVersion.DisplayVersion, "visible": bool(app.Visible), "documents": documents, "project_root": str(ROOT)}
        if action == "create_plate":
            return create_plate(app, arguments["name"], {key: arguments[key] for key in PARAMETERS})
        if action == "update_plate":
            return update_plate(app, arguments["filename"], arguments["new_name"], {key: arguments[key] for key in PARAMETERS})
        if action == "create_quickchanger":
            from quickchanger_model import build_quickchanger

            return build_quickchanger(app, quickchanger_arguments(arguments))
        if action in ("open_assembly", "inspect_assembly", "assembly_preview", "assembly_explode"):
            path = managed_assembly_path(arguments["filename"])
            from quickchanger_model import assembly_action

            return assembly_action(app, action, {"filename": str(path.relative_to(MODELS.resolve()))})
        if action in ("open_model", "inspect_model", "preview"):
            path = managed_path(arguments["filename"])
            doc = doc_for_path(app, path)
            if action in ("open_model", "preview"):
                doc.Activate()
            result = inspect_part(doc)
            if action == "preview":
                result["preview_path"] = capture_preview(app, path.stem)
            return result
        raise ValueError(f"未知操作：{action}")


def main():
    parser = argparse.ArgumentParser(description="InventorBridge bounded COM worker")
    parser.add_argument("action", choices=[
        "status", "launch", "create_plate", "update_plate", "open_model", "inspect_model", "preview",
        "create_quickchanger", "open_assembly", "inspect_assembly", "assembly_preview", "assembly_explode",
    ])
    parser.add_argument("--json", default="{}")
    parser.add_argument("--input", type=Path)
    options = parser.parse_args()
    request_id = uuid.uuid4().hex
    try:
        arguments = json.loads(options.input.read_text(encoding="utf-8") if options.input else options.json)
        if not isinstance(arguments, dict):
            raise ValueError("参数必须是 JSON 对象。")
        result = {"ok": True, "action": options.action, "request_id": request_id, "result": dispatch(options.action, arguments)}
        code = 0
    except Exception as error:
        result = {"ok": False, "action": options.action, "request_id": request_id, "error": str(error), "error_type": type(error).__name__}
        code = 1
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    (log_dir / f"{request_id}.json").write_text(
        json.dumps({"time_utc": datetime.now(timezone.utc).isoformat(), **result}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return code


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
