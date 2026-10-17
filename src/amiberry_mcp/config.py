"""
Shared configuration for Amiberry MCP Server.
Platform detection and path configuration used by both MCP and HTTP servers.
"""

import os
import platform
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# Platform detection - cache the result to avoid repeated system calls
_PLATFORM = platform.system()
IS_MACOS = _PLATFORM == "Darwin"
IS_LINUX = _PLATFORM == "Linux"
IS_FREEBSD = _PLATFORM == "FreeBSD"
IS_WINDOWS = _PLATFORM == "Windows"
IS_POSIX = IS_LINUX or IS_FREEBSD

# Official --model names from Amiberry's usage text (main.cpp).
SUPPORTED_MODELS = [
    "A1000",
    "A500",
    "A500P",
    "A600",
    "A2000",
    "A3000",
    "A1200",
    "A4000",
    "CD32",
    "CDTV",
]

# Supported file extensions
FLOPPY_EXTENSIONS = [".adf", ".adz", ".dms"]
HARDFILE_EXTENSIONS = [".hdf", ".hdz"]
LHA_EXTENSIONS = [".lha"]
CD_EXTENSIONS = [".iso", ".cue", ".chd", ".bin", ".nrg"]

# Where the emulator executable lives when no override is set. Windows
# resolves the real path below by searching PATH, the registry, and the
# default install directories; the other platforms keep their conventional
# locations.
MACOS_EMULATOR_BINARY = "/Applications/Amiberry.app/Contents/MacOS/Amiberry"
WINDOWS_EMULATOR_BINARY = "Amiberry.exe"
POSIX_EMULATOR_BINARY = "amiberry"

# Environment variable that overrides every binary discovery step, on any
# platform. Useful for portable installs and development builds.
EMULATOR_BINARY_OVERRIDE_ENV = "AMIBERRY_BINARY"

# Uninstall key the Inno Setup installer writes. HKCU holds the per-user
# install, HKLM the all-users one; neither adds Amiberry to PATH.
_WINDOWS_UNINSTALL_KEY = (
    "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\amiberry_is1"
)


def _windows_registry_binary() -> Path | None:
    """Locate Amiberry.exe through the Inno Setup uninstall key.

    ``InstallLocation`` is the directory the installer used, so it is the
    only reliable pointer to an installed copy. HKCU (per-user install) is
    checked before HKLM (all-users install).
    """
    try:
        import winreg
    except ImportError:
        return None
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, _WINDOWS_UNINSTALL_KEY) as key:
                location, _ = winreg.QueryValueEx(key, "InstallLocation")
        except OSError:
            continue
        if not location:
            continue
        binary = Path(location) / "Amiberry.exe"
        if binary.is_file():
            return binary
    return None


def _windows_binary(environ: Mapping[str, str]) -> str:
    """Find Amiberry.exe: PATH, the installer, then default install dirs."""
    found = shutil.which(WINDOWS_EMULATOR_BINARY)
    if found:
        return found
    registry = _windows_registry_binary()
    if registry is not None:
        return str(registry)
    candidates = []
    local_app_data = _nonempty(environ, "LOCALAPPDATA")
    if local_app_data:
        candidates.append(
            Path(local_app_data) / "Programs" / "amiberry" / WINDOWS_EMULATOR_BINARY
        )
    program_files = _nonempty(environ, "ProgramFiles")
    if program_files:
        candidates.append(Path(program_files) / "amiberry" / WINDOWS_EMULATOR_BINARY)
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return WINDOWS_EMULATOR_BINARY


def resolve_emulator_binary(platform_name: str, environ: Mapping[str, str]) -> str:
    """Resolve the Amiberry executable to launch.

    ``AMIBERRY_BINARY`` wins over every discovery step when set to a
    non-empty value. Otherwise macOS uses the app bundle, Windows searches
    ``PATH``, the Inno Setup uninstall key (HKCU, then HKLM), and the
    default install directories, and every other platform uses ``amiberry``
    from ``PATH``. The bare name is kept when nothing is found, so
    subprocesses still search ``PATH`` at exec time and error messages name
    the binary.
    """
    override = _nonempty(environ, EMULATOR_BINARY_OVERRIDE_ENV)
    if override:
        return override
    if platform_name == "Darwin":
        return MACOS_EMULATOR_BINARY
    if platform_name == "Windows":
        return _windows_binary(environ)
    found = shutil.which(POSIX_EMULATOR_BINARY)
    return found if found else POSIX_EMULATOR_BINARY


@dataclass(frozen=True)
class AmiberryPaths:
    """Directories Amiberry 8.4 uses for one platform and environment."""

    home: Path
    config_dir: Path
    legacy_config_dirs: tuple[Path, ...]
    settings_dir: Path
    savestate_dir: Path
    screenshot_dir: Path
    rom_dir: Path
    cd_dir: Path
    disk_image_dirs: tuple[Path, ...]
    legacy_rom_dirs: tuple[Path, ...]
    legacy_savestate_dirs: tuple[Path, ...]
    legacy_disk_dirs: tuple[Path, ...]
    portable: bool
    portable_root: Path | None


