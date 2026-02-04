"""Amiberry MCP Server - Control Amiberry emulator through Claude AI."""

__version__ = "1.0.0"

from .ipc_client import (
    AmiberryIPCClient,
    IPCError,
    ConnectionError,
    CommandError,
    send_ipc_command,
)

__all__ = [
    "AmiberryIPCClient",
    "IPCError",
    "ConnectionError",
    "CommandError",
    "send_ipc_command",
]
