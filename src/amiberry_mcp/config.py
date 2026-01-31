"""
Shared configuration for Amiberry MCP Server.
Platform detection and path configuration used by both MCP and HTTP servers.
"""

import os
import platform
from pathlib import Path

# Platform detection
IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

if IS_MACOS:
    EMULATOR_BINARY = "/Applications/Amiberry.app/Contents/MacOS/Amiberry"
    AMIBERRY_HOME = Path.home() / "Amiberry"
    CONFIG_DIR = AMIBERRY_HOME / "Configurations"
    SYSTEM_CONFIG_DIR = None  # macOS doesn't have separate system configs
    SAVESTATE_DIR = AMIBERRY_HOME / "Savestates"
    SCREENSHOT_DIR = AMIBERRY_HOME / "Screenshots"
    DISK_IMAGE_DIRS = [
        AMIBERRY_HOME / "Floppies",
        AMIBERRY_HOME / "Harddrives",
        AMIBERRY_HOME / "Lha",
    ]
elif IS_LINUX:
    EMULATOR_BINARY = "amiberry"  # Assumes it's in PATH
    AMIBERRY_HOME = Path.home() / "Amiberry"

    # XDG_CONFIG_HOME defaults to ~/.config if not set
    XDG_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))

    CONFIG_DIR = AMIBERRY_HOME / "conf"  # User configs
    SYSTEM_CONFIG_DIR = XDG_CONFIG_HOME / "amiberry"  # System configs
    SAVESTATE_DIR = AMIBERRY_HOME / "savestates"
    SCREENSHOT_DIR = AMIBERRY_HOME / "screenshots"
    DISK_IMAGE_DIRS = [
        AMIBERRY_HOME / "floppies",
        AMIBERRY_HOME / "harddrives",
        AMIBERRY_HOME / "lha",
    ]
else:
    raise RuntimeError(f"Unsupported platform: {platform.system()}")

# Supported file extensions
FLOPPY_EXTENSIONS = [".adf", ".adz", ".dms"]
HARDFILE_EXTENSIONS = [".hdf", ".hdz"]
LHA_EXTENSIONS = [".lha"]
ALL_DISK_EXTENSIONS = FLOPPY_EXTENSIONS + HARDFILE_EXTENSIONS + LHA_EXTENSIONS

# Supported Amiga models for quick-launch
SUPPORTED_MODELS = ["A500", "A1200", "CD32"]


def get_platform_info() -> dict:
    """Return platform and path information as a dictionary."""
    info = {
        "platform": platform.system(),
        "emulator_binary": str(EMULATOR_BINARY),
        "amiberry_home": str(AMIBERRY_HOME),
        "config_dir": str(CONFIG_DIR),
        "savestate_dir": str(SAVESTATE_DIR),
        "screenshot_dir": str(SCREENSHOT_DIR),
        "disk_image_dirs": [str(d) for d in DISK_IMAGE_DIRS],
    }

    if IS_LINUX and SYSTEM_CONFIG_DIR:
        info["system_config_dir"] = str(SYSTEM_CONFIG_DIR)

    return info


def ensure_directories_exist() -> None:
    """Create necessary directories if they don't exist."""
    directories = [CONFIG_DIR, SAVESTATE_DIR, SCREENSHOT_DIR] + DISK_IMAGE_DIRS
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
