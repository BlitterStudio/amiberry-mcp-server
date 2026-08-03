#!/usr/bin/env python3
"""
Unit tests for the http_server module.

Covers:
- Fix #2: CORS restricted to localhost, bind to 127.0.0.1
- Fix #3: Path traversal prevention on HTTP endpoints
- Fix #10: Specific pgrep pattern, tracked process preferred
"""

import platform
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

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

from amiberry_mcp.common import _is_path_within
from amiberry_mcp.gui_automation import (
    ActionableBounds,
    AutomationResult,
    CaptureError,
    CaptureView,
    CleanupState,
    ClickRequest,
    DragRequest,
    ExecutionState,
    GuiAction,
    MouseButton,
    MoveRequest,
    NextAction,
    Point,
    StableCode,
)
from amiberry_mcp.shared_state import (
    MutationImpact,
    mutation_impact_for,
)


def _result(action: GuiAction, request_id: str) -> AutomationResult:
    """Build one complete canonical result for HTTP adapter tests."""
    return AutomationResult(
        schema_version=1,
        ok=True,
        code=StableCode.OK,
        message="GUI action completed",
        controller_id="controller-http",
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
        capture_id="capture-http",
        requested_coordinates=(Point(2, 3),),
        applied_coordinates=(Point(12, 13),),
        runtime_id="runtime-http",
        geometry_revision=7,
    )


@pytest.fixture
async def api_client():
    """Drive requests through FastAPI's real validation and routing stack."""
    from amiberry_mcp.http_server import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        yield client


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


