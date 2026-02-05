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
import datetime
import platform
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

from .config import (
    IS_MACOS,
    IS_LINUX,
    AMIBERRY_HOME,
    EMULATOR_BINARY,
    CONFIG_DIR,
    SYSTEM_CONFIG_DIR,
    SAVESTATE_DIR,
    DISK_IMAGE_DIRS,
    FLOPPY_EXTENSIONS,
    HARDFILE_EXTENSIONS,
    LHA_EXTENSIONS,
    SUPPORTED_MODELS,
    get_platform_info,
    ensure_directories_exist,
)
from .uae_config import (
    parse_uae_config,
    modify_uae_config,
    create_config_from_template,
    get_config_summary,
)
from .savestate import (
    inspect_savestate,
    get_savestate_summary,
)
from .rom_manager import (
    identify_rom,
    scan_rom_directory,
    get_rom_summary,
)
from .ipc_client import (
    AmiberryIPCClient,
    IPCError,
    ConnectionError as IPCConnectionError,
    CommandError,
)

# CD image extensions
CD_EXTENSIONS = [".iso", ".cue", ".chd", ".bin", ".nrg"]

# Log directory for captured output
LOG_DIR = AMIBERRY_HOME / "logs"

# ROM directory
ROM_DIR = AMIBERRY_HOME / "Kickstarts" if IS_MACOS else AMIBERRY_HOME / "kickstarts"

# Process lifecycle tracking
_http_amiberry_process: subprocess.Popen | None = None
_http_amiberry_launch_cmd: list[str] | None = None
_http_amiberry_log_path: Path | None = None
_http_last_log_read_position: dict[str, int] = {}

# FastAPI app
app = FastAPI(
    title="Amiberry HTTP API",
    description="REST API for controlling Amiberry emulator via HTTP - works with Siri Shortcuts, Google Assistant, Home Assistant, and more",
    version="1.0.0",
)

# Enable CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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
    config: Optional[str] = None
    model: Optional[str] = None
    disk_image: Optional[str] = None
    lha_file: Optional[str] = None
    autostart: bool = True


class LaunchWithLoggingRequest(BaseModel):
    config: Optional[str] = None
    model: Optional[str] = None
    disk_image: Optional[str] = None
    lha_file: Optional[str] = None
    autostart: bool = True
    log_name: Optional[str] = None


class CreateConfigRequest(BaseModel):
    template: str = "A500"
    overrides: Optional[Dict[str, str]] = None


class ModifyConfigRequest(BaseModel):
    modifications: Dict[str, Optional[str]]


class LaunchCDRequest(BaseModel):
    cd_image: Optional[str] = None
    search_term: Optional[str] = None
    model: str = "CD32"
    autostart: bool = True


class DiskSwapperRequest(BaseModel):
    disk_images: List[str]
    model: Optional[str] = None
    config: Optional[str] = None
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
    version: Optional[str] = None
    revision: Optional[str] = None
    model: Optional[str] = None
    probable_type: Optional[str] = None
    size: int


class StatusResponse(BaseModel):
    success: bool
    message: str
    data: Optional[dict] = None


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


class RuntimeGetDriveStateRequest(BaseModel):
    drive: Optional[int] = None


# Autonomous troubleshooting request models
class WaitForExitRequest(BaseModel):
    timeout: int = 30


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
    filename: Optional[str] = None


class TailLogRequest(BaseModel):
    log_name: str


class WaitForLogPatternRequest(BaseModel):
    log_name: str
    pattern: str
    timeout: int = 30


class GetCrashInfoRequest(BaseModel):
    log_name: Optional[str] = None


class LaunchAndWaitRequest(BaseModel):
    config: Optional[str] = None
    model: Optional[str] = None
    disk_image: Optional[str] = None
    lha_file: Optional[str] = None
    autostart: bool = True
    timeout: int = 30


def _is_amiberry_running() -> bool:
    """Check if Amiberry process is currently running."""
    try:
        if IS_MACOS:
            result = subprocess.run(
                ["pgrep", "-f", "Amiberry"], capture_output=True, text=True
            )
        else:
            result = subprocess.run(
                ["pgrep", "amiberry"], capture_output=True, text=True
            )
        return result.returncode == 0
    except Exception:
        return False


def _stop_amiberry() -> bool:
    """Stop all running Amiberry instances."""
    try:
        if IS_MACOS:
            subprocess.run(["pkill", "-f", "Amiberry"], check=False)
        else:
            subprocess.run(["pkill", "amiberry"], check=False)
        return True
    except Exception:
        return False


def _find_config_path(config_name: str) -> Path | None:
    """Find a configuration file by name, checking user and system directories."""
    config_path = CONFIG_DIR / config_name
    if config_path.exists():
        return config_path

    if IS_LINUX and SYSTEM_CONFIG_DIR:
        config_path = SYSTEM_CONFIG_DIR / config_name
        if config_path.exists():
            return config_path

    return None


def _classify_image_type(suffix: str) -> str:
    """Classify a disk image by its file extension."""
    suffix_lower = suffix.lower()
    if suffix_lower in FLOPPY_EXTENSIONS:
        return "floppy"
    elif suffix_lower in LHA_EXTENSIONS:
        return "lha"
    else:
        return "hardfile"


def _get_extensions_for_type(image_type: str) -> list[str]:
    """Get file extensions for a given image type."""
    if image_type == "floppy":
        return FLOPPY_EXTENSIONS
    elif image_type == "hardfile":
        return HARDFILE_EXTENSIONS
    elif image_type == "lha":
        return LHA_EXTENSIONS
    else:  # all
        return FLOPPY_EXTENSIONS + HARDFILE_EXTENSIONS + LHA_EXTENSIONS


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
    running = _is_amiberry_running()
    return StatusResponse(
        success=True,
        message=f"Amiberry is {'running' if running else 'not running'}",
        data={"running": running},
    )


@app.post("/stop")
async def stop():
    """Stop all running Amiberry instances."""
    if not _is_amiberry_running():
        return StatusResponse(success=True, message="Amiberry is not running")

    success = _stop_amiberry()
    if success:
        # Wait a bit for process to terminate
        await asyncio.sleep(1)
        return StatusResponse(success=True, message="Amiberry stopped successfully")
    else:
        raise HTTPException(status_code=500, detail="Failed to stop Amiberry")


@app.get("/configs", response_model=List[ConfigInfo])
async def list_configs(include_system: bool = False):
    """List available Amiberry configuration files."""
    configs = []

    # User configs
    if CONFIG_DIR.exists():
        for f in CONFIG_DIR.glob("*.uae"):
            configs.append(ConfigInfo(name=f.name, source="user", path=str(f)))

    # System configs (Linux only)
    if IS_LINUX and include_system and SYSTEM_CONFIG_DIR and SYSTEM_CONFIG_DIR.exists():
        for f in SYSTEM_CONFIG_DIR.glob("*.uae"):
            configs.append(ConfigInfo(name=f.name, source="system", path=str(f)))

    return sorted(configs, key=lambda x: x.name)


