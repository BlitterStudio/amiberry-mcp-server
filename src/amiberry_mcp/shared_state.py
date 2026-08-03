"""
Shared process state and IPC client management.

Provides the ProcessState dataclass, IPC client caching, and process launch
helpers used by both the MCP server and the HTTP API server. Each server
process gets its own module-level state instance.
"""

import asyncio
import secrets
import subprocess
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any

from .common import launch_process
from .ipc_client import (
    ActionableCaptureGeometry,
    AmiberryIPCClient,
    MouseUntrapMode,
    TabletMode,
)

CAPTURE_REGISTRY_LIMIT = 128
CAPTURE_TOMBSTONE_LIMIT = 128
REQUEST_LEDGER_LIMIT = 256
REQUEST_IN_FLIGHT_LIMIT = 64


class MutationImpact(str, Enum):
    """Coordinator behavior required by a runtime operation."""

    READ_ONLY = "read_only"
    SERIALIZE = "serialize"
    SERIALIZE_GEOMETRY_INVALIDATE = "serialize_geometry_invalidate"
    ROUTE_LIFECYCLE_RESET = "route_lifecycle_reset"


_SERIALIZED_OPERATIONS = frozenset(
    {
        "runtime_gui_move",
        "runtime_gui_click",
        "runtime_gui_drag",
        "runtime_send_mouse",
        "runtime_send_mouse_abs",
        "runtime_set_mouse_speed",
        "runtime_toggle_mouse_grab",
    }
)
_GEOMETRY_OPERATIONS = frozenset(
    {
        "runtime_toggle_fullscreen",
        "runtime_set_display_mode",
        "runtime_toggle_rtg",
        "runtime_set_scaling",
        "runtime_set_resolution",
        "runtime_set_autocrop",
        "runtime_set_window_size",
        "runtime_set_line_mode",
        "runtime_set_ntsc",
        "runtime_toggle_status_line",
        "runtime_load_config",
        "runtime_load_state",
        "runtime_quickload",
        "reset_emulation",
    }
)
_LIFECYCLE_OPERATIONS = frozenset(
    {
        "runtime_select_instance",
        "runtime_launch",
        "runtime_restart",
        "runtime_kill",
        "runtime_quit",
        "set_active_instance",
        "launch_amiberry",
        "launch_and_wait_for_ipc",
        "launch_cd",
        "launch_whdload",
        "launch_with_logging",
        "set_disk_swapper",
        "restart_amiberry",
        "kill_amiberry",
    }
)
_GEOMETRY_CONFIG_OPTIONS = frozenset(
    {
        "gfx_fullscreen",
        "ntsc",
        "tablet_mode",
        "mouse_untrap",
    }
)


def mutation_impact_for(operation: str, option: str | None = None) -> MutationImpact:
    """Classify an operation using the central mutation-impact policy."""
    if operation in _LIFECYCLE_OPERATIONS:
        return MutationImpact.ROUTE_LIFECYCLE_RESET
    if operation in _GEOMETRY_OPERATIONS:
        return MutationImpact.SERIALIZE_GEOMETRY_INVALIDATE
    if operation == "runtime_set_config" and option in _GEOMETRY_CONFIG_OPTIONS:
        return MutationImpact.SERIALIZE_GEOMETRY_INVALIDATE
    if operation in _SERIALIZED_OPERATIONS:
        return MutationImpact.SERIALIZE
    return MutationImpact.READ_ONLY


def normalize_endpoint(instance: int | None) -> str:
    """Return the stable abstract route key used by controller state.

    ``None`` means auto-discovery and remains distinct from explicit instance 0,
    even when both currently resolve to the same socket. This conservative
    identity prevents a capture from silently following a later discovery.
    """
    return "instance:default" if instance is None else f"instance:{instance}"


@dataclass(frozen=True)
class CaptureRecord:
    """Process-local binding between a capture ID and upstream geometry."""

    capture_id: str
    controller_id: str
    endpoint: str
    path: Path
    geometry: ActionableCaptureGeometry
    stale: bool = False


@dataclass(frozen=True)
class CaptureTombstone:
    """Bounded evidence that a known capture was evicted."""

    capture_id: str
    controller_id: str


@dataclass(frozen=True)
class CaptureLookup:
    """Stable capture lookup classification plus an optional record."""

    status: str
    record: CaptureRecord | None = None


@dataclass
class RequestLedgerEntry:
    """One retained request identity and its replay state."""

    request_id: str
    payload_hash: str
    state: str = "in_progress"
    result: Any | None = None
    endpoint: str | None = None


