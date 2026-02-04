"""
IPC client for Amiberry runtime control.

Supports:
- Unix domain sockets (cross-platform: Linux, macOS, FreeBSD)
- D-Bus (Linux only, when available)

The client automatically chooses the best available transport.
"""

import asyncio
import os
import socket
import sys
from typing import Any, Optional

# Check for D-Bus support (Linux only)
DBUS_AVAILABLE = False
if sys.platform == "linux":
    try:
        from jeepney import DBusAddress, new_method_call
        from jeepney.io.asyncio import open_dbus_connection

        DBUS_AVAILABLE = True
    except ImportError:
        pass


# Socket paths
def _get_socket_path() -> str:
    """Determine the socket path based on environment."""
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime:
        path = os.path.join(xdg_runtime, "amiberry.sock")
        if os.path.exists(path):
            return path
    return "/tmp/amiberry.sock"


# D-Bus constants
DBUS_INTERFACE = "com.blitterstudio.amiberry"
DBUS_PATH = "/"


class IPCError(Exception):
    """Base exception for IPC errors."""

    pass


class ConnectionError(IPCError):
    """Failed to connect to Amiberry."""

    pass


class CommandError(IPCError):
    """Command execution failed."""

    pass


class AmiberryIPCClient:
    """
    Async IPC client for Amiberry runtime control.

    Supports Unix sockets (cross-platform) and D-Bus (Linux).
    Automatically selects the best available transport.
    """

    def __init__(self, prefer_dbus: bool = True):
        """
        Initialize the IPC client.

        Args:
            prefer_dbus: If True and on Linux with D-Bus available, prefer D-Bus
                        over Unix sockets. Set to False to always use sockets.
        """
        self._prefer_dbus = prefer_dbus and DBUS_AVAILABLE
        self._dbus_conn = None
        self._socket_path = _get_socket_path()

    @property
    def transport(self) -> str:
        """Return the transport type that will be used."""
        if self._prefer_dbus:
            return "dbus"
        return "socket"

    def is_available(self) -> bool:
        """Check if Amiberry IPC is available."""
        if self._prefer_dbus:
            # For D-Bus, we'd need to actually try connecting
            # For now, just return True if D-Bus is available
            return DBUS_AVAILABLE
        else:
            return os.path.exists(self._socket_path)

    async def _send_socket_command(
        self, command: str, *args: str, timeout: float = 5.0
    ) -> tuple[bool, list[str]]:
        """Send a command over Unix socket and return the response."""
        socket_path = self._socket_path

        if not os.path.exists(socket_path):
            raise ConnectionError(
                f"Socket not found at {socket_path}. Is Amiberry running with USE_IPC_SOCKET?"
            )

        # Build message: COMMAND\tARG1\tARG2...\n
        parts = [command.upper()] + list(args)
        message = "\t".join(parts) + "\n"

        try:
            # Create socket and connect
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(socket_path), timeout=timeout
            )

            # Send command
            writer.write(message.encode("utf-8"))
            await writer.drain()

            # Read response
            response = await asyncio.wait_for(reader.readline(), timeout=timeout)
            writer.close()
            await writer.wait_closed()

            # Parse response
            response_str = response.decode("utf-8").strip()
            parts = response_str.split("\t")

            if not parts:
                return False, ["Empty response"]

            success = parts[0] == "OK"
            data = parts[1:] if len(parts) > 1 else []

            return success, data

        except asyncio.TimeoutError:
            raise ConnectionError(f"Connection to {socket_path} timed out")
        except FileNotFoundError:
            raise ConnectionError(f"Socket not found: {socket_path}")
        except ConnectionRefusedError:
            raise ConnectionError(f"Connection refused to {socket_path}")
        except Exception as e:
            raise ConnectionError(f"Socket error: {e}")

    async def _send_dbus_command(
        self, method: str, *args: Any, timeout: float = 5.0
    ) -> tuple[bool, list[str]]:
        """Send a command over D-Bus and return the response."""
        if not DBUS_AVAILABLE:
            raise ConnectionError("D-Bus support not available")

        try:
            async with open_dbus_connection(bus="SESSION") as conn:
                addr = DBusAddress(DBUS_PATH, bus_name=DBUS_INTERFACE, interface=DBUS_INTERFACE)

                # Build the method call
                msg = new_method_call(addr, method)
                if args:
                    msg.body = args

                # Send and wait for reply
                reply = await asyncio.wait_for(conn.send_and_get_reply(msg), timeout=timeout)

                # Parse reply - D-Bus returns boolean success
                if reply.body and len(reply.body) > 0:
                    success = bool(reply.body[0])
                    data = list(reply.body[1:]) if len(reply.body) > 1 else []
                    return success, [str(d) for d in data]
                return True, []

        except asyncio.TimeoutError:
            raise ConnectionError("D-Bus call timed out")
        except Exception as e:
            raise ConnectionError(f"D-Bus error: {e}")

    async def _send_command(
        self, command: str, *args: str, timeout: float = 5.0
    ) -> tuple[bool, list[str]]:
        """Send a command using the preferred transport."""
        if self._prefer_dbus:
            return await self._send_dbus_command(command, *args, timeout=timeout)
        else:
            return await self._send_socket_command(command, *args, timeout=timeout)

    # High-level API methods

    async def pause(self) -> bool:
        """Pause emulation."""
        success, _ = await self._send_socket_command("PAUSE")
        return success

    async def resume(self) -> bool:
        """Resume emulation."""
        success, _ = await self._send_socket_command("RESUME")
        return success

    async def reset(self, hard: bool = False) -> bool:
        """
        Reset emulation.

        Args:
            hard: If True, perform a hard reset. Otherwise soft/keyboard reset.
        """
        reset_type = "HARD" if hard else "SOFT"
        success, _ = await self._send_socket_command("RESET", reset_type)
        return success

    async def quit(self) -> bool:
        """Quit Amiberry."""
        success, _ = await self._send_socket_command("QUIT")
        return success

    async def screenshot(self, filename: str) -> bool:
        """
        Take a screenshot.

        Args:
            filename: Path where the screenshot should be saved.
        """
        success, _ = await self._send_socket_command("SCREENSHOT", filename)
        return success

    async def save_state(self, state_file: str, config_file: str) -> bool:
        """
        Save emulation state.

        Args:
            state_file: Path for the savestate file (.uss)
            config_file: Path for the config file (.uae)
        """
        success, _ = await self._send_socket_command("SAVESTATE", state_file, config_file)
        return success

    async def load_state(self, state_file: str) -> bool:
        """
        Load emulation state.

        Args:
            state_file: Path to the savestate file (.uss)
        """
        success, _ = await self._send_socket_command("LOADSTATE", state_file)
        return success

    async def insert_floppy(self, drive: int, image_path: str) -> bool:
        """
        Insert a floppy disk image.

        Args:
            drive: Drive number (0-3 for DF0-DF3)
            image_path: Path to the disk image file
        """
        if not 0 <= drive <= 3:
            raise ValueError("Drive must be 0-3")
        success, _ = await self._send_socket_command("INSERTFLOPPY", image_path, str(drive))
        return success

    async def insert_cd(self, image_path: str) -> bool:
        """
        Insert a CD image.

        Args:
            image_path: Path to the CD image file
        """
        success, _ = await self._send_socket_command("INSERTCD", image_path)
        return success

    async def disk_swap(self, disk_num: int, drive_num: int) -> bool:
        """
        Swap a disk from the disk swapper list into a drive.

        Args:
            disk_num: Index in the disk swapper list
            drive_num: Target drive number (0-3)
        """
        success, _ = await self._send_socket_command("DISKSWAP", str(disk_num), str(drive_num))
        return success

    async def query_disk_swap(self, drive_num: int) -> int:
        """
        Query which disk from the swapper list is in a drive.

        Args:
            drive_num: Drive number to query (0-3)

        Returns:
            Disk index in swapper list, or -1 if not from swapper
        """
        success, data = await self._send_socket_command("QUERYDISKSWAP", str(drive_num))
        if success and data:
            return int(data[0])
        return -1

    async def get_status(self) -> dict[str, Any]:
        """
        Get current emulation status.

        Returns:
            Dictionary with status information (Paused, Config, Floppy0-3)
        """
        success, data = await self._send_socket_command("GET_STATUS")
        if not success:
            raise CommandError("Failed to get status")

        status = {}
        for item in data:
            if "=" in item:
                key, value = item.split("=", 1)
                # Convert boolean strings
                if value.lower() == "true":
                    value = True
                elif value.lower() == "false":
                    value = False
                status[key] = value

        return status

    async def get_config(self, option: str) -> Optional[str]:
        """
        Get a configuration option value.

        Args:
            option: Configuration option name

        Returns:
            Option value as string, or None if not found
        """
        success, data = await self._send_socket_command("GET_CONFIG", option)
        if success and data:
            return data[0]
        return None

    async def set_config(self, option: str, value: str) -> bool:
        """
        Set a configuration option.

        Args:
            option: Configuration option name
            value: New value

        Returns:
            True if successful
        """
        success, _ = await self._send_socket_command("SET_CONFIG", option, value)
        return success

    async def load_config(self, config_path: str) -> bool:
        """
        Load a configuration file.

        Args:
            config_path: Path to the .uae config file

        Returns:
            True if successful
        """
        success, _ = await self._send_socket_command("LOAD_CONFIG", config_path)
        return success

    async def send_key(self, keycode: int, pressed: bool) -> bool:
        """
        Send a key event.

        Args:
            keycode: Amiga keycode
            pressed: True for key down, False for key up

        Returns:
            True if successful
        """
        state = "1" if pressed else "0"
        success, _ = await self._send_socket_command("SEND_KEY", str(keycode), state)
        return success

    async def read_memory(self, address: int, width: int = 1) -> Optional[int]:
        """
        Read memory from the emulated Amiga.

        Args:
            address: Memory address to read
            width: Number of bytes (1, 2, or 4)

        Returns:
            Value at address, or None on error
        """
        if width not in (1, 2, 4):
            raise ValueError("Width must be 1, 2, or 4")

        # Support hex addresses
        addr_str = f"0x{address:x}" if isinstance(address, int) else str(address)
        success, data = await self._send_socket_command("READ_MEM", addr_str, str(width))
        if success and data:
            return int(data[0])
        return None

    async def write_memory(self, address: int, width: int, value: int) -> bool:
        """
        Write memory to the emulated Amiga.

        Args:
            address: Memory address to write
            width: Number of bytes (1, 2, or 4)
            value: Value to write

        Returns:
            True if successful
        """
        if width not in (1, 2, 4):
            raise ValueError("Width must be 1, 2, or 4")

        addr_str = f"0x{address:x}" if isinstance(address, int) else str(address)
        success, _ = await self._send_socket_command(
            "WRITE_MEM", addr_str, str(width), str(value)
        )
        return success


# Convenience function for quick commands
async def send_ipc_command(command: str, *args: str) -> tuple[bool, list[str]]:
    """
    Send a single IPC command to Amiberry.

    Args:
        command: Command name
        *args: Command arguments

    Returns:
        Tuple of (success, response_data)
    """
    client = AmiberryIPCClient(prefer_dbus=False)  # Use socket for simplicity
    return await client._send_socket_command(command, *args)
