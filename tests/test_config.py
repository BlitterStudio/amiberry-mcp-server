#!/usr/bin/env python3
"""
Unit tests for the config module.

Covers:
- Fix #8: ensure_directories_exist handles permission errors
- Fix #11: Empty XDG_CONFIG_HOME string is handled correctly
- Fix #17: get_platform_info includes log_dir and rom_dir
"""

import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest


class TestEnsureDirectoriesExist:
    """Tests for Fix #8: ensure_directories_exist permission handling."""

    def test_creates_directories(self, tmp_path):
        """Directories should be created when they don't exist."""
        from amiberry_mcp import config as cfg

        new_dir = tmp_path / "test_configs"

        with (
            patch.object(cfg, "_dirs_ensured", False),
            patch.object(cfg, "CONFIG_DIR", new_dir),
            patch.object(cfg, "SAVESTATE_DIR", tmp_path / "saves"),
            patch.object(cfg, "SCREENSHOT_DIR", tmp_path / "screenshots"),
            patch.object(cfg, "LOG_DIR", tmp_path / "logs"),
            patch.object(cfg, "ROM_DIR", tmp_path / "roms"),
            patch.object(cfg, "DISK_IMAGE_DIRS", [tmp_path / "floppies"]),
        ):
            cfg.ensure_directories_exist()

        assert new_dir.exists()

    def test_handles_permission_error(self, tmp_path):
        """Permission errors should be silently ignored."""
        from amiberry_mcp import config as cfg

        with (
            patch.object(cfg, "_dirs_ensured", False),
            patch.object(cfg, "CONFIG_DIR", Path("/root/no_permission")),
            patch.object(cfg, "SAVESTATE_DIR", tmp_path / "saves"),
            patch.object(cfg, "SCREENSHOT_DIR", tmp_path / "screenshots"),
            patch.object(cfg, "LOG_DIR", tmp_path / "logs"),
            patch.object(cfg, "ROM_DIR", tmp_path / "roms"),
            patch.object(cfg, "DISK_IMAGE_DIRS", []),
        ):
            # Should not raise even if one dir fails
            cfg.ensure_directories_exist()

    def test_idempotent(self, tmp_path):
        """Calling multiple times should be safe."""
        from amiberry_mcp import config as cfg

        with (
            patch.object(cfg, "_dirs_ensured", False),
            patch.object(cfg, "CONFIG_DIR", tmp_path / "configs"),
            patch.object(cfg, "SAVESTATE_DIR", tmp_path / "saves"),
            patch.object(cfg, "SCREENSHOT_DIR", tmp_path / "screenshots"),
            patch.object(cfg, "LOG_DIR", tmp_path / "logs"),
            patch.object(cfg, "ROM_DIR", tmp_path / "roms"),
            patch.object(cfg, "DISK_IMAGE_DIRS", []),
        ):
            cfg.ensure_directories_exist()
            # _dirs_ensured is now True; second call should be a no-op
            cfg.ensure_directories_exist()


class TestXDGConfigHome:
    """Tests for Fix #11: Empty XDG_CONFIG_HOME handling."""

    def test_empty_string_falls_back_to_default(self):
        """Empty XDG_CONFIG_HOME should fall back to ~/.config."""
        import amiberry_mcp.config as cfg

        if not cfg.IS_LINUX:
            pytest.skip("Linux-only test")

        with patch.dict("os.environ", {"XDG_CONFIG_HOME": ""}):
            # Re-evaluate the expression
            import os

            result = Path(
                os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
            )

        assert result == Path.home() / ".config"

    def test_set_value_is_used(self):
        """Non-empty XDG_CONFIG_HOME should be used as-is."""
        import os

        result = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))

        with patch.dict("os.environ", {"XDG_CONFIG_HOME": "/custom/config"}):
            result = Path(
                os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
            )

        assert result == Path("/custom/config")

    def test_unset_falls_back_to_default(self):
        """Unset XDG_CONFIG_HOME should fall back to ~/.config."""
        import os

        env = os.environ.copy()
        env.pop("XDG_CONFIG_HOME", None)

        with patch.dict("os.environ", env, clear=True):
            result = Path(
                os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
            )

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
            "platform",
            "emulator_binary",
            "amiberry_home",
            "config_dir",
            "settings_dir",
            "savestate_dir",
            "screenshot_dir",
            "log_dir",
            "rom_dir",
            "cd_dir",
            "disk_image_dirs",
            "portable_mode",
        ]
        for field in required:
            assert field in info, f"Missing field: {field}"


