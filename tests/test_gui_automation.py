"""Tests for the screenshot-driven GUI automation domain service."""

import asyncio
import platform
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, call, patch

import pytest

if platform.system() not in ("Darwin", "Linux"):
    _cfg = "amiberry_mcp.config"
    if _cfg not in sys.modules:
        _mod = ModuleType(_cfg)
        _root = Path.home() / "Amiberry"
        _mod.IS_MACOS = False  # type: ignore[attr-defined]
        _mod.IS_LINUX = True  # type: ignore[attr-defined]
        _mod.EMULATOR_BINARY = "amiberry"  # type: ignore[attr-defined]
        for _name, _value in {
            "AMIBERRY_HOME": _root,
            "CONFIG_DIR": _root / "conf",
            "SYSTEM_CONFIG_DIR": None,
            "SAVESTATE_DIR": _root / "savestates",
            "SCREENSHOT_DIR": _root / "screenshots",
            "LOG_DIR": _root / "logs",
            "ROM_DIR": _root / "kickstarts",
            "DISK_IMAGE_DIRS": [_root / "floppies"],
            "FLOPPY_EXTENSIONS": [".adf"],
            "HARDFILE_EXTENSIONS": [".hdf"],
            "LHA_EXTENSIONS": [".lha"],
            "CD_EXTENSIONS": [".iso"],
            "SUPPORTED_MODELS": ["A500", "A1200", "CD32"],
        }.items():
            setattr(_mod, _name, _value)
        _mod.ensure_directories_exist = lambda: None  # type: ignore[attr-defined]
        _mod.get_platform_info = lambda: {}  # type: ignore[attr-defined]
        sys.modules[_cfg] = _mod

from amiberry_mcp.gui_automation import (
    CaptureError,
    ClickRequest,
    DragRequest,
    ExecutionState,
    FailurePhase,
    GuiAction,
    GuiAutomationService,
    MouseButton,
    MoveRequest,
    Point,
    StableCode,
    _desired_mouse_untrap,
    _desired_tablet_mode,
    translate_screenshot_point,
)
from amiberry_mcp.ipc_client import (
    ActionableCaptureGeometry,
    ActionableScreenshotResult,
    AutomationConfigResponse,
    AutomationState,
    ConfigUpdateReason,
    DisplayMode,
    GuardedInputReason,
    GuardedInputResponse,
    MouseUntrapMode,
    ReleaseMouseButtonsResponse,
    Renderer,
    TabletMode,
)
from amiberry_mcp.shared_state import CaptureRecord, DirtyOwnership, ProcessState


def _png(width: int, height: int, suffix: bytes = b"") -> bytes:
    """Build the PNG signature and IHDR bytes needed by the decoder."""
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + suffix
    )


def _geometry(
    path: str = "/tmp/capture.png",
    *,
    image_width: int = 8,
    image_height: int = 6,
    source_x: int = 1,
    source_y: int = 1,
    source_width: int = 6,
    source_height: int = 4,
    viewport_x: int = 10,
    viewport_y: int = 20,
    viewport_width: int = 12,
    viewport_height: int = 8,
    revision: int = 4,
) -> ActionableCaptureGeometry:
    return ActionableCaptureGeometry(
        schema_version=1,
        path=path,
        runtime_id="runtime-a",
        capture_nonce="nonce-a",
        geometry_revision=revision,
        monitor_id=0,
        display_mode=DisplayMode.NATIVE,
        renderer=Renderer.SDL,
        image_width=image_width,
        image_height=image_height,
        source_x=source_x,
        source_y=source_y,
        source_width=source_width,
        source_height=source_height,
        viewport_x=viewport_x,
        viewport_y=viewport_y,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        window_width=64,
        window_height=64,
    )