@app.get("/configs/{config_name}")
async def get_config(config_name: str):
    """Get content of a specific configuration file."""
    config_path = _find_config_path(config_name)

    if not config_path:
        raise HTTPException(
            status_code=404, detail=f"Configuration '{config_name}' not found"
        )

    try:
        content = config_path.read_text()
        return StatusResponse(
            success=True,
            message=f"Configuration: {config_name}",
            data={"content": content},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading config: {str(e)}")


@app.get("/disk-images", response_model=List[DiskImage])
async def list_disk_images(search: Optional[str] = None, type: str = "all"):
    """List available disk images."""
    search_term = search.lower() if search else ""
    images = []

    extensions = _get_extensions_for_type(type)

    for directory in DISK_IMAGE_DIRS:
        if directory.exists():
            for ext in extensions:
                # Search both lowercase and uppercase extensions
                for pattern in [f"**/*{ext}", f"**/*{ext.upper()}"]:
                    for img in directory.glob(pattern):
                        if not search_term or search_term in img.name.lower():
                            images.append(
                                DiskImage(
                                    name=img.name,
                                    type=_classify_image_type(img.suffix),
                                    path=str(img),
                                )
                            )

    # Remove duplicates (from case-insensitive matching)
    seen = set()
    unique_images = []
    for img in images:
        if img.path not in seen:
            seen.add(img.path)
            unique_images.append(img)

    return sorted(unique_images, key=lambda x: x.name.lower())


@app.get("/savestates", response_model=List[Savestate])
async def list_savestates(search: Optional[str] = None):
    """List available savestate files."""
    search_term = search.lower() if search else ""
    savestates = []

    if SAVESTATE_DIR.exists():
        for state in SAVESTATE_DIR.glob("**/*.uss"):
            if not search_term or search_term in state.name.lower():
                mtime = state.stat().st_mtime
                timestamp = datetime.datetime.fromtimestamp(mtime).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                savestates.append(
                    Savestate(name=state.name, modified=timestamp, path=str(state))
                )

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

    # Build command
    cmd = [EMULATOR_BINARY]

    # Add model or config (optional if .lha file is provided)
    if request.model:
        if request.model not in SUPPORTED_MODELS:
            raise HTTPException(
                status_code=400,
                detail=f"Model must be one of: {', '.join(SUPPORTED_MODELS)}",
            )
        cmd.extend(["--model", request.model])
    elif request.config:
        config_path = _find_config_path(request.config)
        if not config_path:
            raise HTTPException(
                status_code=404,
                detail=f"Configuration '{request.config}' not found",
            )
        cmd.extend(["-f", str(config_path)])

    # Add disk image if specified
    if request.disk_image:
        disk_path = Path(request.disk_image)
        if not disk_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Disk image not found: {request.disk_image}",
            )
        cmd.extend(["-0", str(disk_path)])

    # Add .lha file if specified (Amiberry auto-extracts and mounts)
    if request.lha_file:
        lha_path = Path(request.lha_file)
        if not lha_path.exists():
            raise HTTPException(
                status_code=404, detail=f"LHA file not found: {request.lha_file}"
            )
        if not lha_path.suffix.lower() == ".lha":
            raise HTTPException(status_code=400, detail="File must have .lha extension")
        cmd.append(str(lha_path))

    # Add autostart flag
    if request.autostart:
        cmd.append("-G")

    try:
        # Launch in background
        global _http_amiberry_process, _http_amiberry_launch_cmd, _http_amiberry_log_path
        _http_amiberry_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _http_amiberry_launch_cmd = cmd
        _http_amiberry_log_path = None

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
        )


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

    # Create log directory if needed
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Generate log filename
    log_name = request.log_name
    if not log_name:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        log_name = f"amiberry_{timestamp}.log"
    if not log_name.endswith(".log"):
        log_name += ".log"

    log_path = LOG_DIR / log_name

    # Build command
    cmd = [EMULATOR_BINARY, "--log"]

    if request.model:
        cmd.extend(["--model", request.model])
    elif request.config:
        config_path = _find_config_path(request.config)
        if not config_path:
            raise HTTPException(
                status_code=404, detail=f"Configuration '{request.config}' not found"
            )
        cmd.extend(["-f", str(config_path)])

    if request.disk_image:
        cmd.extend(["-0", request.disk_image])

    if request.lha_file:
        lha_path = Path(request.lha_file)
        if not lha_path.exists():
            raise HTTPException(
                status_code=404, detail=f"LHA file not found: {request.lha_file}"
            )
        cmd.append(str(lha_path))

    if request.autostart:
        cmd.append("-G")

    try:
        global _http_amiberry_process, _http_amiberry_launch_cmd, _http_amiberry_log_path
        with open(log_path, "w") as log_file:
            _http_amiberry_process = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        _http_amiberry_launch_cmd = cmd
        _http_amiberry_log_path = log_path

        return StatusResponse(
            success=True,
            message="Launched Amiberry with logging enabled",
            data={"log_file": str(log_path), "command": " ".join(cmd)},
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error launching Amiberry: {str(e)}"
        )


@app.get("/configs/{config_name}/parsed")
async def get_config_parsed(config_name: str, include_raw: bool = False):
    """Get a parsed configuration file with summary."""
    config_path = _find_config_path(config_name)

    if not config_path:
        raise HTTPException(
            status_code=404, detail=f"Configuration '{config_name}' not found"
        )

    try:
        config = parse_uae_config(config_path)
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
        raise HTTPException(status_code=500, detail=f"Error parsing config: {str(e)}")


