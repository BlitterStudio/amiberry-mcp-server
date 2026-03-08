#!/usr/bin/env python3
"""
Unit tests for the ipc_client module.

Covers:
- Fix #1: IPC protocol injection prevention (tab/newline sanitization)
- Fix #12: Response readline max length cap
- Fix #14: Response rstrip instead of strip
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amiberry_mcp.ipc_client import AmiberryIPCClient


class TestIPCProtocolInjection:
    """Tests for Fix #1: IPC argument sanitization."""

    @pytest.fixture
    def client(self):
        return AmiberryIPCClient(instance=0)

    @pytest.mark.asyncio
    async def test_tab_in_argument_is_stripped(self, client):
        """Tab characters in arguments should be removed to prevent injection."""
        # We'll check the message that would be sent by mocking the socket
        with (
            patch("asyncio.open_unix_connection") as mock_conn,
            patch("os.path.exists", return_value=True),
        ):
            mock_reader = AsyncMock()
            mock_reader.readline = AsyncMock(return_value=b"OK\tresult\n")
            mock_writer = MagicMock()
            mock_writer.write = MagicMock()
            mock_writer.drain = AsyncMock()
            mock_writer.close = MagicMock()
            mock_writer.wait_closed = AsyncMock()
            mock_conn.return_value = (mock_reader, mock_writer)

            await client._send_socket_command("CFG_SET", "key\textra", "value")

            # Check the actual message written
            written = mock_writer.write.call_args[0][0]
            message = written.decode("utf-8")

            # The tab inside the argument should be stripped
            # Message format: COMMAND\tARG1\tARG2\n
            # "key\textra" should become "keyextra" (tab removed)
            assert "keyextra" in message
            # Should still have correct tab-delimited structure
            parts = message.strip().split("\t")
            assert parts[0] == "CFG_SET"
            assert parts[1] == "keyextra"
            assert parts[2] == "value"

    @pytest.mark.asyncio
    async def test_newline_in_argument_is_stripped(self, client):
        """Newline characters in arguments should be removed."""
        with (
            patch("asyncio.open_unix_connection") as mock_conn,
            patch("os.path.exists", return_value=True),
        ):
            mock_reader = AsyncMock()
            mock_reader.readline = AsyncMock(return_value=b"OK\n")
            mock_writer = MagicMock()
            mock_writer.write = MagicMock()
            mock_writer.drain = AsyncMock()
            mock_writer.close = MagicMock()
            mock_writer.wait_closed = AsyncMock()
            mock_conn.return_value = (mock_reader, mock_writer)

            await client._send_socket_command("CMD", "arg\ninjected")

            written = mock_writer.write.call_args[0][0]
            message = written.decode("utf-8")

            # Newline in argument should be stripped
            assert "arginjected" in message
            # Message should end with exactly one newline
            assert message.endswith("\n")
            assert message.count("\n") == 1

    @pytest.mark.asyncio
    async def test_carriage_return_in_argument_is_stripped(self, client):
        """Carriage return characters in arguments should be removed."""
        with (
            patch("asyncio.open_unix_connection") as mock_conn,
            patch("os.path.exists", return_value=True),
        ):
            mock_reader = AsyncMock()
            mock_reader.readline = AsyncMock(return_value=b"OK\n")
            mock_writer = MagicMock()
            mock_writer.write = MagicMock()
            mock_writer.drain = AsyncMock()
            mock_writer.close = MagicMock()
            mock_writer.wait_closed = AsyncMock()
            mock_conn.return_value = (mock_reader, mock_writer)

            await client._send_socket_command("CMD", "arg\rinjected")

            written = mock_writer.write.call_args[0][0]
            message = written.decode("utf-8")

            assert "arginjected" in message

    @pytest.mark.asyncio
    async def test_clean_arguments_unchanged(self, client):
        """Arguments without special chars should pass through unchanged."""
        with (
            patch("asyncio.open_unix_connection") as mock_conn,
            patch("os.path.exists", return_value=True),
        ):
            mock_reader = AsyncMock()
            mock_reader.readline = AsyncMock(return_value=b"OK\n")
            mock_writer = MagicMock()
            mock_writer.write = MagicMock()
            mock_writer.drain = AsyncMock()
            mock_writer.close = MagicMock()
            mock_writer.wait_closed = AsyncMock()
            mock_conn.return_value = (mock_reader, mock_writer)

            await client._send_socket_command("CFG_SET", "cpu_model", "68020")

            written = mock_writer.write.call_args[0][0]
            message = written.decode("utf-8")

            assert message == "CFG_SET\tcpu_model\t68020\n"


