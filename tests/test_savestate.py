#!/usr/bin/env python3
"""
Unit tests for the savestate module.
"""

import struct
import zlib
from pathlib import Path

import pytest

from amiberry_mcp.savestate import (
    ASF_MAGIC,
    _read_string,
    _read_u16_be,
    _read_u32_be,
    get_savestate_summary,
    inspect_savestate,
    list_savestate_chunks,
)


def _iff_chunk(name: bytes, payload: bytes) -> bytes:
    """Build one uncompressed Amiberry savestate chunk, including padding."""
    size = 12 + len(payload)
    pad = 4 - (len(payload) & 3)
    return name + struct.pack(">II", size, 0) + payload + (b"\x00" * pad)


def _asf_chunk(
    version: int, emulator: str, emu_version: str, description: str
) -> bytes:
    payload = struct.pack(">I", version)
    payload += emulator.encode("latin-1") + b"\x00"
    payload += emu_version.encode("latin-1") + b"\x00"
    payload += description.encode("latin-1") + b"\x00"
    return _iff_chunk(ASF_MAGIC, payload)


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_read_u32_be(self):
        """Test big-endian 32-bit unsigned integer reading."""
        data = b"\x00\x00\x00\x01"  # 1 in big-endian
        assert _read_u32_be(data, 0) == 1

        data = b"\x12\x34\x56\x78"
        assert _read_u32_be(data, 0) == 0x12345678

        data = b"\xff\xff\xff\xff"
        assert _read_u32_be(data, 0) == 0xFFFFFFFF

    def test_read_u16_be(self):
        """Test big-endian 16-bit unsigned integer reading."""
        data = b"\x00\x01"
        assert _read_u16_be(data, 0) == 1

        data = b"\x12\x34"
        assert _read_u16_be(data, 0) == 0x1234

        data = b"\xff\xff"
        assert _read_u16_be(data, 0) == 0xFFFF

    def test_read_string(self):
        """Test null-terminated string reading."""
        data = b"Hello\x00World"
        string, consumed = _read_string(data, 0)
        assert string == "Hello"
        assert consumed == 6  # 5 chars + null terminator

    def test_read_string_at_offset(self):
        """Test reading string at offset."""
        data = b"XXXXX\x00Hello\x00World"
        string, consumed = _read_string(data, 6)
        assert string == "Hello"
        assert consumed == 6

    def test_read_string_empty(self):
        """Test reading empty string."""
        data = b"\x00rest"
        string, consumed = _read_string(data, 0)
        assert string == ""
        assert consumed == 1

    def test_read_string_no_terminator(self):
        """Test reading when no null terminator exists."""
        data = b"NoNull"
        string, consumed = _read_string(data, 0)
        assert string == ""
        assert consumed == 0