def _nonempty(environ: Mapping[str, str], name: str) -> str | None:
    """Return an environment value, treating an empty string as unset."""
    value = environ.get(name)
    if value:
        return value
    return None


# Resolved once at import; every consumer (launch, version probe, /status
# image name, portable-marker detection, get_platform_info) uses this.
EMULATOR_BINARY = resolve_emulator_binary(_PLATFORM, os.environ)


def _legacy_content_dirs(
    content_root: Path,
) -> tuple[tuple[Path, ...], tuple[Path, ...], tuple[Path, ...]]:
    """Pre-8.4 content directories this server used or Amiberry still migrates.

    Amiberry rewrites ``roms``/``Roms``, ``savestates``/``Savestates``,
    ``floppies``, ``harddrives``/``Harddrives``, and ``lha``/``Lha`` into the
    title-case names. It does not move ``kickstarts``/``Kickstarts``, which
    this server used as the ROM directory before 8.4.
    """
    roms = (
        content_root / "kickstarts",
        content_root / "Kickstarts",
        content_root / "roms",
        content_root / "Roms",
    )
    savestates = (
        content_root / "savestates",
        content_root / "Savestates",
    )
    disks = (
        content_root / "floppies",
        content_root / "harddrives",
        content_root / "Harddrives",
        content_root / "lha",
        content_root / "Lha",
    )
    return roms, savestates, disks


