"""Native Inventor concept assembly derived from the user's V3 render.

Creates sketches, extrusions, holes and a coil feature, never a mesh or image proxy.
Manufacturing tolerances, release kinematics and load capacity are not certified.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import sys
import time

import win32com.client as com

ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "models"
IDENTITY = ((1, 0, 0), (0, 1, 0), (0, 0, 1))


def progress(message):
    print(message, file=sys.stderr, flush=True)


def rr(width, depth, radius, cx=0, cy=0):
    return {"type": "rounded_rectangle", "width": width, "depth": depth, "radius": radius, "cx": cx, "cy": cy}


def circle(diameter, cx=0, cy=0):
    return {"type": "circle", "diameter": diameter, "cx": cx, "cy": cy}


def op(name, profiles, height, z=0, cut=False, origin=None, axes=None):
    return {"name": name, "profiles": profiles, "height": height, "origin": origin or (0, 0, z), "axes": axes or IDENTITY[:2], "cut": cut}


def cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def plane_sketch(app, doc, name, origin, axes):
    g = app.TransientGeometry
    plane = doc.ComponentDefinition.WorkPlanes.AddFixed(
        g.CreatePoint(*(value / 10 for value in origin)),
        g.CreateUnitVector(*axes[0]), g.CreateUnitVector(*axes[1]), False,
    )
    plane.Name = name + "_Plane"
    plane.Visible = False
    sketch = doc.ComponentDefinition.Sketches.Add(plane)
    sketch.Name = name + "_Sketch"
    return sketch


def add_profile(app, sketch, shape, doc, frame):
    g = app.TransientGeometry
    origin, axes = frame["origin"], frame["axes"]
    def project(x, y):
        point = tuple((origin[j] + axes[0][j]*x + axes[1][j]*y)/10 for j in range(3))
        return sketch.ModelToSketchSpace(g.CreatePoint(*point))
    zero, unit_x, unit_y = project(0,0), project(1,0), project(0,1)
    ccw = (unit_x.X-zero.X)*(unit_y.Y-zero.Y)-(unit_x.Y-zero.Y)*(unit_y.X-zero.X) > 0
    if shape["type"] == "circle":
        c = sketch.SketchCircles.AddByCenterRadius(
            project(shape["cx"],shape["cy"]), shape["diameter"] / 20,
        )
        dimension = sketch.DimensionConstraints.AddDiameter(
            c, project(shape["cx"] + shape["diameter"],shape["cy"]), False,
        )
        dimension.Parameter.Expression = f"{shape['diameter']:.8g} mm"
        return
    a, b = shape["width"] / 2, shape["depth"] / 2
    r = min(shape["radius"], a * 0.9, b * 0.9)
    cx, cy = shape["cx"], shape["cy"]
    def p(x, y):
        return project(x+cx,y+cy)
    if r <= 0:
        sketch.SketchLines.AddAsTwoPointRectangle(p(-a, -b), p(a, b))
        return
    first = sketch.SketchLines.AddByTwoPoints(p(-a+r, -b), p(a-r, -b))
    arc = sketch.SketchArcs.AddByCenterStartEndPoint(p(a-r, -b+r), first.EndSketchPoint, p(a, -b+r), ccw)
    line = sketch.SketchLines.AddByTwoPoints(arc.EndSketchPoint, p(a, b-r))
    arc = sketch.SketchArcs.AddByCenterStartEndPoint(p(a-r, b-r), line.EndSketchPoint, p(a-r, b), ccw)
    line = sketch.SketchLines.AddByTwoPoints(arc.EndSketchPoint, p(-a+r, b))
    arc = sketch.SketchArcs.AddByCenterStartEndPoint(p(-a+r, b-r), line.EndSketchPoint, p(-a, b-r), ccw)
    line = sketch.SketchLines.AddByTwoPoints(arc.EndSketchPoint, p(-a, -b+r))
    sketch.SketchArcs.AddByCenterStartEndPoint(p(-a+r, -b+r), line.EndSketchPoint, first.StartSketchPoint, ccw)


def appearance(app, doc, name, rgb):
    asset = doc.Assets.Add(com.constants.kAssetTypeAppearance, "Generic", "QC_" + name, name)
    color = com.CastTo(asset.Item("generic_diffuse"), "ColorAssetValue")
    color.Value = app.TransientObjects.CreateColor(*rgb)
    doc.ActiveAppearance = asset


def component_stats(doc):
    definition = doc.ComponentDefinition
    box = definition.RangeBox
    return {
        "file": Path(doc.FullFileName).name,
        "native_sketches": definition.Sketches.Count,
        "extrusions": definition.Features.ExtrudeFeatures.Count,
        "coil_features": definition.Features.CoilFeatures.Count,
        "solid_bodies": definition.SurfaceBodies.Count,
        "volume_mm3": float(definition.MassProperties.Volume) * 1000,
        "bounds_mm": [round((getattr(box.MaxPoint, axis)-getattr(box.MinPoint, axis))*10, 5) for axis in ("X", "Y", "Z")],
    }


def make_part(app, folder, key, spec, resume=False):
    path = folder / (key + ".ipt")
    digest = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
    metadata = folder / (key + ".build.json")
    if path.exists():
        if not resume or not metadata.exists() or json.loads(metadata.read_text(encoding="utf-8"))["digest"] != digest:
            raise FileExistsError(f"已有零件不能覆盖：{path}")
        doc = com.CastTo(app.Documents.Open(str(path), False), "PartDocument")
        result = component_stats(doc)
        doc.Close(True)
        return path, result
    template = app.FileManager.GetTemplateFile(com.constants.kPartDocumentObject)
    doc = com.CastTo(app.Documents.Add(com.constants.kPartDocumentObject, template, False), "PartDocument")
    transaction = app.TransactionManager.StartTransaction(doc, "V3 quickchanger: " + key)
    try:
        doc.UnitsOfMeasure.LengthUnits = com.constants.kMillimeterLengthUnits
        params = doc.ComponentDefinition.Parameters.UserParameters
        for name, value in spec.get("parameters", {}).items():
            params.AddByExpression(name, f"{value:.8g} mm", com.constants.kMillimeterLengthUnits)
        for index, operation in enumerate(spec.get("operations", []), 1):
            sketch = plane_sketch(app, doc, operation["name"], operation["origin"], operation["axes"])
            for shape in operation["profiles"]:
                add_profile(app, sketch, shape, doc, operation)
            extrusion = doc.ComponentDefinition.Features.ExtrudeFeatures.CreateExtrudeDefinition(
                sketch.Profiles.AddForSolid(), com.constants.kCutOperation if operation["cut"] else com.constants.kJoinOperation,
            )
            parameter_name = f"FeatureDepth_{index}"
            params.AddByExpression(parameter_name, f"{operation['height']:.8g} mm", com.constants.kMillimeterLengthUnits)
            extrusion.SetDistanceExtent(parameter_name, com.constants.kPositiveExtentDirection)
            feature = doc.ComponentDefinition.Features.ExtrudeFeatures.Add(extrusion)
            feature.Name = operation["name"]
            sketch.Visible = False
        if spec.get("spring"):
            spring = spec["spring"]
            g = app.TransientGeometry
            definition = doc.ComponentDefinition
            sketch = definition.Sketches.Add(definition.WorkPlanes.Item(2))
            sketch.Name = "SpringWireProfile"
            center = sketch.ModelToSketchSpace(g.CreatePoint((spring["diameter"]-spring["wire"])/20, 0, spring["wire"]/20))
            sketch.SketchCircles.AddByCenterRadius(center, spring["wire"] / 20)
            coil = definition.Features.CoilFeatures.AddByRevolutionAndHeight(
                sketch.Profiles.AddForSolid(), definition.WorkAxes.Item(3), spring["turns"],
                f"{spring['length'] - spring['wire']:.8g} mm", com.constants.kJoinOperation, False, False,
            )
            coil.Name = "CaptiveCompressionSpring"
            sketch.Visible = False
        appearance(app, doc, key, spec["color"])
        if not doc.Update2(False):
            raise RuntimeError("Inventor 更新失败：" + key)
        if doc.ComponentDefinition.SurfaceBodies.Count != 1:
            raise RuntimeError("零件必须是一个有效实体：" + key)
        if doc.ComponentDefinition.MassProperties.Volume <= 0:
            raise RuntimeError("零件体积无效：" + key)
        transaction.End()
        doc.SaveAs(str(path), False)
        result = component_stats(doc)
        metadata.write_text(json.dumps({"digest": digest, "stats": result}, indent=2), encoding="utf-8")
        doc.Close(True)
        return path, result
    except Exception:
        transaction.Abort()
        doc.Close(True)
        raise


def transform(app, origin=(0, 0, 0), axes=IDENTITY):
    g = app.TransientGeometry
    matrix = g.CreateMatrix()
    matrix.SetCoordinateSystem(g.CreatePoint(*(value / 10 for value in origin)), *(g.CreateVector(*axis) for axis in axes))
    return matrix


def spec_for(width, depth, height):
    s = min(width / 120, depth / 110)
    hs = height / 40
    radius = 15 * s
    iw, id_ = width - 34*s, depth - 34*s
    mw, md = iw - 0.4*s, id_ - 0.4*s
    wall = 2.4*s
    base, top, cover = 5*hs, height, 4*hs
    sleeve_h, cavity_h = height-base, height-base-6*hs
    pole_z, lock_z = 20*hs, 25*hs
    side_placements = []
    for normal, tangent, half in (
        ((0,-1,0),(1,0,0),md/2), ((1,0,0),(0,1,0),mw/2),
        ((0,1,0),(-1,0,0),md/2), ((-1,0,0),(0,-1,0),mw/2),
    ):
        axes = (tangent, (0,0,1), normal)
        side_placements.append({"normal": normal, "tangent": tangent, "half": half, "axes": axes})
    silver, steel, graphite = (185,190,198), (152,158,168), (36,41,47)
    parts = {}
    occurrences = []
    def part(key, color, operations=None, spring=None, parameters=None):
        parts[key] = {"color": color, "operations": operations or [], "parameters": parameters or {}}
        if spring:
            parts[key]["spring"] = spring
    def place(key, name, origin=(0,0,0), axes=IDENTITY, group="upper"):
        occurrences.append({"part": key, "name": name, "origin": origin, "axes": axes, "group": group})
    sleeve_ops = [
        op("OuterRoundedSleeve", [rr(width,depth,radius)], sleeve_h),
        op("OpenBottomNestingCavity", [rr(iw,id_,max(radius-10*s,2*s))], cavity_h, cut=True),
        op("CentralUpperServiceBore", [circle(28*s)], sleeve_h, cut=True),
    ]
    # Internal-only magnetic bores and lock pockets, never openings on outer sidewalls.
    lower_ops = [op("SteelMaleInsert", [rr(mw,md,8*s)], 27*hs),
                 op("ReceiverInnerCavity", [rr(mw-2*wall,md-2*wall,5.6*s)], 23*hs, z=4*hs, cut=True),
                 op("CentralLowerServiceBore", [circle(24*s)], 27*hs, cut=True)]
    for index, side in enumerate(side_placements, 1):
        n, t = side["normal"], side["tangent"]
        innerhalf = side["half"] + 0.2*s
        origin = (n[0]*innerhalf,n[1]*innerhalf,pole_z-base)
        sleeve_ops.append(op(f"RadialMagnetBore_{index}",[circle(16.2*s)],15.4*s,cut=True,origin=origin,axes=side["axes"][:2]))
        lock_start = (n[0]*innerhalf+t[0]*20*s,n[1]*innerhalf+t[1]*20*s,lock_z-base)
        sleeve_ops.append(op(f"InternalLatchPocket_{index}",[rr(10.8*s,8.8*hs,0.6*s)],5.3*s,cut=True,origin=lock_start,axes=side["axes"][:2]))
        lower_start = (n[0]*(side["half"]-wall-0.2*s)+t[0]*20*s,n[1]*(side["half"]-wall-0.2*s)+t[1]*20*s,lock_z-base)
        lower_ops.append(op(f"LatchExit_{index}",[rr(6.8*s,6.8*hs,0.3*s)],wall+0.4*s,cut=True,origin=lower_start,axes=side["axes"][:2]))
        # Clearance for upper release actuator where it enters the inner ceiling.
        center = (n[0]*(side["half"]-10*s)+t[0]*20*s,n[1]*(side["half"]-10*s)+t[1]*20*s)
        sleeve_ops.append(op(f"ReleaseActuatorSeat_{index}",[rr(10*s,10*s,1*s,*center)],4.6*hs,z=cavity_h-0.1*hs,cut=True))
    port_axes = ((1,0,0),(0,0,1))
    sleeve_ops.append(op("RecessedServicePort",[rr(12*s,5*hs,2*s)],3*s,cut=True,origin=(0,-depth/2+3*s,31*hs),axes=port_axes))
    sleeve_ops.append(op("StatusLightSeat",[circle(3*s)],2*s,cut=True,origin=(16*s,-depth/2+2*s,31*hs),axes=port_axes))
    part("01_Upper_Nesting_Sleeve",silver,sleeve_ops,parameters={"BodyWidth":width,"BodyDepth":depth,"CornerRadius":radius,"SleeveHeight":sleeve_h})
    place("01_Upper_Nesting_Sleeve","Upper sleeve (female)",(0,0,base))
    part("02_Lower_Steel_Insert",steel,lower_ops,parameters={"InsertWidth":mw,"InsertDepth":md,"WallThickness":wall})
    place("02_Lower_Steel_Insert","Lower steel insert (male)",(0,0,base),group="lower")
    part("03_Lower_Locating_Shoulder",steel,[op("RoundedLocatingShoulder",[rr(width,depth,radius)],base),op("ToolCableBore",[circle(24*s)],base,cut=True)])
    place("03_Lower_Locating_Shoulder","Lower locating shoulder",group="lower")
    cover_ops=[op("SlimTopCover",[rr(width-0.6*s,depth-0.6*s,radius-0.3*s)],cover),op("CentralCableOpening",[circle(28*s)],cover,cut=True)]
    for x in (-42*s,42*s):
        cover_ops.append(op("CarbonInset"+str(x),[rr(16*s,72*s,3*s,x,0)],1*hs,z=cover-0.8*hs,cut=True))
    bolts=[]
    for x in (-(width/2-11*s),width/2-11*s):
        for y in (-(depth/2-11*s),depth/2-11*s):
            bolts.append(circle(3.6*s,x,y))
    cover_ops.append(op("CoverFastenerHoles",bolts,cover,cut=True))
    part("04_Graphite_Top_Cover",graphite,cover_ops)
    place("04_Graphite_Top_Cover","Graphite upper cover",(0,0,top))
    part("05_Carbon_Inset",(21,26,32),[op("ThinInsetPanel",[rr(15.6*s,71.6*s,2.8*s)],0.8*hs)])
    for i,x in enumerate((-42*s,42*s),1):
        place("05_Carbon_Inset",f"Carbon inset {i}",(x,0,top+cover-0.8*hs))
    part("06_Upper_Enclosed_Neck",graphite,[op("ShortEnclosedNeck",[circle(60*s),circle(28*s)],18*hs),op("NeckFoot",[circle(66*s),circle(28*s)],3*hs)])
    place("06_Upper_Enclosed_Neck","Enclosed robot-side neck",(0,0,top+cover))
    flange_ops=[op("SteelRobotFlange",[circle(80*s),circle(24*s)],8*hs)]
    holes=[circle(7*s,27*s*math.cos(i*math.pi/2),27*s*math.sin(i*math.pi/2)) for i in range(4)]
    holes += [circle(4*s,35*s*math.cos((i+0.5)*math.pi/8),35*s*math.sin((i+0.5)*math.pi/8)) for i in range(16)]
    flange_ops.append(op("RobotMountingBoltCircles",holes,8*hs,cut=True))
    part("07_Robot_Mounting_Flange",silver,flange_ops,parameters={"FlangeDiameter":80*s,"MainHoleDiameter":7*s})
    place("07_Robot_Mounting_Flange","Robot mounting flange",(0,0,top+cover+18*hs))
    part("08_Lower_Neck",silver,[op("ToolSideNeck",[circle(58*s),circle(24*s)],12*hs)])
    place("08_Lower_Neck","Tool-side neck",(0,0,-12*hs),group="lower")
    part("09_Tool_Mounting_Flange",silver,[op("ToolMountingRing",[circle(76*s),circle(24*s)],8*hs),op("ToolBoltCircle",[circle(4.2*s,30*s*math.cos(i*math.pi/2),30*s*math.sin(i*math.pi/2)) for i in range(4)],8*hs,cut=True)])
    place("09_Tool_Mounting_Flange","Tool mounting flange",(0,0,-20*hs),group="lower")
    part("10_Gripper_Adapter",graphite,[op("CompactGripperAdapter",[circle(62*s),circle(24*s)],4*hs)])
    place("10_Gripper_Adapter","Gripper adapter",(0,0,-24*hs),group="tool")
    part("11_Gripper_Body",silver,[op("GripperHousing",[rr(70*s,52*s,5*s)],24*hs),op("GripperLowerRecess",[rr(57*s,42*s,3*s)],7*hs,cut=True)])
    place("11_Gripper_Body","Parallel gripper body",(0,0,-48*hs),group="tool")
    part("12_Gripper_Carriage",graphite,[op("Carriage",[rr(56*s,41*s,2*s)],7*hs)])
    place("12_Gripper_Carriage","Gripper carriage",(0,0,-48*hs),group="tool")
    part("13_Jaw_Finger",silver,[op("JawFinger",[rr(12*s,62*s,1*s)],10*hs),op("FingerFixingHoles",[circle(3.2*s,0,y*s) for y in (-20,20)],10*hs,cut=True)])
    for side,x in (("L",-23*s),("R",23*s)):
        place("13_Jaw_Finger","Gripper finger "+side,(x,-16*s,-58*hs),group="tool")
    part("14_Radial_Magnet",(64,69,77),[op("SealedMagnetModule",[circle(16*s)],15*s),op("PoleFace",[circle(15.8*s)],0.8*s)])
    part("15_Latch_Cartridge",steel,[op("CaptiveLatchGuide",[rr(10*s,8*hs,0.8*s)],15*s),op("SpringAndSliderBore",[rr(6.4*s,6.4*hs,0.3*s)],15*s,cut=True),op("IntegralMountingFoot",[rr(10*s,12*hs,0.5*s,0,-10*hs)],15*s),op("CaptiveSpringRearSeat",[rr(10*s,8*hs,0.8*s)],1*s,z=-1*s)])
    part("16_Latch_Slider",silver,[op("ShortRadialLockingBolt",[rr(6*s,6*hs,0.4*s)],10.8*s)])
    part("17_Compression_Spring",(111,117,126),spring={"diameter":5.5*s,"wire":0.7*s,"length":12*s,"turns":5},parameters={"SpringOutsideDiameter":5.5*s,"SpringWire":0.7*s})
    part("18_Upper_Release_Actuator",graphite,[op("ProtectedReleaseActuator",[rr(8*s,8*s,1*s)],8*hs),op("UpperCeilingMountingCap",[rr(8*s,8*s,1*s)],1.5*hs,z=8*hs)])
    part("19_Internal_Lock_Pocket",steel,[op("HardenedPocketInsert",[rr(10.4*s,8.4*hs,0.5*s)],5*s),op("ActualEngagementPocket",[rr(6.5*s,6.5*hs,0.3*s)],5*s,cut=True)])
    for index,side in enumerate(side_placements,1):
        n,t,half,axes=side["normal"],side["tangent"],side["half"],side["axes"]
        magnet_origin=(n[0]*(half+0.2*s),n[1]*(half+0.2*s),pole_z)
        place("14_Radial_Magnet",f"Radial magnet {index}",magnet_origin,axes)
        base_origin=(n[0]*(half-18*s)+t[0]*20*s,n[1]*(half-18*s)+t[1]*20*s,lock_z)
        place("15_Latch_Cartridge",f"Captive latch guide {index}",base_origin,axes,"lower")
        place("17_Compression_Spring",f"Latch spring {index}",base_origin,axes,"lower")
        slider_origin=tuple(base_origin[j]+n[j]*12.2*s for j in range(3))
        place("16_Latch_Slider",f"Engaged internal bolt {index}",slider_origin,axes,"lower")
        pocket_origin=(n[0]*(half+0.2*s)+t[0]*20*s,n[1]*(half+0.2*s)+t[1]*20*s,lock_z)
        place("19_Internal_Lock_Pocket",f"Internal hardened pocket {index}",pocket_origin,axes)
        driver_origin=(n[0]*(half-10*s)+t[0]*20*s,n[1]*(half-10*s)+t[1]*20*s,29*hs)
        place("18_Upper_Release_Actuator",f"Upper release actuator {index}",driver_origin)
    part("20_Service_Connector",graphite,[op("RecessedConnectorHousing",[rr(11.6*s,4.6*hs,1.8*s)],2.8*s)])
    place("20_Service_Connector","Service connector",(0,-depth/2+2.8*s,36*hs),((1,0,0),(0,0,1),(0,-1,0)))
    part("21_Status_Indicator",(28,190,72),[op("StatusIndicatorLens",[circle(2.9*s)],1.9*s)])
    place("21_Status_Indicator","Status indicator",(16*s,-depth/2+1.9*s,36*hs),((1,0,0),(0,0,1),(0,-1,0)))
    part("22_Cover_Fastener",steel,[op("RecessedFastenerHead",[circle(6*s)],1.5*hs),op("SocketRecess",[circle(3*s)],1.3*hs,z=0.2*hs,cut=True)])
    for i,hole in enumerate(bolts,1):
        # Heads sit on the cover; threaded shafts are omitted in this concept model.
        place("22_Cover_Fastener",f"Cover fastener {i}",(hole["cx"],hole["cy"],top+cover))
    params={"body_width_mm":width,"body_depth_mm":depth,"body_height_mm":height,"corner_radius_mm":radius,"upper_cavity_mm":[iw,id_],"lower_insert_mm":[mw,md],"slide_clearance_per_side_mm":0.2*s,"overlap_depth_mm":27*hs,"magnet_diameter_mm":16*s,"magnet_depth_mm":15*s,"robot_flange_diameter_mm":80*s}
    return parts,occurrences,params


def save_view(app, doc, folder, name):
    doc.Activate()
    view=app.ActiveView
    camera=view.Camera
    # This design uses Z as insertion/up direction; the default template camera uses Y-up.
    bounds=doc.ComponentDefinition.RangeBox
    center=tuple((getattr(bounds.MaxPoint,a)+getattr(bounds.MinPoint,a))/2 for a in ("X","Y","Z"))
    g=app.TransientGeometry
    camera.Target=g.CreatePoint(*center)
    camera.Eye=g.CreatePoint(center[0]+18,center[1]-24,center[2]+17)
    camera.UpVector=g.CreateUnitVector(0,0,1)
    camera.Perspective=False
    camera.Fit()
    camera.Apply()
    view.Update()
    path=folder/(name+".png")
    view.SaveAsBitmap(str(path),1600,1200)
    return str(path)


def build_quickchanger(app, args):
    from inventor_backend import safe_name
    name=safe_name(args.get("name","QuickChanger_V3"))
    width=float(args.get("body_width_mm",120)); depth=float(args.get("body_depth_mm",110)); height=float(args.get("body_height_mm",40))
    if not all(math.isfinite(x) for x in (width,depth,height)) or not (100<=width<=200 and 90<=depth<=180 and 36<=height<=60):
        raise ValueError("概念模型范围：宽100–200、深90–180、高36–60毫米。")
    if not 0.85<=width/depth<=1.3:
        raise ValueError("主体宽深比例应接近方形。")
    if 5.5*min(width/120,depth/110)>=6.4*(height/40):
        raise ValueError("该宽深高组合不足以容纳当前弹簧包络，请增加主体高度。")
    folder=(MODELS/name).resolve()
    if not folder.is_relative_to(MODELS.resolve()):
        raise ValueError("模型目录无效。")
    resume=bool(args.get("resume",False))
    if folder.exists() and not resume:
        raise FileExistsError("模型目录已存在，不会覆盖；请使用新名称。")
    folder.mkdir(parents=True,exist_ok=True)
    parts,occurrences,dimensions=spec_for(width,depth,height)
    reference = args.get("reference_image")
    manifest={"status":"building","reference_image":reference,"reference_interpretation":"结构由对话中的AI分析后选用此固定模板；程序不直接从像素恢复几何。","name":name,"dimensions":dimensions,"assumptions":"图片没有完整尺寸；宽深高和内部包络采用概念假设，非生产图。","components":[],"placements":occurrences}
    manifest_path=folder/"model_manifest.json"
    if resume and manifest_path.exists() and json.loads(manifest_path.read_text(encoding="utf-8"))["dimensions"]!=dimensions:
        raise ValueError("恢复构建的尺寸与原任务不一致。")
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    built={}
    try:
        for i,(key,spec) in enumerate(parts.items(),1):
            progress(f"Creating native part {i}/{len(parts)}: {key}")
            path,stats=make_part(app,folder,key,spec,resume)
            built[key]=path
            manifest["components"].append(stats)
            manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
        assembly_path=folder/(name+".iam")
        if assembly_path.exists():
            raise FileExistsError("装配体已存在，不会重复创建。")
        doc=com.CastTo(app.Documents.Add(com.constants.kAssemblyDocumentObject,app.FileManager.GetTemplateFile(com.constants.kAssemblyDocumentObject),True),"AssemblyDocument")
        doc.UnitsOfMeasure.LengthUnits=com.constants.kMillimeterLengthUnits
        definition=doc.ComponentDefinition
        asm_params=definition.Parameters.UserParameters
        for parameter,value in (("BodyWidth",width),("BodyDepth",depth),("BodyHeight",height),("OverlapDepth",dimensions["overlap_depth_mm"])):
            asm_params.AddByExpression(parameter,f"{value:g} mm",com.constants.kMillimeterLengthUnits)
        for index,item in enumerate(occurrences,1):
            occ=definition.Occurrences.Add(str(built[item["part"]]),transform(app,item["origin"],item["axes"]))
            occ.Name=item["name"]
            occ.Grounded=True
        doc.Update2(False)
        doc.SaveAs(str(assembly_path),False)
        assembled=definition.RepresentationsManager.DesignViewRepresentations.Add("V3_Assembled",False)
        assembled.Activate()
        exterior_preview=save_view(app,doc,folder,"V3_Assembled_Preview")
        assembled.SaveCurrentCamera()
        # A second native assembly stores the separation pose without altering the original.
        exploded_path=folder/(name+"_Separated.iam")
        doc.SaveAs(str(exploded_path),True)
        separated=com.CastTo(app.Documents.Open(str(exploded_path),True),"AssemblyDocument")
        separated_def=separated.ComponentDefinition
        for index,item in enumerate(occurrences,1):
            occ=separated_def.Occurrences.Item(index)
            if item["group"] in ("lower","tool"):
                occ.Grounded=False
                new_origin=tuple(item["origin"][j]+(0,0,-70)[j] for j in range(3))
                occ.Transformation=transform(app,new_origin,item["axes"])
                occ.Grounded=True
        separated.Update2(False)
        separated_preview=save_view(app,separated,folder,"V3_Vertical_Separation_Preview")
        separated.Save2(True)
        # Check nesting clearance and solid/reference integrity in the actual assembly.
        itemset=app.TransientObjects.CreateObjectCollection()
        itemset.Add(definition.Occurrences.Item(1)); itemset.Add(definition.Occurrences.Item(2))
        nesting=definition.AnalyzeInterference(itemset)
        core_interferences=int(nesting.Count)
        complete_set=app.TransientObjects.CreateObjectCollection()
        for index in range(1,definition.Occurrences.Count+1):
            complete_set.Add(definition.Occurrences.Item(index))
        complete_interference=definition.AnalyzeInterference(complete_set)
        all_interferences=int(complete_interference.Count)
        actual_bounds=definition.RangeBox
        bound_values={axis.lower():round((getattr(actual_bounds.MaxPoint,axis)-getattr(actual_bounds.MinPoint,axis))*10,4) for axis in ("X","Y","Z")}
        verification={"native_parts":len(parts),"assembly_occurrences":definition.Occurrences.Count,"all_parts_have_one_solid":all(c["solid_bodies"]==1 and c["volume_mm3"]>0 for c in manifest["components"]),"nesting_core_interferences":core_interferences,"upper_and_lower_overlap_mm":dimensions["overlap_depth_mm"],"bounds_mm":bound_values,"detailed_release_kinematics_validated":False,"full_assembly_manufacturing_interference_validated":False}
        verification["full_assembly_solid_interference_count"]=all_interferences
        if core_interferences:
            raise RuntimeError("上下嵌套主体出现实体干涉，请检查模型。")
        if all_interferences:
            raise RuntimeError("完整概念装配出现实体干涉，请检查各零件包络。")
        doc.Activate()
        assembled.Activate()
        doc.Save2(True)
        manifest.update(status="complete",assembly_path=str(assembly_path),separated_assembly_path=str(exploded_path),preview_path=exterior_preview,separated_preview_path=separated_preview,verification=verification)
        manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
        return {key:manifest[key] for key in ("assembly_path","separated_assembly_path","preview_path","separated_preview_path","dimensions","verification")}
    except Exception as error:
        manifest.update(status="failed",error=str(error))
        manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
        raise


def assembly_action(app, action, args):
    path=(MODELS/args["filename"]).resolve()
    if not path.is_relative_to(MODELS.resolve()) or path.suffix.lower()!=".iam" or not path.exists():
        raise ValueError("只接受 models 内现有 .iam 装配体。")
    doc=None
    for i in range(1,app.Documents.Count+1):
        candidate=app.Documents.Item(i)
        if candidate.FullFileName and Path(candidate.FullFileName).resolve()==path:
            doc=com.CastTo(candidate,"AssemblyDocument"); break
    if doc is None:
        doc=com.CastTo(app.Documents.Open(str(path),True),"AssemblyDocument")
    if action=="assembly_explode":
        alternate=path.with_name(path.stem+"_Separated.iam")
        if not alternate.exists():
            raise FileNotFoundError("当前模型没有已生成的上下分离装配体。")
        return assembly_action(app,"assembly_preview",{"filename":str(alternate.relative_to(MODELS))})
    doc.Activate()
    result={"assembly_path":str(path),"occurrences":doc.ComponentDefinition.Occurrences.Count,"dirty":bool(doc.Dirty),"reference_names":[doc.ComponentDefinition.Occurrences.Item(i).Name for i in range(1,doc.ComponentDefinition.Occurrences.Count+1)]}
    bounds=doc.ComponentDefinition.RangeBox
    result["bounds_mm"]={a.lower():round((getattr(bounds.MaxPoint,a)-getattr(bounds.MinPoint,a))*10,4) for a in ("X","Y","Z")}
    result["user_parameters_mm"]={doc.ComponentDefinition.Parameters.UserParameters.Item(i).Name:round(doc.ComponentDefinition.Parameters.UserParameters.Item(i).Value*10,4) for i in range(1,doc.ComponentDefinition.Parameters.UserParameters.Count+1)}
    if action=="assembly_preview":
        result["preview_path"]=save_view(app,doc,path.parent,path.stem+"_Live_Preview")
    return result
