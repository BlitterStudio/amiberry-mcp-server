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
def _get_socket_path(instance: int = 0) -> str:
    """Generate socket path for a given instance number."""
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    base_dir = xdg_runtime if xdg_runtime else "/tmp"

    if instance == 0:
        return os.path.join(base_dir, "amiberry.sock")
    else:
        return os.path.join(base_dir, f"amiberry_{instance}.sock")


def _find_socket_path() -> str:
    """Find the first available Amiberry socket (supports multiple instances)."""
    # Try default socket first
    for instance in range(10):
        path = _get_socket_path(instance)
        if os.path.exists(path):
            return path

    # Fall back to default path even if it doesn't exist
    return _get_socket_path(0)


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

    def __init__(self, prefer_dbus: bool = True, socket_path: Optional[str] = None):
        """
        Initialize the IPC client.

        Args:
            prefer_dbus: If True and on Linux with D-Bus available, prefer D-Bus
                        over Unix sockets. Set to False to always use sockets.
            socket_path: Explicit socket path. If None, auto-discovers the first
                        available Amiberry instance.
        """
        self._prefer_dbus = prefer_dbus and DBUS_AVAILABLE
        self._dbus_conn = None
        self._socket_path = socket_path if socket_path else _find_socket_path()

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

    # === NEW COMMANDS ===

    async def eject_floppy(self, drive: int) -> bool:
        """
        Eject a floppy disk from a drive.

        Args:
            drive: Drive number (0-3 for DF0-DF3)

        Returns:
            True if successful
        """
        if not 0 <= drive <= 3:
            raise ValueError("Drive must be 0-3")
        success, _ = await self._send_socket_command("EJECT_FLOPPY", str(drive))
        return success

    async def eject_cd(self) -> bool:
        """
        Eject the CD.

        Returns:
            True if successful
        """
        success, _ = await self._send_socket_command("EJECT_CD")
        return success

    async def set_volume(self, volume: int) -> bool:
        """
        Set the master volume.

        Args:
            volume: Volume level (0-100)

        Returns:
            True if successful
        """
        if not 0 <= volume <= 100:
            raise ValueError("Volume must be 0-100")
        success, _ = await self._send_socket_command("SET_VOLUME", str(volume))
        return success

    async def get_volume(self) -> Optional[int]:
        """
        Get the current volume.

        Returns:
            Current volume (0-100), or None on error
        """
        success, data = await self._send_socket_command("GET_VOLUME")
        if success and data:
            return int(data[0])
        return None

    async def mute(self) -> bool:
        """
        Mute audio.

        Returns:
            True if successful
        """
        success, _ = await self._send_socket_command("MUTE")
        return success

    async def unmute(self) -> bool:
        """
        Unmute audio.

        Returns:
            True if successful
        """
        success, _ = await self._send_socket_command("UNMUTE")
        return success

    async def toggle_fullscreen(self) -> bool:
        """
        Toggle fullscreen mode.

        Returns:
            True if successful
        """
        success, _ = await self._send_socket_command("TOGGLE_FULLSCREEN")
        return success

    async def set_warp(self, enabled: bool) -> bool:
        """
        Enable or disable warp mode.

        Args:
            enabled: True to enable warp mode

        Returns:
            True if successful
        """
        success, _ = await self._send_socket_command("SET_WARP", "1" if enabled else "0")
        return success

    async def get_warp(self) -> Optional[bool]:
        """
        Get warp mode status.

        Returns:
            True if warp mode is enabled, None on error
        """
        success, data = await self._send_socket_command("GET_WARP")
        if success and data:
            return data[0] == "1"
        return None

    async def get_version(self) -> dict[str, str]:
        """
        Get Amiberry version info.

        Returns:
            Dictionary with version info (version, sdl)
        """
        success, data = await self._send_socket_command("GET_VERSION")
        if not success:
            raise CommandError("Failed to get version")

        info = {}
        for item in data:
            if "=" in item:
                key, value = item.split("=", 1)
                info[key] = value
        return info

    async def list_floppies(self) -> dict[str, str]:
        """
        List all floppy drives and their contents.

        Returns:
            Dictionary with drive names and paths (DF0-DF3)
        """
        success, data = await self._send_socket_command("LIST_FLOPPIES")
        if not success:
            raise CommandError("Failed to list floppies")

        drives = {}
        for item in data:
            if "=" in item:
                key, value = item.split("=", 1)
                drives[key] = value
        return drives

    async def list_configs(self) -> list[str]:
        """
        List available configuration files.

        Returns:
            List of config file names
        """
        success, data = await self._send_socket_command("LIST_CONFIGS")
        if not success:
            return []
        # Filter out the "no configs found" placeholder
        return [c for c in data if c != "<no configs found>"]

    async def frame_advance(self, frames: int = 1) -> bool:
        """
        Advance emulation by a number of frames (when paused).

        Args:
            frames: Number of frames to advance (1-100)

        Returns:
            True if successful
        """
        if not 1 <= frames <= 100:
            raise ValueError("Frames must be 1-100")
        success, _ = await self._send_socket_command("FRAME_ADVANCE", str(frames))
        return success

    async def set_mouse_speed(self, speed: int) -> bool:
        """
        Set mouse sensitivity.

        Args:
            speed: Mouse speed (10-200)

        Returns:
            True if successful
        """
        if not 10 <= speed <= 200:
            raise ValueError("Speed must be 10-200")
        success, _ = await self._send_socket_command("SET_MOUSE_SPEED", str(speed))
        return success

    async def send_mouse(self, dx: int, dy: int, buttons: int = 0) -> bool:
        """
        Send mouse input.

        Args:
            dx: X movement delta
            dy: Y movement delta
            buttons: Button mask (bit 0=left, bit 1=right, bit 2=middle)

        Returns:
            True if successful
        """
        success, _ = await self._send_socket_command(
            "SEND_MOUSE", str(dx), str(dy), str(buttons)
        )
        return success

    async def ping(self) -> bool:
        """
        Test the IPC connection.

        Returns:
            True if connection is working
        """
        success, data = await self._send_socket_command("PING")
        return success and data and data[0] == "PONG"

    async def help(self) -> list[str]:
        """
        Get list of available commands from the server.

        Returns:
            List of help strings
        """
        success, data = await self._send_socket_command("HELP")
        return data if success else []


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
