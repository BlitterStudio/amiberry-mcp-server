"""Amiberry MCP Server - Control the Amiberry emulator from any MCP-compatible AI assistant."""

__version__ = "1.0.0"

from .ipc_client import (
    AmiberryIPCClient,
    CommandError,
    IPCConnectionError,
    IPCError,
    send_ipc_command,
)

__all__ = [
    "AmiberryIPCClient",
    "IPCError",
    "IPCConnectionError",
    "CommandError",
    "send_ipc_command",
]
