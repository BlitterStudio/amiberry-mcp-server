"""Tests for the MCP screenshot-driven GUI automation adapter."""

import json
import platform
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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

from amiberry_mcp import server
from amiberry_mcp.gui_automation import (
    AutomationResult,
    CleanupState,
    ClickRequest,
    DragRequest,
    ExecutionState,
    FailurePhase,
    GuiAction,
    MouseButton,
    MoveRequest,
    NextAction,
    Point,
    StableCode,
)

GUI_TOOL_NAMES = {
    "runtime_gui_move",
    "runtime_gui_click",
    "runtime_gui_drag",
}
RESULT_FIELDS = {
    "schema_version",
    "ok",
    "code",
    "message",
    "controller_id",
    "request_id",
    "action",
    "execution_state",
    "failure_phase",
    "next_action",
    "retryable",
    "recapture_required",
    "cleanup_state",
    "cleanup_context",
    "deduplicated",
    "capture_id",
    "requested_coordinates",
    "applied_coordinates",
    "runtime_id",
    "geometry_revision",
}


def _result(action: GuiAction, request_id: str) -> AutomationResult:
    return AutomationResult(
        schema_version=1,
        ok=True,
        code=StableCode.OK,
        message="GUI action completed",
        controller_id="controller-a",
        request_id=request_id,
        action=action,
        execution_state=ExecutionState.COMPLETED,
        failure_phase=None,
        next_action=NextAction.NONE,
        retryable=False,
        recapture_required=False,
        cleanup_state=CleanupState.CONFIRMED,
        cleanup_context={"release": "confirmed"},
        deduplicated=False,
        capture_id="capture-a",
        requested_coordinates=(Point(2, 3),),
        applied_coordinates=(Point(12, 13),),
        runtime_id="runtime-a",
        geometry_revision=7,
    )


