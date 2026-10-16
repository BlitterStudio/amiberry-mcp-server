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
        with (
            patch("amiberry_mcp.common.CONFIG_DIR", tmp_path),
            patch("amiberry_mcp.common.LEGACY_CONFIG_DIRS", []),
        ):
            result = find_config_path("NonExistent.uae")

        assert result is None

    def test_finds_config_with_subdirectory(self, tmp_path):
        """Configs in subdirectories are not found (must be direct children)."""
        sub = tmp_path / "subdir"
        sub.mkdir()
        config_file = sub / "Deep.uae"
        config_file.write_text("cpu_model=68000\n")

        with (
            patch("amiberry_mcp.common.CONFIG_DIR", tmp_path),
            patch("amiberry_mcp.common.LEGACY_CONFIG_DIRS", []),
        ):
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

        with (
            patch("amiberry_mcp.common.CONFIG_DIR", config_dir),
            patch("amiberry_mcp.common.LEGACY_CONFIG_DIRS", []),
        ):
            result = find_config_path("../outside/secret.uae")

        assert result is None

    def test_falls_back_to_legacy_conf_dir(self, tmp_path):
        """Pre-8.4 conf/ is searched when Configurations does not have the file."""
        user_dir = tmp_path / "Configurations"
        user_dir.mkdir()
        legacy_dir = tmp_path / "conf"
        legacy_dir.mkdir()
        legacy_config = legacy_dir / "SystemConfig.uae"
        legacy_config.write_text("cpu_model=68000\n")

        with (
            patch("amiberry_mcp.common.CONFIG_DIR", user_dir),
            patch("amiberry_mcp.common.LEGACY_CONFIG_DIRS", [legacy_dir]),
        ):
            result = find_config_path("SystemConfig.uae")

        assert result == legacy_config.resolve()

    def test_configurations_dir_takes_precedence_over_legacy(self, tmp_path):
        """Configurations is checked before the legacy conf directory."""
        user_dir = tmp_path / "Configurations"
        user_dir.mkdir()
        legacy_dir = tmp_path / "conf"
        legacy_dir.mkdir()
        user_config = user_dir / "Shared.uae"
        user_config.write_text("from=user\n")
        (legacy_dir / "Shared.uae").write_text("from=legacy\n")

        with (
            patch("amiberry_mcp.common.CONFIG_DIR", user_dir),
            patch("amiberry_mcp.common.LEGACY_CONFIG_DIRS", [legacy_dir]),
        ):
            result = find_config_path("Shared.uae")

        assert result == user_config.resolve()


class TestResolveAmiberryPaths:
    """Path formulas match Amiberry 8.4 for Linux, FreeBSD, and macOS."""

    def test_linux_defaults(self, tmp_path):
        from amiberry_mcp.config import resolve_amiberry_paths

        paths = resolve_amiberry_paths("Linux", {}, tmp_path)

        assert paths.home == tmp_path / "Amiberry"
        assert paths.config_dir == paths.home / "Configurations"
        assert paths.legacy_config_dirs == (paths.home / "conf",)
        assert paths.settings_dir == tmp_path / ".config" / "amiberry"
        assert paths.rom_dir == paths.home / "ROMs"
        assert paths.savestate_dir == paths.home / "SaveStates"
        assert paths.screenshot_dir == paths.home / "Screenshots"
        assert paths.cd_dir == paths.home / "CDROMs"
        assert paths.disk_image_dirs == (
            paths.home / "Floppies",
            paths.home / "HardDrives",
            paths.home / "LHA",
        )
        assert paths.portable is False

    def test_freebsd_matches_linux(self, tmp_path):
        from amiberry_mcp.config import resolve_amiberry_paths

        linux = resolve_amiberry_paths("Linux", {}, tmp_path)
        freebsd = resolve_amiberry_paths("FreeBSD", {}, tmp_path)

        assert freebsd == linux

    def test_posix_env_overrides(self, tmp_path):
        from amiberry_mcp.config import resolve_amiberry_paths

        environ = {
            "AMIBERRY_HOME_DIR": str(tmp_path / "custom-home"),
            "AMIBERRY_CONFIG_DIR": str(tmp_path / "custom-conf"),
            "XDG_CONFIG_HOME": "",
        }
        paths = resolve_amiberry_paths("Linux", environ, tmp_path)

        assert paths.home == tmp_path / "custom-home"
        assert paths.config_dir == tmp_path / "custom-conf"
        assert paths.settings_dir == tmp_path / ".config" / "amiberry"

    def test_macos_uses_documents_and_application_support(self, tmp_path):
        from amiberry_mcp.config import resolve_amiberry_paths

        paths = resolve_amiberry_paths("Darwin", {}, tmp_path)

        assert paths.home == tmp_path / "Documents" / "Amiberry"
        assert paths.config_dir == paths.home / "Configurations"
        assert paths.settings_dir == (
            tmp_path / "Library" / "Application Support" / "Amiberry"
        )
        assert paths.legacy_config_dirs == ()
        assert paths.rom_dir == paths.home / "ROMs"

    def test_portable_marker_overrides_home_env_on_linux(self, tmp_path):
        from amiberry_mcp.config import resolve_amiberry_paths

        binary = tmp_path / "bin" / "amiberry"
        binary.parent.mkdir()
        binary.write_text("")
        (binary.parent / "amiberry.portable").write_text("")

        paths = resolve_amiberry_paths(
            "Linux",
            {"AMIBERRY_HOME_DIR": str(tmp_path / "ignored")},
            tmp_path,
            binary,
        )

        assert paths.portable is True
        assert paths.home == binary.parent.resolve()
        assert paths.config_dir == paths.home / "Configurations"
        assert paths.settings_dir == paths.home / "Settings"

    def test_macos_ignores_portable_marker(self, tmp_path):
        from amiberry_mcp.config import resolve_amiberry_paths

        binary = tmp_path / "Amiberry"
        binary.write_text("")
        (tmp_path / "amiberry.portable").write_text("")

        paths = resolve_amiberry_paths("Darwin", {}, tmp_path, binary)

        assert paths.portable is False
        assert paths.home == tmp_path / "Documents" / "Amiberry"

    def test_unsupported_platform_raises(self, tmp_path):
        from amiberry_mcp.config import resolve_amiberry_paths

        with pytest.raises(RuntimeError, match="Unsupported platform"):
            resolve_amiberry_paths("Windows", {}, tmp_path)
