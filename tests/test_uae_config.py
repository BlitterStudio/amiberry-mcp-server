#!/usr/bin/env python3
"""
Unit tests for the uae_config module.
"""

import tempfile
from pathlib import Path

import pytest

from amiberry_mcp.uae_config import (
    parse_uae_config,
    write_uae_config,
    modify_uae_config,
    create_config_from_template,
    get_config_summary,
)


class TestParseUaeConfig:
    """Tests for parse_uae_config function."""

    def test_parse_simple_config(self, tmp_path: Path):
        """Test parsing a simple config file."""
        config_file = tmp_path / "test.uae"
        config_file.write_text("cpu_model=68000\nchipmem_size=2\n")

        config = parse_uae_config(config_file)

        assert config["cpu_model"] == "68000"
        assert config["chipmem_size"] == "2"

    def test_parse_config_with_comments(self, tmp_path: Path):
        """Test that comments are ignored."""
        config_file = tmp_path / "test.uae"
        config_file.write_text(
            "; This is a comment\n"
            "cpu_model=68020\n"
            "# Another comment\n"
            "chipset=aga\n"
        )

        config = parse_uae_config(config_file)

        assert config["cpu_model"] == "68020"
        assert config["chipset"] == "aga"
        assert len(config) == 2

    def test_parse_config_with_empty_lines(self, tmp_path: Path):
        """Test that empty lines are ignored."""
        config_file = tmp_path / "test.uae"
        config_file.write_text("cpu_model=68000\n\n\nchipset=ocs\n")

        config = parse_uae_config(config_file)

        assert len(config) == 2

    def test_parse_config_with_values_containing_equals(self, tmp_path: Path):
        """Test parsing values that contain equals signs."""
        config_file = tmp_path / "test.uae"
        config_file.write_text("path=/some/path=with=equals\n")

        config = parse_uae_config(config_file)

        assert config["path"] == "/some/path=with=equals"

    def test_parse_nonexistent_file(self, tmp_path: Path):
        """Test that FileNotFoundError is raised for missing files."""
        config_file = tmp_path / "nonexistent.uae"

        with pytest.raises(FileNotFoundError):
            parse_uae_config(config_file)


class TestWriteUaeConfig:
    """Tests for write_uae_config function."""

    def test_write_simple_config(self, tmp_path: Path):
        """Test writing a simple config file."""
        config_file = tmp_path / "test.uae"
        config = {"cpu_model": "68000", "chipset": "ocs"}

        write_uae_config(config_file, config)

        content = config_file.read_text()
        assert "cpu_model=68000" in content
        assert "chipset=ocs" in content

    def test_write_creates_parent_directories(self, tmp_path: Path):
        """Test that parent directories are created if needed."""
        config_file = tmp_path / "subdir" / "test.uae"
        config = {"cpu_model": "68000"}

        write_uae_config(config_file, config)

        assert config_file.exists()

    def test_write_includes_header_comment(self, tmp_path: Path):
        """Test that header comments are included."""
        config_file = tmp_path / "test.uae"
        config = {"cpu_model": "68000"}

        write_uae_config(config_file, config)

        content = config_file.read_text()
        assert content.startswith(";")
        assert "amiberry-mcp-server" in content.lower()


class TestModifyUaeConfig:
    """Tests for modify_uae_config function."""

    def test_modify_existing_option(self, tmp_path: Path):
        """Test modifying an existing option."""
        config_file = tmp_path / "test.uae"
        config_file.write_text("cpu_model=68000\nchipset=ocs\n")

        result = modify_uae_config(config_file, {"cpu_model": "68020"})

        assert result["cpu_model"] == "68020"
        assert result["chipset"] == "ocs"

    def test_add_new_option(self, tmp_path: Path):
        """Test adding a new option."""
        config_file = tmp_path / "test.uae"
        config_file.write_text("cpu_model=68000\n")

        result = modify_uae_config(config_file, {"chipset": "aga"})

        assert result["cpu_model"] == "68000"
        assert result["chipset"] == "aga"

    def test_remove_option(self, tmp_path: Path):
        """Test removing an option by setting it to None."""
        config_file = tmp_path / "test.uae"
        config_file.write_text("cpu_model=68000\nchipset=ocs\n")

        result = modify_uae_config(config_file, {"chipset": None})

        assert result["cpu_model"] == "68000"
        assert "chipset" not in result


class TestCreateConfigFromTemplate:
    """Tests for create_config_from_template function."""

    def test_create_a500_config(self, tmp_path: Path):
        """Test creating an A500 config."""
        config_file = tmp_path / "test.uae"

        config = create_config_from_template(config_file, "A500")

        assert config["cpu_model"] == "68000"
        assert config["chipset"] == "ocs"
        assert config_file.exists()

    def test_create_a1200_config(self, tmp_path: Path):
        """Test creating an A1200 config."""
        config_file = tmp_path / "test.uae"

        config = create_config_from_template(config_file, "A1200")

        assert config["cpu_model"] == "68020"
        assert config["chipset"] == "aga"

    def test_create_cd32_config(self, tmp_path: Path):
        """Test creating a CD32 config."""
        config_file = tmp_path / "test.uae"

        config = create_config_from_template(config_file, "CD32")

        assert config["cpu_model"] == "68020"
        assert config["chipset"] == "aga"
        assert config["cd32cd"] == "true"

    def test_create_config_with_overrides(self, tmp_path: Path):
        """Test creating a config with custom overrides."""
        config_file = tmp_path / "test.uae"

        config = create_config_from_template(
            config_file, "A500", {"chipmem_size": "4", "fastmem_size": "4096"}
        )

        assert config["cpu_model"] == "68000"  # From template
        assert config["chipmem_size"] == "4"  # Override
        assert config["fastmem_size"] == "4096"  # Override

    def test_create_config_invalid_template(self, tmp_path: Path):
        """Test that invalid template raises ValueError."""
        config_file = tmp_path / "test.uae"

        with pytest.raises(ValueError):
            create_config_from_template(config_file, "InvalidModel")


class TestGetConfigSummary:
    """Tests for get_config_summary function."""

    def test_summary_cpu_info(self):
        """Test CPU info in summary."""
        config = {"cpu_model": "68020", "cpu_speed": "max"}

        summary = get_config_summary(config)

        assert summary["cpu"]["model"] == "6868020"  # Prepends 68
        assert summary["cpu"]["speed"] == "max"

    def test_summary_memory_info(self):
        """Test memory info in summary."""
        config = {"chipmem_size": "4", "fastmem_size": "8192"}

        summary = get_config_summary(config)

        assert summary["memory"]["chip_kb"] == 2048  # 4 * 512
        assert summary["memory"]["fast_kb"] == 8388608  # 8192 * 1024

    def test_summary_floppy_info(self):
        """Test floppy info in summary."""
        config = {
            "floppy0": "/path/to/disk1.adf",
            "floppy1": "/path/to/disk2.adf",
        }

        summary = get_config_summary(config)

        assert len(summary["floppies"]) == 2
        assert summary["floppies"][0]["drive"] == "DF0"
        assert summary["floppies"][0]["image"] == "/path/to/disk1.adf"

    def test_summary_graphics_info(self):
        """Test graphics info in summary."""
        config = {
            "gfx_width": "800",
            "gfx_height": "600",
            "gfx_fullscreen_amiga": "true",
        }

        summary = get_config_summary(config)

        assert summary["graphics"]["width"] == "800"
        assert summary["graphics"]["height"] == "600"
        assert summary["graphics"]["fullscreen"] is True
