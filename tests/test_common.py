#!/usr/bin/env python3
"""
Unit tests for the common module.

Covers:
- Fix #6: terminate_process handles ProcessLookupError
- Fix #18: classify_image_type handles unknown extensions
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from amiberry_mcp.common import classify_image_type, terminate_process


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