def _state(
    *,
    tablet: TabletMode = TabletMode.REAL,
    untrap: MouseUntrapMode = MouseUntrapMode.BOTH,
    effective_tablet: TabletMode | None = None,
    effective_untrap: MouseUntrapMode | None = None,
    config_revision: int = 7,
    geometry_revision: int = 4,
    runtime_id: str = "runtime-a",
    focus: bool = True,
) -> AutomationState:
    effective_tablet = effective_tablet or tablet
    effective_untrap = effective_untrap or untrap
    return AutomationState(
        schema_version=1,
        runtime_id=runtime_id,
        geometry_revision=geometry_revision,
        monitor_id=0,
        geometry_valid=True,
        pending_tablet_mode=tablet,
        effective_tablet_mode=effective_tablet,
        pending_mouse_untrap=untrap,
        effective_mouse_untrap=effective_untrap,
        pending_effective_diverged=(
            tablet != effective_tablet or untrap != effective_untrap
        ),
        input_config_revision=config_revision,
        focus_ready=focus,
        supported_button_mask=7,
        button_mask=0,
    )


def _guard(x: int, y: int, mask: int, config_revision: int = 7) -> GuardedInputResponse:
    return GuardedInputResponse(
        schema_version=1,
        applied=True,
        reason=GuardedInputReason.NONE,
        runtime_id="runtime-a",
        geometry_revision=4,
        monitor_id=0,
        input_config_revision=config_revision,
        button_mask=mask,
        x=x,
        y=y,
    )


def _release() -> ReleaseMouseButtonsResponse:
    return ReleaseMouseButtonsResponse(schema_version=1, confirmed=True, button_mask=0)


def _client(state_response: AutomationState | None = None) -> AsyncMock:
    client = AsyncMock()
    client._get_gui_automation_state = AsyncMock(
        return_value=state_response or _state()
    )
    client._release_mouse_buttons = AsyncMock(return_value=_release())
    client._send_mouse_abs_guarded = AsyncMock(
        side_effect=lambda x, y, mask, *_args: _guard(x, y, mask, _args[-1])
    )
    return client


def _registered_service(
    geometry: ActionableCaptureGeometry | None = None,
    state_response: AutomationState | None = None,
) -> tuple[GuiAutomationService, ProcessState, AsyncMock, str]:
    geometry = geometry or _geometry()
    state = ProcessState()
    client = _client(state_response)
    state.ipc_client_cache = (None, client)
    capture_id = state.new_capture_id()
    state.register_capture(
        CaptureRecord(
            capture_id=capture_id,
            controller_id=state.controller_id,
            endpoint=state.active_endpoint,
            path=Path(geometry.path),
            geometry=geometry,
        )
    )
    return GuiAutomationService(state), state, client, capture_id


class TestTranslateScreenshotPoint:
    def test_maps_half_open_corners_and_pixel_centers(self):
        geometry = _geometry()

        assert translate_screenshot_point(geometry, 1, 1) == Point(11, 21)
        assert translate_screenshot_point(geometry, 6, 4) == Point(21, 27)
        assert translate_screenshot_point(geometry, 3, 2) == Point(15, 23)

    def test_handles_one_pixel_source(self):
        geometry = _geometry(
            image_width=1,
            image_height=1,
            source_x=0,
            source_y=0,
            source_width=1,
            source_height=1,
            viewport_width=9,
            viewport_height=7,
        )

        assert translate_screenshot_point(geometry, 0, 0) == Point(14, 23)

    @pytest.mark.parametrize("point", [(0, 1), (7, 1), (1, 0), (1, 5), (-1, 1)])
    def test_rejects_outside_without_clamping(self, point):
        with pytest.raises(ValueError):
            translate_screenshot_point(_geometry(), *point)


def test_minimum_settings_preserve_existing_capabilities():
    assert _desired_tablet_mode(TabletMode.REAL) is TabletMode.REAL
    assert _desired_tablet_mode(TabletMode.OFF) is TabletMode.MOUSEHACK
    assert _desired_mouse_untrap(MouseUntrapMode.BOTH) is MouseUntrapMode.BOTH
    assert _desired_mouse_untrap(MouseUntrapMode.MIDDLE) is MouseUntrapMode.BOTH


