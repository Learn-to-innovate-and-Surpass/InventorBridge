"""Portable, project-local deployment. Explicit --check-only or --setup required."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
SOURCES = ("server.py", "inventor_backend.py", "quickchanger_model.py", "requirements.txt", "verify_mcp.py")


def inventor_probe():
    result = {"registered": False, "clsid": None, "executables": [], "detected": False}
    if sys.platform != "win32":
        return result
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Inventor.Application\CLSID",
                            0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            result["clsid"] = winreg.QueryValueEx(key, None)[0]
            result["registered"] = True
    except OSError:
        pass
    candidates = set()
    for variable in ("ProgramW6432", "ProgramFiles"):
        location = os.environ.get(variable)
        if location:
            try:
                candidates.update(Path(location).glob("Autodesk/Inventor*/Bin/Inventor.exe"))
            except OSError:
                pass
    result["executables"] = sorted(str(p.resolve()) for p in candidates if p.is_file())
    result["detected"] = result["registered"] or bool(result["executables"])
    return result


def read_config():
    import tomllib
    path = ROOT / ".codex" / "config.toml"
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    return path, original, tomllib.loads(original)


def inspect():
    supported = (sys.platform == "win32" and struct.calcsize("P") == 8
                 and platform.machine().lower() in ("amd64", "x86_64"))
    report = {"root": str(ROOT), "windows_x64": supported,
              "python": {"executable": sys.executable, "version": platform.python_version(),
                         "supported": sys.version_info >= (3, 11)},
              "missing_source": [n for n in SOURCES if not (ROOT / n).is_file()],
              "inventor": inventor_probe(),
              "venv_python_exists": (ROOT / ".venv" / "Scripts" / "python.exe").is_file()}
    if report["python"]["supported"]:
        try:
            path, _, config = read_config()
            report["project_config"] = {"path": str(path), "valid": True,
                                        "servers": sorted(config.get("mcp_servers", {}))}
        except Exception as exc:
            report["project_config"] = {"valid": False, "error_type": type(exc).__name__}
    report["ok"] = supported and report["python"]["supported"] and not report["missing_source"]
    return report


def run(arguments, timeout=600):
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    completed = subprocess.run(arguments, cwd=str(ROOT), env=env, capture_output=True,
                               encoding="utf-8", errors="replace", timeout=timeout, shell=False)
    if completed.returncode:
        output = (completed.stdout + completed.stderr).lower()
        network = any(word in output for word in
                      ("connection", "proxy", "timed out", "name resolution", "network", "ssl"))
        # Do not echo pip logs: configured package URLs can contain credentials.
        raise RuntimeError(json.dumps({"stage": "subprocess", "exit_code": completed.returncode,
                                       "network_or_proxy_error": network,
                                       "message": "Command failed. No credentials/logs emitted. If network access is blocked, use the host approval mechanism."}))
    return completed.stdout


def environment():
    directory = ROOT / ".venv"
    executable = directory / "Scripts" / "python.exe"
    if directory.exists():
        if not executable.is_file():
            raise RuntimeError("Existing .venv is incomplete; move it aside explicitly before rerunning. Nothing was deleted.")
        details = json.loads(run([str(executable), "-I", "-c",
                                 "import json,sys,struct;print(json.dumps({'prefix':sys.prefix,'venv':sys.prefix!=sys.base_prefix,'version':list(sys.version_info[:2]),'bits':struct.calcsize('P')*8}))"], 30))
        if (not details["venv"] or Path(details["prefix"]).resolve() != directory.resolve()
                or details["version"] < [3, 11] or details["bits"] != 64):
            raise RuntimeError("Existing .venv does not match this root or Python requirements; nothing was deleted.")
        reused = True
    else:
        run([sys.executable, "-m", "venv", str(directory)], 120)
        reused = False
    run([str(executable), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(ROOT / "requirements.txt")])
    run([str(executable), "-m", "pip", "check"], 60)
    return executable.resolve(), reused


def register(executable):
    import tomllib
    path, original, config = read_config()
    servers = config.get("mcp_servers", {})
    expected = {"command": executable.as_posix(), "args": [(ROOT / "server.py").as_posix()],
                "cwd": ROOT.as_posix(), "startup_timeout_sec": 30, "tool_timeout_sec": 300,
                "env": {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}}
    # Reuse our matching registration; never change an unrelated server entry.
    for name, value in servers.items():
        if name.startswith("inventor_local") and value == expected:
            return name, str(path), False
    name = "inventor_local"
    count = 2
    while name in servers:
        name = f"inventor_local_{count}"
        count += 1
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    addition = (f"\n[mcp_servers.{name}]\ncommand = {quote(expected['command'])}\n"
                f"args = {quote(expected['args'])}\ncwd = {quote(expected['cwd'])}\n"
                f"startup_timeout_sec = 30\ntool_timeout_sec = 300\n"
                f"\n[mcp_servers.{name}.env]\nPYTHONUTF8 = \"1\"\nPYTHONIOENCODING = \"utf-8\"\n")
    updated = original.rstrip("\r\n") + "\n" + addition
    tomllib.loads(updated)  # Validate before writing; do not replace existing sections.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(updated)
    return name, str(path), True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check-only", action="store_true")
    modes.add_argument("--setup", action="store_true")
    args = parser.parse_args()
    report = inspect()
    report["mode"] = "check-only" if args.check_only else "setup"
    if args.check_only:
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report["ok"] else 2
    stage = "prerequisites"
    try:
        if not report["ok"] or not report.get("project_config", {}).get("valid"):
            raise RuntimeError("Require Windows x64, Python >=3.11, complete source files and valid project TOML.")
        stage = "dependencies"
        executable, reused = environment()
        report["environment"] = {"python": str(executable), "reused": reused, "pip_check": True}
        stage = "mcp_verification"
        verification = json.loads(run([str(executable), str(ROOT / "verify_mcp.py")], 120))
        report["verification"] = verification
        if not verification.get("mcp_ready"):
            raise RuntimeError("MCP initialization/tool verification failed; existing registrations were preserved.")
        stage = "project_registration"
        name, path, changed = register(executable)
        report["registration"] = {"server_name": name, "config_path": path, "changed": changed,
                                   "scope": "project-only", "global_config_changed": False}
        report["ok"] = True
        report["next"] = "Open this root as a trusted local project and reload MCP. CAD connection is reported separately; install/license Inventor manually if absent."
    except Exception as exc:
        report.update(ok=False, failed_stage=stage, error=str(exc))
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