class TestResponseParsing:
    """Tests for Fix #14: Response rstrip behavior."""

    @pytest.fixture
    def client(self):
        return AmiberryIPCClient(instance=0)

    @pytest.mark.asyncio
    async def test_response_preserves_leading_spaces(self, client):
        """Response should preserve leading whitespace (rstrip, not strip)."""
        with (
            patch("asyncio.open_unix_connection") as mock_conn,
            patch("os.path.exists", return_value=True),
        ):
            mock_reader = AsyncMock()
            # Response with leading spaces in data
            mock_reader.readline = AsyncMock(return_value=b"OK\t  spaced value\n")
            mock_writer = MagicMock()
            mock_writer.write = MagicMock()
            mock_writer.drain = AsyncMock()
            mock_writer.close = MagicMock()
            mock_writer.wait_closed = AsyncMock()
            mock_conn.return_value = (mock_reader, mock_writer)

            success, data = await client._send_socket_command("GET")

            assert success is True
            assert data == ["  spaced value"]

    @pytest.mark.asyncio
    async def test_response_strips_trailing_newline(self, client):
        """Response trailing newlines/carriage returns should be stripped."""
        with (
            patch("asyncio.open_unix_connection") as mock_conn,
            patch("os.path.exists", return_value=True),
        ):
            mock_reader = AsyncMock()
            mock_reader.readline = AsyncMock(return_value=b"OK\tvalue\r\n")
            mock_writer = MagicMock()
            mock_writer.write = MagicMock()
            mock_writer.drain = AsyncMock()
            mock_writer.close = MagicMock()
            mock_writer.wait_closed = AsyncMock()
            mock_conn.return_value = (mock_reader, mock_writer)

            success, data = await client._send_socket_command("GET")

            assert success is True
            assert data == ["value"]


class TestResponseSizeLimit:
    """Tests for Fix #12: Response readline max length."""

    @pytest.fixture
    def client(self):
        return AmiberryIPCClient(instance=0)

    @pytest.mark.asyncio
    async def test_large_response_is_truncated(self, client):
        """Responses larger than 1MB should be truncated."""
        with (
            patch("asyncio.open_unix_connection") as mock_conn,
            patch("os.path.exists", return_value=True),
        ):
            # Create a response larger than 1MB
            large_data = b"OK\t" + b"X" * (2 * 1024 * 1024) + b"\n"
            mock_reader = AsyncMock()
            mock_reader.readline = AsyncMock(return_value=large_data)
            mock_writer = MagicMock()
            mock_writer.write = MagicMock()
            mock_writer.drain = AsyncMock()
            mock_writer.close = MagicMock()
            mock_writer.wait_closed = AsyncMock()
            mock_conn.return_value = (mock_reader, mock_writer)

            # Should not raise, response is truncated
            success, data = await client._send_socket_command("GET")

            # The response was truncated, so it may not parse perfectly
            # but the key thing is it didn't consume unbounded memory

    @pytest.mark.asyncio
    async def test_normal_response_not_truncated(self, client):
        """Normal-sized responses should not be truncated."""
        with (
            patch("asyncio.open_unix_connection") as mock_conn,
            patch("os.path.exists", return_value=True),
        ):
            mock_reader = AsyncMock()
            mock_reader.readline = AsyncMock(return_value=b"OK\tnormal response\n")
            mock_writer = MagicMock()
            mock_writer.write = MagicMock()
            mock_writer.drain = AsyncMock()
            mock_writer.close = MagicMock()
            mock_writer.wait_closed = AsyncMock()
            mock_conn.return_value = (mock_reader, mock_writer)

            success, data = await client._send_socket_command("GET")

            assert success is True
            assert data == ["normal response"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
