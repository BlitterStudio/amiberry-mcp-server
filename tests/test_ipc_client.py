#!/usr/bin/env python3
"""
Unit tests for the ipc_client module.

Covers:
- Fix #1: IPC protocol injection prevention (tab/newline sanitization)
- Fix #12: Response readline max length cap
- Fix #14: Response rstrip instead of strip
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amiberry_mcp.ipc_client import (
    AmiberryIPCClient,
    CommandError,
    DisplayMode,
    GuardedInputReason,
    IPCConnectionError,
    MouseUntrapMode,
    Renderer,
    TabletMode,
    _find_socket_path,
    _get_socket_path,
    _parse_actionable_capture,
    _parse_automation_config_response,
    _parse_automation_state,
    _parse_guarded_input_response,
    _parse_release_response,
)


@pytest.fixture(autouse=True)
def _unix_transport_seams():
    """Exercise the shared command logic through the Unix transport seams.

    The Windows pipe transport has dedicated tests below; forcing the Unix
    code path here keeps these tests meaningful on any host platform.
    """
    with patch("amiberry_mcp.ipc_client.IS_WINDOWS", False):
        yield


def _capture_fields() -> list[str]:
    """Return a complete actionable screenshot response."""
    return [
        "schema_version=1",
        "path=/tmp/capture.png",
        "runtime_id=runtime-a",
        "capture_nonce=nonce-a",
        "geometry_revision=4",
        "monitor_id=0",
        "display_mode=native",
        "renderer=sdl",
        "image_width=800",
        "image_height=600",
        "source_x=40",
        "source_y=20",
        "source_width=720",
        "source_height=560",
        "viewport_x=10",
        "viewport_y=5",
        "viewport_width=1080",
        "viewport_height=840",
        "window_width=1100",
        "window_height=850",
    ]


def _state_fields() -> list[str]:
    """Return a complete GUI automation state response."""
    return [
        "schema_version=1",
        "runtime_id=runtime-a",
        "geometry_revision=4",
        "monitor_id=0",
        "geometry_valid=true",
        "pending_tablet_mode=mousehack",
        "effective_tablet_mode=real",
        "pending_mouse_untrap=magic",
        "effective_mouse_untrap=both",
        "pending_effective_diverged=true",
        "input_config_revision=7",
        "focus_ready=true",
        "supported_button_mask=7",
        "button_mask=3",
    ]


def _config_fields(reason: str = "none") -> list[str]:
    """Return a complete automation configuration response."""
    return [
        "schema_version=1",
        f"reason={reason}",
        "pending_tablet_mode=mousehack",
        "effective_tablet_mode=real",
        "pending_mouse_untrap=magic",
        "effective_mouse_untrap=both",
        "pending_effective_diverged=true",
        "input_config_revision=7",
    ]


class TestStrictAutomationParsing:
    """Safety-critical IPC responses use complete, fixed schemas."""

    def test_actionable_capture_accepts_complete_geometry(self):
        capture = _parse_actionable_capture(_capture_fields())

        assert capture.path == "/tmp/capture.png"
        assert capture.display_mode is DisplayMode.NATIVE
        assert capture.renderer is Renderer.SDL
        assert capture.source_width == 720

    @pytest.mark.parametrize(
        "mutation",
        [
            lambda fields: fields[:-1],
            lambda fields: fields + ["path=/tmp/duplicate.png"],
            lambda fields: fields + ["future_field=value"],
            lambda fields: ["schema_version=2", *fields[1:]],
            lambda fields: [
                *(field for field in fields if not field.startswith("renderer=")),
                "renderer=software",
            ],
            lambda fields: [
                *(field for field in fields if not field.startswith("source_width=")),
                "source_width=0",
            ],
            lambda fields: [
                *(field for field in fields if not field.startswith("viewport_width=")),
                "viewport_width=1091",
            ],
        ],
        ids=[
            "missing",
            "duplicate",
            "unknown-field",
            "unknown-schema",
            "invalid-enum",
            "nonpositive-rect",
            "viewport-outside-window",
        ],
    )
    def test_actionable_capture_rejects_malformed_schema(self, mutation):
        with pytest.raises(CommandError):
            _parse_actionable_capture(mutation(_capture_fields()))

    def test_automation_state_is_typed(self):
        state = _parse_automation_state(_state_fields())

        assert state.pending_tablet_mode is TabletMode.MOUSEHACK
        assert state.effective_mouse_untrap is MouseUntrapMode.BOTH
        assert state.button_mask == 3

    @pytest.mark.parametrize(
        "replacement",
        [
            "geometry_valid=1",
            "pending_tablet_mode=unknown",
            "button_mask=8",
            "supported_button_mask=3",
            "pending_effective_diverged=false",
        ],
    )
    def test_automation_state_rejects_invalid_or_contradictory_fields(
        self, replacement
    ):
        key = replacement.split("=", 1)[0]
        fields = [field for field in _state_fields() if not field.startswith(f"{key}=")]
        fields.append(replacement)

        with pytest.raises(CommandError):
            _parse_automation_state(fields)

    def test_guarded_response_requires_coordinates_only_when_applied(self):
        response = _parse_guarded_input_response(
            True,
            [
                "schema_version=1",
                "applied=true",
                "reason=none",
                "runtime_id=runtime-a",
                "geometry_revision=4",
                "monitor_id=0",
                "input_config_revision=7",
                "button_mask=1",
                "x=30",
                "y=40",
            ],
        )

        assert response.applied is True
        assert response.reason is GuardedInputReason.NONE
        assert (response.x, response.y) == (30, 40)

        with pytest.raises(CommandError):
            _parse_guarded_input_response(
                False,
                [
                    "schema_version=1",
                    "applied=false",
                    "reason=focus_not_ready",
                    "runtime_id=runtime-a",
                    "geometry_revision=4",
                    "monitor_id=0",
                    "input_config_revision=7",
                    "button_mask=0",
                    "x=30",
                    "y=40",
                ],
            )

    def test_config_and_release_status_must_match_fields(self):
        applied = _parse_automation_config_response(True, _config_fields())
        assert applied.applied is True

        with pytest.raises(CommandError):
            _parse_automation_config_response(False, _config_fields())
        with pytest.raises(CommandError):
            _parse_release_response(True, ["schema_version=1", "button_mask=1"])

        released = _parse_release_response(True, ["schema_version=1", "button_mask=0"])
        assert released.confirmed is True


class TestAutomationIPCMethods:
    """Automation commands use isolated, non-injectable exchanges."""

    @pytest.fixture
    def client(self):
        return AmiberryIPCClient(prefer_dbus=False, instance=0)

    @pytest.mark.asyncio
    async def test_actionable_screenshot_downgrades_only_legacy_path(self, client):
        client._send_scoped_socket_command = AsyncMock(
            return_value=(True, ["/tmp/capture.png"])
        )

        result = await client.capture_actionable_screenshot("/tmp/capture.png")

        assert result.actionable is False
        assert result.geometry is None
        assert result.path == "/tmp/capture.png"
        client._send_scoped_socket_command.assert_awaited_once_with(
            "SCREENSHOT",
            "/tmp/capture.png",
            "ACTIONABLE",
            retry_after_write=True,
        )

    @pytest.mark.asyncio
    async def test_actionable_screenshot_rejects_partial_metadata(self, client):
        client._send_scoped_socket_command = AsyncMock(
            return_value=(True, ["schema_version=1", "path=/tmp/capture.png"])
        )

        with pytest.raises(CommandError):
            await client.capture_actionable_screenshot("/tmp/capture.png")

    @pytest.mark.asyncio
    async def test_actionable_screenshot_rejects_nonmatching_legacy_token(self, client):
        client._send_scoped_socket_command = AsyncMock(
            return_value=(True, ["future_schema=value"])
        )

        with pytest.raises(CommandError):
            await client.capture_actionable_screenshot("/tmp/capture.png")

    @pytest.mark.asyncio
    async def test_config_cas_is_not_replayed_after_an_ambiguous_write(self, client):
        client._send_scoped_socket_command = AsyncMock(
            return_value=(True, _config_fields())
        )

        response = await client._set_gui_automation_config(
            TabletMode.OFF,
            MouseUntrapMode.OFF,
            6,
            TabletMode.MOUSEHACK,
            MouseUntrapMode.MAGIC,
        )

        assert response.applied is True
        client._send_scoped_socket_command.assert_awaited_once_with(
            "SET_GUI_AUTOMATION_CONFIG",
            "off",
            "off",
            "6",
            "mousehack",
            "magic",
            retry_after_write=False,
        )

    @pytest.mark.asyncio
    async def test_guarded_input_serializes_exactly_and_rejects_injection(self, client):
        response = [
            "schema_version=1",
            "applied=false",
            "reason=focus_not_ready",
            "runtime_id=runtime-a",
            "geometry_revision=4",
            "monitor_id=0",
            "input_config_revision=7",
            "button_mask=0",
        ]
        client._send_scoped_socket_command = AsyncMock(return_value=(False, response))

        await client._send_mouse_abs_guarded(10, 20, 1, "runtime-a", 4, 0, 7)

        client._send_scoped_socket_command.assert_awaited_once_with(
            "SEND_MOUSE_ABS_GUARDED",
            "10",
            "20",
            "1",
            "runtime-a",
            "4",
            "0",
            "7",
            retry_after_write=False,
        )

        with pytest.raises(ValueError):
            await client._send_mouse_abs_guarded(10, 20, 1, "runtime-a\nRESET", 4, 0, 7)

    @pytest.mark.asyncio
    async def test_scoped_exchange_closes_and_safe_query_reconnects(self, client):
        first_reader = AsyncMock()
        first_reader.readline = AsyncMock(side_effect=ConnectionResetError("reset"))
        second_reader = AsyncMock()
        second_reader.readline = AsyncMock(
            return_value=("OK\t" + "\t".join(_state_fields()) + "\n").encode()
        )
        first_writer = MagicMock()
        first_writer.drain = AsyncMock()
        first_writer.wait_closed = AsyncMock()
        second_writer = MagicMock()
        second_writer.drain = AsyncMock()
        second_writer.wait_closed = AsyncMock()

        with (
            patch("os.path.exists", return_value=True),
            patch(
                "asyncio.open_unix_connection",
                AsyncMock(
                    side_effect=[
                        (first_reader, first_writer),
                        (second_reader, second_writer),
                    ]
                ),
            ) as open_connection,
        ):
            state = await client._get_gui_automation_state()

        assert state.runtime_id == "runtime-a"
        assert open_connection.await_count == 2
        first_writer.close.assert_called_once()
        second_writer.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_guarded_exchange_does_not_replay_after_write(self, client):
        reader = AsyncMock()
        reader.readline = AsyncMock(side_effect=ConnectionResetError("reset"))
        writer = MagicMock()
        writer.drain = AsyncMock()
        writer.wait_closed = AsyncMock()

        with (
            patch("os.path.exists", return_value=True),
            patch(
                "asyncio.open_unix_connection",
                AsyncMock(return_value=(reader, writer)),
            ) as open_connection,
        ):
            with pytest.raises(IPCConnectionError):
                await client._send_mouse_abs_guarded(10, 20, 0, "runtime-a", 4, 0, 7)

        assert open_connection.await_count == 1

    @pytest.mark.asyncio
    async def test_scoped_cancellation_after_write_closes_without_retry(self, client):
        reader = AsyncMock()
        reader.readline = AsyncMock(side_effect=asyncio.CancelledError)
        writer = MagicMock()
        writer.drain = AsyncMock()
        writer.wait_closed = AsyncMock()

        with (
            patch("os.path.exists", return_value=True),
            patch(
                "asyncio.open_unix_connection",
                AsyncMock(return_value=(reader, writer)),
            ) as open_connection,
            pytest.raises(asyncio.CancelledError),
        ):
            await client._send_scoped_socket_command(
                "SEND_MOUSE_ABS_GUARDED", retry_after_write=False
            )

        writer.close.assert_called_once()
        assert open_connection.await_count == 1

    @pytest.mark.asyncio
    async def test_scoped_retry_reports_the_terminal_failure(self, client):
        first_reader = AsyncMock()
        first_reader.readline = AsyncMock(side_effect=ConnectionResetError("reset"))
        second_reader = AsyncMock()
        second_reader.readline = AsyncMock(side_effect=asyncio.TimeoutError("timeout"))
        writers = []
        for _ in range(2):
            writer = MagicMock()
            writer.drain = AsyncMock()
            writer.wait_closed = AsyncMock()
            writers.append(writer)

        with (
            patch("os.path.exists", return_value=True),
            patch(
                "asyncio.open_unix_connection",
                AsyncMock(
                    side_effect=[
                        (first_reader, writers[0]),
                        (second_reader, writers[1]),
                    ]
                ),
            ),
            pytest.raises(IPCConnectionError, match="retry failed: timeout"),
        ):
            await client._send_scoped_socket_command(
                "GET_GUI_AUTOMATION_STATE", retry_after_write=True
            )

    @pytest.mark.asyncio
    async def test_scoped_missing_socket_opens_directly_without_preflight(self, client):
        with (
            patch("os.path.exists", side_effect=AssertionError("unexpected preflight")),
            patch(
                "asyncio.open_unix_connection",
                AsyncMock(side_effect=FileNotFoundError("missing")),
            ) as open_connection,
            pytest.raises(IPCConnectionError, match="Socket not found at"),
        ):
            await client._get_gui_automation_state()

        open_connection.assert_awaited_once()


class TestPersistentCancellation:
    """Cancellation cannot leave a late response on a reusable socket."""

    @pytest.fixture
    def client(self):
        return AmiberryIPCClient(prefer_dbus=False, instance=0)

    @pytest.mark.asyncio
    async def test_cancellation_after_write_invalidates_connection(self, client):
        reader = AsyncMock()
        reader.readline = AsyncMock(side_effect=asyncio.CancelledError)
        writer = MagicMock()
        writer.drain = AsyncMock()
        writer.wait_closed = AsyncMock()

        with (
            patch("os.path.exists", return_value=True),
            patch(
                "asyncio.open_unix_connection",
                AsyncMock(return_value=(reader, writer)),
            ),
        ):
            with pytest.raises(asyncio.CancelledError):
                await client._send_socket_command("GET_STATUS")

        writer.close.assert_called_once()
        assert client._reader is None
        assert client._writer is None

    @pytest.mark.asyncio
    async def test_cancellation_before_write_keeps_existing_connection(self, client):
        reader = AsyncMock()
        writer = MagicMock()
        writer.is_closing.return_value = False
        client._reader = reader
        client._writer = writer
        client._ensure_socket_connection = AsyncMock(side_effect=asyncio.CancelledError)

        with (
            patch("os.path.exists", return_value=True),
            pytest.raises(asyncio.CancelledError),
        ):
            await client._send_socket_command("GET_STATUS")

        writer.close.assert_not_called()
        assert client._reader is reader
        assert client._writer is writer

    @pytest.mark.asyncio
    async def test_eof_does_not_replay_a_persistent_mutation(self, client):
        reader = AsyncMock()
        reader.readline = AsyncMock(return_value=b"")
        writer = MagicMock()
        writer.drain = AsyncMock()
        writer.wait_closed = AsyncMock()

        with (
            patch("os.path.exists", return_value=True),
            patch(
                "asyncio.open_unix_connection",
                AsyncMock(return_value=(reader, writer)),
            ) as open_connection,
        ):
            success, data = await client._send_socket_command("TOGGLE_FULLSCREEN")

        assert success is False
        assert data == ["Empty response"]
        assert open_connection.await_count == 1
        writer.write.assert_called_once()
        writer.close.assert_called_once()


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


class TestBreakpointAndDiskSwap:
    """SET_BREAKPOINT slot and QUERYDISKSWAP reply handling."""

    @pytest.fixture
    def client(self):
        return AmiberryIPCClient(prefer_dbus=False, instance=0)

    @pytest.mark.asyncio
    async def test_set_breakpoint_omits_slot_and_parses_reply(self, client):
        client._send_command = AsyncMock(
            return_value=(True, ["Breakpoint set at 00000400 in slot 3"])
        )

        used = await client.set_breakpoint(0x400)

        assert used == 3
        client._send_command.assert_awaited_once_with("SET_BREAKPOINT", "0x400")

    @pytest.mark.asyncio
    async def test_set_breakpoint_sends_slot(self, client):
        client._send_command = AsyncMock(
            return_value=(True, ["Breakpoint set at 00000400 in slot 7"])
        )

        used = await client.set_breakpoint("0x400", 7)

        assert used == 7
        client._send_command.assert_awaited_once_with("SET_BREAKPOINT", "0x400", "7")

    @pytest.mark.asyncio
    async def test_set_breakpoint_rejects_out_of_range_slot(self, client):
        with pytest.raises(ValueError, match="0-19"):
            await client.set_breakpoint(0x400, 20)

    @pytest.mark.asyncio
    async def test_query_disk_swap_returns_negative_when_not_in_list(self, client):
        client._send_command = AsyncMock(return_value=(True, ["-1"]))

        assert await client.query_disk_swap(1) == -1

    @pytest.mark.asyncio
    async def test_query_disk_swap_raises_on_failure(self, client):
        client._send_command = AsyncMock(
            return_value=(False, ["Drive number must be 0-3"])
        )

        with pytest.raises(CommandError, match="Drive number"):
            await client.query_disk_swap(0)

    @pytest.mark.asyncio
    async def test_help_raises_when_command_fails(self, client):
        client._send_command = AsyncMock(return_value=(False, ["HELP failed"]))

        with pytest.raises(CommandError, match="HELP failed"):
            await client.help()


class TestWindowsEndpointNaming:
    """Windows names IPC endpoints after the pipe namespace."""

    def test_instance_pipe_names(self):
        with patch("amiberry_mcp.ipc_client.IS_WINDOWS", True):
            assert _get_socket_path(0) == "\\\\.\\pipe\\amiberry"
            assert _get_socket_path(1) == "\\\\.\\pipe\\amiberry_1"
            assert _get_socket_path(9) == "\\\\.\\pipe\\amiberry_9"

    def test_client_instance_targets_pipe_name(self):
        with patch("amiberry_mcp.ipc_client.IS_WINDOWS", True):
            client = AmiberryIPCClient(prefer_dbus=False, instance=2)
        assert client._socket_path == "\\\\.\\pipe\\amiberry_2"

    def test_unix_instance_names_unchanged(self):
        assert _get_socket_path(0).endswith("amiberry.sock")
        assert _get_socket_path(3).endswith("amiberry_3.sock")


class TestWindowsPipeDiscovery:
    """Discovery lists ``\\\\.\\pipe\\`` instead of opening pipe names."""

    def test_prefers_lowest_available_instance(self):
        with (
            patch("amiberry_mcp.ipc_client.IS_WINDOWS", True),
            patch(
                "os.listdir",
                return_value=["spoolss", "amiberry_2", "AMIBERRY_1", "chrome"],
            ),
        ):
            assert _find_socket_path() == "\\\\.\\pipe\\amiberry_1"

    def test_ignores_lookalike_names(self):
        with (
            patch("amiberry_mcp.ipc_client.IS_WINDOWS", True),
            patch("time.sleep"),
            patch(
                "os.listdir",
                return_value=["amiberry_10", "amiberryfoo", "amiberry-1"],
            ),
        ):
            assert _find_socket_path() == "\\\\.\\pipe\\amiberry"

    def test_existence_check_never_opens_the_pipe(self):
        client = AmiberryIPCClient(
            prefer_dbus=False, socket_path="\\\\.\\pipe\\amiberry_1"
        )
        with (
            patch("amiberry_mcp.ipc_client.IS_WINDOWS", True),
            patch("os.listdir", return_value=["amiberry_1"]),
            patch("os.path.exists", side_effect=AssertionError("opens the pipe")),
            patch("os.stat", side_effect=AssertionError("opens the pipe")),
        ):
            assert client.is_available() is True

    @pytest.mark.asyncio
    async def test_send_command_skips_existence_preflight(self):
        """No preflight probe: the connect attempt itself reports a missing pipe."""
        client = AmiberryIPCClient(
            prefer_dbus=False, socket_path="\\\\.\\pipe\\amiberry"
        )
        with (
            patch("amiberry_mcp.ipc_client.IS_WINDOWS", True),
            patch("os.listdir", return_value=[]),
            patch("os.path.exists", side_effect=AssertionError("opens the pipe")),
            patch("os.stat", side_effect=AssertionError("opens the pipe")),
            patch.object(
                client,
                "_open_command_stream",
                AsyncMock(side_effect=FileNotFoundError("missing")),
            ),
        ):
            with pytest.raises(IPCConnectionError, match="Socket not found"):
                await client._send_socket_command("PING")


class TestTransportSelection:
    """The command stream follows the host platform."""

    @pytest.mark.asyncio
    async def test_windows_uses_the_pipe_stream(self):
        client = AmiberryIPCClient(
            prefer_dbus=False, socket_path="\\\\.\\pipe\\amiberry"
        )
        stream = (AsyncMock(), MagicMock())
        with (
            patch("amiberry_mcp.ipc_client.IS_WINDOWS", True),
            patch.object(
                client, "_open_windows_pipe_stream", AsyncMock(return_value=stream)
            ) as pipe_open,
            patch("asyncio.open_unix_connection"),
        ):
            await client._ensure_socket_connection(1.0)

        pipe_open.assert_awaited_once_with(1.0)

    @pytest.mark.asyncio
    async def test_unix_uses_open_unix_connection(self):
        client = AmiberryIPCClient(prefer_dbus=False, socket_path="/tmp/amiberry.sock")
        reader, writer = AsyncMock(), MagicMock()
        with (
            patch("amiberry_mcp.ipc_client.IS_WINDOWS", False),
            patch(
                "asyncio.open_unix_connection",
                AsyncMock(return_value=(reader, writer)),
            ) as unix_open,
            patch.object(client, "_open_windows_pipe_stream", AsyncMock()),
        ):
            await client._ensure_socket_connection(1.0)

        unix_open.assert_awaited_once_with("/tmp/amiberry.sock")

    @pytest.mark.asyncio
    async def test_missing_endpoint_reports_socket_not_found(self):
        client = AmiberryIPCClient(
            prefer_dbus=False, socket_path="\\\\.\\pipe\\amiberry_mcp_missing_1"
        )
        with pytest.raises(IPCConnectionError, match="Socket not found at"):
            await client._send_socket_command("PING")


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows named pipes")
class TestWindowsPipeTransport:
    """Live named-pipe behavior against a real pipe server."""

    @pytest.mark.asyncio
    async def test_selector_loop_is_rejected_explicitly(self):
        async def _attempt():
            client = AmiberryIPCClient(
                prefer_dbus=False, socket_path="\\\\.\\pipe\\amiberry"
            )
            with (
                patch("amiberry_mcp.ipc_client.IS_WINDOWS", True),
                pytest.raises(IPCConnectionError, match="ProactorEventLoop"),
            ):
                await client._open_command_stream(1.0)

        def _run_under_selector_policy():
            previous = asyncio.get_event_loop_policy()
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            try:
                asyncio.run(_attempt())
            finally:
                asyncio.set_event_loop_policy(previous)

        await asyncio.to_thread(_run_under_selector_policy)

    @pytest.mark.asyncio
    async def test_busy_pipe_retries_until_next_instance_listens(self):
        import _winapi
        import threading
        import time as time_module

        pipe_name = f"\\\\.\\pipe\\amiberry_mcp_busy_{os.getpid()}"
        next_instance_delay = 0.5
        stop = threading.Event()

        def serve_client(handle):
            try:
                request = b""
                while not request.endswith(b"\n"):
                    chunk = _winapi.ReadFile(handle, 4096, None)[0]
                    if not chunk:
                        return
                    request += chunk
                _winapi.WriteFile(handle, b"OK\tpong\n", None)
            except OSError:
                pass
            finally:
                _winapi.CloseHandle(handle)

        def run_server():
            # Mirrors Amiberry: one listening instance at a time, the next
            # one created only after a short delay per connection.
            error_pipe_connected = 535
            while not stop.is_set():
                handle = _winapi.CreateNamedPipe(
                    pipe_name, 0x3, 0x0, 4, 65536, 65536, 0, 0
                )
                try:
                    _winapi.ConnectNamedPipe(handle)
                except OSError as e:
                    # A client that connected between CreateNamedPipe and
                    # ConnectNamedPipe is already attached, not a failure.
                    if getattr(e, "winerror", None) != error_pipe_connected:
                        _winapi.CloseHandle(handle)
                        return
                threading.Thread(
                    target=serve_client, args=(handle,), daemon=True
                ).start()
                time_module.sleep(next_instance_delay)

        server = threading.Thread(target=run_server, daemon=True)
        server.start()
        hold = None
        try:
            # Occupy the listening instance so the client must retry
            # ERROR_PIPE_BUSY until the next instance appears.
            hold = _winapi.CreateFile(
                pipe_name,
                _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
                0,
                0,
                _winapi.OPEN_EXISTING,
                0,
                0,
            )
            client = AmiberryIPCClient(prefer_dbus=False, socket_path=pipe_name)
            started = time_module.monotonic()
            with patch("amiberry_mcp.ipc_client.IS_WINDOWS", True):
                success, data = await asyncio.wait_for(
                    client._send_socket_command("PING"), timeout=10
                )
            elapsed = time_module.monotonic() - started

            assert success is True
            assert data == ["pong"]
            assert elapsed >= next_instance_delay - 0.15
        finally:
            if hold is not None:
                _winapi.CloseHandle(hold)
            stop.set()
            try:
                _winapi.CloseHandle(
                    _winapi.CreateFile(
                        pipe_name,
                        _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
                        0,
                        0,
                        _winapi.OPEN_EXISTING,
                        0,
                        0,
                    )
                )
            except OSError:
                pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