class TestGuiAutomationHTTP:
    """Tests the strict HTTP action envelopes and immutable screenshot view."""

    def test_openapi_exposes_separate_closed_action_models(self):
        from amiberry_mcp import http_server

        specification = http_server.app.openapi()
        expected = {
            "/runtime/gui/move": "RuntimeGuiMoveRequest",
            "/runtime/gui/click": "RuntimeGuiClickRequest",
            "/runtime/gui/drag": "RuntimeGuiDragRequest",
        }
        for path, model_name in expected.items():
            request_schema = specification["paths"][path]["post"]["requestBody"][
                "content"
            ]["application/json"]["schema"]
            assert request_schema == {"$ref": f"#/components/schemas/{model_name}"}
            assert (
                specification["components"]["schemas"][model_name][
                    "additionalProperties"
                ]
                is False
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("route", "payload"),
        [
            ("/runtime/gui/move", {"capture_id": "cap", "x": 1, "y": 2}),
            (
                "/runtime/gui/move",
                {
                    "capture_id": "cap",
                    "request_id": "move-1",
                    "x": True,
                    "y": 2,
                },
            ),
            (
                "/runtime/gui/move",
                {
                    "capture_id": "cap",
                    "request_id": "move-1",
                    "x": 1.0,
                    "y": 2,
                },
            ),
            (
                "/runtime/gui/move",
                {
                    "capture_id": "cap",
                    "request_id": "move-1",
                    "x": -1,
                    "y": 2,
                },
            ),
            (
                "/runtime/gui/move",
                {
                    "capture_id": "cap",
                    "request_id": "move-1",
                    "x": 1,
                    "y": 2,
                    "dwell_ms": 5001,
                },
            ),
            (
                "/runtime/gui/click",
                {
                    "capture_id": "cap",
                    "request_id": "bad request",
                    "x": 1,
                    "y": 2,
                },
            ),
            (
                "/runtime/gui/click",
                {
                    "capture_id": "cap",
                    "request_id": "click-1",
                    "x": 1,
                    "y": 2,
                    "button": "primary",
                },
            ),
            (
                "/runtime/gui/click",
                {
                    "capture_id": "cap",
                    "request_id": "click-1",
                    "x": 1,
                    "y": 2,
                    "click_count": 1.0,
                },
            ),
            (
                "/runtime/gui/drag",
                {
                    "capture_id": "cap",
                    "request_id": "drag-1",
                    "start_x": 1,
                    "start_y": 2,
                    "end_x": 3,
                    "end_y": 4,
                    "buttons": 1,
                },
            ),
        ],
    )
    async def test_action_models_return_standard_422(self, api_client, route, payload):
        from amiberry_mcp import http_server

        with (
            patch.object(http_server._gui_automation, "move", new=AsyncMock()) as move,
            patch.object(
                http_server._gui_automation, "click", new=AsyncMock()
            ) as click,
            patch.object(http_server._gui_automation, "drag", new=AsyncMock()) as drag,
        ):
            response = await api_client.post(route, json=payload)

        assert response.status_code == 422
        assert isinstance(response.json()["detail"], list)
        move.assert_not_awaited()
        click.assert_not_awaited()
        drag.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("route", "method_name", "payload", "expected_request", "action"),
        [
            (
                "/runtime/gui/move",
                "move",
                {
                    "capture_id": "capture-http",
                    "request_id": "move-1",
                    "x": 2,
                    "y": 3,
                    "dwell_ms": 25,
                },
                MoveRequest("capture-http", "move-1", 2, 3, 25),
                GuiAction.MOVE,
            ),
            (
                "/runtime/gui/click",
                "click",
                {
                    "capture_id": "capture-http",
                    "request_id": "click-1",
                    "x": 2,
                    "y": 3,
                    "button": "right",
                    "click_count": 2,
                },
                ClickRequest("capture-http", "click-1", 2, 3, MouseButton.RIGHT, 2),
                GuiAction.CLICK,
            ),
            (
                "/runtime/gui/drag",
                "drag",
                {
                    "capture_id": "capture-http",
                    "request_id": "drag-1",
                    "start_x": 2,
                    "start_y": 3,
                    "end_x": 4,
                    "end_y": 5,
                },
                DragRequest("capture-http", "drag-1", 2, 3, 4, 5),
                GuiAction.DRAG,
            ),
        ],
    )
    async def test_action_routes_delegate_once(
        self, api_client, route, method_name, payload, expected_request, action
    ):
        from amiberry_mcp import http_server

        expected = _result(action, payload["request_id"])
        with patch.object(
            http_server._gui_automation,
            method_name,
            new=AsyncMock(return_value=expected),
        ) as method:
            response = await api_client.post(route, json=payload)

        assert response.status_code == 200
        assert response.json() == expected.to_dict()
        method.assert_awaited_once_with(expected_request)

    @pytest.mark.asyncio
    async def test_screenshot_view_uses_exact_immutable_capture(
        self, api_client, tmp_path
    ):
        from amiberry_mcp import http_server

        screenshot_dir = tmp_path / "screenshots"
        filename = screenshot_dir / "capture.png"
        view = CaptureView(
            schema_version=1,
            path=filename,
            image_bytes=b"exact-image-bytes",
            mime_type="image/png",
            controller_id="controller-http",
            capture_id="capture-http",
            actionable=True,
            coordinate_space="screenshot_pixels",
            image_width=320,
            image_height=256,
            actionable_bounds=ActionableBounds(4, 5, 300, 240),
            next_action=NextAction.NONE,
        )
        with (
            patch.object(http_server, "SCREENSHOT_DIR", screenshot_dir),
            patch.object(
                http_server._gui_automation,
                "capture",
                new=AsyncMock(return_value=view),
            ) as capture,
        ):
            response = await api_client.post(
                "/runtime/screenshot-view", json={"filename": str(filename)}
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["data"] == {
            "path": str(filename),
            "base64": "ZXhhY3QtaW1hZ2UtYnl0ZXM=",
            "mime_type": "image/png",
            "size": len(view.image_bytes),
            **view.metadata_dict(),
        }
        capture.assert_awaited_once_with(str(filename))

    @pytest.mark.asyncio
    async def test_screenshot_view_keeps_containment_check(self, api_client, tmp_path):
        from amiberry_mcp import http_server

        screenshot_dir = tmp_path / "screenshots"
        with (
            patch.object(http_server, "SCREENSHOT_DIR", screenshot_dir),
            patch.object(
                http_server._gui_automation, "capture", new=AsyncMock()
            ) as capture,
        ):
            response = await api_client.post(
                "/runtime/screenshot-view",
                json={"filename": str(tmp_path / "outside.png")},
            )

        assert response.status_code == 400
        capture.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("code", "expected_status"),
        [
            (StableCode.AUTOMATION_BUSY, 409),
            (StableCode.CAPTURE_STALE, 409),
            (StableCode.RUNTIME_UNREACHABLE, 503),
            (StableCode.CAPTURE_NOT_ACTIONABLE, 500),
        ],
    )
    async def test_screenshot_capture_error_statuses(
        self, api_client, tmp_path, code, expected_status
    ):
        from amiberry_mcp import http_server

        screenshot_dir = tmp_path / "screenshots"
        filename = screenshot_dir / "capture.png"
        with (
            patch.object(http_server, "SCREENSHOT_DIR", screenshot_dir),
            patch.object(
                http_server._gui_automation,
                "capture",
                new=AsyncMock(side_effect=CaptureError(code, "capture failed")),
            ),
        ):
            response = await api_client.post(
                "/runtime/screenshot-view", json={"filename": str(filename)}
            )

        assert response.status_code == expected_status
        assert response.json() == {"detail": "capture failed"}