@pytest.mark.asyncio
async def test_list_tools_exposes_exact_closed_gui_schemas():
    server._TOOLS_CACHE = None
    tools = {tool.name: tool for tool in await server.list_tools()}

    assert {name for name in tools if name.startswith("runtime_gui_")} == GUI_TOOL_NAMES
    for name in GUI_TOOL_NAMES:
        tool = tools[name]
        assert tool.inputSchema["additionalProperties"] is False
        assert "exact preceding runtime_screenshot_view" in tool.description
        assert "next_action" in tool.description

    move = tools["runtime_gui_move"].inputSchema
    assert move["required"] == ["capture_id", "request_id", "x", "y"]
    assert move["properties"]["dwell_ms"] == {
        "type": "integer",
        "minimum": 0,
        "maximum": 5000,
        "default": 0,
    }
    click = tools["runtime_gui_click"].inputSchema
    assert click["required"] == ["capture_id", "request_id", "x", "y"]
    assert click["properties"]["button"]["enum"] == ["left", "right", "middle"]
    assert click["properties"]["button"]["default"] == "left"
    assert click["properties"]["click_count"]["enum"] == [1, 2]
    assert click["properties"]["click_count"]["default"] == 1
    drag = tools["runtime_gui_drag"].inputSchema
    assert drag["required"] == [
        "capture_id",
        "request_id",
        "start_x",
        "start_y",
        "end_x",
        "end_y",
    ]
    assert drag["properties"]["button"]["default"] == "left"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("runtime_gui_move", {"capture_id": "capture-a", "x": 1, "y": 2}),
        (
            "runtime_gui_move",
            {
                "capture_id": "capture-a",
                "request_id": "move-1",
                "x": 1,
                "y": 2,
                "extra": True,
            },
        ),
        (
            "runtime_gui_move",
            {"capture_id": "capture-a", "request_id": "move-1", "x": 1.5, "y": 2},
        ),
        (
            "runtime_gui_move",
            {"capture_id": "capture-a", "request_id": "move-1", "x": True, "y": 2},
        ),
        (
            "runtime_gui_move",
            {"capture_id": "capture-a", "request_id": "move-1", "x": -1, "y": 2},
        ),
        (
            "runtime_gui_move",
            {
                "capture_id": "capture-a",
                "request_id": "move-1",
                "x": 1,
                "y": 2,
                "dwell_ms": 5001,
            },
        ),
        (
            "runtime_gui_click",
            {
                "capture_id": "capture-a",
                "request_id": "click-1",
                "x": 1,
                "y": 2,
                "button": "primary",
            },
        ),
        (
            "runtime_gui_click",
            {
                "capture_id": "capture-a",
                "request_id": "click-1",
                "x": 1,
                "y": 2,
                "button": 1,
            },
        ),
        (
            "runtime_gui_click",
            {
                "capture_id": "capture-a",
                "request_id": "click-1",
                "x": 1,
                "y": 2,
                "click_count": 3,
            },
        ),
        (
            "runtime_gui_click",
            {
                "capture_id": "capture-a",
                "request_id": "click-1",
                "x": 1,
                "y": 2,
                "click_count": 1.0,
            },
        ),
        (
            "runtime_gui_click",
            {
                "capture_id": "capture-a",
                "request_id": "click-1",
                "x": 1,
                "y": 2,
                "click_count": True,
            },
        ),
        (
            "runtime_gui_drag",
            {
                "capture_id": "capture-a",
                "request_id": "drag-1",
                "start_x": 1,
                "start_y": 2,
                "end_x": 3,
                "end_y": False,
            },
        ),
    ],
)
async def test_direct_call_rejects_invalid_action_envelopes(tool_name, arguments):
    with (
        patch.object(server._gui_automation, "move", new=AsyncMock()) as move,
        patch.object(server._gui_automation, "click", new=AsyncMock()) as click,
        patch.object(server._gui_automation, "drag", new=AsyncMock()) as drag,
    ):
        response = await server.call_tool(tool_name, arguments)

    payload = json.loads(response[0].text)
    assert set(payload) == RESULT_FIELDS
    assert payload["ok"] is False
    assert payload["code"] == "action_rejected"
    assert payload["execution_state"] == "not_started"
    assert payload["failure_phase"] == "validation"
    assert payload["next_action"] == "correct_request"
    move.assert_not_awaited()
    click.assert_not_awaited()
    drag.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "method_name", "arguments", "expected_request", "action"),
    [
        (
            "runtime_gui_move",
            "move",
            {
                "capture_id": "capture-a",
                "request_id": "move-1",
                "x": 2,
                "y": 3,
                "dwell_ms": 25,
            },
            MoveRequest("capture-a", "move-1", 2, 3, 25),
            GuiAction.MOVE,
        ),
        (
            "runtime_gui_click",
            "click",
            {
                "capture_id": "capture-a",
                "request_id": "click-1",
                "x": 2,
                "y": 3,
                "button": "right",
                "click_count": 2,
            },
            ClickRequest("capture-a", "click-1", 2, 3, MouseButton.RIGHT, 2),
            GuiAction.CLICK,
        ),
        (
            "runtime_gui_drag",
            "drag",
            {
                "capture_id": "capture-a",
                "request_id": "drag-1",
                "start_x": 2,
                "start_y": 3,
                "end_x": 4,
                "end_y": 5,
            },
            DragRequest("capture-a", "drag-1", 2, 3, 4, 5),
            GuiAction.DRAG,
        ),
    ],
)
async def test_action_handlers_delegate_once_and_preserve_canonical_result(
    tool_name, method_name, arguments, expected_request, action
):
    expected = _result(action, arguments["request_id"])
    with patch.object(
        server._gui_automation, method_name, new=AsyncMock(return_value=expected)
    ) as method:
        response = await server.call_tool(tool_name, arguments)

    assert len(response) == 1
    assert json.loads(response[0].text) == expected.to_dict()
    method.assert_awaited_once_with(expected_request)


