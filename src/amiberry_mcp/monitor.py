"""Build and parse Amiberry ``MONITOR`` commands.

Numeric arguments are hexadecimal, matching ``docs/gdb-debugging.md``.
State-changing commands require ``PAUSE`` or a stopped native debugger.
``step-over``, ``checkpoint``, and ``input sequence`` are refused: use
``DEBUG_STEP_OVER``, ``SAVESTATE``/``LOADSTATE``, and ``input event``.
"""

from __future__ import annotations

import json
from typing import Any

MW_MASK_ALL = 0x7FFFFFFF
MW_MASK_CPU_I = 0x1
WATCH_LIMIT = 0x7FFF0000
WATCH_SLOTS = 20
BREAKPOINT_SLOTS = 20
MEMORY_CHECK_MAX = 8 * 1024 * 1024
HISTORY_MAX = 128
_MAX_TEXT = 4096

# register, access width or range length, access kind, source mask,
# value enabled, value, value mask, change-only, log-only.
WATCH_FIELDS = (
    "address",
    "size",
    "access",
    "sources",
    "value_enabled",
    "value",
    "value_mask",
    "change_only",
    "log_only",
)

_CONDITION_REGISTERS = {
    "d0": 0,
    "d1": 1,
    "d2": 2,
    "d3": 3,
    "d4": 4,
    "d5": 5,
    "d6": 6,
    "d7": 7,
    "a0": 8,
    "a1": 9,
    "a2": 10,
    "a3": 11,
    "a4": 12,
    "a5": 13,
    "a6": 14,
    "a7": 15,
    "usp": 17,
    "msp": 18,
    "isp": 19,
    "vbr": 20,
    "sr": 21,
}
_CONDITION_OPERATORS = {
    "==": 0,
    "!=": 1,
    "<=": 2,
    ">=": 3,
    "<": 4,
    ">": 5,
}
_REFUSED = (
    ("step-over", "DEBUG_STEP_OVER"),
    ("checkpoint", "SAVESTATE or LOADSTATE"),
    ("input sequence", "input event"),
)
_STATE_CHANGING = {
    "runtime_watch": {"add", "range", "remove", "clear-events"},
    "runtime_dma_watch": {"add", "remove"},
    "runtime_condition": {"add", "remove"},
    "runtime_exception": {"mask"},
    "runtime_history": {"on", "off"},
    "runtime_guest_output": {"on", "off", "clear"},
    "runtime_process_break": {"name", "address", "clear"},
}


class MonitorError(ValueError):
    """A monitor command or reply cannot be used as given."""


def state_changing(tool: str, action: str | None) -> bool:
    """Return whether this tool action installs or clears monitor state."""
    return action in _STATE_CHANGING.get(tool, set())


