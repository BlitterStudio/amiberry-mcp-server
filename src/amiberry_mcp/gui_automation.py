"""Screenshot-driven GUI automation domain service.

This module owns the public action contract, screenshot-pixel translation, and
the complete readiness/input/cleanup transaction. Transport adapters only
validate their envelope and serialize these immutable results.
"""

import asyncio
import hashlib
import json
import logging
import re
import secrets
import struct
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any

from .config import SCREENSHOT_DIR
from .ipc_client import (
    ActionableCaptureGeometry,
    AmiberryIPCClient,
    AutomationState,
    GuardedInputReason,
    IPCError,
    MouseUntrapMode,
    TabletMode,
)
from .shared_state import (
    AutomationBusyError,
    CaptureRecord,
    DirtyOwnership,
    ProcessState,
    get_state,
    pin_active_endpoint,
)

RESULT_SCHEMA_VERSION = 1
CAPTURE_SCHEMA_VERSION = 1
COORDINATE_SPACE = "screenshot_pixels"
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
MAX_CAPTURE_ID_LENGTH = 256
MAX_DWELL_MS = 5000
ACTION_LOCK_TIMEOUT = 0.25
READINESS_TIMEOUT = 1.0
READINESS_POLL_INTERVAL = 0.05
INPUT_TRANSITION_INTERVAL = 0.05
CLEANUP_TIMEOUT = 1.5

logger = logging.getLogger(__name__)


class GuiAction(str, Enum):
    """Canonical public GUI actions."""

    MOVE = "move"
    CLICK = "click"
    DRAG = "drag"


class MouseButton(str, Enum):
    """Named public buttons; raw masks remain private."""

    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"

    @property
    def _mask(self) -> int:
        """Return the private complete-mask bit for this public button."""
        return {self.LEFT: 1, self.RIGHT: 2, self.MIDDLE: 4}[self]


class StableCode(str, Enum):
    """Closed action result codes shared by MCP and HTTP."""

    OK = "ok"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    CAPTURE_NOT_ACTIONABLE = "capture_not_actionable"
    CAPTURE_UNKNOWN = "capture_unknown"
    CAPTURE_EVICTED = "capture_evicted"
    CAPTURE_STALE = "capture_stale"
    CAPTURE_WRONG_CONTROLLER = "capture_wrong_controller"
    CAPTURE_WRONG_INSTANCE = "capture_wrong_instance"
    COORDINATE_OUT_OF_BOUNDS = "coordinate_out_of_bounds"
    AUTOMATION_BUSY = "automation_busy"
    ACTION_IN_PROGRESS = "action_in_progress"
    REQUEST_ID_CONFLICT = "request_id_conflict"
    INPUT_NOT_READY = "input_not_ready"
    RUNTIME_UNREACHABLE = "runtime_unreachable"
    ACTION_REJECTED = "action_rejected"
    CLEANUP_UNCONFIRMED = "cleanup_unconfirmed"


class ExecutionState(str, Enum):
    """Whether input definitely did not start, completed, or is ambiguous."""

    NOT_STARTED = "not_started"
    COMPLETED = "completed"
    OUTCOME_UNKNOWN = "outcome_unknown"


class FailurePhase(str, Enum):
    """Stable transaction phase associated with a failure."""

    VALIDATION = "validation"
    ROUTING = "routing"
    READINESS = "readiness"
    MOVE = "move"
    PRESS = "press"
    INTER_CLICK = "inter_click"
    DRAG = "drag"
    RELEASE = "release"
    RESTORE = "restore"
    TRANSPORT = "transport"


class NextAction(str, Enum):
    """Closed caller guidance values."""

    NONE = "none"
    UPGRADE_RUNTIME = "upgrade_runtime"
    RECAPTURE = "recapture"
    RECAPTURE_HERE = "recapture_here"
    SELECT_INSTANCE = "select_instance"
    CORRECT_REQUEST = "correct_request"
    WAIT_THEN_RETRY = "wait_then_retry"
    NEW_REQUEST_ID = "new_request_id"
    RESTORE_FOCUS = "restore_focus"
    RETRY = "retry"
    RECONCILE = "reconcile"


class CleanupState(str, Enum):
    """Whether cleanup was unnecessary, confirmed, or uncertain."""

    NOT_REQUIRED = "not_required"
    CONFIRMED = "confirmed"
    UNCONFIRMED = "unconfirmed"


@dataclass(frozen=True)
class Point:
    """One integer point in a declared coordinate space."""

    x: int
    y: int

    def to_dict(self) -> dict[str, int]:
        """Return a JSON-ready point."""
        return {"x": self.x, "y": self.y}


@dataclass(frozen=True)
class ActionableBounds:
    """Half-open actionable rectangle in screenshot pixels."""

    x: int
    y: int
    width: int
    height: int

    def to_dict(self) -> dict[str, int]:
        """Return a JSON-ready bounds record."""
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class MoveRequest:
    """Public move/hover request."""

    capture_id: str
    request_id: str
    x: int
    y: int
    dwell_ms: int = 0


@dataclass(frozen=True)
class ClickRequest:
    """Public single- or double-click request."""

    capture_id: str
    request_id: str
    x: int
    y: int
    button: MouseButton = MouseButton.LEFT
    click_count: int = 1


@dataclass(frozen=True)
class DragRequest:
    """Public drag request."""

    capture_id: str
    request_id: str
    start_x: int
    start_y: int
    end_x: int
    end_y: int
    button: MouseButton = MouseButton.LEFT


@dataclass(frozen=True)
class CaptureView:
    """Exact immutable image bytes paired with their capture metadata."""

    schema_version: int
    path: Path
    image_bytes: bytes
    mime_type: str
    controller_id: str
    capture_id: str | None
    actionable: bool
    coordinate_space: str
    image_width: int
    image_height: int
    actionable_bounds: ActionableBounds | None
    next_action: NextAction

    def metadata_dict(self) -> dict[str, Any]:
        """Return public metadata without copying screenshot bytes into JSON."""
        return {
            "schema_version": self.schema_version,
            "path": str(self.path),
            "controller_id": self.controller_id,
            "capture_id": self.capture_id,
            "actionable": self.actionable,
            "coordinate_space": self.coordinate_space,
            "image_width": self.image_width,
            "image_height": self.image_height,
            "actionable_bounds": (
                self.actionable_bounds.to_dict() if self.actionable_bounds else None
            ),
            "next_action": self.next_action.value,
        }


