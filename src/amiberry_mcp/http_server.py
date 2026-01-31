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
from typing import Optional, List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

from .config import (
    IS_MACOS,
    IS_LINUX,
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


class StatusResponse(BaseModel):
    success: bool
    message: str
    data: Optional[dict] = None


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
