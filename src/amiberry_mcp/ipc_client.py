"""
IPC client for Amiberry runtime control.

Supports:
- Unix domain sockets (cross-platform: Linux, macOS, FreeBSD)
- D-Bus (Linux only, when available)

The client automatically chooses the best available transport.
"""

import asyncio
import os
import sys
from typing import Any

# Check for D-Bus support (Linux only)
DBUS_AVAILABLE = False
if sys.platform == "linux":
    try:
        from jeepney import DBusAddress, new_method_call
        from jeepney.io.asyncio import open_dbus_connection

        DBUS_AVAILABLE = True
    except ImportError:
        pass


# Socket paths - cache base directory
_SOCKET_BASE_DIR = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"


def _get_socket_path(instance: int = 0) -> str:
    """Generate socket path for a given instance number."""
    if instance == 0:
        return os.path.join(_SOCKET_BASE_DIR, "amiberry.sock")
    else:
        return os.path.join(_SOCKET_BASE_DIR, f"amiberry_{instance}.sock")


def _find_socket_path() -> str:
    """Find the first available Amiberry socket (supports multiple instances)."""
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


class IPCConnectionError(IPCError):
    """Failed to connect to Amiberry."""

    pass


class CommandError(IPCError):
    """Command execution failed."""

    pass


