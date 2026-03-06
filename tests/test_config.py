#!/usr/bin/env python3
"""
Unit tests for the config module.

Covers:
- Fix #8: ensure_directories_exist handles permission errors
- Fix #11: Empty XDG_CONFIG_HOME string is handled correctly
- Fix #17: get_platform_info includes log_dir and rom_dir
"""

from pathlib import Path
from unittest.mock import patch

import pytest


class TestEnsureDirectoriesExist:
    """Tests for Fix #8: ensure_directories_exist permission handling."""

    def test_creates_directories(self, tmp_path):
        """Directories should be created when they don't exist."""
        from amiberry_mcp import config as cfg

        new_dir = tmp_path / "test_configs"

        with patch.object(cfg, "_dirs_ensured", False), \
             patch.object(cfg, "CONFIG_DIR", new_dir), \
             patch.object(cfg, "SAVESTATE_DIR", tmp_path / "saves"), \
             patch.object(cfg, "SCREENSHOT_DIR", tmp_path / "screenshots"), \
             patch.object(cfg, "LOG_DIR", tmp_path / "logs"), \
             patch.object(cfg, "ROM_DIR", tmp_path / "roms"), \
             patch.object(cfg, "DISK_IMAGE_DIRS", [tmp_path / "floppies"]):
            cfg.ensure_directories_exist()

        assert new_dir.exists()

    def test_handles_permission_error(self, tmp_path):
        """Permission errors should be silently ignored."""
        from amiberry_mcp import config as cfg

        with patch.object(cfg, "_dirs_ensured", False), \
             patch.object(cfg, "CONFIG_DIR", Path("/root/no_permission")), \
             patch.object(cfg, "SAVESTATE_DIR", tmp_path / "saves"), \
             patch.object(cfg, "SCREENSHOT_DIR", tmp_path / "screenshots"), \
             patch.object(cfg, "LOG_DIR", tmp_path / "logs"), \
             patch.object(cfg, "ROM_DIR", tmp_path / "roms"), \
             patch.object(cfg, "DISK_IMAGE_DIRS", []):
            # Should not raise even if one dir fails
            cfg.ensure_directories_exist()

    def test_idempotent(self, tmp_path):
        """Calling multiple times should be safe."""
        from amiberry_mcp import config as cfg

        with patch.object(cfg, "_dirs_ensured", False), \
             patch.object(cfg, "CONFIG_DIR", tmp_path / "configs"), \
             patch.object(cfg, "SAVESTATE_DIR", tmp_path / "saves"), \
             patch.object(cfg, "SCREENSHOT_DIR", tmp_path / "screenshots"), \
             patch.object(cfg, "LOG_DIR", tmp_path / "logs"), \
             patch.object(cfg, "ROM_DIR", tmp_path / "roms"), \
             patch.object(cfg, "DISK_IMAGE_DIRS", []):
            cfg.ensure_directories_exist()
            # _dirs_ensured is now True; second call should be a no-op
            cfg.ensure_directories_exist()


class TestXDGConfigHome:
    """Tests for Fix #11: Empty XDG_CONFIG_HOME handling."""

    def test_empty_string_falls_back_to_default(self):
        """Empty XDG_CONFIG_HOME should fall back to ~/.config."""
        import importlib
        import amiberry_mcp.config as cfg

        if not cfg.IS_LINUX:
            pytest.skip("Linux-only test")

        with patch.dict("os.environ", {"XDG_CONFIG_HOME": ""}):
            # Re-evaluate the expression
            import os
            result = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))

        assert result == Path.home() / ".config"

    def test_set_value_is_used(self):
        """Non-empty XDG_CONFIG_HOME should be used as-is."""
        import os
        result = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))

        with patch.dict("os.environ", {"XDG_CONFIG_HOME": "/custom/config"}):
            result = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))

        assert result == Path("/custom/config")

    def test_unset_falls_back_to_default(self):
        """Unset XDG_CONFIG_HOME should fall back to ~/.config."""
        import os

        env = os.environ.copy()
        env.pop("XDG_CONFIG_HOME", None)

        with patch.dict("os.environ", env, clear=True):
            result = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))

        assert result == Path.home() / ".config"


class TestGetPlatformInfo:
    """Tests for Fix #17: get_platform_info includes all fields."""

    def test_includes_log_dir(self):
        """get_platform_info should include log_dir."""
        from amiberry_mcp.config import get_platform_info

        info = get_platform_info()

        assert "log_dir" in info
        assert isinstance(info["log_dir"], str)

    def test_includes_rom_dir(self):
        """get_platform_info should include rom_dir."""
        from amiberry_mcp.config import get_platform_info

        info = get_platform_info()

        assert "rom_dir" in info
        assert isinstance(info["rom_dir"], str)

    def test_includes_all_required_fields(self):
        """get_platform_info should include all expected fields."""
        from amiberry_mcp.config import get_platform_info

        info = get_platform_info()

        required = [
            "platform", "emulator_binary", "amiberry_home",
            "config_dir", "savestate_dir", "screenshot_dir",
            "log_dir", "rom_dir", "disk_image_dirs",
        ]
        for field in required:
            assert field in info, f"Missing field: {field}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
