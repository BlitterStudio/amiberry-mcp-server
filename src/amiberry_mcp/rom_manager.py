"""
ROM manager for Amiberry.
Identifies and catalogs Amiga ROM files by checksum.
"""

import hashlib
import zlib
from pathlib import Path
from typing import Any

# Known Amiga ROM database (CRC32 -> ROM info)
# These are the most common Kickstart ROMs
KNOWN_ROMS = {
    # KS ROM v23.93 (Velvet)
    "ADCB44C9": {
        "version": "23.93",
        "revision": "23.93",
        "model": "VELVET",
        "size": 131072,
    },
    # KS ROM v1.0 (A1000)(NTSC)
    "299790FF": {
        "version": "1.0",
        "revision": "1.0",
        "model": "A1000",
        "size": 262144,
    },
    # KS ROM v1.1 (A1000)(NTSC)
    "D060572A": {
        "version": "1.1",
        "revision": "31.34",
        "model": "A1000",
        "size": 262144,
    },
    # KS ROM v1.1 (A1000)(PAL)
    "EC86DAE2": {
        "version": "1.1",
        "revision": "31.34",
        "model": "A1000",
        "size": 262144,
    },
    # KS ROM v1.2 (A1000)
    "9ED783D0": {
        "version": "1.2",
        "revision": "33.166",
        "model": "A1000",
        "size": 262144,
    },
    # KS ROM v1.2 (A500,A1000,A2000)
    "A6CE1636": {
        "version": "1.2",
        "revision": "33.180",
        "model": "A500/A1000/A2000",
        "size": 262144,
    },
    # KS ROM v1.3 (A500,A1000,A2000)
    "C4F0F55F": {
        "version": "1.3",
        "revision": "34.5",
        "model": "A500/A1000/A2000",
        "size": 262144,
    },
    # KS ROM v1.3 (A3000)(SK)
    "E0F37258": {
        "version": "1.3",
        "revision": "34.5",
        "model": "A3000",
        "size": 262144,
    },
    # KS ROM v1.4 (A3000)
    "BC0EC13F": {
        "version": "1.4",
        "revision": "36.16",
        "model": "A3000",
        "size": 524288,
    },
    # KS ROM v2.04 (A500+)
    "C3BDB240": {
        "version": "2.04",
        "revision": "37.175",
        "model": "A500+",
        "size": 524288,
    },
    # KS ROM v2.05 (A600)
    "83028FB5": {
        "version": "2.05",
        "revision": "37.299",
        "model": "A600",
        "size": 524288,
    },
    # KS ROM v2.05 (A600HD)
    "64466C2A": {
        "version": "2.05",
        "revision": "37.300",
        "model": "A600HD/A600",
        "size": 524288,
    },
    # KS ROM v2.05 (A600HD)
    "43B0DF7B": {
        "version": "2.05",
        "revision": "37.350",
        "model": "A600HD/A600",
        "size": 524288,
    },
    # KS ROM v2.04 (A3000)
    "234A7233": {
        "version": "2.04",
        "revision": "37.175",
        "model": "A3000",
        "size": 524288,
    },
    # KS ROM v3.0 (A1200)
    "6C9B07D2": {
        "version": "3.0",
        "revision": "39.106",
        "model": "A1200",
        "size": 524288,
    },
    # KS ROM v3.0 (A4000)
    "9E6AC152": {
        "version": "3.0",
        "revision": "39.106",
        "model": "A4000",
        "size": 524288,
    },
    # KS ROM v3.1 (A4000)
    "2B4566F1": {
        "version": "3.1",
        "revision": "40.70",
        "model": "A4000",
        "size": 524288,
    },
    # KS ROM v3.1 (A500,A600,A2000)
    "FC24AE0D": {
        "version": "3.1",
        "revision": "40.63",
        "model": "A500/A600/A2000",
        "size": 524288,
    },
    # KS ROM v3.1 (A1200)
    "1483A091": {
        "version": "3.1",
        "revision": "40.68",
        "model": "A1200",
        "size": 524288,
    },
    # KS ROM v3.1 (A3000)
    "EFB239CC": {
        "version": "3.1",
        "revision": "40.68",
        "model": "A3000",
        "size": 524288,
    },
    # KS ROM v3.1 (A4000)(Cloanto)
    "43B6DD22": {
        "version": "3.1",
        "revision": "40.68",
        "model": "A4000",
        "size": 524288,
    },
    # KS ROM v3.1 (A4000)
    "D6BAE334": {
        "version": "3.1",
        "revision": "40.68",
        "model": "A4000",
        "size": 524288,
    },
    # KS ROM v3.1 (A4000T)
    "75932C3A": {
        "version": "3.1",
        "revision": "40.70",
        "model": "A4000T",
        "size": 524288,
    },
    # KS ROM v3.X (A4000)(Cloanto)
    "3AC99EDC": {
        "version": "3.10",
        "revision": "45.57",
        "model": "A4000",
        "size": 524288,
    },
    # KS ROM v3.1.4-1 (A1200)
    "F17FA97F": {
        "version": "3.1.4",
        "revision": "46.143",
        "model": "A1200",
        "size": 524288,
    },
    # KS ROM v3.1.4-1 (A3000)
    "50C3529C": {
        "version": "3.1.4",
        "revision": "46.143",
        "model": "A3000",
        "size": 524288,
    },
    # KS ROM v3.1.4-1 (A4000)
    "D47E18FD": {
        "version": "3.1.4",
        "revision": "46.143",
        "model": "A4000",
        "size": 524288,
    },
    # KS ROM v3.1.4-1 (A4000T)
    "75A2B2A5": {
        "version": "3.1.4",
        "revision": "46.143",
        "model": "A4000T",
        "size": 524288,
    },
    # KS ROM v3.1.4-1 (A500)
    "D52B52FD": {
        "version": "3.1.4",
        "revision": "46.143",
        "model": "A500",
        "size": 524288,
    },
    # KS ROM v3.1.4-2 (A1200)
    "B87506A7": {
        "version": "3.1.4",
        "revision": "46.143",
        "model": "A1200",
        "size": 524288,
    },
    # KS ROM v3.1.4-2 (A3000)
    "BA35F8EB": {
        "version": "3.1.4",
        "revision": "46.143",
        "model": "A3000",
        "size": 524288,
    },
    # KS ROM v3.1.4-2 (A4000)
    "1B84CB33": {
        "version": "3.1.4",
        "revision": "46.143",
        "model": "A4000",
        "size": 524288,
    },
    # KS ROM v3.1.4-2 (A4000T)
    "D6D0EF3E": {
        "version": "3.1.4",
        "revision": "46.143",
        "model": "A4000T",
        "size": 524288,
    },
    # KS ROM v3.1.4-2 (A500)
    "568F8786": {
        "version": "3.1.4",
        "revision": "46.143",
        "model": "A500",
        "size": 524288,
    },
    # KS ROM v3.2 (A1200)
    "BD1FF75E": {
        "version": "3.2",
        "revision": "47.96",
        "model": "A1200",
        "size": 524288,
    },
    # KS ROM v3.2 (A3000)
    "F3AF46CC": {
        "version": "3.2",
        "revision": "47.96",
        "model": "A3000",
        "size": 524288,
    },
    # KS ROM v3.2 (A4000)
    "9BB8FC93": {
        "version": "3.2",
        "revision": "47.96",
        "model": "A4000",
        "size": 524288,
    },
    # KS ROM v3.2 (A4000T)
    "9188A509": {
        "version": "3.2",
        "revision": "47.96",
        "model": "A4000T",
        "size": 524288,
    },
    # KS ROM v3.2 (A500/A600/A2000/A1000/CDTV)
    "8173D7B6": {
        "version": "3.2",
        "revision": "47.96",
        "model": "A500",
        "size": 524288,
    },
    # KS ROM v3.2.1 (A1200)
    "2B653371": {
        "version": "3.2.1",
        "revision": "47.102",
        "model": "A1200",
        "size": 524288,
    },
    # KS ROM v3.2.1 (A3000)
    "0078F607": {
        "version": "3.2.1",
        "revision": "47.102",
        "model": "A3000",
        "size": 524288,
    },
    # KS ROM v3.2.1 (A4000)
    "F3CED3B8": {
        "version": "3.2.1",
        "revision": "47.102",
        "model": "A4000",
        "size": 524288,
    },
    # KS ROM v3.2.1 (A4000T)
    "AF3452EC": {
        "version": "3.2.1",
        "revision": "47.102",
        "model": "A4000T",
        "size": 524288,
    },
    # KS ROM v3.2.1 (A500/A600/A2000/A1000/CDTV)
    "4F078456": {
        "version": "3.2.1",
        "revision": "47.102",
        "model": "A500",
        "size": 524288,
    },
    # KS ROM v3.2.2 (A1200)
    "5C40328A": {
        "version": "3.2.2",
        "revision": "47.111",
        "model": "A1200",
        "size": 524288,
    },
    # KS ROM v3.2.2 (A3000)
    "46335B57": {
        "version": "3.2.2",
        "revision": "47.111",
        "model": "A3000",
        "size": 524288,
    },
    # KS ROM v3.2.2 (A4000)
    "4BEA9798": {
        "version": "3.2.2",
        "revision": "47.111",
        "model": "A4000",
        "size": 524288,
    },
    # KS ROM v3.2.2 (A4000T)
    "36BBCD8A": {
        "version": "3.2.2",
        "revision": "47.111",
        "model": "A4000T",
        "size": 524288,
    },
    # KS ROM v3.2.2 (A500/A600/A2000/A1000/CDTV)
    "E4458462": {
        "version": "3.2.2",
        "revision": "47.111",
        "model": "A500",
        "size": 524288,
    },
    # KS ROM v3.2.3 (A1200)
    "B18D3B67": {
        "version": "3.2.3",
        "revision": "47.115",
        "model": "A1200",
        "size": 524288,
    },
    # KS ROM v3.2.3 (A3000)
    "74C0B23F": {
        "version": "3.2.3",
        "revision": "47.115",
        "model": "A3000",
        "size": 524288,
    },
    # KS ROM v3.2.3 (A4000)
    "B6A4698E": {
        "version": "3.2.3",
        "revision": "47.115",
        "model": "A4000",
        "size": 524288,
    },
    # KS ROM v3.2.3 (A4000T)
    "588A5E6D": {
        "version": "3.2.3",
        "revision": "47.115",
        "model": "A4000T",
        "size": 524288,
    },
    # KS ROM v3.2.3 (A500/A600/A2000/A1000/CDTV)
    "E1F50B0B": {
        "version": "3.2.3",
        "revision": "47.115",
        "model": "A500",
        "size": 524288,
    },
    # KS ROM v3.2 (Walker)
    "261339F8": {
        "version": "3.2",
        "revision": "43.1",
        "model": "WALKER",
        "size": 524288,
    },
    # Casablanca 74095 - 00 717 - 02
    "2EC384E3": {
        "version": "3.1",
        "revision": "0.0",
        "model": "CASABLANCA",
        "size": 1048576,
    },
    # CD32 KS ROM v3.1
    "1E62D4A5": {
        "version": "3.1",
        "revision": "40.60",
        "model": "CD32",
        "size": 524288,
    },
    # CD32 extended ROM
    "87746BE2": {
        "version": "3.1",
        "revision": "40.60",
        "model": "CD32",
        "size": 524288,
    },
    # CD32 ROM (KS + extended)
    "F5D4F3C8": {
        "version": "3.1",
        "revision": "40.60",
        "model": "CD32",
        "size": 1048576,
    },
    # CDTV extended ROM v1.0
    "42BAA124": {
        "version": "1.0",
        "revision": "1.0",
        "model": "CDTV",
        "size": 262144,
    },
    # CDTV extended ROM v2.7
    "CEAE68D2": {
        "version": "2.7",
        "revision": "2.7",
        "model": "CDTV",
        "size": 262144,
    },
    # CDTV/A570 extended ROM v2.30
    "30B54232": {
        "version": "2.30",
        "revision": "2.30",
        "model": "CDTV",
        "size": 262144,
    },
    # CDTV extended ROM v47.1
    "AB6274E7": {
        "version": "47.1",
        "revision": "47.96",
        "model": "CDTV",
        "size": 262144,
    },
    # CDTV-CR extended ROM v3.32
    "581A85CF": {
        "version": "3.32",
        "revision": "3.32",
        "model": "CDTVCR",
        "size": 262144,
    },
    # CDTV-CR extended ROM v3.44
    "0B7BD64F": {
        "version": "3.44",
        "revision": "3.44",
        "model": "CDTVCR",
        "size": 262144,
    },
    # A1000 bootstrap ROM
    "0B1AD2D0": {
        "version": "0.0",
        "revision": "0.0",
        "model": "A1000",
        "size": 65536,
    },
    # The Diagnostic 2.0 (Logica)
    "8484F426": {
        "version": "2.0",
        "revision": "2.0",
        "model": "LOGICA",
        "size": 524288,
    },
}