def _safe_int(value: str, default: int = 0) -> int:
    """Convert a string to int, returning default on failure."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _parse_kv_response(data: list[str], coerce_bools: bool = False) -> dict[str, Any]:
    """Parse a list of 'key=value' strings into a dictionary.

    Args:
        data: List of tab-delimited response items from IPC.
        coerce_bools: If True, convert "true"/"false" strings to bool values.
    """
    result: dict[str, Any] = {}
    for item in data:
        if "=" in item:
            key, value = item.split("=", 1)
            if coerce_bools:
                if value.lower() == "true":
                    value = True
                elif value.lower() == "false":
                    value = False
            result[key] = value
    return result


# Mode lookup tables (avoid recreating per call)
_SCALING_MODE_MAP = {"auto": -1, "nearest": 0, "linear": 1, "integer": 2}
_LINE_MODE_MAP = {"single": 0, "none": 0, "double": 1, "doubled": 1, "scanlines": 2}
_RESOLUTION_MODE_MAP = {
    "lores": 0,
    "low": 0,
    "hires": 1,
    "high": 1,
    "superhires": 2,
    "super": 2,
}

# Amiga keyboard scancode mapping
# Maps friendly key names to Amiga hardware scancodes (0x00-0x67)
AMIGA_KEY_MAP: dict[str, int] = {
    # Row 0 - number row
    "backquote": 0x00, "tilde": 0x00, "grave": 0x00,
    "1": 0x01, "2": 0x02, "3": 0x03, "4": 0x04, "5": 0x05,
    "6": 0x06, "7": 0x07, "8": 0x08, "9": 0x09, "0": 0x0A,
    "minus": 0x0B, "equals": 0x0C, "backslash": 0x0D,
    # Row 1 - QWERTY row
    "q": 0x10, "w": 0x11, "e": 0x12, "r": 0x13, "t": 0x14,
    "y": 0x15, "u": 0x16, "i": 0x17, "o": 0x18, "p": 0x19,
    "leftbracket": 0x1A, "rightbracket": 0x1B,
    # Row 2 - ASDF row
    "a": 0x20, "s": 0x21, "d": 0x22, "f": 0x23, "g": 0x24,
    "h": 0x25, "j": 0x26, "k": 0x27, "l": 0x28,
    "semicolon": 0x29, "quote": 0x2A, "hash": 0x2B,
    # Row 3 - ZXCV row
    "lessthan": 0x30,
    "z": 0x31, "x": 0x32, "c": 0x33, "v": 0x34, "b": 0x35,
    "n": 0x36, "m": 0x37,
    "comma": 0x38, "period": 0x39, "slash": 0x3A,
    # Special keys
    "space": 0x40, "backspace": 0x41, "tab": 0x42,
    "numpad_enter": 0x43, "return": 0x44, "enter": 0x44,
    "escape": 0x45, "esc": 0x45, "delete": 0x46, "del": 0x46,
    # Numpad
    "numpad_0": 0x0F,
    "numpad_1": 0x1D, "numpad_2": 0x1E, "numpad_3": 0x1F,
    "numpad_4": 0x2D, "numpad_5": 0x2E, "numpad_6": 0x2F,
    "numpad_7": 0x3D, "numpad_8": 0x3E, "numpad_9": 0x3F,
    "numpad_period": 0x3C, "numpad_minus": 0x4A,
    "numpad_lparen": 0x5A, "numpad_rparen": 0x5B,
    "numpad_divide": 0x5C, "numpad_multiply": 0x5D,
    "numpad_plus": 0x5E,
    # Cursor keys
    "up": 0x4C, "down": 0x4D, "right": 0x4E, "left": 0x4F,
    # Function keys
    "f1": 0x50, "f2": 0x51, "f3": 0x52, "f4": 0x53, "f5": 0x54,
    "f6": 0x55, "f7": 0x56, "f8": 0x57, "f9": 0x58, "f10": 0x59,
    # Modifier keys
    "left_shift": 0x60, "lshift": 0x60,
    "right_shift": 0x61, "rshift": 0x61,
    "caps_lock": 0x62, "capslock": 0x62,
    "ctrl": 0x63, "control": 0x63,
    "left_alt": 0x64, "lalt": 0x64, "alt": 0x64,
    "right_alt": 0x65, "ralt": 0x65,
    "left_amiga": 0x66, "lamiga": 0x66,
    "right_amiga": 0x67, "ramiga": 0x67,
    "help": 0x5F,
}

# Character to (keycode, needs_shift) mapping for type_text support
# Maps printable ASCII characters to their Amiga key + shift state
_CHAR_TO_KEY: dict[str, tuple[int, bool]] = {
    # Lowercase letters (no shift)
    "a": (0x20, False), "b": (0x35, False), "c": (0x33, False),
    "d": (0x22, False), "e": (0x12, False), "f": (0x23, False),
    "g": (0x24, False), "h": (0x25, False), "i": (0x17, False),
    "j": (0x26, False), "k": (0x27, False), "l": (0x28, False),
    "m": (0x37, False), "n": (0x36, False), "o": (0x18, False),
    "p": (0x19, False), "q": (0x10, False), "r": (0x13, False),
    "s": (0x21, False), "t": (0x14, False), "u": (0x16, False),
    "v": (0x34, False), "w": (0x11, False), "x": (0x32, False),
    "y": (0x15, False), "z": (0x31, False),
    # Uppercase letters (shift)
    "A": (0x20, True), "B": (0x35, True), "C": (0x33, True),
    "D": (0x22, True), "E": (0x12, True), "F": (0x23, True),
    "G": (0x24, True), "H": (0x25, True), "I": (0x17, True),
    "J": (0x26, True), "K": (0x27, True), "L": (0x28, True),
    "M": (0x37, True), "N": (0x36, True), "O": (0x18, True),
    "P": (0x19, True), "Q": (0x10, True), "R": (0x13, True),
    "S": (0x21, True), "T": (0x14, True), "U": (0x16, True),
    "V": (0x34, True), "W": (0x11, True), "X": (0x32, True),
    "Y": (0x15, True), "Z": (0x31, True),
    # Numbers (no shift)
    "1": (0x01, False), "2": (0x02, False), "3": (0x03, False),
    "4": (0x04, False), "5": (0x05, False), "6": (0x06, False),
    "7": (0x07, False), "8": (0x08, False), "9": (0x09, False),
    "0": (0x0A, False),
    # Shifted number row symbols (US layout)
    "!": (0x01, True), "@": (0x02, True), "#": (0x03, True),
    "$": (0x04, True), "%": (0x05, True), "^": (0x06, True),
    "&": (0x07, True), "*": (0x08, True), "(": (0x09, True),
    ")": (0x0A, True),
    # Punctuation (no shift)
    "-": (0x0B, False), "=": (0x0C, False), "\\": (0x0D, False),
    "[": (0x1A, False), "]": (0x1B, False),
    ";": (0x29, False), "'": (0x2A, False),
    ",": (0x38, False), ".": (0x39, False), "/": (0x3A, False),
    "`": (0x00, False),
    # Shifted punctuation
    "_": (0x0B, True), "+": (0x0C, True), "|": (0x0D, True),
    "{": (0x1A, True), "}": (0x1B, True),
    ":": (0x29, True), "\"": (0x2A, True),
    "<": (0x38, True), ">": (0x39, True), "?": (0x3A, True),
    "~": (0x00, True),
    # Whitespace
    " ": (0x40, False),
    "\n": (0x44, False),
    "\t": (0x42, False),
}


def resolve_key_name(key: str) -> int:
    """
    Resolve a key name to its Amiga scancode.

    Accepts friendly names (e.g. 'space', 'return', 'f1', 'a') or
    numeric codes as strings (e.g. '0x44', '68').

    Args:
        key: Key name or numeric code string.

    Returns:
        Amiga scancode (0-127).

    Raises:
        ValueError: If the key name is not recognized.
    """
    normalized = key.strip().lower()

    # Try as a friendly name first
    if normalized in AMIGA_KEY_MAP:
        return AMIGA_KEY_MAP[normalized]

    # Try as a numeric value (decimal or hex)
    try:
        code = int(normalized, 0)
        if 0 <= code <= 127:
            return code
        raise ValueError(f"Keycode {code} out of range (0-127)")
    except ValueError as e:
        if "out of range" in str(e):
            raise
        pass

    raise ValueError(
        f"Unknown key: '{key}'. Use a name (e.g. 'space', 'return', 'f1') "
        f"or a numeric code (0-127). Available names: "
        f"{', '.join(sorted(set(AMIGA_KEY_MAP.keys())))}"
    )


class AmiberryIPCClient:
    """
    Async IPC client for Amiberry runtime control.

    Supports Unix sockets (cross-platform) and D-Bus (Linux).
    Automatically selects the best available transport.
    """

    def __init__(
        self,
        prefer_dbus: bool = True,
        socket_path: str | None = None,
        instance: int | None = None,
    ):
        """
        Initialize the IPC client.

        Args:
            prefer_dbus: If True and on Linux with D-Bus available, prefer D-Bus
                        over Unix sockets. Set to False to always use sockets.
            socket_path: Explicit socket path. If None, auto-discovers the first
                        available Amiberry instance.
            instance: Specific instance number to connect to. Overrides auto-discovery.
        """
        self._prefer_dbus = prefer_dbus and DBUS_AVAILABLE
        self._instance = instance

        if socket_path:
            self._socket_path = socket_path
        elif instance is not None:
            self._socket_path = _get_socket_path(instance)
        else:
            self._socket_path = _find_socket_path()

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
            raise IPCConnectionError(
                f"Socket not found at {socket_path}. Is Amiberry running with USE_IPC_SOCKET?"
            )

        # Build message: COMMAND\tARG1\tARG2...\n
        # Sanitize arguments to prevent protocol injection via tab/newline
        sanitized_args = [
            str(a).replace("\t", "").replace("\n", "").replace("\r", "")
            for a in args
        ]
        parts = [command.upper()] + sanitized_args
        message = "\t".join(parts) + "\n"

        writer = None
        try:
            # Create socket and connect
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(socket_path), timeout=timeout
            )

            # Send command
            writer.write(message.encode("utf-8"))
            await writer.drain()

            # Read response (limit to 1MB to prevent memory exhaustion)
            _MAX_RESPONSE_SIZE = 1024 * 1024
            response = await asyncio.wait_for(
                reader.readline(), timeout=timeout
            )
            if len(response) > _MAX_RESPONSE_SIZE:
                response = response[:_MAX_RESPONSE_SIZE]

            # Parse response
            response_str = response.decode("utf-8").rstrip("\n\r")
            if not response_str:
                return False, ["Empty response"]

            parts = response_str.split("\t")
            success = parts[0] == "OK"
            data = parts[1:] if len(parts) > 1 else []

            return success, data

        except asyncio.TimeoutError as e:
            raise IPCConnectionError(f"Connection to {socket_path} timed out") from e
        except FileNotFoundError as e:
            raise IPCConnectionError(f"Socket not found: {socket_path}") from e
        except ConnectionRefusedError as e:
            raise IPCConnectionError(f"Connection refused to {socket_path}") from e
        except Exception as e:
            raise IPCConnectionError(f"Socket error: {e}") from e
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

    async def _send_dbus_command(
        self, method: str, *args: Any, timeout: float = 5.0
    ) -> tuple[bool, list[str]]:
        """Send a command over D-Bus and return the response."""
        if not DBUS_AVAILABLE:
            raise IPCConnectionError("D-Bus support not available")

        try:
            async with open_dbus_connection(bus="SESSION") as conn:
                addr = DBusAddress(
                    DBUS_PATH, bus_name=DBUS_INTERFACE, interface=DBUS_INTERFACE
                )

                # Build the method call
                msg = new_method_call(addr, method)
                if args:
                    msg.body = args

                # Send and wait for reply
                reply = await asyncio.wait_for(
                    conn.send_and_get_reply(msg), timeout=timeout
                )

                # Parse reply - D-Bus returns boolean success
                if reply.body and len(reply.body) > 0:
                    success = bool(reply.body[0])
                    data = list(reply.body[1:]) if len(reply.body) > 1 else []
                    return success, [str(d) for d in data]
                return True, []

        except asyncio.TimeoutError as e:
            raise IPCConnectionError("D-Bus call timed out") from e
        except Exception as e:
            raise IPCConnectionError(f"D-Bus error: {e}") from e

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
        success, _ = await self._send_command("PAUSE")
        return success

    async def resume(self) -> bool:
        """Resume emulation."""
        success, _ = await self._send_command("RESUME")
        return success

    async def reset(self, hard: bool = False) -> bool:
        """
        Reset emulation.

        Args:
            hard: If True, perform a hard reset. Otherwise soft/keyboard reset.
        """
        reset_type = "HARD" if hard else "SOFT"
        success, _ = await self._send_command("RESET", reset_type)
        return success

    async def quit(self) -> bool:
        """Quit Amiberry."""
        success, _ = await self._send_command("QUIT")
        return success

    async def screenshot(self, filename: str) -> bool:
        """
        Take a screenshot.

        Args:
            filename: Path where the screenshot should be saved.
        """
        success, _ = await self._send_command("SCREENSHOT", filename)
        return success

    async def save_state(self, state_file: str, config_file: str) -> bool:
        """
        Save emulation state.

        Args:
            state_file: Path for the savestate file (.uss)
            config_file: Path for the config file (.uae)
        """
        success, _ = await self._send_command("SAVESTATE", state_file, config_file)
        return success

    async def load_state(self, state_file: str) -> bool:
        """
        Load emulation state.

        Args:
            state_file: Path to the savestate file (.uss)
        """
        success, _ = await self._send_command("LOADSTATE", state_file)
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
        success, _ = await self._send_command("INSERTFLOPPY", image_path, str(drive))
        return success

    async def insert_cd(self, image_path: str) -> bool:
        """
        Insert a CD image.

        Args:
            image_path: Path to the CD image file
        """
        success, _ = await self._send_command("INSERTCD", image_path)
        return success

    async def disk_swap(self, disk_num: int, drive_num: int) -> bool:
        """
        Swap a disk from the disk swapper list into a drive.

        Args:
            disk_num: Index in the disk swapper list
            drive_num: Target drive number (0-3)
        """
        success, _ = await self._send_command("DISKSWAP", str(disk_num), str(drive_num))
        return success

    async def query_disk_swap(self, drive_num: int) -> int:
        """
        Query which disk from the swapper list is in a drive.

        Args:
            drive_num: Drive number to query (0-3)

        Returns:
            Disk index in swapper list, or -1 if not from swapper
        """
        success, data = await self._send_command("QUERYDISKSWAP", str(drive_num))
        if success and data:
            return _safe_int(data[0], -1)
        return -1

    async def get_status(self) -> dict[str, Any]:
        """
        Get current emulation status.

        Returns:
            Dictionary with status information (Paused, Config, Floppy0-3)
        """
        success, data = await self._send_command("GET_STATUS")
        if not success:
            raise CommandError("Failed to get status")

        return _parse_kv_response(data, coerce_bools=True)

    async def get_config(self, option: str) -> str | None:
        """
        Get a configuration option value.

        Args:
            option: Configuration option name

        Returns:
            Option value as string, or None if not found
        """
        success, data = await self._send_command("GET_CONFIG", option)
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
        success, _ = await self._send_command("SET_CONFIG", option, value)
        return success

    async def load_config(self, config_path: str) -> bool:
        """
        Load a configuration file.

        Args:
            config_path: Path to the .uae config file

        Returns:
            True if successful
        """
        success, _ = await self._send_command("LOAD_CONFIG", config_path)
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
        success, _ = await self._send_command("SEND_KEY", str(keycode), state)
        return success

    async def type_text(self, text: str, delay: float = 0.05) -> tuple[int, int]:
        """
        Type a string of text by sending key press/release events.

        Maps each character to its Amiga keycode, handling shift for
        uppercase letters and shifted symbols. Unsupported characters
        are silently skipped.

        Args:
            text: The text to type.
            delay: Delay in seconds between key events (default: 0.05).

        Returns:
            Tuple of (chars_typed, chars_skipped).
        """
        typed = 0
        skipped = 0
        shift_code = AMIGA_KEY_MAP["left_shift"]

        for char in text:
            mapping = _CHAR_TO_KEY.get(char)
            if mapping is None:
                skipped += 1
                continue

            keycode, needs_shift = mapping

            # Press shift if needed
            if needs_shift:
                await self.send_key(shift_code, True)
                await asyncio.sleep(delay)

            # Press and release the key
            await self.send_key(keycode, True)
            await asyncio.sleep(delay)
            await self.send_key(keycode, False)
            await asyncio.sleep(delay)

            # Release shift if it was pressed
            if needs_shift:
                await self.send_key(shift_code, False)
                await asyncio.sleep(delay)

            typed += 1

        return typed, skipped
    async def read_memory(self, address: int, width: int = 1) -> int | None:
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
        success, data = await self._send_command("READ_MEM", addr_str, str(width))
        if success and data:
            try:
                return int(data[0], 0)  # support hex "0x..." responses
            except (ValueError, TypeError):
                return None
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
        success, _ = await self._send_command(
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
        success, _ = await self._send_command("EJECT_FLOPPY", str(drive))
        return success

    async def eject_cd(self) -> bool:
        """
        Eject the CD.

        Returns:
            True if successful
        """
        success, _ = await self._send_command("EJECT_CD")
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
        success, _ = await self._send_command("SET_VOLUME", str(volume))
        return success

    async def get_volume(self) -> int | None:
        """
        Get the current volume.

        Returns:
            Current volume (0-100), or None on error
        """
        success, data = await self._send_command("GET_VOLUME")
        if success and data:
            return _safe_int(data[0])
        return None

    async def mute(self) -> bool:
        """
        Mute audio.

        Returns:
            True if successful
        """
        success, _ = await self._send_command("MUTE")
        return success

    async def unmute(self) -> bool:
        """
        Unmute audio.

        Returns:
            True if successful
        """
        success, _ = await self._send_command("UNMUTE")
        return success

    async def toggle_fullscreen(self) -> bool:
        """
        Toggle fullscreen mode.

        Returns:
            True if successful
        """
        success, _ = await self._send_command("TOGGLE_FULLSCREEN")
        return success

    async def set_warp(self, enabled: bool) -> bool:
        """
        Enable or disable warp mode.

        Args:
            enabled: True to enable warp mode

        Returns:
            True if successful
        """
        success, _ = await self._send_command("SET_WARP", "1" if enabled else "0")
        return success

    async def get_warp(self) -> bool | None:
        """
        Get warp mode status.

        Returns:
            True if warp mode is enabled, None on error
        """
        success, data = await self._send_command("GET_WARP")
        if success and data:
            return data[0] == "1"
        return None

    async def get_version(self) -> dict[str, str]:
        """
        Get Amiberry version info.

        Returns:
            Dictionary with version info (version, sdl)
        """
        success, data = await self._send_command("GET_VERSION")
        if not success:
            raise CommandError("Failed to get version")

        return _parse_kv_response(data)

    async def list_floppies(self) -> dict[str, str]:
        """
        List all floppy drives and their contents.

        Returns:
            Dictionary with drive names and paths (DF0-DF3)
        """
        success, data = await self._send_command("LIST_FLOPPIES")
        if not success:
            raise CommandError("Failed to list floppies")

        return _parse_kv_response(data)

    async def list_configs(self) -> list[str]:
        """
        List available configuration files.

        Returns:
            List of config file names
        """
        success, data = await self._send_command("LIST_CONFIGS")
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
        success, _ = await self._send_command("FRAME_ADVANCE", str(frames))
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
        success, _ = await self._send_command("SET_MOUSE_SPEED", str(speed))
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
        success, _ = await self._send_command(
            "SEND_MOUSE", str(dx), str(dy), str(buttons)
        )
        return success

    async def ping(self) -> bool:
        """
        Test the IPC connection.

        Returns:
            True if connection is working
        """
        success, data = await self._send_command("PING")
        return bool(success and data and data[0] == "PONG")

    async def help(self) -> list[str]:
        """
        Get list of available commands from the server.

        Returns:
            List of help strings
        """
        success, data = await self._send_command("HELP")
        return data if success else []

    # === ROUND 2 COMMANDS ===

    async def quicksave(self, slot: int = 0) -> bool:
        """
        Quick save to a slot.

        Args:
            slot: Slot number (0-9)

        Returns:
            True if successful
        """
        if not 0 <= slot <= 9:
            raise ValueError("Slot must be 0-9")
        success, _ = await self._send_command("QUICKSAVE", str(slot))
        return success

    async def quickload(self, slot: int = 0) -> bool:
        """
        Quick load from a slot.

        Args:
            slot: Slot number (0-9)

        Returns:
            True if successful
        """
        if not 0 <= slot <= 9:
            raise ValueError("Slot must be 0-9")
        success, _ = await self._send_command("QUICKLOAD", str(slot))
        return success

    async def get_joyport_mode(self, port: int) -> tuple[int, str] | None:
        """
        Get joystick port mode.

        Args:
            port: Port number (0-3)

        Returns:
            Tuple of (mode_number, mode_name), or None on error
        """
        if not 0 <= port <= 3:
            raise ValueError("Port must be 0-3")
        success, data = await self._send_command("GET_JOYPORT_MODE", str(port))
        if success and len(data) >= 2:
            return _safe_int(data[0]), data[1]
        return None

    async def set_joyport_mode(self, port: int, mode: int) -> bool:
        """
        Set joystick port mode.

        Args:
            port: Port number (0-3)
            mode: Mode (0=default, 2=mouse, 3=joystick, 4=gamepad, 7=cd32)

        Returns:
            True if successful
        """
        if not 0 <= port <= 3:
            raise ValueError("Port must be 0-3")
        if not 0 <= mode <= 8:
            raise ValueError("Mode must be 0-8")
        success, _ = await self._send_command("SET_JOYPORT_MODE", str(port), str(mode))
        return success

    async def get_autofire(self, port: int) -> int | None:
        """
        Get autofire mode for a port.

        Args:
            port: Port number (0-3)

        Returns:
            Autofire mode, or None on error
        """
        if not 0 <= port <= 3:
            raise ValueError("Port must be 0-3")
        success, data = await self._send_command("GET_AUTOFIRE", str(port))
        if success and data:
            return _safe_int(data[0])
        return None

    async def set_autofire(self, port: int, mode: int) -> bool:
        """
        Set autofire mode for a port.

        Args:
            port: Port number (0-3)
            mode: Autofire mode (0=off, 1=normal, 2=toggle, 3=always)

        Returns:
            True if successful
        """
        if not 0 <= port <= 3:
            raise ValueError("Port must be 0-3")
        if not 0 <= mode <= 4:
            raise ValueError("Mode must be 0-4")
        success, _ = await self._send_command("SET_AUTOFIRE", str(port), str(mode))
        return success

    async def get_led_status(self) -> dict[str, str]:
        """
        Get all LED states.

        Returns:
            Dictionary with LED states (power, df0-df3, hd, cd, caps)
        """
        success, data = await self._send_command("GET_LED_STATUS")
        if not success:
            raise CommandError("Failed to get LED status")

        return _parse_kv_response(data)

    async def list_harddrives(self) -> dict[str, str]:
        """
        List all mounted hard drives.

        Returns:
            Dictionary with unit names and paths
        """
        success, data = await self._send_command("LIST_HARDDRIVES")
        if not success:
            raise CommandError("Failed to list hard drives")

        return _parse_kv_response(data)

    async def set_display_mode(self, mode: int) -> bool:
        """
        Set display mode.

        Args:
            mode: Display mode (0=window, 1=fullscreen, 2=fullwindow)

        Returns:
            True if successful
        """
        if not 0 <= mode <= 2:
            raise ValueError("Mode must be 0-2")
        success, _ = await self._send_command("SET_DISPLAY_MODE", str(mode))
        return success

    async def get_display_mode(self) -> tuple[int, str] | None:
        """
        Get current display mode.

        Returns:
            Tuple of (mode_number, mode_name), or None on error
        """
        success, data = await self._send_command("GET_DISPLAY_MODE")
        if success and len(data) >= 2:
            return _safe_int(data[0]), data[1]
        return None

    async def set_ntsc(self, enabled: bool) -> bool:
        """
        Set NTSC mode.

        Args:
            enabled: True for NTSC, False for PAL

        Returns:
            True if successful
        """
        success, _ = await self._send_command("SET_NTSC", "1" if enabled else "0")
        return success

    async def get_ntsc(self) -> tuple[bool, str] | None:
        """
        Get current video mode (PAL/NTSC).

        Returns:
            Tuple of (is_ntsc, mode_name), or None on error
        """
        success, data = await self._send_command("GET_NTSC")
        if success and len(data) >= 2:
            return data[0] == "1", data[1]
        return None

    async def set_sound_mode(self, mode: int) -> bool:
        """
        Set sound mode.

        Args:
            mode: Sound mode (0=off, 1=normal, 2=stereo, 3=best)

        Returns:
            True if successful
        """
        if not 0 <= mode <= 3:
            raise ValueError("Mode must be 0-3")
        success, _ = await self._send_command("SET_SOUND_MODE", str(mode))
        return success

    async def get_sound_mode(self) -> tuple[int, str] | None:
        """
        Get current sound mode.

        Returns:
            Tuple of (mode_number, mode_name), or None on error
        """
        success, data = await self._send_command("GET_SOUND_MODE")
        if success and len(data) >= 2:
            return _safe_int(data[0]), data[1]
        return None

    # === ROUND 3 COMMANDS ===

    async def toggle_mouse_grab(self) -> bool:
        """
        Toggle mouse capture.

        Returns:
            True if successful
        """
        success, _ = await self._send_command("TOGGLE_MOUSE_GRAB")
        return success

    async def get_mouse_speed(self) -> int | None:
        """
        Get current mouse speed.

        Returns:
            Mouse speed (10-200), or None on error
        """
        success, data = await self._send_command("GET_MOUSE_SPEED")
        if success and data:
            return _safe_int(data[0])
        return None

    async def set_cpu_speed(self, speed: int) -> bool:
        """
        Set CPU speed.

        Args:
            speed: CPU speed (-1=max, 0=cycle-exact, >0=percentage)

        Returns:
            True if successful
        """
        success, _ = await self._send_command("SET_CPU_SPEED", str(speed))
        return success

    async def get_cpu_speed(self) -> tuple[int, str] | None:
        """
        Get current CPU speed.

        Returns:
            Tuple of (speed_value, description), or None on error
        """
        success, data = await self._send_command("GET_CPU_SPEED")
        if success and len(data) >= 2:
            return _safe_int(data[0]), data[1]
        return None

    async def toggle_rtg(self, monid: int = 0) -> str | None:
        """
        Toggle RTG display.

        Args:
            monid: Monitor ID (default 0)

        Returns:
            "RTG" or "Chipset" to indicate current mode, or None on error
        """
        success, data = await self._send_command("TOGGLE_RTG", str(monid))
        if success and data:
            return data[0]
        return None

    async def set_floppy_speed(self, speed: int) -> bool:
        """
        Set floppy drive speed.

        Args:
            speed: Floppy speed (0=turbo, 100=1x, 200=2x, 400=4x, 800=8x)

        Returns:
            True if successful
        """
        if speed not in (0, 100, 200, 400, 800):
            raise ValueError("Speed must be 0, 100, 200, 400, or 800")
        success, _ = await self._send_command("SET_FLOPPY_SPEED", str(speed))
        return success

    async def get_floppy_speed(self) -> tuple[int, str] | None:
        """
        Get current floppy speed.

        Returns:
            Tuple of (speed_value, description), or None on error
        """
        success, data = await self._send_command("GET_FLOPPY_SPEED")
        if success and len(data) >= 2:
            return _safe_int(data[0]), data[1]
        return None

    async def disk_write_protect(self, drive: int, protect: bool) -> bool:
        """
        Set write protection on a floppy disk.

        Args:
            drive: Drive number (0-3)
            protect: True to protect, False to allow writes

        Returns:
            True if successful
        """
        if not 0 <= drive <= 3:
            raise ValueError("Drive must be 0-3")
        success, _ = await self._send_command(
            "DISK_WRITE_PROTECT", str(drive), "1" if protect else "0"
        )
        return success

    async def get_disk_write_protect(self, drive: int) -> tuple[bool, str] | None:
        """
        Get write protection status for a floppy disk.

        Args:
            drive: Drive number (0-3)

        Returns:
            Tuple of (is_protected, status_string), or None on error
        """
        if not 0 <= drive <= 3:
            raise ValueError("Drive must be 0-3")
        success, data = await self._send_command("GET_DISK_WRITE_PROTECT", str(drive))
        if success and len(data) >= 2:
            return data[0] == "1", data[1]
        return None

    async def toggle_status_line(self) -> tuple[int, str] | None:
        """
        Toggle status line display.

        Returns:
            Tuple of (mode, mode_name), or None on error
        """
        success, data = await self._send_command("TOGGLE_STATUS_LINE")
        if success and len(data) >= 2:
            return _safe_int(data[0]), data[1]
        return None

    async def set_chipset(self, chipset: str) -> bool:
        """
        Set chipset.

        Args:
            chipset: Chipset name (OCS, ECS_AGNUS, ECS_DENISE, ECS, AGA)

        Returns:
            True if successful
        """
        valid = (
            "OCS",
            "ECS_AGNUS",
            "ECS_DENISE",
            "ECS",
            "AGA",
            "0",
            "1",
            "2",
            "3",
            "4",
        )
        if chipset.upper() not in valid:
            raise ValueError(f"Chipset must be one of: {valid}")
        success, _ = await self._send_command("SET_CHIPSET", chipset.upper())
        return success

    async def get_chipset(self) -> tuple[int, str] | None:
        """
        Get current chipset.

        Returns:
            Tuple of (mask_value, chipset_name), or None on error
        """
        success, data = await self._send_command("GET_CHIPSET")
        if success and len(data) >= 2:
            return _safe_int(data[0]), data[1]
        return None

    async def get_memory_config(self) -> dict[str, str]:
        """
        Get memory configuration.

        Returns:
            Dictionary with memory sizes (chip, fast, bogo, z3, rtg)
        """
        success, data = await self._send_command("GET_MEMORY_CONFIG")
        if not success:
            raise CommandError("Failed to get memory config")

        return _parse_kv_response(data)

    async def get_fps(self) -> dict[str, str]:
        """
        Get current frame rate and performance info.

        Returns:
            Dictionary with fps, idle, lines, lace
        """
        success, data = await self._send_command("GET_FPS")
        if not success:
            raise CommandError("Failed to get FPS")

        return _parse_kv_response(data)

    # === ROUND 4 COMMANDS - Memory and Window Control ===

    async def set_chip_mem(self, size_kb: int) -> bool:
        """
        Set Chip RAM size.

        Args:
            size_kb: Size in KB (256, 512, 1024, 2048, 4096, 8192)

        Returns:
            True if successful
        """
        valid_sizes = (256, 512, 1024, 2048, 4096, 8192)
        if size_kb not in valid_sizes:
            raise ValueError(f"Size must be one of: {valid_sizes}")
        success, _ = await self._send_command("SET_CHIP_MEM", str(size_kb))
        return success

    async def set_fast_mem(self, size_kb: int) -> bool:
        """
        Set Fast RAM size.

        Args:
            size_kb: Size in KB (0, 64, 128, 256, 512, 1024, 2048, 4096, 8192)

        Returns:
            True if successful
        """
        valid_sizes = (0, 64, 128, 256, 512, 1024, 2048, 4096, 8192)
        if size_kb not in valid_sizes:
            raise ValueError(f"Size must be one of: {valid_sizes}")
        success, _ = await self._send_command("SET_FAST_MEM", str(size_kb))
        return success

    async def set_slow_mem(self, size_kb: int) -> bool:
        """
        Set Slow RAM (Bogo) size.

        Args:
            size_kb: Size in KB (0, 256, 512, 1024, 1536, 1792)

        Returns:
            True if successful
        """
        valid_sizes = (0, 256, 512, 1024, 1536, 1792)
        if size_kb not in valid_sizes:
            raise ValueError(f"Size must be one of: {valid_sizes}")
        success, _ = await self._send_command("SET_SLOW_MEM", str(size_kb))
        return success

    async def set_z3_mem(self, size_mb: int) -> bool:
        """
        Set Zorro III Fast RAM size.

        Args:
            size_mb: Size in MB (0, 1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024)

        Returns:
            True if successful
        """
        valid_sizes = (0, 1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024)
        if size_mb not in valid_sizes:
            raise ValueError(f"Size must be one of: {valid_sizes}")
        success, _ = await self._send_command("SET_Z3_MEM", str(size_mb))
        return success

    async def get_cpu_model(self) -> dict[str, str]:
        """
        Get CPU model information.

        Returns:
            Dictionary with model, name, fpu, 24bit, compatible, cycle_exact
        """
        success, data = await self._send_command("GET_CPU_MODEL")
        if not success:
            raise CommandError("Failed to get CPU model")

        return _parse_kv_response(data)

    async def set_cpu_model(self, model: str | int) -> bool:
        """
        Set CPU model.

        Args:
            model: CPU model (68000, 68010, 68020, 68030, 68040, 68060)

        Returns:
            True if successful
        """
        model_str = str(model)
        valid_models = (
            "68000",
            "68010",
            "68020",
            "68030",
            "68040",
            "68060",
            "0",
            "10",
            "20",
            "30",
            "40",
            "60",
        )
        if model_str not in valid_models:
            raise ValueError(
                "Model must be one of: 68000, 68010, 68020, 68030, 68040, 68060"
            )
        success, _ = await self._send_command("SET_CPU_MODEL", model_str)
        return success

    async def set_window_size(self, width: int, height: int) -> bool:
        """
        Set window size.

        Args:
            width: Window width (320-3840)
            height: Window height (200-2160)

        Returns:
            True if successful
        """
        if not 320 <= width <= 3840 or not 200 <= height <= 2160:
            raise ValueError("Size must be between 320x200 and 3840x2160")
        success, _ = await self._send_command(
            "SET_WINDOW_SIZE", str(width), str(height)
        )
        return success

    async def get_window_size(self) -> dict[str, int]:
        """
        Get current window size.

        Returns:
            Dictionary with width and height
        """
        success, data = await self._send_command("GET_WINDOW_SIZE")
        if not success:
            raise CommandError("Failed to get window size")

        raw = _parse_kv_response(data)
        return {k: _safe_int(v) for k, v in raw.items()}

    async def set_scaling(self, mode: int | str) -> bool:
        """
        Set scaling mode.

        Args:
            mode: Scaling mode (-1=auto, 0=nearest, 1=linear, 2=integer) or string name

        Returns:
            True if successful
        """
        if isinstance(mode, str):
            resolved = _SCALING_MODE_MAP.get(mode.lower())
            if resolved is None:
                raise ValueError(
                    f"Unknown scaling mode: '{mode}'. "
                    f"Valid: {list(_SCALING_MODE_MAP.keys())}"
                )
            mode = resolved
        if not -1 <= mode <= 2:
            raise ValueError("Mode must be -1..2 (auto, nearest, linear, integer)")
        success, _ = await self._send_command("SET_SCALING", str(mode))
        return success

    async def get_scaling(self) -> dict[str, str]:
        """
        Get current scaling mode.

        Returns:
            Dictionary with method, method_name
        """
        success, data = await self._send_command("GET_SCALING")
        if not success:
            raise CommandError("Failed to get scaling")

        return _parse_kv_response(data)

    async def set_line_mode(self, mode: int | str) -> bool:
        """
        Set line mode (doubling/scanlines).

        Args:
            mode: Line mode (0=single, 1=double, 2=scanlines) or string name

        Returns:
            True if successful
        """
        if isinstance(mode, str):
            resolved = _LINE_MODE_MAP.get(mode.lower())
            if resolved is None:
                raise ValueError(
                    f"Unknown line mode: '{mode}'. Valid: {list(_LINE_MODE_MAP.keys())}"
                )
            mode = resolved
        if not 0 <= mode <= 2:
            raise ValueError("Mode must be 0-2 (single, double, scanlines)")
        success, _ = await self._send_command("SET_LINE_MODE", str(mode))
        return success

    async def get_line_mode(self) -> dict[str, str]:
        """
        Get current line mode.

        Returns:
            Dictionary with mode, name, vresolution, pscanlines, iscanlines
        """
        success, data = await self._send_command("GET_LINE_MODE")
        if not success:
            raise CommandError("Failed to get line mode")

        return _parse_kv_response(data)

    async def set_resolution(self, mode: int | str) -> bool:
        """
        Set display resolution.

        Args:
            mode: Resolution (0=lores, 1=hires, 2=superhires) or string name

        Returns:
            True if successful
        """
        if isinstance(mode, str):
            resolved = _RESOLUTION_MODE_MAP.get(mode.lower())
            if resolved is None:
                raise ValueError(
                    f"Unknown resolution mode: '{mode}'. "
                    f"Valid: {list(_RESOLUTION_MODE_MAP.keys())}"
                )
            mode = resolved
        if not 0 <= mode <= 2:
            raise ValueError("Mode must be 0-2 (lores, hires, superhires)")
        success, _ = await self._send_command("SET_RESOLUTION", str(mode))
        return success

    async def get_resolution(self) -> tuple[int, str] | None:
        """
        Get current resolution.

        Returns:
            Tuple of (mode_number, mode_name), or None on error
        """
        success, data = await self._send_command("GET_RESOLUTION")
        if success and len(data) >= 2:
            return _safe_int(data[0]), data[1]
        return None

    # === ROUND 5 COMMANDS - Autocrop and WHDLoad ===

    async def set_autocrop(self, enabled: bool) -> bool:
        """
        Enable or disable automatic display cropping.

        Args:
            enabled: True to enable autocrop, False to disable

        Returns:
            True if successful
        """
        success, _ = await self._send_command("SET_AUTOCROP", "1" if enabled else "0")
        return success

    async def get_autocrop(self) -> bool | None:
        """
        Get current autocrop status.

        Returns:
            True if enabled, False if disabled, None on error
        """
        success, data = await self._send_command("GET_AUTOCROP")
        if success and len(data) >= 1:
            return data[0] == "1"
        return None

    async def insert_whdload(self, path: str) -> bool:
        """
        Load a WHDLoad game from an LHA archive or directory.

        Args:
            path: Path to the LHA archive or WHDLoad game directory

        Returns:
            True if successful
        """
        success, _ = await self._send_command("INSERT_WHDLOAD", path)
        return success

    async def eject_whdload(self) -> bool:
        """
        Eject the currently loaded WHDLoad game.

        Returns:
            True if successful
        """
        success, _ = await self._send_command("EJECT_WHDLOAD")
        return success

    async def get_whdload(self) -> dict[str, str] | None:
        """
        Get information about the currently loaded WHDLoad game.

        Returns:
            Dictionary with loaded, filename, game_name, sub_path, slave, slave_count
            or None on error
        """
        success, data = await self._send_command("GET_WHDLOAD")
        if not success:
            return None

        return _parse_kv_response(data)

    # === ROUND 6 COMMANDS - Debugging and Diagnostics ===

    async def debug_activate(self) -> bool:
        """
        Activate the built-in debugger.

        Returns:
            True if successful
        """
        success, _ = await self._send_command("DEBUG_ACTIVATE")
        return success

    async def debug_deactivate(self) -> bool:
        """
        Deactivate the debugger and resume emulation.

        Returns:
            True if successful
        """
        success, _ = await self._send_command("DEBUG_DEACTIVATE")
        return success

    async def debug_status(self) -> dict[str, str] | None:
        """
        Get debugger status.

        Returns:
            Dictionary with active status, or None on error
        """
        success, data = await self._send_command("DEBUG_STATUS")
        if not success:
            return None

        return _parse_kv_response(data)

    async def debug_step(self, count: int = 1) -> bool:
        """
        Single-step CPU instructions.

        Args:
            count: Number of instructions to step (default: 1)

        Returns:
            True if successful
        """
        success, _ = await self._send_command("DEBUG_STEP", str(count))
        return success

    async def debug_step_over(self) -> bool:
        """
        Step over subroutine calls (execute JSR/BSR as a single step).

        Returns:
            True if successful
        """
        success, _ = await self._send_command("DEBUG_STEP_OVER")
        return success

    async def debug_continue(self) -> bool:
        """
        Continue execution until next breakpoint.

        Returns:
            True if successful
        """
        success, _ = await self._send_command("DEBUG_CONTINUE")
        return success

    async def get_cpu_regs(self) -> dict[str, str] | None:
        """
        Get all CPU registers.

        Returns:
            Dictionary with D0-D7, A0-A7, PC, SR, flags, USP, ISP
            or None on error
        """
        success, data = await self._send_command("GET_CPU_REGS")
        if not success:
            return None

        return _parse_kv_response(data)

    async def get_custom_regs(self) -> dict[str, str] | None:
        """
        Get key custom chip registers.

        Returns:
            Dictionary with DMACON, INTENA, INTREQ, etc.
            or None on error
        """
        success, data = await self._send_command("GET_CUSTOM_REGS")
        if not success:
            return None

        return _parse_kv_response(data)

    async def disassemble(self, address: int | str, count: int = 10) -> list[str]:
        """
        Disassemble instructions at an address.

        Args:
            address: Memory address (hex string or integer)
            count: Number of instructions to disassemble (default: 10)

        Returns:
            List of disassembly lines
        """
        if isinstance(address, int):
            addr_str = f"0x{address:x}"
        else:
            addr_str = str(address)

        success, data = await self._send_command("DISASSEMBLE", addr_str, str(count))
        if success:
            return data
        return []

    async def set_breakpoint(self, address: int | str) -> bool:
        """
        Set a breakpoint at an address.

        Args:
            address: Memory address (hex string or integer)

        Returns:
            True if successful
        """
        if isinstance(address, int):
            addr_str = f"0x{address:x}"
        else:
            addr_str = str(address)

        success, _ = await self._send_command("SET_BREAKPOINT", addr_str)
        return success

    async def clear_breakpoint(self, address: int | str | None = None) -> bool:
        """
        Clear a breakpoint at an address or all breakpoints.

        Args:
            address: Memory address (hex string or integer), or None/ALL to clear all

        Returns:
            True if successful
        """
        if address is None or str(address).upper() == "ALL":
            addr_str = "ALL"
        elif isinstance(address, int):
            addr_str = f"0x{address:x}"
        else:
            addr_str = str(address)

        success, _ = await self._send_command("CLEAR_BREAKPOINT", addr_str)
        return success

    async def list_breakpoints(self) -> list[str]:
        """
        List all active breakpoints.

        Returns:
            List of breakpoint addresses
        """
        success, data = await self._send_command("LIST_BREAKPOINTS")
        if success:
            return data
        return []

    async def get_copper_state(self) -> dict[str, str] | None:
        """
        Get Copper coprocessor state.

        Returns:
            Dictionary with Copper addresses and status, or None on error
        """
        success, data = await self._send_command("GET_COPPER_STATE")
        if not success:
            return None

        return _parse_kv_response(data)

    async def get_blitter_state(self) -> dict[str, str] | None:
        """
        Get Blitter state.

        Returns:
            Dictionary with Blitter status, channels, dimensions, addresses
            or None on error
        """
        success, data = await self._send_command("GET_BLITTER_STATE")
        if not success:
            return None

        return _parse_kv_response(data)

    async def get_drive_state(self, drive: int | None = None) -> dict[str, str] | None:
        """
        Get floppy drive state.

        Args:
            drive: Drive number (0-3), or None for all drives

        Returns:
            Dictionary with drive state (track, side, motor, disk inserted)
            or None on error
        """
        if drive is not None:
            if not 0 <= drive <= 3:
                raise ValueError("Drive must be 0-3")
            success, data = await self._send_command("GET_DRIVE_STATE", str(drive))
        else:
            success, data = await self._send_command("GET_DRIVE_STATE")

        if not success:
            return None

        return _parse_kv_response(data)

    async def get_audio_state(self) -> dict[str, str] | None:
        """
        Get audio channel states.

        Returns:
            Dictionary with audio channel status (volume, period, enabled)
            or None on error
        """
        success, data = await self._send_command("GET_AUDIO_STATE")
        if not success:
            return None

        return _parse_kv_response(data)

    async def get_dma_state(self) -> dict[str, str] | None:
        """
        Get DMA channel states.

        Returns:
            Dictionary with DMA channel status (bitplane, sprite, audio, disk, copper, blitter)
            or None on error
        """
        success, data = await self._send_command("GET_DMA_STATE")
        if not success:
            return None

        return _parse_kv_response(data)


# Convenience function for quick commands
async def send_ipc_command(
    command: str,
    *args: str,
    client: AmiberryIPCClient | None = None,
) -> tuple[bool, list[str]]:
    """
    Send a single IPC command to Amiberry.

    Args:
        command: Command name
        *args: Command arguments
        client: Optional existing client to reuse (avoids reconnecting)

    Returns:
        Tuple of (success, response_data)
    """
    if client is None:
        client = AmiberryIPCClient(prefer_dbus=False)
    return await client._send_command(command, *args)
