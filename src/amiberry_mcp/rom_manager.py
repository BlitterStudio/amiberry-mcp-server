"""
ROM manager for Amiberry.
Identifies and catalogs Amiga ROM files by checksum.
"""

import hashlib
from pathlib import Path
from typing import Any

# Known Amiga ROM database (CRC32 -> ROM info)
# These are the most common Kickstart ROMs
KNOWN_ROMS = {
    # Kickstart 1.2
    "A6CE1636": {"version": "1.2", "revision": "33.180", "model": "A500/A1000/A2000", "size": 262144},
    # Kickstart 1.3
    "C4F0F55F": {"version": "1.3", "revision": "34.5", "model": "A500/A1000/A2000", "size": 262144},
    "E40A5DFB": {"version": "1.3", "revision": "34.5", "model": "A500/A1000/A2000 (alt)", "size": 262144},
    # Kickstart 2.04
    "C3BDB240": {"version": "2.04", "revision": "37.175", "model": "A500+", "size": 524288},
    # Kickstart 2.05
    "83028FB5": {"version": "2.05", "revision": "37.299", "model": "A600", "size": 524288},
    "64466C2A": {"version": "2.05", "revision": "37.300", "model": "A600HD", "size": 524288},
    "43B0DF7B": {"version": "2.05", "revision": "37.350", "model": "A600HD", "size": 524288},
    # Kickstart 3.0
    "6C9B07D2": {"version": "3.0", "revision": "39.106", "model": "A1200", "size": 524288},
    "FC24AE0D": {"version": "3.0", "revision": "39.106", "model": "A4000", "size": 524288},
    # Kickstart 3.1
    "1483A091": {"version": "3.1", "revision": "40.63", "model": "A500/A600/A2000", "size": 524288},
    "D6BAE334": {"version": "3.1", "revision": "40.68", "model": "A1200", "size": 524288},
    "B7CC148B": {"version": "3.1", "revision": "40.68", "model": "A3000", "size": 524288},
    "2B4566F1": {"version": "3.1", "revision": "40.70", "model": "A4000", "size": 524288},
    "9E6AC152": {"version": "3.1", "revision": "40.70", "model": "A4000T", "size": 524288},
    # Kickstart 3.1.4
    "AFE0A9C3": {"version": "3.1.4", "revision": "46.143", "model": "A500/A600/A2000", "size": 524288},
    "D52B52FD": {"version": "3.1.4", "revision": "46.143", "model": "A1200", "size": 524288},
    "FCA4B7E2": {"version": "3.1.4", "revision": "46.143", "model": "A3000", "size": 524288},
    "C3C48116": {"version": "3.1.4", "revision": "46.143", "model": "A4000/A4000T", "size": 524288},
    # Kickstart 3.2
    "C96B41EA": {"version": "3.2", "revision": "47.96", "model": "A500/A600/A2000", "size": 524288},
    "26D37C36": {"version": "3.2", "revision": "47.96", "model": "A1200", "size": 524288},
    "F2BA9D52": {"version": "3.2", "revision": "47.96", "model": "A3000", "size": 524288},
    "5BB85713": {"version": "3.2", "revision": "47.96", "model": "A4000/A4000T", "size": 524288},
    # CD32 / CDTV
    "1E5C4FE2": {"version": "3.1", "revision": "40.60", "model": "CD32", "size": 524288},
    "3525BE88": {"version": "CD32", "revision": "ext", "model": "CD32 Extended", "size": 524288},
    "8D28A7D9": {"version": "1.0", "revision": "1.0", "model": "CDTV", "size": 262144},
    "7BA40FFA": {"version": "2.30", "revision": "2.30", "model": "CDTV Extended", "size": 262144},
    # AROS
    "E4FED7D0": {"version": "AROS", "revision": "ROM", "model": "AROS Kickstart replacement", "size": 524288},
}

# ROM file extensions
ROM_EXTENSIONS = [".rom", ".bin", ".adf", ".a500", ".a600", ".a1200", ".a4000"]


def calculate_rom_crc32(path: Path) -> str:
    """
    Calculate the CRC32 checksum of a ROM file.

    Args:
        path: Path to the ROM file

    Returns:
        CRC32 checksum as uppercase hex string
    """
    import zlib

    data = path.read_bytes()
    crc = zlib.crc32(data) & 0xFFFFFFFF
    return f"{crc:08X}"


def calculate_rom_md5(path: Path) -> str:
    """
    Calculate the MD5 hash of a ROM file.

    Args:
        path: Path to the ROM file

    Returns:
        MD5 hash as lowercase hex string
    """
    data = path.read_bytes()
    return hashlib.md5(data).hexdigest()