def _u32(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MonitorError(f"{name} must be an integer")
    if value < 0 or value > 0xFFFFFFFF:
        raise MonitorError(f"{name} must be 0..0xffffffff")
    return value


def _hex(value: int) -> str:
    return f"{value:x}"


def _flag(value: Any, name: str) -> int:
    number = _u32(value, name)
    if number > 1:
        raise MonitorError(f"{name} must be 0 or 1")
    return number


def _slot(value: Any, limit: int, name: str) -> int:
    number = _u32(value, name)
    if number >= limit:
        raise MonitorError(f"{name} must be 0..{limit - 1}")
    return number


def _structure_address(value: Any, name: str) -> int:
    number = _u32(value, name)
    if number == 0 or number & 3:
        raise MonitorError(f"{name} must be a non-zero longword address")
    return number


def validate_monitor_text(text: str) -> str:
    """Return monitor command text safe to send as one IPC argument."""
    if not isinstance(text, str) or not text.strip():
        raise MonitorError("MONITOR text is required")
    command = text.strip()
    if any(char in command for char in ("\t", "\n", "\r")):
        raise MonitorError("MONITOR text cannot contain tabs or newlines")
    if len(command) > _MAX_TEXT:
        raise MonitorError(f"MONITOR text is longer than {_MAX_TEXT} characters")
    lowered = command.lower()
    for prefix, replacement in _REFUSED:
        if lowered == prefix or lowered.startswith(prefix + " "):
            raise MonitorError(f"{prefix} is refused over IPC; use {replacement}")
    return command


def parse_json_reply(lines: list[str]) -> Any:
    """Parse a one-line JSON monitor reply, including ``null``."""
    if len(lines) != 1:
        raise MonitorError(f"expected one JSON line, got {len(lines)}")
    try:
        return json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise MonitorError(f"MONITOR reply is not JSON: {lines[0]!r}") from exc


def reply_truncated(payload: Any) -> bool:
    """Return whether a monitor JSON object hit a reply or record limit."""
    if not isinstance(payload, dict):
        return False
    if payload.get("truncated") is True:
        return True
    records = payload.get("records")
    if isinstance(records, list):
        return any(
            isinstance(item, dict) and item.get("truncated") is True for item in records
        )
    return False


def apply_debug_status(fields: dict[str, str]) -> dict[str, str]:
    """Keep ``reason`` only while the native debugger is stopped.

    After ``DEBUG_CONTINUE`` the previous stop text can linger on the wire
    until the next debugger entry. It is meaningful only when ``stopped=1``.
    """
    result = dict(fields)
    reason = result.pop("reason", "")
    if str(result.get("stopped", "0")) == "1":
        result["reason"] = reason
    return result


def _watch_words(arguments: dict[str, Any], *, ranged: bool) -> str:
    address = _u32(arguments.get("address"), "address")
    size = _u32(arguments.get("size"), "size")
    access = _u32(arguments.get("access"), "access")
    sources = _u32(arguments.get("sources"), "sources")
    value_enabled = _flag(arguments.get("value_enabled", 0), "value_enabled")
    value = _u32(arguments.get("value", 0), "value")
    value_mask = _u32(arguments.get("value_mask", 0), "value_mask")
    change_only = _flag(arguments.get("change_only", 0), "change_only")
    log_only = _flag(arguments.get("log_only", 0), "log_only")
    if not sources or sources & ~MW_MASK_ALL:
        raise MonitorError("sources must be a non-zero mask within 0x7fffffff")
    if change_only and access != 2:
        raise MonitorError("change_only requires access 2 (write)")
    end = address + size
    if ranged:
        if size < 1 or size > 0x10000:
            raise MonitorError("range size must be 1..0x10000")
        if access < 1 or access > 7:
            raise MonitorError("range access must be 1..7")
        fetch = bool(access & 4)
        if fetch != bool(sources & MW_MASK_CPU_I):
            raise MonitorError(
                "range fetch (access bit 2) must match CPU instruction source"
            )
        if access & 3 and not (sources & ~MW_MASK_CPU_I):
            raise MonitorError(
                "a data range needs a source other than CPU instruction fetch"
            )
        if value_enabled and size not in (1, 2, 4):
            raise MonitorError("a valued range must be 1, 2, or 4 bytes")
    else:
        if size not in (1, 2, 4):
            raise MonitorError("watch size must be 1, 2, or 4")
        if address & (size - 1):
            raise MonitorError("watch address must be aligned to its size")
        if access < 1 or access > 3:
            raise MonitorError("watch access must be 1 (read), 2 (write), or 3 (both)")
        if sources & MW_MASK_CPU_I:
            raise MonitorError(
                "an exact watch cannot include the CPU instruction source"
            )
    if end > WATCH_LIMIT:
        raise MonitorError("watch must end at or below 0x7fff0000")
    words = (
        address,
        size,
        access,
        sources,
        value_enabled,
        value,
        value_mask,
        change_only,
        log_only,
    )
    return " ".join(_hex(word) for word in words)


def _condition_register(value: Any) -> int:
    if isinstance(value, str):
        key = value.lower()
        if key not in _CONDITION_REGISTERS:
            raise MonitorError(
                "register must be D0-D7, A0-A7, USP, MSP, ISP, VBR, or SR"
            )
        return _CONDITION_REGISTERS[key]
    number = _u32(value, "register")
    if number > 21 or number == 16:
        raise MonitorError(
            "PC is not a condition register; use D0-A7, USP, MSP, ISP, VBR, or SR"
        )
    return number


def _condition_operator(value: Any) -> int:
    if isinstance(value, str):
        if value not in _CONDITION_OPERATORS:
            raise MonitorError("operator must be ==, !=, <, >, <=, or >=")
        return _CONDITION_OPERATORS[value]
    number = _u32(value, "operator")
    if number > 5:
        raise MonitorError("operator must be 0..5 (==, !=, <=, >=, <, >)")
    return number


def _input_event(arguments: dict[str, Any]) -> str:
    kind = _u32(arguments.get("kind"), "kind")
    port = _u32(arguments.get("port", 0), "port")
    code = _u32(arguments.get("code"), "code")
    raw_value = arguments.get("value")
    if isinstance(raw_value, bool) or not isinstance(raw_value, int):
        raise MonitorError("value must be an integer")
    if kind == 0:
        if port != 0 or code >= 0x68 or raw_value not in (0, 1):
            raise MonitorError(
                "keyboard event needs port 0, code 0..0x67, and value 0 or 1"
            )
        value = raw_value
    elif kind == 1:
        if port > 1 or code > 1 or raw_value < -127 or raw_value > 127:
            raise MonitorError(
                "mouse event needs port 0..1, axis 0..1, and value -127..127"
            )
        value = raw_value & 0xFFFFFFFF
    elif kind == 2:
        if port > 1 or code > 6 or raw_value not in (0, 1):
            raise MonitorError(
                "joystick event needs port 0..1, control 0..6, and value 0 or 1"
            )
        value = raw_value
    else:
        raise MonitorError("kind must be 0 (keyboard), 1 (mouse), or 2 (joystick)")
    return " ".join(_hex(word) for word in (kind, port, code, value))


def _process_name(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise MonitorError("name is required")
    if len(value) > 255:
        raise MonitorError("name must be 1..255 characters")
    if any(ord(char) < 32 or ord(char) >= 127 for char in value):
        raise MonitorError("name must be printable ASCII")
    return value


def build_tool_command(tool: str, arguments: dict[str, Any] | None) -> str:
    """Build the monitor command for one typed tool call."""
    args = arguments or {}
    action = args.get("action")

    if tool == "runtime_execution_status":
        return "execution-status"
    if tool == "runtime_tasks":
        return "tasks"
    if tool == "runtime_segments":
        if args.get("process") is None:
            return "segments"
        address = _structure_address(args.get("process"), "process")
        return f"segments {_hex(address)}"
    if tool == "runtime_memory":
        if action == "map":
            return "memory-map"
        if action == "check":
            address = _u32(args.get("address"), "address")
            length = _u32(args.get("length"), "length")
            if length < 1 or length > MEMORY_CHECK_MAX:
                raise MonitorError("length must be 1..8MiB")
            if address + length > 0x100000000:
                raise MonitorError("address + length must fit in 32 bits")
            return f"memory-check {_hex(address)} {_hex(length)}"
        raise MonitorError("memory action must be map or check")
    if tool == "runtime_exception":
        if action in (None, "status"):
            return "exception"
        if action == "mask":
            mask = args.get("mask")
            if isinstance(mask, str):
                text = mask.strip().removeprefix("0x").removeprefix("0X")
            elif isinstance(mask, int) and not isinstance(mask, bool):
                if mask < 0 or mask > 0xFFFFFFFFFFFFFFFF:
                    raise MonitorError("mask must fit in 64 bits")
                text = f"{mask:x}"
            else:
                raise MonitorError("mask must be an integer or hex string")
            if (
                not text
                or len(text) > 16
                or any(char not in "0123456789abcdefABCDEF" for char in text)
            ):
                raise MonitorError("mask must be 1..16 hex digits")
            if int(text, 16) & 3:
                raise MonitorError("exception mask cannot include vectors 0 or 1")
            return f"exception-mask {text}"
        raise MonitorError("exception action must be status or mask")
    if tool == "runtime_watch":
        if action == "add":
            return "watch add " + _watch_words(args, ranged=False)
        if action == "range":
            return "watch range " + _watch_words(args, ranged=True)
        if action == "list":
            return "watch list"
        if action == "remove":
            return f"watch remove {_hex(_slot(args.get('id'), WATCH_SLOTS, 'id'))}"
        if action == "events":
            return "watch events"
        if action == "last":
            return "watch last"
        if action == "clear-events":
            return "watch clear-events"
        raise MonitorError(
            "watch action must be add, range, list, remove, events, last, or clear-events"
        )
    if tool == "runtime_dma_watch":
        if action == "add":
            address = _u32(args.get("address"), "address")
            length = _u32(args.get("length"), "length")
            mode = _u32(args.get("mode"), "mode")
            mask = _u32(args.get("mask"), "mask")
            if length < 1 or length > 0x10000:
                raise MonitorError("DMA watch length must be 1..0x10000")
            if address + length > WATCH_LIMIT:
                raise MonitorError("DMA watch must end at or below 0x7fff0000")
            if mode < 1 or mode > 3:
                raise MonitorError("DMA mode must be 1 (read), 2 (write), or 3 (both)")
            if not mask or mask & ~((MW_MASK_ALL) & ~7):
                raise MonitorError("DMA mask must be a non-zero non-CPU source mask")
            return f"dma-watch add {_hex(address)} {_hex(length)} {_hex(mode)} {_hex(mask)}"
        if action == "list":
            return "dma-watch list"
        if action == "remove":
            return f"dma-watch remove {_hex(_slot(args.get('id'), WATCH_SLOTS, 'id'))}"
        raise MonitorError("dma-watch action must be add, list, or remove")
    if tool == "runtime_condition":
        if action == "add":
            register = _condition_register(args.get("register"))
            operator = _condition_operator(args.get("operator"))
            value = _u32(args.get("value"), "value")
            mask = _u32(args.get("mask", 0xFFFFFFFF), "mask")
            signed = _flag(args.get("signed", 0), "signed")
            words = " ".join(
                _hex(word) for word in (register, operator, value, mask, signed)
            )
            return f"condition add {words}"
        if action == "list":
            return "condition list"
        if action == "remove":
            return f"condition remove {_hex(_slot(args.get('id'), BREAKPOINT_SLOTS, 'id'))}"
        raise MonitorError("condition action must be add, list, or remove")
    if tool == "runtime_history":
        if action == "on":
            return "history on"
        if action == "off":
            return "history off"
        if action == "read":
            count = _u32(args.get("count", 16), "count")
            if count < 1 or count > HISTORY_MAX:
                raise MonitorError("history count must be 1..128")
            return f"history read {_hex(count)}"
        raise MonitorError("history action must be on, off, or read")
    if tool == "runtime_guest_output":
        if action not in ("on", "off", "read", "clear"):
            raise MonitorError("guest-output action must be on, off, read, or clear")
        return f"guest-output {action}"
    if tool == "runtime_process_break":
        if action == "name":
            return "process-break name " + _process_name(args.get("name"))
        if action == "address":
            address = _structure_address(args.get("address"), "address")
            return f"process-break address {_hex(address)}"
        if action == "status":
            return "process-break status"
        if action == "clear":
            return "process-break clear"
        raise MonitorError(
            "process-break action must be name, address, status, or clear"
        )
    if tool == "runtime_input":
        if action == "event":
            return "input event " + _input_event(args)
        if action == "release":
            return "input release"
        if action == "status":
            return "input status"
        raise MonitorError("input action must be event, release, or status")
    raise MonitorError(f"unknown monitor tool: {tool}")


def _hex_addr(value: Any) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        return f"${value:08X}"
    return str(value)


def _note_truncated(payload: Any, lines: list[str]) -> list[str]:
    if reply_truncated(payload):
        lines.append("truncated: true")
    return lines


def format_reply(command: str, lines: list[str]) -> str:
    """Turn raw monitor lines into a short readable result."""
    if not lines:
        return "OK"
    if command.startswith("dma-watch add ") and len(lines) == 1 and lines[0].isdigit():
        return f"DMA watch id {lines[0]}"
    if len(lines) != 1 or lines[0][:1] not in "[{n":
        return "\n".join(lines)
    try:
        payload = json.loads(lines[0])
    except json.JSONDecodeError:
        return "\n".join(lines)
    rendered = _format_payload(command, payload)
    return "\n".join(_note_truncated(payload, rendered))


def _format_payload(command: str, payload: Any) -> list[str]:
    if payload is None:
        return ["none"]
    if command == "tasks" and isinstance(payload, dict):
        tasks = payload.get("tasks") or []
        rows = [f"Tasks: {len(tasks)}"]
        for task in tasks:
            if not isinstance(task, dict):
                continue
            rows.append(
                f"- {task.get('name', '?')} {task.get('list', '')} "
                f"{_hex_addr(task.get('address'))}"
            )
        return rows
    if command.startswith("segments") and isinstance(payload, dict):
        rows = [
            f"Process {_hex_addr(payload.get('process'))} {payload.get('name', '')}".rstrip()
        ]
        for segment in payload.get("segments") or []:
            if isinstance(segment, dict):
                rows.append(
                    f"- {segment.get('index')}: {_hex_addr(segment.get('address'))} "
                    f"size {segment.get('size')}"
                )
        return rows
    if command == "memory-map" and isinstance(payload, dict):
        rows = [f"Memory map ({payload.get('addressing', 'physical')})"]
        region_list = payload.get("regions")
        if not isinstance(region_list, list):
            region_list = []
        for region in region_list:
            if not isinstance(region, dict):
                continue
            chip = " chip" if region.get("chip") else ""
            rows.append(
                f"- {_hex_addr(region.get('address'))} size {region.get('size')} "
                f"{region.get('type', '')}{chip} {region.get('name', '')}".rstrip()
            )
        return rows
    if command.startswith("memory-check") and isinstance(payload, dict):
        return [
            f"writable {payload.get('memory')} RAM"
            if payload.get("writable_ram")
            else "not writable RAM"
        ]
    if command == "exception" and isinstance(payload, dict):
        last = payload.get("last")
        rows = [f"mask {payload.get('mask')}"]
        if not isinstance(last, dict):
            rows.append("last: none")
        else:
            rows.append(
                f"last vector {last.get('vector')} pc {_hex_addr(last.get('instruction_pc'))}"
            )
        return rows
    if command in ("watch list", "dma-watch list", "condition list") and isinstance(
        payload, list
    ):
        if not payload:
            return ["none"]
        return [json.dumps(item, separators=(",", ":")) for item in payload]
    if command == "watch events" and isinstance(payload, dict):
        events = payload.get("events") or []
        rows = [f"events {len(events)}, dropped {payload.get('dropped', 0)}"]
        rows.extend(json.dumps(item, separators=(",", ":")) for item in events[:8])
        return rows
    if isinstance(payload, dict) and "id" in payload and len(payload) == 1:
        return [f"id {payload['id']}"]
    if isinstance(payload, dict):
        return [json.dumps(payload, separators=(",", ":"))]
    return [json.dumps(payload)]