class TestHTTPMutationCoordination:
    """Ensure every HTTP mouse/geometry conflict uses the shared policy."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("route", "payload", "operation", "option", "method", "result"),
        [
            ("/runtime/reset", {"hard": False}, "reset_emulation", None, "reset", True),
            (
                "/runtime/load-state",
                {"state_file": "state.uss"},
                "runtime_load_state",
                None,
                "load_state",
                True,
            ),
            (
                "/runtime/config",
                {"option": "tablet_mode", "value": "mousehack"},
                "runtime_set_config",
                "tablet_mode",
                "set_config",
                True,
            ),
            (
                "/runtime/mouse",
                {"dx": 1, "dy": 2},
                "runtime_send_mouse",
                None,
                "send_mouse",
                True,
            ),
            (
                "/runtime/mouse-speed",
                {"speed": 100},
                "runtime_set_mouse_speed",
                None,
                "set_mouse_speed",
                True,
            ),
            (
                "/runtime/quickload",
                {"slot": 0},
                "runtime_quickload",
                None,
                "quickload",
                True,
            ),
            (
                "/runtime/fullscreen",
                None,
                "runtime_toggle_fullscreen",
                None,
                "toggle_fullscreen",
                True,
            ),
            (
                "/runtime/mouse-grab",
                None,
                "runtime_toggle_mouse_grab",
                None,
                "toggle_mouse_grab",
                True,
            ),
            (
                "/runtime/display-mode",
                {"mode": 1},
                "runtime_set_display_mode",
                None,
                "set_display_mode",
                True,
            ),
            (
                "/runtime/ntsc",
                {"enabled": True},
                "runtime_set_ntsc",
                None,
                "set_ntsc",
                True,
            ),
            (
                "/runtime/rtg",
                {"monid": 0},
                "runtime_toggle_rtg",
                None,
                "toggle_rtg",
                "native",
            ),
            (
                "/runtime/status-line",
                None,
                "runtime_toggle_status_line",
                None,
                "toggle_status_line",
                (1, "on"),
            ),
            (
                "/runtime/window-size",
                {"width": 800, "height": 600},
                "runtime_set_window_size",
                None,
                "set_window_size",
                True,
            ),
            (
                "/runtime/scaling",
                {"mode": 0},
                "runtime_set_scaling",
                None,
                "set_scaling",
                True,
            ),
            (
                "/runtime/line-mode",
                {"mode": 1},
                "runtime_set_line_mode",
                None,
                "set_line_mode",
                True,
            ),
            (
                "/runtime/resolution",
                {"mode": 1},
                "runtime_set_resolution",
                None,
                "set_resolution",
                True,
            ),
            (
                "/runtime/autocrop",
                {"enabled": True},
                "runtime_set_autocrop",
                None,
                "set_autocrop",
                True,
            ),
            (
                "/runtime/load-config",
                {"config_path": "config.uae"},
                "runtime_load_config",
                None,
                "load_config",
                True,
            ),
        ],
    )
    async def test_conflicting_routes_use_shared_coordinator(
        self, api_client, route, payload, operation, option, method, result
    ):
        from amiberry_mcp import http_server

        assert mutation_impact_for(operation, option) in {
            MutationImpact.SERIALIZE,
            MutationImpact.SERIALIZE_GEOMETRY_INVALIDATE,
        }
        calls = []
        client = SimpleNamespace()
        setattr(client, method, AsyncMock(return_value=result))

        @asynccontextmanager
        async def coordinate(selected_operation, selected_option=None, state=None):
            calls.append((selected_operation, selected_option, state))
            yield SimpleNamespace(
                client=client, endpoint="instance:default", instance=None
            )

        with (
            patch.object(http_server, "coordinated_runtime_operation", new=coordinate),
            patch.object(http_server, "get_ipc_client", return_value=client),
        ):
            response = await api_client.post(route, json=payload)

        assert response.status_code < 400, response.text
        assert calls == [(operation, option, http_server._state)]

    @pytest.mark.asyncio
    async def test_coordinator_contention_is_http_409(self, api_client):
        from amiberry_mcp import http_server
        from amiberry_mcp.shared_state import AutomationBusyError

        @asynccontextmanager
        async def coordinate(*_args, **_kwargs):
            raise AutomationBusyError("endpoint is busy")
            yield  # pragma: no cover

        with patch.object(http_server, "coordinated_runtime_operation", new=coordinate):
            response = await api_client.post("/runtime/mouse", json={"dx": 1, "dy": 2})

        assert response.status_code == 409
        assert response.json() == {"detail": "Automation busy: endpoint is busy"}


class TestHTTPLifecycleCoordination:
    """Ensure public lifecycle aliases use atomic state transitions."""

    def test_lifecycle_route_inventory_is_explicit(self):
        from amiberry_mcp import http_server

        expected_routes = {
            "/stop": "kill_amiberry",
            "/launch": "launch_amiberry",
            "/quick-launch/{model_or_config}": "launch_amiberry",
            "/launch-lha": "launch_amiberry",
            "/launch-with-logging": "launch_with_logging",
            "/launch-whdload": "launch_whdload",
            "/launch-cd": "launch_cd",
            "/disk-swapper": "set_disk_swapper",
            "/runtime/active-instance": "set_active_instance",
            "/runtime/quit": "runtime_quit",
            "/process/kill": "kill_amiberry",
            "/process/restart": "restart_amiberry",
            "/launch-and-wait": "launch_and_wait_for_ipc",
        }
        assert http_server._HTTP_LIFECYCLE_ROUTES == expected_routes

        post_paths = {
            route.path
            for route in http_server.app.routes
            if "POST" in getattr(route, "methods", set())
        }
        assert set(expected_routes) <= post_paths
        for operation in expected_routes.values():
            assert (
                mutation_impact_for(operation) is MutationImpact.ROUTE_LIFECYCLE_RESET
            )

    @pytest.mark.asyncio
    async def test_active_instance_uses_state_transition(self):
        from amiberry_mcp import http_server

        with patch.object(
            http_server._state, "select_instance", new=AsyncMock()
        ) as select_instance:
            response = await http_server.set_active_instance(
                http_server.ActiveInstanceRequest(instance=2)
            )

        select_instance.assert_awaited_once_with(2)
        assert response.data == {"instance": 2}

    @pytest.mark.asyncio
    async def test_launch_holds_transition_through_process_creation(self):
        from amiberry_mcp import http_server

        events = []

        @asynccontextmanager
        async def transition(instance):
            events.append(("enter", instance))
            yield
            events.append(("exit", instance))

        def launch(cmd):
            events.append(("launch", cmd))
            return SimpleNamespace(pid=123)

        with (
            patch.object(http_server._state, "active_instance", 2),
            patch.object(
                http_server._state,
                "reset_endpoint_transition",
                new=transition,
            ),
            patch.object(
                http_server, "build_launch_command", return_value=["amiberry"]
            ),
            patch.object(http_server, "launch_and_store", side_effect=launch),
        ):
            response = await http_server.launch_amiberry(
                http_server.LaunchRequest(model="A500")
            )

        assert response.success is True
        assert events == [
            ("enter", 2),
            ("launch", ["amiberry"]),
            ("exit", 2),
        ]

    @pytest.mark.asyncio
    async def test_restart_failure_remains_inside_failed_transition(self):
        from amiberry_mcp import http_server

        events = []
        process = MagicMock(pid=41)
        process.poll.return_value = None

        @asynccontextmanager
        async def transition(instance):
            events.append(("enter", instance))
            try:
                yield
            except OSError:
                events.append(("failed", instance))
                raise

        with (
            patch.object(http_server._state, "active_instance", 1),
            patch.object(http_server._state, "process", process),
            patch.object(http_server._state, "launch_cmd", ["amiberry"]),
            patch.object(http_server._state, "log_path", None),
            patch.object(
                http_server._state,
                "reset_endpoint_transition",
                new=transition,
            ),
            patch.object(http_server, "terminate_process"),
            patch.object(
                http_server,
                "launch_and_store",
                side_effect=OSError("launch failed"),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await http_server.restart_amiberry_process()

        assert exc_info.value.status_code == 500
        assert events == [("enter", 1), ("failed", 1)]

    @pytest.mark.asyncio
    async def test_kill_holds_transition_through_termination(self):
        from amiberry_mcp import http_server

        events = []
        process = MagicMock(pid=77)
        process.poll.return_value = None

        @asynccontextmanager
        async def transition(instance):
            events.append(("enter", instance))
            yield
            events.append(("exit", instance))

        def terminate(target):
            events.append(("terminate", target.pid))

        with (
            patch.object(http_server._state, "active_instance", None),
            patch.object(http_server._state, "process", process),
            patch.object(
                http_server._state,
                "reset_endpoint_transition",
                new=transition,
            ),
            patch.object(http_server, "terminate_process", side_effect=terminate),
        ):
            response = await http_server.kill_amiberry_process()

        assert response.success is True
        assert events == [
            ("enter", None),
            ("terminate", 77),
            ("exit", None),
        ]

    @pytest.mark.asyncio
    async def test_stop_failure_exits_transition_exceptionally(self):
        from amiberry_mcp import http_server

        events = []

        @asynccontextmanager
        async def transition(instance):
            events.append(("enter", instance))
            try:
                yield
            except HTTPException:
                events.append(("failed", instance))
                raise

        with (
            patch.object(http_server._state, "active_instance", 3),
            patch.object(
                http_server._state,
                "reset_endpoint_transition",
                new=transition,
            ),
            patch.object(http_server, "_is_amiberry_running", return_value=True),
            patch.object(http_server, "_stop_amiberry", return_value=False),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await http_server.stop()

        assert exc_info.value.status_code == 500
        assert events == [("enter", 3), ("failed", 3)]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
