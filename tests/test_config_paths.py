#!/usr/bin/env python3
"""
Unit tests for configuration path resolution.

Ensures that find_config_path correctly locates .uae files when given
only a filename (e.g. 'Lightwave.uae') against the platform-specific
CONFIG_DIR.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from amiberry_mcp.common import find_config_path


class TestFindConfigPath:
    """Tests for find_config_path()."""

    def test_finds_config_by_filename(self, tmp_path):
        """A bare filename is resolved under CONFIG_DIR."""
        config_file = tmp_path / "Lightwave.uae"
        config_file.write_text("cpu_model=68040\n")

        with patch("amiberry_mcp.common.CONFIG_DIR", tmp_path):
            result = find_config_path("Lightwave.uae")

        assert result is not None
        assert result == config_file.resolve()

    def test_returns_none_for_missing_config(self, tmp_path):
        """None is returned when the file doesn't exist."""
        with patch("amiberry_mcp.common.CONFIG_DIR", tmp_path), \
             patch("amiberry_mcp.common.IS_LINUX", False), \
             patch("amiberry_mcp.common.SYSTEM_CONFIG_DIR", None):
            result = find_config_path("NonExistent.uae")

        assert result is None

    def test_finds_config_with_subdirectory(self, tmp_path):
        """Configs in subdirectories are not found (must be direct children)."""
        sub = tmp_path / "subdir"
        sub.mkdir()
        config_file = sub / "Deep.uae"
        config_file.write_text("cpu_model=68000\n")

        with patch("amiberry_mcp.common.CONFIG_DIR", tmp_path), \
             patch("amiberry_mcp.common.IS_LINUX", False), \
             patch("amiberry_mcp.common.SYSTEM_CONFIG_DIR", None):
            # Asking for "subdir/Deep.uae" should work (it's a relative path)
            result = find_config_path("subdir/Deep.uae")

        assert result is not None
        assert result == config_file.resolve()

    def test_rejects_path_traversal(self, tmp_path):
        """Path traversal attempts (../) are rejected."""
        # Create a file outside CONFIG_DIR
        outside = tmp_path / "outside"
        outside.mkdir()
        secret = outside / "secret.uae"
        secret.write_text("password=hunter2\n")

        config_dir = tmp_path / "configs"
        config_dir.mkdir()

        with patch("amiberry_mcp.common.CONFIG_DIR", config_dir), \
             patch("amiberry_mcp.common.IS_LINUX", False), \
             patch("amiberry_mcp.common.SYSTEM_CONFIG_DIR", None):
            result = find_config_path("../outside/secret.uae")

        assert result is None

    def test_linux_falls_back_to_system_config_dir(self, tmp_path):
        """On Linux, system config dir is checked as fallback."""
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        system_dir = tmp_path / "system"
        system_dir.mkdir()

        system_config = system_dir / "SystemConfig.uae"
        system_config.write_text("cpu_model=68000\n")

        with patch("amiberry_mcp.common.CONFIG_DIR", user_dir), \
             patch("amiberry_mcp.common.IS_LINUX", True), \
             patch("amiberry_mcp.common.SYSTEM_CONFIG_DIR", system_dir):
            result = find_config_path("SystemConfig.uae")

        assert result is not None
        assert result == system_config.resolve()

    def test_user_dir_takes_precedence_over_system(self, tmp_path):
        """User config dir is checked before system config dir."""
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        system_dir = tmp_path / "system"
        system_dir.mkdir()

        # Same filename in both dirs
        user_config = user_dir / "Shared.uae"
        user_config.write_text("from=user\n")
        system_config = system_dir / "Shared.uae"
        system_config.write_text("from=system\n")

        with patch("amiberry_mcp.common.CONFIG_DIR", user_dir), \
             patch("amiberry_mcp.common.IS_LINUX", True), \
             patch("amiberry_mcp.common.SYSTEM_CONFIG_DIR", system_dir):
            result = find_config_path("Shared.uae")

        assert result is not None
        assert result == user_config.resolve()


class TestAmiberryHomeMacOS:
    """Verify the macOS AMIBERRY_HOME points to the correct location."""

    def test_macos_home_is_application_support(self):
        """On macOS, AMIBERRY_HOME should be under Library/Application Support."""
        import amiberry_mcp.config as cfg

        if not cfg.IS_MACOS:
            pytest.skip("macOS-only test")

        expected = Path.home() / "Library" / "Application Support" / "Amiberry"
        assert cfg.AMIBERRY_HOME == expected

    def test_macos_config_dir_under_application_support(self):
        """On macOS, CONFIG_DIR should be under the Application Support path."""
        import amiberry_mcp.config as cfg

        if not cfg.IS_MACOS:
            pytest.skip("macOS-only test")

        assert "Application Support" in str(cfg.CONFIG_DIR)
        assert cfg.CONFIG_DIR == cfg.AMIBERRY_HOME / "Configurations"