def identify_rom(path: Path) -> dict[str, Any]:
    """
    Identify a ROM file by its checksum.

    Args:
        path: Path to the ROM file

    Returns:
        Dictionary with ROM information
    """
    if not path.exists():
        raise FileNotFoundError(f"ROM file not found: {path}")

    file_size = path.stat().st_size
    crc32 = calculate_rom_crc32(path)
    md5 = calculate_rom_md5(path)

    result: dict[str, Any] = {
        "file": str(path),
        "filename": path.name,
        "size": file_size,
        "crc32": crc32,
        "md5": md5,
    }

    # Try to identify from known ROMs
    if crc32 in KNOWN_ROMS:
        known = KNOWN_ROMS[crc32]
        result["identified"] = True
        result["version"] = known["version"]
        result["revision"] = known["revision"]
        result["model"] = known["model"]
    else:
        result["identified"] = False
        # Try to guess from file size
        if file_size == 262144:
            result["probable_type"] = "Kickstart 1.x (256KB)"
        elif file_size == 524288:
            result["probable_type"] = "Kickstart 2.x/3.x (512KB)"
        elif file_size == 1048576:
            result["probable_type"] = "Extended ROM or combined ROM (1MB)"
        else:
            result["probable_type"] = "Unknown"

    return result


def scan_rom_directory(directory: Path, recursive: bool = True) -> list[dict[str, Any]]:
    """
    Scan a directory for ROM files and identify them.

    Args:
        directory: Directory to scan
        recursive: Whether to scan subdirectories

    Returns:
        List of ROM information dictionaries
    """
    if not directory.exists():
        return []

    roms = []
    pattern = "**/*" if recursive else "*"

    for ext in ROM_EXTENSIONS:
        for rom_path in directory.glob(f"{pattern}{ext}"):
            if rom_path.is_file():
                try:
                    rom_info = identify_rom(rom_path)
                    roms.append(rom_info)
                except Exception as e:
                    roms.append({
                        "file": str(rom_path),
                        "filename": rom_path.name,
                        "error": str(e),
                    })

        # Also check uppercase extensions
        for rom_path in directory.glob(f"{pattern}{ext.upper()}"):
            if rom_path.is_file():
                # Skip if already found (case-insensitive filesystem)
                if any(r.get("file") == str(rom_path) for r in roms):
                    continue
                try:
                    rom_info = identify_rom(rom_path)
                    roms.append(rom_info)
                except Exception as e:
                    roms.append({
                        "file": str(rom_path),
                        "filename": rom_path.name,
                        "error": str(e),
                    })

    return roms


def get_rom_summary(rom_info: dict[str, Any]) -> str:
    """
    Generate a human-readable summary of a ROM.

    Args:
        rom_info: ROM information dictionary from identify_rom()

    Returns:
        Formatted summary string
    """
    lines = [f"ROM: {rom_info.get('filename', 'Unknown')}"]

    if rom_info.get("identified"):
        lines.append(f"Kickstart {rom_info['version']} (Rev {rom_info['revision']})")
        lines.append(f"Model: {rom_info['model']}")
    elif rom_info.get("probable_type"):
        lines.append(f"Type: {rom_info['probable_type']} (unidentified)")

    size_kb = rom_info.get("size", 0) // 1024
    lines.append(f"Size: {size_kb} KB")
    lines.append(f"CRC32: {rom_info.get('crc32', 'Unknown')}")

    return "\n".join(lines)


def find_rom_for_model(roms: list[dict[str, Any]], model: str) -> dict[str, Any] | None:
    """
    Find the best ROM for a specific Amiga model from a list of ROMs.

    Args:
        roms: List of ROM info dictionaries
        model: Amiga model (e.g., "A500", "A1200", "CD32")

    Returns:
        Best matching ROM info, or None if not found
    """
    model_upper = model.upper()

    # First pass: exact model match
    for rom in roms:
        if rom.get("identified") and model_upper in rom.get("model", "").upper():
            return rom

    # Second pass: compatible models
    compatible = {
        "A500": ["A500", "A1000", "A2000"],
        "A500+": ["A500+", "A500"],
        "A600": ["A600", "A500+"],
        "A1200": ["A1200"],
        "A3000": ["A3000", "A4000"],
        "A4000": ["A4000", "A4000T", "A3000"],
        "CD32": ["CD32"],
        "CDTV": ["CDTV"],
    }

    if model_upper in compatible:
        for compat_model in compatible[model_upper]:
            for rom in roms:
                if rom.get("identified") and compat_model in rom.get("model", "").upper():
                    return rom

    return None
