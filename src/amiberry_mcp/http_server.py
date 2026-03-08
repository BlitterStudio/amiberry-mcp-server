#!/usr/bin/env python3
"""
FastAPI HTTP API server for Amiberry emulator control.
This server exposes REST API endpoints for automation tools including:
- Siri Shortcuts (macOS/iOS)
- Google Assistant (Android/Linux)
- Home Assistant
- curl/wget and shell scripts
- Any HTTP client
"""

import asyncio
import base64
import datetime
import os
import platform
import re
import signal
import subprocess
from contextlib import asynccontextmanager as _asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .common import (
    _is_path_within,
    build_launch_command,
    detect_amiberry_version,
    format_log_timestamp,
    format_signal_info,
    launch_process,
    normalize_log_path,
    scan_disk_images,
    terminate_process,
)
from .common import (
    find_config_path as _find_config_path,
)
from .config import (
    AMIBERRY_HOME,
    CD_EXTENSIONS,
    CONFIG_DIR,
    DISK_IMAGE_DIRS,
    EMULATOR_BINARY,
    IS_LINUX,
    IS_MACOS,
    LOG_DIR,
    ROM_DIR,
    SAVESTATE_DIR,
    SCREENSHOT_DIR,
    SUPPORTED_MODELS,
    SYSTEM_CONFIG_DIR,
    ensure_directories_exist,
    get_platform_info,
)
from .ipc_client import (
    AmiberryIPCClient,
    IPCConnectionError,
)
from .rom_manager import (
    get_rom_summary,
    identify_rom,
    scan_rom_directory,
)
from .savestate import (
    get_savestate_summary,
    inspect_savestate,
)
from .uae_config import (
    create_config_from_template,
    get_config_summary,
    modify_uae_config,
    parse_uae_config,
)


@dataclass
class _ProcessState:
    process: subprocess.Popen | None = None
    launch_cmd: list[str] | None = None
    log_path: Path | None = None
    log_file_handle: Any | None = None
    log_read_positions: dict[str, int] = field(default_factory=dict)
    active_instance: int | None = None
    ipc_client_cache: tuple[int | None, AmiberryIPCClient] | None = None

    def close_log_handle(self) -> None:
        """Close the log file handle if open."""
        if self.log_file_handle is not None:
            try:
                self.log_file_handle.close()
            except OSError:
                pass
            self.log_file_handle = None


_state = _ProcessState()


def _get_ipc_client() -> AmiberryIPCClient:
    """Get an IPC client for the active instance, reusing cached clients."""
    if (
        _state.ipc_client_cache is not None
        and _state.ipc_client_cache[0] == _state.active_instance
    ):
        return _state.ipc_client_cache[1]
    client = AmiberryIPCClient(prefer_dbus=False, instance=_state.active_instance)
    _state.ipc_client_cache = (_state.active_instance, client)
    return client


@_asynccontextmanager
async def _ipc_context():
    """Async context manager for IPC calls with standardized error handling.

    Yields an IPC client. Maps IPC errors to appropriate HTTPExceptions.
    """
    try:
        yield _get_ipc_client()
    except IPCConnectionError as e:
        raise HTTPException(
            status_code=503, detail=f"IPC connection error: {str(e)}"
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}") from e


# FastAPI app
app = FastAPI(
    title="Amiberry HTTP API",
    description="REST API for controlling Amiberry emulator via HTTP - works with Siri Shortcuts, Google Assistant, Home Assistant, and more",
    version="1.0.0",
)

# Enable CORS for local clients (Siri Shortcuts, Home Assistant, etc.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://localhost:*",
        "http://127.0.0.1",
        "http://127.0.0.1:*",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Response models
class ConfigInfo(BaseModel):
    name: str
    source: str
    path: str


class DiskImage(BaseModel):
    name: str
    type: str
    path: str


class Savestate(BaseModel):
    name: str
    modified: str
    path: str


class LaunchRequest(BaseModel):
    config: str | None = None
    model: str | None = None
    disk_image: str | None = None
    lha_file: str | None = None
    autostart: bool = True


class LaunchWithLoggingRequest(LaunchRequest):
    log_name: str | None = None


class CreateConfigRequest(BaseModel):
    template: str = "A500"
    overrides: dict[str, str] | None = None


class ModifyConfigRequest(BaseModel):
    modifications: dict[str, str | None]


class LaunchCDRequest(BaseModel):
    cd_image: str | None = None
    search_term: str | None = None
    model: str = "CD32"
    autostart: bool = True


class DiskSwapperRequest(BaseModel):
    disk_images: list[str]
    model: str | None = None
    config: str | None = None
    autostart: bool = True


class CDImage(BaseModel):
    name: str
    type: str
    path: str


class LogFile(BaseModel):
    name: str
    modified: str
    size: int


class RomInfo(BaseModel):
    file: str
    filename: str
    size: int
    crc32: str
    md5: str
    identified: bool
    version: str | None = None
    revision: str | None = None
    model: str | None = None
    probable_type: str | None = None


class StatusResponse(BaseModel):
    success: bool
    message: str
    data: dict | None = None


def _ipc_success_or_raise(
    success: bool,
    message: str,
    failure_detail: str,
    data: dict | None = None,
) -> StatusResponse:
    """Return a StatusResponse on success or raise HTTPException on failure."""
    if success:
        return StatusResponse(success=True, message=message, data=data)
    raise HTTPException(status_code=500, detail=failure_detail)


def _require_config_path(config_name: str) -> Path:
    """Find a config path or raise 404."""
    path = _find_config_path(config_name)
    if not path:
        raise HTTPException(
            status_code=404, detail=f"Configuration '{config_name}' not found"
        )
    return path


def _validate_range(value: int, min_val: int, max_val: int, name: str) -> None:
    """Validate an integer is within range or raise 400."""
    if not min_val <= value <= max_val:
        raise HTTPException(
            status_code=400, detail=f"{name} must be {min_val}-{max_val}"
        )


# Platform process name constant
_PGREP_ARGS = ["-f", "Amiberry.app/Contents/MacOS/Amiberry"] if IS_MACOS else ["amiberry"]

# Mode map constants
_AUTOFIRE_MODES = {0: "off", 1: "normal", 2: "toggle", 3: "always", 4: "toggle_noaf"}
_DISPLAY_MODES = {0: "window", 1: "fullscreen", 2: "fullwindow"}
_SOUND_MODES = {0: "off", 1: "normal", 2: "stereo", 3: "best"}


class ActiveInstanceRequest(BaseModel):
    instance: int | None = None


# Runtime control request models
class RuntimeResetRequest(BaseModel):
    hard: bool = False


class RuntimeScreenshotRequest(BaseModel):
    filename: str


class RuntimeSaveStateRequest(BaseModel):
    state_file: str
    config_file: str


class RuntimeLoadStateRequest(BaseModel):
    state_file: str


class RuntimeInsertFloppyRequest(BaseModel):
    drive: int
    image_path: str


class RuntimeInsertCDRequest(BaseModel):
    image_path: str


class RuntimeSetConfigRequest(BaseModel):
    option: str
    value: str


class RuntimeEjectFloppyRequest(BaseModel):
    drive: int


class RuntimeSetVolumeRequest(BaseModel):
    volume: int


class RuntimeSetWarpRequest(BaseModel):
    enabled: bool


class RuntimeFrameAdvanceRequest(BaseModel):
    count: int = 1


class RuntimeSendMouseRequest(BaseModel):
    dx: int
    dy: int
    buttons: int = 0


class RuntimeSetMouseSpeedRequest(BaseModel):
    speed: int


class RuntimeSendKeyRequest(BaseModel):
    keycode: int
    state: int


class RuntimeTypeTextRequest(BaseModel):
    text: str
    delay_ms: int = 50

# Round 2 request models
class RuntimeQuickSaveRequest(BaseModel):
    slot: int = 0


class RuntimeQuickLoadRequest(BaseModel):
    slot: int = 0


class RuntimeSetJoyportModeRequest(BaseModel):
    port: int
    mode: int


class RuntimeSetAutofireRequest(BaseModel):
    port: int
    mode: int


class RuntimeSetDisplayModeRequest(BaseModel):
    mode: int


class RuntimeSetNTSCRequest(BaseModel):
    enabled: bool


class RuntimeSetSoundModeRequest(BaseModel):
    mode: int


# Round 3 request models
class RuntimeSetCPUSpeedRequest(BaseModel):
    speed: int


class RuntimeToggleRTGRequest(BaseModel):
    monid: int = 0


class RuntimeSetFloppySpeedRequest(BaseModel):
    speed: int


class RuntimeDiskWriteProtectRequest(BaseModel):
    drive: int
    protect: bool


class RuntimeSetChipsetRequest(BaseModel):
    chipset: str


# Round 4 request models - Memory and Window Control
class RuntimeSetChipMemRequest(BaseModel):
    size_kb: int


class RuntimeSetFastMemRequest(BaseModel):
    size_kb: int


class RuntimeSetSlowMemRequest(BaseModel):
    size_kb: int


class RuntimeSetZ3MemRequest(BaseModel):
    size_mb: int


class RuntimeSetCPUModelRequest(BaseModel):
    model: str


class RuntimeSetWindowSizeRequest(BaseModel):
    width: int
    height: int


class RuntimeSetScalingRequest(BaseModel):
    mode: int


class RuntimeSetLineModeRequest(BaseModel):
    mode: int


class RuntimeSetResolutionRequest(BaseModel):
    mode: int


# Round 5 - Autocrop and WHDLoad
class RuntimeSetAutocropRequest(BaseModel):
    enabled: bool


class RuntimeInsertWHDLoadRequest(BaseModel):
    path: str


# Round 6 - Debugging and Diagnostics
class RuntimeDebugStepRequest(BaseModel):
    count: int = 1


class RuntimeDisassembleRequest(BaseModel):
    address: str
    count: int = 10


class RuntimeSetBreakpointRequest(BaseModel):
    address: str


class RuntimeClearBreakpointRequest(BaseModel):
    address: str


# Autonomous troubleshooting request models
class WaitForExitRequest(BaseModel):
    timeout: int = Field(default=30, ge=1)


class RuntimeReadMemoryRequest(BaseModel):
    address: str
    width: int


class RuntimeWriteMemoryRequest(BaseModel):
    address: str
    width: int
    value: int


class RuntimeLoadConfigRequest(BaseModel):
    config_path: str


class RuntimeScreenshotViewRequest(BaseModel):
    filename: str | None = None


class TailLogRequest(BaseModel):
    log_name: str


class WaitForLogPatternRequest(BaseModel):
    log_name: str
    pattern: str
    timeout: int = Field(default=30, ge=1)


class GetCrashInfoRequest(BaseModel):
    log_name: str | None = None


class LaunchAndWaitRequest(LaunchRequest):
    timeout: int = Field(default=30, ge=1)


def _is_amiberry_running() -> bool:
    """Check if Amiberry process is currently running."""
    try:
        result = subprocess.run(["pgrep"] + _PGREP_ARGS, capture_output=True, text=True)
        return result.returncode == 0
    except Exception:
        return False


def _stop_amiberry() -> bool:
    """Stop the running Amiberry instance, preferring tracked process."""
    # First try the tracked process if available
    if _state.process is not None and _state.process.poll() is None:
        terminate_process(_state.process)
        return True
    # Fall back to pkill for externally started instances
    try:
        subprocess.run(["pkill"] + _PGREP_ARGS, check=False)
        return True
    except Exception:
        return False


# API Endpoints


@app.get("/")
async def root():
    """API root endpoint."""
    return {
        "name": "Amiberry HTTP API",
        "version": "1.0.0",
        "platform": platform.system(),
        "status": "running",
    }


