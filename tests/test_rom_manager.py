#!/usr/bin/env python3
"""
Unit tests for the rom_manager module.
"""

import tempfile
import zlib
from pathlib import Path

import pytest

from amiberry_mcp.rom_manager import (
    KNOWN_ROMS,
    ROM_EXTENSIONS,
    calculate_rom_crc32,
    calculate_rom_md5,
    identify_rom,
    scan_rom_directory,
    get_rom_summary,
    find_rom_for_model,
)


class TestChecksumFunctions:
    """Tests for checksum calculation functions."""

    def test_calculate_crc32(self, tmp_path):
        """Test CRC32 calculation."""
        # Create a test file with known content
        test_file = tmp_path / "test.rom"
        test_content = b"Hello, Amiga!"
        test_file.write_bytes(test_content)

        crc = calculate_rom_crc32(test_file)

        # Verify it matches Python's zlib calculation
        expected = f"{zlib.crc32(test_content) & 0xFFFFFFFF:08X}"
        assert crc == expected

    def test_calculate_crc32_uppercase(self, tmp_path):
        """Test that CRC32 is uppercase hex."""
        test_file = tmp_path / "test.rom"
        test_file.write_bytes(b"test")

        crc = calculate_rom_crc32(test_file)

        assert crc == crc.upper()
        assert len(crc) == 8

    def test_calculate_md5(self, tmp_path):
        """Test MD5 calculation."""
        test_file = tmp_path / "test.rom"
        test_content = b"Hello, Amiga!"
        test_file.write_bytes(test_content)

        md5 = calculate_rom_md5(test_file)

        # MD5 should be 32 hex characters
        assert len(md5) == 32
        assert all(c in "0123456789abcdef" for c in md5)

    def test_calculate_md5_lowercase(self, tmp_path):
        """Test that MD5 is lowercase hex."""
        test_file = tmp_path / "test.rom"
        test_file.write_bytes(b"test")

        md5 = calculate_rom_md5(test_file)

        assert md5 == md5.lower()


class TestIdentifyRom:
    """Tests for the identify_rom function."""

    def test_identify_unknown_rom(self, tmp_path):
        """Test identifying an unknown ROM."""
        test_file = tmp_path / "unknown.rom"
        test_file.write_bytes(b"X" * 262144)  # 256KB

        result = identify_rom(test_file)

        assert result["filename"] == "unknown.rom"
        assert result["size"] == 262144
        assert result["identified"] == False
        assert "crc32" in result
        assert "md5" in result
        assert result["probable_type"] == "Kickstart 1.x (256KB)"

    def test_identify_512kb_rom(self, tmp_path):
        """Test identifying a 512KB ROM size."""
        test_file = tmp_path / "kick31.rom"
        test_file.write_bytes(b"Y" * 524288)  # 512KB

        result = identify_rom(test_file)

        assert result["size"] == 524288
        assert result["identified"] == False
        assert result["probable_type"] == "Kickstart 2.x/3.x (512KB)"

    def test_identify_1mb_rom(self, tmp_path):
        """Test identifying a 1MB ROM size."""
        test_file = tmp_path / "extended.rom"
        test_file.write_bytes(b"Z" * 1048576)  # 1MB

        result = identify_rom(test_file)

        assert result["size"] == 1048576
        assert result["identified"] == False
        assert result["probable_type"] == "Extended ROM or combined ROM (1MB)"

    def test_identify_unknown_size(self, tmp_path):
        """Test identifying a ROM with unusual size."""
        test_file = tmp_path / "weird.rom"
        test_file.write_bytes(b"W" * 100000)

        result = identify_rom(test_file)

        assert result["probable_type"] == "Unknown"

    def test_identify_nonexistent_rom(self, tmp_path):
        """Test identifying a ROM that doesn't exist."""
        test_file = tmp_path / "nonexistent.rom"

        with pytest.raises(FileNotFoundError):
            identify_rom(test_file)