def test_validation_error_uses_canonical_result_contract():
    service = GuiAutomationService(ProcessState(controller_id="controller-test"))

    result = service.validation_error(
        GuiAction.CLICK,
        "request-1",
        "capture-1",
        "button must be left, right, or middle",
    )

    payload = result.to_dict()
    assert payload["schema_version"] == 1
    assert payload["ok"] is False
    assert payload["code"] == "action_rejected"
    assert payload["controller_id"] == "controller-test"
    assert payload["request_id"] == "request-1"
    assert payload["capture_id"] == "capture-1"
    assert payload["action"] == "click"
    assert payload["failure_phase"] == FailurePhase.VALIDATION.value
    assert payload["next_action"] == "correct_request"


class TestCaptureTransaction:
    @pytest.mark.asyncio
    async def test_registers_only_validated_exact_bytes(self, tmp_path):
        path = tmp_path / "capture.png"
        geometry = _geometry(str(path))
        state = ProcessState()
        client = _client()

        async def capture(filename):
            path.write_bytes(_png(8, 6, b"first"))
            return ActionableScreenshotResult(filename, geometry)

        client.capture_actionable_screenshot = AsyncMock(side_effect=capture)
        state.ipc_client_cache = (None, client)

        view = await GuiAutomationService(state).capture(str(path))

        assert view.image_bytes == _png(8, 6, b"first")
        assert view.capture_id in state.captures
        assert view.actionable is True

    @pytest.mark.asyncio
    async def test_same_path_concurrent_captures_keep_bytes_and_metadata_paired(
        self, tmp_path
    ):
        path = tmp_path / "shared.png"
        state = ProcessState()
        client = _client()
        calls = 0

        async def capture(filename):
            nonlocal calls
            calls += 1
            width = 8 + calls
            path.write_bytes(_png(width, 6, bytes([calls])))
            await asyncio.sleep(0)
            return ActionableScreenshotResult(
                filename,
                _geometry(str(path), image_width=width),
            )

        client.capture_actionable_screenshot = AsyncMock(side_effect=capture)
        client._get_gui_automation_state = AsyncMock(return_value=_state())
        state.ipc_client_cache = (None, client)
        service = GuiAutomationService(state)

        first, second = await asyncio.gather(
            service.capture(str(path)), service.capture(str(path))
        )

        assert first.image_width == 9
        assert first.image_bytes.endswith(b"\x01")
        assert second.image_width == 10
        assert second.image_bytes.endswith(b"\x02")

    @pytest.mark.asyncio
    async def test_dimension_mismatch_does_not_register(self, tmp_path):
        path = tmp_path / "bad.png"
        path.write_bytes(_png(7, 6))
        state = ProcessState()
        client = _client()
        client.capture_actionable_screenshot = AsyncMock(
            return_value=ActionableScreenshotResult(str(path), _geometry(str(path)))
        )
        state.ipc_client_cache = (None, client)

        with pytest.raises(CaptureError):
            await GuiAutomationService(state).capture(str(path))

        assert not state.captures


