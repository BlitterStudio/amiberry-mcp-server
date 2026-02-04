#!/usr/bin/env python3
"""
Unit tests for the savestate module.
"""

import struct
import tempfile
from pathlib import Path

import pytest

from amiberry_mcp.savestate import (
    ASF_MAGIC,
    inspect_savestate,
    get_savestate_summary,
    list_savestate_chunks,
    _read_u32_be,
    _read_u16_be,
    _read_string,
)


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_read_u32_be(self):
        """Test big-endian 32-bit unsigned integer reading."""
        data = b"\x00\x00\x00\x01"  # 1 in big-endian
        assert _read_u32_be(data, 0) == 1

        data = b"\x12\x34\x56\x78"
        assert _read_u32_be(data, 0) == 0x12345678

        data = b"\xFF\xFF\xFF\xFF"
        assert _read_u32_be(data, 0) == 0xFFFFFFFF

    def test_read_u16_be(self):
        """Test big-endian 16-bit unsigned integer reading."""
        data = b"\x00\x01"
        assert _read_u16_be(data, 0) == 1

        data = b"\x12\x34"
        assert _read_u16_be(data, 0) == 0x1234

        data = b"\xFF\xFF"
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

        # Build a minimal ASF file
        data = bytearray()

        # Magic header
        data.extend(ASF_MAGIC)

        # Version (4 bytes big-endian)
        data.extend(struct.pack(">I", 1))

        # Emulator name (null-terminated)
        data.extend(b"Amiberry\x00")

        # Emulator version (null-terminated)
        data.extend(b"v5.7\x00")

        # Description (null-terminated)
        data.extend(b"Test savestate\x00")

        # Add an END chunk
        data.extend(b"END ")
        data.extend(struct.pack(">I", 8))  # chunk size

        path.write_bytes(bytes(data))
        return path

    def _create_savestate_with_cpu(self, tmpdir) -> Path:
        """Create a savestate with CPU chunk."""
        path = Path(tmpdir) / "cpu.uss"

        data = bytearray()

        # Header
        data.extend(ASF_MAGIC)
        data.extend(struct.pack(">I", 1))  # version
        data.extend(b"Amiberry\x00")
        data.extend(b"v5.7\x00")
        data.extend(b"CPU test\x00")

        # CPU chunk (68020)
        cpu_data = bytearray()
        cpu_data.extend(struct.pack(">I", 20))  # CPU model (68020)
        cpu_data.extend(struct.pack(">I", 0))  # flags
        chunk_size = 8 + len(cpu_data)

        data.extend(b"CPU ")
        data.extend(struct.pack(">I", chunk_size))
        data.extend(cpu_data)

        # CHIP chunk (AGA)
        chip_data = bytearray()
        chip_data.extend(struct.pack(">I", 4))  # AGA flag
        chunk_size = 8 + len(chip_data)

        data.extend(b"CHIP")
        data.extend(struct.pack(">I", chunk_size))
        data.extend(chip_data)

        # CRAM chunk (2MB chip RAM)
        cram_data = bytearray()
        cram_data.extend(struct.pack(">I", 0))  # start
        cram_data.extend(struct.pack(">I", 2097152))  # size (2MB)
        chunk_size = 8 + len(cram_data)

        data.extend(b"CRAM")
        data.extend(struct.pack(">I", chunk_size))
        data.extend(cram_data)

        # END chunk
        data.extend(b"END ")
        data.extend(struct.pack(">I", 8))

        path.write_bytes(bytes(data))
        return path

    def test_inspect_minimal_savestate(self, tmp_path):
        """Test inspecting a minimal valid savestate."""
        path = self._create_minimal_savestate(tmp_path)

        metadata = inspect_savestate(path)

        assert metadata["filename"] == "test.uss"
        assert metadata["emulator"] == "Amiberry"
        assert metadata["emulator_version"] == "v5.7"
        assert metadata["description"] == "Test savestate"
        assert metadata["version"] == 1
        assert "END " in metadata["chunks"]

    def test_inspect_savestate_with_cpu(self, tmp_path):
        """Test inspecting savestate with CPU info."""
        path = self._create_savestate_with_cpu(tmp_path)

        metadata = inspect_savestate(path)

        assert "cpu" in metadata
        assert metadata["cpu"]["model"] == "68020"
        assert metadata["cpu"]["chipset"] == "AGA"

        assert "memory" in metadata
        assert metadata["memory"]["chip"] == 2048  # 2MB in KB

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


class TestListSavestateChunks:
    """Tests for the list_savestate_chunks function."""

    def _create_multi_chunk_savestate(self, tmpdir) -> Path:
        """Create a savestate with multiple chunks."""
        path = Path(tmpdir) / "chunks.uss"

        data = bytearray()

        # Header
        data.extend(ASF_MAGIC)
        data.extend(struct.pack(">I", 1))
        data.extend(b"Test\x00")
        data.extend(b"1.0\x00")
        data.extend(b"\x00")

        # Multiple chunks
        for name in [b"CPU ", b"FPU ", b"CHIP", b"CRAM", b"END "]:
            chunk_data = b"\x00" * 8  # 8 bytes of dummy data
            chunk_size = 8 + len(chunk_data)
            data.extend(name)
            data.extend(struct.pack(">I", chunk_size))
            data.extend(chunk_data)
            if name == b"END ":
                break

        path.write_bytes(bytes(data))
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