def _make_fake_winreg(hkcu: str | None, hklm: str | None) -> types.ModuleType:
    """Build a winreg stand-in whose uninstall key reports install locations."""
    module = types.ModuleType("winreg")
    module.HKEY_CURRENT_USER = 0
    module.HKEY_LOCAL_MACHINE = 1
    locations = {module.HKEY_CURRENT_USER: hkcu, module.HKEY_LOCAL_MACHINE: hklm}

    class _Key:
        def __init__(self, location):
            self.location = location

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return None

    def _open_key(hive, subkey):
        assert subkey.endswith("amiberry_is1")
        if locations.get(hive) is None:
            raise OSError(2, "The system cannot find the file specified")
        return _Key(locations[hive])

    def _query_value_ex(key, value_name):
        assert value_name == "InstallLocation"
        return key.location, 1

    module.OpenKey = _open_key
    module.QueryValueEx = _query_value_ex
    return module


class TestResolveEmulatorBinaryOverride:
    """AMIBERRY_BINARY wins over every discovery step, on any platform."""

    def test_override_wins_on_windows(self):
        """The override beats PATH, the registry, and the default dirs."""
        from amiberry_mcp import config as cfg

        with patch.object(cfg.shutil, "which", return_value="C:/on/path/Amiberry.exe"):
            resolved = cfg.resolve_emulator_binary(
                "Windows", {"AMIBERRY_BINARY": "C:/custom/Amiberry.exe"}
            )

        assert resolved == "C:/custom/Amiberry.exe"

    def test_override_wins_on_posix(self):
        """The override beats an amiberry found on PATH."""
        from amiberry_mcp import config as cfg

        with patch.object(cfg.shutil, "which", return_value="/usr/bin/amiberry"):
            resolved = cfg.resolve_emulator_binary(
                "Linux", {"AMIBERRY_BINARY": "/opt/amiberry/amiberry"}
            )

        assert resolved == "/opt/amiberry/amiberry"

    def test_empty_override_is_ignored(self):
        """An empty override behaves as unset."""
        from amiberry_mcp import config as cfg

        with patch.object(cfg.shutil, "which", return_value=None):
            resolved = cfg.resolve_emulator_binary("Linux", {"AMIBERRY_BINARY": ""})

        assert resolved == "amiberry"


