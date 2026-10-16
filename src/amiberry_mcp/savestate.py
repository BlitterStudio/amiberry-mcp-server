"""
Savestate (.uss) file parser for Amiberry.
Reads metadata from Amiga savestate files without loading the full state.
"""

import struct
from pathlib import Path
from typing import Any

# Amiberry writes UAE IFF chunks: 4-byte name, 4-byte size, 4-byte flags,
# then payload. The size includes those 12 header bytes and excludes the
# 1-4 byte alignment padding that follows the payload. END is the older
# 8-byte marker (name + size 8) with no flags.
ASF_MAGIC = b"ASF "
CHUNK_HEADER_SIZE = 12
END_CHUNK_SIZE = 8

# Pre-compiled struct formats to avoid repeated format string parsing
_STRUCT_U32_BE = struct.Struct(">I")
_STRUCT_U16_BE = struct.Struct(">H")


# Maximum savestate file size to read (256MB - generous limit for any Amiga config)
_MAX_SAVESTATE_SIZE = 256 * 1024 * 1024


def _read_u32_be(data: bytes, offset: int) -> int:
    """Read a big-endian 32-bit unsigned integer."""
    return _STRUCT_U32_BE.unpack_from(data, offset)[0]


def _read_u16_be(data: bytes, offset: int) -> int:
    """Read a big-endian 16-bit unsigned integer."""
    return _STRUCT_U16_BE.unpack_from(data, offset)[0]


def _read_string(data: bytes, offset: int) -> tuple[str, int]:
    """Read a null-terminated string, return (string, bytes_consumed)."""
    end = data.find(b"\x00", offset)
    if end == -1:
        return "", 0
    string = data[offset:end].decode("latin-1", errors="replace")
    return string, end - offset + 1


def _chunk_padding(payload_len: int) -> int:
    """Return the 1-4 alignment bytes Amiberry writes after a payload."""
    return 4 - (payload_len & 3)


def _decode_chunk_payload(
    data: bytes, start: int, chunk_size: int
) -> tuple[bytes, int]:
    """Return the payload and the on-disk payload length used for padding.

    ``start`` is the offset of the chunk name. Compressed chunks (flags bit 0)
    store an uncompressed length and a zlib payload.
    """
    flags = _read_u32_be(data, start + 8)
    body_len = chunk_size - CHUNK_HEADER_SIZE
    body = data[start + CHUNK_HEADER_SIZE : start + chunk_size]
    if flags & 1:
        if len(body) < 4:
            return b"", 0
        import zlib

        stored = body[4:]
        try:
            payload = zlib.decompress(stored)
        except zlib.error:
            payload = b""
        return payload, len(stored)
    return body[:body_len], body_len


def _parse_asf_payload(payload: bytes) -> dict[str, Any]:
    """Parse the ASF payload: version and three null-terminated strings."""
    metadata: dict[str, Any] = {}
    if len(payload) < 4:
        return metadata
    metadata["version"] = _read_u32_be(payload, 0)
    offset = 4
    for key in ("emulator", "emulator_version", "description"):
        if offset >= len(payload):
            break
        text, consumed = _read_string(payload, offset)
        if consumed == 0:
            break
        metadata[key] = text
        offset += consumed
    return metadata


def _parse_header(data: bytes) -> tuple[dict[str, Any], int]:
    """Parse the leading ASF chunk and return metadata plus the next offset."""
    if len(data) < CHUNK_HEADER_SIZE or data[:4] != ASF_MAGIC:
        raise ValueError("Invalid savestate file: missing ASF header")
    chunk_size = _read_u32_be(data, 4)
    if chunk_size < CHUNK_HEADER_SIZE or chunk_size > len(data):
        raise ValueError("Invalid savestate file: truncated ASF chunk")
    payload, on_disk = _decode_chunk_payload(data, 0, chunk_size)
    return _parse_asf_payload(payload), chunk_size + _chunk_padding(on_disk)


