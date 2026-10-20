"""Read-only protocol probe. Never launches Inventor or creates CAD documents."""
import asyncio
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
EXPECTED = {
    "inventor_status", "inventor_launch", "inventor_create_plate", "inventor_open_model",
    "inventor_inspect_model", "inventor_update_plate", "inventor_preview",
    "inventor_create_quickchanger", "inventor_open_assembly", "inventor_inspect_assembly",
    "inventor_assembly_preview", "inventor_assembly_explode",
}


async def probe():
    report = {"mcp_initialized": False, "mcp_ready": False,
              "cad": {"connected": False, "checked": False}}
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        parameters = StdioServerParameters(
            command=sys.executable, args=[str(ROOT / "server.py")], cwd=str(ROOT),
            env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))
        # Server stderr is suppressed here to avoid dumping environment secrets.
        with open(os.devnull, "w", encoding="utf-8") as errors:
            async with stdio_client(parameters, errlog=errors) as streams:
                async with ClientSession(*streams) as session:
                    await asyncio.wait_for(session.initialize(), 30)
                    report["mcp_initialized"] = True
                    listing = await asyncio.wait_for(session.list_tools(), 30)
                    names = {tool.name for tool in listing.tools}
                    report.update(tool_count=len(names), tools=sorted(names),
                                  missing_tools=sorted(EXPECTED - names),
                                  unexpected_tools=sorted(names - EXPECTED),
                                  mcp_ready=names == EXPECTED)
                    if "inventor_status" in names:
                        try:
                            status = await asyncio.wait_for(session.call_tool("inventor_status", {}), 60)
                            report["cad"] = {"connected": not status.isError, "checked": True,
                                             "message": "Read-only status succeeded." if not status.isError else
                                             "CAD status failed; Inventor may not be running, installed or available to this session. MCP readiness is independent."}
                        except Exception as exc:
                            report["cad"] = {"connected": False, "checked": True,
                                             "error_type": type(exc).__name__,
                                             "message": "CAD status did not complete; no launch attempted."}
    except Exception as exc:
        report["protocol_error_type"] = type(exc).__name__
    return report


if __name__ == "__main__":
    # Always exit successfully to let the installer preserve the structured report.
    print(json.dumps(asyncio.run(probe()), ensure_ascii=False))