class TestActionSequences:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("button", "expected_mask"),
        [
            (MouseButton.LEFT, 1),
            (MouseButton.RIGHT, 2),
            (MouseButton.MIDDLE, 4),
        ],
    )
    async def test_click_uses_named_complete_masks(self, button, expected_mask):
        service, _state_obj, client, capture_id = _registered_service()

        with patch("amiberry_mcp.gui_automation.asyncio.sleep", new=AsyncMock()):
            result = await service.click(
                ClickRequest(capture_id, "click-1", 3, 2, button, 2)
            )

        assert result.code is StableCode.OK
        assert result.cleanup_state.value == "confirmed"
        masks = [
            args.args[2] for args in client._send_mouse_abs_guarded.await_args_list
        ]
        assert masks == [0, expected_mask, 0, expected_mask, 0]
        client._release_mouse_buttons.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_drag_uses_guarded_start_press_move_release(self):
        service, _state_obj, client, capture_id = _registered_service()

        with patch("amiberry_mcp.gui_automation.asyncio.sleep", new=AsyncMock()):
            result = await service.drag(
                DragRequest(capture_id, "drag-1", 1, 1, 6, 4, MouseButton.RIGHT)
            )

        assert result.code is StableCode.OK
        calls = client._send_mouse_abs_guarded.await_args_list
        assert [(item.args[0], item.args[1], item.args[2]) for item in calls] == [
            (11, 21, 0),
            (11, 21, 2),
            (21, 27, 2),
            (21, 27, 0),
        ]

    @pytest.mark.asyncio
    async def test_identical_retry_is_deduplicated_without_ipc(self):
        service, _state_obj, client, capture_id = _registered_service()
        request = MoveRequest(capture_id, "move-1", 3, 2)

        first = await service.move(request)
        call_count = client._send_mouse_abs_guarded.await_count
        second = await service.move(request)

        assert first.code is StableCode.OK
        assert second.deduplicated is True
        assert client._send_mouse_abs_guarded.await_count == call_count

    @pytest.mark.asyncio
    async def test_changed_payload_conflicts(self):
        service, _state_obj, _client_obj, capture_id = _registered_service()

        await service.move(MoveRequest(capture_id, "move-1", 3, 2))
        result = await service.move(MoveRequest(capture_id, "move-1", 4, 2))

        assert result.code is StableCode.REQUEST_ID_CONFLICT

    @pytest.mark.asyncio
    async def test_busy_request_id_can_execute_later(self):
        service, state, client, capture_id = _registered_service()
        lock = state.endpoint_lock(state.active_endpoint)
        await lock.acquire()
        try:
            with patch("amiberry_mcp.gui_automation.ACTION_LOCK_TIMEOUT", 0.001):
                busy = await service.move(MoveRequest(capture_id, "retry-me", 3, 2))
        finally:
            lock.release()

        success = await service.move(MoveRequest(capture_id, "retry-me", 3, 2))

        assert busy.code is StableCode.AUTOMATION_BUSY
        assert success.code is StableCode.OK
        assert client._send_mouse_abs_guarded.await_count == 1

    @pytest.mark.asyncio
    async def test_stale_capture_emits_no_input_or_cleanup(self):
        service, state, client, capture_id = _registered_service()
        state.invalidate_endpoint_captures(state.active_endpoint)

        result = await service.move(MoveRequest(capture_id, "stale-1", 3, 2))

        assert result.code is StableCode.CAPTURE_STALE
        client._send_mouse_abs_guarded.assert_not_awaited()
        client._release_mouse_buttons.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancellation_cleans_up_and_never_replays(self):
        service, _state_obj, client, capture_id = _registered_service()
        client._send_mouse_abs_guarded = AsyncMock(
            side_effect=[_guard(11, 21, 0), asyncio.CancelledError]
        )
        request = ClickRequest(capture_id, "cancel-1", 1, 1)

        with pytest.raises(asyncio.CancelledError):
            await service.click(request)

        client._release_mouse_buttons.assert_awaited_once()
        replay = await service.click(request)
        assert replay.execution_state is ExecutionState.OUTCOME_UNKNOWN
        assert replay.deduplicated is True
        assert client._send_mouse_abs_guarded.await_count == 2

    @pytest.mark.asyncio
    async def test_cancellation_during_cleanup_waits_under_lock_then_finalizes(self):
        service, state, client, capture_id = _registered_service()
        release_started = asyncio.Event()
        allow_release = asyncio.Event()

        async def delayed_release():
            release_started.set()
            await allow_release.wait()
            return _release()

        client._release_mouse_buttons = AsyncMock(side_effect=delayed_release)
        request = MoveRequest(capture_id, "cancel-cleanup", 3, 2)
        task = asyncio.create_task(service.move(request))
        await release_started.wait()

        task.cancel()
        await asyncio.sleep(0)
        assert state.endpoint_lock(state.active_endpoint).locked()
        allow_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert not state.endpoint_lock(state.active_endpoint).locked()
        entry = state.request_active[request.request_id]
        assert entry.state == "outcome_unknown"
        assert entry.result.code is StableCode.ACTION_REJECTED
        assert entry.result.cleanup_state.value == "confirmed"

    @pytest.mark.asyncio
    async def test_cancellation_before_pin_releases_request_identity(self):
        service, state, client, capture_id = _registered_service()
        request = MoveRequest(capture_id, "cancel-before", 3, 2)
        await state.routing_lock.acquire()
        task = asyncio.create_task(service.move(request))
        await asyncio.sleep(0)
        task.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            state.routing_lock.release()

        result = await service.move(request)
        assert result.code is StableCode.OK
        assert client._send_mouse_abs_guarded.await_count == 1


