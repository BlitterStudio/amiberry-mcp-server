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
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

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
        with open(log_path, "w") as log_file:
            subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

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
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

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
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

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
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

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