@app.get("/status")
async def get_status():
    """Check if Amiberry is currently running."""
    running = await asyncio.to_thread(_is_amiberry_running)
    return StatusResponse(
        success=True,
        message=f"Amiberry is {'running' if running else 'not running'}",
        data={"running": running},
    )


@app.post("/stop")
async def stop():
    """Stop all running Amiberry instances."""
    if not await asyncio.to_thread(_is_amiberry_running):
        return StatusResponse(success=True, message="Amiberry is not running")

    success = await asyncio.to_thread(_stop_amiberry)
    if success:
        # Wait a bit for process to terminate
        await asyncio.sleep(1)
    return _ipc_success_or_raise(
        success, "Amiberry stopped successfully", "Failed to stop Amiberry"
    )


@app.get("/configs", response_model=list[ConfigInfo])
async def list_configs(include_system: bool = False):
    """List available Amiberry configuration files."""

    def _scan_configs() -> list[ConfigInfo]:
        configs = []
        # User configs
        if CONFIG_DIR.exists():
            for f in CONFIG_DIR.glob("*.uae"):
                configs.append(ConfigInfo(name=f.name, source="user", path=str(f)))
        # System configs (Linux only)
        if (
            IS_LINUX
            and include_system
            and SYSTEM_CONFIG_DIR
            and SYSTEM_CONFIG_DIR.exists()
        ):
            for f in SYSTEM_CONFIG_DIR.glob("*.uae"):
                configs.append(ConfigInfo(name=f.name, source="system", path=str(f)))
        return configs

    configs = await asyncio.to_thread(_scan_configs)
    return sorted(configs, key=lambda x: x.name)


@app.get("/configs/{config_name}")
async def get_config(config_name: str):
    """Get content of a specific configuration file."""
    config_path = _require_config_path(config_name)

    try:
        content = await asyncio.to_thread(config_path.read_text)
        return StatusResponse(
            success=True,
            message=f"Configuration: {config_name}",
            data={"content": content},
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error reading config: {str(e)}"
        ) from e


@app.get("/disk-images", response_model=list[DiskImage])
async def list_disk_images(search: str | None = None, type: str = "all"):
    """List available disk images."""
    results = await asyncio.to_thread(
        scan_disk_images, DISK_IMAGE_DIRS, type, search or ""
    )
    return [DiskImage(name=r["name"], type=r["type"], path=r["path"]) for r in results]


@app.get("/savestates", response_model=list[Savestate])
async def list_savestates(search: str | None = None):
    """List available savestate files."""
    search_term = search.lower() if search else ""

    def _scan_savestates() -> list[Savestate]:
        savestates = []
        if SAVESTATE_DIR.exists():
            for state in SAVESTATE_DIR.glob("**/*.uss"):
                if not search_term or search_term in state.name.lower():
                    mtime = state.stat().st_mtime
                    timestamp = format_log_timestamp(mtime)
                    savestates.append(
                        Savestate(name=state.name, modified=timestamp, path=str(state))
                    )
        return savestates

    savestates = await asyncio.to_thread(_scan_savestates)
    return sorted(savestates, key=lambda x: x.name)


@app.post("/launch")
async def launch_amiberry(request: LaunchRequest):
    """Launch Amiberry with specified configuration."""
    # Validate that either model, config, or lha_file is specified
    if not request.model and not request.config and not request.lha_file:
        raise HTTPException(
            status_code=400,
            detail="Either 'model', 'config', or 'lha_file' must be specified",
        )

    # Validate inputs
    config_path = None
    if request.model:
        if request.model not in SUPPORTED_MODELS:
            raise HTTPException(
                status_code=400,
                detail=f"Model must be one of: {', '.join(SUPPORTED_MODELS)}",
            )
    elif request.config:
        config_path = _find_config_path(request.config)
        if not config_path:
            raise HTTPException(
                status_code=404,
                detail=f"Configuration '{request.config}' not found",
            )

    if request.disk_image:
        disk_path = Path(request.disk_image)
        if not disk_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Disk image not found: {request.disk_image}",
            )

    if request.lha_file:
        lha_path = Path(request.lha_file)
        if not lha_path.exists():
            raise HTTPException(
                status_code=404, detail=f"LHA file not found: {request.lha_file}"
            )
        if not lha_path.suffix.lower() == ".lha":
            raise HTTPException(status_code=400, detail="File must have .lha extension")

    cmd = build_launch_command(
        model=request.model,
        config_path=config_path,
        disk_image=request.disk_image,
        lha_file=request.lha_file,
        autostart=request.autostart,
    )

    try:
        # Launch in background
        _state.close_log_handle()
        _state.process, _ = launch_process(cmd)
        _state.launch_cmd = cmd
        _state.log_path = None

        if request.model:
            message = f"Launched Amiberry with model: {request.model}"
        elif request.config:
            message = f"Launched Amiberry with config: {request.config}"
        elif request.lha_file:
            message = f"Launched Amiberry with LHA: {Path(request.lha_file).name}"
        else:
            message = "Launched Amiberry"

        if request.disk_image:
            message += f" | Disk: {Path(request.disk_image).name}"
        if request.lha_file and (request.model or request.config):
            message += f" | LHA: {Path(request.lha_file).name}"

        return StatusResponse(success=True, message=message)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error launching Amiberry: {str(e)}"
        ) from e


@app.get("/platform")
async def get_platform_info_endpoint():
    """Get platform and path information."""
    info = get_platform_info()
    return StatusResponse(success=True, message="Platform information", data=info)


# Convenience endpoints for automation


@app.post("/quick-launch/{model_or_config}")
async def quick_launch(model_or_config: str):
    """
    Quick launch endpoint for automation tools.
    If the parameter matches A500, A1200, or CD32, launches as model.
    Otherwise, treats it as a config name.
    """
    if model_or_config in SUPPORTED_MODELS:
        request = LaunchRequest(model=model_or_config, autostart=True)
    else:
        # Add .uae extension if not present
        config_name = (
            model_or_config
            if model_or_config.endswith(".uae")
            else f"{model_or_config}.uae"
        )
        request = LaunchRequest(config=config_name, autostart=True)

    return await launch_amiberry(request)


@app.post("/launch-lha")
async def launch_lha(lha_path: str):
    """
    Launch an .lha file directly with Amiberry.
    Amiberry will auto-extract, mount the contents, and set the appropriate configuration.
    """
    request = LaunchRequest(lha_file=lha_path, autostart=True)
    return await launch_amiberry(request)


# New Phase 1 endpoints


@app.post("/launch-with-logging")
async def launch_with_logging(request: LaunchWithLoggingRequest):
    """
    Launch Amiberry with console logging enabled, capturing output to a log file.
    Useful for debugging.
    """
    if not request.model and not request.config and not request.lha_file:
        raise HTTPException(
            status_code=400,
            detail="Either 'model', 'config', or 'lha_file' must be specified",
        )

    if request.model and request.model not in SUPPORTED_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Model must be one of: {', '.join(SUPPORTED_MODELS)}",
        )

    # Create log directory if needed
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Generate log filename
    log_name = request.log_name
    if not log_name:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        log_name = f"amiberry_{timestamp}.log"
    try:
        log_path = normalize_log_path(log_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Resolve config path if specified
    config_path = None
    if request.config:
        config_path = _find_config_path(request.config)
        if not config_path:
            raise HTTPException(
                status_code=404, detail=f"Configuration '{request.config}' not found"
            )

    if request.lha_file:
        lha_path = Path(request.lha_file)
        if not lha_path.exists():
            raise HTTPException(
                status_code=404, detail=f"LHA file not found: {request.lha_file}"
            )

    cmd = build_launch_command(
        model=request.model,
        config_path=config_path,
        disk_image=request.disk_image,
        lha_file=request.lha_file,
        autostart=request.autostart,
        with_logging=True,
    )

    try:
        _state.close_log_handle()
        _state.process, _state.log_file_handle = launch_process(cmd, log_path=log_path)
        _state.launch_cmd = cmd
        _state.log_path = log_path

        return StatusResponse(
            success=True,
            message="Launched Amiberry with logging enabled",
            data={"log_file": str(log_path), "command": " ".join(cmd)},
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error launching Amiberry: {str(e)}"
        ) from e


@app.get("/configs/{config_name}/parsed")
async def get_config_parsed(config_name: str, include_raw: bool = False):
    """Get a parsed configuration file with summary."""
    config_path = _require_config_path(config_name)

    try:
        config = await asyncio.to_thread(parse_uae_config, config_path)
        summary = get_config_summary(config)

        data = {"summary": summary}
        if include_raw:
            data["raw"] = config

        return StatusResponse(
            success=True,
            message=f"Parsed configuration: {config_name}",
            data=data,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error parsing config: {str(e)}"
        ) from e


@app.post("/configs/create/{config_name}")
async def create_config(config_name: str, request: CreateConfigRequest):
    """Create a new configuration file from a template."""
    if not config_name.endswith(".uae"):
        config_name += ".uae"

    config_path = (CONFIG_DIR / config_name).resolve()
    if not config_path.is_relative_to(CONFIG_DIR.resolve()):
        raise HTTPException(
            status_code=400, detail=f"Invalid config name: {config_name}"
        )

    if config_path.exists():
        raise HTTPException(
            status_code=409,
            detail=f"Configuration '{config_name}' already exists. Use PATCH to modify.",
        )

    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(
            lambda: create_config_from_template(
                config_path, request.template, request.overrides
            )
        )

        return StatusResponse(
            success=True,
            message=f"Created configuration: {config_name}",
            data={
                "path": str(config_path),
                "template": request.template,
                "overrides": request.overrides,
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error creating config: {str(e)}"
        ) from e


@app.patch("/configs/{config_name}")
async def modify_config(config_name: str, request: ModifyConfigRequest):
    """Modify specific options in an existing configuration file."""
    config_path = _require_config_path(config_name)

    try:
        await asyncio.to_thread(modify_uae_config, config_path, request.modifications)

        return StatusResponse(
            success=True,
            message=f"Modified configuration: {config_name}",
            data={"modifications": request.modifications},
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error modifying config: {str(e)}"
        ) from e


@app.post("/launch-whdload")
async def launch_whdload(
    search_term: str | None = None,
    exact_path: str | None = None,
    model: str = "A1200",
    autostart: bool = True,
):
    """Search for and launch a WHDLoad game (.lha file)."""
    if model not in SUPPORTED_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Model must be one of: {', '.join(SUPPORTED_MODELS)}",
        )

    lha_path = None

    if exact_path:
        lha_path = Path(exact_path)
        if not lha_path.exists():
            raise HTTPException(
                status_code=404, detail=f"LHA file not found: {exact_path}"
            )
    elif search_term:
        search_lower = search_term.lower()

        def _scan_lha_files() -> list[Path]:
            seen: set[str] = set()
            results: list[Path] = []
            for directory in DISK_IMAGE_DIRS:
                if directory.exists():
                    for pattern in ["**/*.lha", "**/*.LHA"]:
                        for lha in directory.glob(pattern):
                            if search_lower in lha.name.lower():
                                key = str(lha).lower()
                                if key not in seen:
                                    seen.add(key)
                                    results.append(lha)
            return results

        lha_files = await asyncio.to_thread(_scan_lha_files)

        if not lha_files:
            raise HTTPException(
                status_code=404,
                detail=f"No WHDLoad games found matching '{search_term}'",
            )

        if len(lha_files) > 1:
            matches = [{"name": f.name, "path": str(f)} for f in lha_files[:10]]
            return StatusResponse(
                success=False,
                message=f"Found {len(lha_files)} matches. Please specify exact_path.",
                data={"matches": matches},
            )

        lha_path = lha_files[0]
    else:
        raise HTTPException(
            status_code=400,
            detail="Either 'search_term' or 'exact_path' must be specified",
        )

    cmd = build_launch_command(
        model=model,
        lha_file=str(lha_path),
        autostart=autostart,
    )

    try:
        _state.close_log_handle()
        _state.process, _ = launch_process(cmd)
        _state.launch_cmd = cmd
        _state.log_path = None

        return StatusResponse(
            success=True,
            message=f"Launched WHDLoad game: {lha_path.name}",
            data={"game": lha_path.name, "model": model},
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error launching WHDLoad game: {str(e)}"
        ) from e


@app.post("/launch-cd")
async def launch_cd(request: LaunchCDRequest):
    """Launch a CD image with automatic CD32/CDTV detection."""
    if request.model not in SUPPORTED_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Model must be one of: {', '.join(SUPPORTED_MODELS)}",
        )

    cd_path = None

    if request.cd_image:
        cd_path = Path(request.cd_image)
        if not cd_path.exists():
            raise HTTPException(
                status_code=404, detail=f"CD image not found: {request.cd_image}"
            )
    elif request.search_term:
        search_lower = request.search_term.lower()
        search_dirs = DISK_IMAGE_DIRS + [AMIBERRY_HOME / "CD"]
        if IS_MACOS:
            search_dirs.append(AMIBERRY_HOME / "CDs")

        def _scan_cd_files() -> list[Path]:
            images = scan_disk_images(search_dirs, "cd", request.search_term)
            return [Path(img["path"]) for img in images]

        cd_files = await asyncio.to_thread(_scan_cd_files)

        if not cd_files:
            raise HTTPException(
                status_code=404,
                detail=f"No CD images found matching '{request.search_term}'",
            )

        if len(cd_files) > 1:
            matches = [{"name": f.name, "path": str(f)} for f in cd_files[:10]]
            return StatusResponse(
                success=False,
                message=f"Found {len(cd_files)} matches. Please specify cd_image path.",
                data={"matches": matches},
            )

        cd_path = cd_files[0]
    else:
        raise HTTPException(
            status_code=400,
            detail="Either 'cd_image' or 'search_term' must be specified",
        )

    cmd = build_launch_command(
        model=request.model,
        cd_image=str(cd_path),
        autostart=request.autostart,
    )

    try:
        _state.close_log_handle()
        _state.process, _ = launch_process(cmd)
        _state.launch_cmd = cmd
        _state.log_path = None

        return StatusResponse(
            success=True,
            message=f"Launched CD image: {cd_path.name}",
            data={"cd_image": cd_path.name, "model": request.model},
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error launching CD image: {str(e)}"
        ) from e