class TestReadinessOwnership:
    @pytest.mark.asyncio
    async def test_applies_minimum_settings_and_restores_owned_values(self):
        original = _state(tablet=TabletMode.OFF, untrap=MouseUntrapMode.MIDDLE)
        owned = _state(
            tablet=TabletMode.MOUSEHACK,
            untrap=MouseUntrapMode.BOTH,
            config_revision=8,
        )
        restored = _state(
            tablet=TabletMode.OFF,
            untrap=MouseUntrapMode.MIDDLE,
            config_revision=9,
        )
        service, _state_obj, client, capture_id = _registered_service(
            state_response=original
        )
        client._get_gui_automation_state = AsyncMock(
            side_effect=[original, owned, owned, restored]
        )
        client._set_gui_automation_config = AsyncMock(
            side_effect=[
                AutomationConfigResponse(
                    1,
                    True,
                    ConfigUpdateReason.NONE,
                    TabletMode.MOUSEHACK,
                    TabletMode.OFF,
                    MouseUntrapMode.BOTH,
                    MouseUntrapMode.MIDDLE,
                    True,
                    8,
                ),
                AutomationConfigResponse(
                    1,
                    True,
                    ConfigUpdateReason.NONE,
                    TabletMode.OFF,
                    TabletMode.MOUSEHACK,
                    MouseUntrapMode.MIDDLE,
                    MouseUntrapMode.BOTH,
                    True,
                    9,
                ),
            ]
        )

        result = await service.move(MoveRequest(capture_id, "ready-1", 3, 2))

        assert result.code is StableCode.OK
        assert client._set_gui_automation_config.await_args_list == [
            call(
                TabletMode.OFF,
                MouseUntrapMode.MIDDLE,
                7,
                TabletMode.MOUSEHACK,
                MouseUntrapMode.BOTH,
            ),
            call(
                TabletMode.MOUSEHACK,
                MouseUntrapMode.BOTH,
                8,
                TabletMode.OFF,
                MouseUntrapMode.MIDDLE,
            ),
        ]

    @pytest.mark.asyncio
    async def test_revision_change_prevents_restore_overwrite(self):
        service, state, client, _capture_id = _registered_service()
        owner = state.dirty_ownership[state.active_endpoint] = DirtyOwnership(
            endpoint=state.active_endpoint,
            runtime_id="runtime-a",
            original_tablet_mode=TabletMode.OFF,
            original_mouse_untrap=MouseUntrapMode.OFF,
            owned_tablet_mode=TabletMode.MOUSEHACK,
            owned_mouse_untrap=MouseUntrapMode.MAGIC,
            input_config_revision=8,
        )
        client._get_gui_automation_state = AsyncMock(
            return_value=_state(
                tablet=owner.owned_tablet_mode,
                untrap=owner.owned_mouse_untrap,
                config_revision=10,
            )
        )

        outcome = await service._cleanup(client, owner)

        assert outcome.state.value == "unconfirmed"
        assert outcome.context["restore"] == "ownership_lost_external_value_preserved"
        client._set_gui_automation_config.assert_not_awaited()
