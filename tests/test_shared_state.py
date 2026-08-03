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
from pathlib import Path
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

from amiberry_mcp.ipc_client import (
    ActionableCaptureGeometry,
    DisplayMode,
    MouseUntrapMode,
    Renderer,
    TabletMode,
)
from amiberry_mcp.shared_state import (
    CAPTURE_REGISTRY_LIMIT,
    CAPTURE_TOMBSTONE_LIMIT,
    REQUEST_IN_FLIGHT_LIMIT,
    REQUEST_LEDGER_LIMIT,
    AutomationBusyError,
    CaptureRecord,
    DirtyOwnership,
    MutationImpact,
    ProcessState,
    coordinated_runtime_operation,
    get_ipc_client,
    get_state,
    get_state_lock,
    launch_and_store,
    mutation_impact_for,
    pin_active_endpoint,
)


def _capture_geometry(path: str = "/tmp/capture.png") -> ActionableCaptureGeometry:
    """Return valid geometry for controller-state tests."""
    return ActionableCaptureGeometry(
        schema_version=1,
        path=path,
        runtime_id="runtime-a",
        capture_nonce="nonce-a",
        geometry_revision=1,
        monitor_id=0,
        display_mode=DisplayMode.NATIVE,
        renderer=Renderer.SDL,
        image_width=8,
        image_height=6,
        source_x=0,
        source_y=0,
        source_width=8,
        source_height=6,
        viewport_x=0,
        viewport_y=0,
        viewport_width=8,
        viewport_height=6,
        window_width=8,
        window_height=6,
    )