class TestInspectSavestate:
    """Tests for the inspect_savestate function."""

    def _create_minimal_savestate(self, tmpdir) -> Path:
        """Create a minimal valid savestate file for testing."""
        path = Path(tmpdir) / "test.uss"
        data = _asf_chunk(0, "UAE", "8.4.1", "Test savestate")
        data += b"END " + struct.pack(">I", 8)
        path.write_bytes(data)
        return path

    def _create_savestate_with_cpu(self, tmpdir) -> Path:
        """Create a savestate with CPU, chipset, and chip-RAM chunks."""
        path = Path(tmpdir) / "cpu.uss"
        data = _asf_chunk(0, "UAE", "8.4.1", "CPU test")
        data += _iff_chunk(b"CPU ", struct.pack(">II", 68020, 0))
        data += _iff_chunk(b"FPU ", struct.pack(">I", 68881))
        data += _iff_chunk(b"CHIP", struct.pack(">I", 4))
        # Bytes 4-7 look like a size field. They are RAM contents.
        ram = b"\x00\x00\x00\x00\xff\xff\xff\xff" + (b"\x00" * (1024 - 8))
        data += _iff_chunk(b"CRAM", ram)
        data += b"END " + struct.pack(">I", 8)
        path.write_bytes(data)
        return path

    def test_inspect_minimal_savestate(self, tmp_path):
        """Test inspecting a minimal valid savestate."""
        path = self._create_minimal_savestate(tmp_path)

        metadata = inspect_savestate(path)

        assert metadata["filename"] == "test.uss"
        assert metadata["emulator"] == "UAE"
        assert metadata["emulator_version"] == "8.4.1"
        assert metadata["description"] == "Test savestate"
        assert metadata["version"] == 0
        assert "END " in metadata["chunks"]

    def test_inspect_savestate_with_cpu(self, tmp_path):
        """Test inspecting savestate with CPU info."""
        path = self._create_savestate_with_cpu(tmp_path)

        metadata = inspect_savestate(path)

        assert "cpu" in metadata
        assert metadata["cpu"]["model"] == "68020"
        assert metadata["cpu"]["fpu"] == "68881"

        assert "memory" in metadata
        assert metadata["memory"]["chip"] == 1  # payload length, not offset 4

    def test_cpu_and_fpu_numbers_are_not_prefixed(self, tmp_path):
        """A raw 68020 must not become 6868020, and FPU 0 is none."""
        path = tmp_path / "models.uss"
        data = _asf_chunk(0, "UAE", "8.4.1", "models")
        data += _iff_chunk(b"CPU ", struct.pack(">II", 68020, 0))
        data += _iff_chunk(b"FPU ", struct.pack(">I", 0))
        data += b"END " + struct.pack(">I", 8)
        path.write_bytes(data)

        metadata = inspect_savestate(path)

        assert metadata["cpu"]["model"] == "68020"
        assert metadata["cpu"]["fpu"] == "none"
        assert "FPU" not in get_savestate_summary(metadata)

    def test_compressed_ram_uses_uncompressed_length(self, tmp_path):
        """A zlib RAM bank reports the bank size, not the compressed size."""
        bank = b"\xff" * 2048
        stored = struct.pack(">I", len(bank)) + zlib.compress(bank)
        size = 12 + len(stored)
        pad = 4 - (len(stored) & 3)
        chunk = b"FRAM" + struct.pack(">II", size, 1) + stored + (b"\x00" * pad)
        path = tmp_path / "fast.uss"
        path.write_bytes(
            _asf_chunk(0, "UAE", "8.4.1", "fast")
            + chunk
            + b"END "
            + struct.pack(">I", 8)
        )

        metadata = inspect_savestate(path)

        assert metadata["memory"]["fast"] == 2

    def test_inspect_nonexistent_file(self, tmp_path):
        """Test inspecting a file that doesn't exist."""
        path = tmp_path / "nonexistent.uss"

        with pytest.raises(FileNotFoundError):
            inspect_savestate(path)

    def test_inspect_invalid_file(self, tmp_path):
        """Test inspecting an invalid (non-ASF) file."""
        path = tmp_path / "invalid.uss"
        path.write_bytes(b"NOT ASF FILE CONTENT")

        with pytest.raises(ValueError, match="missing ASF header"):
            inspect_savestate(path)

    def test_inspect_empty_file(self, tmp_path):
        """Test inspecting an empty file."""
        path = tmp_path / "empty.uss"
        path.write_bytes(b"")

        with pytest.raises(ValueError, match="missing ASF header"):
            inspect_savestate(path)