@dataclass(frozen=True)
class AutomationResult:
    """Canonical versioned action result returned by every adapter."""

    schema_version: int
    ok: bool
    code: StableCode
    message: str
    controller_id: str
    request_id: str
    action: GuiAction
    execution_state: ExecutionState
    failure_phase: FailurePhase | None
    next_action: NextAction
    retryable: bool
    recapture_required: bool
    cleanup_state: CleanupState
    cleanup_context: dict[str, Any]
    deduplicated: bool
    capture_id: str | None = None
    requested_coordinates: tuple[Point, ...] = ()
    applied_coordinates: tuple[Point, ...] = ()
    runtime_id: str | None = None
    geometry_revision: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the pure-JSON representation used by MCP and HTTP."""
        return {
            "schema_version": self.schema_version,
            "ok": self.ok,
            "code": self.code.value,
            "message": self.message,
            "controller_id": self.controller_id,
            "request_id": self.request_id,
            "action": self.action.value,
            "execution_state": self.execution_state.value,
            "failure_phase": self.failure_phase.value if self.failure_phase else None,
            "next_action": self.next_action.value,
            "retryable": self.retryable,
            "recapture_required": self.recapture_required,
            "cleanup_state": self.cleanup_state.value,
            "cleanup_context": dict(self.cleanup_context),
            "deduplicated": self.deduplicated,
            "capture_id": self.capture_id,
            "requested_coordinates": [
                point.to_dict() for point in self.requested_coordinates
            ],
            "applied_coordinates": [
                point.to_dict() for point in self.applied_coordinates
            ],
            "runtime_id": self.runtime_id,
            "geometry_revision": self.geometry_revision,
        }


@dataclass(frozen=True)
class _CodeDefaults:
    execution: ExecutionState
    next_action: NextAction
    retryable: bool = False
    recapture: bool = False


_CODE_DEFAULTS = {
    StableCode.OK: _CodeDefaults(ExecutionState.COMPLETED, NextAction.NONE),
    StableCode.UNSUPPORTED_CAPABILITY: _CodeDefaults(
        ExecutionState.NOT_STARTED, NextAction.UPGRADE_RUNTIME
    ),
    StableCode.CAPTURE_NOT_ACTIONABLE: _CodeDefaults(
        ExecutionState.NOT_STARTED, NextAction.UPGRADE_RUNTIME
    ),
    StableCode.CAPTURE_UNKNOWN: _CodeDefaults(
        ExecutionState.NOT_STARTED, NextAction.RECAPTURE, recapture=True
    ),
    StableCode.CAPTURE_EVICTED: _CodeDefaults(
        ExecutionState.NOT_STARTED, NextAction.RECAPTURE, recapture=True
    ),
    StableCode.CAPTURE_STALE: _CodeDefaults(
        ExecutionState.NOT_STARTED, NextAction.RECAPTURE, recapture=True
    ),
    StableCode.CAPTURE_WRONG_CONTROLLER: _CodeDefaults(
        ExecutionState.NOT_STARTED, NextAction.RECAPTURE_HERE, recapture=True
    ),
    StableCode.CAPTURE_WRONG_INSTANCE: _CodeDefaults(
        ExecutionState.NOT_STARTED, NextAction.SELECT_INSTANCE
    ),
    StableCode.COORDINATE_OUT_OF_BOUNDS: _CodeDefaults(
        ExecutionState.NOT_STARTED, NextAction.CORRECT_REQUEST
    ),
    StableCode.AUTOMATION_BUSY: _CodeDefaults(
        ExecutionState.NOT_STARTED, NextAction.WAIT_THEN_RETRY, retryable=True
    ),
    StableCode.ACTION_IN_PROGRESS: _CodeDefaults(
        ExecutionState.NOT_STARTED, NextAction.WAIT_THEN_RETRY, retryable=True
    ),
    StableCode.REQUEST_ID_CONFLICT: _CodeDefaults(
        ExecutionState.NOT_STARTED, NextAction.NEW_REQUEST_ID
    ),
    StableCode.INPUT_NOT_READY: _CodeDefaults(
        ExecutionState.NOT_STARTED, NextAction.RESTORE_FOCUS, retryable=True
    ),
    StableCode.RUNTIME_UNREACHABLE: _CodeDefaults(
        ExecutionState.NOT_STARTED, NextAction.RETRY, retryable=True
    ),
    StableCode.ACTION_REJECTED: _CodeDefaults(
        ExecutionState.NOT_STARTED, NextAction.CORRECT_REQUEST
    ),
    StableCode.CLEANUP_UNCONFIRMED: _CodeDefaults(
        ExecutionState.OUTCOME_UNKNOWN, NextAction.RECONCILE
    ),
}


class CaptureError(RuntimeError):
    """Actionable screenshot transaction failed with a stable code."""

    def __init__(self, code: StableCode, message: str) -> None:
        """Initialize an error with its public stable code."""
        super().__init__(message)
        self.code = code


class CoordinateError(ValueError):
    """A screenshot pixel lies outside the actionable half-open rectangle."""


class _ActionAbort(RuntimeError):
    """Internal structured transaction stop."""

    def __init__(
        self,
        code: StableCode,
        phase: FailurePhase,
        message: str,
        *,
        execution_state: ExecutionState | None = None,
        next_action: NextAction | None = None,
    ) -> None:
        """Initialize an internal abort with its result classification."""
        super().__init__(message)
        self.code = code
        self.phase = phase
        self.execution_state = execution_state
        self.next_action = next_action


@dataclass(frozen=True)
class _CleanupOutcome:
    state: CleanupState
    context: dict[str, Any]
    cancelled: bool = False


@dataclass
class _ReadinessContext:
    observed: AutomationState | None = None
    owner: DirtyOwnership | None = None
    cleanup_needed: bool = False
    mutation_possible: bool = False


def translate_screenshot_point(
    geometry: ActionableCaptureGeometry, x: int, y: int
) -> Point:
    """Map one screenshot pixel center into SDL logical window coordinates."""
    if (
        isinstance(x, bool)
        or isinstance(y, bool)
        or not isinstance(x, int)
        or not isinstance(y, int)
    ):
        raise CoordinateError("Coordinates must be integers")
    if not (0 <= x < geometry.image_width and 0 <= y < geometry.image_height):
        raise CoordinateError("Coordinate is outside the screenshot image")
    source_right = geometry.source_x + geometry.source_width
    source_bottom = geometry.source_y + geometry.source_height
    if not (
        geometry.source_x <= x < source_right and geometry.source_y <= y < source_bottom
    ):
        raise CoordinateError("Coordinate is outside the actionable source rectangle")

    relative_x = x - geometry.source_x
    relative_y = y - geometry.source_y
    mapped_x = geometry.viewport_x + min(
        geometry.viewport_width - 1,
        ((2 * relative_x + 1) * geometry.viewport_width) // (2 * geometry.source_width),
    )
    mapped_y = geometry.viewport_y + min(
        geometry.viewport_height - 1,
        ((2 * relative_y + 1) * geometry.viewport_height)
        // (2 * geometry.source_height),
    )
    return Point(mapped_x, mapped_y)


def _decode_image_dimensions(data: bytes) -> tuple[str, int, int]:
    """Decode bounded dimensions from a supported screenshot image header."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(data) < 24 or data[12:16] != b"IHDR":
            raise CaptureError(StableCode.CAPTURE_NOT_ACTIONABLE, "Invalid PNG IHDR")
        width, height = struct.unpack(">II", data[16:24])
        if width <= 0 or height <= 0:
            raise CaptureError(
                StableCode.CAPTURE_NOT_ACTIONABLE, "PNG dimensions are invalid"
            )
        return "image/png", width, height

    if data.startswith(b"\xff\xd8"):
        offset = 2
        while offset + 4 <= len(data):
            if data[offset] != 0xFF:
                offset += 1
                continue
            marker = data[offset + 1]
            offset += 2
            if marker in {0xD8, 0xD9}:
                continue
            if offset + 2 > len(data):
                break
            length = int.from_bytes(data[offset : offset + 2], "big")
            if length < 2 or offset + length > len(data):
                break
            if marker in {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            }:
                if length < 7:
                    break
                height = int.from_bytes(data[offset + 3 : offset + 5], "big")
                width = int.from_bytes(data[offset + 5 : offset + 7], "big")
                if width > 0 and height > 0:
                    return "image/jpeg", width, height
                break
            offset += length

    if data[:6] in {b"GIF87a", b"GIF89a"}:
        if len(data) < 13:
            raise CaptureError(
                StableCode.CAPTURE_NOT_ACTIONABLE,
                "GIF logical screen descriptor is truncated",
            )
        width, height = struct.unpack("<HH", data[6:10])
        if width <= 0 or height <= 0:
            raise CaptureError(
                StableCode.CAPTURE_NOT_ACTIONABLE, "GIF dimensions are invalid"
            )
        return "image/gif", width, height

    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        if len(data) < 20:
            raise CaptureError(
                StableCode.CAPTURE_NOT_ACTIONABLE, "WebP chunk header is truncated"
            )
        riff_size = int.from_bytes(data[4:8], "little")
        riff_end = riff_size + 8
        chunk_size = int.from_bytes(data[16:20], "little")
        chunk_end = 20 + chunk_size
        if riff_size < 12 or riff_end > len(data) or chunk_end > riff_end:
            raise CaptureError(
                StableCode.CAPTURE_NOT_ACTIONABLE,
                "WebP RIFF or image chunk is truncated",
            )

        chunk_type = data[12:16]
        payload = data[20:chunk_end]
        if chunk_type == b"VP8 ":
            if len(payload) < 10 or payload[0] & 1 or payload[3:6] != b"\x9d\x01\x2a":
                raise CaptureError(
                    StableCode.CAPTURE_NOT_ACTIONABLE,
                    "WebP VP8 frame header is invalid",
                )
            width = int.from_bytes(payload[6:8], "little") & 0x3FFF
            height = int.from_bytes(payload[8:10], "little") & 0x3FFF
        elif chunk_type == b"VP8L":
            if len(payload) < 5 or payload[0] != 0x2F:
                raise CaptureError(
                    StableCode.CAPTURE_NOT_ACTIONABLE,
                    "WebP VP8L frame header is invalid",
                )
            dimensions = int.from_bytes(payload[1:5], "little")
            if dimensions >> 29:
                raise CaptureError(
                    StableCode.CAPTURE_NOT_ACTIONABLE,
                    "WebP VP8L version is unsupported",
                )
            width = (dimensions & 0x3FFF) + 1
            height = ((dimensions >> 14) & 0x3FFF) + 1
        elif chunk_type == b"VP8X":
            if len(payload) != 10 or payload[1:4] != b"\x00\x00\x00":
                raise CaptureError(
                    StableCode.CAPTURE_NOT_ACTIONABLE,
                    "WebP VP8X canvas header is invalid",
                )
            width = int.from_bytes(payload[4:7], "little") + 1
            height = int.from_bytes(payload[7:10], "little") + 1
        else:
            raise CaptureError(
                StableCode.CAPTURE_NOT_ACTIONABLE,
                "WebP image chunk type is unsupported",
            )
        if width <= 0 or height <= 0:
            raise CaptureError(
                StableCode.CAPTURE_NOT_ACTIONABLE, "WebP dimensions are invalid"
            )
        return "image/webp", width, height

    raise CaptureError(
        StableCode.CAPTURE_NOT_ACTIONABLE, "Screenshot image format is invalid"
    )


