#!/usr/bin/env python3
"""
Unit tests for the common module.

Covers:
- Fix #6: terminate_process handles ProcessLookupError
- Fix #18: classify_image_type handles unknown extensions
"""

import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from amiberry_mcp.common import (
    _popen_session_kwargs,
    classify_image_type,
    disk_search_dirs,
    iter_files_by_suffix,
    launch_process,
    rom_search_dirs,
    savestate_search_dirs,
    scan_disk_images,
    terminate_process,
    unique_existing_dirs,
)


class TestTerminateProcess:
    """Tests for Fix #6: terminate_process ProcessLookupError handling."""

    def test_normal_termination(self):
        """Process that terminates cleanly."""
        proc = MagicMock(spec=subprocess.Popen)
        proc.terminate = MagicMock()
        proc.wait = MagicMock()

        terminate_process(proc, timeout=1.0)

        proc.terminate.assert_called_once()
        proc.wait.assert_called_once_with(timeout=1.0)
        proc.kill.assert_not_called()

    def test_process_already_exited_on_terminate(self):
        """ProcessLookupError on terminate should not raise."""
        proc = MagicMock(spec=subprocess.Popen)
        proc.terminate = MagicMock(side_effect=ProcessLookupError)

        # Should not raise
        terminate_process(proc, timeout=1.0)

        proc.terminate.assert_called_once()
        # wait and kill should not be called
        proc.wait.assert_not_called()

    def test_fallback_to_kill_on_timeout(self):
        """If terminate times out, kill should be called."""
        proc = MagicMock(spec=subprocess.Popen)
        proc.terminate = MagicMock()
        proc.wait = MagicMock(side_effect=[subprocess.TimeoutExpired("cmd", 1.0), None])
        proc.kill = MagicMock()

        terminate_process(proc, timeout=1.0)

        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()
        assert proc.wait.call_count == 2

    def test_process_exits_between_terminate_and_kill(self):
        """ProcessLookupError on kill should not raise."""
        proc = MagicMock(spec=subprocess.Popen)
        proc.terminate = MagicMock()
        proc.wait = MagicMock(side_effect=subprocess.TimeoutExpired("cmd", 1.0))
        proc.kill = MagicMock(side_effect=ProcessLookupError)

        # Should not raise
        terminate_process(proc, timeout=1.0)

        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()

    def test_kill_also_times_out(self):
        """If both terminate and kill time out, should not raise."""
        proc = MagicMock(spec=subprocess.Popen)
        proc.terminate = MagicMock()
        proc.wait = MagicMock(side_effect=subprocess.TimeoutExpired("cmd", 1.0))
        proc.kill = MagicMock()

        # Should not raise even if kill also times out
        terminate_process(proc, timeout=1.0)


class TestClassifyImageType:
    """Tests for Fix #18: classify_image_type with unknown extensions."""

    def test_floppy_extensions(self):
        """Floppy disk extensions should be classified correctly."""
        assert classify_image_type(".adf") == "floppy"
        assert classify_image_type(".adz") == "floppy"
        assert classify_image_type(".dms") == "floppy"

    def test_hardfile_extensions(self):
        """Hard drive image extensions should be classified correctly."""
        assert classify_image_type(".hdf") == "hardfile"
        assert classify_image_type(".hdz") == "hardfile"

    def test_lha_extensions(self):
        """LHA archive extensions should be classified correctly."""
        assert classify_image_type(".lha") == "lha"

    def test_cd_extensions(self):
        """CD image extensions should be classified correctly."""
        assert classify_image_type(".iso") == "cd"
        assert classify_image_type(".cue") == "cd"
        assert classify_image_type(".chd") == "cd"
        assert classify_image_type(".bin") == "cd"
        assert classify_image_type(".nrg") == "cd"

    def test_unknown_extension_returns_unknown(self):
        """Unknown extensions should return 'unknown', not 'hardfile'."""
        assert classify_image_type(".zip") == "unknown"
        assert classify_image_type(".txt") == "unknown"
        assert classify_image_type(".exe") == "unknown"
        assert classify_image_type("") == "unknown"

    def test_case_insensitive(self):
        """Classification should be case-insensitive."""
        assert classify_image_type(".ADF") == "floppy"
        assert classify_image_type(".HDF") == "hardfile"
        assert classify_image_type(".LHA") == "lha"
        assert classify_image_type(".ISO") == "cd"


class TestLegacyContentSearch:
    """Pre-8.4 content trees are searched, without listing one file twice."""

    def test_legacy_rom_and_savestate_dirs_are_searched(self, tmp_path):
        kickstarts = tmp_path / "kickstarts"
        kickstarts.mkdir()
        (kickstarts / "kick.rom").write_bytes(b"rom")
        saves = tmp_path / "savestates"
        saves.mkdir()
        (saves / "game.uss").write_bytes(b"uss")

        with (
            patch("amiberry_mcp.common.ROM_DIR", tmp_path / "ROMs"),
            patch("amiberry_mcp.common.LEGACY_ROM_DIRS", [kickstarts]),
            patch("amiberry_mcp.common.SAVESTATE_DIR", tmp_path / "SaveStates"),
            patch("amiberry_mcp.common.LEGACY_SAVESTATE_DIRS", [saves]),
        ):
            roms = iter_files_by_suffix(rom_search_dirs(), (".rom",))
            states = iter_files_by_suffix(savestate_search_dirs(), (".uss",))

        assert [path.name for path in roms] == ["kick.rom"]
        assert [path.name for path in states] == ["game.uss"]

    def test_legacy_disk_dirs_are_scanned(self, tmp_path):
        floppies = tmp_path / "floppies"
        floppies.mkdir()
        (floppies / "game.adf").write_bytes(b"adf")

        with (
            patch("amiberry_mcp.common.DISK_IMAGE_DIRS", [tmp_path / "Floppies"]),
            patch("amiberry_mcp.common.LEGACY_DISK_DIRS", [floppies]),
        ):
            images = scan_disk_images(disk_search_dirs(), "floppy")

        assert [image["name"] for image in images] == ["game.adf"]

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="needs a case-sensitive filesystem and symlink privilege",
    )
    def test_same_inode_is_listed_once(self, tmp_path):
        real = tmp_path / "Floppies"
        real.mkdir()
        (real / "game.adf").write_bytes(b"adf")
        alias = tmp_path / "floppies"
        alias.symlink_to(real, target_is_directory=True)

        images = scan_disk_images([real, alias], "floppy")

        assert [image["name"] for image in images] == ["game.adf"]
        assert len(unique_existing_dirs([real, alias])) == 1


class TestLaunchProcessSessionFlags:
    """launch_process only passes session flags the platform supports."""

    def test_unix_detaches_with_start_new_session(self):
        from amiberry_mcp import common

        proc = MagicMock()
        with (
            patch.object(common, "IS_WINDOWS", False),
            patch("subprocess.Popen", return_value=proc) as popen,
        ):
            launch_process(["amiberry"])

        assert popen.call_args.kwargs.get("start_new_session") is True

    def test_windows_omits_start_new_session(self):
        from amiberry_mcp import common

        proc = MagicMock()
        with (
            patch.object(common, "IS_WINDOWS", True),
            patch("subprocess.Popen", return_value=proc) as popen,
        ):
            launch_process(["Amiberry.exe"])

        assert "start_new_session" not in popen.call_args.kwargs

    def test_platform_flags_follow_the_host(self):
        assert _popen_session_kwargs() == (
            {} if sys.platform == "win32" else {"start_new_session": True}
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
