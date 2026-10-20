# InventorBridge

[中文使用说明](README_zh.md)

A compact local interface that lets an MCP-capable AI client call Autodesk Inventor through its COM API. This repository contains the author's current modified interface core, portable setup helpers, and usage instructions.

The core source is copied unchanged from the desktop installation. [SOURCE_MANIFEST.json](SOURCE_MANIFEST.json) records the original relative paths and hashes. Existing CAD projects, generated models, screenshots, logs, virtual environments, local client configuration, and one-off modeling scripts are excluded.

## Architecture

```text
MCP-capable AI client
    -> local stdio MCP server
    -> separate COM worker for each operation
    -> locally installed Autodesk Inventor
```

`server.py` exposes 12 tools. `inventor_backend.py` owns COM connections and performs bounded operations. `quickchanger_model.py` provides the concept assembly template required by the assembly tools. Keep these three files together.

An existing Inventor installation and its license are required for CAD operations. This community interface uses the Inventor API; Autodesk software and licenses are not distributed here.

## Requirements

- Windows x64.
- 64-bit Python 3.11 or later.
- An installed, licensed Autodesk Inventor for actual modeling.
- An AI client that supports local stdio MCP servers.
- Dependencies pinned in `requirements.txt`: `mcp==1.30.0`, `pywin32==312`, and `Pillow==12.3.0`.

The AI client supplies the model and its account configuration. This bridge does not call an OpenAI model API directly.

## Setup

Download or clone this repository into its own folder. Keep the Python files, dependency list, and setup helpers at the repository root. Rebuild the virtual environment on each computer.

From PowerShell in that folder:

```powershell
python .\setup_bridge.py --check-only
python .\setup_bridge.py --setup
```

`--check-only` checks platform, source files, configuration syntax, and the local Inventor installation registration. It does not launch Inventor.

`--setup` creates or reuses the folder's `.venv`, installs pinned dependencies, checks MCP initialization/tool discovery, and attempts a read-only Inventor status query. It writes a project-local `.codex/config.toml` entry for the current path while preserving unrelated entries. It does not change global client configuration, install Inventor, or create models.

For a client that uses another MCP configuration format, set the stdio command to this folder's `.venv\Scripts\python.exe`, the argument to the absolute path of `server.py`, and the working directory to this folder. Use a 300-second tool timeout for the slower concept assembly operation. Client trust and registration requirements follow that client's own documentation.

The included diagnostic can also be run after setup:

```powershell
.\.venv\Scripts\python.exe -B .\verify_mcp.py
```

This diagnostic initializes MCP, lists the expected tools, and calls `inventor_status`. A successful MCP check and an available CAD session are separate results. Run Inventor and the bridge in the same Windows user session with compatible privileges.

## Available tools

| Tool | Function |
| --- | --- |
| `inventor_status` | Read version, document list, and connection state without launching Inventor |
| `inventor_launch` | Connect to Inventor or launch the installed application |
| `inventor_create_plate` | Create a native rectangular plate with a centered through hole |
| `inventor_open_model` | Open a project `.ipt` |
| `inventor_inspect_model` | Inspect dimensions, parameters, volume, and native geometry |
| `inventor_update_plate` | Update a bridge-generated plate and save with a new name |
| `inventor_preview` | Return an Inventor model viewport image |
| `inventor_create_quickchanger` | Create the supported parametric concept assembly |
| `inventor_open_assembly` | Open a project `.iam` |
| `inventor_inspect_assembly` | Inspect assembly dimensions and components |
| `inventor_assembly_preview` | Return an Inventor assembly viewport image |
| `inventor_assembly_explode` | Open the previously generated separated concept assembly |

## First modeling request

After connecting the interface, provide the AI client with a name and explicit dimensions. For example:

> Create a plate named demo_plate_01, 100 mm long, 60 mm wide and 10 mm thick, with a centered 20 mm through hole. Check the resulting bounds, volume and named dimensions, then return a preview.

Use a new name for every output. Existing files are not overwritten. Plate updates accept bridge-generated plates without unsaved changes and preserve the source by saving under a different name.

Dimensions enter the interface in millimetres; the worker converts geometry values for Inventor's internal units. Model files are restricted to the project's `models` directory. Viewport previews and operation logs are written under `previews` and `logs`. Python installation and COM-wrapper libraries may also use their standard cache locations.

## Capability boundaries

The supplied creation tools cover a rectangular plate and a fixed parametric concept quickchanger. More varied parts require additional modeling logic. The optional reference-image argument checks and records an image path; it does not reconstruct arbitrary geometry from image pixels.

The quickchanger's default dimensions are concept assumptions. Its separated assembly is a static illustration, and manufacturing tolerances, strength, magnetic performance, and release motion require independent engineering validation.

Ordinary operations have a 45-second worker timeout; concept assembly creation has a 240-second timeout. A timed-out or cancelled worker can leave a partially completed Inventor operation. Check the application, output files, and logs before deciding what to do next; do not automatically repeat a creation request.

Model inspection can open a document and previews can change its view. The protocol-only release check did not invoke those operations or connect to Inventor; see [VERIFICATION.md](VERIFICATION.md).

## Relationship to the robot project

The related [SinoGermanRobot project](https://github.com/Learn-to-innovate-and-Surpass/SinoGermanRobot) provides robot simulation, action editing, and reviewed-file execution. InventorBridge is a separate CAD tool. It is not imported by that project's browser simulation service or robot worker.

## License

Author-owned bridge code and documentation use the [MIT license](LICENSE). Autodesk products, trademarks and API documentation, Python dependencies, and any user-supplied models or images retain their respective rights and licenses.
