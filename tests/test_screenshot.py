#!/usr/bin/env python3
"""
Unit tests for the screenshot tools (runtime_screenshot and runtime_screenshot_view).

These tests mock the IPC layer so they run without a live Amiberry instance.
"""

import base64
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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

# Minimal valid JPEG (SOI + APP0 header + EOI)
_JPEG_BYTES = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ipc_client_mock(success: bool = True):
    """Return a mock AmiberryIPCClient whose screenshot() returns *success*."""
    mock_client = MagicMock()
    mock_client.screenshot = AsyncMock(return_value=success)
    return mock_client


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
        with patch("amiberry_mcp.server.get_ipc_client", return_value=mock_client):
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
        with patch("amiberry_mcp.server.get_ipc_client", return_value=mock_client):
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
        with patch("amiberry_mcp.server.get_ipc_client", return_value=mock_client):
            result = await call_tool("runtime_screenshot", {"filename": "/tmp/err.png"})

        assert len(result) == 1
        assert "Connection error" in result[0].text or "error" in result[0].text.lower()


# ---------------------------------------------------------------------------
# runtime_screenshot_view (returns image data)
# ---------------------------------------------------------------------------


class TestRuntimeScreenshotView:
    """Tests for the runtime_screenshot_view tool (returns image content)."""

    @pytest.mark.asyncio
    async def test_screenshot_view_generates_file(self, tmp_path):
        """The tool writes a real file and returns image content."""
        from amiberry_mcp.server import call_tool

        screenshot_file = tmp_path / "view_shot.png"
        # Pre-create the file to simulate Amiberry writing it
        screenshot_file.write_bytes(_MINIMAL_PNG)

        mock_client = _make_ipc_client_mock(success=True)
        with patch("amiberry_mcp.server.get_ipc_client", return_value=mock_client):
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
        with patch("amiberry_mcp.server.get_ipc_client", return_value=mock_client):
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
        with patch("amiberry_mcp.server.get_ipc_client", return_value=mock_client):
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
        with patch("amiberry_mcp.server.get_ipc_client", return_value=mock_client):
            result = await call_tool(
                "runtime_screenshot_view", {"filename": str(screenshot_file)}
            )

        images = [r for r in result if r.type == "image"]
        if images:
            assert images[0].mimeType == "image/jpeg"

    @pytest.mark.asyncio
    async def test_screenshot_view_defaults_to_png_for_unknown(self, tmp_path):
        """Unknown magic bytes default to image/png (Amiberry's native format)."""
        from amiberry_mcp.server import call_tool

        screenshot_file = tmp_path / "shot.bin"
        # Write some arbitrary bytes that aren't JPEG, GIF, or WebP
        screenshot_file.write_bytes(b"\x00\x01\x02\x03" * 64)

        mock_client = _make_ipc_client_mock(success=True)
        with patch("amiberry_mcp.server.get_ipc_client", return_value=mock_client):
            result = await call_tool(
                "runtime_screenshot_view", {"filename": str(screenshot_file)}
            )

        images = [r for r in result if r.type == "image"]
        if images:
            assert images[0].mimeType == "image/png"

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
        with patch("amiberry_mcp.server.get_ipc_client", return_value=mock_client):
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
        with patch("amiberry_mcp.server.get_ipc_client", return_value=mock_client):
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
        with patch("amiberry_mcp.server.get_ipc_client", return_value=mock_client):
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
        with patch("amiberry_mcp.server.get_ipc_client", return_value=mock_client):
            result = await call_tool(
                "runtime_screenshot_view", {"filename": "/tmp/err.png"}
            )

        texts = [r for r in result if r.type == "text"]
        assert any("error" in t.text.lower() for t in texts)