def inspect_savestate(path: Path) -> dict[str, Any]:
    """
    Inspect a savestate file and extract metadata.

    Args:
        path: Path to the .uss savestate file

    Returns:
        Dictionary containing savestate metadata

    Raises:
        FileNotFoundError: If the file doesn't exist
        ValueError: If the file is not a valid savestate
    """
    if not path.exists():
        raise FileNotFoundError(f"Savestate file not found: {path}")

    file_size = path.stat().st_size
    if file_size > _MAX_SAVESTATE_SIZE:
        raise ValueError(
            f"Savestate file too large ({file_size} bytes, max {_MAX_SAVESTATE_SIZE})"
        )

    data = path.read_bytes()
    header_meta, offset = _parse_header(data)

    metadata: dict[str, Any] = {
        "file": str(path),
        "filename": path.name,
        "size_bytes": len(data),
        **header_meta,
    }

    # Parse chunks to extract more info
    chunks = []
    cpu_info = {}
    rom_info = {}
    memory_info = {"chip": 0, "bogo": 0, "fast": 0, "z3": 0}
    disk_info = []

    end_markers = 0
    while offset + END_CHUNK_SIZE <= len(data):
        chunk_name = data[offset : offset + 4].decode("latin-1", errors="replace")
        chunk_size = _read_u32_be(data, offset + 4)
        if chunk_size < END_CHUNK_SIZE or offset + chunk_size > len(data):
            break
        if chunk_size == END_CHUNK_SIZE:
            chunks.append({"name": chunk_name, "size": chunk_size})
            offset += END_CHUNK_SIZE
            if chunk_name == "END ":
                end_markers += 1
                if end_markers >= 2:
                    break
            continue
        if chunk_size < CHUNK_HEADER_SIZE:
            break
        chunk_data, on_disk = _decode_chunk_payload(data, offset, chunk_size)
        chunks.append({"name": chunk_name, "size": chunk_size})
        offset += chunk_size + _chunk_padding(on_disk)

        # Parse specific chunks
        if chunk_name == "CPU " and len(chunk_data) >= 8:
            cpu_model = _read_u32_be(chunk_data, 0)
            cpu_info["model"] = f"68{cpu_model:03d}"
            cpu_info["flags"] = _read_u32_be(chunk_data, 4)

        elif chunk_name == "FPU " and len(chunk_data) >= 4:
            fpu_model = _read_u32_be(chunk_data, 0)
            if fpu_model > 0:
                cpu_info["fpu"] = f"68{fpu_model:03d}"

        elif chunk_name == "CHIP" and len(chunk_data) >= 4:
            chipset_flags = _read_u32_be(chunk_data, 0)
            chipset = "OCS"
            if chipset_flags & 4:
                chipset = "AGA"
            elif chipset_flags & 3:
                chipset = "ECS"
            cpu_info["chipset"] = chipset

        elif chunk_name == "ROM " and len(chunk_data) >= 20:
            rom_info["start"] = _read_u32_be(chunk_data, 0)
            rom_info["size"] = _read_u32_be(chunk_data, 4)
            rom_info["type"] = _read_u32_be(chunk_data, 8)
            rom_info["flags"] = _read_u32_be(chunk_data, 12)
            rom_info["version"] = _read_u16_be(chunk_data, 16)
            rom_info["revision"] = _read_u16_be(chunk_data, 18)
            if len(chunk_data) >= 24:
                rom_info["crc"] = f"{_read_u32_be(chunk_data, 20):08X}"
            # ROM ID string follows
            if len(chunk_data) > 24:
                rom_id, _ = _read_string(chunk_data, 24)
                if rom_id:
                    rom_info["id"] = rom_id

        elif chunk_name in ("CRAM", "BRAM", "FRAM", "ZRAM"):
            # The payload is the RAM bank. Amiberry restores the size from
            # the uncompressed length, not from an integer inside the bank.
            key = {
                "CRAM": "chip",
                "BRAM": "bogo",
                "FRAM": "fast",
                "ZRAM": "z3",
            }[chunk_name]
            memory_info[key] += len(chunk_data)

        elif chunk_name.startswith("DSK") and len(chunk_data) >= 8:
            # Floppy drive info
            drive_num = chunk_name[3]
            if drive_num.isdigit():
                drive_info = {"drive": f"DF{drive_num}"}
                drive_info["id"] = _read_u32_be(chunk_data, 0)
                drive_info["state"] = chunk_data[4] if len(chunk_data) > 4 else 0
                drive_info["track"] = chunk_data[5] if len(chunk_data) > 5 else 0
                # Try to find disk image path
                if len(chunk_data) > 20:
                    img_path, _ = _read_string(chunk_data, 20)
                    if img_path:
                        drive_info["image"] = img_path
                disk_info.append(drive_info)

        elif chunk_name == "END ":
            continue

    # Add parsed info to metadata
    if cpu_info:
        metadata["cpu"] = cpu_info
    if rom_info:
        metadata["rom"] = rom_info
    if any(v > 0 for v in memory_info.values()):
        # Convert to KB for readability
        metadata["memory"] = {
            k: v // 1024 if v > 0 else 0 for k, v in memory_info.items()
        }
    if disk_info:
        metadata["disks"] = disk_info
    metadata["chunks"] = [c["name"] for c in chunks]

    return metadata