@dataclass(frozen=True)
class RequestReservation:
    """Result of attempting to reserve a request identity."""

    status: str
    entry: RequestLedgerEntry | None = None


@dataclass(frozen=True)
class DirtyOwnership:
    """Runtime-bound cleanup ownership retained after uncertain cleanup."""

    endpoint: str
    runtime_id: str
    original_tablet_mode: TabletMode
    original_mouse_untrap: MouseUntrapMode
    owned_tablet_mode: TabletMode
    owned_mouse_untrap: MouseUntrapMode
    input_config_revision: int


@dataclass(frozen=True)
class PinnedEndpoint:
    """Route and client pinned while its endpoint action lock is held."""

    endpoint: str
    instance: int | None
    client: AmiberryIPCClient


class AutomationBusyError(RuntimeError):
    """The endpoint action lock could not be acquired in time."""


@dataclass
class ProcessState:
    """Holds the state of the managed Amiberry process and IPC connection."""

    process: subprocess.Popen | None = None
    launch_cmd: list[str] | None = None
    log_path: Path | None = None
    log_file_handle: Any | None = None
    log_read_positions: dict[str, int] = field(default_factory=dict)
    active_instance: int | None = None
    ipc_client_cache: tuple[int | None, AmiberryIPCClient] | None = None
    controller_id: str = field(
        default_factory=lambda: f"ctrl_{secrets.token_urlsafe(16)}"
    )
    routing_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    endpoint_locks: dict[str, asyncio.Lock] = field(default_factory=dict, repr=False)
    captures: OrderedDict[str, CaptureRecord] = field(
        default_factory=OrderedDict, repr=False
    )
    capture_tombstones: OrderedDict[str, CaptureTombstone] = field(
        default_factory=OrderedDict, repr=False
    )
    request_terminal: OrderedDict[str, RequestLedgerEntry] = field(
        default_factory=OrderedDict, repr=False
    )
    request_active: dict[str, RequestLedgerEntry] = field(
        default_factory=dict, repr=False
    )
    dirty_ownership: dict[str, DirtyOwnership] = field(default_factory=dict, repr=False)

    def close_log_handle(self) -> None:
        """Close the log file handle if open."""
        if self.log_file_handle is not None:
            try:
                self.log_file_handle.close()
            except OSError:
                pass
            self.log_file_handle = None

    @property
    def active_endpoint(self) -> str:
        """Return the normalized endpoint currently selected for routing."""
        return normalize_endpoint(self.active_instance)

    def endpoint_lock(self, endpoint: str) -> asyncio.Lock:
        """Return the stable per-endpoint action lock."""
        return self.endpoint_locks.setdefault(endpoint, asyncio.Lock())

    def _prune_endpoint_locks(self, keep: set[str]) -> None:
        """Drop inactive unlocked endpoint locks while routing is held."""
        for endpoint, lock in tuple(self.endpoint_locks.items()):
            if endpoint not in keep and not lock.locked():
                self.endpoint_locks.pop(endpoint, None)

    def new_capture_id(self) -> str:
        """Create an unpredictable controller-namespaced capture ID."""
        return f"{self.controller_id}:cap_{secrets.token_urlsafe(18)}"

    def register_capture(self, record: CaptureRecord) -> None:
        """Register a capture and retain bounded eviction evidence."""
        if record.controller_id != self.controller_id:
            raise ValueError("Capture controller does not match this process")
        self.captures[record.capture_id] = record
        self.captures.move_to_end(record.capture_id)
        while len(self.captures) > CAPTURE_REGISTRY_LIMIT:
            capture_id, evicted = self.captures.popitem(last=False)
            self.capture_tombstones[capture_id] = CaptureTombstone(
                capture_id=capture_id,
                controller_id=evicted.controller_id,
            )
        while len(self.capture_tombstones) > CAPTURE_TOMBSTONE_LIMIT:
            self.capture_tombstones.popitem(last=False)

    def resolve_capture(self, capture_id: str, endpoint: str) -> CaptureLookup:
        """Resolve a capture without weakening controller or route binding."""
        record = self.captures.get(capture_id)
        if record is not None:
            self.captures.move_to_end(capture_id)
            if record.stale:
                return CaptureLookup("capture_stale", record)
            if record.endpoint != endpoint:
                return CaptureLookup("capture_wrong_instance", record)
            return CaptureLookup("ok", record)
        if capture_id in self.capture_tombstones:
            return CaptureLookup("capture_evicted")
        if (
            capture_id.startswith("ctrl_")
            and ":cap_" in capture_id
            and not capture_id.startswith(f"{self.controller_id}:cap_")
        ):
            return CaptureLookup("capture_wrong_controller")
        return CaptureLookup("capture_unknown")

    def invalidate_endpoint_captures(
        self, endpoint: str, runtime_id: str | None = None
    ) -> None:
        """Mark endpoint captures stale, optionally retaining one live runtime."""
        for capture_id, record in tuple(self.captures.items()):
            if record.endpoint == endpoint and (
                runtime_id is None or record.geometry.runtime_id != runtime_id
            ):
                self.captures[capture_id] = replace(record, stale=True)

    def reserve_request(self, request_id: str, payload_hash: str) -> RequestReservation:
        """Reserve a request without evicting active or unknown outcomes."""
        existing = self.request_active.get(request_id)
        if existing is None:
            existing = self.request_terminal.get(request_id)
        if existing is not None:
            if existing.payload_hash != payload_hash:
                return RequestReservation("conflict", existing)
            return RequestReservation(existing.state, existing)
        if len(self.request_active) >= REQUEST_IN_FLIGHT_LIMIT:
            return RequestReservation("busy")
        entry = RequestLedgerEntry(request_id=request_id, payload_hash=payload_hash)
        self.request_active[request_id] = entry
        return RequestReservation("reserved", entry)

    def finish_request(
        self,
        request_id: str,
        result: Any,
        *,
        outcome_unknown: bool = False,
        retain_terminal: bool = True,
    ) -> None:
        """Finish, retain, or release a request reservation truthfully."""
        entry = self.request_active.get(request_id)
        if entry is None:
            return
        entry.result = result
        if outcome_unknown:
            entry.state = "outcome_unknown"
            return
        self.request_active.pop(request_id, None)
        if not retain_terminal:
            return
        entry.state = "terminal"
        self.request_terminal[request_id] = entry
        self.request_terminal.move_to_end(request_id)
        while len(self.request_terminal) > REQUEST_LEDGER_LIMIT:
            self.request_terminal.popitem(last=False)

    def mark_active_requests_outcome_unknown(self, endpoint: str) -> None:
        """Prevent replay after an unexpected endpoint exit."""
        for entry in self.request_active.values():
            if entry.endpoint == endpoint and entry.state == "in_progress":
                entry.state = "outcome_unknown"

    async def select_instance(self, instance: int | None) -> None:
        """Publish an instance change under routing and canonical endpoint locks."""
        async with self.routing_lock:
            endpoints = sorted({self.active_endpoint, normalize_endpoint(instance)})
            locks = [self.endpoint_lock(endpoint) for endpoint in endpoints]
            acquired_locks: list[asyncio.Lock] = []
            try:
                for lock in locks:
                    await lock.acquire()
                    acquired_locks.append(lock)
                cached = self.ipc_client_cache
                if cached is not None:
                    await cached[1].close()
                self.active_instance = instance
                self.ipc_client_cache = None
            finally:
                for lock in reversed(acquired_locks):
                    lock.release()
                self._prune_endpoint_locks({self.active_endpoint})

    @asynccontextmanager
    async def reset_endpoint_transition(
        self, instance: int | None, *, unexpected_exit: bool = False
    ) -> AsyncIterator[None]:
        """Reset one endpoint and hold lifecycle locks across its mutation.

        Expected launch/restart/kill transitions discard obsolete cleanup
        ownership. Unexpected exits retain ownership and mark only requests
        bound to the affected endpoint outcome-unknown for later reconciliation.
        """
        endpoint = normalize_endpoint(instance)
        async with self.routing_lock:
            lock = self.endpoint_lock(endpoint)
            await lock.acquire()
            completed = False
            try:
                cached = self.ipc_client_cache
                if cached is not None and cached[0] == instance:
                    await cached[1].close()
                    self.ipc_client_cache = None
                self.invalidate_endpoint_captures(endpoint)
                yield
                completed = True
            finally:
                if completed:
                    if unexpected_exit:
                        self.mark_active_requests_outcome_unknown(endpoint)
                    else:
                        self.dirty_ownership.pop(endpoint, None)
                lock.release()
                self._prune_endpoint_locks({self.active_endpoint})

    async def reset_endpoint(
        self, instance: int | None, *, unexpected_exit: bool = False
    ) -> None:
        """Invalidate one runtime endpoint under lifecycle lock ordering."""
        async with self.reset_endpoint_transition(
            instance, unexpected_exit=unexpected_exit
        ):
            pass