def _capture_record(state: ProcessState, capture_id: str) -> CaptureRecord:
    """Return a record bound to the state's active abstract endpoint."""
    return CaptureRecord(
        capture_id,
        state.controller_id,
        state.active_endpoint,
        Path("/tmp/capture.png"),
        _capture_geometry(),
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
        assert state.controller_id.startswith("ctrl_")
        assert state.captures == {}
        assert state.request_active == {}

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


class TestControllerCaptureRegistry:
    """Tests bounded captures, tombstones, and route/controller identity."""

    def test_eviction_is_bounded_and_distinguishable(self):
        state = ProcessState()
        capture_ids = []
        for _index in range(CAPTURE_REGISTRY_LIMIT + CAPTURE_TOMBSTONE_LIMIT + 1):
            capture_id = state.new_capture_id()
            capture_ids.append(capture_id)
            state.register_capture(_capture_record(state, capture_id))

        assert len(state.captures) == CAPTURE_REGISTRY_LIMIT
        assert len(state.capture_tombstones) == CAPTURE_TOMBSTONE_LIMIT
        assert state.resolve_capture(capture_ids[0], state.active_endpoint).status == (
            "capture_unknown"
        )
        assert state.resolve_capture(
            capture_ids[CAPTURE_TOMBSTONE_LIMIT], state.active_endpoint
        ).status == ("capture_evicted")

    def test_same_controller_missing_is_unknown_but_foreign_is_wrong_controller(self):
        state = ProcessState()

        assert (
            state.resolve_capture(
                f"{state.controller_id}:cap_missing", state.active_endpoint
            ).status
            == "capture_unknown"
        )
        assert (
            state.resolve_capture(
                "ctrl_foreign:cap_missing", state.active_endpoint
            ).status
            == "capture_wrong_controller"
        )

    @pytest.mark.asyncio
    async def test_instance_switch_retains_endpoint_bound_capture(self):
        state = ProcessState(active_instance=0)
        capture_id = state.new_capture_id()
        state.register_capture(_capture_record(state, capture_id))

        await state.select_instance(1)

        assert state.resolve_capture(capture_id, state.active_endpoint).status == (
            "capture_wrong_instance"
        )


class TestControllerRequestLedger:
    """Tests bounded admission and replay-safe retention."""

    def test_in_flight_entries_are_never_evicted(self):
        state = ProcessState()
        for index in range(REQUEST_IN_FLIGHT_LIMIT):
            assert (
                state.reserve_request(f"req-{index}", str(index)).status == "reserved"
            )

        assert state.reserve_request("overflow", "payload").status == "busy"
        assert len(state.request_active) == REQUEST_IN_FLIGHT_LIMIT

    def test_retryable_not_started_reservation_can_execute_later(self):
        state = ProcessState()
        state.reserve_request("retry", "payload")

        state.finish_request("retry", object(), retain_terminal=False)

        assert state.reserve_request("retry", "payload").status == "reserved"

    def test_terminal_results_are_bounded_without_touching_active_entries(self):
        state = ProcessState()
        for index in range(REQUEST_LEDGER_LIMIT + 1):
            request_id = f"terminal-{index}"
            state.reserve_request(request_id, str(index))
            state.finish_request(request_id, index)
        active = state.reserve_request("still-active", "payload").entry

        assert len(state.request_terminal) == REQUEST_LEDGER_LIMIT
        assert "terminal-0" not in state.request_terminal
        assert active is state.request_active["still-active"]

    def test_unexpected_exit_marks_only_bound_endpoint_unknown(self):
        state = ProcessState(active_instance=0)
        first = state.reserve_request("first", "one").entry
        second = state.reserve_request("second", "two").entry
        assert first is not None and second is not None
        first.endpoint = "instance:0"
        second.endpoint = "instance:1"

        state.mark_active_requests_outcome_unknown("instance:0")

        assert first.state == "outcome_unknown"
        assert second.state == "in_progress"


class TestCoordinator:
    """Tests bounded lock acquisition and mutation classification."""

    @pytest.mark.asyncio
    async def test_routing_contention_returns_busy_within_total_timeout(self):
        state = ProcessState()
        await state.routing_lock.acquire()
        try:
            with pytest.raises(AutomationBusyError):
                async with pin_active_endpoint(state, timeout=0.001):
                    pass
        finally:
            state.routing_lock.release()

    @pytest.mark.asyncio
    async def test_expected_endpoint_reset_invalidates_runtime_state(self):
        state = ProcessState(active_instance=0)
        capture_id = state.new_capture_id()
        state.register_capture(_capture_record(state, capture_id))
        state.dirty_ownership[state.active_endpoint] = DirtyOwnership(
            state.active_endpoint,
            "runtime-a",
            TabletMode.OFF,
            MouseUntrapMode.OFF,
            TabletMode.MOUSEHACK,
            MouseUntrapMode.MAGIC,
            2,
        )

        await state.reset_endpoint(0)

        assert state.resolve_capture(capture_id, state.active_endpoint).status == (
            "capture_stale"
        )
        assert state.active_endpoint not in state.dirty_ownership

    @pytest.mark.asyncio
    async def test_lifecycle_transition_excludes_actions_through_mutation(self):
        state = ProcessState(active_instance=0)
        action_entered = asyncio.Event()

        async def run_action() -> None:
            async with coordinated_runtime_operation(
                "runtime_send_mouse", state=state, timeout=1.0
            ):
                action_entered.set()

        async with state.reset_endpoint_transition(0):
            assert state.routing_lock.locked()
            assert state.endpoint_lock("instance:0").locked()
            action_task = asyncio.create_task(run_action())
            await asyncio.sleep(0)
            assert action_entered.is_set() is False

        await action_task
        assert action_entered.is_set() is True

    @pytest.mark.asyncio
    async def test_failed_lifecycle_transition_retains_dirty_ownership(self):
        state = ProcessState(active_instance=0)
        endpoint = state.active_endpoint
        state.dirty_ownership[endpoint] = DirtyOwnership(
            endpoint,
            "runtime-a",
            TabletMode.OFF,
            MouseUntrapMode.OFF,
            TabletMode.MOUSEHACK,
            MouseUntrapMode.MAGIC,
            2,
        )

        with pytest.raises(RuntimeError, match="launch failed"):
            async with state.reset_endpoint_transition(0):
                raise RuntimeError("launch failed")

        assert endpoint in state.dirty_ownership

    @pytest.mark.asyncio
    async def test_cancelled_instance_switch_releases_acquired_endpoint_locks(self):
        state = ProcessState(active_instance=0)
        old_lock = state.endpoint_lock("instance:0")
        blocked_lock = state.endpoint_lock("instance:1")
        await blocked_lock.acquire()
        task = asyncio.create_task(state.select_instance(1))
        await asyncio.sleep(0)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert old_lock.locked() is False
        assert state.routing_lock.locked() is False
        blocked_lock.release()

    @pytest.mark.asyncio
    async def test_instance_switch_prunes_inactive_endpoint_locks(self):
        state = ProcessState(active_instance=0)
        state.endpoint_lock("instance:0")

        await state.select_instance(1)

        assert set(state.endpoint_locks) == {"instance:1"}

    def test_mutation_impact_is_centralized(self):
        assert mutation_impact_for("runtime_gui_click") is MutationImpact.SERIALIZE
        assert (
            mutation_impact_for("runtime_set_config", "tablet_mode")
            is MutationImpact.SERIALIZE_GEOMETRY_INVALIDATE
        )
        assert (
            mutation_impact_for("runtime_set_config", "gfx_fullscreen")
            is MutationImpact.SERIALIZE_GEOMETRY_INVALIDATE
        )
        assert (
            mutation_impact_for("runtime_set_config", "ntsc")
            is MutationImpact.SERIALIZE_GEOMETRY_INVALIDATE
        )
        assert (
            mutation_impact_for("runtime_select_instance")
            is MutationImpact.ROUTE_LIFECYCLE_RESET
        )
        assert (
            mutation_impact_for("set_active_instance")
            is MutationImpact.ROUTE_LIFECYCLE_RESET
        )
        assert (
            mutation_impact_for("launch_amiberry")
            is MutationImpact.ROUTE_LIFECYCLE_RESET
        )
        assert (
            mutation_impact_for("set_disk_swapper")
            is MutationImpact.ROUTE_LIFECYCLE_RESET
        )
        assert (
            mutation_impact_for("runtime_toggle_status_line")
            is MutationImpact.SERIALIZE_GEOMETRY_INVALIDATE
        )
        assert (
            mutation_impact_for("reset_emulation")
            is MutationImpact.SERIALIZE_GEOMETRY_INVALIDATE
        )
        assert (
            mutation_impact_for("runtime_toggle_mouse_grab") is MutationImpact.SERIALIZE
        )
        assert mutation_impact_for("runtime_get_status") is MutationImpact.READ_ONLY

    @pytest.mark.asyncio
    async def test_geometry_operation_serializes_then_invalidates(self):
        state = ProcessState(active_instance=0)
        capture_id = state.new_capture_id()
        state.register_capture(_capture_record(state, capture_id))

        async with coordinated_runtime_operation(
            "runtime_set_resolution", state=state
        ) as pinned:
            assert pinned.endpoint == state.active_endpoint
            assert state.endpoint_lock(pinned.endpoint).locked()

        assert state.resolve_capture(capture_id, state.active_endpoint).status == (
            "capture_stale"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
