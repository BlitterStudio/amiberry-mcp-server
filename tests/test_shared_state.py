#!/usr/bin/env python3
"""
Unit tests for the shared_state module.

Covers:
- ProcessState dataclass behaviour
- get_ipc_client caching
- launch_and_store state management
- State lock availability
"""

import asyncio
import platform
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

# Patch platform detection before importing modules that depend on config.py,
# so the test suite runs on any OS (the project targets macOS/Linux only).
if platform.system() not in ("Darwin", "Linux"):
    _cfg = "amiberry_mcp.config"
    if _cfg not in sys.modules:
        from types import ModuleType

        _mod = ModuleType(_cfg)
        _mod.__dict__.update(  # type: ignore[attr-defined]
            IS_MACOS=False,
            IS_LINUX=True,
            EMULATOR_BINARY="amiberry",
        )
        from pathlib import Path as _P

        _home = _P.home() / "Amiberry"
        for _attr, _val in {
            "AMIBERRY_HOME": _home,
            "CONFIG_DIR": _home / "conf",
            "SYSTEM_CONFIG_DIR": None,
            "SAVESTATE_DIR": _home / "savestates",
            "SCREENSHOT_DIR": _home / "screenshots",
            "LOG_DIR": _home / "logs",
            "ROM_DIR": _home / "kickstarts",
            "DISK_IMAGE_DIRS": [_home / "floppies"],
            "FLOPPY_EXTENSIONS": [".adf"],
            "HARDFILE_EXTENSIONS": [".hdf"],
            "LHA_EXTENSIONS": [".lha"],
            "CD_EXTENSIONS": [".iso"],
            "SUPPORTED_MODELS": ["A500", "A1200", "CD32"],
        }.items():
            setattr(_mod, _attr, _val)

        def _ensure_directories_exist() -> None:
            pass

        def _get_platform_info() -> dict:
            return {}

        _mod.ensure_directories_exist = _ensure_directories_exist  # type: ignore[attr-defined]
        _mod.get_platform_info = _get_platform_info  # type: ignore[attr-defined]
        sys.modules[_cfg] = _mod

from amiberry_mcp.shared_state import (
    ProcessState,
    get_ipc_client,
    get_state,
    get_state_lock,
    launch_and_store,
)


class TestProcessState:
    """Tests for the ProcessState dataclass."""

    def test_defaults(self):
        """All fields should default to None/empty."""
        state = ProcessState()
        assert state.process is None
        assert state.launch_cmd is None
        assert state.log_path is None
        assert state.log_file_handle is None
        assert state.log_read_positions == {}
        assert state.active_instance is None
        assert state.ipc_client_cache is None

    def test_close_log_handle_when_open(self):
        """close_log_handle should close and clear the handle."""
        mock_handle = MagicMock()
        state = ProcessState(log_file_handle=mock_handle)

        state.close_log_handle()

        mock_handle.close.assert_called_once()
        assert state.log_file_handle is None

    def test_close_log_handle_when_none(self):
        """close_log_handle should be a no-op when handle is None."""
        state = ProcessState()
        state.close_log_handle()  # Should not raise
        assert state.log_file_handle is None

    def test_close_log_handle_swallows_oserror(self):
        """close_log_handle should swallow OSError from close."""
        mock_handle = MagicMock()
        mock_handle.close.side_effect = OSError("Permission denied")
        state = ProcessState(log_file_handle=mock_handle)

        state.close_log_handle()  # Should not raise

        assert state.log_file_handle is None


class TestGetState:
    """Tests for the module-level state singleton."""

    def test_returns_process_state(self):
        """get_state should return a ProcessState instance."""
        state = get_state()
        assert isinstance(state, ProcessState)

    def test_returns_same_instance(self):
        """get_state should always return the same instance."""
        assert get_state() is get_state()


class TestGetStateLock:
    """Tests for the module-level state lock."""

    def test_returns_asyncio_lock(self):
        """get_state_lock should return an asyncio.Lock."""
        lock = get_state_lock()
        assert isinstance(lock, asyncio.Lock)

    def test_returns_same_instance(self):
        """get_state_lock should always return the same instance."""
        assert get_state_lock() is get_state_lock()


class TestGetIpcClient:
    """Tests for IPC client caching."""

    def test_creates_new_client(self):
        """Should create a new client when cache is empty."""
        state = ProcessState()
        with patch("amiberry_mcp.shared_state.AmiberryIPCClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            client = get_ipc_client(state)

            mock_cls.assert_called_once_with(prefer_dbus=False, instance=None)
            assert client is mock_cls.return_value

    def test_returns_cached_client(self):
        """Should return the cached client for the same instance."""
        state = ProcessState()
        with patch("amiberry_mcp.shared_state.AmiberryIPCClient") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client

            client1 = get_ipc_client(state)
            client2 = get_ipc_client(state)

            # Should only create one client
            mock_cls.assert_called_once()
            assert client1 is client2

    def test_creates_new_client_on_instance_change(self):
        """Should create a new client when active_instance changes."""
        state = ProcessState()
        with patch("amiberry_mcp.shared_state.AmiberryIPCClient") as mock_cls:
            mock_cls.return_value = MagicMock()

            get_ipc_client(state)
            state.active_instance = 1
            get_ipc_client(state)

            assert mock_cls.call_count == 2

    def test_uses_module_state_by_default(self):
        """Should use module-level state when no explicit state given."""
        with patch("amiberry_mcp.shared_state.AmiberryIPCClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            # This should not raise — it uses the module-level _state
            client = get_ipc_client()
            assert client is not None


class TestLaunchAndStore:
    """Tests for launch_and_store."""

    def test_stores_process_and_command(self):
        """Should store process, command, and log path in state."""
        state = ProcessState()
        mock_proc = MagicMock(spec=subprocess.Popen)
        cmd = ["amiberry", "--model", "A500"]

        with patch("amiberry_mcp.shared_state.launch_process") as mock_launch:
            mock_launch.return_value = (mock_proc, None)

            result = launch_and_store(cmd, state=state)

            assert result is mock_proc
            assert state.process is mock_proc
            assert state.launch_cmd == cmd
            assert state.log_path is None
            assert state.log_file_handle is None

    def test_stores_log_handle(self, tmp_path):
        """Should store the log file handle when log_path is given."""
        state = ProcessState()
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_log = MagicMock()
        cmd = ["amiberry"]
        log_path = tmp_path / "test.log"

        with patch("amiberry_mcp.shared_state.launch_process") as mock_launch:
            mock_launch.return_value = (mock_proc, mock_log)

            launch_and_store(cmd, log_path=log_path, state=state)

            assert state.log_path == log_path
            assert state.log_file_handle is mock_log

    def test_closes_existing_log_handle(self):
        """Should close existing log handle before launching."""
        old_handle = MagicMock()
        state = ProcessState(log_file_handle=old_handle)
        mock_proc = MagicMock(spec=subprocess.Popen)
        cmd = ["amiberry"]

        with patch("amiberry_mcp.shared_state.launch_process") as mock_launch:
            mock_launch.return_value = (mock_proc, None)

            launch_and_store(cmd, state=state)

            old_handle.close.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
