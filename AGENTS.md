# AGENTS.md

## Project Overview

MCP (Model Context Protocol) server for controlling the Amiberry Amiga emulator via Claude AI.
Python 3.10+ project using `hatchling` build system with `ruff` for linting/formatting and `pytest` for testing.

**Source layout**: `src/amiberry_mcp/` (package), `tests/` (flat test dir), `scripts/` (shell installers).

Key modules:
- `server.py` — MCP server with 130+ tools, data-driven handler dispatch (~4600 lines)
- `http_server.py` — FastAPI REST API with endpoint factories (~3700 lines)
- `ipc_client.py` — Unix socket / D-Bus IPC client with persistent connections (~2100 lines)
- `shared_state.py` — Process state, IPC client caching, launch helpers
- `common.py` — Shared helpers (launch, scan with TTL caching, validation)
- `config.py` — Platform detection, path constants
- `uae_config.py` — .uae config file parser/generator
- `savestate.py` — .uss savestate metadata parser
- `rom_manager.py` — ROM identification by CRC32/MD5

## Build & Development Commands

```bash
# Setup
python3 -m venv venv
source venv/bin/activate           # Linux/macOS
pip install -e ".[all]"            # Install with all optional deps (http, dbus, dev)
pip install -e ".[dev]"            # Dev deps only (pytest, pytest-asyncio, ruff)

# Run tests
pytest tests/ -v                   # All tests
pytest tests/test_uae_config.py -v # Single file
pytest tests/test_uae_config.py::TestParseUaeConfig -v                        # Single class
pytest tests/test_uae_config.py::TestParseUaeConfig::test_parse_simple_config -v  # Single test
pytest tests/ -v -k "test_parse"   # By keyword match

# Lint & format
ruff check src/ tests/             # Lint (errors, warnings, pyflakes, isort, bugbear, etc.)
ruff check --fix src/ tests/       # Auto-fix lint issues
ruff format src/ tests/            # Format (black-compatible, 88 char line length)
ruff format --check src/ tests/    # Check formatting without modifying

# Run servers
amiberry-mcp                       # MCP server (stdio transport)
amiberry-http                      # HTTP API server (localhost:8080)
```

## Test Conventions

- Framework: `pytest` with `pytest-asyncio` (asyncio_mode = "auto")
- Test files: `tests/test_<module>.py` — one test file per source module
- Test classes group related tests: `class TestParseUaeConfig:`, `class TestTerminateProcess:`
- Test methods: `def test_<what_is_tested>(self, ...):`
- Use `tmp_path` fixture for temp files, `unittest.mock` for mocking
- Async tests use `@pytest.mark.asyncio` decorator
- Mocking pattern: `patch("asyncio.open_unix_connection")`, `MagicMock(spec=subprocess.Popen)`

## Code Style

### Formatting (enforced by ruff)
- Line length: 88 characters (black-compatible)
- Target: Python 3.10 (`target-version = "py310"`)
- E501 (line-too-long) is ignored — handled by formatter

### Imports (isort via ruff)
Order: stdlib, third-party, local (relative). Grouped with blank lines between.
```python
import asyncio                          # stdlib
import subprocess
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException  # third-party
from mcp.server import Server

from .common import build_launch_command    # local (relative imports within package)
from .config import CONFIG_DIR, IS_LINUX
```
- `known-first-party = ["amiberry_mcp"]`
- Always use relative imports within the `amiberry_mcp` package (`.common`, `.config`)
- Import aliases for name clarity: `from .common import find_config_path as _find_config_path`

### Type Annotations
- Use modern Python 3.10+ union syntax: `Path | None`, `str | None` (not `Optional[Path]`)
- Use lowercase generics: `dict[str, Any]`, `list[str]`, `tuple[int, str]` (not `Dict`, `List`)
- Annotate all function signatures (parameters and return types)
- Use `Any` from `typing` for flexible dict values: `dict[str, Any]`
- Dataclass fields use type annotations: `process: subprocess.Popen | None = None`

### Naming
- `snake_case` — functions, methods, variables, module names
- `PascalCase` — classes (`AmiberryIPCClient`, `TestParseUaeConfig`)
- `UPPER_SNAKE_CASE` — module-level constants (`EMULATOR_BINARY`, `ASF_MAGIC`, `KNOWN_ROMS`)
- Prefix private/internal names with `_`: `_state`, `_is_path_within`, `_get_cache_key`
- Descriptive names; no abbreviations except well-known ones (e.g., `ipc`, `config`, `proc`)

