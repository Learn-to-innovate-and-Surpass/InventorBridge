# Core release verification

Checked on 20 October 2026. Structured results are in [VERIFICATION.json](VERIFICATION.json).

- All five Python files parsed successfully.
- All six copied source/dependency files match the SHA-256 values recorded in [SOURCE_MANIFEST.json](SOURCE_MANIFEST.json).
- The local test environment used Python 3.14.7, MCP 1.30.0, pywin32 312 and Pillow 12.3.0.
- A real stdio MCP client initialized the staged `server.py` and received all 12 expected tool definitions.
- The client made zero tool calls. No COM worker, Inventor connection, application launch, model creation, setup or client registration was requested.

The protocol check used the existing local dependency environment with bytecode writing disabled. It does not establish a fresh dependency installation or successful modeling on another computer.

`verify_mcp.py` has broader behavior than this release check: it also calls the read-only `inventor_status` tool. Users should distinguish MCP availability from an actual usable Inventor session.