class TestScanRomDirectory:
    """Tests for the scan_rom_directory function."""

    def test_scan_empty_directory(self, tmp_path):
        """Test scanning an empty directory."""
        result = scan_rom_directory(tmp_path)
        assert result == []

    def test_scan_nonexistent_directory(self, tmp_path):
        """Test scanning a directory that doesn't exist."""
        nonexistent = tmp_path / "nonexistent"
        result = scan_rom_directory(nonexistent)
        assert result == []

    def test_scan_with_rom_files(self, tmp_path):
        """Test scanning a directory with ROM files."""
        # Create some test ROM files
        (tmp_path / "kick13.rom").write_bytes(b"A" * 262144)
        (tmp_path / "kick31.rom").write_bytes(b"B" * 524288)
        (tmp_path / "notrom.txt").write_bytes(b"text file")

        result = scan_rom_directory(tmp_path)

        filenames = [r["filename"] for r in result]
        assert "kick13.rom" in filenames
        assert "kick31.rom" in filenames
        assert "notrom.txt" not in filenames

    def test_scan_recursive(self, tmp_path):
        """Test recursive scanning."""
        # Create nested structure
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (tmp_path / "top.rom").write_bytes(b"X" * 262144)
        (subdir / "nested.rom").write_bytes(b"Y" * 262144)

        result = scan_rom_directory(tmp_path, recursive=True)

        filenames = [r["filename"] for r in result]
        assert "top.rom" in filenames
        assert "nested.rom" in filenames

    def test_scan_non_recursive(self, tmp_path):
        """Test non-recursive scanning."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (tmp_path / "top.rom").write_bytes(b"X" * 262144)
        (subdir / "nested.rom").write_bytes(b"Y" * 262144)

        result = scan_rom_directory(tmp_path, recursive=False)

        filenames = [r["filename"] for r in result]
        assert "top.rom" in filenames
        assert "nested.rom" not in filenames

    def test_scan_multiple_extensions(self, tmp_path):
        """Test scanning finds multiple extensions."""
        (tmp_path / "kick.rom").write_bytes(b"A" * 262144)
        (tmp_path / "kick.bin").write_bytes(b"B" * 262144)
        (tmp_path / "kick.a500").write_bytes(b"C" * 262144)

        result = scan_rom_directory(tmp_path)

        filenames = [r["filename"] for r in result]
        assert "kick.rom" in filenames
        assert "kick.bin" in filenames
        assert "kick.a500" in filenames


class TestGetRomSummary:
    """Tests for the get_rom_summary function."""

    def test_summary_identified_rom(self):
        """Test summary for an identified ROM."""
        rom_info = {
            "filename": "kick31.rom",
            "identified": True,
            "version": "3.1",
            "revision": "40.68",
            "model": "A1200",
            "size": 524288,
            "crc32": "D6BAE334",
        }

        summary = get_rom_summary(rom_info)

        assert "kick31.rom" in summary
        assert "Kickstart 3.1" in summary
        assert "Rev 40.68" in summary
        assert "A1200" in summary
        assert "512 KB" in summary
        assert "D6BAE334" in summary

    def test_summary_unidentified_rom(self):
        """Test summary for an unidentified ROM."""
        rom_info = {
            "filename": "unknown.rom",
            "identified": False,
            "probable_type": "Kickstart 2.x/3.x (512KB)",
            "size": 524288,
            "crc32": "12345678",
        }

        summary = get_rom_summary(rom_info)

        assert "unknown.rom" in summary
        assert "unidentified" in summary
        assert "512 KB" in summary
        assert "12345678" in summary


class TestFindRomForModel:
    """Tests for the find_rom_for_model function."""

    def test_find_exact_match(self):
        """Test finding a ROM with exact model match."""
        roms = [
            {"identified": True, "model": "A500/A1000/A2000", "version": "1.3"},
            {"identified": True, "model": "A1200", "version": "3.1"},
        ]

        result = find_rom_for_model(roms, "A1200")

        assert result is not None
        assert result["version"] == "3.1"

    def test_find_partial_match(self):
        """Test finding a ROM with partial model match."""
        roms = [
            {"identified": True, "model": "A500/A1000/A2000", "version": "1.3"},
        ]

        result = find_rom_for_model(roms, "A500")

        assert result is not None
        assert result["version"] == "1.3"

    def test_find_compatible_fallback(self):
        """Test finding a compatible ROM as fallback."""
        roms = [
            {"identified": True, "model": "A500/A1000/A2000", "version": "1.3"},
        ]

        # A500+ is compatible with A500 ROMs
        result = find_rom_for_model(roms, "A500+")

        assert result is not None

    def test_find_no_match(self):
        """Test when no matching ROM is found."""
        roms = [
            {"identified": True, "model": "A1200", "version": "3.1"},
        ]

        result = find_rom_for_model(roms, "CD32")

        assert result is None

    def test_find_unidentified_skipped(self):
        """Test that unidentified ROMs are skipped."""
        roms = [
            {"identified": False, "filename": "unknown.rom"},
            {"identified": True, "model": "A1200", "version": "3.1"},
        ]

        result = find_rom_for_model(roms, "A1200")

        assert result is not None
        assert result["version"] == "3.1"

    def test_find_case_insensitive(self):
        """Test that model search is case-insensitive."""
        roms = [
            {"identified": True, "model": "A1200", "version": "3.1"},
        ]

        result = find_rom_for_model(roms, "a1200")

        assert result is not None


class TestKnownRomsDatabase:
    """Tests for the KNOWN_ROMS database."""

    def test_known_roms_format(self):
        """Test that all entries have required fields."""
        for crc, info in KNOWN_ROMS.items():
            assert "version" in info
            assert "revision" in info
            assert "model" in info
            assert "size" in info

            # CRC should be 8 uppercase hex chars
            assert len(crc) == 8
            assert crc == crc.upper()

    def test_known_roms_sizes(self):
        """Test that ROM sizes are valid."""
        valid_sizes = {262144, 524288, 1048576}  # 256KB, 512KB, 1MB

        for crc, info in KNOWN_ROMS.items():
            assert info["size"] in valid_sizes, f"Invalid size for {crc}: {info['size']}"

    def test_kickstart_versions_present(self):
        """Test that common Kickstart versions are present."""
        versions = [info["version"] for info in KNOWN_ROMS.values()]

        assert "1.3" in versions
        assert "2.04" in versions or "2.05" in versions
        assert "3.0" in versions
        assert "3.1" in versions


class TestRomExtensions:
    """Tests for ROM file extensions."""

    def test_common_extensions_present(self):
        """Test that common ROM extensions are present."""
        assert ".rom" in ROM_EXTENSIONS
        assert ".bin" in ROM_EXTENSIONS

    def test_model_extensions_present(self):
        """Test that model-specific extensions are present."""
        assert ".a500" in ROM_EXTENSIONS
        assert ".a600" in ROM_EXTENSIONS
        assert ".a1200" in ROM_EXTENSIONS
        assert ".a4000" in ROM_EXTENSIONS


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