### Docstrings
- Every module has a top-level docstring: `"""Module description."""`
- Functions use Google-style docstrings with Args/Returns/Raises:
```python
def normalize_log_path(log_name: str) -> Path:
    """Ensure a log name has a .log extension and return the full path.

    Raises:
        ValueError: If the resulting path is outside LOG_DIR (path traversal).
    """
```
- Short helper functions use single-line docstrings: `"""Wrap a string in the MCP TextContent return format."""`

### Error Handling
- Custom exception hierarchy rooted in domain base classes:
  `IPCError` -> `IPCConnectionError`, `CommandError`
- Catch specific exceptions, never bare `except:`
- OSError for filesystem operations with graceful degradation (log/skip, don't crash):
```python
try:
    directory.mkdir(parents=True, exist_ok=True)
except OSError:
    pass  # Permission denied — not fatal
```
- ProcessLookupError handled explicitly for subprocess lifecycle
- Use `from` for exception chaining: `raise ValueError(...) from e`

### Path Handling
- Always use `pathlib.Path`, never raw string paths
- Security: validate paths are within expected directories with `_is_path_within()`
- Resolve paths before comparison: `path.resolve().relative_to(parent.resolve())`

### Async Patterns
- Use `asyncio.create_subprocess_exec` for non-blocking subprocess calls
- `asyncio.wait_for(coro, timeout=N)` for timeouts
- Wrap blocking I/O in async handlers with `asyncio.to_thread()` (`.exists()`, `.mkdir()`, `.stat()`)
- IPC client uses persistent connections with automatic reconnect on error
- Helper pattern for IPC calls with standard error handling:
```python
async def _ipc_bool_call(method_name, *args, success_msg, failure_msg) -> list[TextContent]:
```

### Ruff Lint Rules Enabled
- `E` — pycodestyle errors
- `W` — pycodestyle warnings
- `F` — Pyflakes
- `I` — isort
- `B` — flake8-bugbear
- `C4` — flake8-comprehensions
- `UP` — pyupgrade

### Suppress Warnings
- Use `# noqa: XXXX` sparingly and only with specific codes: `# noqa: SIM115`, `# noqa: B007`
- Never blanket `# noqa` without a code

## Architecture Notes

- **State management**: `ProcessState` dataclass in `shared_state.py` holds process, IPC client cache, and log handles. Shared between `server.py` and `http_server.py` via `get_state()` singleton.
- **IPC client caching**: `get_ipc_client()` in `shared_state.py` caches clients per instance. Cache is invalidated on process launch/restart.
- **Launch pattern**: All process launches go through `launch_and_store()` in `shared_state.py`, which centralises close-log → invalidate-cache → launch → store-state.
- **Handler dispatch**: `server.py` uses data-driven handler tables (`_NO_ARG_BOOL_HANDLERS`, `_ARG_BOOL_HANDLERS`, `_SIMPLE_QUERY_HANDLERS`) with `functools.partial` dispatch, reducing boilerplate for ~36 repetitive handlers.
- **Endpoint factories**: `http_server.py` uses `_create_no_arg_ipc_endpoint()` for no-arg boolean IPC endpoints.
- **IPC connection reuse**: `AmiberryIPCClient` maintains persistent Unix socket connections with `asyncio.Lock`-protected access and automatic reconnect on `BrokenPipeError`/`ConnectionResetError`.
- **Scan caching**: `common.py` caches `scan_disk_images()` results with a 60-second TTL. Use `clear_scan_cache()` to force refresh.
- **MCP tools**: Registered as `@app.call_tool()` handlers returning `list[TextContent]`
- **Platform support**: macOS + Linux with platform-specific paths in `config.py`. `RuntimeError` on unsupported platforms.
- **IPC transport**: Prefers Unix socket, falls back to D-Bus on Linux. Socket paths support multiple instances.
- **Security**: Path traversal prevention on all user-supplied paths. IPC argument sanitization (tab/newline stripping).
- **Optional deps**: `fastapi`/`uvicorn` for HTTP, `jeepney` for D-Bus — guarded by try/except ImportError
