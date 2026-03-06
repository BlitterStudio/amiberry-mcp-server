#!/usr/bin/env python3
"""
Unit tests for server.py fixes.

Covers:
- Fix #4: _launch_and_store helper centralizes launch pattern
- Fix #16: Warp mode uses explicit enable/disable strings
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestLaunchAndStore:
    """Tests for Fix #4: _launch_and_store helper."""

    def test_stores_process_and_state(self):
        """_launch_and_store should store process, cmd, and log_path in _state."""
        from amiberry_mcp.server import _launch_and_store, _state

        mock_proc = MagicMock()
        mock_log = MagicMock()
        cmd = ["/usr/bin/amiberry", "-G"]

        with patch("amiberry_mcp.server.launch_process", return_value=(mock_proc, mock_log)):
            with patch.object(_state, "close_log_handle"):
                result = _launch_and_store(cmd, log_path=Path("/tmp/test.log"))

        assert result is mock_proc
        assert _state.process is mock_proc
        assert _state.launch_cmd == cmd
        assert _state.log_path == Path("/tmp/test.log")
        assert _state.log_file_handle is mock_log

    def test_closes_previous_log_handle(self):
        """_launch_and_store should close the previous log handle first."""
        from amiberry_mcp.server import _launch_and_store, _state

        mock_proc = MagicMock()

        with patch("amiberry_mcp.server.launch_process", return_value=(mock_proc, None)), \
             patch.object(_state, "close_log_handle") as mock_close:
            _launch_and_store(["/usr/bin/amiberry"])

        mock_close.assert_called_once()


class TestWarpModeStrings:
    """Tests for Fix #16: Warp mode status/action strings."""

    @pytest.mark.asyncio
    async def test_warp_enable_success_message(self):
        """Enabling warp should say 'enabled', not use string slicing."""
        from amiberry_mcp.server import _handle_runtime_set_warp

        mock_client = MagicMock()
        mock_client.set_warp = MagicMock(return_value=True)

        # Make set_warp async
        import asyncio

        async def mock_set_warp(enabled):
            return True

        mock_client.set_warp = mock_set_warp

        with patch("amiberry_mcp.server._ipc_call") as mock_ipc:
            # Capture the callback to test it
            async def capture_cb(cb):
                result = await cb(mock_client)
                return [MagicMock(type="text", text=result)]

            mock_ipc.side_effect = capture_cb

            result = await _handle_runtime_set_warp({"enabled": True})

            assert any("enabled" in str(r.text) for r in result)

    @pytest.mark.asyncio
    async def test_warp_disable_success_message(self):
        """Disabling warp should say 'disabled', not use string slicing."""
        from amiberry_mcp.server import _handle_runtime_set_warp

        mock_client = MagicMock()

        async def mock_set_warp(enabled):
            return True

        mock_client.set_warp = mock_set_warp

        with patch("amiberry_mcp.server._ipc_call") as mock_ipc:
            async def capture_cb(cb):
                result = await cb(mock_client)
                return [MagicMock(type="text", text=result)]

            mock_ipc.side_effect = capture_cb

            result = await _handle_runtime_set_warp({"enabled": False})

            assert any("disabled" in str(r.text) for r in result)

    @pytest.mark.asyncio
    async def test_warp_enable_failure_message(self):
        """Failed enable should say 'enable', not use string slicing."""
        from amiberry_mcp.server import _handle_runtime_set_warp

        mock_client = MagicMock()

        async def mock_set_warp(enabled):
            return False

        mock_client.set_warp = mock_set_warp

        with patch("amiberry_mcp.server._ipc_call") as mock_ipc:
            async def capture_cb(cb):
                result = await cb(mock_client)
                return [MagicMock(type="text", text=result)]

            mock_ipc.side_effect = capture_cb

            result = await _handle_runtime_set_warp({"enabled": True})

            assert any("enable" in str(r.text) for r in result)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