@pytest.mark.asyncio
async def test_action_handler_preserves_complete_failure_result():
    expected = AutomationResult(
        schema_version=1,
        ok=False,
        code=StableCode.CLEANUP_UNCONFIRMED,
        message="Release could not be confirmed",
        controller_id="controller-a",
        request_id="drag-failure",
        action=GuiAction.DRAG,
        execution_state=ExecutionState.OUTCOME_UNKNOWN,
        failure_phase=FailurePhase.RELEASE,
        next_action=NextAction.RECONCILE,
        retryable=False,
        recapture_required=False,
        cleanup_state=CleanupState.UNCONFIRMED,
        cleanup_context={"release_error": "connection reset"},
        deduplicated=True,
        capture_id="capture-a",
        requested_coordinates=(Point(1, 2), Point(3, 4)),
        applied_coordinates=(Point(11, 12),),
        runtime_id="runtime-a",
        geometry_revision=9,
    )
    arguments = {
        "capture_id": "capture-a",
        "request_id": "drag-failure",
        "start_x": 1,
        "start_y": 2,
        "end_x": 3,
        "end_y": 4,
    }

    with patch.object(
        server._gui_automation, "drag", new=AsyncMock(return_value=expected)
    ) as drag:
        response = await server.call_tool("runtime_gui_drag", arguments)

    assert json.loads(response[0].text) == expected.to_dict()
    assert set(json.loads(response[0].text)) == RESULT_FIELDS
    drag.assert_awaited_once_with(DragRequest("capture-a", "drag-failure", 1, 2, 3, 4))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments", "method_name", "return_value", "option"),
    [
        ("runtime_send_mouse", {"dx": 1, "dy": 2}, "send_mouse", True, None),
        ("runtime_set_mouse_speed", {"speed": 2}, "set_mouse_speed", True, None),
        (
            "runtime_set_config",
            {"option": "tablet_mode", "value": "mousehack"},
            "set_config",
            True,
            "tablet_mode",
        ),
        ("runtime_load_state", {"state_file": "state.uss"}, "load_state", True, None),
        ("runtime_quickload", {}, "quickload", True, None),
        ("runtime_set_autocrop", {"enabled": True}, "set_autocrop", True, None),
        (
            "runtime_load_config",
            {"config_path": "config.uae"},
            "load_config",
            True,
            None,
        ),
        ("runtime_toggle_fullscreen", {}, "toggle_fullscreen", True, None),
        ("runtime_toggle_mouse_grab", {}, "toggle_mouse_grab", True, None),
        ("runtime_set_display_mode", {"mode": 1}, "set_display_mode", True, None),
        ("runtime_set_ntsc", {"enabled": True}, "set_ntsc", True, None),
        ("runtime_toggle_rtg", {}, "toggle_rtg", "native", None),
        (
            "runtime_toggle_status_line",
            {},
            "toggle_status_line",
            (1, "on"),
            None,
        ),
        (
            "runtime_set_window_size",
            {"width": 800, "height": 600},
            "set_window_size",
            True,
            None,
        ),
        ("runtime_set_scaling", {"mode": 0}, "set_scaling", True, None),
        ("runtime_set_line_mode", {"mode": 1}, "set_line_mode", True, None),
        ("runtime_set_resolution", {"mode": 1}, "set_resolution", True, None),
    ],
)
async def test_conflicting_handlers_use_shared_coordinator(
    tool_name, arguments, method_name, return_value, option
):
    calls = []
    client = SimpleNamespace()
    setattr(client, method_name, AsyncMock(return_value=return_value))

    @asynccontextmanager
    async def coordinate(operation, selected_option=None, state=None, timeout=0.25):
        calls.append((operation, selected_option, state))
        yield SimpleNamespace(client=client, endpoint="instance:default", instance=None)

    with patch.object(server, "coordinated_runtime_operation", new=coordinate):
        response = await server.call_tool(tool_name, arguments)

    assert response[0].type == "text"
    assert calls == [(tool_name, option, server._state)]


@pytest.mark.asyncio
async def test_set_active_instance_uses_state_transition():
    with patch.object(
        server._state, "select_instance", new=AsyncMock()
    ) as select_instance:
        response = await server.call_tool("set_active_instance", {"instance": 2})

    select_instance.assert_awaited_once_with(2)
    assert response[0].text == "Active instance set to 2"