@app.post("/configs/create/{config_name}")
async def create_config(config_name: str, request: CreateConfigRequest):
    """Create a new configuration file from a template."""
    if not config_name.endswith(".uae"):
        config_name += ".uae"

    config_path = CONFIG_DIR / config_name

    if config_path.exists():
        raise HTTPException(
            status_code=409,
            detail=f"Configuration '{config_name}' already exists. Use PATCH to modify.",
        )

    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        config = create_config_from_template(
            config_path, request.template, request.overrides
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
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating config: {str(e)}")


@app.patch("/configs/{config_name}")
async def modify_config(config_name: str, request: ModifyConfigRequest):
    """Modify specific options in an existing configuration file."""
    config_path = _find_config_path(config_name)

    if not config_path:
        raise HTTPException(
            status_code=404, detail=f"Configuration '{config_name}' not found"
        )

    try:
        updated_config = modify_uae_config(config_path, request.modifications)

        return StatusResponse(
            success=True,
            message=f"Modified configuration: {config_name}",
            data={"modifications": request.modifications},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error modifying config: {str(e)}")


@app.post("/launch-whdload")
async def launch_whdload(
    search_term: Optional[str] = None,
    exact_path: Optional[str] = None,
    model: str = "A1200",
    autostart: bool = True,
):
    """Search for and launch a WHDLoad game (.lha file)."""
    lha_path = None

    if exact_path:
        lha_path = Path(exact_path)
        if not lha_path.exists():
            raise HTTPException(
                status_code=404, detail=f"LHA file not found: {exact_path}"
            )
    elif search_term:
        search_lower = search_term.lower()
        lha_files = []
        for directory in DISK_IMAGE_DIRS:
            if directory.exists():
                for pattern in ["**/*.lha", "**/*.LHA"]:
                    for lha in directory.glob(pattern):
                        if search_lower in lha.name.lower():
                            lha_files.append(lha)

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

    cmd = [EMULATOR_BINARY, "--model", model, str(lha_path)]
    if autostart:
        cmd.append("-G")

    try:
        global _http_amiberry_process, _http_amiberry_launch_cmd, _http_amiberry_log_path
        _http_amiberry_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _http_amiberry_launch_cmd = cmd
        _http_amiberry_log_path = None

        return StatusResponse(
            success=True,
            message=f"Launched WHDLoad game: {lha_path.name}",
            data={"game": lha_path.name, "model": model},
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error launching WHDLoad game: {str(e)}"
        )


@app.post("/launch-cd")
async def launch_cd(request: LaunchCDRequest):
    """Launch a CD image with automatic CD32/CDTV detection."""
    cd_path = None

    if request.cd_image:
        cd_path = Path(request.cd_image)
        if not cd_path.exists():
            raise HTTPException(
                status_code=404, detail=f"CD image not found: {request.cd_image}"
            )
    elif request.search_term:
        search_lower = request.search_term.lower()
        cd_files = []
        search_dirs = DISK_IMAGE_DIRS + [AMIBERRY_HOME / "CD"]
        if IS_MACOS:
            search_dirs.append(AMIBERRY_HOME / "CDs")

        for directory in search_dirs:
            if directory.exists():
                for ext in CD_EXTENSIONS:
                    for pattern in [f"**/*{ext}", f"**/*{ext.upper()}"]:
                        for cd in directory.glob(pattern):
                            if search_lower in cd.name.lower():
                                cd_files.append(cd)

        cd_files = list(set(cd_files))

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

    cmd = [EMULATOR_BINARY, "--model", request.model, "--cdimage", str(cd_path)]
    if request.autostart:
        cmd.append("-G")

    try:
        global _http_amiberry_process, _http_amiberry_launch_cmd, _http_amiberry_log_path
        _http_amiberry_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _http_amiberry_launch_cmd = cmd
        _http_amiberry_log_path = None

        return StatusResponse(
            success=True,
            message=f"Launched CD image: {cd_path.name}",
            data={"cd_image": cd_path.name, "model": request.model},
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error launching CD image: {str(e)}"
        )


@app.get("/cd-images", response_model=List[CDImage])
async def list_cd_images(search: Optional[str] = None):
    """List available CD images."""
    search_term = search.lower() if search else ""
    cd_files = []

    search_dirs = DISK_IMAGE_DIRS + [AMIBERRY_HOME / "CD"]
    if IS_MACOS:
        search_dirs.append(AMIBERRY_HOME / "CDs")

    for directory in search_dirs:
        if directory.exists():
            for ext in CD_EXTENSIONS:
                for pattern in [f"**/*{ext}", f"**/*{ext.upper()}"]:
                    for cd in directory.glob(pattern):
                        if not search_term or search_term in cd.name.lower():
                            cd_files.append(
                                CDImage(
                                    name=cd.name,
                                    type=cd.suffix.lower().lstrip("."),
                                    path=str(cd),
                                )
                            )

    # Remove duplicates
    seen = set()
    unique_cds = []
    for cd in cd_files:
        if cd.path not in seen:
            seen.add(cd.path)
            unique_cds.append(cd)

    return sorted(unique_cds, key=lambda x: x.name.lower())


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
            raise HTTPException(
                status_code=404, detail=f"Disk image not found: {img}"
            )
        verified_paths.append(str(img_path))

    cmd = [EMULATOR_BINARY]

    if request.model:
        cmd.extend(["--model", request.model])
    elif request.config:
        config_path = _find_config_path(request.config)
        if not config_path:
            raise HTTPException(
                status_code=404, detail=f"Configuration '{request.config}' not found"
            )
        cmd.extend(["-f", str(config_path)])
    else:
        cmd.extend(["--model", "A500"])

    # Add first disk to DF0
    cmd.extend(["-0", verified_paths[0]])

    # Add disk swapper
    cmd.append(f"-diskswapper={','.join(verified_paths)}")

    if request.autostart:
        cmd.append("-G")

    try:
        global _http_amiberry_process, _http_amiberry_launch_cmd, _http_amiberry_log_path
        _http_amiberry_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _http_amiberry_launch_cmd = cmd
        _http_amiberry_log_path = None

        return StatusResponse(
            success=True,
            message=f"Launched with disk swapper ({len(verified_paths)} disks)",
            data={"disks": [Path(p).name for p in verified_paths]},
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error launching with disk swapper: {str(e)}"
        )


@app.get("/logs", response_model=List[LogFile])
async def list_logs():
    """List available log files from previous launches."""
    if not LOG_DIR.exists():
        return []

    logs = []
    for log in LOG_DIR.glob("*.log"):
        mtime = log.stat().st_mtime
        timestamp = datetime.datetime.fromtimestamp(mtime).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        logs.append(
            LogFile(name=log.name, modified=timestamp, size=log.stat().st_size)
        )

    return sorted(logs, key=lambda x: x.modified, reverse=True)


@app.get("/logs/{log_name}")
async def get_log_content(log_name: str, tail_lines: Optional[int] = None):
    """Get the content of a log file."""
    if not log_name.endswith(".log"):
        log_name += ".log"

    log_path = LOG_DIR / log_name

    if not log_path.exists():
        raise HTTPException(status_code=404, detail=f"Log file not found: {log_name}")

    try:
        content = log_path.read_text(errors="replace")

        if tail_lines and tail_lines > 0:
            lines = content.splitlines()
            content = "\n".join(lines[-tail_lines:])

        return StatusResponse(
            success=True,
            message=f"Log file: {log_name}",
            data={"content": content, "lines": len(content.splitlines())},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading log: {str(e)}")


# Phase 2 endpoints


@app.get("/savestates/{savestate_name}/inspect")
async def inspect_savestate_endpoint(savestate_name: str):
    """Inspect a savestate file and extract metadata."""
    from .config import SAVESTATE_DIR

    # Handle both full path and just filename
    if "/" in savestate_name or "\\" in savestate_name:
        path = Path(savestate_name)
    else:
        if not savestate_name.endswith(".uss"):
            savestate_name += ".uss"
        path = SAVESTATE_DIR / savestate_name

    if not path.exists():
        raise HTTPException(
            status_code=404, detail=f"Savestate not found: {savestate_name}"
        )

    try:
        metadata = inspect_savestate(path)
        summary = get_savestate_summary(metadata)

        return StatusResponse(
            success=True,
            message=f"Inspected savestate: {path.name}",
            data={"metadata": metadata, "summary": summary},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error inspecting savestate: {str(e)}"
        )


@app.get("/roms", response_model=List[RomInfo])
async def list_roms(directory: Optional[str] = None):
    """List and identify ROM files in the ROMs directory."""
    if directory:
        rom_dir = Path(directory)
    else:
        rom_dir = ROM_DIR

    if not rom_dir.exists():
        return []

    try:
        roms = scan_rom_directory(rom_dir)
        return [RomInfo(**rom) for rom in roms if not rom.get("error")]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error scanning ROMs: {str(e)}")


@app.get("/roms/identify")
async def identify_rom_endpoint(rom_path: str):
    """Identify a specific ROM file by its checksum."""
    path = Path(rom_path)

    if not path.exists():
        raise HTTPException(status_code=404, detail=f"ROM file not found: {rom_path}")

    try:
        rom_info = identify_rom(path)
        summary = get_rom_summary(rom_info)

        return StatusResponse(
            success=True,
            message=f"Identified ROM: {path.name}",
            data={"rom": rom_info, "summary": summary},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error identifying ROM: {str(e)}")


@app.get("/version")
async def get_amiberry_version():
    """Get Amiberry version and build information."""
    try:
        result = subprocess.run(
            [EMULATOR_BINARY, "--help"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        output = result.stdout + result.stderr

        version_info = {
            "binary": str(EMULATOR_BINARY),
            "available": True,
        }

        # Look for version string
        for line in output.split("\n"):
            line_lower = line.lower()
            if "version" in line_lower or "amiberry" in line_lower:
                version_info["version_line"] = line.strip()
                break

        # Check for features
        features = []
        if "--log" in output:
            features.append("console_logging")
        if "--model" in output:
            features.append("model_presets")
        if "--cdimage" in output or "cdimage" in output:
            features.append("cd_image_support")
        if "lua" in output.lower():
            features.append("lua_scripting")

        version_info["features"] = features

        return StatusResponse(
            success=True,
            message="Amiberry version information",
            data=version_info,
        )

    except subprocess.TimeoutExpired:
        return StatusResponse(
            success=True,
            message="Amiberry found but version check timed out",
            data={"binary": str(EMULATOR_BINARY), "available": True},
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Amiberry binary not found at {EMULATOR_BINARY}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error getting Amiberry version: {str(e)}"
        )


# Runtime control endpoints (IPC)


@app.get("/runtime/status")
async def get_runtime_status():
    """
    Get the current status of a running Amiberry emulation.
    Requires Amiberry to be running with IPC enabled (USE_IPC_SOCKET).
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        status = await client.get_status()

        return StatusResponse(
            success=True,
            message="Runtime status retrieved",
            data={
                "paused": status.get("Paused", False),
                "config": status.get("Config", ""),
                "floppies": {
                    f"DF{i}": status.get(f"Floppy{i}") for i in range(4) if f"Floppy{i}" in status
                },
            },
        )
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/pause")
async def pause_emulation():
    """
    Pause a running Amiberry emulation.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.pause()

        if success:
            return StatusResponse(success=True, message="Emulation paused")
        else:
            raise HTTPException(status_code=500, detail="Failed to pause emulation")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/resume")
async def resume_emulation():
    """
    Resume a paused Amiberry emulation.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.resume()

        if success:
            return StatusResponse(success=True, message="Emulation resumed")
        else:
            raise HTTPException(status_code=500, detail="Failed to resume emulation")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/reset")
async def reset_emulation(request: RuntimeResetRequest):
    """
    Reset the running Amiberry emulation.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.reset(hard=request.hard)

        reset_type = "hard" if request.hard else "soft"
        if success:
            return StatusResponse(success=True, message=f"Emulation {reset_type} reset")
        else:
            raise HTTPException(status_code=500, detail=f"Failed to {reset_type} reset")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/quit")
async def quit_emulation():
    """
    Quit the running Amiberry emulation.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.quit()

        if success:
            return StatusResponse(success=True, message="Amiberry quit command sent")
        else:
            raise HTTPException(status_code=500, detail="Failed to quit Amiberry")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/screenshot")
async def runtime_screenshot(request: RuntimeScreenshotRequest):
    """
    Take a screenshot of the running emulation.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.screenshot(request.filename)

        if success:
            return StatusResponse(
                success=True,
                message="Screenshot taken",
                data={"filename": request.filename},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to take screenshot")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/save-state")
async def runtime_save_state(request: RuntimeSaveStateRequest):
    """
    Save the current emulation state while running.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.save_state(request.state_file, request.config_file)

        if success:
            return StatusResponse(
                success=True,
                message="State saved",
                data={
                    "state_file": request.state_file,
                    "config_file": request.config_file,
                },
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to save state")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/load-state")
async def runtime_load_state(request: RuntimeLoadStateRequest):
    """
    Load a savestate into the running emulation.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.load_state(request.state_file)

        if success:
            return StatusResponse(
                success=True,
                message="Loading state",
                data={"state_file": request.state_file},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to load state")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/insert-floppy")
async def runtime_insert_floppy(request: RuntimeInsertFloppyRequest):
    """
    Insert a floppy disk image into a running emulation.
    Requires Amiberry to be running with IPC enabled.
    """
    if not 0 <= request.drive <= 3:
        raise HTTPException(status_code=400, detail="Drive must be 0-3")

    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.insert_floppy(request.drive, request.image_path)

        if success:
            return StatusResponse(
                success=True,
                message=f"Inserted disk into DF{request.drive}:",
                data={
                    "drive": request.drive,
                    "image": Path(request.image_path).name,
                },
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to insert floppy")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/insert-cd")
async def runtime_insert_cd(request: RuntimeInsertCDRequest):
    """
    Insert a CD image into a running emulation.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.insert_cd(request.image_path)

        if success:
            return StatusResponse(
                success=True,
                message="CD inserted",
                data={"image": Path(request.image_path).name},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to insert CD")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/config/{option}")
async def runtime_get_config(option: str):
    """
    Get a configuration option from the running emulation.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        value = await client.get_config(option)

        if value is not None:
            return StatusResponse(
                success=True,
                message=f"Config option: {option}",
                data={"option": option, "value": value},
            )
        else:
            raise HTTPException(status_code=404, detail=f"Unknown option: {option}")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/config")
async def runtime_set_config(request: RuntimeSetConfigRequest):
    """
    Set a configuration option on the running emulation.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.set_config(request.option, request.value)

        if success:
            return StatusResponse(
                success=True,
                message=f"Set {request.option} = {request.value}",
                data={"option": request.option, "value": request.value},
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to set {request.option}. Unknown option or invalid value.",
            )
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/ipc-check")
async def check_ipc_connection():
    """
    Check if Amiberry IPC is available and get connection status.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)

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
        raise HTTPException(status_code=500, detail=f"Error checking IPC: {str(e)}")


# New runtime control endpoints


@app.post("/runtime/eject-floppy")
async def runtime_eject_floppy(request: RuntimeEjectFloppyRequest):
    """
    Eject a floppy disk from a drive in the running emulation.
    Requires Amiberry to be running with IPC enabled.
    """
    if not 0 <= request.drive <= 3:
        raise HTTPException(status_code=400, detail="Drive must be 0-3")

    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.eject_floppy(request.drive)

        if success:
            return StatusResponse(
                success=True,
                message=f"Ejected disk from DF{request.drive}:",
                data={"drive": request.drive},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to eject floppy")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/eject-cd")
async def runtime_eject_cd():
    """
    Eject the CD from the running emulation.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.eject_cd()

        if success:
            return StatusResponse(success=True, message="CD ejected")
        else:
            raise HTTPException(status_code=500, detail="Failed to eject CD")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/list-floppies")
async def runtime_list_floppies():
    """
    List all floppy drives and their contents.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        floppies = await client.list_floppies()

        return StatusResponse(
            success=True,
            message="Floppy drives",
            data={"floppies": floppies},
        )
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/configs")
async def runtime_list_configs():
    """
    List available configuration files from the running emulation.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        configs = await client.list_configs()

        return StatusResponse(
            success=True,
            message="Available configurations",
            data={"configs": configs},
        )
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/volume")
async def runtime_get_volume():
    """
    Get the current master volume.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        volume = await client.get_volume()

        return StatusResponse(
            success=True,
            message=f"Volume: {volume}%",
            data={"volume": volume},
        )
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/volume")
async def runtime_set_volume(request: RuntimeSetVolumeRequest):
    """
    Set the master volume (0-100).
    Requires Amiberry to be running with IPC enabled.
    """
    if not 0 <= request.volume <= 100:
        raise HTTPException(status_code=400, detail="Volume must be 0-100")

    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.set_volume(request.volume)

        if success:
            return StatusResponse(
                success=True,
                message=f"Volume set to {request.volume}%",
                data={"volume": request.volume},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to set volume")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/mute")
async def runtime_mute():
    """
    Mute the audio.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.mute()

        if success:
            return StatusResponse(success=True, message="Audio muted")
        else:
            raise HTTPException(status_code=500, detail="Failed to mute")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/unmute")
async def runtime_unmute():
    """
    Unmute the audio.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.unmute()

        if success:
            return StatusResponse(success=True, message="Audio unmuted")
        else:
            raise HTTPException(status_code=500, detail="Failed to unmute")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/fullscreen")
async def runtime_toggle_fullscreen():
    """
    Toggle fullscreen/windowed mode.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.toggle_fullscreen()

        if success:
            return StatusResponse(success=True, message="Fullscreen toggled")
        else:
            raise HTTPException(status_code=500, detail="Failed to toggle fullscreen")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/warp")
async def runtime_get_warp():
    """
    Get the current warp mode status.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        enabled = await client.get_warp()

        return StatusResponse(
            success=True,
            message=f"Warp mode: {'enabled' if enabled else 'disabled'}",
            data={"enabled": enabled},
        )
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/warp")
async def runtime_set_warp(request: RuntimeSetWarpRequest):
    """
    Enable or disable warp mode.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.set_warp(request.enabled)

        if success:
            status = "enabled" if request.enabled else "disabled"
            return StatusResponse(
                success=True,
                message=f"Warp mode {status}",
                data={"enabled": request.enabled},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to set warp mode")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/version")
async def runtime_get_version():
    """
    Get Amiberry version info from the running emulation.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        version_info = await client.get_version()

        return StatusResponse(
            success=True,
            message="Amiberry version info",
            data=version_info,
        )
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/ping")
async def runtime_ping():
    """
    Ping the running Amiberry instance to test connectivity.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.ping()

        if success:
            return StatusResponse(success=True, message="PONG")
        else:
            raise HTTPException(status_code=500, detail="Ping failed")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/frame-advance")
async def runtime_frame_advance(request: RuntimeFrameAdvanceRequest):
    """
    Advance N frames when emulation is paused.
    Requires Amiberry to be running with IPC enabled.
    """
    if request.count < 1:
        raise HTTPException(status_code=400, detail="Count must be at least 1")

    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.frame_advance(request.count)

        if success:
            return StatusResponse(
                success=True,
                message=f"Advanced {request.count} frame(s)",
                data={"count": request.count},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to advance frames")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/key")
async def runtime_send_key(request: RuntimeSendKeyRequest):
    """
    Send keyboard input to the emulation.
    State: 0=release, 1=press.
    Requires Amiberry to be running with IPC enabled.
    """
    if request.state not in (0, 1):
        raise HTTPException(status_code=400, detail="State must be 0 or 1")

    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.send_key(request.keycode, request.state)

        if success:
            action = "pressed" if request.state == 1 else "released"
            return StatusResponse(
                success=True,
                message=f"Key {request.keycode} {action}",
                data={"keycode": request.keycode, "state": request.state},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to send key")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/mouse")
async def runtime_send_mouse(request: RuntimeSendMouseRequest):
    """
    Send mouse input to the emulation.
    Buttons: bit0=Left, bit1=Right, bit2=Middle.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.send_mouse(request.dx, request.dy, request.buttons)

        if success:
            return StatusResponse(
                success=True,
                message=f"Mouse moved ({request.dx}, {request.dy})",
                data={"dx": request.dx, "dy": request.dy, "buttons": request.buttons},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to send mouse input")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/mouse-speed")
async def runtime_set_mouse_speed(request: RuntimeSetMouseSpeedRequest):
    """
    Set mouse sensitivity (10-200).
    Requires Amiberry to be running with IPC enabled.
    """
    if not 10 <= request.speed <= 200:
        raise HTTPException(status_code=400, detail="Speed must be 10-200")

    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.set_mouse_speed(request.speed)

        if success:
            return StatusResponse(
                success=True,
                message=f"Mouse speed set to {request.speed}",
                data={"speed": request.speed},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to set mouse speed")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


# Round 2 runtime control endpoints


@app.post("/runtime/quicksave")
async def runtime_quicksave(request: RuntimeQuickSaveRequest):
    """
    Quick save to a slot (0-9).
    Requires Amiberry to be running with IPC enabled.
    """
    if not 0 <= request.slot <= 9:
        raise HTTPException(status_code=400, detail="Slot must be 0-9")

    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.quicksave(request.slot)

        if success:
            return StatusResponse(
                success=True,
                message=f"Quick saved to slot {request.slot}",
                data={"slot": request.slot},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to quick save")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/quickload")
async def runtime_quickload(request: RuntimeQuickLoadRequest):
    """
    Quick load from a slot (0-9).
    Requires Amiberry to be running with IPC enabled.
    """
    if not 0 <= request.slot <= 9:
        raise HTTPException(status_code=400, detail="Slot must be 0-9")

    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.quickload(request.slot)

        if success:
            return StatusResponse(
                success=True,
                message=f"Quick loading from slot {request.slot}",
                data={"slot": request.slot},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to quick load")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/joyport/{port}")
async def runtime_get_joyport_mode(port: int):
    """
    Get joystick port mode.
    Requires Amiberry to be running with IPC enabled.
    """
    if not 0 <= port <= 3:
        raise HTTPException(status_code=400, detail="Port must be 0-3")

    try:
        client = AmiberryIPCClient(prefer_dbus=False)
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
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/joyport")
async def runtime_set_joyport_mode(request: RuntimeSetJoyportModeRequest):
    """
    Set joystick port mode.
    Modes: 0=default, 2=mouse, 3=joystick, 4=gamepad, 7=cd32.
    Requires Amiberry to be running with IPC enabled.
    """
    if not 0 <= request.port <= 3:
        raise HTTPException(status_code=400, detail="Port must be 0-3")
    if not 0 <= request.mode <= 8:
        raise HTTPException(status_code=400, detail="Mode must be 0-8")

    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.set_joyport_mode(request.port, request.mode)

        if success:
            return StatusResponse(
                success=True,
                message=f"Port {request.port} mode set to {request.mode}",
                data={"port": request.port, "mode": request.mode},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to set port mode")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/autofire/{port}")
async def runtime_get_autofire(port: int):
    """
    Get autofire mode for a port.
    Requires Amiberry to be running with IPC enabled.
    """
    if not 0 <= port <= 3:
        raise HTTPException(status_code=400, detail="Port must be 0-3")

    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        mode = await client.get_autofire(port)

        if mode is not None:
            modes = {0: "off", 1: "normal", 2: "toggle", 3: "always", 4: "toggle_noaf"}
            return StatusResponse(
                success=True,
                message=f"Port {port} autofire: {modes.get(mode, 'unknown')}",
                data={"port": port, "mode": mode, "mode_name": modes.get(mode, "unknown")},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get autofire mode")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/autofire")
async def runtime_set_autofire(request: RuntimeSetAutofireRequest):
    """
    Set autofire mode for a port.
    Modes: 0=off, 1=normal, 2=toggle, 3=always, 4=toggle_noaf.
    Requires Amiberry to be running with IPC enabled.
    """
    if not 0 <= request.port <= 3:
        raise HTTPException(status_code=400, detail="Port must be 0-3")
    if not 0 <= request.mode <= 4:
        raise HTTPException(status_code=400, detail="Mode must be 0-4")

    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.set_autofire(request.port, request.mode)

        if success:
            return StatusResponse(
                success=True,
                message=f"Port {request.port} autofire set to {request.mode}",
                data={"port": request.port, "mode": request.mode},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to set autofire mode")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/led-status")
async def runtime_get_led_status():
    """
    Get all LED states (power, floppy, HD, CD, caps).
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        status = await client.get_led_status()

        return StatusResponse(
            success=True,
            message="LED status",
            data={"leds": status},
        )
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/harddrives")
async def runtime_list_harddrives():
    """
    List all mounted hard drives.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        drives = await client.list_harddrives()

        return StatusResponse(
            success=True,
            message="Mounted hard drives",
            data={"drives": drives},
        )
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/display-mode")
async def runtime_get_display_mode():
    """
    Get current display mode.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
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
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/display-mode")
async def runtime_set_display_mode(request: RuntimeSetDisplayModeRequest):
    """
    Set display mode.
    Modes: 0=window, 1=fullscreen, 2=fullwindow.
    Requires Amiberry to be running with IPC enabled.
    """
    if not 0 <= request.mode <= 2:
        raise HTTPException(status_code=400, detail="Mode must be 0-2")

    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.set_display_mode(request.mode)

        modes = {0: "window", 1: "fullscreen", 2: "fullwindow"}
        if success:
            return StatusResponse(
                success=True,
                message=f"Display mode set to {modes.get(request.mode)}",
                data={"mode": request.mode, "mode_name": modes.get(request.mode)},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to set display mode")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/ntsc")
async def runtime_get_ntsc():
    """
    Get current video mode (PAL or NTSC).
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
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
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/ntsc")
async def runtime_set_ntsc(request: RuntimeSetNTSCRequest):
    """
    Set video mode to PAL or NTSC.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.set_ntsc(request.enabled)

        mode = "NTSC" if request.enabled else "PAL"
        if success:
            return StatusResponse(
                success=True,
                message=f"Video mode set to {mode}",
                data={"ntsc": request.enabled, "mode_name": mode},
            )
        else:
            raise HTTPException(status_code=500, detail=f"Failed to set video mode to {mode}")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/sound-mode")
async def runtime_get_sound_mode():
    """
    Get current sound mode.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
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
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/sound-mode")
async def runtime_set_sound_mode(request: RuntimeSetSoundModeRequest):
    """
    Set sound mode.
    Modes: 0=off, 1=normal, 2=stereo, 3=best.
    Requires Amiberry to be running with IPC enabled.
    """
    if not 0 <= request.mode <= 3:
        raise HTTPException(status_code=400, detail="Mode must be 0-3")

    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.set_sound_mode(request.mode)

        modes = {0: "off", 1: "normal", 2: "stereo", 3: "best"}
        if success:
            return StatusResponse(
                success=True,
                message=f"Sound mode set to {modes.get(request.mode)}",
                data={"mode": request.mode, "mode_name": modes.get(request.mode)},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to set sound mode")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


# Round 3 runtime control endpoints


@app.post("/runtime/mouse-grab")
async def runtime_toggle_mouse_grab():
    """
    Toggle mouse capture.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.toggle_mouse_grab()

        if success:
            return StatusResponse(success=True, message="Mouse grab toggled")
        else:
            raise HTTPException(status_code=500, detail="Failed to toggle mouse grab")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/mouse-speed")
async def runtime_get_mouse_speed():
    """
    Get current mouse speed.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        speed = await client.get_mouse_speed()

        if speed is not None:
            return StatusResponse(
                success=True,
                message=f"Mouse speed: {speed}",
                data={"speed": speed},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get mouse speed")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/cpu-speed")
async def runtime_get_cpu_speed():
    """
    Get current CPU speed setting.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
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
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/cpu-speed")
async def runtime_set_cpu_speed(request: RuntimeSetCPUSpeedRequest):
    """
    Set CPU speed.
    Speed: -1=max, 0=cycle-exact, >0=percentage.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.set_cpu_speed(request.speed)

        if success:
            return StatusResponse(
                success=True,
                message=f"CPU speed set to {request.speed}",
                data={"speed": request.speed},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to set CPU speed")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/rtg")
async def runtime_toggle_rtg(request: RuntimeToggleRTGRequest = RuntimeToggleRTGRequest()):
    """
    Toggle between RTG and chipset display.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        result = await client.toggle_rtg(request.monid)

        if result:
            return StatusResponse(
                success=True,
                message=f"Display mode: {result}",
                data={"mode": result},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to toggle RTG")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/floppy-speed")
async def runtime_get_floppy_speed():
    """
    Get current floppy drive speed.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
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
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/floppy-speed")
async def runtime_set_floppy_speed(request: RuntimeSetFloppySpeedRequest):
    """
    Set floppy drive speed.
    Speed: 0=turbo, 100=1x, 200=2x, 400=4x, 800=8x.
    Requires Amiberry to be running with IPC enabled.
    """
    if request.speed not in (0, 100, 200, 400, 800):
        raise HTTPException(status_code=400, detail="Speed must be 0, 100, 200, 400, or 800")

    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.set_floppy_speed(request.speed)

        descs = {0: "turbo", 100: "1x", 200: "2x", 400: "4x", 800: "8x"}
        if success:
            return StatusResponse(
                success=True,
                message=f"Floppy speed set to {descs.get(request.speed)}",
                data={"speed": request.speed, "description": descs.get(request.speed)},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to set floppy speed")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/disk-write-protect/{drive}")
async def runtime_get_disk_write_protect(drive: int):
    """
    Get write protection status for a floppy disk.
    Requires Amiberry to be running with IPC enabled.
    """
    if not 0 <= drive <= 3:
        raise HTTPException(status_code=400, detail="Drive must be 0-3")

    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        result = await client.get_disk_write_protect(drive)

        if result:
            is_protected, status = result
            return StatusResponse(
                success=True,
                message=f"Drive DF{drive}: {status}",
                data={"drive": drive, "protected": is_protected, "status": status},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get write protection status")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/disk-write-protect")
async def runtime_disk_write_protect(request: RuntimeDiskWriteProtectRequest):
    """
    Set write protection on a floppy disk.
    Requires Amiberry to be running with IPC enabled.
    """
    if not 0 <= request.drive <= 3:
        raise HTTPException(status_code=400, detail="Drive must be 0-3")

    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.disk_write_protect(request.drive, request.protect)

        status = "protected" if request.protect else "writable"
        if success:
            return StatusResponse(
                success=True,
                message=f"Drive DF{request.drive} set to {status}",
                data={"drive": request.drive, "protected": request.protect},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to set write protection")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/status-line")
async def runtime_toggle_status_line():
    """
    Toggle on-screen status line (cycle: off/chipset/rtg/both).
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
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
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/chipset")
async def runtime_get_chipset():
    """
    Get current chipset.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
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
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


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

    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.set_chipset(request.chipset)

        if success:
            return StatusResponse(
                success=True,
                message=f"Chipset set to {request.chipset.upper()}",
                data={"chipset": request.chipset.upper()},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to set chipset")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/memory-config")
async def runtime_get_memory_config():
    """
    Get all memory sizes (chip, fast, bogo, z3, rtg).
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        config = await client.get_memory_config()

        return StatusResponse(
            success=True,
            message="Memory configuration",
            data={"memory": config},
        )
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/fps")
async def runtime_get_fps():
    """
    Get current frame rate and performance info.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        info = await client.get_fps()

        return StatusResponse(
            success=True,
            message="Performance info",
            data=info,
        )
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


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
        raise HTTPException(status_code=400, detail=f"Size must be one of: {valid_sizes}")

    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.set_chip_mem(request.size_kb)

        if success:
            return StatusResponse(
                success=True,
                message=f"Chip RAM set to {request.size_kb} KB. Reset required for changes to take effect.",
                data={"size_kb": request.size_kb},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to set Chip RAM size")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


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
        raise HTTPException(status_code=400, detail=f"Size must be one of: {valid_sizes}")

    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.set_fast_mem(request.size_kb)

        if success:
            return StatusResponse(
                success=True,
                message=f"Fast RAM set to {request.size_kb} KB. Reset required for changes to take effect.",
                data={"size_kb": request.size_kb},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to set Fast RAM size")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


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
        raise HTTPException(status_code=400, detail=f"Size must be one of: {valid_sizes}")

    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.set_slow_mem(request.size_kb)

        if success:
            return StatusResponse(
                success=True,
                message=f"Slow RAM set to {request.size_kb} KB. Reset required for changes to take effect.",
                data={"size_kb": request.size_kb},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to set Slow RAM size")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


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
        raise HTTPException(status_code=400, detail=f"Size must be one of: {valid_sizes}")

    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.set_z3_mem(request.size_mb)

        if success:
            return StatusResponse(
                success=True,
                message=f"Z3 Fast RAM set to {request.size_mb} MB. Reset required for changes to take effect.",
                data={"size_mb": request.size_mb},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to set Z3 Fast RAM size")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/cpu-model")
async def runtime_get_cpu_model():
    """
    Get CPU model information.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        info = await client.get_cpu_model()

        return StatusResponse(
            success=True,
            message="CPU model information",
            data=info,
        )
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


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
        raise HTTPException(status_code=400, detail=f"Model must be one of: {valid_models}")

    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.set_cpu_model(request.model)

        if success:
            return StatusResponse(
                success=True,
                message=f"CPU model set to {request.model}. Reset required for changes to take effect.",
                data={"model": request.model},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to set CPU model")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/window-size")
async def runtime_get_window_size():
    """
    Get current window size.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        info = await client.get_window_size()

        return StatusResponse(
            success=True,
            message=f"Window size: {info.get('width', '?')}x{info.get('height', '?')}",
            data=info,
        )
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/window-size")
async def runtime_set_window_size(request: RuntimeSetWindowSizeRequest):
    """
    Set window size.
    Width: 320-3840, Height: 200-2160.
    Requires Amiberry to be running with IPC enabled.
    """
    if not 320 <= request.width <= 3840:
        raise HTTPException(status_code=400, detail="Width must be 320-3840")
    if not 200 <= request.height <= 2160:
        raise HTTPException(status_code=400, detail="Height must be 200-2160")

    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.set_window_size(request.width, request.height)

        if success:
            return StatusResponse(
                success=True,
                message=f"Window size set to {request.width}x{request.height}",
                data={"width": request.width, "height": request.height},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to set window size")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/scaling")
async def runtime_get_scaling():
    """
    Get current scaling mode.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        info = await client.get_scaling()

        return StatusResponse(
            success=True,
            message="Scaling mode",
            data=info,
        )
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/scaling")
async def runtime_set_scaling(request: RuntimeSetScalingRequest):
    """
    Set scaling mode.
    Modes: -1=auto, 0=nearest, 1=linear, 2=integer.
    Requires Amiberry to be running with IPC enabled.
    """
    if not -1 <= request.mode <= 2:
        raise HTTPException(status_code=400, detail="Mode must be -1..2")

    mode_names = ["auto", "nearest", "linear", "integer"]
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.set_scaling(request.mode)

        if success:
            mode_index = request.mode + 1  # -1..2 -> 0..3
            return StatusResponse(
                success=True,
                message=f"Scaling mode set to {mode_names[mode_index]}",
                data={"mode": request.mode, "mode_name": mode_names[mode_index]},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to set scaling mode")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/line-mode")
async def runtime_get_line_mode():
    """
    Get current line mode.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        info = await client.get_line_mode()

        return StatusResponse(
            success=True,
            message="Line mode",
            data=info,
        )
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/line-mode")
async def runtime_set_line_mode(request: RuntimeSetLineModeRequest):
    """
    Set line mode.
    Modes: 0=single, 1=double, 2=scanlines.
    Requires Amiberry to be running with IPC enabled.
    """
    if not 0 <= request.mode <= 2:
        raise HTTPException(status_code=400, detail="Mode must be 0-2")

    mode_names = ["single", "double", "scanlines"]
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.set_line_mode(request.mode)

        if success:
            return StatusResponse(
                success=True,
                message=f"Line mode set to {mode_names[request.mode]}",
                data={"mode": request.mode, "mode_name": mode_names[request.mode]},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to set line mode")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/resolution")
async def runtime_get_resolution():
    """
    Get current display resolution.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
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
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/resolution")
async def runtime_set_resolution(request: RuntimeSetResolutionRequest):
    """
    Set display resolution.
    Modes: 0=lores, 1=hires, 2=superhires.
    Requires Amiberry to be running with IPC enabled.
    """
    if not 0 <= request.mode <= 2:
        raise HTTPException(status_code=400, detail="Mode must be 0-2")

    mode_names = ["lores", "hires", "superhires"]
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.set_resolution(request.mode)

        if success:
            return StatusResponse(
                success=True,
                message=f"Resolution set to {mode_names[request.mode]}",
                data={"mode": request.mode, "mode_name": mode_names[request.mode]},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to set resolution")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


# Round 5 - Autocrop and WHDLoad
@app.get("/runtime/autocrop")
async def runtime_get_autocrop():
    """
    Get current autocrop status.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        result = await client.get_autocrop()

        if result is not None:
            return StatusResponse(
                success=True,
                message=f"Autocrop: {'enabled' if result else 'disabled'}",
                data={"enabled": result},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get autocrop status")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/autocrop")
async def runtime_set_autocrop(request: RuntimeSetAutocropRequest):
    """
    Enable or disable automatic display cropping.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.set_autocrop(request.enabled)

        if success:
            return StatusResponse(
                success=True,
                message=f"Autocrop {'enabled' if request.enabled else 'disabled'}",
                data={"enabled": request.enabled},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to set autocrop")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/whdload")
async def runtime_get_whdload():
    """
    Get information about the currently loaded WHDLoad game.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
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
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/whdload")
async def runtime_insert_whdload(request: RuntimeInsertWHDLoadRequest):
    """
    Load a WHDLoad game from an LHA archive or directory.
    Note: A reset may be required for the game to start.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.insert_whdload(request.path)

        if success:
            return StatusResponse(
                success=True,
                message=f"WHDLoad game loaded: {request.path}. Note: A reset may be required.",
                data={"path": request.path},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to load WHDLoad game")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.delete("/runtime/whdload")
async def runtime_eject_whdload():
    """
    Eject the currently loaded WHDLoad game.
    Requires Amiberry to be running with IPC enabled.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.eject_whdload()

        if success:
            return StatusResponse(
                success=True,
                message="WHDLoad game ejected",
                data={},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to eject WHDLoad game")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


# === Round 6 - Debugging and Diagnostics ===


@app.post("/runtime/debug/activate")
async def runtime_debug_activate():
    """
    Activate the built-in debugger.
    Requires Amiberry to be built with debugger support.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.debug_activate()

        if success:
            return StatusResponse(
                success=True,
                message="Debugger activated",
                data={},
            )
        else:
            raise HTTPException(
                status_code=500,
                detail="Failed to activate debugger. Amiberry may not be built with debugger support.",
            )
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/debug/deactivate")
async def runtime_debug_deactivate():
    """
    Deactivate the debugger and resume emulation.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.debug_deactivate()

        if success:
            return StatusResponse(
                success=True,
                message="Debugger deactivated, emulation resumed",
                data={},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to deactivate debugger")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/debug/status")
async def runtime_debug_status():
    """
    Get debugger status (active/inactive).
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        info = await client.debug_status()

        if info:
            return StatusResponse(
                success=True,
                message="Debugger status retrieved",
                data=info,
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get debugger status")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/debug/step")
async def runtime_debug_step(request: RuntimeDebugStepRequest):
    """
    Single-step CPU instructions when debugger is active.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.debug_step(request.count)

        if success:
            return StatusResponse(
                success=True,
                message=f"Stepped {request.count} instruction(s)",
                data={"count": request.count},
            )
        else:
            raise HTTPException(
                status_code=500, detail="Failed to step. Debugger may not be active."
            )
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/debug/continue")
async def runtime_debug_continue():
    """
    Continue execution until next breakpoint.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.debug_continue()

        if success:
            return StatusResponse(
                success=True,
                message="Execution continued",
                data={},
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to continue execution")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/cpu/regs")
async def runtime_get_cpu_regs():
    """
    Get all CPU registers (D0-D7, A0-A7, PC, SR, USP, ISP).
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        info = await client.get_cpu_regs()

        if info:
            return StatusResponse(
                success=True,
                message="CPU registers retrieved",
                data=info,
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get CPU registers")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/custom/regs")
async def runtime_get_custom_regs():
    """
    Get key custom chip registers (DMACON, INTENA, INTREQ, Copper addresses).
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        info = await client.get_custom_regs()

        if info:
            return StatusResponse(
                success=True,
                message="Custom registers retrieved",
                data=info,
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get custom registers")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/disassemble")
async def runtime_disassemble(request: RuntimeDisassembleRequest):
    """
    Disassemble instructions at a memory address.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        lines = await client.disassemble(request.address, request.count)

        return StatusResponse(
            success=True,
            message=f"Disassembly at {request.address}",
            data={"address": request.address, "count": request.count, "lines": lines},
        )
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/breakpoints")
async def runtime_list_breakpoints():
    """
    List all active breakpoints.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        breakpoints = await client.list_breakpoints()

        return StatusResponse(
            success=True,
            message=f"Found {len(breakpoints)} breakpoint(s)",
            data={"breakpoints": breakpoints},
        )
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/breakpoints")
async def runtime_set_breakpoint(request: RuntimeSetBreakpointRequest):
    """
    Set a breakpoint at a memory address. Maximum 20 breakpoints.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.set_breakpoint(request.address)

        if success:
            return StatusResponse(
                success=True,
                message=f"Breakpoint set at {request.address}",
                data={"address": request.address},
            )
        else:
            raise HTTPException(
                status_code=500,
                detail="Failed to set breakpoint. Maximum 20 breakpoints allowed.",
            )
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.delete("/runtime/breakpoints")
async def runtime_clear_breakpoint(request: RuntimeClearBreakpointRequest):
    """
    Clear a breakpoint at a specific address or all breakpoints (address='ALL').
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.clear_breakpoint(request.address)

        if success:
            if request.address.upper() == "ALL":
                return StatusResponse(
                    success=True,
                    message="All breakpoints cleared",
                    data={},
                )
            else:
                return StatusResponse(
                    success=True,
                    message=f"Breakpoint at {request.address} cleared",
                    data={"address": request.address},
                )
        else:
            raise HTTPException(status_code=500, detail="Failed to clear breakpoint")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/copper/state")
async def runtime_get_copper_state():
    """
    Get Copper coprocessor state (addresses, enabled status).
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        info = await client.get_copper_state()

        if info:
            return StatusResponse(
                success=True,
                message="Copper state retrieved",
                data=info,
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get Copper state")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/blitter/state")
async def runtime_get_blitter_state():
    """
    Get Blitter state (busy status, channels, dimensions, addresses).
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        info = await client.get_blitter_state()

        if info:
            return StatusResponse(
                success=True,
                message="Blitter state retrieved",
                data=info,
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get Blitter state")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/drive/state")
async def runtime_get_drive_state(drive: Optional[int] = None):
    """
    Get floppy drive state (track, side, motor, disk inserted).
    Optionally specify drive number 0-3, or omit for all drives.
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        info = await client.get_drive_state(drive)

        if info:
            return StatusResponse(
                success=True,
                message=f"Drive state retrieved{' (DF' + str(drive) + ')' if drive is not None else ''}",
                data=info,
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get drive state")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/audio/state")
async def runtime_get_audio_state():
    """
    Get audio channel states (volume, period, enabled).
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        info = await client.get_audio_state()

        if info:
            return StatusResponse(
                success=True,
                message="Audio state retrieved",
                data=info,
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get audio state")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/runtime/dma/state")
async def runtime_get_dma_state():
    """
    Get DMA channel states (bitplane, sprite, audio, disk, copper, blitter).
    """
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        info = await client.get_dma_state()

        if info:
            return StatusResponse(
                success=True,
                message="DMA state retrieved",
                data=info,
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to get DMA state")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


# === Process Lifecycle Management ===


@app.get("/process/alive")
async def check_process_alive():
    """Check if the Amiberry process is still running."""
    if _http_amiberry_process is None:
        return StatusResponse(
            success=False,
            message="No Amiberry process tracked. Launch Amiberry first.",
        )
    returncode = _http_amiberry_process.poll()
    if returncode is None:
        return StatusResponse(
            success=True,
            message=f"Amiberry is RUNNING (PID: {_http_amiberry_process.pid})",
            data={"pid": _http_amiberry_process.pid, "running": True},
        )
    else:
        signal_info = ""
        if returncode < 0:
            import signal
            try:
                sig = signal.Signals(-returncode)
                signal_info = f" (killed by signal {sig.name})"
            except ValueError:
                signal_info = f" (killed by signal {-returncode})"
        return StatusResponse(
            success=True,
            message=f"Amiberry has EXITED with code {returncode}{signal_info}",
            data={"pid": _http_amiberry_process.pid, "running": False, "exit_code": returncode},
        )


@app.get("/process/info")
async def get_process_info():
    """Get detailed process information."""
    if _http_amiberry_process is None:
        return StatusResponse(
            success=False,
            message="No Amiberry process tracked. Launch Amiberry first.",
        )
    returncode = _http_amiberry_process.poll()
    data = {"pid": _http_amiberry_process.pid}

    if returncode is None:
        data["status"] = "RUNNING"
    else:
        data["status"] = "EXITED"
        data["exit_code"] = returncode
        if returncode < 0:
            import signal
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

    if _http_amiberry_launch_cmd:
        data["command"] = " ".join(_http_amiberry_launch_cmd)
    if _http_amiberry_log_path:
        data["log_file"] = str(_http_amiberry_log_path)

    return StatusResponse(success=True, message="Process info", data=data)


@app.post("/process/kill")
async def kill_amiberry_process():
    """Force kill the running Amiberry process."""
    if _http_amiberry_process is None or _http_amiberry_process.poll() is not None:
        return StatusResponse(success=False, message="No running Amiberry process to kill.")

    pid = _http_amiberry_process.pid
    _http_amiberry_process.terminate()
    try:
        _http_amiberry_process.wait(timeout=5)
        return StatusResponse(success=True, message=f"Amiberry process (PID {pid}) terminated gracefully.")
    except subprocess.TimeoutExpired:
        _http_amiberry_process.kill()
        try:
            _http_amiberry_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        return StatusResponse(success=True, message=f"Amiberry process (PID {pid}) force killed.")


@app.post("/process/wait-for-exit")
async def wait_for_exit(request: WaitForExitRequest):
    """Wait for the Amiberry process to exit."""
    if _http_amiberry_process is None:
        return StatusResponse(success=False, message="No Amiberry process tracked.")

    returncode = _http_amiberry_process.poll()
    if returncode is not None:
        return StatusResponse(
            success=True,
            message=f"Amiberry already exited with code {returncode}.",
            data={"exit_code": returncode},
        )
    try:
        returncode = _http_amiberry_process.wait(timeout=request.timeout)
        return StatusResponse(
            success=True,
            message=f"Amiberry exited with code {returncode}.",
            data={"exit_code": returncode},
        )
    except subprocess.TimeoutExpired:
        return StatusResponse(
            success=False,
            message=f"Timeout after {request.timeout}s. Amiberry still running (PID {_http_amiberry_process.pid}).",
        )


@app.post("/process/restart")
async def restart_amiberry_process():
    """Kill and re-launch Amiberry with the same command."""
    global _http_amiberry_process, _http_amiberry_launch_cmd, _http_amiberry_log_path
    if _http_amiberry_launch_cmd is None:
        return StatusResponse(success=False, message="No previous launch command stored.")

    if _http_amiberry_process is not None and _http_amiberry_process.poll() is None:
        _http_amiberry_process.terminate()
        try:
            _http_amiberry_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _http_amiberry_process.kill()
            try:
                _http_amiberry_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    cmd = _http_amiberry_launch_cmd
    try:
        if _http_amiberry_log_path:
            log_file = open(_http_amiberry_log_path, "w")
            _http_amiberry_process = subprocess.Popen(
                cmd, stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True,
            )
        else:
            _http_amiberry_process = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
            )
        return StatusResponse(
            success=True,
            message=f"Amiberry restarted (PID: {_http_amiberry_process.pid})",
            data={"pid": _http_amiberry_process.pid, "command": " ".join(cmd)},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error restarting: {str(e)}")


# === Missing IPC Wrappers ===


@app.post("/runtime/memory/read")
async def runtime_read_memory(request: RuntimeReadMemoryRequest):
    """Read memory from the emulated Amiga."""
    try:
        address = int(request.address, 0)
        if request.width not in (1, 2, 4):
            raise HTTPException(status_code=400, detail="Width must be 1, 2, or 4")
        client = AmiberryIPCClient(prefer_dbus=False)
        value = await client.read_memory(address, request.width)
        if value is not None:
            return StatusResponse(
                success=True,
                message=f"Memory at 0x{address:08X}: 0x{value:0{request.width*2}X} ({value})",
                data={"address": f"0x{address:08X}", "value": value, "hex": f"0x{value:0{request.width*2}X}", "width": request.width},
            )
        else:
            raise HTTPException(status_code=500, detail=f"Failed to read memory at {request.address}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid address: {str(e)}")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/memory/write")
async def runtime_write_memory(request: RuntimeWriteMemoryRequest):
    """Write memory to the emulated Amiga."""
    try:
        address = int(request.address, 0)
        if request.width not in (1, 2, 4):
            raise HTTPException(status_code=400, detail="Width must be 1, 2, or 4")
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.write_memory(address, request.width, request.value)
        if success:
            return StatusResponse(
                success=True,
                message=f"Wrote 0x{request.value:0{request.width*2}X} to 0x{address:08X}",
            )
        else:
            raise HTTPException(status_code=500, detail=f"Failed to write memory at {request.address}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid argument: {str(e)}")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/load-config")
async def runtime_load_config(request: RuntimeLoadConfigRequest):
    """Load a .uae configuration file into the running emulation."""
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.load_config(request.config_path)
        if success:
            return StatusResponse(success=True, message=f"Configuration loaded: {request.config_path}")
        else:
            raise HTTPException(status_code=500, detail=f"Failed to load config: {request.config_path}")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/runtime/debug/step-over")
async def runtime_debug_step_over():
    """Step over subroutine calls (JSR/BSR)."""
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.debug_step_over()
        if success:
            return StatusResponse(success=True, message="Stepped over subroutine.")
        else:
            raise HTTPException(status_code=500, detail="Failed to step over. Is debugger active?")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


# === Screenshot with Image Data ===


@app.post("/runtime/screenshot-view")
async def runtime_screenshot_view(request: RuntimeScreenshotViewRequest):
    """Take a screenshot and return the image data as base64."""
    import base64
    from .config import SCREENSHOT_DIR

    filename = request.filename
    if not filename:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = str(SCREENSHOT_DIR / f"debug_{timestamp}.png")

    try:
        client = AmiberryIPCClient(prefer_dbus=False)
        success = await client.screenshot(filename)
        if success:
            screenshot_path = Path(filename)
            if screenshot_path.exists():
                image_data = screenshot_path.read_bytes()
                b64_data = base64.b64encode(image_data).decode("utf-8")
                mime_type = "image/png" if filename.lower().endswith(".png") else "image/bmp"
                return StatusResponse(
                    success=True,
                    message=f"Screenshot saved to: {filename}",
                    data={"path": filename, "base64": b64_data, "mime_type": mime_type, "size": len(image_data)},
                )
            else:
                raise HTTPException(status_code=500, detail=f"Screenshot file not found: {filename}")
        else:
            raise HTTPException(status_code=500, detail="Failed to take screenshot")
    except IPCConnectionError as e:
        raise HTTPException(status_code=503, detail=f"IPC connection error: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


# === Log Tailing and Crash Detection ===


@app.post("/logs/tail")
async def tail_log(request: TailLogRequest):
    """Get new log lines since last read."""
    log_name = request.log_name
    if not log_name.endswith(".log"):
        log_name += ".log"
    log_path = LOG_DIR / log_name

    if not log_path.exists():
        raise HTTPException(status_code=404, detail=f"Log file not found: {log_name}")

    last_pos = _http_last_log_read_position.get(log_name, 0)
    try:
        with open(log_path, "r", errors="replace") as f:
            f.seek(last_pos)
            new_content = f.read()
            new_pos = f.tell()

        _http_last_log_read_position[log_name] = new_pos

        if new_content:
            line_count = new_content.count("\n")
            return StatusResponse(
                success=True,
                message=f"New log output ({line_count} lines)",
                data={"content": new_content, "lines": line_count},
            )
        else:
            return StatusResponse(success=True, message="No new log output since last read.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading log: {str(e)}")


@app.post("/logs/wait-for-pattern")
async def wait_for_log_pattern(request: WaitForLogPatternRequest):
    """Wait for a pattern to appear in a log file."""
    import re

    log_name = request.log_name
    if not log_name.endswith(".log"):
        log_name += ".log"
    log_path = LOG_DIR / log_name

    try:
        compiled_pattern = re.compile(request.pattern)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {str(e)}")

    start_time = asyncio.get_event_loop().time()
    last_pos = _http_last_log_read_position.get(log_name, 0)

    while True:
        elapsed = asyncio.get_event_loop().time() - start_time
        if elapsed >= request.timeout:
            return StatusResponse(
                success=False,
                message=f"Timeout after {request.timeout}s. Pattern '{request.pattern}' not found.",
            )

        if log_path.exists():
            try:
                with open(log_path, "r", errors="replace") as f:
                    f.seek(last_pos)
                    new_content = f.read()
                    new_pos = f.tell()

                if new_content:
                    for line in new_content.splitlines():
                        if compiled_pattern.search(line):
                            _http_last_log_read_position[log_name] = new_pos
                            return StatusResponse(
                                success=True,
                                message=f"Pattern found after {elapsed:.1f}s",
                                data={"line": line, "elapsed": round(elapsed, 1)},
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
    if _http_amiberry_process is not None:
        returncode = _http_amiberry_process.poll()
        if returncode is None:
            data["process"] = {"status": "RUNNING", "pid": _http_amiberry_process.pid}
        else:
            proc_info = {"status": "EXITED", "exit_code": returncode, "pid": _http_amiberry_process.pid}
            if returncode < 0:
                import signal
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
        "Segmentation fault", "SIGSEGV", "SIGABRT", "Aborted",
        "core dumped", "assertion failed", "FATAL", "Bus error",
        "SIGBUS", "double free", "heap-buffer-overflow",
        "stack-buffer-overflow", "AddressSanitizer", "undefined behavior",
    ]

    log_files_to_scan: list[Path] = []
    if request.log_name:
        ln = request.log_name if request.log_name.endswith(".log") else request.log_name + ".log"
        log_files_to_scan = [LOG_DIR / ln]
    elif _http_amiberry_log_path:
        log_files_to_scan = [_http_amiberry_log_path]
    elif LOG_DIR.exists():
        log_files_to_scan = sorted(LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:1]

    crash_indicators = []
    for lp in log_files_to_scan:
        if lp.exists():
            try:
                content = lp.read_text(errors="replace")
                for i, line in enumerate(content.splitlines()):
                    for cp in crash_patterns:
                        if cp.lower() in line.lower():
                            crash_indicators.append({"line_number": i + 1, "text": line.strip(), "file": lp.name})
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
    if _http_amiberry_process is not None:
        rc = _http_amiberry_process.poll()
        if rc is None:
            data["process"] = {"status": "RUNNING", "pid": _http_amiberry_process.pid}
        else:
            data["process"] = {"status": "EXITED", "exit_code": rc}
    else:
        data["process"] = {"status": "NOT_TRACKED"}

    # IPC check
    try:
        client = AmiberryIPCClient(prefer_dbus=False)
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
    global _http_amiberry_process, _http_amiberry_launch_cmd, _http_amiberry_log_path

    # Kill existing process
    if _http_amiberry_process is not None and _http_amiberry_process.poll() is None:
        _http_amiberry_process.terminate()
        try:
            _http_amiberry_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _http_amiberry_process.kill()
            try:
                _http_amiberry_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    # Build command
    cmd = [EMULATOR_BINARY, "--log"]

    if request.model:
        cmd.extend(["--model", request.model])
    elif request.config:
        config_path = _find_config_path(request.config)
        if not config_path:
            raise HTTPException(status_code=404, detail=f"Config '{request.config}' not found")
        cmd.extend(["-f", str(config_path)])

    if request.disk_image:
        cmd.extend(["-0", request.disk_image])

    if request.lha_file:
        lha_path = Path(request.lha_file)
        if not lha_path.exists():
            raise HTTPException(status_code=404, detail=f"LHA not found: {request.lha_file}")
        cmd.append(str(lha_path))

    if request.autostart:
        cmd.append("-G")

    # Launch with logging
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_name = f"amiberry_{timestamp}.log"
    log_path = LOG_DIR / log_name

    try:
        log_file = open(log_path, "w")
        _http_amiberry_process = subprocess.Popen(
            cmd, stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True,
        )
        _http_amiberry_launch_cmd = cmd
        _http_amiberry_log_path = log_path
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error launching: {str(e)}")

    # Wait for IPC
    start_time = asyncio.get_event_loop().time()
    ipc_ready = False

    while True:
        elapsed = asyncio.get_event_loop().time() - start_time
        if elapsed >= request.timeout:
            break

        if _http_amiberry_process.poll() is not None:
            rc = _http_amiberry_process.returncode
            raise HTTPException(
                status_code=500,
                detail=f"Amiberry exited before IPC available (code {rc})",
            )

        try:
            client = AmiberryIPCClient(prefer_dbus=False)
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
                "pid": _http_amiberry_process.pid,
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
                "pid": _http_amiberry_process.pid,
                "log_file": str(log_path),
                "still_running": _http_amiberry_process.poll() is None,
            },
        )


def main():
    """Main entry point for the HTTP API server."""
    ensure_directories_exist()

    print(f"Starting Amiberry HTTP API Server on http://localhost:8080")
    print(f"Platform: {platform.system()}")
    print(f"Config directory: {CONFIG_DIR}")
    print(f"\nAPI Documentation: http://localhost:8080/docs")
    print(f"Integration Guide: docs/HTTP_API_GUIDE.md")

    uvicorn.run(app, host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