def _windows_settings_dir(
    environ: Mapping[str, str], home: Path, portable_root: Path | None
) -> Path:
    """Resolve the Windows settings directory the way Amiberry does.

    Order: ``%LOCALAPPDATA%\\Amiberry``, then ``%APPDATA%\\Amiberry``, then
    ``<home>\\AppData\\Local\\Amiberry``, then ``<portable root>\\Settings``.
    """
    local_app_data = _nonempty(environ, "LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Amiberry"
    roaming_app_data = _nonempty(environ, "APPDATA")
    if roaming_app_data:
        return Path(roaming_app_data) / "Amiberry"
    if portable_root is not None:
        return portable_root / "Settings"
    return home / "AppData" / "Local" / "Amiberry"


def portable_marker_root(platform_name: str, emulator_path: Path | None) -> Path | None:
    """Return the executable directory when portable mode is active.

    Amiberry enables portable mode when ``amiberry.portable`` sits next to
    the executable. macOS ignores that marker.
    """
    if platform_name == "Darwin" or emulator_path is None:
        return None
    root = emulator_path.resolve().parent
    if (root / "amiberry.portable").is_file():
        return root
    return None


def resolve_amiberry_paths(
    platform_name: str,
    environ: Mapping[str, str],
    home: Path,
    emulator_path: Path | None = None,
) -> AmiberryPaths:
    """Resolve content, config, and settings directories the way Amiberry does.

    POSIX (Linux and FreeBSD) order for the content root is portable marker,
    then ``AMIBERRY_HOME_DIR``, then ``$HOME/Amiberry``. Configurations follow
    the portable root, else ``AMIBERRY_CONFIG_DIR``, else
    ``<content>/Configurations``. Settings live in ``$XDG_CONFIG_HOME/amiberry``
    (default ``~/.config/amiberry``) and are not a ``.uae`` directory.

    macOS uses ``AMIBERRY_HOME_DIR`` or ``~/Documents/Amiberry`` for content
    and ``~/Library/Application Support/Amiberry`` for settings. It does not
    honor ``AMIBERRY_CONFIG_DIR`` or the portable marker.

    Windows uses the portable marker, then ``AMIBERRY_HOME_DIR``, then
    ``%USERPROFILE%\\Amiberry`` for content. Configurations always follow the
    content root and ``AMIBERRY_CONFIG_DIR`` is not honored. Settings live in
    ``%LOCALAPPDATA%\\Amiberry``; see ``_windows_settings_dir`` for the full
    fallback order.
    """
    if platform_name not in ("Darwin", "Linux", "FreeBSD", "Windows"):
        raise RuntimeError(f"Unsupported platform: {platform_name}")

    portable_root = portable_marker_root(platform_name, emulator_path)
    if platform_name == "Windows":
        env_home = _nonempty(environ, "AMIBERRY_HOME_DIR")
        if portable_root is not None:
            content_root = portable_root
        elif env_home:
            content_root = Path(env_home)
        else:
            content_root = home / "Amiberry"
        config_dir = content_root / "Configurations"
        settings_dir = _windows_settings_dir(environ, home, portable_root)
        legacy = (content_root / "conf",)
        legacy_roms, legacy_saves, legacy_disks = _legacy_content_dirs(content_root)
    elif portable_root is not None:
        content_root = portable_root
        config_dir = portable_root / "Configurations"
        settings_dir = portable_root / "Settings"
        legacy = (portable_root / "conf",)
        legacy_roms, legacy_saves, legacy_disks = _legacy_content_dirs(content_root)
    elif platform_name == "Darwin":
        env_home = _nonempty(environ, "AMIBERRY_HOME_DIR")
        content_root = Path(env_home) if env_home else home / "Documents" / "Amiberry"
        config_dir = content_root / "Configurations"
        settings_dir = home / "Library" / "Application Support" / "Amiberry"
        legacy = ()
        legacy_roms, legacy_saves, legacy_disks = _legacy_content_dirs(content_root)
    else:
        env_home = _nonempty(environ, "AMIBERRY_HOME_DIR")
        env_config = _nonempty(environ, "AMIBERRY_CONFIG_DIR")
        content_root = Path(env_home) if env_home else home / "Amiberry"
        config_dir = Path(env_config) if env_config else content_root / "Configurations"
        xdg_config = _nonempty(environ, "XDG_CONFIG_HOME")
        settings_dir = (
            Path(xdg_config) if xdg_config else home / ".config"
        ) / "amiberry"
        legacy = (content_root / "conf",)
        legacy_roms, legacy_saves, legacy_disks = _legacy_content_dirs(content_root)

    return AmiberryPaths(
        home=content_root,
        config_dir=config_dir,
        legacy_config_dirs=legacy,
        settings_dir=settings_dir,
        savestate_dir=content_root / "SaveStates",
        screenshot_dir=content_root / "Screenshots",
        rom_dir=content_root / "ROMs",
        cd_dir=content_root / "CDROMs",
        disk_image_dirs=(
            content_root / "Floppies",
            content_root / "HardDrives",
            content_root / "LHA",
        ),
        legacy_rom_dirs=legacy_roms,
        legacy_savestate_dirs=legacy_saves,
        legacy_disk_dirs=legacy_disks,
        portable=portable_root is not None,
        portable_root=portable_root,
    )


def _resolved_emulator_path() -> Path | None:
    """Locate the emulator binary used for portable-marker detection."""
    if os.path.isabs(EMULATOR_BINARY):
        path = Path(EMULATOR_BINARY)
        return path if path.exists() else None
    found = shutil.which(EMULATOR_BINARY)
    return Path(found) if found else None


if _PLATFORM not in ("Darwin", "Linux", "FreeBSD", "Windows"):
    raise RuntimeError(f"Unsupported platform: {_PLATFORM}")

_PATHS = resolve_amiberry_paths(
    _PLATFORM, os.environ, Path.home(), _resolved_emulator_path()
)

AMIBERRY_HOME = _PATHS.home
CONFIG_DIR = _PATHS.config_dir
LEGACY_CONFIG_DIRS = list(_PATHS.legacy_config_dirs)
SETTINGS_DIR = _PATHS.settings_dir
SAVESTATE_DIR = _PATHS.savestate_dir
SCREENSHOT_DIR = _PATHS.screenshot_dir
ROM_DIR = _PATHS.rom_dir
CD_DIR = _PATHS.cd_dir
DISK_IMAGE_DIRS = list(_PATHS.disk_image_dirs)
LEGACY_ROM_DIRS = list(_PATHS.legacy_rom_dirs)
LEGACY_SAVESTATE_DIRS = list(_PATHS.legacy_savestate_dirs)
LEGACY_DISK_DIRS = list(_PATHS.legacy_disk_dirs)
PORTABLE_MODE = _PATHS.portable

# Captured launch logs owned by this server, not Amiberry's Amiberry.log.
LOG_DIR = AMIBERRY_HOME / "logs"


def get_platform_info() -> dict:
    """Return platform and path information as a dictionary."""
    info = {
        "platform": _PLATFORM,
        "emulator_binary": str(EMULATOR_BINARY),
        "amiberry_home": str(AMIBERRY_HOME),
        "config_dir": str(CONFIG_DIR),
        "settings_dir": str(SETTINGS_DIR),
        "savestate_dir": str(SAVESTATE_DIR),
        "screenshot_dir": str(SCREENSHOT_DIR),
        "log_dir": str(LOG_DIR),
        "rom_dir": str(ROM_DIR),
        "cd_dir": str(CD_DIR),
        "disk_image_dirs": [str(d) for d in DISK_IMAGE_DIRS],
        "portable_mode": PORTABLE_MODE,
    }
    if _PATHS.portable_root is not None:
        info["portable_root"] = str(_PATHS.portable_root)
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
        CD_DIR,
    ] + DISK_IMAGE_DIRS
    for directory in directories:
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Permission denied or read-only filesystem — not fatal
            pass
    _dirs_ensured = True
