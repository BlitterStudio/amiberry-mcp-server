"""
Shared utility functions for Amiberry MCP and HTTP servers.
Extracts duplicated logic into reusable helpers.
"""

import asyncio
import datetime
import signal
import subprocess
from pathlib import Path
from typing import Any

from .config import (
    CD_EXTENSIONS,
    CONFIG_DIR,
    EMULATOR_BINARY,
    FLOPPY_EXTENSIONS,
    HARDFILE_EXTENSIONS,
    IS_LINUX,
    LHA_EXTENSIONS,
    LOG_DIR,
    SYSTEM_CONFIG_DIR,
)


def _is_path_within(path: Path, parent: Path) -> bool:
    """Check that a resolved path is within the expected parent directory."""
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def find_config_path(config_name: str) -> Path | None:
    """Find a configuration file by name, checking user and system directories."""
    config_path = (CONFIG_DIR / config_name).resolve()
    if _is_path_within(config_path, CONFIG_DIR) and config_path.exists():
        return config_path

    if IS_LINUX and SYSTEM_CONFIG_DIR:
        config_path = (SYSTEM_CONFIG_DIR / config_name).resolve()
        if _is_path_within(config_path, SYSTEM_CONFIG_DIR) and config_path.exists():
            return config_path

    return None


def classify_image_type(suffix: str) -> str:
    """Classify a disk image by its file extension."""
    suffix_lower = suffix.lower()
    if suffix_lower in FLOPPY_EXTENSIONS:
        return "floppy"
    elif suffix_lower in LHA_EXTENSIONS:
        return "lha"
    elif suffix_lower in CD_EXTENSIONS:
        return "cd"
    else:
        return "hardfile"


def get_extensions_for_type(image_type: str) -> list[str]:
    """Get file extensions for a given image type."""
    if image_type == "floppy":
        return FLOPPY_EXTENSIONS
    elif image_type == "hardfile":
        return HARDFILE_EXTENSIONS
    elif image_type == "lha":
        return LHA_EXTENSIONS
    elif image_type == "cd":
        return CD_EXTENSIONS
    else:  # all
        return FLOPPY_EXTENSIONS + HARDFILE_EXTENSIONS + LHA_EXTENSIONS + CD_EXTENSIONS


def build_launch_command(
    *,
    model: str | None = None,
    config_path: Path | None = None,
    disk_image: str | None = None,
    lha_file: str | None = None,
    cd_image: str | None = None,
    disk_swapper: list[str] | None = None,
    autostart: bool = True,
    with_logging: bool = False,
) -> list[str]:
    """Build the Amiberry launch command from components.

    Returns the command list. Callers are responsible for validating
    that paths exist before calling this function.
    """
    cmd = [EMULATOR_BINARY]

    if with_logging:
        cmd.append("--log")

    if model:
        cmd.extend(["--model", model])
    elif config_path:
        cmd.extend(["-f", str(config_path)])

    if cd_image:
        cmd.extend(["--cdimage", str(cd_image)])

    if disk_image:
        cmd.extend(["-0", str(disk_image)])

    if lha_file:
        cmd.append(str(lha_file))

    if disk_swapper:
        if not disk_image:
            cmd.extend(["-0", disk_swapper[0]])
        cmd.append(f"-diskswapper={','.join(disk_swapper)}")

    if autostart:
        cmd.append("-G")

    return cmd


def launch_process(
    cmd: list[str],
    log_path: Path | None = None,
) -> tuple[subprocess.Popen, Any]:
    """Launch Amiberry as a background process.

    Args:
        cmd: Command to execute.
        log_path: If provided, stdout is redirected to this log file.

    Returns:
        (process, log_file_or_None) — caller owns the log file handle if not None.
    """
    if log_path:
        log_file = open(log_path, "w")  # noqa: SIM115
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except Exception:
            log_file.close()
            raise
        return proc, log_file
    else:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return proc, None


def terminate_process(proc: subprocess.Popen, timeout: float = 5.0) -> None:
    """Gracefully terminate a subprocess, falling back to kill.

    Sends SIGTERM, waits up to `timeout` seconds, then sends SIGKILL
    if the process is still alive.
    """
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass


def scan_disk_images(
    search_dirs: list[Path],
    image_type: str = "all",
    search_term: str = "",
) -> list[dict[str, Any]]:
    """Scan directories for disk images, with optional type filtering and search.

    Returns a deduplicated list of image info dicts sorted by name.
    """
    extensions = get_extensions_for_type(image_type)
    ext_set = {e.lower() for e in extensions}
    search_lower = search_term.lower() if search_term else ""

    seen: set[str] = set()
    images: list[dict[str, Any]] = []

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for img_path in search_dir.rglob("*"):
            if not img_path.is_file():
                continue
            if img_path.suffix.lower() not in ext_set:
                continue
            path_str = str(img_path)
            if path_str in seen:
                continue
            if search_lower and search_lower not in img_path.name.lower():
                continue
            seen.add(path_str)
            try:
                file_size = img_path.stat().st_size
            except OSError:
                continue
            images.append(
                {
                    "name": img_path.name,
                    "path": path_str,
                    "type": classify_image_type(img_path.suffix),
                    "size": file_size,
                }
            )

    images.sort(key=lambda x: x["name"].lower())
    return images


def format_log_timestamp(mtime: float) -> str:
    """Format a file modification time as a human-readable timestamp."""
    return (
        datetime.datetime.fromtimestamp(mtime, tz=datetime.timezone.utc)
        .astimezone()
        .strftime("%Y-%m-%d %H:%M:%S")
    )


def normalize_log_path(log_name: str) -> Path:
    """Ensure a log name has a .log extension and return the full path.

    Raises:
        ValueError: If the resulting path is outside LOG_DIR (path traversal).
    """
    if not log_name.endswith(".log"):
        log_name += ".log"
    result = (LOG_DIR / log_name).resolve()
    if not _is_path_within(result, LOG_DIR):
        raise ValueError(f"Invalid log name: {log_name}")
    return result


def format_signal_info(returncode: int) -> str:
    """Format a negative return code as a signal name.

    Returns a string like ' (killed by signal SIGTERM)' or ' (killed by signal 9)'.
    Returns empty string for non-negative return codes.
    """
    if returncode >= 0:
        return ""
    try:
        sig = signal.Signals(-returncode)
        return f" (killed by signal {sig.name})"
    except ValueError:
        return f" (killed by signal {-returncode})"


async def detect_amiberry_version() -> dict[str, Any]:
    """Detect Amiberry version by running --help.

    Uses asyncio subprocess to avoid blocking the event loop.
    """
    version_info: dict[str, Any] = {
        "binary": str(EMULATOR_BINARY),
        "available": False,
    }

    try:
        proc = await asyncio.create_subprocess_exec(
            EMULATOR_BINARY,
            "--help",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
        output = (stdout or b"").decode("utf-8", errors="replace") + (
            stderr or b""
        ).decode("utf-8", errors="replace")

        version_info["available"] = True

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

    except FileNotFoundError:
        version_info["available"] = False
        version_info["error"] = f"Binary not found: {EMULATOR_BINARY}"
    except asyncio.TimeoutError:
        version_info["available"] = False
        version_info["error"] = "Timed out getting version info"
    except Exception as e:
        version_info["available"] = False
        version_info["error"] = str(e)

    return version_info