# ROM file extensions
ROM_EXTENSIONS = [".rom", ".bin", ".a500", ".a600", ".a1200", ".a4000"]


# Maximum ROM file size to read (16MB - well above any real Amiga ROM)
_MAX_ROM_SIZE = 16 * 1024 * 1024


def calculate_rom_crc32(path: Path) -> str:
    """
    Calculate the CRC32 checksum of a ROM file.

    Args:
        path: Path to the ROM file

    Returns:
        CRC32 checksum as uppercase hex string

    Raises:
        ValueError: If the file exceeds the maximum allowed size
    """
    if path.stat().st_size > _MAX_ROM_SIZE:
        raise ValueError(
            f"ROM file too large ({path.stat().st_size} bytes, max {_MAX_ROM_SIZE})"
        )
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

    Raises:
        ValueError: If the file exceeds the maximum allowed size
    """
    if path.stat().st_size > _MAX_ROM_SIZE:
        raise ValueError(
            f"ROM file too large ({path.stat().st_size} bytes, max {_MAX_ROM_SIZE})"
        )
    data = path.read_bytes()
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


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
    if file_size > _MAX_ROM_SIZE:
        raise ValueError(f"ROM file too large ({file_size} bytes, max {_MAX_ROM_SIZE})")

    data = path.read_bytes()
    file_size = len(data)
    crc32 = f"{zlib.crc32(data) & 0xFFFFFFFF:08X}"
    md5 = hashlib.md5(data, usedforsecurity=False).hexdigest()

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

    ext_set = {e.lower() for e in ROM_EXTENSIONS}
    roms = []
    seen: set[str] = set()
    glob_pattern = "**/*" if recursive else "*"

    for rom_path in directory.glob(glob_pattern):
        if not rom_path.is_file():
            continue
        if rom_path.suffix.lower() not in ext_set:
            continue
        path_str = str(rom_path)
        if path_str in seen:
            continue
        seen.add(path_str)
        try:
            rom_info = identify_rom(rom_path)
            roms.append(rom_info)
        except Exception as e:
            roms.append(
                {
                    "file": path_str,
                    "filename": rom_path.name,
                    "error": str(e),
                }
            )

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

    # First pass: exact model match (split on "/" for multi-model ROM entries)
    for rom in roms:
        if rom.get("identified"):
            rom_models = {m.strip().upper() for m in rom.get("model", "").split("/")}
            if model_upper in rom_models:
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
                if rom.get("identified"):
                    rom_models = {
                        m.strip().upper() for m in rom.get("model", "").split("/")
                    }
                    if compat_model in rom_models:
                        return rom

    return None