# Module-level state — each importing process gets its own instance.
_state = ProcessState()


def get_state() -> ProcessState:
    """Return the global process state singleton."""
    return _state


def get_state_lock() -> asyncio.Lock:
    """Return the lock for synchronising state mutations."""
    return _state.routing_lock


@asynccontextmanager
async def pin_active_endpoint(
    state: ProcessState | None = None, timeout: float = 0.25
) -> AsyncIterator[PinnedEndpoint]:
    """Pin routing and endpoint within one total bounded acquisition deadline."""
    if state is None:
        state = _state
    acquired_endpoint = False
    acquired_routing = False
    endpoint_lock: asyncio.Lock | None = None
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        try:
            await asyncio.wait_for(state.routing_lock.acquire(), timeout=timeout)
        except asyncio.TimeoutError as e:
            raise AutomationBusyError("Controller routing is busy") from e
        acquired_routing = True
        instance = state.active_instance
        endpoint = normalize_endpoint(instance)
        endpoint_lock = state.endpoint_lock(endpoint)
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise AutomationBusyError("Controller routing acquisition timed out")
        try:
            await asyncio.wait_for(endpoint_lock.acquire(), timeout=remaining)
        except asyncio.TimeoutError as e:
            raise AutomationBusyError(f"Endpoint {endpoint} is busy") from e
        acquired_endpoint = True
        client = get_ipc_client(state)
    finally:
        if acquired_routing:
            state.routing_lock.release()

    try:
        yield PinnedEndpoint(endpoint=endpoint, instance=instance, client=client)
    finally:
        if acquired_endpoint and endpoint_lock is not None:
            endpoint_lock.release()