def get_savestate_summary(metadata: dict[str, Any]) -> str:
    """
    Generate a human-readable summary from savestate metadata.

    Args:
        metadata: Metadata dictionary from inspect_savestate()

    Returns:
        Formatted summary string
    """
    lines = [f"Savestate: {metadata.get('filename', 'Unknown')}"]
    lines.append(f"Size: {metadata.get('size_bytes', 0) / 1024:.1f} KB")

    if metadata.get("description"):
        lines.append(f"Description: {metadata['description']}")

    if metadata.get("emulator"):
        emu = metadata["emulator"]
        if metadata.get("emulator_version"):
            emu += f" {metadata['emulator_version']}"
        lines.append(f"Created by: {emu}")

    if "cpu" in metadata:
        cpu = metadata["cpu"]
        cpu_str = cpu.get("model", "Unknown")
        if cpu.get("fpu"):
            cpu_str += f" + {cpu['fpu']} FPU"
        if cpu.get("chipset"):
            cpu_str += f" ({cpu['chipset']})"
        lines.append(f"CPU: {cpu_str}")

    if "memory" in metadata:
        mem = metadata["memory"]
        mem_parts = []
        if mem.get("chip"):
            mem_parts.append(f"{mem['chip']}KB Chip")
        if mem.get("bogo"):
            mem_parts.append(f"{mem['bogo']}KB Slow")
        if mem.get("fast"):
            mem_parts.append(f"{mem['fast']}KB Fast")
        if mem.get("z3"):
            mem_parts.append(f"{mem['z3']}KB Z3")
        if mem_parts:
            lines.append(f"Memory: {', '.join(mem_parts)}")

    if "rom" in metadata:
        rom = metadata["rom"]
        rom_str = f"v{rom.get('version', 0)}.{rom.get('revision', 0)}"
        if rom.get("id"):
            rom_str += f" ({rom['id']})"
        if rom.get("crc"):
            rom_str += f" [CRC: {rom['crc']}]"
        lines.append(f"Kickstart: {rom_str}")

    if "disks" in metadata:
        for disk in metadata["disks"]:
            disk_str = disk["drive"]
            if disk.get("image"):
                disk_str += f": {disk['image']}"
            elif disk.get("state", 0) & 1:
                disk_str += ": (motor on)"
            lines.append(f"Floppy {disk_str}")

    return "\n".join(lines)


def list_savestate_chunks(path: Path) -> list[dict[str, Any]]:
    """
    List all chunks in a savestate file with their sizes.

    Args:
        path: Path to the .uss savestate file

    Returns:
        List of chunk info dictionaries
    """
    if not path.exists():
        raise FileNotFoundError(f"Savestate file not found: {path}")

    file_size = path.stat().st_size
    if file_size > _MAX_SAVESTATE_SIZE:
        raise ValueError(
            f"Savestate file too large ({file_size} bytes, max {_MAX_SAVESTATE_SIZE})"
        )

    data = path.read_bytes()
    _, offset = _parse_header(data)

    chunks = []
    while offset + END_CHUNK_SIZE <= len(data):
        chunk_name = data[offset : offset + 4].decode("latin-1", errors="replace")
        chunk_size = _read_u32_be(data, offset + 4)
        if chunk_size < END_CHUNK_SIZE or offset + chunk_size > len(data):
            break
        if chunk_size == END_CHUNK_SIZE:
            chunks.append(
                {
                    "name": chunk_name,
                    "offset": offset,
                    "size": chunk_size,
                    "data_size": 0,
                }
            )
            offset += END_CHUNK_SIZE
            if chunk_name == "END ":
                break
            continue
        if chunk_size < CHUNK_HEADER_SIZE:
            break
        _, on_disk = _decode_chunk_payload(data, offset, chunk_size)
        chunks.append(
            {
                "name": chunk_name,
                "offset": offset,
                "size": chunk_size,
                "data_size": chunk_size - CHUNK_HEADER_SIZE,
            }
        )
        offset += chunk_size + _chunk_padding(on_disk)
        if chunk_name == "END ":
            break

    return chunks