class TestGetSavestateSummary:
    """Tests for the get_savestate_summary function."""

    def test_summary_basic(self):
        """Test basic summary generation."""
        metadata = {
            "filename": "test.uss",
            "size_bytes": 102400,
            "emulator": "Amiberry",
            "emulator_version": "v5.7",
        }

        summary = get_savestate_summary(metadata)

        assert "test.uss" in summary
        assert "100.0 KB" in summary
        assert "Amiberry v5.7" in summary

    def test_summary_with_cpu(self):
        """Test summary with CPU info."""
        metadata = {
            "filename": "game.uss",
            "size_bytes": 51200,
            "cpu": {
                "model": "68020",
                "fpu": "68881",
                "chipset": "ECS",
            },
        }

        summary = get_savestate_summary(metadata)

        assert "68020" in summary
        assert "68881" in summary
        assert "FPU" in summary
        assert "ECS" in summary

    def test_summary_with_memory(self):
        """Test summary with memory info."""
        metadata = {
            "filename": "game.uss",
            "size_bytes": 51200,
            "memory": {
                "chip": 512,
                "bogo": 512,
                "fast": 8192,
                "z3": 0,
            },
        }

        summary = get_savestate_summary(metadata)

        assert "512KB Chip" in summary
        assert "512KB Slow" in summary
        assert "8192KB Fast" in summary

    def test_summary_with_description(self):
        """Test summary with description."""
        metadata = {
            "filename": "save.uss",
            "size_bytes": 10000,
            "description": "Level 5 - Boss fight",
        }

        summary = get_savestate_summary(metadata)

        assert "Level 5 - Boss fight" in summary


class TestSavestateFileSizeCap:
    """Tests for Fix #9: Savestate file size cap."""

    def test_inspect_rejects_oversized_file(self, tmp_path):
        """inspect_savestate should reject files larger than 256MB."""
        from amiberry_mcp.savestate import _MAX_SAVESTATE_SIZE

        path = tmp_path / "huge.uss"
        with open(path, "wb") as f:
            f.write(ASF_MAGIC)
            f.seek(_MAX_SAVESTATE_SIZE + 1)
            f.write(b"\x00")

        with pytest.raises(ValueError, match="too large"):
            inspect_savestate(path)

    def test_list_chunks_rejects_oversized_file(self, tmp_path):
        """list_savestate_chunks should reject files larger than 256MB."""
        from amiberry_mcp.savestate import _MAX_SAVESTATE_SIZE

        path = tmp_path / "huge.uss"
        with open(path, "wb") as f:
            f.write(ASF_MAGIC)
            f.seek(_MAX_SAVESTATE_SIZE + 1)
            f.write(b"\x00")

        with pytest.raises(ValueError, match="too large"):
            list_savestate_chunks(path)


class TestListSavestateChunks:
    """Tests for the list_savestate_chunks function."""

    def _create_multi_chunk_savestate(self, tmpdir) -> Path:
        """Create a savestate with multiple chunks."""
        path = Path(tmpdir) / "chunks.uss"
        data = _asf_chunk(0, "UAE", "8.4.1", "chunks")
        for name in (b"CPU ", b"FPU ", b"CHIP", b"CRAM"):
            data += _iff_chunk(name, b"\x00" * 8)
        data += b"END " + struct.pack(">I", 8)
        path.write_bytes(data)
        return path

    def test_list_chunks(self, tmp_path):
        """Test listing chunks in a savestate."""
        path = self._create_multi_chunk_savestate(tmp_path)

        chunks = list_savestate_chunks(path)

        chunk_names = [c["name"] for c in chunks]
        assert "CPU " in chunk_names
        assert "FPU " in chunk_names
        assert "CHIP" in chunk_names
        assert "CRAM" in chunk_names
        assert "END " in chunk_names

    def test_list_chunks_nonexistent(self, tmp_path):
        """Test listing chunks for nonexistent file."""
        path = tmp_path / "missing.uss"

        with pytest.raises(FileNotFoundError):
            list_savestate_chunks(path)

    def test_list_chunks_invalid(self, tmp_path):
        """Test listing chunks for invalid file."""
        path = tmp_path / "invalid.uss"
        path.write_bytes(b"INVALID CONTENT")

        with pytest.raises(ValueError, match="missing ASF header"):
            list_savestate_chunks(path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