def _desired_tablet_mode(current: TabletMode) -> TabletMode:
    """Preserve REAL and minimally upgrade OFF for absolute input."""
    return TabletMode.MOUSEHACK if current is TabletMode.OFF else current


def _desired_mouse_untrap(current: MouseUntrapMode) -> MouseUntrapMode:
    """Add the MAGIC capability while preserving the MIDDLE bit."""
    return {
        MouseUntrapMode.OFF: MouseUntrapMode.MAGIC,
        MouseUntrapMode.MIDDLE: MouseUntrapMode.BOTH,
        MouseUntrapMode.MAGIC: MouseUntrapMode.MAGIC,
        MouseUntrapMode.BOTH: MouseUntrapMode.BOTH,
    }[current]


def _payload_hash(payload: dict[str, Any]) -> str:
    """Return the canonical retained-request payload digest."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class GuiAutomationService:
    """Own screenshot capture and serialized GUI action transactions."""

    def __init__(self, state: ProcessState | None = None) -> None:
        """Initialize the service with one controller process state."""
        self._state = state or get_state()
        self._cleanup_tasks: set[asyncio.Task[_CleanupOutcome]] = set()

    async def capture(self, filename: str | None = None) -> CaptureView:
        """Capture, read once, revalidate, and optionally register an image."""
        if filename is None:
            entropy = secrets.token_urlsafe(18)
            path = SCREENSHOT_DIR / f"gui-{self._state.controller_id}-{entropy}.png"
            try:
                await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
            except OSError as e:
                raise CaptureError(
                    StableCode.CAPTURE_NOT_ACTIONABLE,
                    f"Screenshot directory could not be created: {e}",
                ) from e
        else:
            path = Path(filename)
        try:
            async with pin_active_endpoint(
                self._state, timeout=ACTION_LOCK_TIMEOUT
            ) as pinned:
                upstream = await pinned.client.capture_actionable_screenshot(str(path))
                try:
                    image_bytes = await asyncio.to_thread(path.read_bytes)
                except OSError as e:
                    raise CaptureError(
                        StableCode.CAPTURE_NOT_ACTIONABLE,
                        f"Captured screenshot could not be read: {e}",
                    ) from e
                mime_type, width, height = _decode_image_dimensions(image_bytes)

                geometry = upstream.geometry
                if geometry is None:
                    return CaptureView(
                        schema_version=CAPTURE_SCHEMA_VERSION,
                        path=path,
                        image_bytes=image_bytes,
                        mime_type=mime_type,
                        controller_id=self._state.controller_id,
                        capture_id=None,
                        actionable=False,
                        coordinate_space=COORDINATE_SPACE,
                        image_width=width,
                        image_height=height,
                        actionable_bounds=None,
                        next_action=NextAction.UPGRADE_RUNTIME,
                    )
                if (width, height) != (geometry.image_width, geometry.image_height):
                    raise CaptureError(
                        StableCode.CAPTURE_STALE,
                        "Decoded image dimensions differ from capture metadata",
                    )
                observed = await pinned.client._get_gui_automation_state()
                if (
                    not observed.geometry_valid
                    or observed.runtime_id != geometry.runtime_id
                    or observed.geometry_revision != geometry.geometry_revision
                    or observed.monitor_id != geometry.monitor_id
                    or self._state.active_endpoint != pinned.endpoint
                ):
                    raise CaptureError(
                        StableCode.CAPTURE_STALE,
                        "Runtime geometry changed while decoding the screenshot",
                    )
                capture_id = self._state.new_capture_id()
                self._state.register_capture(
                    CaptureRecord(
                        capture_id=capture_id,
                        controller_id=self._state.controller_id,
                        endpoint=pinned.endpoint,
                        path=path,
                        geometry=geometry,
                    )
                )
        except AutomationBusyError as e:
            raise CaptureError(StableCode.AUTOMATION_BUSY, str(e)) from e
        except IPCError as e:
            raise CaptureError(StableCode.RUNTIME_UNREACHABLE, str(e)) from e

        return CaptureView(
            schema_version=CAPTURE_SCHEMA_VERSION,
            path=path,
            image_bytes=image_bytes,
            mime_type=mime_type,
            controller_id=self._state.controller_id,
            capture_id=capture_id,
            actionable=True,
            coordinate_space=COORDINATE_SPACE,
            image_width=width,
            image_height=height,
            actionable_bounds=ActionableBounds(
                geometry.source_x,
                geometry.source_y,
                geometry.source_width,
                geometry.source_height,
            ),
            next_action=NextAction.NONE,
        )

    async def move(self, request: MoveRequest) -> AutomationResult:
        """Move or hover at one screenshot pixel."""
        return await self._execute(GuiAction.MOVE, request)

    async def click(self, request: ClickRequest) -> AutomationResult:
        """Perform one named single- or double-click."""
        return await self._execute(GuiAction.CLICK, request)

    async def drag(self, request: DragRequest) -> AutomationResult:
        """Drag one named button between two screenshot pixels."""
        return await self._execute(GuiAction.DRAG, request)

    def validation_error(
        self,
        action: GuiAction,
        request_id: str,
        capture_id: str | None,
        message: str,
    ) -> AutomationResult:
        """Return the canonical result for an adapter envelope rejection."""
        return self._make_result(
            StableCode.ACTION_REJECTED,
            request_id,
            action,
            message,
            capture_id=capture_id,
            phase=FailurePhase.VALIDATION,
        )

    def _make_result(
        self,
        code: StableCode,
        request_id: str,
        action: GuiAction,
        message: str,
        *,
        capture_id: str | None = None,
        requested: tuple[Point, ...] = (),
        applied: tuple[Point, ...] = (),
        runtime_id: str | None = None,
        geometry_revision: int | None = None,
        phase: FailurePhase | None = None,
        execution: ExecutionState | None = None,
        next_action: NextAction | None = None,
        cleanup: CleanupState = CleanupState.NOT_REQUIRED,
        cleanup_context: dict[str, Any] | None = None,
        deduplicated: bool = False,
    ) -> AutomationResult:
        """Construct one result from the closed stable-code table."""
        defaults = _CODE_DEFAULTS[code]
        resolved_execution = execution or defaults.execution
        resolved_next = next_action or defaults.next_action
        retryable = defaults.retryable
        if resolved_execution is ExecutionState.OUTCOME_UNKNOWN:
            retryable = False
            if next_action is None:
                resolved_next = NextAction.RECONCILE
        return AutomationResult(
            schema_version=RESULT_SCHEMA_VERSION,
            ok=code is StableCode.OK,
            code=code,
            message=message,
            controller_id=self._state.controller_id,
            request_id=request_id,
            action=action,
            execution_state=resolved_execution,
            failure_phase=phase,
            next_action=resolved_next,
            retryable=retryable,
            recapture_required=defaults.recapture,
            cleanup_state=cleanup,
            cleanup_context=cleanup_context or {},
            deduplicated=deduplicated,
            capture_id=capture_id,
            requested_coordinates=requested,
            applied_coordinates=applied,
            runtime_id=runtime_id,
            geometry_revision=geometry_revision,
        )

    def _validate_request(
        self, action: GuiAction, request: MoveRequest | ClickRequest | DragRequest
    ) -> tuple[dict[str, Any], tuple[Point, ...]]:
        """Validate canonical request fields before ledger reservation."""
        if not REQUEST_ID_PATTERN.fullmatch(request.request_id):
            raise ValueError(
                "request_id must be 1-128 characters from A-Z, a-z, 0-9, . _ : -"
            )
        if (
            not isinstance(request.capture_id, str)
            or not request.capture_id
            or len(request.capture_id) > MAX_CAPTURE_ID_LENGTH
        ):
            raise ValueError("capture_id is empty or too long")

        if action is GuiAction.MOVE and isinstance(request, MoveRequest):
            if (
                isinstance(request.dwell_ms, bool)
                or not isinstance(request.dwell_ms, int)
                or not 0 <= request.dwell_ms <= MAX_DWELL_MS
            ):
                raise ValueError("dwell_ms must be an integer from 0 to 5000")
            points = (Point(request.x, request.y),)
            payload = {
                "action": action.value,
                "capture_id": request.capture_id,
                "x": request.x,
                "y": request.y,
                "dwell_ms": request.dwell_ms,
            }
        elif action is GuiAction.CLICK and isinstance(request, ClickRequest):
            try:
                button = MouseButton(request.button)
            except ValueError as e:
                raise ValueError("button must be left, right, or middle") from e
            if isinstance(request.click_count, bool) or request.click_count not in {
                1,
                2,
            }:
                raise ValueError("click_count must be 1 or 2")
            points = (Point(request.x, request.y),)
            payload = {
                "action": action.value,
                "capture_id": request.capture_id,
                "x": request.x,
                "y": request.y,
                "button": button.value,
                "click_count": request.click_count,
            }
        elif action is GuiAction.DRAG and isinstance(request, DragRequest):
            try:
                button = MouseButton(request.button)
            except ValueError as e:
                raise ValueError("button must be left, right, or middle") from e
            points = (
                Point(request.start_x, request.start_y),
                Point(request.end_x, request.end_y),
            )
            payload = {
                "action": action.value,
                "capture_id": request.capture_id,
                "start_x": request.start_x,
                "start_y": request.start_y,
                "end_x": request.end_x,
                "end_y": request.end_y,
                "button": button.value,
            }
        else:
            raise ValueError("Request model does not match its action")

        for point in points:
            if (
                isinstance(point.x, bool)
                or isinstance(point.y, bool)
                or not isinstance(point.x, int)
                or not isinstance(point.y, int)
            ):
                raise ValueError("Coordinates must be integers")
        return payload, points

    async def _execute(
        self, action: GuiAction, request: MoveRequest | ClickRequest | DragRequest
    ) -> AutomationResult:
        """Reserve, route, transact, clean up, and retain one public action."""
        request_id = request.request_id if isinstance(request.request_id, str) else ""
        try:
            payload, requested = self._validate_request(action, request)
        except ValueError as e:
            return self._make_result(
                StableCode.ACTION_REJECTED,
                request_id,
                action,
                str(e),
                capture_id=getattr(request, "capture_id", None),
                phase=FailurePhase.VALIDATION,
            )

        reservation = self._state.reserve_request(request_id, _payload_hash(payload))
        if reservation.status == "conflict":
            return self._make_result(
                StableCode.REQUEST_ID_CONFLICT,
                request_id,
                action,
                "request_id is already bound to a different payload",
                capture_id=request.capture_id,
                requested=requested,
                phase=FailurePhase.VALIDATION,
            )
        if reservation.status == "busy":
            return self._make_result(
                StableCode.AUTOMATION_BUSY,
                request_id,
                action,
                "The controller request ledger is at its in-flight limit",
                capture_id=request.capture_id,
                requested=requested,
                phase=FailurePhase.ROUTING,
            )
        if reservation.status == "in_progress":
            return self._make_result(
                StableCode.ACTION_IN_PROGRESS,
                request_id,
                action,
                "An identical request is still in progress",
                capture_id=request.capture_id,
                requested=requested,
                phase=FailurePhase.ROUTING,
                deduplicated=True,
            )
        if reservation.status in {"terminal", "outcome_unknown"}:
            retained = reservation.entry.result if reservation.entry else None
            if isinstance(retained, AutomationResult):
                return replace(retained, deduplicated=True)
            return self._make_result(
                StableCode.CLEANUP_UNCONFIRMED,
                request_id,
                action,
                "The retained request outcome is unknown; reconcile before acting",
                capture_id=request.capture_id,
                requested=requested,
                phase=FailurePhase.TRANSPORT,
                deduplicated=True,
            )

        result: AutomationResult | None = None
        cleanup_needed = False
        cleanup_owner: DirtyOwnership | None = None
        readiness = _ReadinessContext()
        applied: list[Point] = []
        input_confirmed = False
        input_ambiguous = False
        cancelled = False
        pinned_endpoint: str | None = None
        client: AmiberryIPCClient | None = None
        record: CaptureRecord | None = None
        observed: AutomationState | None = None
        pin_manager: Any | None = None
        pin_entered = False

        try:
            pin_manager = pin_active_endpoint(self._state, timeout=ACTION_LOCK_TIMEOUT)
            pinned = await pin_manager.__aenter__()
            pin_entered = True
            pinned_endpoint = pinned.endpoint
            client = pinned.client
            if reservation.entry is not None:
                reservation.entry.endpoint = pinned.endpoint
            lookup = self._state.resolve_capture(request.capture_id, pinned.endpoint)
            if lookup.status != "ok" or lookup.record is None:
                code = StableCode(lookup.status)
                raise _ActionAbort(
                    code,
                    FailurePhase.ROUTING,
                    f"Capture lookup failed: {lookup.status}",
                )
            record = lookup.record
            try:
                mapped = tuple(
                    translate_screenshot_point(record.geometry, point.x, point.y)
                    for point in requested
                )
            except CoordinateError as e:
                raise _ActionAbort(
                    StableCode.COORDINATE_OUT_OF_BOUNDS,
                    FailurePhase.VALIDATION,
                    str(e),
                ) from e

            await self._reconcile_dirty(client, pinned.endpoint, record)
            observed = await self._prepare_readiness(
                client, pinned.endpoint, record, readiness
            )
            cleanup_owner = readiness.owner
            cleanup_needed = readiness.cleanup_needed

            async def guarded(point: Point, mask: int, phase: FailurePhase) -> None:
                """Emit one guarded input transition and validate its receipt."""
                nonlocal input_ambiguous, input_confirmed, cleanup_needed
                input_ambiguous = True
                cleanup_needed = True
                response = await client._send_mouse_abs_guarded(
                    point.x,
                    point.y,
                    mask,
                    record.geometry.runtime_id,
                    record.geometry.geometry_revision,
                    record.geometry.monitor_id,
                    observed.input_config_revision,
                )
                input_ambiguous = False
                if not response.applied:
                    stale_reasons = {
                        GuardedInputReason.RUNTIME_MISMATCH,
                        GuardedInputReason.GEOMETRY_REVISION_MISMATCH,
                        GuardedInputReason.MONITOR_MISMATCH,
                        GuardedInputReason.GEOMETRY_INVALID,
                    }
                    readiness_reasons = {
                        GuardedInputReason.INPUT_CONFIG_REVISION_MISMATCH,
                        GuardedInputReason.FOCUS_NOT_READY,
                        GuardedInputReason.SETTINGS_INCOMPATIBLE,
                    }
                    if input_confirmed:
                        code = StableCode.ACTION_REJECTED
                        execution = ExecutionState.OUTCOME_UNKNOWN
                        next_action = NextAction.RECONCILE
                    elif response.reason in stale_reasons:
                        self._state.invalidate_endpoint_captures(pinned.endpoint)
                        code = StableCode.CAPTURE_STALE
                        execution = ExecutionState.NOT_STARTED
                        next_action = None
                    elif response.reason in readiness_reasons:
                        code = StableCode.INPUT_NOT_READY
                        execution = ExecutionState.NOT_STARTED
                        next_action = None
                    elif response.reason is GuardedInputReason.COORDINATE_OUT_OF_BOUNDS:
                        code = StableCode.COORDINATE_OUT_OF_BOUNDS
                        execution = ExecutionState.NOT_STARTED
                        next_action = None
                    else:
                        code = StableCode.ACTION_REJECTED
                        execution = ExecutionState.NOT_STARTED
                        next_action = None
                    raise _ActionAbort(
                        code,
                        phase,
                        f"Guarded input rejected: {response.reason.value}",
                        execution_state=execution,
                        next_action=next_action,
                    )
                input_confirmed = True
                if response.x is None or response.y is None:
                    raise IPCError("Applied guarded input omitted coordinates")
                if (
                    response.runtime_id != record.geometry.runtime_id
                    or response.geometry_revision != record.geometry.geometry_revision
                    or response.monitor_id != record.geometry.monitor_id
                    or response.input_config_revision != observed.input_config_revision
                    or response.button_mask != mask
                    or response.x != point.x
                    or response.y != point.y
                ):
                    raise IPCError("Applied guarded input response contradicts request")
                applied_point = Point(response.x, response.y)
                if not applied or applied[-1] != applied_point:
                    applied.append(applied_point)

            await self._run_sequence(action, request, mapped, guarded)
            result = self._make_result(
                StableCode.OK,
                request_id,
                action,
                "GUI action completed",
                capture_id=request.capture_id,
                requested=requested,
                applied=tuple(applied),
                runtime_id=record.geometry.runtime_id,
                geometry_revision=record.geometry.geometry_revision,
            )
        except AutomationBusyError as e:
            result = self._make_result(
                StableCode.AUTOMATION_BUSY,
                request_id,
                action,
                str(e),
                capture_id=request.capture_id,
                requested=requested,
                phase=FailurePhase.ROUTING,
            )
        except _ActionAbort as e:
            result = self._make_result(
                e.code,
                request_id,
                action,
                str(e),
                capture_id=request.capture_id,
                requested=requested,
                applied=tuple(applied),
                runtime_id=record.geometry.runtime_id if record else None,
                geometry_revision=(
                    record.geometry.geometry_revision if record else None
                ),
                phase=e.phase,
                execution=e.execution_state,
                next_action=e.next_action,
            )
        except asyncio.CancelledError:
            cancelled = True
            input_ambiguous = input_ambiguous or input_confirmed
            result = self._make_result(
                StableCode.CLEANUP_UNCONFIRMED,
                request_id,
                action,
                "Action was cancelled; cleanup and reconciliation were required",
                capture_id=request.capture_id,
                requested=requested,
                applied=tuple(applied),
                runtime_id=record.geometry.runtime_id if record else None,
                geometry_revision=(
                    record.geometry.geometry_revision if record else None
                ),
                phase=FailurePhase.TRANSPORT,
            )
        except IPCError as e:
            execution = (
                ExecutionState.OUTCOME_UNKNOWN
                if input_ambiguous or input_confirmed
                else ExecutionState.NOT_STARTED
            )
            result = self._make_result(
                StableCode.RUNTIME_UNREACHABLE,
                request_id,
                action,
                str(e),
                capture_id=request.capture_id,
                requested=requested,
                applied=tuple(applied),
                runtime_id=record.geometry.runtime_id if record else None,
                geometry_revision=(
                    record.geometry.geometry_revision if record else None
                ),
                phase=FailurePhase.TRANSPORT,
                execution=execution,
            )
        except Exception as e:
            execution = (
                ExecutionState.OUTCOME_UNKNOWN
                if input_ambiguous or input_confirmed
                else ExecutionState.NOT_STARTED
            )
            result = self._make_result(
                StableCode.ACTION_REJECTED,
                request_id,
                action,
                f"Unexpected automation failure: {e}",
                capture_id=request.capture_id,
                requested=requested,
                applied=tuple(applied),
                runtime_id=record.geometry.runtime_id if record else None,
                geometry_revision=(
                    record.geometry.geometry_revision if record else None
                ),
                phase=FailurePhase.TRANSPORT,
                execution=execution,
            )

        observed = observed or readiness.observed
        cleanup_owner = cleanup_owner or readiness.owner
        cleanup_needed = cleanup_needed or readiness.cleanup_needed
        if cleanup_needed and client is not None and pinned_endpoint is not None:
            if cleanup_owner is None and observed is not None:
                cleanup_owner = DirtyOwnership(
                    endpoint=pinned_endpoint,
                    runtime_id=observed.runtime_id,
                    original_tablet_mode=observed.pending_tablet_mode,
                    original_mouse_untrap=observed.pending_mouse_untrap,
                    owned_tablet_mode=observed.pending_tablet_mode,
                    owned_mouse_untrap=observed.pending_mouse_untrap,
                    input_config_revision=observed.input_config_revision,
                )
            try:
                cleanup = await self._bounded_cleanup(client, cleanup_owner)
            finally:
                if pin_entered and pin_manager is not None:
                    await pin_manager.__aexit__(None, None, None)
                    pin_entered = False
            cancelled = cancelled or cleanup.cancelled
            if result is not None:
                result = replace(
                    result,
                    cleanup_state=cleanup.state,
                    cleanup_context=cleanup.context,
                )
            if cleanup.state is CleanupState.UNCONFIRMED:
                cleanup_phase = (
                    FailurePhase.RELEASE
                    if "release_error" in cleanup.context
                    or cleanup.context.get("release") == "not_confirmed"
                    else FailurePhase.RESTORE
                )
                result = self._make_result(
                    StableCode.CLEANUP_UNCONFIRMED,
                    request_id,
                    action,
                    "Action cleanup could not be confirmed",
                    capture_id=request.capture_id,
                    requested=requested,
                    applied=tuple(applied),
                    runtime_id=record.geometry.runtime_id if record else None,
                    geometry_revision=(
                        record.geometry.geometry_revision if record else None
                    ),
                    phase=cleanup_phase,
                    cleanup=cleanup.state,
                    cleanup_context=cleanup.context,
                )
        elif pin_entered and pin_manager is not None:
            await pin_manager.__aexit__(None, None, None)
            pin_entered = False

        mutation_possible = (
            input_ambiguous or input_confirmed or readiness.mutation_possible
        )
        if (
            cancelled
            and mutation_possible
            and result is not None
            and result.cleanup_state is CleanupState.CONFIRMED
        ):
            result = self._make_result(
                StableCode.ACTION_REJECTED,
                request_id,
                action,
                "Action was cancelled after a mutation could have occurred",
                capture_id=request.capture_id,
                requested=requested,
                applied=tuple(applied),
                runtime_id=record.geometry.runtime_id if record else None,
                geometry_revision=(
                    record.geometry.geometry_revision if record else None
                ),
                phase=FailurePhase.TRANSPORT,
                execution=ExecutionState.OUTCOME_UNKNOWN,
                next_action=NextAction.RECONCILE,
                cleanup=CleanupState.CONFIRMED,
                cleanup_context=result.cleanup_context,
            )

        if result is None:
            result = self._make_result(
                StableCode.ACTION_REJECTED,
                request_id,
                action,
                "Action ended without a result",
                capture_id=request.capture_id,
                requested=requested,
                phase=FailurePhase.TRANSPORT,
            )

        outcome_unknown = result.execution_state is ExecutionState.OUTCOME_UNKNOWN
        retain_terminal = not (
            result.execution_state is ExecutionState.NOT_STARTED and result.retryable
        )
        if cancelled and not mutation_possible:
            retain_terminal = False
            outcome_unknown = False
        self._state.finish_request(
            request_id,
            result,
            outcome_unknown=outcome_unknown,
            retain_terminal=retain_terminal,
        )
        logger.info(
            "GUI automation action result",
            extra={
                "controller_id": result.controller_id,
                "request_id": result.request_id,
                "capture_id": result.capture_id,
                "endpoint": pinned_endpoint,
                "runtime_id": result.runtime_id,
                "geometry_revision": result.geometry_revision,
                "gui_action": result.action.value,
                "failure_phase": (
                    result.failure_phase.value if result.failure_phase else None
                ),
                "stable_code": result.code.value,
                "execution_state": result.execution_state.value,
                "cleanup_state": result.cleanup_state.value,
            },
        )
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def _run_sequence(
        self,
        action: GuiAction,
        request: MoveRequest | ClickRequest | DragRequest,
        mapped: tuple[Point, ...],
        guarded: Callable[[Point, int, FailurePhase], Awaitable[None]],
    ) -> None:
        """Emit the private complete-mask transition sequence for one action."""
        if action is GuiAction.MOVE and isinstance(request, MoveRequest):
            await guarded(mapped[0], 0, FailurePhase.MOVE)
            if request.dwell_ms:
                await asyncio.sleep(request.dwell_ms / 1000)
            return

        if action is GuiAction.CLICK and isinstance(request, ClickRequest):
            button = MouseButton(request.button)
            await guarded(mapped[0], 0, FailurePhase.MOVE)
            for click_index in range(request.click_count):
                await guarded(mapped[0], button._mask, FailurePhase.PRESS)
                await asyncio.sleep(INPUT_TRANSITION_INTERVAL)
                await guarded(mapped[0], 0, FailurePhase.RELEASE)
                if click_index + 1 < request.click_count:
                    await asyncio.sleep(INPUT_TRANSITION_INTERVAL)
            return

        if action is GuiAction.DRAG and isinstance(request, DragRequest):
            button = MouseButton(request.button)
            await guarded(mapped[0], 0, FailurePhase.MOVE)
            await guarded(mapped[0], button._mask, FailurePhase.PRESS)
            await asyncio.sleep(INPUT_TRANSITION_INTERVAL)
            await guarded(mapped[1], button._mask, FailurePhase.DRAG)
            await asyncio.sleep(INPUT_TRANSITION_INTERVAL)
            await guarded(mapped[1], 0, FailurePhase.RELEASE)
            return
        raise _ActionAbort(
            StableCode.ACTION_REJECTED,
            FailurePhase.VALIDATION,
            "Request model does not match its action",
        )

    @staticmethod
    def _capture_matches_state(
        record: CaptureRecord, observed: AutomationState
    ) -> bool:
        """Return whether state still matches every capture guard identity."""
        geometry = record.geometry
        return (
            observed.geometry_valid
            and observed.runtime_id == geometry.runtime_id
            and observed.geometry_revision == geometry.geometry_revision
            and observed.monitor_id == geometry.monitor_id
        )

    async def _query_until(
        self,
        client: AmiberryIPCClient,
        predicate: Callable[[AutomationState], bool],
    ) -> AutomationState:
        """Poll command-scoped state until a readiness predicate is true."""
        deadline = asyncio.get_running_loop().time() + READINESS_TIMEOUT
        observed = await client._get_gui_automation_state()
        while not predicate(observed):
            if asyncio.get_running_loop().time() >= deadline:
                return observed
            await asyncio.sleep(READINESS_POLL_INTERVAL)
            observed = await client._get_gui_automation_state()
        return observed

    async def _prepare_readiness(
        self,
        client: AmiberryIPCClient,
        endpoint: str,
        record: CaptureRecord,
        context: _ReadinessContext,
    ) -> AutomationState:
        """Settle existing changes, apply minimum settings, and verify focus."""
        observed = await client._get_gui_automation_state()
        context.observed = observed
        if not self._capture_matches_state(record, observed):
            self._state.invalidate_endpoint_captures(endpoint)
            raise _ActionAbort(
                StableCode.CAPTURE_STALE,
                FailurePhase.READINESS,
                "Capture identity no longer matches the runtime",
            )

        if observed.pending_effective_diverged:
            observed = await self._query_until(
                client, lambda state: not state.pending_effective_diverged
            )
            context.observed = observed
            if observed.pending_effective_diverged:
                raise _ActionAbort(
                    StableCode.INPUT_NOT_READY,
                    FailurePhase.READINESS,
                    "A pre-existing input configuration change has not settled",
                )
            if not self._capture_matches_state(record, observed):
                raise _ActionAbort(
                    StableCode.CAPTURE_STALE,
                    FailurePhase.READINESS,
                    "Geometry changed while input settings settled",
                )

        desired_tablet = _desired_tablet_mode(observed.pending_tablet_mode)
        desired_untrap = _desired_mouse_untrap(observed.pending_mouse_untrap)
        settings_changed = (
            desired_tablet != observed.pending_tablet_mode
            or desired_untrap != observed.pending_mouse_untrap
        )
        if settings_changed:
            context.owner = DirtyOwnership(
                endpoint=endpoint,
                runtime_id=observed.runtime_id,
                original_tablet_mode=observed.pending_tablet_mode,
                original_mouse_untrap=observed.pending_mouse_untrap,
                owned_tablet_mode=desired_tablet,
                owned_mouse_untrap=desired_untrap,
                input_config_revision=observed.input_config_revision,
            )
            context.cleanup_needed = True
            context.mutation_possible = True
            response = await client._set_gui_automation_config(
                observed.pending_tablet_mode,
                observed.pending_mouse_untrap,
                observed.input_config_revision,
                desired_tablet,
                desired_untrap,
            )
            if not response.applied:
                context.cleanup_needed = False
                context.owner = None
                context.mutation_possible = False
                raise _ActionAbort(
                    StableCode.INPUT_NOT_READY,
                    FailurePhase.READINESS,
                    "Input configuration ownership changed before apply",
                )
            observed = await self._query_until(
                client,
                lambda state: (
                    not state.pending_effective_diverged
                    and state.effective_tablet_mode == desired_tablet
                    and state.effective_mouse_untrap == desired_untrap
                ),
            )
            context.observed = observed
            context.owner = replace(
                context.owner, input_config_revision=observed.input_config_revision
            )
            if (
                observed.pending_effective_diverged
                or observed.effective_tablet_mode != desired_tablet
                or observed.effective_mouse_untrap != desired_untrap
            ):
                raise _ActionAbort(
                    StableCode.INPUT_NOT_READY,
                    FailurePhase.READINESS,
                    "Temporary input settings did not become effective",
                )

        if not observed.focus_ready:
            observed = await self._query_until(client, lambda state: state.focus_ready)
            context.observed = observed
        if not self._capture_matches_state(record, observed):
            raise _ActionAbort(
                StableCode.CAPTURE_STALE,
                FailurePhase.READINESS,
                "Geometry changed while waiting for input readiness",
            )
        if not observed.focus_ready:
            raise _ActionAbort(
                StableCode.INPUT_NOT_READY,
                FailurePhase.READINESS,
                "Amiberry input focus is not ready",
            )
        return observed

    async def _reconcile_dirty(
        self, client: AmiberryIPCClient, endpoint: str, record: CaptureRecord
    ) -> None:
        """Reconcile uncertain release/restoration before emitting new input."""
        owner = self._state.dirty_ownership.get(endpoint)
        if owner is None:
            return
        try:
            observed = await client._get_gui_automation_state()
            if observed.runtime_id != owner.runtime_id:
                self._state.dirty_ownership.pop(endpoint, None)
                self._state.invalidate_endpoint_captures(
                    endpoint, runtime_id=observed.runtime_id
                )
                return
            release = await client._release_mouse_buttons()
            if not release.confirmed:
                raise IPCError("Mouse release was not confirmed")
            if (
                owner.original_tablet_mode != owner.owned_tablet_mode
                or owner.original_mouse_untrap != owner.owned_mouse_untrap
            ):
                if (
                    observed.pending_tablet_mode != owner.owned_tablet_mode
                    or observed.pending_mouse_untrap != owner.owned_mouse_untrap
                    or observed.input_config_revision != owner.input_config_revision
                ):
                    self._state.dirty_ownership.pop(endpoint, None)
                    return
                restored = await client._set_gui_automation_config(
                    owner.owned_tablet_mode,
                    owner.owned_mouse_untrap,
                    observed.input_config_revision,
                    owner.original_tablet_mode,
                    owner.original_mouse_untrap,
                )
                if not restored.applied:
                    self._state.dirty_ownership.pop(endpoint, None)
                    return
                settled = await self._query_until(
                    client,
                    lambda state: (
                        not state.pending_effective_diverged
                        and state.effective_tablet_mode == owner.original_tablet_mode
                        and state.effective_mouse_untrap == owner.original_mouse_untrap
                    ),
                )
                if (
                    settled.pending_effective_diverged
                    or settled.effective_tablet_mode != owner.original_tablet_mode
                    or settled.effective_mouse_untrap != owner.original_mouse_untrap
                ):
                    raise IPCError("Restored input settings did not become effective")
            self._state.dirty_ownership.pop(endpoint, None)
        except IPCError as e:
            raise _ActionAbort(
                StableCode.RUNTIME_UNREACHABLE,
                FailurePhase.READINESS,
                f"Dirty-state reconciliation failed: {e}",
            ) from e

        if not self._capture_matches_state(record, observed):
            raise _ActionAbort(
                StableCode.CAPTURE_STALE,
                FailurePhase.READINESS,
                "Runtime changed while reconciling uncertain cleanup",
            )

    async def _bounded_cleanup(
        self, client: AmiberryIPCClient, owner: DirtyOwnership | None
    ) -> _CleanupOutcome:
        """Shield a strongly referenced cleanup task within a fixed deadline."""
        task = asyncio.create_task(self._cleanup(client, owner))
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)
        deadline = asyncio.get_running_loop().time() + CLEANUP_TIMEOUT
        cancellation_seen = False
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                if owner is not None:
                    self._state.dirty_ownership[owner.endpoint] = owner
                return _CleanupOutcome(
                    CleanupState.UNCONFIRMED,
                    {"reason": "cleanup_timeout", "reconciliation_required": True},
                    cancelled=cancellation_seen,
                )
            try:
                outcome = await asyncio.wait_for(
                    asyncio.shield(task), timeout=remaining
                )
                return replace(outcome, cancelled=cancellation_seen)
            except asyncio.CancelledError:
                cancellation_seen = True
                continue
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                if owner is not None:
                    self._state.dirty_ownership[owner.endpoint] = owner
                return _CleanupOutcome(
                    CleanupState.UNCONFIRMED,
                    {
                        "reason": "cleanup_failed",
                        "error": str(e),
                        "reconciliation_required": True,
                    },
                    cancelled=cancellation_seen,
                )

    async def _cleanup(
        self, client: AmiberryIPCClient, owner: DirtyOwnership | None
    ) -> _CleanupOutcome:
        """Release first, then restore owned settings without overwriting users."""
        context: dict[str, Any] = {}
        release_confirmed = False
        restoration_confirmed = owner is None or (
            owner.original_tablet_mode == owner.owned_tablet_mode
            and owner.original_mouse_untrap == owner.owned_mouse_untrap
        )
        ownership_lost = False
        restarted = False
        try:
            release = await client._release_mouse_buttons()
            release_confirmed = release.confirmed
            if not release.confirmed:
                context["release"] = "not_confirmed"
        except IPCError as e:
            context["release_error"] = str(e)

        if owner is not None and not restoration_confirmed:
            try:
                observed = await client._get_gui_automation_state()
                if observed.runtime_id != owner.runtime_id:
                    restarted = True
                    restoration_confirmed = True
                    context["restore"] = "runtime_restarted_ownership_discarded"
                    self._state.invalidate_endpoint_captures(
                        owner.endpoint, runtime_id=observed.runtime_id
                    )
                elif (
                    observed.pending_tablet_mode != owner.owned_tablet_mode
                    or observed.pending_mouse_untrap != owner.owned_mouse_untrap
                    or observed.input_config_revision != owner.input_config_revision
                ):
                    ownership_lost = True
                    context["restore"] = "ownership_lost_external_value_preserved"
                else:
                    restored = await client._set_gui_automation_config(
                        owner.owned_tablet_mode,
                        owner.owned_mouse_untrap,
                        observed.input_config_revision,
                        owner.original_tablet_mode,
                        owner.original_mouse_untrap,
                    )
                    if restored.applied:
                        settled = await self._query_until(
                            client,
                            lambda state: (
                                not state.pending_effective_diverged
                                and state.effective_tablet_mode
                                == owner.original_tablet_mode
                                and state.effective_mouse_untrap
                                == owner.original_mouse_untrap
                            ),
                        )
                        restoration_confirmed = (
                            not settled.pending_effective_diverged
                            and settled.effective_tablet_mode
                            == owner.original_tablet_mode
                            and settled.effective_mouse_untrap
                            == owner.original_mouse_untrap
                        )
                    if not restoration_confirmed:
                        context["restore"] = "not_confirmed"
            except IPCError as e:
                context["restore_error"] = str(e)

        confirmed = release_confirmed and restoration_confirmed and not ownership_lost
        if owner is not None:
            if confirmed or ownership_lost or restarted:
                self._state.dirty_ownership.pop(owner.endpoint, None)
            else:
                self._state.dirty_ownership[owner.endpoint] = owner
        if confirmed:
            return _CleanupOutcome(CleanupState.CONFIRMED, context)
        context["reconciliation_required"] = not ownership_lost and not restarted
        return _CleanupOutcome(CleanupState.UNCONFIRMED, context)