@app.get("/cd-images", response_model=list[CDImage])
async def list_cd_images(search: str | None = None):
    """List available CD images."""
    search_dirs = DISK_IMAGE_DIRS + [AMIBERRY_HOME / "CD"]
    if IS_MACOS:
        search_dirs.append(AMIBERRY_HOME / "CDs")

    results = await asyncio.to_thread(scan_disk_images, search_dirs, "cd", search or "")
    return [CDImage(name=r["name"], type=r["type"], path=r["path"]) for r in results]


@app.post("/disk-swapper")
async def launch_with_disk_swapper(request: DiskSwapperRequest):
    """Launch Amiberry with disk swapper configured for multi-disk games."""
    if len(request.disk_images) < 2:
        raise HTTPException(
            status_code=400,
            detail="Disk swapper requires at least 2 disk images",
        )

    # Verify all disk images exist
    verified_paths = []
    for img in request.disk_images:
        img_path = Path(img)
        if not img_path.exists():
            raise HTTPException(status_code=404, detail=f"Disk image not found: {img}")
        verified_paths.append(str(img_path))

    # Resolve config path if specified
    config_path = None
    if request.config:
        config_path = _find_config_path(request.config)
        if not config_path:
            raise HTTPException(
                status_code=404, detail=f"Configuration '{request.config}' not found"
            )

    cmd = build_launch_command(
        model=request.model or ("A500" if not request.config else None),
        config_path=config_path,
        disk_image=verified_paths[0],
        disk_swapper=verified_paths,
        autostart=request.autostart,
    )

    try:
        _state.close_log_handle()
        _state.process, _ = launch_process(cmd)
        _state.launch_cmd = cmd
        _state.log_path = None

        return StatusResponse(
            success=True,
            message=f"Launched with disk swapper ({len(verified_paths)} disks)",
            data={"disks": [Path(p).name for p in verified_paths]},
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error launching with disk swapper: {str(e)}"
        ) from e


@app.get("/logs", response_model=list[LogFile])
async def list_logs():
    """List available log files from previous launches."""
    if not LOG_DIR.exists():
        return []

    def _scan_logs():
        result = []
        for log in LOG_DIR.glob("*.log"):
            st = log.stat()
            timestamp = format_log_timestamp(st.st_mtime)
            result.append(LogFile(name=log.name, modified=timestamp, size=st.st_size))
        return sorted(result, key=lambda x: x.modified, reverse=True)

    return await asyncio.to_thread(_scan_logs)


@app.get("/logs/{log_name}")
async def get_log_content(log_name: str, tail_lines: int | None = None):
    """Get the content of a log file."""
    try:
        log_path = normalize_log_path(log_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if not log_path.exists():
        raise HTTPException(status_code=404, detail=f"Log file not found: {log_name}")

    try:
        content = await asyncio.to_thread(log_path.read_text, errors="replace")

        if tail_lines and tail_lines > 0:
            lines = content.splitlines()
            content = "\n".join(lines[-tail_lines:])

        return StatusResponse(
            success=True,
            message=f"Log file: {log_name}",
            data={"content": content, "lines": len(content.splitlines())},
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error reading log: {str(e)}"
        ) from e


# Phase 2 endpoints


@app.get("/savestates/{savestate_name}/inspect")
async def inspect_savestate_endpoint(savestate_name: str):
    """Inspect a savestate file and extract metadata."""
    if not savestate_name.endswith(".uss"):
        savestate_name += ".uss"
    path = (SAVESTATE_DIR / savestate_name).resolve()

    # Prevent path traversal
    if not _is_path_within(path, SAVESTATE_DIR):
        raise HTTPException(status_code=400, detail="Invalid savestate name")

    if not path.exists():
        raise HTTPException(
            status_code=404, detail=f"Savestate not found: {savestate_name}"
        )

    try:
        metadata = await asyncio.to_thread(inspect_savestate, path)
        summary = get_savestate_summary(metadata)

        return StatusResponse(
            success=True,
            message=f"Inspected savestate: {path.name}",
            data={"metadata": metadata, "summary": summary},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error inspecting savestate: {str(e)}"
        ) from e


@app.get("/roms", response_model=list[RomInfo])
async def list_roms(directory: str | None = None):
    """List and identify ROM files in the ROMs directory."""
    rom_dir = ROM_DIR
    if directory:
        candidate = Path(directory).resolve()
        if not _is_path_within(candidate, AMIBERRY_HOME):
            raise HTTPException(
                status_code=400,
                detail="ROM directory must be within the Amiberry home directory",
            )
        rom_dir = candidate

    if not rom_dir.exists():
        return []

    try:
        roms = await asyncio.to_thread(scan_rom_directory, rom_dir)
        return [RomInfo(**rom) for rom in roms if not rom.get("error")]
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error scanning ROMs: {str(e)}"
        ) from e


@app.get("/roms/identify")
async def identify_rom_endpoint(rom_path: str):
    """Identify a specific ROM file by its checksum."""
    path = Path(rom_path).resolve()

    # Prevent path traversal — ROM must be within Amiberry home
    if not _is_path_within(path, AMIBERRY_HOME):
        raise HTTPException(
            status_code=400,
            detail="ROM path must be within the Amiberry home directory",
        )

    if not path.exists():
        raise HTTPException(status_code=404, detail=f"ROM file not found: {rom_path}")

    try:
        rom_info = await asyncio.to_thread(identify_rom, path)
        summary = get_rom_summary(rom_info)

        return StatusResponse(
            success=True,
            message=f"Identified ROM: {path.name}",
            data={"rom": rom_info, "summary": summary},
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error identifying ROM: {str(e)}"
        ) from e


@app.get("/version")
async def get_amiberry_version_endpoint():
    """Get Amiberry version and build information."""
    version_info = await detect_amiberry_version()

    if (
        not version_info.get("available")
        and "not found" in version_info.get("error", "").lower()
    ):
        raise HTTPException(
            status_code=404,
            detail=f"Amiberry binary not found at {EMULATOR_BINARY}",
        )

    return StatusResponse(
        success=True,
        message="Amiberry version information",
        data=version_info,
    )


# Runtime control endpoints (IPC)


@app.get("/runtime/active-instance")
async def get_active_instance():
    """Get the currently active Amiberry instance being controlled."""
    if _state.active_instance is None:
        return StatusResponse(
            success=True,
            message="Auto-discovering (no specific instance set)",
            data={"instance": None},
        )
    return StatusResponse(
        success=True,
        message=f"Active instance: {_state.active_instance}",
        data={"instance": _state.active_instance},
    )


@app.post("/runtime/active-instance")
async def set_active_instance(request: ActiveInstanceRequest):
    """Set the active Amiberry instance to control (e.g. 0, 1). Set to null to auto-discover."""

    _state.active_instance = request.instance
    status = (
        f"Active instance set to {request.instance}"
        if request.instance is not None
        else "Active instance set to auto-discover"
    )
    return StatusResponse(
        success=True, message=status, data={"instance": _state.active_instance}
    )


@app.get("/runtime/status")
async def get_runtime_status():
    """
    Get the current status of a running Amiberry emulation.
    Requires Amiberry to be running with IPC enabled (USE_IPC_SOCKET).
    """
    async with _ipc_context() as client:
        status = await client.get_status()

        return StatusResponse(
            success=True,
            message="Runtime status retrieved",
            data={
                "paused": status.get("Paused", False),
                "config": status.get("Config", ""),
                "floppies": {
                    f"DF{i}": status.get(f"Floppy{i}")
                    for i in range(4)
                    if f"Floppy{i}" in status
                },
            },
        )


@app.post("/runtime/pause")
async def pause_emulation():
    """
    Pause a running Amiberry emulation.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        success = await client.pause()
        return _ipc_success_or_raise(
            success, "Emulation paused", "Failed to pause emulation"
        )


@app.post("/runtime/resume")
async def resume_emulation():
    """
    Resume a paused Amiberry emulation.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        success = await client.resume()
        return _ipc_success_or_raise(
            success, "Emulation resumed", "Failed to resume emulation"
        )


@app.post("/runtime/reset")
async def reset_emulation(request: RuntimeResetRequest):
    """
    Reset the running Amiberry emulation.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        success = await client.reset(hard=request.hard)
        reset_type = "hard" if request.hard else "soft"
        return _ipc_success_or_raise(
            success, f"Emulation {reset_type} reset", f"Failed to {reset_type} reset"
        )


@app.post("/runtime/quit")
async def quit_emulation():
    """
    Quit the running Amiberry emulation.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        success = await client.quit()
        return _ipc_success_or_raise(
            success, "Amiberry quit command sent", "Failed to quit Amiberry"
        )


@app.post("/runtime/screenshot")
async def runtime_screenshot(request: RuntimeScreenshotRequest):
    """
    Take a screenshot of the running emulation.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        success = await client.screenshot(request.filename)
        return _ipc_success_or_raise(
            success,
            "Screenshot taken",
            "Failed to take screenshot",
            data={"filename": request.filename},
        )


@app.post("/runtime/save-state")
async def runtime_save_state(request: RuntimeSaveStateRequest):
    """
    Save the current emulation state while running.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        success = await client.save_state(request.state_file, request.config_file)
        return _ipc_success_or_raise(
            success,
            "State saved",
            "Failed to save state",
            data={
                "state_file": request.state_file,
                "config_file": request.config_file,
            },
        )