@asynccontextmanager
async def coordinated_runtime_operation(
    operation: str,
    option: str | None = None,
    state: ProcessState | None = None,
    timeout: float = 0.25,
) -> AsyncIterator[PinnedEndpoint]:
    """Coordinate one classified adapter operation.

    Read-only operations pin only the client selection moment and never take an
    action lock. Serialized mutations hold the endpoint lock. Geometry-impacting
    mutations conservatively stale that endpoint's captures before unlocking.
    Lifecycle-reset operations use ``select_instance`` or ``reset_endpoint`` so
    their affected endpoint set and cache transition are explicit.
    """
    if state is None:
        state = _state
    impact = mutation_impact_for(operation, option)
    if impact is MutationImpact.ROUTE_LIFECYCLE_RESET:
        raise ValueError("Lifecycle operations require an explicit state transition")
    if impact is MutationImpact.READ_ONLY:
        try:
            await asyncio.wait_for(state.routing_lock.acquire(), timeout=timeout)
        except asyncio.TimeoutError as e:
            raise AutomationBusyError("Controller routing is busy") from e
        try:
            pinned = PinnedEndpoint(
                endpoint=state.active_endpoint,
                instance=state.active_instance,
                client=get_ipc_client(state),
            )
        finally:
            state.routing_lock.release()
        yield pinned
        return

    async with pin_active_endpoint(state, timeout=timeout) as pinned:
        try:
            yield pinned
        finally:
            if impact is MutationImpact.SERIALIZE_GEOMETRY_INVALIDATE:
                state.invalidate_endpoint_captures(pinned.endpoint)


def get_ipc_client(state: ProcessState | None = None) -> AmiberryIPCClient:
    """Get an IPC client for the active instance, reusing cached clients.

    Args:
        state: Optional explicit state; defaults to the module singleton.
    """
    if state is None:
        state = _state
    if (
        state.ipc_client_cache is not None
        and state.ipc_client_cache[0] == state.active_instance
    ):
        return state.ipc_client_cache[1]
    client = AmiberryIPCClient(prefer_dbus=False, instance=state.active_instance)
    state.ipc_client_cache = (state.active_instance, client)
    return client


def launch_and_store(
    cmd: list[str],
    log_path: Path | None = None,
    state: ProcessState | None = None,
) -> subprocess.Popen:
    """Launch Amiberry, store state, and return the process.

    Centralises the close-log -> launch -> store-state pattern used by
    all launch handlers.

    Args:
        cmd: Command to execute.
        log_path: If provided, stdout is redirected to this log file.
        state: Optional explicit state; defaults to the module singleton.
    """
    if state is None:
        state = _state
    state.close_log_handle()
    state.ipc_client_cache = None
    proc, log_handle = launch_process(cmd, log_path=log_path)
    state.process = proc
    state.launch_cmd = cmd
    state.log_path = log_path
    state.log_file_handle = log_handle
    return proc
