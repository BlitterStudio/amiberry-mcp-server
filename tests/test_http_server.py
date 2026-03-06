#!/usr/bin/env python3
"""
Unit tests for the http_server module.

Covers:
- Fix #2: CORS restricted to localhost, bind to 127.0.0.1
- Fix #3: Path traversal prevention on HTTP endpoints
- Fix #10: Specific pgrep pattern, tracked process preferred
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from amiberry_mcp.common import _is_path_within


class TestCORSConfiguration:
    """Tests for Fix #2: CORS and bind address."""

    def test_cors_rejects_wildcard(self):
        """CORS should not allow all origins."""
        from amiberry_mcp import http_server

        # Check the middleware was configured with restrictive origins
        for mw in http_server.app.user_middleware:
            if hasattr(mw, "kwargs") and "allow_origins" in mw.kwargs:
                origins = mw.kwargs["allow_origins"]
                assert "*" not in origins, "CORS should not allow wildcard origin"

    def test_cors_allows_localhost(self):
        """CORS should allow localhost origins."""
        from amiberry_mcp import http_server

        for mw in http_server.app.user_middleware:
            if hasattr(mw, "kwargs") and "allow_origins" in mw.kwargs:
                origins = mw.kwargs["allow_origins"]
                assert any("localhost" in o for o in origins)
                assert any("127.0.0.1" in o for o in origins)

    def test_credentials_disabled(self):
        """CORS credentials should be disabled."""
        from amiberry_mcp import http_server

        for mw in http_server.app.user_middleware:
            if hasattr(mw, "kwargs") and "allow_credentials" in mw.kwargs:
                assert mw.kwargs["allow_credentials"] is False


class TestPathTraversal:
    """Tests for Fix #3: Path traversal prevention."""

    def test_is_path_within_rejects_traversal(self, tmp_path):
        """Paths outside parent should be rejected."""
        parent = tmp_path / "safe"
        parent.mkdir()

        outside = (tmp_path / "outside" / "secret.txt").resolve()
        assert not _is_path_within(outside, parent)

    def test_is_path_within_accepts_child(self, tmp_path):
        """Paths within parent should be accepted."""
        parent = tmp_path / "safe"
        parent.mkdir()
        child = parent / "file.txt"
        child.touch()

        assert _is_path_within(child, parent)

    def test_is_path_within_rejects_dotdot(self, tmp_path):
        """Path traversal with .. should be rejected."""
        parent = tmp_path / "safe"
        parent.mkdir()

        traversal = (parent / ".." / "outside").resolve()
        assert not _is_path_within(traversal, parent)

    def test_savestate_path_traversal(self):
        """Savestate endpoint should reject path traversal names."""
        from amiberry_mcp.config import SAVESTATE_DIR

        # Simulate what the endpoint does
        malicious_name = "../../../etc/passwd"
        path = (SAVESTATE_DIR / malicious_name).resolve()
        assert not _is_path_within(path, SAVESTATE_DIR)

    def test_rom_directory_traversal(self):
        """ROM endpoint should reject directories outside AMIBERRY_HOME."""
        from amiberry_mcp.config import AMIBERRY_HOME

        malicious_dir = "/etc"
        candidate = Path(malicious_dir).resolve()
        assert not _is_path_within(candidate, AMIBERRY_HOME)


class TestPgrepPattern:
    """Tests for Fix #10: Specific pgrep/pkill pattern."""

    def test_macos_uses_specific_pattern(self):
        """On macOS, pgrep pattern should be specific to the binary path."""
        from amiberry_mcp import http_server

        if not http_server.IS_MACOS:
            pytest.skip("macOS-only test")

        assert "Amiberry.app/Contents/MacOS/Amiberry" in http_server._PGREP_ARGS[-1]

    def test_stop_prefers_tracked_process(self):
        """_stop_amiberry should try tracked process first."""
        from amiberry_mcp.http_server import _state, _stop_amiberry

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # Process is running

        original_process = _state.process
        try:
            _state.process = mock_proc

            with patch("amiberry_mcp.http_server.terminate_process") as mock_term:
                result = _stop_amiberry()

                assert result is True
                mock_term.assert_called_once_with(mock_proc)
        finally:
            _state.process = original_process


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