@app.post("/runtime/load-state")
async def runtime_load_state(request: RuntimeLoadStateRequest):
    """
    Load a savestate into the running emulation.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        success = await client.load_state(request.state_file)
        return _ipc_success_or_raise(
            success,
            "Loading state",
            "Failed to load state",
            data={"state_file": request.state_file},
        )


@app.post("/runtime/insert-floppy")
async def runtime_insert_floppy(request: RuntimeInsertFloppyRequest):
    """
    Insert a floppy disk image into a running emulation.
    Requires Amiberry to be running with IPC enabled.
    """
    _validate_range(request.drive, 0, 3, "Drive")

    async with _ipc_context() as client:
        success = await client.insert_floppy(request.drive, request.image_path)
        return _ipc_success_or_raise(
            success,
            f"Inserted disk into DF{request.drive}:",
            "Failed to insert floppy",
            data={
                "drive": request.drive,
                "image": Path(request.image_path).name,
            },
        )


@app.post("/runtime/insert-cd")
async def runtime_insert_cd(request: RuntimeInsertCDRequest):
    """
    Insert a CD image into a running emulation.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        success = await client.insert_cd(request.image_path)
        return _ipc_success_or_raise(
            success,
            "CD inserted",
            "Failed to insert CD",
            data={"image": Path(request.image_path).name},
        )


@app.get("/runtime/config/{option}")
async def runtime_get_config(option: str):
    """
    Get a configuration option from the running emulation.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        value = await client.get_config(option)

        if value is not None:
            return StatusResponse(
                success=True,
                message=f"Config option: {option}",
                data={"option": option, "value": value},
            )
        else:
            raise HTTPException(status_code=404, detail=f"Unknown option: {option}")


@app.post("/runtime/config")
async def runtime_set_config(request: RuntimeSetConfigRequest):
    """
    Set a configuration option on the running emulation.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        success = await client.set_config(request.option, request.value)
        if not success:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to set {request.option}. Unknown option or invalid value.",
            )
        return StatusResponse(
            success=True,
            message=f"Set {request.option} = {request.value}",
            data={"option": request.option, "value": request.value},
        )


@app.get("/runtime/ipc-check")
async def check_ipc_connection():
    """
    Check if Amiberry IPC is available and get connection status.
    """
    try:
        client = _get_ipc_client()

        result = {
            "transport": client.transport,
            "socket_available": client.is_available(),
            "connected": False,
        }

        if client.is_available():
            try:
                status = await client.get_status()
                result["connected"] = True
                result["paused"] = status.get("Paused", False)
            except Exception:
                result["connected"] = False

        return StatusResponse(
            success=True,
            message="IPC connection check",
            data=result,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error checking IPC: {str(e)}"
        ) from e


# New runtime control endpoints


@app.post("/runtime/eject-floppy")
async def runtime_eject_floppy(request: RuntimeEjectFloppyRequest):
    """
    Eject a floppy disk from a drive in the running emulation.
    Requires Amiberry to be running with IPC enabled.
    """
    _validate_range(request.drive, 0, 3, "Drive")

    async with _ipc_context() as client:
        success = await client.eject_floppy(request.drive)
        return _ipc_success_or_raise(
            success,
            f"Ejected disk from DF{request.drive}:",
            "Failed to eject floppy",
            data={"drive": request.drive},
        )


@app.post("/runtime/eject-cd")
async def runtime_eject_cd():
    """
    Eject the CD from the running emulation.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        success = await client.eject_cd()
        return _ipc_success_or_raise(success, "CD ejected", "Failed to eject CD")


@app.get("/runtime/list-floppies")
async def runtime_list_floppies():
    """
    List all floppy drives and their contents.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        floppies = await client.list_floppies()

        return StatusResponse(
            success=True,
            message="Floppy drives",
            data={"floppies": floppies},
        )


@app.get("/runtime/configs")
async def runtime_list_configs():
    """
    List available configuration files from the running emulation.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        configs = await client.list_configs()

        return StatusResponse(
            success=True,
            message="Available configurations",
            data={"configs": configs},
        )


@app.get("/runtime/volume")
async def runtime_get_volume():
    """
    Get the current master volume.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        volume = await client.get_volume()
        if volume is None:
            raise HTTPException(status_code=500, detail="Failed to get volume")

        return StatusResponse(
            success=True,
            message=f"Volume: {volume}%",
            data={"volume": volume},
        )


@app.post("/runtime/volume")
async def runtime_set_volume(request: RuntimeSetVolumeRequest):
    """
    Set the master volume (0-100).
    Requires Amiberry to be running with IPC enabled.
    """
    _validate_range(request.volume, 0, 100, "Volume")

    async with _ipc_context() as client:
        success = await client.set_volume(request.volume)
        return _ipc_success_or_raise(
            success,
            f"Volume set to {request.volume}%",
            "Failed to set volume",
            data={"volume": request.volume},
        )


@app.post("/runtime/mute")
async def runtime_mute():
    """
    Mute the audio.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        success = await client.mute()
        return _ipc_success_or_raise(success, "Audio muted", "Failed to mute")


@app.post("/runtime/unmute")
async def runtime_unmute():
    """
    Unmute the audio.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        success = await client.unmute()
        return _ipc_success_or_raise(success, "Audio unmuted", "Failed to unmute")


@app.post("/runtime/fullscreen")
async def runtime_toggle_fullscreen():
    """
    Toggle fullscreen/windowed mode.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        success = await client.toggle_fullscreen()
        return _ipc_success_or_raise(
            success, "Fullscreen toggled", "Failed to toggle fullscreen"
        )


@app.get("/runtime/warp")
async def runtime_get_warp():
    """
    Get the current warp mode status.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        enabled = await client.get_warp()
        if enabled is None:
            raise HTTPException(status_code=500, detail="Failed to get warp mode")

        return StatusResponse(
            success=True,
            message=f"Warp mode: {'enabled' if enabled else 'disabled'}",
            data={"enabled": enabled},
        )


@app.post("/runtime/warp")
async def runtime_set_warp(request: RuntimeSetWarpRequest):
    """
    Enable or disable warp mode.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        success = await client.set_warp(request.enabled)
        status = "enabled" if request.enabled else "disabled"
        return _ipc_success_or_raise(
            success,
            f"Warp mode {status}",
            "Failed to set warp mode",
            data={"enabled": request.enabled},
        )


@app.get("/runtime/version")
async def runtime_get_version():
    """
    Get Amiberry version info from the running emulation.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        version_info = await client.get_version()

        return StatusResponse(
            success=True,
            message="Amiberry version info",
            data=version_info,
        )


@app.get("/runtime/ping")
async def runtime_ping():
    """
    Ping the running Amiberry instance to test connectivity.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        success = await client.ping()
        return _ipc_success_or_raise(success, "PONG", "Ping failed")


@app.post("/runtime/frame-advance")
async def runtime_frame_advance(request: RuntimeFrameAdvanceRequest):
    """
    Advance N frames when emulation is paused.
    Requires Amiberry to be running with IPC enabled.
    """
    if request.count < 1:
        raise HTTPException(status_code=400, detail="Count must be at least 1")

    async with _ipc_context() as client:
        success = await client.frame_advance(request.count)
        return _ipc_success_or_raise(
            success,
            f"Advanced {request.count} frame(s)",
            "Failed to advance frames",
            data={"count": request.count},
        )


@app.post("/runtime/key")
async def runtime_send_key(request: RuntimeSendKeyRequest):
    """
    Send keyboard input to the emulation.
    State: 0=release, 1=press.
    Requires Amiberry to be running with IPC enabled.
    """
    if request.state not in (0, 1):
        raise HTTPException(status_code=400, detail="State must be 0 or 1")

    async with _ipc_context() as client:
        success = await client.send_key(request.keycode, request.state)
        action = "pressed" if request.state == 1 else "released"
        return _ipc_success_or_raise(
            success,
            f"Key {request.keycode} {action}",
            "Failed to send key",
            data={"keycode": request.keycode, "state": request.state},
        )


@app.post("/runtime/type")
async def runtime_type_text(request: RuntimeTypeTextRequest):
    """
    Type a string of text into the emulation character by character.
    Handles uppercase (via Shift) and common symbols.
    Requires Amiberry to be running with IPC enabled.
    """
    if not request.text:
        raise HTTPException(status_code=400, detail="Text must not be empty")
    if not 10 <= request.delay_ms <= 1000:
        raise HTTPException(
            status_code=400, detail="delay_ms must be between 10 and 1000"
        )

    delay = request.delay_ms / 1000.0
    async with _ipc_context() as client:
        typed, skipped = await client.type_text(request.text, delay=delay)
        return _ipc_success_or_raise(
            typed > 0,
            f"Typed {typed} character(s)",
            "Failed to type text (no characters typed)",
            data={"typed": typed, "skipped": skipped},
        )
@app.post("/runtime/mouse")
async def runtime_send_mouse(request: RuntimeSendMouseRequest):
    """
    Send mouse input to the emulation.
    Buttons: bit0=Left, bit1=Right, bit2=Middle.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        success = await client.send_mouse(request.dx, request.dy, request.buttons)
        return _ipc_success_or_raise(
            success,
            f"Mouse moved ({request.dx}, {request.dy})",
            "Failed to send mouse input",
            data={"dx": request.dx, "dy": request.dy, "buttons": request.buttons},
        )


@app.post("/runtime/mouse-speed")
async def runtime_set_mouse_speed(request: RuntimeSetMouseSpeedRequest):
    """
    Set mouse sensitivity (10-200).
    Requires Amiberry to be running with IPC enabled.
    """
    _validate_range(request.speed, 10, 200, "Speed")

    async with _ipc_context() as client:
        success = await client.set_mouse_speed(request.speed)
        return _ipc_success_or_raise(
            success,
            f"Mouse speed set to {request.speed}",
            "Failed to set mouse speed",
            data={"speed": request.speed},
        )


# Round 2 runtime control endpoints


@app.post("/runtime/quicksave")
async def runtime_quicksave(request: RuntimeQuickSaveRequest):
    """
    Quick save to a slot (0-9).
    Requires Amiberry to be running with IPC enabled.
    """
    _validate_range(request.slot, 0, 9, "Slot")

    async with _ipc_context() as client:
        success = await client.quicksave(request.slot)
        return _ipc_success_or_raise(
            success,
            f"Quick saved to slot {request.slot}",
            "Failed to quick save",
            data={"slot": request.slot},
        )


@app.post("/runtime/quickload")
async def runtime_quickload(request: RuntimeQuickLoadRequest):
    """
    Quick load from a slot (0-9).
    Requires Amiberry to be running with IPC enabled.
    """
    _validate_range(request.slot, 0, 9, "Slot")

    async with _ipc_context() as client:
        success = await client.quickload(request.slot)
        return _ipc_success_or_raise(
            success,
            f"Quick loading from slot {request.slot}",
            "Failed to quick load",
            data={"slot": request.slot},
        )