class TestWindowsBinaryDiscovery:
    """Windows resolution order: PATH, registry HKCU, registry HKLM, dirs."""

    def test_path_is_searched_first(self):
        """An Amiberry.exe on PATH beats the registry and default dirs."""
        from amiberry_mcp import config as cfg

        with (
            patch.object(cfg.shutil, "which", return_value="C:/tools/Amiberry.exe"),
            patch.object(cfg, "_windows_registry_binary", return_value=None),
        ):
            resolved = cfg.resolve_emulator_binary(
                "Windows", {"LOCALAPPDATA": "C:/Users/x/AppData/Local"}
            )

        assert resolved == "C:/tools/Amiberry.exe"

    def test_registry_hkcu_beats_hklm(self, tmp_path):
        """A per-user install is preferred over an all-users install."""
        from amiberry_mcp import config as cfg

        user_dir = tmp_path / "user"
        machine_dir = tmp_path / "machine"
        user_dir.mkdir()
        machine_dir.mkdir()
        (user_dir / "Amiberry.exe").touch()
        (machine_dir / "Amiberry.exe").touch()
        fake = _make_fake_winreg(str(user_dir), str(machine_dir))

        with (
            patch.dict(sys.modules, {"winreg": fake}),
            patch.object(cfg.shutil, "which", return_value=None),
        ):
            resolved = cfg.resolve_emulator_binary("Windows", {})

        assert resolved == str(user_dir / "Amiberry.exe")

    def test_registry_hklm_used_when_hkcu_missing(self, tmp_path):
        """An all-users install is found when there is no per-user one."""
        from amiberry_mcp import config as cfg

        machine_dir = tmp_path / "machine"
        machine_dir.mkdir()
        (machine_dir / "Amiberry.exe").touch()
        fake = _make_fake_winreg(None, str(machine_dir))

        with (
            patch.dict(sys.modules, {"winreg": fake}),
            patch.object(cfg.shutil, "which", return_value=None),
        ):
            resolved = cfg.resolve_emulator_binary("Windows", {})

        assert resolved == str(machine_dir / "Amiberry.exe")

    def test_registry_location_without_binary_is_skipped(self, tmp_path):
        """An InstallLocation without Amiberry.exe falls through to the dirs."""
        from amiberry_mcp import config as cfg

        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        install = tmp_path / "Programs" / "amiberry"
        install.mkdir(parents=True)
        (install / "Amiberry.exe").touch()
        fake = _make_fake_winreg(str(empty_dir), None)

        with (
            patch.dict(sys.modules, {"winreg": fake}),
            patch.object(cfg.shutil, "which", return_value=None),
        ):
            resolved = cfg.resolve_emulator_binary(
                "Windows", {"LOCALAPPDATA": str(tmp_path)}
            )

        assert resolved == str(install / "Amiberry.exe")

    def test_registry_tolerates_missing_winreg(self):
        """A missing winreg module is not an error."""
        from amiberry_mcp import config as cfg

        with patch.dict(sys.modules, {"winreg": None}):
            assert cfg._windows_registry_binary() is None

    def test_localappdata_fallback(self, tmp_path):
        """The per-user default install directory is checked."""
        from amiberry_mcp import config as cfg

        install = tmp_path / "Programs" / "amiberry"
        install.mkdir(parents=True)
        (install / "Amiberry.exe").touch()

        with (
            patch.object(cfg.shutil, "which", return_value=None),
            patch.object(cfg, "_windows_registry_binary", return_value=None),
        ):
            resolved = cfg.resolve_emulator_binary(
                "Windows",
                {"LOCALAPPDATA": str(tmp_path), "ProgramFiles": str(tmp_path / "pf")},
            )

        assert resolved == str(install / "Amiberry.exe")

    def test_program_files_fallback(self, tmp_path):
        """The all-users default install directory is checked last."""
        from amiberry_mcp import config as cfg

        install = tmp_path / "pf" / "amiberry"
        install.mkdir(parents=True)
        (install / "Amiberry.exe").touch()

        with (
            patch.object(cfg.shutil, "which", return_value=None),
            patch.object(cfg, "_windows_registry_binary", return_value=None),
        ):
            resolved = cfg.resolve_emulator_binary(
                "Windows", {"ProgramFiles": str(tmp_path / "pf")}
            )

        assert resolved == str(install / "Amiberry.exe")

    def test_missing_everywhere_keeps_bare_name(self):
        """With no discovery hit the bare name keeps exec-time PATH lookup."""
        from amiberry_mcp import config as cfg

        with (
            patch.object(cfg.shutil, "which", return_value=None),
            patch.object(cfg, "_windows_registry_binary", return_value=None),
        ):
            resolved = cfg.resolve_emulator_binary("Windows", {})

        assert resolved == "Amiberry.exe"


class TestPlatformBinaryDefaults:
    """macOS and Linux/FreeBSD defaults are unchanged apart from the override."""

    def test_macos_app_bundle(self):
        """macOS keeps the /Applications app bundle path."""
        from amiberry_mcp import config as cfg

        resolved = cfg.resolve_emulator_binary("Darwin", {})

        assert resolved == "/Applications/Amiberry.app/Contents/MacOS/Amiberry"

    def test_linux_resolves_through_which(self):
        """Linux uses the amiberry found on PATH."""
        from amiberry_mcp import config as cfg

        with patch.object(cfg.shutil, "which", return_value="/usr/bin/amiberry"):
            resolved = cfg.resolve_emulator_binary("Linux", {})

        assert resolved == "/usr/bin/amiberry"

    def test_linux_missing_falls_back_to_name(self):
        """Without a PATH hit the bare name is kept."""
        from amiberry_mcp import config as cfg

        with patch.object(cfg.shutil, "which", return_value=None):
            resolved = cfg.resolve_emulator_binary("FreeBSD", {})

        assert resolved == "amiberry"


class TestEmulatorBinaryWiring:
    """The module constant and consumers use the resolver's result."""

    def test_module_constant_comes_from_resolver(self):
        """EMULATOR_BINARY is the resolver's output for this platform."""
        from amiberry_mcp import config as cfg

        assert cfg.EMULATOR_BINARY == cfg.resolve_emulator_binary(
            cfg._PLATFORM, os.environ
        )

    def test_platform_info_reports_resolved_binary(self):
        """get_platform_info exposes the resolved path."""
        from amiberry_mcp import config as cfg

        info = cfg.get_platform_info()

        assert info["emulator_binary"] == str(cfg.EMULATOR_BINARY)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
