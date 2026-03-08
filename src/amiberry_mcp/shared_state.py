"""
Shared process state and IPC client management.

Provides the ProcessState dataclass, IPC client caching, and process launch
helpers used by both the MCP server and the HTTP API server. Each server
process gets its own module-level state instance.
"""

import asyncio
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .common import launch_process
from .ipc_client import AmiberryIPCClient


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

    def close_log_handle(self) -> None:
        """Close the log file handle if open."""
        if self.log_file_handle is not None:
            try:
                self.log_file_handle.close()
            except OSError:
                pass
            self.log_file_handle = None


# Module-level state — each importing process gets its own instance.
_state = ProcessState()
_state_lock = asyncio.Lock()


def get_state() -> ProcessState:
    """Return the global process state singleton."""
    return _state


def get_state_lock() -> asyncio.Lock:
    """Return the lock for synchronising state mutations."""
    return _state_lock


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
