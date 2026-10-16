"""Tests for MONITOR command building and reply parsing."""

import json

import pytest

from amiberry_mcp.monitor import (
    MonitorError,
    apply_debug_status,
    build_tool_command,
    format_reply,
    parse_json_reply,
    reply_truncated,
    validate_monitor_text,
)


class TestMonitorText:
    def test_strips_and_refuses_gdb_only_commands(self):
        assert validate_monitor_text("  tasks  ") == "tasks"
        with pytest.raises(MonitorError, match="DEBUG_STEP_OVER"):
            validate_monitor_text("step-over")
        with pytest.raises(MonitorError, match="SAVESTATE"):
            validate_monitor_text("checkpoint save /tmp/x.uss")
        with pytest.raises(MonitorError, match="input event"):
            validate_monitor_text("input sequence 0 0 0 39 1")

    def test_rejects_protocol_characters(self):
        with pytest.raises(MonitorError, match="tabs"):
            validate_monitor_text("tasks\textra")


class TestWatchCommands:
    def test_add_emits_nine_hex_fields(self):
        command = build_tool_command(
            "runtime_watch",
            {
                "action": "add",
                "address": 0x400,
                "size": 2,
                "access": 2,
                "sources": 0x4,
                "value_enabled": 1,
                "value": 0xABCD,
                "value_mask": 0xFFFF,
                "change_only": 1,
                "log_only": 0,
            },
        )
        assert command == "watch add 400 2 2 4 1 abcd ffff 1 0"

    def test_add_rejects_misaligned_and_instruction_source(self):
        with pytest.raises(MonitorError, match="aligned"):
            build_tool_command(
                "runtime_watch",
                {"action": "add", "address": 1, "size": 2, "access": 1, "sources": 2},
            )
        with pytest.raises(MonitorError, match="instruction"):
            build_tool_command(
                "runtime_watch",
                {"action": "add", "address": 0, "size": 2, "access": 1, "sources": 1},
            )

    def test_range_requires_matching_fetch_source(self):
        with pytest.raises(MonitorError, match="fetch"):
            build_tool_command(
                "runtime_watch",
                {
                    "action": "range",
                    "address": 0x1000,
                    "size": 16,
                    "access": 4,
                    "sources": 2,
                },
            )
        command = build_tool_command(
            "runtime_watch",
            {
                "action": "range",
                "address": 0x1000,
                "size": 16,
                "access": 4,
                "sources": 1,
            },
        )
        assert command == "watch range 1000 10 4 1 0 0 0 0 0"

    def test_remove_and_clear_events(self):
        assert build_tool_command("runtime_watch", {"action": "remove", "id": 3}) == (
            "watch remove 3"
        )
        assert (
            build_tool_command("runtime_watch", {"action": "clear-events"})
            == "watch clear-events"
        )
        with pytest.raises(MonitorError, match="0..19"):
            build_tool_command("runtime_watch", {"action": "remove", "id": 20})


class TestOtherCommands:
    def test_exception_mask_rejects_reset_vectors(self):
        assert (
            build_tool_command("runtime_exception", {"action": "mask", "mask": "4"})
            == "exception-mask 4"
        )
        with pytest.raises(MonitorError, match="vectors 0 or 1"):
            build_tool_command("runtime_exception", {"action": "mask", "mask": 3})

    def test_process_name_and_address(self):
        assert (
            build_tool_command(
                "runtime_process_break", {"action": "name", "name": "Workbench"}
            )
            == "process-break name Workbench"
        )
        with pytest.raises(MonitorError, match="printable"):
            build_tool_command(
                "runtime_process_break", {"action": "name", "name": "bad\nname"}
            )
        with pytest.raises(MonitorError, match="longword"):
            build_tool_command(
                "runtime_process_break", {"action": "address", "address": 2}
            )

    def test_history_count_is_hex_and_bounded(self):
        assert (
            build_tool_command("runtime_history", {"action": "read", "count": 128})
            == "history read 80"
        )
        with pytest.raises(MonitorError, match="1..128"):
            build_tool_command("runtime_history", {"action": "read", "count": 0})

    def test_mouse_value_is_sign_extended(self):
        command = build_tool_command(
            "runtime_input",
            {"action": "event", "kind": 1, "port": 0, "code": 0, "value": -1},
        )
        assert command == "input event 1 0 0 ffffffff"

    def test_dma_watch_rejects_cpu_mask(self):
        with pytest.raises(MonitorError, match="non-CPU"):
            build_tool_command(
                "runtime_dma_watch",
                {
                    "action": "add",
                    "address": 0,
                    "length": 2,
                    "mode": 2,
                    "mask": 4,
                },
            )

    def test_condition_uses_register_names(self):
        command = build_tool_command(
            "runtime_condition",
            {
                "action": "add",
                "register": "D0",
                "operator": "==",
                "value": 1,
                "mask": 0xFF,
            },
        )
        assert command == "condition add 0 0 1 ff 0"


class TestReplies:
    def test_json_error_and_null(self):
        with pytest.raises(MonitorError, match="not JSON"):
            parse_json_reply(["not-json"])
        assert parse_json_reply(["null"]) is None
        with pytest.raises(MonitorError, match="one JSON line"):
            parse_json_reply(["a", "b"])

    def test_truncated_flag_on_object_and_record(self):
        assert reply_truncated({"tasks": [], "truncated": True}) is True
        assert reply_truncated({"records": [{"text": "x", "truncated": True}]}) is True
        assert reply_truncated({"truncated": False}) is False
        text = format_reply(
            "tasks",
            [json.dumps({"tasks": [], "truncated": True})],
        )
        assert "truncated: true" in text

    def test_debug_status_reason_only_while_stopped(self):
        running = apply_debug_status({"stopped": "0", "reason": "T05awatch:00008298;"})
        assert "reason" not in running
        stopped = apply_debug_status(
            {"stopped": "1", "reason": "T05winuae-entry:00000004;"}
        )
        assert stopped["reason"] == "T05winuae-entry:00000004;"

    def test_dma_id_and_empty_success(self):
        assert format_reply("dma-watch add 0 2 2 200", ["4"]) == "DMA watch id 4"
        assert format_reply("history on", []) == "OK"
