#!/usr/bin/env python3
"""
Unit tests for the screenshot tools (runtime_screenshot and runtime_screenshot_view).

These tests mock the IPC layer so they run without a live Amiberry instance.
"""

import base64
import json
import platform
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
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

from amiberry_mcp.gui_automation import (
    ActionableBounds,
    CaptureView,
    NextAction,
)
from amiberry_mcp.ipc_client import ActionableScreenshotResult, IPCConnectionError

# Minimal valid PNG: 1x1 pixel, RGBA, white
_PNG_HEADER = b"\x89PNG\r\n\x1a\n"
_MINIMAL_PNG = (
    _PNG_HEADER
    + b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    + b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    + b"\x00\x00\x00\nIDATx"
    + b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
    + b"\x00\x00\x00\x00IEND\xaeB`\x82"
)

# Minimal JPEG structure with a 1x1 baseline SOF marker.
_JPEG_BYTES = (
    b"\xff\xd8"
    b"\xff\xc0\x00\x11\x08\x00\x01\x00\x01\x03"
    b"\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    b"\xff\xd9"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ipc_client_mock(success: bool = True):
    """Return a mock AmiberryIPCClient whose screenshot() returns *success*."""
    mock_client = MagicMock()
    mock_client.screenshot = AsyncMock(return_value=success)

    async def capture_actionable(filename):
        if not await mock_client.screenshot(filename):
            raise IPCConnectionError("Failed to take screenshot.")
        return ActionableScreenshotResult(filename, None)

    mock_client.capture_actionable_screenshot = AsyncMock(
        side_effect=capture_actionable
    )
    return mock_client


@contextmanager
def _patch_ipc_client(mock_client):
    """Patch both legacy server calls and the shared U4 service client lookup."""
    with (
        patch("amiberry_mcp.server.get_ipc_client", return_value=mock_client),
        patch("amiberry_mcp.shared_state.get_ipc_client", return_value=mock_client),
    ):
        yield


def _capture_view(
    path: Path,
    *,
    actionable: bool,
    image_bytes: bytes = _MINIMAL_PNG,
    mime_type: str = "image/png",
) -> CaptureView:
    """Return one immutable service view for adapter serialization tests."""
    return CaptureView(
        schema_version=1,
        path=path,
        image_bytes=image_bytes,
        mime_type=mime_type,
        controller_id="controller-a",
        capture_id="capture-a" if actionable else None,
        actionable=actionable,
        coordinate_space="screenshot_pixels",
        image_width=1,
        image_height=1,
        actionable_bounds=ActionableBounds(0, 0, 1, 1) if actionable else None,
        next_action=NextAction.NONE if actionable else NextAction.UPGRADE_RUNTIME,
    )


# ---------------------------------------------------------------------------
# runtime_screenshot (simple IPC wrapper)
# ---------------------------------------------------------------------------


class TestRuntimeScreenshot:
    """Tests for the runtime_screenshot tool (saves to disk, no image data)."""

    @pytest.mark.asyncio
    async def test_screenshot_success(self):
        """A successful IPC call returns the expected success message."""
        from amiberry_mcp.server import call_tool

        mock_client = _make_ipc_client_mock(success=True)
        with _patch_ipc_client(mock_client):
            result = await call_tool(
                "runtime_screenshot", {"filename": "/tmp/test_shot.png"}
            )

        assert len(result) == 1
        assert "Screenshot saved to" in result[0].text
        assert "/tmp/test_shot.png" in result[0].text
        mock_client.screenshot.assert_awaited_once_with("/tmp/test_shot.png")

    @pytest.mark.asyncio
    async def test_screenshot_failure(self):
        """A failed IPC call returns a failure message."""
        from amiberry_mcp.server import call_tool

        mock_client = _make_ipc_client_mock(success=False)
        with _patch_ipc_client(mock_client):
            result = await call_tool(
                "runtime_screenshot", {"filename": "/tmp/fail.png"}
            )

        assert len(result) == 1
        assert "Failed" in result[0].text

    @pytest.mark.asyncio
    async def test_screenshot_ipc_connection_error(self):
        """An IPC connection error is reported gracefully."""
        from amiberry_mcp.ipc_client import IPCConnectionError
        from amiberry_mcp.server import call_tool

        mock_client = _make_ipc_client_mock()
        mock_client.screenshot = AsyncMock(
            side_effect=IPCConnectionError("socket not found")
        )
        with _patch_ipc_client(mock_client):
            result = await call_tool("runtime_screenshot", {"filename": "/tmp/err.png"})

        assert len(result) == 1
        assert "Connection error" in result[0].text or "error" in result[0].text.lower()


# ---------------------------------------------------------------------------
# runtime_screenshot_view (returns image data)
# ---------------------------------------------------------------------------


class TestRuntimeScreenshotView:
    """Tests for the runtime_screenshot_view tool (returns image content)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("actionable", [True, False])
    async def test_preserves_exact_bytes_order_and_metadata(self, tmp_path, actionable):
        """Path and exact image bytes precede pure JSON capture metadata."""
        from amiberry_mcp.server import call_tool

        screenshot_file = tmp_path / "immutable.png"
        exact_bytes = _MINIMAL_PNG + b"immutable-suffix"
        view = _capture_view(
            screenshot_file, actionable=actionable, image_bytes=exact_bytes
        )

        with patch(
            "amiberry_mcp.server._gui_automation.capture",
            new=AsyncMock(return_value=view),
        ) as capture:
            result = await call_tool(
                "runtime_screenshot_view", {"filename": str(screenshot_file)}
            )

        assert [content.type for content in result] == ["text", "image", "text"]
        assert result[0].text == f"Screenshot saved to: {screenshot_file}"
        assert base64.b64decode(result[1].data) == exact_bytes
        metadata = json.loads(result[2].text)
        assert metadata == view.metadata_dict()
        assert metadata["actionable"] is actionable
        if actionable:
            assert metadata["capture_id"] == "capture-a"
            assert metadata["actionable_bounds"] == {
                "x": 0,
                "y": 0,
                "width": 1,
                "height": 1,
            }
            assert metadata["next_action"] == "none"
        else:
            assert metadata["capture_id"] is None
            assert metadata["actionable_bounds"] is None
            assert metadata["next_action"] == "upgrade_runtime"
        capture.assert_awaited_once_with(str(screenshot_file))

    @pytest.mark.asyncio
    async def test_screenshot_view_generates_file(self, tmp_path):
        """The tool writes a real file and returns image content."""
        from amiberry_mcp.server import call_tool

        screenshot_file = tmp_path / "view_shot.png"
        # Pre-create the file to simulate Amiberry writing it
        screenshot_file.write_bytes(_MINIMAL_PNG)

        mock_client = _make_ipc_client_mock(success=True)
        with _patch_ipc_client(mock_client):
            result = await call_tool(
                "runtime_screenshot_view", {"filename": str(screenshot_file)}
            )

        # Should contain at least a TextContent with the path
        texts = [r for r in result if r.type == "text"]
        assert any(str(screenshot_file) in t.text for t in texts)

    @pytest.mark.asyncio
    async def test_screenshot_view_returns_valid_png(self, tmp_path):
        """The returned base64 data decodes to a valid PNG."""
        from amiberry_mcp.server import call_tool

        screenshot_file = tmp_path / "valid.png"
        screenshot_file.write_bytes(_MINIMAL_PNG)

        mock_client = _make_ipc_client_mock(success=True)
        with _patch_ipc_client(mock_client):
            result = await call_tool(
                "runtime_screenshot_view", {"filename": str(screenshot_file)}
            )

        images = [r for r in result if r.type == "image"]
        if images:
            img = images[0]
            assert img.mimeType == "image/png"
            raw = base64.b64decode(img.data)
            # PNG files must start with the 8-byte signature
            assert raw[:8] == _PNG_HEADER, "Decoded data is not a valid PNG"
            assert len(raw) > 8, "PNG file is essentially empty"

    @pytest.mark.asyncio
    async def test_screenshot_view_nonempty_base64(self, tmp_path):
        """The base64 payload is not empty."""
        from amiberry_mcp.server import call_tool

        screenshot_file = tmp_path / "nonempty.png"
        screenshot_file.write_bytes(_MINIMAL_PNG)

        mock_client = _make_ipc_client_mock(success=True)
        with _patch_ipc_client(mock_client):
            result = await call_tool(
                "runtime_screenshot_view", {"filename": str(screenshot_file)}
            )

        images = [r for r in result if r.type == "image"]
        if images:
            assert len(images[0].data) > 0, "Base64 data is empty"

    @pytest.mark.asyncio
    async def test_screenshot_view_detects_jpeg(self, tmp_path):
        """JPEG files are detected and labelled with the correct MIME type."""
        from amiberry_mcp.server import call_tool

        screenshot_file = tmp_path / "shot.jpg"
        screenshot_file.write_bytes(_JPEG_BYTES)

        mock_client = _make_ipc_client_mock(success=True)
        with _patch_ipc_client(mock_client):
            result = await call_tool(
                "runtime_screenshot_view", {"filename": str(screenshot_file)}
            )

        images = [r for r in result if r.type == "image"]
        assert len(images) == 1
        assert images[0].mimeType == "image/jpeg"

    @pytest.mark.asyncio
    async def test_screenshot_view_rejects_invalid_image_bytes(self, tmp_path):
        """Invalid image bytes cannot produce trustworthy capture metadata."""
        from amiberry_mcp.server import call_tool

        screenshot_file = tmp_path / "shot.bin"
        # Write some arbitrary bytes that aren't JPEG, GIF, or WebP
        screenshot_file.write_bytes(b"\x00\x01\x02\x03" * 64)

        mock_client = _make_ipc_client_mock(success=True)
        with _patch_ipc_client(mock_client):
            result = await call_tool(
                "runtime_screenshot_view", {"filename": str(screenshot_file)}
            )

        images = [r for r in result if r.type == "image"]
        assert images == []
        assert result[0].type == "text"
        assert "format is invalid" in result[0].text

    @pytest.mark.asyncio
    async def test_screenshot_view_auto_generates_filename(self):
        """When no filename is given, the tool auto-generates one under SCREENSHOT_DIR."""
        from amiberry_mcp.server import call_tool

        mock_client = _make_ipc_client_mock(success=True)

        saved_filename = None

        async def capture_filename(fn):
            nonlocal saved_filename
            saved_filename = fn
            # Create the file so the handler can read it
            Path(fn).parent.mkdir(parents=True, exist_ok=True)
            Path(fn).write_bytes(_MINIMAL_PNG)
            return True

        mock_client.screenshot = AsyncMock(side_effect=capture_filename)
        with _patch_ipc_client(mock_client):
            await call_tool("runtime_screenshot_view", {})

        assert saved_filename is not None, "screenshot() was never called"
        assert "debug_" in saved_filename
        assert saved_filename.endswith(".png")

        # Cleanup
        try:
            Path(saved_filename).unlink(missing_ok=True)
        except Exception:
            pass

    @pytest.mark.asyncio
    async def test_screenshot_view_file_not_found(self, tmp_path):
        """If the IPC succeeds but the file doesn't appear, report the problem."""
        from amiberry_mcp.server import call_tool

        nonexistent = tmp_path / "ghost.png"

        mock_client = _make_ipc_client_mock(success=True)
        with _patch_ipc_client(mock_client):
            result = await call_tool(
                "runtime_screenshot_view", {"filename": str(nonexistent)}
            )

        texts = [r for r in result if r.type == "text"]
        assert any("not found" in t.text.lower() for t in texts)

    @pytest.mark.asyncio
    async def test_screenshot_view_ipc_failure(self):
        """If the IPC call itself fails, a failure message is returned."""
        from amiberry_mcp.server import call_tool

        mock_client = _make_ipc_client_mock(success=False)
        with _patch_ipc_client(mock_client):
            result = await call_tool(
                "runtime_screenshot_view", {"filename": "/tmp/nope.png"}
            )

        texts = [r for r in result if r.type == "text"]
        assert any("failed" in t.text.lower() for t in texts)

    @pytest.mark.asyncio
    async def test_screenshot_view_ipc_connection_error(self):
        """An IPC connection error is caught and reported."""
        from amiberry_mcp.ipc_client import IPCConnectionError
        from amiberry_mcp.server import call_tool

        mock_client = _make_ipc_client_mock()
        mock_client.screenshot = AsyncMock(side_effect=IPCConnectionError("no socket"))
        with _patch_ipc_client(mock_client):
            result = await call_tool(
                "runtime_screenshot_view", {"filename": "/tmp/err.png"}
            )

        texts = [r for r in result if r.type == "text"]
        assert any("error" in t.text.lower() for t in texts)