@app.get("/runtime/joyport/{port}")
async def runtime_get_joyport_mode(port: int):
    """
    Get joystick port mode.
    Requires Amiberry to be running with IPC enabled.
    """
    _validate_range(port, 0, 3, "Port")

    async with _ipc_context() as client:
        result = await client.get_joyport_mode(port)

        if result:
            mode, mode_name = result
            return StatusResponse(
                success=True,
                message=f"Port {port} mode: {mode_name}",
                data={"port": port, "mode": mode, "mode_name": mode_name},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get port mode")


@app.post("/runtime/joyport")
async def runtime_set_joyport_mode(request: RuntimeSetJoyportModeRequest):
    """
    Set joystick port mode.
    Modes: 0=default, 2=mouse, 3=joystick, 4=gamepad, 7=cd32.
    Requires Amiberry to be running with IPC enabled.
    """
    _validate_range(request.port, 0, 3, "Port")
    _validate_range(request.mode, 0, 8, "Mode")

    async with _ipc_context() as client:
        success = await client.set_joyport_mode(request.port, request.mode)
        return _ipc_success_or_raise(
            success,
            f"Port {request.port} mode set to {request.mode}",
            "Failed to set port mode",
            data={"port": request.port, "mode": request.mode},
        )


@app.get("/runtime/autofire/{port}")
async def runtime_get_autofire(port: int):
    """
    Get autofire mode for a port.
    Requires Amiberry to be running with IPC enabled.
    """
    _validate_range(port, 0, 3, "Port")

    async with _ipc_context() as client:
        mode = await client.get_autofire(port)

        if mode is not None:
            return StatusResponse(
                success=True,
                message=f"Port {port} autofire: {_AUTOFIRE_MODES.get(mode, 'unknown')}",
                data={
                    "port": port,
                    "mode": mode,
                    "mode_name": _AUTOFIRE_MODES.get(mode, "unknown"),
                },
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get autofire mode")


@app.post("/runtime/autofire")
async def runtime_set_autofire(request: RuntimeSetAutofireRequest):
    """
    Set autofire mode for a port.
    Modes: 0=off, 1=normal, 2=toggle, 3=always, 4=toggle_noaf.
    Requires Amiberry to be running with IPC enabled.
    """
    _validate_range(request.port, 0, 3, "Port")
    _validate_range(request.mode, 0, 4, "Mode")

    async with _ipc_context() as client:
        success = await client.set_autofire(request.port, request.mode)
        return _ipc_success_or_raise(
            success,
            f"Port {request.port} autofire set to {request.mode}",
            "Failed to set autofire mode",
            data={"port": request.port, "mode": request.mode},
        )


@app.get("/runtime/led-status")
async def runtime_get_led_status():
    """
    Get all LED states (power, floppy, HD, CD, caps).
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        status = await client.get_led_status()

        return StatusResponse(
            success=True,
            message="LED status",
            data={"leds": status},
        )


@app.get("/runtime/harddrives")
async def runtime_list_harddrives():
    """
    List all mounted hard drives.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        drives = await client.list_harddrives()

        return StatusResponse(
            success=True,
            message="Mounted hard drives",
            data={"drives": drives},
        )


@app.get("/runtime/display-mode")
async def runtime_get_display_mode():
    """
    Get current display mode.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        result = await client.get_display_mode()

        if result:
            mode, mode_name = result
            return StatusResponse(
                success=True,
                message=f"Display mode: {mode_name}",
                data={"mode": mode, "mode_name": mode_name},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get display mode")


@app.post("/runtime/display-mode")
async def runtime_set_display_mode(request: RuntimeSetDisplayModeRequest):
    """
    Set display mode.
    Modes: 0=window, 1=fullscreen, 2=fullwindow.
    Requires Amiberry to be running with IPC enabled.
    """
    _validate_range(request.mode, 0, 2, "Mode")

    async with _ipc_context() as client:
        success = await client.set_display_mode(request.mode)
        return _ipc_success_or_raise(
            success,
            f"Display mode set to {_DISPLAY_MODES.get(request.mode)}",
            "Failed to set display mode",
            data={"mode": request.mode, "mode_name": _DISPLAY_MODES.get(request.mode)},
        )


@app.get("/runtime/ntsc")
async def runtime_get_ntsc():
    """
    Get current video mode (PAL or NTSC).
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        result = await client.get_ntsc()

        if result:
            is_ntsc, mode_name = result
            return StatusResponse(
                success=True,
                message=f"Video mode: {mode_name}",
                data={"ntsc": is_ntsc, "mode_name": mode_name},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get video mode")


@app.post("/runtime/ntsc")
async def runtime_set_ntsc(request: RuntimeSetNTSCRequest):
    """
    Set video mode to PAL or NTSC.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        success = await client.set_ntsc(request.enabled)
        mode = "NTSC" if request.enabled else "PAL"
        return _ipc_success_or_raise(
            success,
            f"Video mode set to {mode}",
            f"Failed to set video mode to {mode}",
            data={"ntsc": request.enabled, "mode_name": mode},
        )


@app.get("/runtime/sound-mode")
async def runtime_get_sound_mode():
    """
    Get current sound mode.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        result = await client.get_sound_mode()

        if result:
            mode, mode_name = result
            return StatusResponse(
                success=True,
                message=f"Sound mode: {mode_name}",
                data={"mode": mode, "mode_name": mode_name},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get sound mode")


@app.post("/runtime/sound-mode")
async def runtime_set_sound_mode(request: RuntimeSetSoundModeRequest):
    """
    Set sound mode.
    Modes: 0=off, 1=normal, 2=stereo, 3=best.
    Requires Amiberry to be running with IPC enabled.
    """
    _validate_range(request.mode, 0, 3, "Mode")

    async with _ipc_context() as client:
        success = await client.set_sound_mode(request.mode)
        return _ipc_success_or_raise(
            success,
            f"Sound mode set to {_SOUND_MODES.get(request.mode)}",
            "Failed to set sound mode",
            data={"mode": request.mode, "mode_name": _SOUND_MODES.get(request.mode)},
        )


# Round 3 runtime control endpoints


@app.post("/runtime/mouse-grab")
async def runtime_toggle_mouse_grab():
    """
    Toggle mouse capture.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        success = await client.toggle_mouse_grab()
        return _ipc_success_or_raise(
            success, "Mouse grab toggled", "Failed to toggle mouse grab"
        )


@app.get("/runtime/mouse-speed")
async def runtime_get_mouse_speed():
    """
    Get current mouse speed.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        speed = await client.get_mouse_speed()

        if speed is not None:
            return StatusResponse(
                success=True,
                message=f"Mouse speed: {speed}",
                data={"speed": speed},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get mouse speed")


@app.get("/runtime/cpu-speed")
async def runtime_get_cpu_speed():
    """
    Get current CPU speed setting.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        result = await client.get_cpu_speed()

        if result:
            speed, desc = result
            return StatusResponse(
                success=True,
                message=f"CPU speed: {desc}",
                data={"speed": speed, "description": desc},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get CPU speed")


@app.post("/runtime/cpu-speed")
async def runtime_set_cpu_speed(request: RuntimeSetCPUSpeedRequest):
    """
    Set CPU speed.
    Speed: -1=max, 0=cycle-exact, >0=percentage.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        success = await client.set_cpu_speed(request.speed)
        return _ipc_success_or_raise(
            success,
            f"CPU speed set to {request.speed}",
            "Failed to set CPU speed",
            data={"speed": request.speed},
        )


@app.post("/runtime/rtg")
async def runtime_toggle_rtg(request: RuntimeToggleRTGRequest | None = None):
    """
    Toggle between RTG and chipset display.
    Requires Amiberry to be running with IPC enabled.
    """
    if request is None:
        request = RuntimeToggleRTGRequest()
    async with _ipc_context() as client:
        result = await client.toggle_rtg(request.monid)

        if result:
            return StatusResponse(
                success=True,
                message=f"Display mode: {result}",
                data={"mode": result},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to toggle RTG")


@app.get("/runtime/floppy-speed")
async def runtime_get_floppy_speed():
    """
    Get current floppy drive speed.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        result = await client.get_floppy_speed()

        if result:
            speed, desc = result
            return StatusResponse(
                success=True,
                message=f"Floppy speed: {desc}",
                data={"speed": speed, "description": desc},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get floppy speed")


@app.post("/runtime/floppy-speed")
async def runtime_set_floppy_speed(request: RuntimeSetFloppySpeedRequest):
    """
    Set floppy drive speed.
    Speed: 0=turbo, 100=1x, 200=2x, 400=4x, 800=8x.
    Requires Amiberry to be running with IPC enabled.
    """
    if request.speed not in (0, 100, 200, 400, 800):
        raise HTTPException(
            status_code=400, detail="Speed must be 0, 100, 200, 400, or 800"
        )

    async with _ipc_context() as client:
        success = await client.set_floppy_speed(request.speed)

        descs = {0: "turbo", 100: "1x", 200: "2x", 400: "4x", 800: "8x"}
        return _ipc_success_or_raise(
            success,
            f"Floppy speed set to {descs.get(request.speed)}",
            "Failed to set floppy speed",
            data={"speed": request.speed, "description": descs.get(request.speed)},
        )


@app.get("/runtime/disk-write-protect/{drive}")
async def runtime_get_disk_write_protect(drive: int):
    """
    Get write protection status for a floppy disk.
    Requires Amiberry to be running with IPC enabled.
    """
    _validate_range(drive, 0, 3, "Drive")

    async with _ipc_context() as client:
        result = await client.get_disk_write_protect(drive)

        if result:
            is_protected, status = result
            return StatusResponse(
                success=True,
                message=f"Drive DF{drive}: {status}",
                data={"drive": drive, "protected": is_protected, "status": status},
            )
        else:
            raise HTTPException(
                status_code=500, detail="Failed to get write protection status"
            )


@app.post("/runtime/disk-write-protect")
async def runtime_disk_write_protect(request: RuntimeDiskWriteProtectRequest):
    """
    Set write protection on a floppy disk.
    Requires Amiberry to be running with IPC enabled.
    """
    _validate_range(request.drive, 0, 3, "Drive")

    async with _ipc_context() as client:
        success = await client.disk_write_protect(request.drive, request.protect)
        status = "protected" if request.protect else "writable"
        return _ipc_success_or_raise(
            success,
            f"Drive DF{request.drive} set to {status}",
            "Failed to set write protection",
            data={"drive": request.drive, "protected": request.protect},
        )


@app.post("/runtime/status-line")
async def runtime_toggle_status_line():
    """
    Toggle on-screen status line (cycle: off/chipset/rtg/both).
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        result = await client.toggle_status_line()

        if result:
            mode, mode_name = result
            return StatusResponse(
                success=True,
                message=f"Status line: {mode_name}",
                data={"mode": mode, "mode_name": mode_name},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to toggle status line")


@app.get("/runtime/chipset")
async def runtime_get_chipset():
    """
    Get current chipset.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        result = await client.get_chipset()

        if result:
            mask, name = result
            return StatusResponse(
                success=True,
                message=f"Chipset: {name}",
                data={"mask": mask, "name": name},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get chipset")


@app.post("/runtime/chipset")
async def runtime_set_chipset(request: RuntimeSetChipsetRequest):
    """
    Set chipset.
    Valid values: OCS, ECS_AGNUS, ECS_DENISE, ECS, AGA.
    Requires Amiberry to be running with IPC enabled.
    """
    valid = ("OCS", "ECS_AGNUS", "ECS_DENISE", "ECS", "AGA")
    if request.chipset.upper() not in valid:
        raise HTTPException(status_code=400, detail=f"Chipset must be one of: {valid}")

    async with _ipc_context() as client:
        success = await client.set_chipset(request.chipset)
        return _ipc_success_or_raise(
            success,
            f"Chipset set to {request.chipset.upper()}",
            "Failed to set chipset",
            data={"chipset": request.chipset.upper()},
        )


@app.get("/runtime/memory-config")
async def runtime_get_memory_config():
    """
    Get all memory sizes (chip, fast, bogo, z3, rtg).
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        config = await client.get_memory_config()

        return StatusResponse(
            success=True,
            message="Memory configuration",
            data={"memory": config},
        )


@app.get("/runtime/fps")
async def runtime_get_fps():
    """
    Get current frame rate and performance info.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        info = await client.get_fps()

        return StatusResponse(
            success=True,
            message="Performance info",
            data=info,
        )


# Round 4 runtime control endpoints - Memory and Window Control


@app.post("/runtime/chip-mem")
async def runtime_set_chip_mem(request: RuntimeSetChipMemRequest):
    """
    Set Chip RAM size.
    Valid sizes: 256, 512, 1024, 2048, 4096, 8192 KB.
    Note: Memory changes require a reset to take effect.
    Requires Amiberry to be running with IPC enabled.
    """
    valid_sizes = (256, 512, 1024, 2048, 4096, 8192)
    if request.size_kb not in valid_sizes:
        raise HTTPException(
            status_code=400, detail=f"Size must be one of: {valid_sizes}"
        )

    async with _ipc_context() as client:
        success = await client.set_chip_mem(request.size_kb)
        return _ipc_success_or_raise(
            success,
            f"Chip RAM set to {request.size_kb} KB. Reset required for changes to take effect.",
            "Failed to set Chip RAM size",
            data={"size_kb": request.size_kb},
        )


@app.post("/runtime/fast-mem")
async def runtime_set_fast_mem(request: RuntimeSetFastMemRequest):
    """
    Set Fast RAM size.
    Valid sizes: 0, 64, 128, 256, 512, 1024, 2048, 4096, 8192 KB.
    Note: Memory changes require a reset to take effect.
    Requires Amiberry to be running with IPC enabled.
    """
    valid_sizes = (0, 64, 128, 256, 512, 1024, 2048, 4096, 8192)
    if request.size_kb not in valid_sizes:
        raise HTTPException(
            status_code=400, detail=f"Size must be one of: {valid_sizes}"
        )

    async with _ipc_context() as client:
        success = await client.set_fast_mem(request.size_kb)
        return _ipc_success_or_raise(
            success,
            f"Fast RAM set to {request.size_kb} KB. Reset required for changes to take effect.",
            "Failed to set Fast RAM size",
            data={"size_kb": request.size_kb},
        )


@app.post("/runtime/slow-mem")
async def runtime_set_slow_mem(request: RuntimeSetSlowMemRequest):
    """
    Set Slow RAM (Bogo) size.
    Valid sizes: 0, 256, 512, 1024, 1536, 1792 KB.
    Note: Memory changes require a reset to take effect.
    Requires Amiberry to be running with IPC enabled.
    """
    valid_sizes = (0, 256, 512, 1024, 1536, 1792)
    if request.size_kb not in valid_sizes:
        raise HTTPException(
            status_code=400, detail=f"Size must be one of: {valid_sizes}"
        )

    async with _ipc_context() as client:
        success = await client.set_slow_mem(request.size_kb)
        return _ipc_success_or_raise(
            success,
            f"Slow RAM set to {request.size_kb} KB. Reset required for changes to take effect.",
            "Failed to set Slow RAM size",
            data={"size_kb": request.size_kb},
        )


@app.post("/runtime/z3-mem")
async def runtime_set_z3_mem(request: RuntimeSetZ3MemRequest):
    """
    Set Zorro III Fast RAM size.
    Valid sizes: 0, 1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024 MB.
    Note: Memory changes require a reset to take effect.
    Requires Amiberry to be running with IPC enabled.
    """
    valid_sizes = (0, 1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024)
    if request.size_mb not in valid_sizes:
        raise HTTPException(
            status_code=400, detail=f"Size must be one of: {valid_sizes}"
        )

    async with _ipc_context() as client:
        success = await client.set_z3_mem(request.size_mb)
        return _ipc_success_or_raise(
            success,
            f"Z3 Fast RAM set to {request.size_mb} MB. Reset required for changes to take effect.",
            "Failed to set Z3 Fast RAM size",
            data={"size_mb": request.size_mb},
        )


@app.get("/runtime/cpu-model")
async def runtime_get_cpu_model():
    """
    Get CPU model information.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        info = await client.get_cpu_model()

        return StatusResponse(
            success=True,
            message="CPU model information",
            data=info,
        )


@app.post("/runtime/cpu-model")
async def runtime_set_cpu_model(request: RuntimeSetCPUModelRequest):
    """
    Set CPU model.
    Valid models: 68000, 68010, 68020, 68030, 68040, 68060.
    Note: CPU changes require a reset to take effect.
    Requires Amiberry to be running with IPC enabled.
    """
    valid_models = ("68000", "68010", "68020", "68030", "68040", "68060")
    if request.model not in valid_models:
        raise HTTPException(
            status_code=400, detail=f"Model must be one of: {valid_models}"
        )

    async with _ipc_context() as client:
        success = await client.set_cpu_model(request.model)
        return _ipc_success_or_raise(
            success,
            f"CPU model set to {request.model}. Reset required for changes to take effect.",
            "Failed to set CPU model",
            data={"model": request.model},
        )


@app.get("/runtime/window-size")
async def runtime_get_window_size():
    """
    Get current window size.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        info = await client.get_window_size()

        return StatusResponse(
            success=True,
            message=f"Window size: {info.get('width', '?')}x{info.get('height', '?')}",
            data=info,
        )


@app.post("/runtime/window-size")
async def runtime_set_window_size(request: RuntimeSetWindowSizeRequest):
    """
    Set window size.
    Width: 320-3840, Height: 200-2160.
    Requires Amiberry to be running with IPC enabled.
    """
    _validate_range(request.width, 320, 3840, "Width")
    _validate_range(request.height, 200, 2160, "Height")

    async with _ipc_context() as client:
        success = await client.set_window_size(request.width, request.height)
        return _ipc_success_or_raise(
            success,
            f"Window size set to {request.width}x{request.height}",
            "Failed to set window size",
            data={"width": request.width, "height": request.height},
        )


@app.get("/runtime/scaling")
async def runtime_get_scaling():
    """
    Get current scaling mode.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        info = await client.get_scaling()

        return StatusResponse(
            success=True,
            message="Scaling mode",
            data=info,
        )


@app.post("/runtime/scaling")
async def runtime_set_scaling(request: RuntimeSetScalingRequest):
    """
    Set scaling mode.
    Modes: -1=auto, 0=nearest, 1=linear, 2=integer.
    Requires Amiberry to be running with IPC enabled.
    """
    _validate_range(request.mode, -1, 2, "Mode")

    mode_names = ["auto", "nearest", "linear", "integer"]
    async with _ipc_context() as client:
        success = await client.set_scaling(request.mode)
        mode_index = request.mode + 1  # -1..2 -> 0..3
        return _ipc_success_or_raise(
            success,
            f"Scaling mode set to {mode_names[mode_index]}",
            "Failed to set scaling mode",
            data={"mode": request.mode, "mode_name": mode_names[mode_index]},
        )


@app.get("/runtime/line-mode")
async def runtime_get_line_mode():
    """
    Get current line mode.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        info = await client.get_line_mode()

        return StatusResponse(
            success=True,
            message="Line mode",
            data=info,
        )


@app.post("/runtime/line-mode")
async def runtime_set_line_mode(request: RuntimeSetLineModeRequest):
    """
    Set line mode.
    Modes: 0=single, 1=double, 2=scanlines.
    Requires Amiberry to be running with IPC enabled.
    """
    _validate_range(request.mode, 0, 2, "Mode")

    mode_names = ["single", "double", "scanlines"]
    async with _ipc_context() as client:
        success = await client.set_line_mode(request.mode)
        return _ipc_success_or_raise(
            success,
            f"Line mode set to {mode_names[request.mode]}",
            "Failed to set line mode",
            data={"mode": request.mode, "mode_name": mode_names[request.mode]},
        )


@app.get("/runtime/resolution")
async def runtime_get_resolution():
    """
    Get current display resolution.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        result = await client.get_resolution()

        if result:
            mode, mode_name = result
            return StatusResponse(
                success=True,
                message=f"Resolution: {mode_name}",
                data={"mode": mode, "mode_name": mode_name},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get resolution")


@app.post("/runtime/resolution")
async def runtime_set_resolution(request: RuntimeSetResolutionRequest):
    """
    Set display resolution.
    Modes: 0=lores, 1=hires, 2=superhires.
    Requires Amiberry to be running with IPC enabled.
    """
    _validate_range(request.mode, 0, 2, "Mode")

    mode_names = ["lores", "hires", "superhires"]
    async with _ipc_context() as client:
        success = await client.set_resolution(request.mode)
        return _ipc_success_or_raise(
            success,
            f"Resolution set to {mode_names[request.mode]}",
            "Failed to set resolution",
            data={"mode": request.mode, "mode_name": mode_names[request.mode]},
        )


# Round 5 - Autocrop and WHDLoad
@app.get("/runtime/autocrop")
async def runtime_get_autocrop():
    """
    Get current autocrop status.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        result = await client.get_autocrop()

        if result is not None:
            return StatusResponse(
                success=True,
                message=f"Autocrop: {'enabled' if result else 'disabled'}",
                data={"enabled": result},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get autocrop status")


@app.post("/runtime/autocrop")
async def runtime_set_autocrop(request: RuntimeSetAutocropRequest):
    """
    Enable or disable automatic display cropping.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        success = await client.set_autocrop(request.enabled)
        return _ipc_success_or_raise(
            success,
            f"Autocrop {'enabled' if request.enabled else 'disabled'}",
            "Failed to set autocrop",
            data={"enabled": request.enabled},
        )


@app.get("/runtime/whdload")
async def runtime_get_whdload():
    """
    Get information about the currently loaded WHDLoad game.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        info = await client.get_whdload()

        if info:
            loaded = info.get("loaded") == "1"
            return StatusResponse(
                success=True,
                message="WHDLoad game loaded" if loaded else "No WHDLoad game loaded",
                data=info,
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get WHDLoad info")


@app.post("/runtime/whdload")
async def runtime_insert_whdload(request: RuntimeInsertWHDLoadRequest):
    """
    Load a WHDLoad game from an LHA archive or directory.
    Note: A reset may be required for the game to start.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        success = await client.insert_whdload(request.path)
        return _ipc_success_or_raise(
            success,
            f"WHDLoad game loaded: {request.path}. Note: A reset may be required.",
            "Failed to load WHDLoad game",
            data={"path": request.path},
        )


@app.delete("/runtime/whdload")
async def runtime_eject_whdload():
    """
    Eject the currently loaded WHDLoad game.
    Requires Amiberry to be running with IPC enabled.
    """
    async with _ipc_context() as client:
        success = await client.eject_whdload()
        return _ipc_success_or_raise(
            success, "WHDLoad game ejected", "Failed to eject WHDLoad game", data={}
        )


# === Round 6 - Debugging and Diagnostics ===


@app.post("/runtime/debug/activate")
async def runtime_debug_activate():
    """
    Activate the built-in debugger.
    Requires Amiberry to be built with debugger support.
    """
    async with _ipc_context() as client:
        success = await client.debug_activate()
        return _ipc_success_or_raise(
            success,
            "Debugger activated",
            "Failed to activate debugger. Amiberry may not be built with debugger support.",
            data={},
        )


@app.post("/runtime/debug/deactivate")
async def runtime_debug_deactivate():
    """
    Deactivate the debugger and resume emulation.
    """
    async with _ipc_context() as client:
        success = await client.debug_deactivate()
        return _ipc_success_or_raise(
            success,
            "Debugger deactivated, emulation resumed",
            "Failed to deactivate debugger",
            data={},
        )


@app.get("/runtime/debug/status")
async def runtime_debug_status():
    """
    Get debugger status (active/inactive).
    """
    async with _ipc_context() as client:
        info = await client.debug_status()

        if info:
            return StatusResponse(
                success=True,
                message="Debugger status retrieved",
                data=info,
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get debugger status")


@app.post("/runtime/debug/step")
async def runtime_debug_step(request: RuntimeDebugStepRequest):
    """
    Single-step CPU instructions when debugger is active.
    """
    async with _ipc_context() as client:
        success = await client.debug_step(request.count)
        return _ipc_success_or_raise(
            success,
            f"Stepped {request.count} instruction(s)",
            "Failed to step. Debugger may not be active.",
            data={"count": request.count},
        )


@app.post("/runtime/debug/continue")
async def runtime_debug_continue():
    """
    Continue execution until next breakpoint.
    """
    async with _ipc_context() as client:
        success = await client.debug_continue()
        return _ipc_success_or_raise(
            success, "Execution continued", "Failed to continue execution", data={}
        )


@app.get("/runtime/cpu/regs")
async def runtime_get_cpu_regs():
    """
    Get all CPU registers (D0-D7, A0-A7, PC, SR, USP, ISP).
    """
    async with _ipc_context() as client:
        info = await client.get_cpu_regs()

        if info:
            return StatusResponse(
                success=True,
                message="CPU registers retrieved",
                data=info,
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get CPU registers")


@app.get("/runtime/custom/regs")
async def runtime_get_custom_regs():
    """
    Get key custom chip registers (DMACON, INTENA, INTREQ, Copper addresses).
    """
    async with _ipc_context() as client:
        info = await client.get_custom_regs()

        if info:
            return StatusResponse(
                success=True,
                message="Custom registers retrieved",
                data=info,
            )
        else:
            raise HTTPException(
                status_code=500, detail="Failed to get custom registers"
            )


@app.post("/runtime/disassemble")
async def runtime_disassemble(request: RuntimeDisassembleRequest):
    """
    Disassemble instructions at a memory address.
    """
    async with _ipc_context() as client:
        lines = await client.disassemble(request.address, request.count)

        return StatusResponse(
            success=True,
            message=f"Disassembly at {request.address}",
            data={"address": request.address, "count": request.count, "lines": lines},
        )


@app.get("/runtime/breakpoints")
async def runtime_list_breakpoints():
    """
    List all active breakpoints.
    """
    async with _ipc_context() as client:
        breakpoints = await client.list_breakpoints()

        return StatusResponse(
            success=True,
            message=f"Found {len(breakpoints)} breakpoint(s)",
            data={"breakpoints": breakpoints},
        )


@app.post("/runtime/breakpoints")
async def runtime_set_breakpoint(request: RuntimeSetBreakpointRequest):
    """
    Set a breakpoint at a memory address. Maximum 20 breakpoints.
    """
    async with _ipc_context() as client:
        success = await client.set_breakpoint(request.address)
        return _ipc_success_or_raise(
            success,
            f"Breakpoint set at {request.address}",
            "Failed to set breakpoint. Maximum 20 breakpoints allowed.",
            data={"address": request.address},
        )


@app.delete("/runtime/breakpoints")
async def runtime_clear_breakpoint(request: RuntimeClearBreakpointRequest):
    """
    Clear a breakpoint at a specific address or all breakpoints (address='ALL').
    """
    async with _ipc_context() as client:
        success = await client.clear_breakpoint(request.address)
        if request.address.upper() == "ALL":
            return _ipc_success_or_raise(
                success,
                "All breakpoints cleared",
                "Failed to clear breakpoint",
                data={},
            )
        return _ipc_success_or_raise(
            success,
            f"Breakpoint at {request.address} cleared",
            "Failed to clear breakpoint",
            data={"address": request.address},
        )


@app.get("/runtime/copper/state")
async def runtime_get_copper_state():
    """
    Get Copper coprocessor state (addresses, enabled status).
    """
    async with _ipc_context() as client:
        info = await client.get_copper_state()

        if info:
            return StatusResponse(
                success=True,
                message="Copper state retrieved",
                data=info,
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get Copper state")


@app.get("/runtime/blitter/state")
async def runtime_get_blitter_state():
    """
    Get Blitter state (busy status, channels, dimensions, addresses).
    """
    async with _ipc_context() as client:
        info = await client.get_blitter_state()

        if info:
            return StatusResponse(
                success=True,
                message="Blitter state retrieved",
                data=info,
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get Blitter state")


@app.get("/runtime/drive/state")
async def runtime_get_drive_state(drive: int | None = None):
    """
    Get floppy drive state (track, side, motor, disk inserted).
    Optionally specify drive number 0-3, or omit for all drives.
    """
    if drive is not None:
        _validate_range(drive, 0, 3, "drive")
    async with _ipc_context() as client:
        info = await client.get_drive_state(drive)

        if info:
            return StatusResponse(
                success=True,
                message=f"Drive state retrieved{' (DF' + str(drive) + ')' if drive is not None else ''}",
                data=info,
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get drive state")


@app.get("/runtime/audio/state")
async def runtime_get_audio_state():
    """
    Get audio channel states (volume, period, enabled).
    """
    async with _ipc_context() as client:
        info = await client.get_audio_state()

        if info:
            return StatusResponse(
                success=True,
                message="Audio state retrieved",
                data=info,
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get audio state")


@app.get("/runtime/dma/state")
async def runtime_get_dma_state():
    """
    Get DMA channel states (bitplane, sprite, audio, disk, copper, blitter).
    """
    async with _ipc_context() as client:
        info = await client.get_dma_state()

        if info:
            return StatusResponse(
                success=True,
                message="DMA state retrieved",
                data=info,
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get DMA state")


# === Process Lifecycle Management ===


@app.get("/process/alive")
async def check_process_alive():
    """Check if the Amiberry process is still running."""
    if _state.process is None:
        return StatusResponse(
            success=False,
            message="No Amiberry process tracked. Launch Amiberry first.",
        )
    returncode = _state.process.poll()
    if returncode is None:
        return StatusResponse(
            success=True,
            message=f"Amiberry is RUNNING (PID: {_state.process.pid})",
            data={"pid": _state.process.pid, "running": True},
        )
    else:
        signal_info = format_signal_info(returncode)
        return StatusResponse(
            success=True,
            message=f"Amiberry has EXITED with code {returncode}{signal_info}",
            data={
                "pid": _state.process.pid,
                "running": False,
                "exit_code": returncode,
            },
        )


@app.get("/process/info")
async def get_process_info():
    """Get detailed process information."""
    if _state.process is None:
        return StatusResponse(
            success=False,
            message="No Amiberry process tracked. Launch Amiberry first.",
        )
    returncode = _state.process.poll()
    data = {"pid": _state.process.pid}

    if returncode is None:
        data["status"] = "RUNNING"
    else:
        data["status"] = "EXITED"
        data["exit_code"] = returncode
        if returncode < 0:
            try:
                sig = signal.Signals(-returncode)
                data["signal"] = sig.name
                data["crash"] = True
            except ValueError:
                data["signal"] = str(-returncode)
                data["crash"] = True
        elif returncode != 0:
            data["crash"] = False
            data["abnormal_exit"] = True

    if _state.launch_cmd:
        data["command"] = " ".join(_state.launch_cmd)
    if _state.log_path:
        data["log_file"] = str(_state.log_path)

    return StatusResponse(success=True, message="Process info", data=data)


@app.post("/process/kill")
async def kill_amiberry_process():
    """Force kill the running Amiberry process."""
    if _state.process is None or _state.process.poll() is not None:
        return StatusResponse(
            success=False, message="No running Amiberry process to kill."
        )

    pid = _state.process.pid
    await asyncio.to_thread(terminate_process, _state.process)
    return StatusResponse(
        success=True, message=f"Amiberry process (PID {pid}) terminated."
    )


@app.post("/process/wait-for-exit")
async def wait_for_exit(request: WaitForExitRequest):
    """Wait for the Amiberry process to exit."""
    if _state.process is None:
        return StatusResponse(success=False, message="No Amiberry process tracked.")

    returncode = _state.process.poll()
    if returncode is not None:
        return StatusResponse(
            success=True,
            message=f"Amiberry already exited with code {returncode}.",
            data={"exit_code": returncode},
        )
    try:
        returncode = await asyncio.to_thread(
            _state.process.wait, timeout=request.timeout
        )
        return StatusResponse(
            success=True,
            message=f"Amiberry exited with code {returncode}.",
            data={"exit_code": returncode},
        )
    except subprocess.TimeoutExpired:
        return StatusResponse(
            success=False,
            message=f"Timeout after {request.timeout}s. Amiberry still running (PID {_state.process.pid}).",
        )


@app.post("/process/restart")
async def restart_amiberry_process():
    """Kill and re-launch Amiberry with the same command."""

    if _state.launch_cmd is None:
        return StatusResponse(
            success=False, message="No previous launch command stored."
        )

    if _state.process is not None and _state.process.poll() is None:
        await asyncio.to_thread(terminate_process, _state.process)

    cmd = _state.launch_cmd
    _state.close_log_handle()
    try:
        _state.process, _state.log_file_handle = launch_process(
            cmd, log_path=_state.log_path
        )
        return StatusResponse(
            success=True,
            message=f"Amiberry restarted (PID: {_state.process.pid})",
            data={"pid": _state.process.pid, "command": " ".join(cmd)},
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error restarting: {str(e)}"
        ) from e


# === Missing IPC Wrappers ===


@app.post("/runtime/memory/read")
async def runtime_read_memory(request: RuntimeReadMemoryRequest):
    """Read memory from the emulated Amiga."""
    try:
        address = int(request.address, 0)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid address: {str(e)}") from e
    if request.width not in (1, 2, 4):
        raise HTTPException(status_code=400, detail="Width must be 1, 2, or 4")

    async with _ipc_context() as client:
        value = await client.read_memory(address, request.width)
        if value is not None:
            return StatusResponse(
                success=True,
                message=f"Memory at 0x{address:08X}: 0x{value:0{request.width * 2}X} ({value})",
                data={
                    "address": f"0x{address:08X}",
                    "value": value,
                    "hex": f"0x{value:0{request.width * 2}X}",
                    "width": request.width,
                },
            )
        else:
            raise HTTPException(
                status_code=500, detail=f"Failed to read memory at {request.address}"
            )


@app.post("/runtime/memory/write")
async def runtime_write_memory(request: RuntimeWriteMemoryRequest):
    """Write memory to the emulated Amiga."""
    try:
        address = int(request.address, 0)
    except ValueError as e:
        raise HTTPException(
            status_code=400, detail=f"Invalid argument: {str(e)}"
        ) from e
    if request.width not in (1, 2, 4):
        raise HTTPException(status_code=400, detail="Width must be 1, 2, or 4")

    async with _ipc_context() as client:
        success = await client.write_memory(address, request.width, request.value)
        return _ipc_success_or_raise(
            success,
            f"Wrote 0x{request.value:0{request.width * 2}X} to 0x{address:08X}",
            f"Failed to write memory at {request.address}",
        )


@app.post("/runtime/load-config")
async def runtime_load_config(request: RuntimeLoadConfigRequest):
    """Load a .uae configuration file into the running emulation."""
    async with _ipc_context() as client:
        success = await client.load_config(request.config_path)
        return _ipc_success_or_raise(
            success,
            f"Configuration loaded: {request.config_path}",
            f"Failed to load config: {request.config_path}",
        )


@app.post("/runtime/debug/step-over")
async def runtime_debug_step_over():
    """Step over subroutine calls (JSR/BSR)."""
    async with _ipc_context() as client:
        success = await client.debug_step_over()
        return _ipc_success_or_raise(
            success,
            "Stepped over subroutine.",
            "Failed to step over. Is debugger active?",
        )


# === Screenshot with Image Data ===


@app.post("/runtime/screenshot-view")
async def runtime_screenshot_view(request: RuntimeScreenshotViewRequest):
    """Take a screenshot and return the image data as base64."""
    filename = request.filename
    if not filename:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = str(SCREENSHOT_DIR / f"debug_{timestamp}.png")
    else:
        # Validate user-provided filename stays within SCREENSHOT_DIR
        screenshot_check = Path(filename).resolve()
        if not screenshot_check.is_relative_to(SCREENSHOT_DIR.resolve()):
            raise HTTPException(
                status_code=400,
                detail="Filename must be within the screenshots directory",
            )

    async with _ipc_context() as client:
        success = await client.screenshot(filename)
        if success:
            screenshot_path = Path(filename)
            if screenshot_path.exists():
                image_data = await asyncio.to_thread(screenshot_path.read_bytes)
                b64_data = base64.b64encode(image_data).decode("utf-8")
                # Detect format from magic bytes
                # Claude API only accepts: image/jpeg, image/png, image/gif, image/webp
                if image_data[:2] in (b'\xff\xd8',):
                    mime_type = "image/jpeg"
                elif image_data[:4] == b'GIF8':
                    mime_type = "image/gif"
                elif image_data[:4] == b'RIFF' and image_data[8:12] == b'WEBP':
                    mime_type = "image/webp"
                else:
                    # Amiberry saves PNG format - default to image/png
                    mime_type = "image/png"
                return StatusResponse(
                    success=True,
                    message=f"Screenshot saved to: {filename}",
                    data={
                        "path": filename,
                        "base64": b64_data,
                        "mime_type": mime_type,
                        "size": len(image_data),
                    },
                )
            else:
                raise HTTPException(
                    status_code=500, detail=f"Screenshot file not found: {filename}"
                )
        else:
            raise HTTPException(status_code=500, detail="Failed to take screenshot")


# === Log Tailing and Crash Detection ===


@app.post("/logs/tail")
async def tail_log(request: TailLogRequest):
    """Get new log lines since last read."""
    log_name = request.log_name
    try:
        log_path = normalize_log_path(log_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if not log_path.exists():
        raise HTTPException(status_code=404, detail=f"Log file not found: {log_name}")

    last_pos = _state.log_read_positions.get(log_name, 0)
    try:

        def _read_from_pos():
            with open(log_path, errors="replace") as f:
                file_size = f.seek(0, 2)  # Get file size
                pos = last_pos if last_pos <= file_size else 0
                f.seek(pos)
                content = f.read()
                return content, f.tell()

        new_content, new_pos = await asyncio.to_thread(_read_from_pos)
        _state.log_read_positions[log_name] = new_pos

        if new_content:
            line_count = new_content.count("\n")
            return StatusResponse(
                success=True,
                message=f"New log output ({line_count} lines)",
                data={"content": new_content, "lines": line_count},
            )
        else:
            return StatusResponse(
                success=True, message="No new log output since last read."
            )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error reading log: {str(e)}"
        ) from e


@app.post("/logs/wait-for-pattern")
async def wait_for_log_pattern(request: WaitForLogPatternRequest):
    """Wait for a pattern to appear in a log file."""
    log_name = request.log_name
    try:
        log_path = normalize_log_path(log_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        compiled_pattern = re.compile(request.pattern)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {str(e)}") from e

    start_time = asyncio.get_running_loop().time()
    last_pos = _state.log_read_positions.get(log_name, 0)

    while True:
        elapsed = asyncio.get_running_loop().time() - start_time
        if elapsed >= request.timeout:
            _state.log_read_positions[log_name] = last_pos
            return StatusResponse(
                success=False,
                message=f"Timeout after {request.timeout}s. Pattern '{request.pattern}' not found.",
            )

        if log_path.exists():
            try:

                def _read_and_match(_pos=last_pos):
                    with open(log_path, errors="replace") as f:
                        file_size = f.seek(0, 2)
                        pos = _pos if _pos <= file_size else 0
                        f.seek(pos)
                        content = f.read()
                        new_p = f.tell()
                    if content:
                        for ln in content.splitlines():
                            if compiled_pattern.search(ln):
                                return ln, new_p
                    return None, new_p

                match_line, new_pos = await asyncio.to_thread(_read_and_match)

                if match_line is not None:
                    _state.log_read_positions[log_name] = new_pos
                    return StatusResponse(
                        success=True,
                        message=f"Pattern found after {elapsed:.1f}s",
                        data={"line": match_line, "elapsed": round(elapsed, 1)},
                    )
                last_pos = new_pos
            except Exception:
                pass

        await asyncio.sleep(0.5)


@app.post("/process/crash-info")
async def get_crash_info(request: GetCrashInfoRequest):
    """Detect if Amiberry crashed by checking process state and scanning logs."""
    data = {}

    # Check process state
    if _state.process is not None:
        returncode = _state.process.poll()
        if returncode is None:
            data["process"] = {"status": "RUNNING", "pid": _state.process.pid}
        else:
            proc_info = {
                "status": "EXITED",
                "exit_code": returncode,
                "pid": _state.process.pid,
            }
            if returncode < 0:
                try:
                    sig = signal.Signals(-returncode)
                    proc_info["signal"] = sig.name
                except ValueError:
                    proc_info["signal"] = str(-returncode)
                proc_info["crash"] = True
            elif returncode != 0:
                proc_info["abnormal_exit"] = True
            data["process"] = proc_info
    else:
        data["process"] = {"status": "NOT_TRACKED"}

    # Scan logs
    crash_patterns = [
        "Segmentation fault",
        "SIGSEGV",
        "SIGABRT",
        "Aborted",
        "core dumped",
        "assertion failed",
        "FATAL",
        "Bus error",
        "SIGBUS",
        "double free",
        "heap-buffer-overflow",
        "stack-buffer-overflow",
        "AddressSanitizer",
        "undefined behavior",
    ]

    log_files_to_scan: list[Path] = []
    if request.log_name:
        try:
            log_files_to_scan = [normalize_log_path(request.log_name)]
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    elif _state.log_path:
        log_files_to_scan = [_state.log_path]
    elif LOG_DIR.exists():

        def _find_latest_log() -> list[Path]:
            logs = []
            for p in LOG_DIR.glob("*.log"):
                try:
                    logs.append((p, p.stat().st_mtime))
                except OSError:
                    continue
            logs.sort(key=lambda x: x[1], reverse=True)
            return [p for p, _ in logs[:1]]

        log_files_to_scan = await asyncio.to_thread(_find_latest_log)

    crash_indicators = []
    for lp in log_files_to_scan:
        if lp.exists():
            try:
                content = await asyncio.to_thread(lp.read_text, errors="replace")
                for i, line in enumerate(content.splitlines()):
                    for cp in crash_patterns:
                        if cp.lower() in line.lower():
                            crash_indicators.append(
                                {
                                    "line_number": i + 1,
                                    "text": line.strip(),
                                    "file": lp.name,
                                }
                            )
                            break
            except Exception:
                pass

    data["crash_indicators"] = crash_indicators[:20]
    data["log_files_scanned"] = [str(lp) for lp in log_files_to_scan]

    has_crash = bool(crash_indicators) or data.get("process", {}).get("crash", False)
    return StatusResponse(
        success=True,
        message="Crash detected" if has_crash else "No crash detected",
        data=data,
    )


# === Workflow Tools ===


@app.get("/health")
async def health_check():
    """Comprehensive health check: process + IPC + emulation status."""
    data = {}

    # Process check
    if _state.process is not None:
        rc = _state.process.poll()
        if rc is None:
            data["process"] = {"status": "RUNNING", "pid": _state.process.pid}
        else:
            data["process"] = {"status": "EXITED", "exit_code": rc}
    else:
        data["process"] = {"status": "NOT_TRACKED"}

    # IPC check
    try:
        client = _get_ipc_client()
        pong = await client.ping()
        if pong:
            data["ipc"] = {"status": "CONNECTED"}
            status = await client.get_status()
            if status:
                data["emulation"] = {
                    "paused": status.get("Paused", "?"),
                    "config": status.get("Config", "?"),
                }
                for key in ["Floppy0", "Floppy1", "Floppy2", "Floppy3"]:
                    val = status.get(key)
                    if val:
                        data["emulation"][key.lower()] = val
            fps_info = await client.get_fps()
            if fps_info:
                data["fps"] = fps_info
        else:
            data["ipc"] = {"status": "NOT_RESPONDING"}
    except IPCConnectionError:
        data["ipc"] = {"status": "NOT_CONNECTED"}
    except Exception as e:
        data["ipc"] = {"status": "ERROR", "error": str(e)}

    healthy = data.get("ipc", {}).get("status") == "CONNECTED"
    return StatusResponse(
        success=healthy,
        message="Healthy" if healthy else "Unhealthy",
        data=data,
    )


@app.post("/launch-and-wait")
async def launch_and_wait_for_ipc(request: LaunchAndWaitRequest):
    """Launch Amiberry and wait until IPC is available."""

    if not request.model and not request.config and not request.lha_file:
        raise HTTPException(
            status_code=400,
            detail="Either 'model', 'config', or 'lha_file' must be specified",
        )

    if request.model and request.model not in SUPPORTED_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Model must be one of: {', '.join(SUPPORTED_MODELS)}",
        )

    # Kill existing process
    if _state.process is not None and _state.process.poll() is None:
        await asyncio.to_thread(terminate_process, _state.process)

    # Resolve config path if specified
    config_path = None
    if request.config:
        config_path = _find_config_path(request.config)
        if not config_path:
            raise HTTPException(
                status_code=404, detail=f"Config '{request.config}' not found"
            )

    if request.lha_file:
        lha_path = Path(request.lha_file)
        if not lha_path.exists():
            raise HTTPException(
                status_code=404, detail=f"LHA not found: {request.lha_file}"
            )

    cmd = build_launch_command(
        model=request.model,
        config_path=config_path,
        disk_image=request.disk_image,
        lha_file=request.lha_file,
        autostart=request.autostart,
        with_logging=True,
    )

    # Launch with logging
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_name = f"amiberry_{timestamp}.log"
    log_path = LOG_DIR / log_name

    _state.close_log_handle()
    try:
        _state.process, _state.log_file_handle = launch_process(cmd, log_path=log_path)
        _state.launch_cmd = cmd
        _state.log_path = log_path
    except Exception as e:
        _state.close_log_handle()
        raise HTTPException(status_code=500, detail=f"Error launching: {str(e)}") from e

    # Wait for IPC
    start_time = asyncio.get_running_loop().time()
    ipc_ready = False

    while True:
        elapsed = asyncio.get_running_loop().time() - start_time
        if elapsed >= request.timeout:
            break

        if _state.process.poll() is not None:
            rc = _state.process.returncode
            raise HTTPException(
                status_code=500,
                detail=f"Amiberry exited before IPC available (code {rc})",
            )

        try:
            client = _get_ipc_client()
            pong = await client.ping()
            if pong:
                ipc_ready = True
                break
        except Exception:
            pass

        await asyncio.sleep(0.5)

    if ipc_ready:
        return StatusResponse(
            success=True,
            message=f"Amiberry launched and IPC ready after {elapsed:.1f}s",
            data={
                "pid": _state.process.pid,
                "log_file": str(log_path),
                "command": " ".join(cmd),
                "elapsed": round(elapsed, 1),
            },
        )
    else:
        return StatusResponse(
            success=False,
            message=f"IPC not responding after {request.timeout}s",
            data={
                "pid": _state.process.pid,
                "log_file": str(log_path),
                "still_running": _state.process.poll() is None,
            },
        )


def main():
    """Main entry point for the HTTP API server."""
    ensure_directories_exist()

    host = os.environ.get("AMIBERRY_HTTP_HOST", "127.0.0.1")
    port = int(os.environ.get("AMIBERRY_HTTP_PORT", "8080"))

    print(f"Starting Amiberry HTTP API Server on http://{host}:{port}")
    print(f"Platform: {platform.system()}")
    print(f"Config directory: {CONFIG_DIR}")
    print(f"\nAPI Documentation: http://{host}:{port}/docs")
    print("Integration Guide: docs/HTTP_API_GUIDE.md")

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