@pytest.mark.asyncio
async def test_launch_resets_endpoint_before_publishing_process():
    events = []

    @asynccontextmanager
    async def transition(instance):
        events.append(("reset_enter", instance))
        yield
        events.append(("reset_exit", instance))

    def launch(cmd):
        events.append(("launch", cmd))
        return SimpleNamespace(pid=123)

    with (
        patch.object(server._state, "active_instance", 2),
        patch.object(server._state, "reset_endpoint_transition", new=transition),
        patch.object(server, "build_launch_command", return_value=["amiberry"]),
        patch.object(server, "launch_and_store", side_effect=launch),
    ):
        response = await server.call_tool("launch_amiberry", {"model": "A500"})

    assert events == [
        ("reset_enter", 2),
        ("launch", ["amiberry"]),
        ("reset_exit", 2),
    ]
    assert response[0].text == "Launched Amiberry with model: A500\n  PID: 123"


@pytest.mark.asyncio
async def test_restart_resets_endpoint_before_terminate_and_launch():
    events = []
    process = MagicMock(pid=41)
    process.poll.return_value = None

    @asynccontextmanager
    async def transition(instance):
        events.append(("reset_enter", instance))
        yield
        events.append(("reset_exit", instance))

    def terminate(target):
        events.append(("terminate", target.pid))

    def launch(cmd, log_path=None):
        events.append(("launch", cmd, log_path))
        return SimpleNamespace(pid=42)

    with (
        patch.object(server._state, "active_instance", 1),
        patch.object(server._state, "process", process),
        patch.object(server._state, "launch_cmd", ["amiberry", "--model", "A500"]),
        patch.object(server._state, "log_path", None),
        patch.object(server._state, "reset_endpoint_transition", new=transition),
        patch.object(server, "terminate_process", side_effect=terminate),
        patch.object(server, "launch_and_store", side_effect=launch),
    ):
        response = await server.call_tool("restart_amiberry", {})

    assert events == [
        ("reset_enter", 1),
        ("terminate", 41),
        ("launch", ["amiberry", "--model", "A500"], None),
        ("reset_exit", 1),
    ]
    assert response[0].text.startswith("Amiberry restarted (PID: 42)")


@pytest.mark.asyncio
async def test_restart_failure_propagates_through_lifecycle_transition():
    events = []
    process = MagicMock(pid=41)
    process.poll.return_value = None

    @asynccontextmanager
    async def transition(instance):
        events.append(("reset_enter", instance))
        try:
            yield
        except OSError:
            events.append(("reset_failed", instance))
            raise

    def terminate(target):
        events.append(("terminate", target.pid))

    def launch(cmd, log_path=None):
        events.append(("launch", cmd, log_path))
        raise OSError("launch failed")

    with (
        patch.object(server._state, "active_instance", 1),
        patch.object(server._state, "process", process),
        patch.object(server._state, "launch_cmd", ["amiberry", "--model", "A500"]),
        patch.object(server._state, "log_path", None),
        patch.object(server._state, "reset_endpoint_transition", new=transition),
        patch.object(server, "terminate_process", side_effect=terminate),
        patch.object(server, "launch_and_store", side_effect=launch),
    ):
        response = await server.call_tool("restart_amiberry", {})

    assert events == [
        ("reset_enter", 1),
        ("terminate", 41),
        ("launch", ["amiberry", "--model", "A500"], None),
        ("reset_failed", 1),
    ]
    assert response[0].text == "Error restarting Amiberry: launch failed"


@pytest.mark.asyncio
async def test_kill_resets_endpoint_before_terminating_process():
    events = []
    process = MagicMock(pid=77)
    process.poll.return_value = None

    @asynccontextmanager
    async def transition(instance):
        events.append(("reset_enter", instance))
        yield
        events.append(("reset_exit", instance))

    def terminate(target):
        events.append(("terminate", target.pid))

    with (
        patch.object(server._state, "active_instance", None),
        patch.object(server._state, "process", process),
        patch.object(server._state, "reset_endpoint_transition", new=transition),
        patch.object(server, "terminate_process", side_effect=terminate),
    ):
        response = await server.call_tool("kill_amiberry", {})

    assert events == [
        ("reset_enter", None),
        ("terminate", 77),
        ("reset_exit", None),
    ]
    assert response[0].text == "Amiberry process (PID 77) terminated."
