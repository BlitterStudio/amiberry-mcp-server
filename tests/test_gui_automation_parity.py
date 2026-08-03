"""Cross-transport parity tests for screenshot-driven GUI automation."""

import base64
import json
import platform
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, patch

import httpx
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

from amiberry_mcp import http_server, server
from amiberry_mcp.gui_automation import (
    ActionableBounds,
    AutomationResult,
    CaptureView,
    CleanupState,
    ExecutionState,
    FailurePhase,
    GuiAction,
    NextAction,
    Point,
    StableCode,
)

HTTP_STATUS = {
    StableCode.OK: 200,
    StableCode.CAPTURE_UNKNOWN: 404,
    StableCode.CAPTURE_EVICTED: 404,
    StableCode.COORDINATE_OUT_OF_BOUNDS: 422,
    StableCode.UNSUPPORTED_CAPABILITY: 501,
    StableCode.RUNTIME_UNREACHABLE: 503,
    StableCode.CLEANUP_UNCONFIRMED: 503,
}


def _result(code: StableCode) -> AutomationResult:
    """Build a distinctive complete result for one stable code."""
    success = code is StableCode.OK
    return AutomationResult(
        schema_version=1,
        ok=success,
        code=code,
        message=f"result:{code.value}",
        controller_id="controller-parity",
        request_id="parity-1",
        action=GuiAction.CLICK,
        execution_state=(
            ExecutionState.COMPLETED if success else ExecutionState.NOT_STARTED
        ),
        failure_phase=None if success else FailurePhase.READINESS,
        next_action=NextAction.NONE if success else NextAction.CORRECT_REQUEST,
        retryable=False,
        recapture_required=False,
        cleanup_state=(
            CleanupState.CONFIRMED if success else CleanupState.NOT_REQUIRED
        ),
        cleanup_context={"code": code.value},
        deduplicated=success,
        capture_id="capture-parity",
        requested_coordinates=(Point(2, 3),),
        applied_coordinates=(Point(12, 13),) if success else (),
        runtime_id="runtime-parity" if success else None,
        geometry_revision=9 if success else None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("code", list(StableCode))
async def test_every_stable_result_has_http_mcp_body_parity(code):
    """HTTP status is metadata; its body exactly matches parsed MCP JSON."""
    expected = _result(code)
    arguments = {
        "capture_id": "capture-parity",
        "request_id": "parity-1",
        "x": 2,
        "y": 3,
    }
    transport = httpx.ASGITransport(app=http_server.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        with (
            patch.object(
                http_server._gui_automation,
                "click",
                new=AsyncMock(return_value=expected),
            ),
            patch.object(
                server._gui_automation,
                "click",
                new=AsyncMock(return_value=expected),
            ),
        ):
            http_response = await client.post("/runtime/gui/click", json=arguments)
            mcp_response = await server.call_tool("runtime_gui_click", arguments)

    assert http_response.status_code == HTTP_STATUS.get(code, 409)
    assert "detail" not in http_response.json()
    assert http_response.json() == json.loads(mcp_response[0].text)
    assert http_response.json() == expected.to_dict()


@pytest.mark.asyncio
@pytest.mark.parametrize("actionable", [True, False])
async def test_screenshot_actionable_and_legacy_metadata_parity(actionable, tmp_path):
    """Both adapters expose the same metadata bound to the same exact bytes."""
    screenshot_dir = tmp_path / "screenshots"
    filename = screenshot_dir / "parity.png"
    view = CaptureView(
        schema_version=1,
        path=filename,
        image_bytes=b"immutable-parity-image",
        mime_type="image/png",
        controller_id="controller-parity",
        capture_id="capture-parity" if actionable else None,
        actionable=actionable,
        coordinate_space="screenshot_pixels",
        image_width=640,
        image_height=512,
        actionable_bounds=(ActionableBounds(4, 6, 600, 480) if actionable else None),
        next_action=NextAction.NONE if actionable else NextAction.UPGRADE_RUNTIME,
    )
    transport = httpx.ASGITransport(app=http_server.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        with (
            patch.object(http_server, "SCREENSHOT_DIR", screenshot_dir),
            patch.object(server, "SCREENSHOT_DIR", screenshot_dir),
            patch.object(
                http_server._gui_automation,
                "capture",
                new=AsyncMock(return_value=view),
            ),
            patch.object(
                server._gui_automation,
                "capture",
                new=AsyncMock(return_value=view),
            ),
        ):
            http_response = await client.post(
                "/runtime/screenshot-view", json={"filename": str(filename)}
            )
            mcp_response = await server.call_tool(
                "runtime_screenshot_view", {"filename": str(filename)}
            )

    http_data = http_response.json()["data"]
    mcp_metadata = json.loads(mcp_response[-1].text)
    assert {key: http_data[key] for key in mcp_metadata} == mcp_metadata
    assert http_data["base64"] == base64.b64encode(view.image_bytes).decode()
    assert http_data["mime_type"] == view.mime_type
    assert http_data["size"] == len(view.image_bytes)
    if len(mcp_response) == 3:
        assert mcp_response[1].data == http_data["base64"]
        assert mcp_response[1].mimeType == http_data["mime_type"]
