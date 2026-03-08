"""
Shared configuration for Amiberry MCP Server.
Platform detection and path configuration used by both MCP and HTTP servers.
"""

import os
import platform
from pathlib import Path

# Platform detection - cache the result to avoid repeated system calls
_PLATFORM = platform.system()
IS_MACOS = _PLATFORM == "Darwin"
IS_LINUX = _PLATFORM == "Linux"

if IS_MACOS:
    EMULATOR_BINARY = "/Applications/Amiberry.app/Contents/MacOS/Amiberry"
    AMIBERRY_HOME = Path.home() / "Library" / "Application Support" / "Amiberry"
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

    # XDG_CONFIG_HOME defaults to ~/.config if not set or empty
    XDG_CONFIG_HOME = Path(
        os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    )

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
    raise RuntimeError(f"Unsupported platform: {_PLATFORM}")

# Supported file extensions
FLOPPY_EXTENSIONS = [".adf", ".adz", ".dms"]
HARDFILE_EXTENSIONS = [".hdf", ".hdz"]
LHA_EXTENSIONS = [".lha"]
CD_EXTENSIONS = [".iso", ".cue", ".chd", ".bin", ".nrg"]

# Supported Amiga models for quick-launch
SUPPORTED_MODELS = ["A500", "A1200", "CD32"]

# Log directory for captured output
LOG_DIR = AMIBERRY_HOME / "logs"

# ROM directory
ROM_DIR = AMIBERRY_HOME / "Kickstarts" if IS_MACOS else AMIBERRY_HOME / "kickstarts"


def get_platform_info() -> dict:
    """Return platform and path information as a dictionary."""
    info = {
        "platform": _PLATFORM,
        "emulator_binary": str(EMULATOR_BINARY),
        "amiberry_home": str(AMIBERRY_HOME),
        "config_dir": str(CONFIG_DIR),
        "savestate_dir": str(SAVESTATE_DIR),
        "screenshot_dir": str(SCREENSHOT_DIR),
        "log_dir": str(LOG_DIR),
        "rom_dir": str(ROM_DIR),
        "disk_image_dirs": [str(d) for d in DISK_IMAGE_DIRS],
    }

    if IS_LINUX and SYSTEM_CONFIG_DIR:
        info["system_config_dir"] = str(SYSTEM_CONFIG_DIR)

    return info


_dirs_ensured = False


def ensure_directories_exist() -> None:
    """Create necessary directories if they don't exist.

    Logs warnings for directories that cannot be created (e.g. permission denied)
    but does not raise, so the server can still start.
    """
    global _dirs_ensured
    if _dirs_ensured:
        return
    directories = [
        CONFIG_DIR,
        SAVESTATE_DIR,
        SCREENSHOT_DIR,
        LOG_DIR,
        ROM_DIR,
    ] + DISK_IMAGE_DIRS
    for directory in directories:
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Permission denied or read-only filesystem — not fatal
            pass
    _dirs_ensured = True
