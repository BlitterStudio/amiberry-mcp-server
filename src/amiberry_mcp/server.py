#!/usr/bin/env python3
"""
MCP Server for Amiberry emulator control.
Enables Claude AI to interact with Amiberry through the Model Context Protocol.
"""

import asyncio
import base64
import datetime
import json
import re
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import mcp.server.stdio
from mcp.server import Server
from mcp.types import TextContent, Tool

try:
    from mcp.types import ImageContent

    _HAS_IMAGE_CONTENT = True
except ImportError:
    _HAS_IMAGE_CONTENT = False

from .common import (
    build_launch_command,
    detect_amiberry_version,
    format_log_timestamp,
    format_signal_info,
    launch_process,
    normalize_log_path,
    scan_disk_images,
    terminate_process,
)
from .common import (
    find_config_path as _find_config_path,
)
from .config import (
    AMIBERRY_HOME,
    CD_EXTENSIONS,
    CONFIG_DIR,
    DISK_IMAGE_DIRS,
    IS_LINUX,
    IS_MACOS,
    LOG_DIR,
    ROM_DIR,
    SAVESTATE_DIR,
    SCREENSHOT_DIR,
    SUPPORTED_MODELS,
    SYSTEM_CONFIG_DIR,
    get_platform_info,
)
from .ipc_client import (
    AmiberryIPCClient,
    IPCConnectionError,
    resolve_key_name,
)
from .rom_manager import (
    get_rom_summary,
    identify_rom,
    scan_rom_directory,
)
from .savestate import (
    get_savestate_summary,
    inspect_savestate,
)
from .uae_config import (
    create_config_from_template,
    get_config_summary,
    modify_uae_config,
    parse_uae_config,
)


@dataclass
class _ProcessState:
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


_state = _ProcessState()

app = Server("amiberry-emulator")


def _get_ipc_client() -> AmiberryIPCClient:
    """Get an IPC client for the active instance, reusing cached clients."""
    if (
        _state.ipc_client_cache is not None
        and _state.ipc_client_cache[0] == _state.active_instance
    ):
        return _state.ipc_client_cache[1]
    client = AmiberryIPCClient(prefer_dbus=False, instance=_state.active_instance)
    _state.ipc_client_cache = (_state.active_instance, client)
    return client


def _text_result(msg: str) -> list[TextContent]:
    """Wrap a string in the MCP TextContent return format."""
    return [TextContent(type="text", text=msg)]


def _launch_and_store(
    cmd: list[str],
    log_path: Path | None = None,
) -> subprocess.Popen:
    """Launch Amiberry, store state, and return the process.

    Centralizes the close-log → launch → store-state pattern used by
    all launch handlers.
    """
    _state.close_log_handle()
    proc, log_handle = launch_process(cmd, log_path=log_path)
    _state.process = proc
    _state.launch_cmd = cmd
    _state.log_path = log_path
    _state.log_file_handle = log_handle
    return proc


async def _ipc_bool_call(
    method_name: str,
    *args: Any,
    success_msg: str,
    failure_msg: str,
) -> list[TextContent]:
    """Call a boolean-returning IPC method with standard error handling."""

    async def _cb(client):
        method = getattr(client, method_name)
        success = await method(*args)
        if success:
            return success_msg
        else:
            return failure_msg

    return await _ipc_call(_cb)


async def _ipc_call(
    callback: Any,
) -> list[TextContent]:
    """Call an IPC callback with standard error handling.

    The callback receives the IPC client and should return a string result.
    """
    try:
        client = _get_ipc_client()
        result = await callback(client)
        return _text_result(result)
    except IPCConnectionError as e:
        return _text_result(f"Connection error: {str(e)}")
    except ValueError as e:
        return _text_result(f"Invalid argument: {str(e)}")
    except Exception as e:
        return _text_result(f"Error: {str(e)}")


_TOOLS_CACHE: list[Tool] | None = None


@app.list_tools()
async def list_tools() -> list[Tool]:
    """Define available tools (cached after first call)."""
    global _TOOLS_CACHE
    if _TOOLS_CACHE is not None:
        return _TOOLS_CACHE
    _TOOLS_CACHE = [
        Tool(
            name="list_configs",
            description="List available Amiberry configuration files",
            inputSchema={
                "type": "object",
                "properties": {
                    "include_system": {
                        "type": "boolean",
                        "description": "On Linux, also include system configs from XDG_CONFIG_HOME (default: false)",
                    }
                },
            },
        ),
        Tool(
            name="get_config_content",
            description="Read the contents of a specific configuration file",
            inputSchema={
                "type": "object",
                "properties": {
                    "config_name": {
                        "type": "string",
                        "description": "Name of the config file (e.g., 'A500.uae')",
                    }
                },
                "required": ["config_name"],
            },
        ),
        Tool(
            name="list_disk_images",
            description="Find ADF/HDF/DMS disk images in configured directories",
            inputSchema={
                "type": "object",
                "properties": {
                    "search_term": {
                        "type": "string",
                        "description": "Optional search term to filter results",
                    },
                    "image_type": {
                        "type": "string",
                        "enum": ["all", "floppy", "hardfile", "lha"],
                        "description": "Filter by image type (default: all)",
                    },
                },
            },
        ),
        Tool(
            name="launch_amiberry",
            description="Launch Amiberry with specified configuration and optional disk image or .lha file",
            inputSchema={
                "type": "object",
                "properties": {
                    "config": {
                        "type": "string",
                        "description": "Name of config file to use (e.g., 'A500.uae')",
                    },
                    "model": {
                        "type": "string",
                        "enum": SUPPORTED_MODELS,
                        "description": "Launch with a specific model configuration (A500, A1200, or CD32). If specified, this overrides the config file.",
                    },
                    "disk_image": {
                        "type": "string",
                        "description": "Optional disk image path to mount in DF0:",
                    },
                    "lha_file": {
                        "type": "string",
                        "description": "Optional .lha archive file. Amiberry will auto-extract and configure it. Can be used alone without model or config.",
                    },
                    "autostart": {
                        "type": "boolean",
                        "description": "Auto-start the emulation (default: true)",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="list_savestates",
            description="List available savestate files",
            inputSchema={
                "type": "object",
                "properties": {
                    "search_term": {
                        "type": "string",
                        "description": "Optional search term to filter results",
                    }
                },
            },
        ),
        Tool(
            name="get_platform_info",
            description="Get information about the current platform and Amiberry paths",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        # New Phase 1 tools
        Tool(
            name="launch_with_logging",
            description="Launch Amiberry with console logging enabled, capturing output to a log file for debugging",
            inputSchema={
                "type": "object",
                "properties": {
                    "config": {
                        "type": "string",
                        "description": "Name of config file to use (e.g., 'A500.uae')",
                    },
                    "model": {
                        "type": "string",
                        "enum": SUPPORTED_MODELS + ["A500P", "A600", "A4000", "CDTV"],
                        "description": "Launch with a specific model configuration",
                    },
                    "disk_image": {
                        "type": "string",
                        "description": "Optional disk image path to mount in DF0:",
                    },
                    "lha_file": {
                        "type": "string",
                        "description": "Optional .lha archive file",
                    },
                    "autostart": {
                        "type": "boolean",
                        "description": "Auto-start the emulation (default: true)",
                    },
                    "log_name": {
                        "type": "string",
                        "description": "Optional name for the log file (default: auto-generated timestamp)",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="parse_config",
            description="Parse a .uae configuration file and return its contents as structured data with a summary",
            inputSchema={
                "type": "object",
                "properties": {
                    "config_name": {
                        "type": "string",
                        "description": "Name of the config file (e.g., 'A500.uae')",
                    },
                    "include_raw": {
                        "type": "boolean",
                        "description": "Include the raw key-value pairs in addition to the summary (default: false)",
                    },
                },
                "required": ["config_name"],
            },
        ),
        Tool(
            name="modify_config",
            description="Modify specific options in an existing .uae configuration file",
            inputSchema={
                "type": "object",
                "properties": {
                    "config_name": {
                        "type": "string",
                        "description": "Name of the config file to modify (e.g., 'A500.uae')",
                    },
                    "modifications": {
                        "type": "object",
                        "description": "Dictionary of options to modify. Set value to null to remove an option.",
                        "additionalProperties": {
                            "type": ["string", "null"],
                        },
                    },
                },
                "required": ["config_name", "modifications"],
            },
        ),
        Tool(
            name="create_config",
            description="Create a new .uae configuration file from a built-in template",
            inputSchema={
                "type": "object",
                "properties": {
                    "config_name": {
                        "type": "string",
                        "description": "Name for the new config file (e.g., 'MyConfig.uae')",
                    },
                    "template": {
                        "type": "string",
                        "enum": [
                            "A500",
                            "A500P",
                            "A600",
                            "A1200",
                            "A4000",
                            "CD32",
                            "CDTV",
                        ],
                        "description": "Template to base the config on (default: A500)",
                    },
                    "overrides": {
                        "type": "object",
                        "description": "Optional settings to override from the template",
                        "additionalProperties": {
                            "type": "string",
                        },
                    },
                },
                "required": ["config_name"],
            },
        ),
        Tool(
            name="launch_whdload",
            description="Search for and launch a WHDLoad game (.lha file) by name",
            inputSchema={
                "type": "object",
                "properties": {
                    "search_term": {
                        "type": "string",
                        "description": "Game name to search for",
                    },
                    "exact_path": {
                        "type": "string",
                        "description": "Exact path to the .lha file (alternative to search_term)",
                    },
                    "model": {
                        "type": "string",
                        "enum": ["A500", "A1200", "A4000"],
                        "description": "Amiga model to use (default: auto-detect or A1200)",
                    },
                    "autostart": {
                        "type": "boolean",
                        "description": "Auto-start the emulation (default: true)",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="launch_cd",
            description="Launch a CD image (ISO/CUE/CHD) with automatic CD32 or CDTV detection",
            inputSchema={
                "type": "object",
                "properties": {
                    "cd_image": {
                        "type": "string",
                        "description": "Path to the CD image file (.iso, .cue, .chd)",
                    },
                    "search_term": {
                        "type": "string",
                        "description": "Search for CD image by name instead of providing exact path",
                    },
                    "model": {
                        "type": "string",
                        "enum": ["CD32", "CDTV"],
                        "description": "Force specific model (default: CD32)",
                    },
                    "autostart": {
                        "type": "boolean",
                        "description": "Auto-start the emulation (default: true)",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="set_disk_swapper",
            description="Configure the disk swapper with multiple floppy images for multi-disk games",
            inputSchema={
                "type": "object",
                "properties": {
                    "disk_images": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of disk image paths to include in the swapper",
                    },
                    "model": {
                        "type": "string",
                        "enum": SUPPORTED_MODELS,
                        "description": "Amiga model to use (default: A500)",
                    },
                    "config": {
                        "type": "string",
                        "description": "Config file to use instead of model preset",
                    },
                    "autostart": {
                        "type": "boolean",
                        "description": "Auto-start the emulation (default: true)",
                    },
                },
                "required": ["disk_images"],
            },
        ),
        Tool(
            name="list_cd_images",
            description="Find CD images (ISO/CUE/CHD) in configured directories",
            inputSchema={
                "type": "object",
                "properties": {
                    "search_term": {
                        "type": "string",
                        "description": "Optional search term to filter results",
                    },
                },
            },
        ),
        Tool(
            name="get_log_content",
            description="Read the content of a captured log file",
            inputSchema={
                "type": "object",
                "properties": {
                    "log_name": {
                        "type": "string",
                        "description": "Name of the log file to read",
                    },
                    "tail_lines": {
                        "type": "integer",
                        "description": "Only return the last N lines (default: all)",
                    },
                },
                "required": ["log_name"],
            },
        ),
        Tool(
            name="list_logs",
            description="List available log files from previous launches with logging",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        # Phase 2 tools
        Tool(
            name="inspect_savestate",
            description="Inspect a .uss savestate file and extract metadata (CPU, memory, ROM, disks)",
            inputSchema={
                "type": "object",
                "properties": {
                    "savestate_path": {
                        "type": "string",
                        "description": "Path to the .uss savestate file, or just the filename if in default savestates directory",
                    },
                },
                "required": ["savestate_path"],
            },
        ),
        Tool(
            name="list_roms",
            description="List and identify ROM files (Kickstart) in the ROMs directory",
            inputSchema={
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Optional custom directory to scan (default: Amiberry's Kickstarts folder)",
                    },
                },
            },
        ),
        Tool(
            name="identify_rom",
            description="Identify a specific ROM file by calculating its checksum and looking it up",
            inputSchema={
                "type": "object",
                "properties": {
                    "rom_path": {
                        "type": "string",
                        "description": "Path to the ROM file",
                    },
                },
                "required": ["rom_path"],
            },
        ),
        Tool(
            name="get_amiberry_version",
            description="Get Amiberry version and build information",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        # Runtime control tools (IPC)
        Tool(
            name="pause_emulation",
            description="Pause a running Amiberry emulation. Requires Amiberry to be running with IPC enabled (USE_IPC_SOCKET).",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="resume_emulation",
            description="Resume a paused Amiberry emulation. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="reset_emulation",
            description="Reset the running Amiberry emulation. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "hard": {
                        "type": "boolean",
                        "description": "If true, perform a hard reset. Otherwise soft/keyboard reset (default: false).",
                    },
                },
            },
        ),
        Tool(
            name="runtime_screenshot",
            description="Take a screenshot of the running emulation. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Filename for the screenshot (saved in Amiberry screenshots folder if not absolute path).",
                    },
                },
                "required": ["filename"],
            },
        ),
        Tool(
            name="runtime_save_state",
            description="Save the current emulation state while running. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "state_file": {
                        "type": "string",
                        "description": "Path for the savestate file (.uss)",
                    },
                    "config_file": {
                        "type": "string",
                        "description": "Path for the associated config file (.uae)",
                    },
                },
                "required": ["state_file", "config_file"],
            },
        ),
        Tool(
            name="runtime_load_state",
            description="Load a savestate into the running emulation. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "state_file": {
                        "type": "string",
                        "description": "Path to the savestate file (.uss)",
                    },
                },
                "required": ["state_file"],
            },
        ),
        Tool(
            name="runtime_insert_floppy",
            description="Insert a floppy disk image into a running emulation. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "drive": {
                        "type": "integer",
                        "description": "Drive number (0-3 for DF0-DF3)",
                        "minimum": 0,
                        "maximum": 3,
                    },
                    "image_path": {
                        "type": "string",
                        "description": "Path to the disk image file",
                    },
                },
                "required": ["drive", "image_path"],
            },
        ),
        Tool(
            name="runtime_insert_cd",
            description="Insert a CD image into a running emulation. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "image_path": {
                        "type": "string",
                        "description": "Path to the CD image file",
                    },
                },
                "required": ["image_path"],
            },
        ),
        Tool(
            name="get_runtime_status",
            description="Get the current status of a running Amiberry emulation (paused state, loaded config, mounted disks). Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_get_config",
            description="Get a configuration option from the running emulation. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "option": {
                        "type": "string",
                        "description": "Configuration option name (e.g., 'chipmem_size', 'cpu_model', 'floppy_speed')",
                    },
                },
                "required": ["option"],
            },
        ),
        Tool(
            name="runtime_set_config",
            description="Set a configuration option on the running emulation. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "option": {
                        "type": "string",
                        "description": "Configuration option name",
                    },
                    "value": {
                        "type": "string",
                        "description": "New value for the option",
                    },
                },
                "required": ["option", "value"],
            },
        ),
        Tool(
            name="check_ipc_connection",
            description="Check if Amiberry IPC is available and get connection status",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        # New runtime control tools
        Tool(
            name="runtime_eject_floppy",
            description="Eject a floppy disk from a running emulation. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "drive": {
                        "type": "integer",
                        "description": "Drive number (0-3 for DF0-DF3)",
                        "minimum": 0,
                        "maximum": 3,
                    },
                },
                "required": ["drive"],
            },
        ),
        Tool(
            name="runtime_eject_cd",
            description="Eject the CD from a running emulation. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_list_floppies",
            description="List all floppy drives and their contents in the running emulation. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_list_configs",
            description="List available configuration files from the running Amiberry. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_set_volume",
            description="Set the master volume of the running emulation. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "volume": {
                        "type": "integer",
                        "description": "Volume level (0-100)",
                        "minimum": 0,
                        "maximum": 100,
                    },
                },
                "required": ["volume"],
            },
        ),
        Tool(
            name="runtime_get_volume",
            description="Get the current volume of the running emulation. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_mute",
            description="Mute audio in the running emulation. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_unmute",
            description="Unmute audio in the running emulation. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_toggle_fullscreen",
            description="Toggle fullscreen mode in the running emulation. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_set_warp",
            description="Enable or disable warp mode (maximum speed) in the running emulation. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "enabled": {
                        "type": "boolean",
                        "description": "True to enable warp mode, False to disable",
                    },
                },
                "required": ["enabled"],
            },
        ),
        Tool(
            name="runtime_get_warp",
            description="Get the current warp mode status of the running emulation. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_get_version",
            description="Get version information from the running Amiberry instance. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_frame_advance",
            description="Advance emulation by a number of frames (when paused). Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "frames": {
                        "type": "integer",
                        "description": "Number of frames to advance (1-100, default: 1)",
                        "minimum": 1,
                        "maximum": 100,
                    },
                },
            },
        ),
        Tool(
            name="runtime_send_mouse",
            description="Send mouse input to the running emulation. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "dx": {
                        "type": "integer",
                        "description": "X movement delta",
                    },
                    "dy": {
                        "type": "integer",
                        "description": "Y movement delta",
                    },
                    "buttons": {
                        "type": "integer",
                        "description": "Button mask (bit 0=left, bit 1=right, bit 2=middle)",
                        "minimum": 0,
                        "maximum": 7,
                    },
                },
                "required": ["dx", "dy"],
            },
        ),
        Tool(
            name="runtime_set_mouse_speed",
            description="Set mouse sensitivity in the running emulation. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "speed": {
                        "type": "integer",
                        "description": "Mouse speed (10-200, default: 100)",
                        "minimum": 10,
                        "maximum": 200,
                    },
                },
                "required": ["speed"],
            },
        ),
        Tool(
            name="runtime_send_key",
            description="Send keyboard input to the running emulation. Accepts a key name (e.g. 'space', 'return', 'f1', 'a') or numeric Amiga scancode. By default performs a press-and-release; set state to 'press' or 'release' for individual events. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Key name (e.g. 'space', 'return', 'escape', 'f1', 'a', 'up', 'ctrl') or numeric Amiga scancode (e.g. '68', '0x44'). Common keys: space, return/enter, escape/esc, backspace, delete/del, tab, up, down, left, right, f1-f10, ctrl, alt/lalt, left_shift/lshift, right_shift/rshift, left_amiga/lamiga, right_amiga/ramiga, help, a-z, 0-9.",
                    },
                    "state": {
                        "type": "string",
                        "description": "Key state: 'press' (key down only), 'release' (key up only), or 'press_and_release' (full keypress, default). Use 'press'/'release' for modifier keys or key combinations.",
                        "enum": ["press", "release", "press_and_release"],
                    },
                },
                "required": ["key"],
            },
        ),
        Tool(
            name="runtime_send_text",
            description="Send a string of text into the running emulation by sending key events for each character. Handles uppercase (via Shift), symbols, and common whitespace (space, newline as Return, tab). Use this for entering commands like 'dir' or filenames. For special keys like F1 or Return, use runtime_send_key instead. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to type. Supports letters (a-z, A-Z), digits (0-9), common punctuation, space, and newline (\\n for Return). Example: 'dir\\n' types 'dir' and presses Return.",
                    },
                    "delay_ms": {
                        "type": "integer",
                        "description": "Delay in milliseconds between key events (default: 50). Increase for slower machines or if characters are dropped.",
                        "minimum": 10,
                        "maximum": 1000,
                    },
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="runtime_ping",
            description="Test the IPC connection to a running Amiberry instance.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="set_active_instance",
            description="Set the active Amiberry instance to control (e.g. 0, 1, 2). Set to null to auto-discover.",
            inputSchema={
                "type": "object",
                "properties": {
                    "instance": {
                        "type": ["integer", "null"],
                        "description": "Instance number to control, or null to auto-discover.",
                    }
                },
                "required": ["instance"],
            },
        ),
        Tool(
            name="get_active_instance",
            description="Get the currently active Amiberry instance being controlled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        # Round 2 runtime control tools
        Tool(
            name="runtime_quicksave",
            description="Quick save to a slot (0-9). Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "slot": {
                        "type": "integer",
                        "description": "Slot number (0-9, default: 0)",
                        "minimum": 0,
                        "maximum": 9,
                    },
                },
            },
        ),
        Tool(
            name="runtime_quickload",
            description="Quick load from a slot (0-9). Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "slot": {
                        "type": "integer",
                        "description": "Slot number (0-9, default: 0)",
                        "minimum": 0,
                        "maximum": 9,
                    },
                },
            },
        ),
        Tool(
            name="runtime_get_joyport_mode",
            description="Get joystick port mode. Returns mode number and name. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "port": {
                        "type": "integer",
                        "description": "Port number (0-3)",
                        "minimum": 0,
                        "maximum": 3,
                    },
                },
                "required": ["port"],
            },
        ),
        Tool(
            name="runtime_set_joyport_mode",
            description="Set joystick port mode. Modes: 0=default, 2=mouse, 3=joystick, 4=gamepad, 7=cd32. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "port": {
                        "type": "integer",
                        "description": "Port number (0-3)",
                        "minimum": 0,
                        "maximum": 3,
                    },
                    "mode": {
                        "type": "integer",
                        "description": "Mode (0=default, 2=mouse, 3=joystick, 4=gamepad, 7=cd32)",
                        "minimum": 0,
                        "maximum": 8,
                    },
                },
                "required": ["port", "mode"],
            },
        ),
        Tool(
            name="runtime_get_autofire",
            description="Get autofire mode for a port. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "port": {
                        "type": "integer",
                        "description": "Port number (0-3)",
                        "minimum": 0,
                        "maximum": 3,
                    },
                },
                "required": ["port"],
            },
        ),
        Tool(
            name="runtime_set_autofire",
            description="Set autofire mode for a port. Modes: 0=off, 1=normal, 2=toggle, 3=always, 4=toggle_noaf. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "port": {
                        "type": "integer",
                        "description": "Port number (0-3)",
                        "minimum": 0,
                        "maximum": 3,
                    },
                    "mode": {
                        "type": "integer",
                        "description": "Autofire mode (0=off, 1=normal, 2=toggle, 3=always, 4=toggle_noaf)",
                        "minimum": 0,
                        "maximum": 4,
                    },
                },
                "required": ["port", "mode"],
            },
        ),
        Tool(
            name="runtime_get_led_status",
            description="Get all LED states (power, floppy drives, HD, CD, caps lock). Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_list_harddrives",
            description="List all mounted hard drives and directories. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_set_display_mode",
            description="Set display mode. Modes: 0=window, 1=fullscreen, 2=fullwindow. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "integer",
                        "description": "Display mode (0=window, 1=fullscreen, 2=fullwindow)",
                        "minimum": 0,
                        "maximum": 2,
                    },
                },
                "required": ["mode"],
            },
        ),
        Tool(
            name="runtime_get_display_mode",
            description="Get current display mode. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_set_ntsc",
            description="Set video mode to PAL or NTSC. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "enabled": {
                        "type": "boolean",
                        "description": "True for NTSC, False for PAL",
                    },
                },
                "required": ["enabled"],
            },
        ),
        Tool(
            name="runtime_get_ntsc",
            description="Get current video mode (PAL or NTSC). Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_set_sound_mode",
            description="Set sound mode. Modes: 0=off, 1=normal, 2=stereo, 3=best. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "integer",
                        "description": "Sound mode (0=off, 1=normal, 2=stereo, 3=best)",
                        "minimum": 0,
                        "maximum": 3,
                    },
                },
                "required": ["mode"],
            },
        ),
        Tool(
            name="runtime_get_sound_mode",
            description="Get current sound mode. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        # Round 3 runtime control tools
        Tool(
            name="runtime_toggle_mouse_grab",
            description="Toggle mouse capture. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_get_mouse_speed",
            description="Get current mouse speed. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_set_cpu_speed",
            description="Set CPU speed. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "speed": {
                        "type": "integer",
                        "description": "CPU speed (-1=max, 0=cycle-exact, >0=percentage)",
                    },
                },
                "required": ["speed"],
            },
        ),
        Tool(
            name="runtime_get_cpu_speed",
            description="Get current CPU speed. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_toggle_rtg",
            description="Toggle between RTG and chipset display. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "monid": {
                        "type": "integer",
                        "description": "Monitor ID (default 0)",
                        "minimum": 0,
                    },
                },
            },
        ),
        Tool(
            name="runtime_set_floppy_speed",
            description="Set floppy drive speed. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "speed": {
                        "type": "integer",
                        "description": "Floppy speed (0=turbo, 100=1x, 200=2x, 400=4x, 800=8x)",
                        "enum": [0, 100, 200, 400, 800],
                    },
                },
                "required": ["speed"],
            },
        ),
        Tool(
            name="runtime_get_floppy_speed",
            description="Get current floppy speed. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_disk_write_protect",
            description="Set write protection on a floppy disk. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "drive": {
                        "type": "integer",
                        "description": "Drive number (0-3)",
                        "minimum": 0,
                        "maximum": 3,
                    },
                    "protect": {
                        "type": "boolean",
                        "description": "True to protect, False to allow writes",
                    },
                },
                "required": ["drive", "protect"],
            },
        ),
        Tool(
            name="runtime_get_disk_write_protect",
            description="Get write protection status for a floppy disk. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "drive": {
                        "type": "integer",
                        "description": "Drive number (0-3)",
                        "minimum": 0,
                        "maximum": 3,
                    },
                },
                "required": ["drive"],
            },
        ),
        Tool(
            name="runtime_toggle_status_line",
            description="Toggle on-screen status line (cycle: off/chipset/rtg/both). Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_set_chipset",
            description="Set chipset. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "chipset": {
                        "type": "string",
                        "description": "Chipset name",
                        "enum": ["OCS", "ECS_AGNUS", "ECS_DENISE", "ECS", "AGA"],
                    },
                },
                "required": ["chipset"],
            },
        ),
        Tool(
            name="runtime_get_chipset",
            description="Get current chipset. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_get_memory_config",
            description="Get all memory sizes (chip, fast, bogo, z3, rtg). Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_get_fps",
            description="Get current frame rate and performance info. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        # Round 4 runtime control tools - Memory and Window Control
        Tool(
            name="runtime_set_chip_mem",
            description="Set Chip RAM size. Requires Amiberry to be running with IPC enabled. Note: Memory changes require a reset to take effect.",
            inputSchema={
                "type": "object",
                "properties": {
                    "size_kb": {
                        "type": "integer",
                        "description": "Chip RAM size in KB",
                        "enum": [256, 512, 1024, 2048, 4096, 8192],
                    },
                },
                "required": ["size_kb"],
            },
        ),
        Tool(
            name="runtime_set_fast_mem",
            description="Set Fast RAM size. Requires Amiberry to be running with IPC enabled. Note: Memory changes require a reset to take effect.",
            inputSchema={
                "type": "object",
                "properties": {
                    "size_kb": {
                        "type": "integer",
                        "description": "Fast RAM size in KB",
                        "enum": [0, 64, 128, 256, 512, 1024, 2048, 4096, 8192],
                    },
                },
                "required": ["size_kb"],
            },
        ),
        Tool(
            name="runtime_set_slow_mem",
            description="Set Slow RAM (Bogo) size. Requires Amiberry to be running with IPC enabled. Note: Memory changes require a reset to take effect.",
            inputSchema={
                "type": "object",
                "properties": {
                    "size_kb": {
                        "type": "integer",
                        "description": "Slow RAM size in KB",
                        "enum": [0, 256, 512, 1024, 1536, 1792],
                    },
                },
                "required": ["size_kb"],
            },
        ),
        Tool(
            name="runtime_set_z3_mem",
            description="Set Zorro III Fast RAM size. Requires Amiberry to be running with IPC enabled. Note: Memory changes require a reset to take effect.",
            inputSchema={
                "type": "object",
                "properties": {
                    "size_mb": {
                        "type": "integer",
                        "description": "Z3 Fast RAM size in MB",
                        "enum": [0, 1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024],
                    },
                },
                "required": ["size_mb"],
            },
        ),
        Tool(
            name="runtime_get_cpu_model",
            description="Get CPU model information. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_set_cpu_model",
            description="Set CPU model. Requires Amiberry to be running with IPC enabled. Note: CPU changes require a reset to take effect.",
            inputSchema={
                "type": "object",
                "properties": {
                    "model": {
                        "type": "string",
                        "description": "CPU model",
                        "enum": ["68000", "68010", "68020", "68030", "68040", "68060"],
                    },
                },
                "required": ["model"],
            },
        ),
        Tool(
            name="runtime_set_window_size",
            description="Set window size. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "width": {
                        "type": "integer",
                        "description": "Window width (320-3840)",
                        "minimum": 320,
                        "maximum": 3840,
                    },
                    "height": {
                        "type": "integer",
                        "description": "Window height (200-2160)",
                        "minimum": 200,
                        "maximum": 2160,
                    },
                },
                "required": ["width", "height"],
            },
        ),
        Tool(
            name="runtime_get_window_size",
            description="Get current window size. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_set_scaling",
            description="Set scaling mode. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "integer",
                        "description": "Scaling mode (-1=auto, 0=nearest, 1=linear, 2=integer)",
                        "minimum": -1,
                        "maximum": 2,
                    },
                },
                "required": ["mode"],
            },
        ),
        Tool(
            name="runtime_get_scaling",
            description="Get current scaling mode. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_set_line_mode",
            description="Set line mode (single/double/scanlines). Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "integer",
                        "description": "Line mode (0=single, 1=double, 2=scanlines)",
                        "minimum": 0,
                        "maximum": 2,
                    },
                },
                "required": ["mode"],
            },
        ),
        Tool(
            name="runtime_get_line_mode",
            description="Get current line mode. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_set_resolution",
            description="Set display resolution. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "integer",
                        "description": "Resolution (0=lores, 1=hires, 2=superhires)",
                        "minimum": 0,
                        "maximum": 2,
                    },
                },
                "required": ["mode"],
            },
        ),
        Tool(
            name="runtime_get_resolution",
            description="Get current display resolution. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        # Round 5 - Autocrop and WHDLoad
        Tool(
            name="runtime_set_autocrop",
            description="Enable or disable automatic display cropping. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {
                    "enabled": {
                        "type": "boolean",
                        "description": "True to enable autocrop, False to disable",
                    },
                },
                "required": ["enabled"],
            },
        ),
        Tool(
            name="runtime_get_autocrop",
            description="Get current autocrop status. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_insert_whdload",
            description="Load a WHDLoad game from an LHA archive or directory. Requires Amiberry to be running with IPC enabled. Note: A reset may be required for the game to start.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the LHA archive or WHDLoad game directory",
                    },
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="runtime_eject_whdload",
            description="Eject the currently loaded WHDLoad game. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_get_whdload",
            description="Get information about the currently loaded WHDLoad game. Requires Amiberry to be running with IPC enabled.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        # === Debugging Tools ===
        Tool(
            name="runtime_debug_activate",
            description="Activate the built-in debugger. Requires Amiberry to be built with debugger support.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_debug_deactivate",
            description="Deactivate the debugger and resume emulation.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_debug_status",
            description="Get debugger status (active/inactive).",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_debug_step",
            description="Single-step CPU instructions when debugger is active.",
            inputSchema={
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "Number of instructions to step (default: 1)",
                        "default": 1,
                    },
                },
            },
        ),
        Tool(
            name="runtime_debug_continue",
            description="Continue execution until next breakpoint when debugger is active.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_get_cpu_regs",
            description="Get all CPU registers (D0-D7, A0-A7, PC, SR, USP, ISP).",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_get_custom_regs",
            description="Get key custom chip registers (DMACON, INTENA, INTREQ, Copper addresses).",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_disassemble",
            description="Disassemble instructions at a memory address.",
            inputSchema={
                "type": "object",
                "properties": {
                    "address": {
                        "type": "string",
                        "description": "Memory address (hex e.g., '0xFC0000' or decimal)",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of instructions to disassemble (default: 10)",
                        "default": 10,
                    },
                },
                "required": ["address"],
            },
        ),
        Tool(
            name="runtime_set_breakpoint",
            description="Set a breakpoint at a memory address. Maximum 20 breakpoints.",
            inputSchema={
                "type": "object",
                "properties": {
                    "address": {
                        "type": "string",
                        "description": "Memory address (hex e.g., '0x400' or decimal)",
                    },
                },
                "required": ["address"],
            },
        ),
        Tool(
            name="runtime_clear_breakpoint",
            description="Clear a breakpoint at a specific address or all breakpoints.",
            inputSchema={
                "type": "object",
                "properties": {
                    "address": {
                        "type": "string",
                        "description": "Memory address (hex e.g., '0x400') or 'ALL' to clear all",
                    },
                },
                "required": ["address"],
            },
        ),
        Tool(
            name="runtime_list_breakpoints",
            description="List all active breakpoints.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_get_copper_state",
            description="Get Copper coprocessor state (addresses, enabled status).",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_get_blitter_state",
            description="Get Blitter state (busy status, channels, dimensions, addresses).",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_get_drive_state",
            description="Get floppy drive state (track, side, motor, disk inserted).",
            inputSchema={
                "type": "object",
                "properties": {
                    "drive": {
                        "type": "integer",
                        "description": "Drive number 0-3 (default: all drives)",
                    },
                },
            },
        ),
        Tool(
            name="runtime_get_audio_state",
            description="Get audio channel states (volume, period, enabled).",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="runtime_get_dma_state",
            description="Get DMA channel states (bitplane, sprite, audio, disk, copper, blitter).",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        # === Process Lifecycle Management ===
        Tool(
            name="check_process_alive",
            description="Check if the Amiberry process is still running. Returns PID, running status, and exit code/signal if terminated.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="get_process_info",
            description="Get detailed process information: PID, running status, exit code, crash detection (signal-based termination).",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="kill_amiberry",
            description="Force kill a running Amiberry process. Sends SIGTERM first, then SIGKILL after 5 seconds if needed.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="wait_for_exit",
            description="Wait for the Amiberry process to exit. Returns the exit code when done or times out.",
            inputSchema={
                "type": "object",
                "properties": {
                    "timeout": {
                        "type": "integer",
                        "description": "Maximum seconds to wait (default: 30)",
                    },
                },
            },
        ),
        Tool(
            name="restart_amiberry",
            description="Kill the existing Amiberry process and re-launch with the same command that was used previously.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        # === Missing IPC Tool Wrappers ===
        Tool(
            name="runtime_read_memory",
            description="Read memory from the emulated Amiga at a given address. Returns the value in hex and decimal.",
            inputSchema={
                "type": "object",
                "properties": {
                    "address": {
                        "type": "string",
                        "description": "Memory address (hex e.g. '0xBFE001' or decimal)",
                    },
                    "width": {
                        "type": "integer",
                        "description": "Bytes to read: 1, 2, or 4",
                        "enum": [1, 2, 4],
                    },
                },
                "required": ["address", "width"],
            },
        ),
        Tool(
            name="runtime_write_memory",
            description="Write a value to the emulated Amiga memory at a given address.",
            inputSchema={
                "type": "object",
                "properties": {
                    "address": {
                        "type": "string",
                        "description": "Memory address (hex e.g. '0xBFE001' or decimal)",
                    },
                    "width": {
                        "type": "integer",
                        "description": "Bytes to write: 1, 2, or 4",
                        "enum": [1, 2, 4],
                    },
                    "value": {
                        "type": "integer",
                        "description": "Value to write",
                    },
                },
                "required": ["address", "width", "value"],
            },
        ),
        Tool(
            name="runtime_load_config",
            description="Load a .uae configuration file into the running emulation. The config path can be absolute or just the filename if in the default config directory.",
            inputSchema={
                "type": "object",
                "properties": {
                    "config_path": {
                        "type": "string",
                        "description": "Path to the .uae config file",
                    },
                },
                "required": ["config_path"],
            },
        ),
        Tool(
            name="runtime_debug_step_over",
            description="Step over subroutine calls (execute JSR/BSR as a single step). Requires the debugger to be active.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        # === Screenshot with Image Data ===
        Tool(
            name="runtime_screenshot_view",
            description="Take a screenshot and return the image data so Claude can see what is displayed on the emulation screen. Essential for debugging visual issues.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Optional filename for the screenshot (default: auto-generated)",
                    },
                },
            },
        ),
        # === Log Tailing and Crash Detection ===
        Tool(
            name="tail_log",
            description="Get new log lines since the last read of this log file. Efficient for monitoring ongoing output without re-reading the entire file.",
            inputSchema={
                "type": "object",
                "properties": {
                    "log_name": {
                        "type": "string",
                        "description": "Name of the log file",
                    },
                },
                "required": ["log_name"],
            },
        ),
        Tool(
            name="wait_for_log_pattern",
            description="Wait for a specific pattern to appear in the log file. Useful for waiting until Amiberry has finished starting, or detecting specific events.",
            inputSchema={
                "type": "object",
                "properties": {
                    "log_name": {
                        "type": "string",
                        "description": "Name of the log file",
                    },
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern to search for",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Maximum seconds to wait (default: 30)",
                    },
                },
                "required": ["log_name", "pattern"],
            },
        ),
        Tool(
            name="get_crash_info",
            description="Detect if Amiberry crashed by checking process state and scanning logs for crash indicators (segfault, abort, assertion failures). Returns crash details if found.",
            inputSchema={
                "type": "object",
                "properties": {
                    "log_name": {
                        "type": "string",
                        "description": "Optional log file name to scan (default: most recent log)",
                    },
                },
            },
        ),
        # === Workflow Tools ===
        Tool(
            name="health_check",
            description="Comprehensive health check: verifies Amiberry process is running, IPC socket is responsive, and returns basic emulation status. Use this before any debugging session.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="launch_and_wait_for_ipc",
            description="Launch Amiberry with logging enabled and wait until the IPC socket becomes available. Returns when ready for IPC commands or on timeout.",
            inputSchema={
                "type": "object",
                "properties": {
                    "config": {
                        "type": "string",
                        "description": "Config file name to use",
                    },
                    "model": {
                        "type": "string",
                        "description": "Amiga model (A500, A500P, A600, A1200, A4000, CD32, CDTV)",
                        "enum": [
                            "A500",
                            "A500P",
                            "A600",
                            "A1200",
                            "A4000",
                            "CD32",
                            "CDTV",
                        ],
                    },
                    "disk_image": {
                        "type": "string",
                        "description": "Optional disk image to insert in DF0",
                    },
                    "lha_file": {
                        "type": "string",
                        "description": "Optional .lha file to launch",
                    },
                    "autostart": {
                        "type": "boolean",
                        "description": "Auto-start emulation (default: true)",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Max seconds to wait for IPC (default: 30)",
                    },
                },
            },
        ),
    ]
    return _TOOLS_CACHE


# ---- Tool handler functions ----


async def _handle_get_platform_info(arguments: Any) -> list:
    """Handle get_platform_info tool."""
    info = get_platform_info()
    lines = [f"{key}: {value}" for key, value in info.items()]
    return _text_result("\n".join(lines))


async def _handle_list_configs(arguments: Any) -> list:
    """Handle list_configs tool."""
    include_system = arguments.get("include_system", False)

    def _scan_configs():
        configs = []
        # User configs
        if CONFIG_DIR.exists():
            user_configs = [(f.name, "user", str(f)) for f in CONFIG_DIR.glob("*.uae")]
            configs.extend(user_configs)

        # System configs (Linux only)
        if IS_LINUX and include_system:
            if SYSTEM_CONFIG_DIR and SYSTEM_CONFIG_DIR.exists():
                sys_configs = [
                    (f.name, "system", str(f)) for f in SYSTEM_CONFIG_DIR.glob("*.uae")
                ]
                configs.extend(sys_configs)
        return configs

    configs = await asyncio.to_thread(_scan_configs)

    if not configs:
        return _text_result("No configuration files found.")

    result = f"Found {len(configs)} configuration(s):\n\n"
    for cfg_name, source, path in sorted(configs):
        result += f"- {cfg_name} ({source})\n  Path: {path}\n"

    return _text_result(result)


async def _handle_get_config_content(arguments: Any) -> list:
    """Handle get_config_content tool."""
    config_name = arguments["config_name"]
    config_path = _find_config_path(config_name)

    if not config_path:
        return _text_result(f"Error: Configuration '{config_name}' not found")

    try:
        content = await asyncio.to_thread(config_path.read_text)
        return _text_result(f"Configuration: {config_name}\n\n{content}")
    except Exception as e:
        return _text_result(f"Error reading config: {str(e)}")


async def _handle_list_disk_images(arguments: Any) -> list:
    """Handle list_disk_images tool."""
    search_term = arguments.get("search_term", "")
    image_type = arguments.get("image_type", "all")

    images = await asyncio.to_thread(
        scan_disk_images, DISK_IMAGE_DIRS, image_type, search_term
    )

    if not images:
        msg = "No disk images found"
        if search_term:
            msg += f' matching "{search_term}"'
        return _text_result(f"{msg}.")

    result = f"Found {len(images)} disk image(s):\n\n"
    for img in images:
        result += f"- {img['name']} ({img['type']})\n  {img['path']}\n"

    return _text_result(result)


async def _handle_launch_amiberry(arguments: Any) -> list:
    """Handle launch_amiberry tool."""

    model = arguments.get("model")
    config = arguments.get("config")
    lha_file = arguments.get("lha_file")

    # Validate that at least one of model, config, or lha_file is specified
    if not model and not config and not lha_file:
        return _text_result(
            "Error: Either 'model', 'config', or 'lha_file' must be specified"
        )

    # Resolve config path if specified
    config_path = None
    if config:
        config_path = _find_config_path(config)
        if not config_path:
            return _text_result(f"Error: Configuration '{config}' not found")

    # Validate LHA file
    if lha_file:
        lha_path = Path(lha_file)
        if not lha_path.exists():
            return _text_result(f"Error: LHA file not found: {lha_file}")

    cmd = build_launch_command(
        model=model,
        config_path=config_path,
        disk_image=arguments.get("disk_image"),
        lha_file=lha_file,
        autostart=arguments.get("autostart", True),
    )

    try:
        proc = _launch_and_store(cmd)

        if model:
            result = f"Launched Amiberry with model: {model}"
        elif config:
            result = f"Launched Amiberry with config: {config}"
        elif lha_file:
            result = f"Launched Amiberry with LHA: {Path(lha_file).name}"
        else:
            result = "Launched Amiberry"

        result += f"\n  PID: {proc.pid}"

        if "disk_image" in arguments and arguments["disk_image"]:
            result += f"\n  Disk in DF0: {Path(arguments['disk_image']).name}"

        if lha_file and (model or config):
            result += f"\n  LHA: {Path(lha_file).name}"

        return _text_result(result)
    except Exception as e:
        return _text_result(f"Error launching Amiberry: {str(e)}")


async def _handle_list_savestates(arguments: Any) -> list:
    """Handle list_savestates tool."""
    search_term = arguments.get("search_term", "").lower()

    def _scan_savestates():
        results = []
        if not SAVESTATE_DIR.exists():
            return results
        for state in SAVESTATE_DIR.glob("**/*.uss"):
            if not search_term or search_term in state.name.lower():
                try:
                    mtime = state.stat().st_mtime
                except OSError:
                    continue
                timestamp = format_log_timestamp(mtime)
                results.append(
                    {
                        "path": str(state),
                        "name": state.name,
                        "modified": timestamp,
                    }
                )
        return results

    savestates = await asyncio.to_thread(_scan_savestates)

    if not savestates:
        msg = "No savestates found"
        if search_term:
            msg += f' matching "{search_term}"'
        return _text_result(f"{msg}.")

    result = f"Found {len(savestates)} savestate(s):\n\n"
    for state in sorted(savestates, key=lambda x: x["name"]):
        result += f"- {state['name']}\n  Modified: {state['modified']}\n  Path: {state['path']}\n"

    return _text_result(result)


async def _handle_launch_with_logging(arguments: Any) -> list:
    """Handle launch_with_logging tool."""

    model = arguments.get("model")
    config = arguments.get("config")
    lha_file = arguments.get("lha_file")

    if not model and not config and not lha_file:
        return _text_result(
            "Error: Either 'model', 'config', or 'lha_file' must be specified"
        )

    # Create log directory if needed
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Generate log filename
    log_name = arguments.get("log_name")
    if not log_name:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        log_name = f"amiberry_{timestamp}.log"
    try:
        log_path = normalize_log_path(log_name)
    except ValueError:
        return _text_result(f"Error: Invalid log name '{log_name}'")

    # Resolve config path if specified
    config_path = None
    if config:
        config_path = _find_config_path(config)
        if not config_path:
            return _text_result(f"Error: Configuration '{config}' not found")

    # Validate LHA file
    if lha_file:
        lha_path = Path(lha_file)
        if not lha_path.exists():
            return _text_result(f"Error: LHA file not found: {lha_file}")

    cmd = build_launch_command(
        model=model,
        config_path=config_path,
        disk_image=arguments.get("disk_image"),
        lha_file=lha_file,
        autostart=arguments.get("autostart", True),
        with_logging=True,
    )

    try:
        proc = _launch_and_store(cmd, log_path=log_path)

        result = "Launched Amiberry with logging enabled\n"
        result += f"PID: {proc.pid}\n"
        result += f"Log file: {log_path}\n"
        result += f"Command: {' '.join(cmd)}"

        return _text_result(result)
    except Exception as e:
        _state.close_log_handle()
        return _text_result(f"Error launching Amiberry: {str(e)}")


async def _handle_parse_config(arguments: Any) -> list:
    """Handle parse_config tool."""
    config_name = arguments["config_name"]
    config_path = _find_config_path(config_name)

    if not config_path:
        return _text_result(f"Error: Configuration '{config_name}' not found")

    try:
        config = await asyncio.to_thread(parse_uae_config, config_path)
        summary = get_config_summary(config)

        result = f"Configuration: {config_name}\n\n"
        result += "=== Summary ===\n"
        result += f"CPU: {summary['cpu']['model']} ({summary['cpu']['speed']})\n"
        result += f"Chipset: {summary['chipset']}\n"
        result += f"Memory: {summary['memory']['chip_kb']}KB Chip"
        if summary["memory"]["fast_kb"]:
            result += f", {summary['memory']['fast_kb']}KB Fast"
        result += "\n"

        if summary["floppies"]:
            result += "Floppies:\n"
            for floppy in summary["floppies"]:
                result += f"  {floppy['drive']}: {floppy['image']}\n"

        if summary["kickstart"]:
            result += f"Kickstart: {summary['kickstart']}\n"

        result += (
            f"Graphics: {summary['graphics']['width']}x{summary['graphics']['height']}"
        )
        if summary["graphics"]["fullscreen"]:
            result += " (fullscreen)"
        result += "\n"

        if arguments.get("include_raw", False):
            result += "\n=== Raw Configuration ===\n"
            result += json.dumps(config, indent=2)

        return _text_result(result)
    except Exception as e:
        return _text_result(f"Error parsing config: {str(e)}")


async def _handle_modify_config(arguments: Any) -> list:
    """Handle modify_config tool."""
    config_name = arguments["config_name"]
    modifications = arguments["modifications"]
    config_path = _find_config_path(config_name)

    if not config_path:
        return _text_result(f"Error: Configuration '{config_name}' not found")

    try:
        await asyncio.to_thread(modify_uae_config, config_path, modifications)

        result = f"Modified configuration: {config_name}\n\n"
        result += "Changes applied:\n"
        for key, value in modifications.items():
            if value is None:
                result += f"  - Removed: {key}\n"
            else:
                result += f"  - {key} = {value}\n"

        return _text_result(result)
    except Exception as e:
        return _text_result(f"Error modifying config: {str(e)}")


async def _handle_create_config(arguments: Any) -> list:
    """Handle create_config tool."""
    config_name = arguments["config_name"]
    template = arguments.get("template", "A500")
    overrides = arguments.get("overrides", {})

    if not config_name.endswith(".uae"):
        config_name += ".uae"

    config_path = (CONFIG_DIR / config_name).resolve()
    if not config_path.is_relative_to(CONFIG_DIR.resolve()):
        return _text_result(f"Error: Invalid config name '{config_name}'")

    if config_path.exists():
        return _text_result(
            f"Error: Configuration '{config_name}' already exists. Use modify_config to update it."
        )

    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(
            lambda: create_config_from_template(config_path, template, overrides)
        )

        result = f"Created configuration: {config_name}\n"
        result += f"Based on template: {template}\n"
        result += f"Path: {config_path}\n"

        if overrides:
            result += "\nCustom overrides:\n"
            for key, value in overrides.items():
                result += f"  - {key} = {value}\n"

        return _text_result(result)
    except ValueError as e:
        return _text_result(f"Error: {str(e)}")
    except Exception as e:
        return _text_result(f"Error creating config: {str(e)}")


async def _handle_launch_whdload(arguments: Any) -> list:
    """Handle launch_whdload tool."""

    search_term = arguments.get("search_term", "").lower()
    exact_path = arguments.get("exact_path")
    model = arguments.get("model", "A1200")  # A1200 is typical for WHDLoad

    lha_path = None

    if exact_path:
        lha_path = Path(exact_path)
        if not lha_path.exists():
            return _text_result(f"Error: LHA file not found: {exact_path}")
    elif search_term:
        # Search for the LHA file using scan_disk_images for consistency
        def _scan_lha_files():
            images = scan_disk_images(DISK_IMAGE_DIRS, "lha", search_term)
            return [Path(img["path"]) for img in images]

        lha_files = await asyncio.to_thread(_scan_lha_files)

        if not lha_files:
            return _text_result(f"No WHDLoad games found matching '{search_term}'")

        if len(lha_files) > 1:
            result = f"Found {len(lha_files)} matches for '{search_term}':\n\n"
            for lha in sorted(lha_files, key=lambda x: x.name.lower())[:10]:
                result += f"- {lha.name}\n  {lha}\n"
            if len(lha_files) > 10:
                result += f"\n... and {len(lha_files) - 10} more"
            result += (
                "\n\nPlease specify exact_path or use a more specific search term."
            )
            return _text_result(result)

        lha_path = lha_files[0]
    else:
        return _text_result(
            "Error: Either 'search_term' or 'exact_path' must be specified"
        )

    cmd = build_launch_command(
        model=model,
        lha_file=str(lha_path),
        autostart=arguments.get("autostart", True),
    )

    try:
        proc = _launch_and_store(cmd)

        return _text_result(
            f"Launched WHDLoad game: {lha_path.name}\nModel: {model}\nPID: {proc.pid}"
        )
    except Exception as e:
        return _text_result(f"Error launching WHDLoad game: {str(e)}")


async def _handle_launch_cd(arguments: Any) -> list:
    """Handle launch_cd tool."""

    cd_image = arguments.get("cd_image")
    search_term = arguments.get("search_term", "").lower()
    model = arguments.get("model", "CD32")

    cd_path = None

    if cd_image:
        cd_path = Path(cd_image)
        if not cd_path.exists():
            return _text_result(f"Error: CD image not found: {cd_image}")
    elif search_term:
        # Search for CD images using scan_disk_images for consistency
        def _scan_cd_files():
            search_dirs = DISK_IMAGE_DIRS + [AMIBERRY_HOME / "CD"]
            if IS_MACOS:
                search_dirs.append(AMIBERRY_HOME / "CDs")
            images = scan_disk_images(search_dirs, "cd", search_term)
            return [Path(img["path"]) for img in images]

        cd_files = await asyncio.to_thread(_scan_cd_files)

        if not cd_files:
            return _text_result(f"No CD images found matching '{search_term}'")

        if len(cd_files) > 1:
            result = f"Found {len(cd_files)} CD images matching '{search_term}':\n\n"
            for cd in sorted(cd_files, key=lambda x: x.name.lower())[:10]:
                result += f"- {cd.name}\n  {cd}\n"
            if len(cd_files) > 10:
                result += f"\n... and {len(cd_files) - 10} more"
            result += (
                "\n\nPlease specify cd_image path or use a more specific search term."
            )
            return _text_result(result)

        cd_path = cd_files[0]
    else:
        return _text_result(
            "Error: Either 'cd_image' or 'search_term' must be specified"
        )

    cmd = build_launch_command(
        model=model,
        cd_image=str(cd_path),
        autostart=arguments.get("autostart", True),
    )

    try:
        proc = _launch_and_store(cmd)

        return _text_result(
            f"Launched CD image: {cd_path.name}\nModel: {model}\nPID: {proc.pid}"
        )
    except Exception as e:
        return _text_result(f"Error launching CD image: {str(e)}")


async def _handle_set_disk_swapper(arguments: Any) -> list:
    """Handle set_disk_swapper tool."""

    disk_images = arguments.get("disk_images", [])

    if not disk_images:
        return _text_result("Error: No disk images specified")

    if len(disk_images) < 2:
        return _text_result("Error: Disk swapper requires at least 2 disk images")

    # Verify all disk images exist
    verified_paths = []
    for img in disk_images:
        img_path = Path(img)
        if not img_path.exists():
            return _text_result(f"Error: Disk image not found: {img}")
        verified_paths.append(str(img_path))

    model = arguments.get("model")
    config = arguments.get("config")

    # Resolve config path if specified
    config_path = None
    if config:
        config_path = _find_config_path(config)
        if not config_path:
            return _text_result(f"Error: Configuration '{config}' not found")

    cmd = build_launch_command(
        model=model or ("A500" if not config else None),
        config_path=config_path,
        disk_image=verified_paths[0],
        disk_swapper=verified_paths,
        autostart=arguments.get("autostart", True),
    )

    try:
        proc = _launch_and_store(cmd)

        result = f"Launched with disk swapper ({len(verified_paths)} disks):\n"
        result += f"  PID: {proc.pid}\n"
        for i, path in enumerate(verified_paths):
            result += f"  Disk {i + 1}: {Path(path).name}\n"

        return _text_result(result)
    except Exception as e:
        return _text_result(f"Error launching with disk swapper: {str(e)}")


async def _handle_list_cd_images(arguments: Any) -> list:
    """Handle list_cd_images tool."""
    search_term = arguments.get("search_term", "")

    search_dirs = DISK_IMAGE_DIRS + [AMIBERRY_HOME / "CD"]
    if IS_MACOS:
        search_dirs.append(AMIBERRY_HOME / "CDs")

    cd_files = await asyncio.to_thread(scan_disk_images, search_dirs, "cd", search_term)

    if not cd_files:
        msg = "No CD images found"
        if search_term:
            msg += f' matching "{search_term}"'
        return _text_result(f"{msg}.")

    result = f"Found {len(cd_files)} CD image(s):\n\n"
    for cd in cd_files:
        result += f"- {cd['name']} ({cd['type']})\n  {cd['path']}\n"

    return _text_result(result)


async def _handle_get_log_content(arguments: Any) -> list:
    """Handle get_log_content tool."""
    log_name = arguments["log_name"]
    tail_lines = arguments.get("tail_lines")
    try:
        log_path = normalize_log_path(log_name)
    except ValueError:
        return _text_result(f"Error: Invalid log name '{log_name}'")

    if not log_path.exists():
        return _text_result(f"Error: Log file not found: {log_name}")

    try:
        content = await asyncio.to_thread(log_path.read_text, errors="replace")

        if tail_lines and tail_lines > 0:
            lines = content.splitlines()
            content = "\n".join(lines[-tail_lines:])
            result = (
                f"Last {min(tail_lines, len(lines))} lines of {log_name}:\n\n{content}"
            )
        else:
            result = f"Log file: {log_name}\n\n{content}"

        return _text_result(result)
    except Exception as e:
        return _text_result(f"Error reading log: {str(e)}")


async def _handle_list_logs(arguments: Any) -> list:
    """Handle list_logs tool."""

    def _scan_logs():
        if not LOG_DIR.exists():
            return None
        result = []
        for log in LOG_DIR.glob("*.log"):
            try:
                st = log.stat()
            except OSError:
                continue
            timestamp = format_log_timestamp(st.st_mtime)
            result.append(
                {
                    "name": log.name,
                    "modified": timestamp,
                    "size": st.st_size,
                }
            )
        return result

    logs = await asyncio.to_thread(_scan_logs)

    if logs is None:
        return _text_result("No log directory found.")

    if not logs:
        return _text_result("No log files found.")

    result = f"Found {len(logs)} log file(s):\n\n"
    for log in sorted(logs, key=lambda x: x["modified"], reverse=True):
        size_str = f"{log['size']} bytes"
        if log["size"] > 1024:
            size_str = f"{log['size'] / 1024:.1f} KB"
        result += f"- {log['name']}\n  Modified: {log['modified']} ({size_str})\n"

    return _text_result(result)


# Phase 2 tools


async def _handle_inspect_savestate(arguments: Any) -> list:
    """Handle inspect_savestate tool."""
    savestate_path = arguments["savestate_path"]

    # If just a filename, look in default savestates directory
    path = Path(savestate_path)
    if not path.is_absolute():
        path = SAVESTATE_DIR / savestate_path

    if not path.exists():
        return _text_result(f"Error: Savestate not found: {savestate_path}")

    try:
        metadata = await asyncio.to_thread(inspect_savestate, path)
        summary = get_savestate_summary(metadata)

        result = summary + "\n\n"
        result += f"Chunks: {', '.join(metadata.get('chunks', []))}"

        return _text_result(result)
    except ValueError as e:
        return _text_result(f"Error: {str(e)}")
    except Exception as e:
        return _text_result(f"Error inspecting savestate: {str(e)}")


async def _handle_list_roms(arguments: Any) -> list:
    """Handle list_roms tool."""
    directory = arguments.get("directory")

    if directory:
        rom_dir = Path(directory)
    else:
        rom_dir = ROM_DIR

    if not rom_dir.exists():
        return _text_result(
            f"ROM directory not found: {rom_dir}\n\nCreate this directory and add your Kickstart ROMs."
        )

    try:
        roms = await asyncio.to_thread(scan_rom_directory, rom_dir)

        if not roms:
            return _text_result(
                f"No ROM files found in {rom_dir}\n\nAdd Kickstart ROM files (.rom, .bin) to this directory."
            )

        result = f"Found {len(roms)} ROM file(s) in {rom_dir}:\n\n"
        for rom in sorted(roms, key=lambda x: x.get("filename", "").lower()):
            if rom.get("error"):
                result += f"- {rom['filename']}: Error - {rom['error']}\n"
            elif rom.get("identified"):
                result += f"- {rom['filename']}\n"
                result += f"  Kickstart {rom['version']} (Rev {rom['revision']})\n"
                result += f"  Model: {rom['model']}\n"
                result += f"  CRC32: {rom['crc32']}\n"
            else:
                result += f"- {rom['filename']}\n"
                result += f"  {rom.get('probable_type', 'Unknown type')}\n"
                result += f"  CRC32: {rom['crc32']}\n"

        return _text_result(result)
    except Exception as e:
        return _text_result(f"Error scanning ROMs: {str(e)}")


async def _handle_identify_rom(arguments: Any) -> list:
    """Handle identify_rom tool."""
    rom_path = arguments["rom_path"]
    path = Path(rom_path)

    if not path.exists():
        return _text_result(f"Error: ROM file not found: {rom_path}")

    try:
        rom_info = await asyncio.to_thread(identify_rom, path)
        summary = get_rom_summary(rom_info)

        return _text_result(summary)
    except Exception as e:
        return _text_result(f"Error identifying ROM: {str(e)}")


async def _handle_get_amiberry_version(arguments: Any) -> list:
    """Handle get_amiberry_version tool."""
    version_info = await detect_amiberry_version()

    result_text = f"Amiberry Binary: {version_info['binary']}\n"
    if version_info.get("available"):
        if version_info.get("version_line"):
            result_text += f"Version: {version_info['version_line']}\n"
        result_text += "Status: Available\n"
        features = version_info.get("features", [])
        if features:
            result_text += f"Features detected: {', '.join(features)}"
    elif version_info.get("error"):
        result_text += f"Status: {version_info['error']}"
    else:
        result_text += "Status: Not available"

    return _text_result(result_text)


# Runtime control tools (IPC)


async def _handle_pause_emulation(arguments: Any) -> list:
    """Handle pause_emulation tool."""
    return await _ipc_bool_call(
        "pause",
        success_msg="Emulation paused.",
        failure_msg="Failed to pause emulation.",
    )


async def _handle_resume_emulation(arguments: Any) -> list:
    """Handle resume_emulation tool."""
    return await _ipc_bool_call(
        "resume",
        success_msg="Emulation resumed.",
        failure_msg="Failed to resume emulation.",
    )


async def _handle_reset_emulation(arguments: Any) -> list:
    """Handle reset_emulation tool."""
    hard = arguments.get("hard", False)

    async def _cb(client):
        success = await client.reset(hard=hard)
        reset_type = "hard" if hard else "soft"
        if success:
            return f"Emulation {reset_type} reset performed."
        else:
            return f"Failed to perform {reset_type} reset."

    return await _ipc_call(_cb)


async def _handle_runtime_screenshot(arguments: Any) -> list:
    """Handle runtime_screenshot tool."""
    filename = arguments["filename"]
    return await _ipc_bool_call(
        "screenshot",
        filename,
        success_msg=f"Screenshot saved to: {filename}",
        failure_msg="Failed to take screenshot.",
    )


async def _handle_runtime_save_state(arguments: Any) -> list:
    """Handle runtime_save_state tool."""
    state_file = arguments["state_file"]
    config_file = arguments["config_file"]
    return await _ipc_bool_call(
        "save_state",
        state_file,
        config_file,
        success_msg=f"State saved:\n  State: {state_file}\n  Config: {config_file}",
        failure_msg="Failed to save state.",
    )


async def _handle_runtime_load_state(arguments: Any) -> list:
    """Handle runtime_load_state tool."""
    state_file = arguments["state_file"]
    return await _ipc_bool_call(
        "load_state",
        state_file,
        success_msg=f"Loading state: {state_file}",
        failure_msg="Failed to load state.",
    )


async def _handle_runtime_insert_floppy(arguments: Any) -> list:
    """Handle runtime_insert_floppy tool."""
    drive = arguments["drive"]
    image_path = arguments["image_path"]
    return await _ipc_bool_call(
        "insert_floppy",
        drive,
        image_path,
        success_msg=f"Inserted {Path(image_path).name} into DF{drive}:",
        failure_msg=f"Failed to insert disk into DF{drive}:.",
    )


async def _handle_runtime_insert_cd(arguments: Any) -> list:
    """Handle runtime_insert_cd tool."""
    image_path = arguments["image_path"]
    return await _ipc_bool_call(
        "insert_cd",
        image_path,
        success_msg=f"Inserted CD: {Path(image_path).name}",
        failure_msg="Failed to insert CD.",
    )


async def _handle_get_runtime_status(arguments: Any) -> list:
    """Handle get_runtime_status tool."""

    async def _cb(client):
        status = await client.get_status()

        result = "Amiberry Runtime Status:\n\n"
        result += f"  Paused: {status.get('Paused', 'Unknown')}\n"
        result += f"  Config: {status.get('Config', 'Unknown')}\n"

        # Show mounted floppies
        for i in range(4):
            key = f"Floppy{i}"
            if key in status:
                result += f"  DF{i}: {status[key]}\n"

        return result

    return await _ipc_call(_cb)


async def _handle_runtime_get_config(arguments: Any) -> list:
    """Handle runtime_get_config tool."""
    option = arguments["option"]

    async def _cb(client):
        value = await client.get_config(option)
        if value is not None:
            return f"{option} = {value}"
        else:
            return f"Unknown or unavailable option: {option}"

    return await _ipc_call(_cb)


async def _handle_runtime_set_config(arguments: Any) -> list:
    """Handle runtime_set_config tool."""
    option = arguments["option"]
    value = arguments["value"]
    return await _ipc_bool_call(
        "set_config",
        option,
        value,
        success_msg=f"Set {option} = {value}",
        failure_msg=f"Failed to set {option}. Unknown option or invalid value.",
    )


async def _handle_check_ipc_connection(arguments: Any) -> list:
    """Handle check_ipc_connection tool."""
    try:
        client = _get_ipc_client()

        result = "Amiberry IPC Connection Check:\n\n"
        result += f"  Transport: {client.transport}\n"
        result += f"  Socket available: {client.is_available()}\n"

        if client.is_available():
            # Try to get status to verify connection works
            try:
                status = await client.get_status()
                result += "  Connection: OK\n"
                result += f"  Emulation paused: {status.get('Paused', 'Unknown')}\n"
            except Exception as e:
                result += f"  Connection: Failed ({str(e)})\n"
        else:
            result += "  Connection: Not available\n"
            result += "\nAmiberry may not be running, or was not built with USE_IPC_SOCKET=ON."

        return _text_result(result)
    except Exception as e:
        return _text_result(f"Error checking IPC: {str(e)}")


# New runtime control tools


async def _handle_runtime_eject_floppy(arguments: Any) -> list:
    """Handle runtime_eject_floppy tool."""
    drive = arguments["drive"]
    return await _ipc_bool_call(
        "eject_floppy",
        drive,
        success_msg=f"Ejected disk from DF{drive}:",
        failure_msg=f"Failed to eject disk from DF{drive}:.",
    )


async def _handle_runtime_eject_cd(arguments: Any) -> list:
    """Handle runtime_eject_cd tool."""
    return await _ipc_bool_call(
        "eject_cd", success_msg="CD ejected.", failure_msg="Failed to eject CD."
    )


async def _handle_runtime_list_floppies(arguments: Any) -> list:
    """Handle runtime_list_floppies tool."""

    async def _cb(client):
        drives = await client.list_floppies()

        result = "Floppy Drives:\n\n"
        for drive, path in sorted(drives.items()):
            result += f"  {drive}: {path}\n"

        return result

    return await _ipc_call(_cb)


async def _handle_runtime_list_configs(arguments: Any) -> list:
    """Handle runtime_list_configs tool."""

    async def _cb(client):
        configs = await client.list_configs()

        if not configs:
            return "No configuration files found."

        result = f"Found {len(configs)} configuration file(s):\n\n"
        for cfg in sorted(configs):
            result += f"  - {cfg}\n"

        return result

    return await _ipc_call(_cb)


async def _handle_runtime_set_volume(arguments: Any) -> list:
    """Handle runtime_set_volume tool."""
    volume = arguments["volume"]
    return await _ipc_bool_call(
        "set_volume",
        volume,
        success_msg=f"Volume set to {volume}%",
        failure_msg="Failed to set volume.",
    )


async def _handle_runtime_get_volume(arguments: Any) -> list:
    """Handle runtime_get_volume tool."""

    async def _cb(client):
        volume = await client.get_volume()
        if volume is not None:
            return f"Current volume: {volume}%"
        else:
            return "Failed to get volume."

    return await _ipc_call(_cb)


async def _handle_runtime_mute(arguments: Any) -> list:
    """Handle runtime_mute tool."""
    return await _ipc_bool_call(
        "mute", success_msg="Audio muted.", failure_msg="Failed to mute audio."
    )


async def _handle_runtime_unmute(arguments: Any) -> list:
    """Handle runtime_unmute tool."""
    return await _ipc_bool_call(
        "unmute", success_msg="Audio unmuted.", failure_msg="Failed to unmute audio."
    )


async def _handle_runtime_toggle_fullscreen(arguments: Any) -> list:
    """Handle runtime_toggle_fullscreen tool."""
    return await _ipc_bool_call(
        "toggle_fullscreen",
        success_msg="Fullscreen mode toggled.",
        failure_msg="Failed to toggle fullscreen.",
    )


async def _handle_runtime_set_warp(arguments: Any) -> list:
    """Handle runtime_set_warp tool."""
    enabled = arguments["enabled"]

    async def _cb(client):
        success = await client.set_warp(enabled)
        if success:
            status = "enabled" if enabled else "disabled"
            return f"Warp mode {status}."
        else:
            action = "enable" if enabled else "disable"
            return f"Failed to {action} warp mode."

    return await _ipc_call(_cb)


async def _handle_runtime_get_warp(arguments: Any) -> list:
    """Handle runtime_get_warp tool."""

    async def _cb(client):
        enabled = await client.get_warp()
        if enabled is not None:
            status = "enabled" if enabled else "disabled"
            return f"Warp mode is {status}."
        else:
            return "Failed to get warp mode status."

    return await _ipc_call(_cb)


async def _handle_runtime_get_version(arguments: Any) -> list:
    """Handle runtime_get_version tool."""

    async def _cb(client):
        info = await client.get_version()

        result = "Amiberry Version Info:\n\n"
        for key, value in info.items():
            result += f"  {key}: {value}\n"

        return result

    return await _ipc_call(_cb)


async def _handle_runtime_frame_advance(arguments: Any) -> list:
    """Handle runtime_frame_advance tool."""
    frames = arguments.get("frames", 1)
    return await _ipc_bool_call(
        "frame_advance",
        frames,
        success_msg=f"Advanced {frames} frame(s).",
        failure_msg="Failed to advance frames. Is emulation paused?",
    )


async def _handle_runtime_send_mouse(arguments: Any) -> list:
    """Handle runtime_send_mouse tool."""
    dx = arguments["dx"]
    dy = arguments["dy"]
    buttons = arguments.get("buttons", 0)
    return await _ipc_bool_call(
        "send_mouse",
        dx,
        dy,
        buttons,
        success_msg=f"Mouse input sent: dx={dx}, dy={dy}, buttons={buttons}",
        failure_msg="Failed to send mouse input.",
    )


async def _handle_runtime_set_mouse_speed(arguments: Any) -> list:
    """Handle runtime_set_mouse_speed tool."""
    speed = arguments["speed"]
    return await _ipc_bool_call(
        "set_mouse_speed",
        speed,
        success_msg=f"Mouse speed set to {speed}.",
        failure_msg="Failed to set mouse speed.",
    )

async def _handle_runtime_send_key(arguments: Any) -> list:
    """Handle runtime_send_key tool."""
    key = arguments["key"]
    state = arguments.get("state", "press_and_release")

    try:
        keycode = resolve_key_name(key)
    except ValueError as e:
        return _text_result(str(e))

    async def _cb(client):
        if state == "press":
            success = await client.send_key(keycode, True)
            action = "pressed"
        elif state == "release":
            success = await client.send_key(keycode, False)
            action = "released"
        else:  # press_and_release
            success = await client.send_key(keycode, True)
            if success:
                import asyncio
                await asyncio.sleep(0.05)
                success = await client.send_key(keycode, False)
            action = "pressed and released"

        if success:
            return f"Key '{key}' (scancode 0x{keycode:02X}) {action}."
        else:
            return f"Failed to send key '{key}'."

    return await _ipc_call(_cb)


async def _handle_runtime_send_text(arguments: Any) -> list:
    """Handle runtime_send_text tool."""
    text = arguments["text"]
    delay_ms = arguments.get("delay_ms", 50)
    delay = delay_ms / 1000.0

    async def _cb(client):
        typed, skipped = await client.send_text(text, delay=delay)
        preview = text[:40] + ("..." if len(text) > 40 else "")
        preview = preview.replace("\n", "\\n").replace("\t", "\\t")
        msg = f"Typed {typed} character(s) from '{preview}'."
        if skipped:
            msg += f" ({skipped} unsupported character(s) skipped.)"
        return msg

    return await _ipc_call(_cb)

async def _handle_runtime_ping(arguments: Any) -> list:
    """Handle runtime_ping tool."""
    return await _ipc_bool_call(
        "ping",
        success_msg="PONG - Amiberry IPC connection is working.",
        failure_msg="Ping failed - no response from Amiberry.",
    )


async def _handle_set_active_instance(arguments: Any) -> list:
    """Handle set_active_instance tool."""

    instance = arguments.get("instance")
    _state.active_instance = instance
    status = (
        f"Active instance set to {instance}"
        if instance is not None
        else "Active instance set to auto-discover"
    )
    return _text_result(status)


async def _handle_get_active_instance(arguments: Any) -> list:
    """Handle get_active_instance tool."""
    if _state.active_instance is None:
        return _text_result("Auto-discovering (no specific instance set)")
    else:
        return _text_result(f"Active instance: {_state.active_instance}")


# Round 2 runtime control tools


async def _handle_runtime_quicksave(arguments: Any) -> list:
    """Handle runtime_quicksave tool."""
    slot = arguments.get("slot", 0)
    return await _ipc_bool_call(
        "quicksave",
        slot,
        success_msg=f"Quick saved to slot {slot}.",
        failure_msg=f"Failed to quick save to slot {slot}.",
    )


async def _handle_runtime_quickload(arguments: Any) -> list:
    """Handle runtime_quickload tool."""
    slot = arguments.get("slot", 0)
    return await _ipc_bool_call(
        "quickload",
        slot,
        success_msg=f"Quick loading from slot {slot}.",
        failure_msg=f"Failed to quick load from slot {slot}.",
    )


async def _handle_runtime_get_joyport_mode(arguments: Any) -> list:
    """Handle runtime_get_joyport_mode tool."""
    port = arguments["port"]

    async def _cb(client):
        result = await client.get_joyport_mode(port)
        if result:
            mode, mode_name = result
            return f"Port {port} mode: {mode} ({mode_name})"
        else:
            return f"Failed to get port {port} mode."

    return await _ipc_call(_cb)


async def _handle_runtime_set_joyport_mode(arguments: Any) -> list:
    """Handle runtime_set_joyport_mode tool."""
    port = arguments["port"]
    mode = arguments["mode"]
    return await _ipc_bool_call(
        "set_joyport_mode",
        port,
        mode,
        success_msg=f"Port {port} mode set to {mode}.",
        failure_msg=f"Failed to set port {port} mode.",
    )


async def _handle_runtime_get_autofire(arguments: Any) -> list:
    """Handle runtime_get_autofire tool."""
    port = arguments["port"]

    async def _cb(client):
        mode = await client.get_autofire(port)
        if mode is not None:
            modes = {
                0: "off",
                1: "normal",
                2: "toggle",
                3: "always",
                4: "toggle (no autofire)",
            }
            mode_name = modes.get(mode, "unknown")
            return f"Port {port} autofire: {mode} ({mode_name})"
        else:
            return f"Failed to get port {port} autofire mode."

    return await _ipc_call(_cb)


async def _handle_runtime_set_autofire(arguments: Any) -> list:
    """Handle runtime_set_autofire tool."""
    port = arguments["port"]
    mode = arguments["mode"]
    return await _ipc_bool_call(
        "set_autofire",
        port,
        mode,
        success_msg=f"Port {port} autofire set to {mode}.",
        failure_msg=f"Failed to set port {port} autofire.",
    )


async def _handle_runtime_get_led_status(arguments: Any) -> list:
    """Handle runtime_get_led_status tool."""

    async def _cb(client):
        status = await client.get_led_status()

        result = "LED Status:\n\n"
        for key, value in sorted(status.items()):
            result += f"  {key}: {value}\n"

        return result

    return await _ipc_call(_cb)


async def _handle_runtime_list_harddrives(arguments: Any) -> list:
    """Handle runtime_list_harddrives tool."""

    async def _cb(client):
        drives = await client.list_harddrives()

        if not drives or (
            len(drives) == 1 and "<no harddrives mounted>" in str(drives)
        ):
            return "No hard drives mounted."

        result = "Mounted Hard Drives:\n\n"
        for key, value in sorted(drives.items()):
            result += f"  {key}: {value}\n"

        return result

    return await _ipc_call(_cb)


async def _handle_runtime_set_display_mode(arguments: Any) -> list:
    """Handle runtime_set_display_mode tool."""
    mode = arguments["mode"]

    async def _cb(client):
        success = await client.set_display_mode(mode)
        modes = {0: "window", 1: "fullscreen", 2: "fullwindow"}
        if success:
            return f"Display mode set to {modes.get(mode, mode)}."
        else:
            return "Failed to set display mode."

    return await _ipc_call(_cb)


async def _handle_runtime_get_display_mode(arguments: Any) -> list:
    """Handle runtime_get_display_mode tool."""

    async def _cb(client):
        result = await client.get_display_mode()
        if result:
            mode, mode_name = result
            return f"Display mode: {mode} ({mode_name})"
        else:
            return "Failed to get display mode."

    return await _ipc_call(_cb)


async def _handle_runtime_set_ntsc(arguments: Any) -> list:
    """Handle runtime_set_ntsc tool."""
    enabled = arguments["enabled"]

    async def _cb(client):
        success = await client.set_ntsc(enabled)
        mode = "NTSC" if enabled else "PAL"
        if success:
            return f"Video mode set to {mode}."
        else:
            return f"Failed to set video mode to {mode}."

    return await _ipc_call(_cb)


async def _handle_runtime_get_ntsc(arguments: Any) -> list:
    """Handle runtime_get_ntsc tool."""

    async def _cb(client):
        result = await client.get_ntsc()
        if result:
            is_ntsc, mode_name = result
            return f"Video mode: {mode_name}"
        else:
            return "Failed to get video mode."

    return await _ipc_call(_cb)


async def _handle_runtime_set_sound_mode(arguments: Any) -> list:
    """Handle runtime_set_sound_mode tool."""
    mode = arguments["mode"]

    async def _cb(client):
        success = await client.set_sound_mode(mode)
        modes = {0: "off", 1: "normal", 2: "stereo", 3: "best"}
        if success:
            return f"Sound mode set to {modes.get(mode, mode)}."
        else:
            return "Failed to set sound mode."

    return await _ipc_call(_cb)


async def _handle_runtime_get_sound_mode(arguments: Any) -> list:
    """Handle runtime_get_sound_mode tool."""

    async def _cb(client):
        result = await client.get_sound_mode()
        if result:
            mode, mode_name = result
            return f"Sound mode: {mode} ({mode_name})"
        else:
            return "Failed to get sound mode."

    return await _ipc_call(_cb)


# Round 3 runtime control tools


async def _handle_runtime_toggle_mouse_grab(arguments: Any) -> list:
    """Handle runtime_toggle_mouse_grab tool."""
    return await _ipc_bool_call(
        "toggle_mouse_grab",
        success_msg="Mouse grab toggled.",
        failure_msg="Failed to toggle mouse grab.",
    )


async def _handle_runtime_get_mouse_speed(arguments: Any) -> list:
    """Handle runtime_get_mouse_speed tool."""

    async def _cb(client):
        speed = await client.get_mouse_speed()
        if speed is not None:
            return f"Mouse speed: {speed}"
        else:
            return "Failed to get mouse speed."

    return await _ipc_call(_cb)


async def _handle_runtime_set_cpu_speed(arguments: Any) -> list:
    """Handle runtime_set_cpu_speed tool."""
    speed = arguments["speed"]
    return await _ipc_bool_call(
        "set_cpu_speed",
        speed,
        success_msg=f"CPU speed set to {speed}.",
        failure_msg="Failed to set CPU speed.",
    )


async def _handle_runtime_get_cpu_speed(arguments: Any) -> list:
    """Handle runtime_get_cpu_speed tool."""

    async def _cb(client):
        result = await client.get_cpu_speed()
        if result:
            speed, desc = result
            return f"CPU speed: {speed} ({desc})"
        else:
            return "Failed to get CPU speed."

    return await _ipc_call(_cb)


async def _handle_runtime_toggle_rtg(arguments: Any) -> list:
    """Handle runtime_toggle_rtg tool."""
    monid = arguments.get("monid", 0)

    async def _cb(client):
        result = await client.toggle_rtg(monid)
        if result:
            return f"Display mode: {result}"
        else:
            return "Failed to toggle RTG."

    return await _ipc_call(_cb)


async def _handle_runtime_set_floppy_speed(arguments: Any) -> list:
    """Handle runtime_set_floppy_speed tool."""
    speed = arguments["speed"]

    async def _cb(client):
        success = await client.set_floppy_speed(speed)
        if success:
            desc = {0: "turbo", 100: "1x", 200: "2x", 400: "4x", 800: "8x"}.get(
                speed, str(speed)
            )
            return f"Floppy speed set to {speed} ({desc})."
        else:
            return "Failed to set floppy speed."

    return await _ipc_call(_cb)


async def _handle_runtime_get_floppy_speed(arguments: Any) -> list:
    """Handle runtime_get_floppy_speed tool."""

    async def _cb(client):
        result = await client.get_floppy_speed()
        if result:
            speed, desc = result
            return f"Floppy speed: {speed} ({desc})"
        else:
            return "Failed to get floppy speed."

    return await _ipc_call(_cb)


async def _handle_runtime_disk_write_protect(arguments: Any) -> list:
    """Handle runtime_disk_write_protect tool."""
    drive = arguments["drive"]
    protect = arguments["protect"]

    async def _cb(client):
        success = await client.disk_write_protect(drive, protect)
        if success:
            status = "protected" if protect else "writable"
            return f"Drive DF{drive} set to {status}."
        else:
            return "Failed to set write protection."

    return await _ipc_call(_cb)


async def _handle_runtime_get_disk_write_protect(arguments: Any) -> list:
    """Handle runtime_get_disk_write_protect tool."""
    drive = arguments["drive"]

    async def _cb(client):
        result = await client.get_disk_write_protect(drive)
        if result:
            is_protected, status = result
            return f"Drive DF{drive}: {status}"
        else:
            return "Failed to get write protection status."

    return await _ipc_call(_cb)


async def _handle_runtime_toggle_status_line(arguments: Any) -> list:
    """Handle runtime_toggle_status_line tool."""

    async def _cb(client):
        result = await client.toggle_status_line()
        if result:
            mode, mode_name = result
            return f"Status line: {mode_name}"
        else:
            return "Failed to toggle status line."

    return await _ipc_call(_cb)


async def _handle_runtime_set_chipset(arguments: Any) -> list:
    """Handle runtime_set_chipset tool."""
    chipset = arguments["chipset"]

    async def _cb(client):
        success = await client.set_chipset(chipset)
        if success:
            return f"Chipset set to {chipset}."
        else:
            return "Failed to set chipset."

    return await _ipc_call(_cb)


async def _handle_runtime_get_chipset(arguments: Any) -> list:
    """Handle runtime_get_chipset tool."""

    async def _cb(client):
        result = await client.get_chipset()
        if result:
            mask, name = result
            return f"Chipset: {name} (mask={mask})"
        else:
            return "Failed to get chipset."

    return await _ipc_call(_cb)


async def _handle_runtime_get_memory_config(arguments: Any) -> list:
    """Handle runtime_get_memory_config tool."""

    async def _cb(client):
        config = await client.get_memory_config()
        result = "Memory configuration:\n"
        for key, value in config.items():
            result += f"  {key}: {value}\n"
        return result

    return await _ipc_call(_cb)


async def _handle_runtime_get_fps(arguments: Any) -> list:
    """Handle runtime_get_fps tool."""

    async def _cb(client):
        info = await client.get_fps()
        result = "Performance info:\n"
        for key, value in info.items():
            result += f"  {key}: {value}\n"
        return result

    return await _ipc_call(_cb)


# Round 4 runtime control tools - Memory and Window Control


async def _handle_runtime_set_chip_mem(arguments: Any) -> list:
    """Handle runtime_set_chip_mem tool."""
    size_kb = arguments["size_kb"]

    async def _cb(client):
        success = await client.set_chip_mem(size_kb)
        if success:
            return f"Chip RAM set to {size_kb} KB. Reset required for changes to take effect."
        else:
            return "Failed to set Chip RAM size."

    return await _ipc_call(_cb)


async def _handle_runtime_set_fast_mem(arguments: Any) -> list:
    """Handle runtime_set_fast_mem tool."""
    size_kb = arguments["size_kb"]

    async def _cb(client):
        success = await client.set_fast_mem(size_kb)
        if success:
            return f"Fast RAM set to {size_kb} KB. Reset required for changes to take effect."
        else:
            return "Failed to set Fast RAM size."

    return await _ipc_call(_cb)


async def _handle_runtime_set_slow_mem(arguments: Any) -> list:
    """Handle runtime_set_slow_mem tool."""
    size_kb = arguments["size_kb"]

    async def _cb(client):
        success = await client.set_slow_mem(size_kb)
        if success:
            return f"Slow RAM set to {size_kb} KB. Reset required for changes to take effect."
        else:
            return "Failed to set Slow RAM size."

    return await _ipc_call(_cb)


async def _handle_runtime_set_z3_mem(arguments: Any) -> list:
    """Handle runtime_set_z3_mem tool."""
    size_mb = arguments["size_mb"]

    async def _cb(client):
        success = await client.set_z3_mem(size_mb)
        if success:
            return f"Z3 Fast RAM set to {size_mb} MB. Reset required for changes to take effect."
        else:
            return "Failed to set Z3 Fast RAM size."

    return await _ipc_call(_cb)


async def _handle_runtime_get_cpu_model(arguments: Any) -> list:
    """Handle runtime_get_cpu_model tool."""

    async def _cb(client):
        info = await client.get_cpu_model()
        result = "CPU Model:\n"
        for key, value in info.items():
            result += f"  {key}: {value}\n"
        return result

    return await _ipc_call(_cb)


async def _handle_runtime_set_cpu_model(arguments: Any) -> list:
    """Handle runtime_set_cpu_model tool."""
    model = arguments["model"]

    async def _cb(client):
        success = await client.set_cpu_model(model)
        if success:
            return (
                f"CPU model set to {model}. Reset required for changes to take effect."
            )
        else:
            return "Failed to set CPU model."

    return await _ipc_call(_cb)


async def _handle_runtime_set_window_size(arguments: Any) -> list:
    """Handle runtime_set_window_size tool."""
    width = arguments["width"]
    height = arguments["height"]

    async def _cb(client):
        success = await client.set_window_size(width, height)
        if success:
            return f"Window size set to {width}x{height}."
        else:
            return "Failed to set window size."

    return await _ipc_call(_cb)


async def _handle_runtime_get_window_size(arguments: Any) -> list:
    """Handle runtime_get_window_size tool."""

    async def _cb(client):
        info = await client.get_window_size()
        width = info.get("width", "?")
        height = info.get("height", "?")
        return f"Window size: {width}x{height}"

    return await _ipc_call(_cb)


async def _handle_runtime_set_scaling(arguments: Any) -> list:
    """Handle runtime_set_scaling tool."""
    mode = arguments["mode"]
    mode_names = ["auto", "nearest", "linear", "integer"]

    async def _cb(client):
        success = await client.set_scaling(mode)
        if success:
            mode_index = mode + 1  # -1..2 -> 0..3
            mode_name = (
                mode_names[mode_index]
                if 0 <= mode_index < len(mode_names)
                else str(mode)
            )
            return f"Scaling mode set to {mode_name}."
        else:
            return "Failed to set scaling mode."

    return await _ipc_call(_cb)


async def _handle_runtime_get_scaling(arguments: Any) -> list:
    """Handle runtime_get_scaling tool."""

    async def _cb(client):
        info = await client.get_scaling()
        result = "Scaling:\n"
        for key, value in info.items():
            result += f"  {key}: {value}\n"
        return result

    return await _ipc_call(_cb)


async def _handle_runtime_set_line_mode(arguments: Any) -> list:
    """Handle runtime_set_line_mode tool."""
    mode = arguments["mode"]
    mode_names = ["single", "double", "scanlines"]

    async def _cb(client):
        success = await client.set_line_mode(mode)
        if success:
            mode_name = mode_names[mode] if 0 <= mode < len(mode_names) else str(mode)
            return f"Line mode set to {mode_name}."
        else:
            return "Failed to set line mode."

    return await _ipc_call(_cb)


async def _handle_runtime_get_line_mode(arguments: Any) -> list:
    """Handle runtime_get_line_mode tool."""

    async def _cb(client):
        info = await client.get_line_mode()
        result = "Line mode:\n"
        for key, value in info.items():
            result += f"  {key}: {value}\n"
        return result

    return await _ipc_call(_cb)


async def _handle_runtime_set_resolution(arguments: Any) -> list:
    """Handle runtime_set_resolution tool."""
    mode = arguments["mode"]
    mode_names = ["lores", "hires", "superhires"]

    async def _cb(client):
        success = await client.set_resolution(mode)
        if success:
            mode_name = mode_names[mode] if 0 <= mode < len(mode_names) else str(mode)
            return f"Resolution set to {mode_name}."
        else:
            return "Failed to set resolution."

    return await _ipc_call(_cb)


async def _handle_runtime_get_resolution(arguments: Any) -> list:
    """Handle runtime_get_resolution tool."""

    async def _cb(client):
        result = await client.get_resolution()
        if result:
            mode, mode_name = result
            return f"Resolution: {mode_name} ({mode})"
        else:
            return "Failed to get resolution."

    return await _ipc_call(_cb)


# Round 5 - Autocrop and WHDLoad


async def _handle_runtime_set_autocrop(arguments: Any) -> list:
    """Handle runtime_set_autocrop tool."""
    enabled = arguments["enabled"]
    return await _ipc_bool_call(
        "set_autocrop",
        enabled,
        success_msg=f"Autocrop {'enabled' if enabled else 'disabled'}.",
        failure_msg="Failed to set autocrop.",
    )


async def _handle_runtime_get_autocrop(arguments: Any) -> list:
    """Handle runtime_get_autocrop tool."""

    async def _cb(client):
        result = await client.get_autocrop()
        if result is not None:
            return f"Autocrop: {'enabled' if result else 'disabled'}"
        else:
            return "Failed to get autocrop status."

    return await _ipc_call(_cb)


async def _handle_runtime_insert_whdload(arguments: Any) -> list:
    """Handle runtime_insert_whdload tool."""
    path = arguments["path"]
    return await _ipc_bool_call(
        "insert_whdload",
        path,
        success_msg=f"WHDLoad game loaded: {path}\nNote: A reset may be required for the game to start.",
        failure_msg="Failed to load WHDLoad game.",
    )


async def _handle_runtime_eject_whdload(arguments: Any) -> list:
    """Handle runtime_eject_whdload tool."""
    return await _ipc_bool_call(
        "eject_whdload",
        success_msg="WHDLoad game ejected.",
        failure_msg="Failed to eject WHDLoad game.",
    )


async def _handle_runtime_get_whdload(arguments: Any) -> list:
    """Handle runtime_get_whdload tool."""

    async def _cb(client):
        info = await client.get_whdload()
        if info:
            if info.get("loaded") == "0":
                return "No WHDLoad game loaded."
            result = "WHDLoad game:\n"
            for key, value in info.items():
                if value:  # Only show non-empty values
                    result += f"  {key}: {value}\n"
            return result
        else:
            return "Failed to get WHDLoad info."

    return await _ipc_call(_cb)


# Round 6 - Debugging and Diagnostics


async def _handle_runtime_debug_activate(arguments: Any) -> list:
    """Handle runtime_debug_activate tool."""
    return await _ipc_bool_call(
        "debug_activate",
        success_msg="Debugger activated.",
        failure_msg="Failed to activate debugger. Amiberry may not be built with debugger support.",
    )


async def _handle_runtime_debug_deactivate(arguments: Any) -> list:
    """Handle runtime_debug_deactivate tool."""
    return await _ipc_bool_call(
        "debug_deactivate",
        success_msg="Debugger deactivated, emulation resumed.",
        failure_msg="Failed to deactivate debugger.",
    )


async def _handle_runtime_debug_status(arguments: Any) -> list:
    """Handle runtime_debug_status tool."""

    async def _cb(client):
        info = await client.debug_status()
        if info:
            result = "Debugger status:\n"
            for key, value in info.items():
                result += f"  {key}: {value}\n"
            return result
        else:
            return "Failed to get debugger status."

    return await _ipc_call(_cb)


async def _handle_runtime_debug_step(arguments: Any) -> list:
    """Handle runtime_debug_step tool."""
    count = arguments.get("count", 1)
    return await _ipc_bool_call(
        "debug_step",
        count,
        success_msg=f"Stepped {count} instruction(s).",
        failure_msg="Failed to step. Debugger may not be active.",
    )


async def _handle_runtime_debug_continue(arguments: Any) -> list:
    """Handle runtime_debug_continue tool."""
    return await _ipc_bool_call(
        "debug_continue",
        success_msg="Execution continued.",
        failure_msg="Failed to continue execution.",
    )


async def _handle_runtime_get_cpu_regs(arguments: Any) -> list:
    """Handle runtime_get_cpu_regs tool."""

    async def _cb(client):
        info = await client.get_cpu_regs()
        if info:
            result = "CPU Registers:\n"
            # Format nicely: D0-D7 on one section, A0-A7 on another
            data_regs = [f"  {k}: {v}" for k, v in info.items() if k.startswith("D")]
            addr_regs = [f"  {k}: {v}" for k, v in info.items() if k.startswith("A")]
            other_regs = [
                f"  {k}: {v}"
                for k, v in info.items()
                if not k.startswith("D") and not k.startswith("A")
            ]
            result += "Data registers:\n" + "\n".join(data_regs) + "\n"
            result += "Address registers:\n" + "\n".join(addr_regs) + "\n"
            result += "Other:\n" + "\n".join(other_regs)
            return result
        else:
            return "Failed to get CPU registers."

    return await _ipc_call(_cb)


async def _handle_runtime_get_custom_regs(arguments: Any) -> list:
    """Handle runtime_get_custom_regs tool."""

    async def _cb(client):
        info = await client.get_custom_regs()
        if info:
            result = "Custom Chip Registers:\n"
            for key, value in info.items():
                result += f"  {key}: {value}\n"
            return result
        else:
            return "Failed to get custom registers."

    return await _ipc_call(_cb)


async def _handle_runtime_disassemble(arguments: Any) -> list:
    """Handle runtime_disassemble tool."""
    address = arguments["address"]
    count = arguments.get("count", 10)

    async def _cb(client):
        lines = await client.disassemble(address, count)
        if lines:
            result = f"Disassembly at {address}:\n"
            for line in lines:
                result += f"  {line}\n"
            return result
        else:
            return "No disassembly returned."

    return await _ipc_call(_cb)


async def _handle_runtime_set_breakpoint(arguments: Any) -> list:
    """Handle runtime_set_breakpoint tool."""
    address = arguments["address"]
    return await _ipc_bool_call(
        "set_breakpoint",
        address,
        success_msg=f"Breakpoint set at {address}.",
        failure_msg="Failed to set breakpoint. Maximum 20 breakpoints allowed.",
    )


async def _handle_runtime_clear_breakpoint(arguments: Any) -> list:
    """Handle runtime_clear_breakpoint tool."""
    address = arguments["address"]

    async def _cb(client):
        success = await client.clear_breakpoint(address)
        if success:
            if address.upper() == "ALL":
                return "All breakpoints cleared."
            else:
                return f"Breakpoint at {address} cleared."
        else:
            return "Failed to clear breakpoint."

    return await _ipc_call(_cb)


async def _handle_runtime_list_breakpoints(arguments: Any) -> list:
    """Handle runtime_list_breakpoints tool."""

    async def _cb(client):
        breakpoints = await client.list_breakpoints()
        if breakpoints:
            result = "Active breakpoints:\n"
            for bp in breakpoints:
                result += f"  {bp}\n"
            return result
        else:
            return "No active breakpoints."

    return await _ipc_call(_cb)


async def _handle_runtime_get_copper_state(arguments: Any) -> list:
    """Handle runtime_get_copper_state tool."""

    async def _cb(client):
        info = await client.get_copper_state()
        if info:
            result = "Copper State:\n"
            for key, value in info.items():
                result += f"  {key}: {value}\n"
            return result
        else:
            return "Failed to get Copper state."

    return await _ipc_call(_cb)


async def _handle_runtime_get_blitter_state(arguments: Any) -> list:
    """Handle runtime_get_blitter_state tool."""

    async def _cb(client):
        info = await client.get_blitter_state()
        if info:
            result = "Blitter State:\n"
            for key, value in info.items():
                result += f"  {key}: {value}\n"
            return result
        else:
            return "Failed to get Blitter state."

    return await _ipc_call(_cb)


async def _handle_runtime_get_drive_state(arguments: Any) -> list:
    """Handle runtime_get_drive_state tool."""
    drive = arguments.get("drive")

    async def _cb(client):
        info = await client.get_drive_state(drive)
        if info:
            result = f"Drive State{' (DF' + str(drive) + ')' if drive is not None else ''}:\n"
            for key, value in info.items():
                result += f"  {key}: {value}\n"
            return result
        else:
            return "Failed to get drive state."

    return await _ipc_call(_cb)


async def _handle_runtime_get_audio_state(arguments: Any) -> list:
    """Handle runtime_get_audio_state tool."""

    async def _cb(client):
        info = await client.get_audio_state()
        if info:
            result = "Audio State:\n"
            for key, value in info.items():
                result += f"  {key}: {value}\n"
            return result
        else:
            return "Failed to get audio state."

    return await _ipc_call(_cb)


async def _handle_runtime_get_dma_state(arguments: Any) -> list:
    """Handle runtime_get_dma_state tool."""

    async def _cb(client):
        info = await client.get_dma_state()
        if info:
            result = "DMA State:\n"
            for key, value in info.items():
                result += f"  {key}: {value}\n"
            return result
        else:
            return "Failed to get DMA state."

    return await _ipc_call(_cb)

    # === Process Lifecycle Management ===


async def _handle_check_process_alive(arguments: Any) -> list:
    """Handle check_process_alive tool."""

    if _state.process is None:
        return _text_result("No Amiberry process tracked. Launch Amiberry first.")
    returncode = _state.process.poll()
    if returncode is None:
        return _text_result(f"Amiberry is RUNNING (PID: {_state.process.pid})")
    else:
        signal_info = format_signal_info(returncode)
        return _text_result(f"Amiberry has EXITED with code {returncode}{signal_info}")


async def _handle_get_process_info(arguments: Any) -> list:
    """Handle get_process_info tool."""

    if _state.process is None:
        return _text_result("No Amiberry process tracked. Launch Amiberry first.")
    returncode = _state.process.poll()
    info_parts = [f"PID: {_state.process.pid}"]

    if returncode is None:
        info_parts.append("Status: RUNNING")
    else:
        info_parts.append("Status: EXITED")
        info_parts.append(f"Exit code: {returncode}")
        if returncode < 0:
            try:
                sig = signal.Signals(-returncode)
                info_parts.append(f"Signal: {sig.name}")
                info_parts.append("CRASH DETECTED: Process was killed by a signal")
            except ValueError:
                info_parts.append(f"Signal: {-returncode}")
                info_parts.append("CRASH DETECTED: Process was killed by a signal")
        elif returncode != 0:
            info_parts.append("ABNORMAL EXIT: Non-zero exit code")

    if _state.launch_cmd:
        info_parts.append(f"Command: {' '.join(_state.launch_cmd)}")
    if _state.log_path:
        info_parts.append(f"Log file: {_state.log_path}")

    return _text_result("\n".join(info_parts))


async def _handle_kill_amiberry(arguments: Any) -> list:
    """Handle kill_amiberry tool."""

    if _state.process is None or _state.process.poll() is not None:
        return _text_result("No running Amiberry process to kill.")
    pid = _state.process.pid
    await asyncio.to_thread(terminate_process, _state.process)
    return _text_result(f"Amiberry process (PID {pid}) terminated.")


async def _handle_wait_for_exit(arguments: Any) -> list:
    """Handle wait_for_exit tool."""

    if _state.process is None:
        return _text_result("No Amiberry process tracked. Launch Amiberry first.")
    returncode = _state.process.poll()
    if returncode is not None:
        return _text_result(f"Amiberry already exited with code {returncode}.")
    timeout = arguments.get("timeout", 30)
    try:
        returncode = await asyncio.to_thread(_state.process.wait, timeout=timeout)
        return _text_result(f"Amiberry exited with code {returncode}.")
    except subprocess.TimeoutExpired:
        return _text_result(
            f"Timeout after {timeout}s. Amiberry still running (PID {_state.process.pid})."
        )


async def _handle_restart_amiberry(arguments: Any) -> list:
    """Handle restart_amiberry tool."""

    if _state.launch_cmd is None:
        return _text_result(
            "No previous launch command stored. Use a launch tool first."
        )
    # Kill existing process if running
    if _state.process is not None and _state.process.poll() is None:
        await asyncio.to_thread(terminate_process, _state.process)

    # Re-launch with stored command
    cmd = _state.launch_cmd
    _state.close_log_handle()
    try:
        _state.process, _state.log_file_handle = launch_process(
            cmd, log_path=_state.log_path
        )
        return _text_result(
            f"Amiberry restarted (PID: {_state.process.pid})\nCommand: {' '.join(cmd)}"
        )
    except Exception as e:
        return _text_result(f"Error restarting Amiberry: {str(e)}")


# === Missing IPC Tool Wrappers ===


async def _handle_runtime_read_memory(arguments: Any) -> list:
    """Handle runtime_read_memory tool."""
    address_str = arguments["address"]
    width = arguments["width"]
    try:
        address = int(address_str, 0)  # Handles both hex (0x...) and decimal
        client = _get_ipc_client()
        value = await client.read_memory(address, width)
        if value is not None:
            return _text_result(
                f"Memory at 0x{address:08X} ({width} byte{'s' if width > 1 else ''}): 0x{value:0{width * 2}X} ({value})"
            )
        else:
            return _text_result(f"Failed to read memory at {address_str}.")
    except ValueError as e:
        return _text_result(f"Invalid address: {str(e)}")
    except IPCConnectionError as e:
        return _text_result(f"Connection error: {str(e)}")
    except Exception as e:
        return _text_result(f"Error: {str(e)}")


async def _handle_runtime_write_memory(arguments: Any) -> list:
    """Handle runtime_write_memory tool."""
    address_str = arguments["address"]
    width = arguments["width"]
    value = arguments["value"]
    try:
        address = int(address_str, 0)
        client = _get_ipc_client()
        success = await client.write_memory(address, width, value)
        if success:
            return _text_result(
                f"Wrote 0x{value:0{width * 2}X} ({value}) to 0x{address:08X} ({width} byte{'s' if width > 1 else ''})."
            )
        else:
            return _text_result(f"Failed to write memory at {address_str}.")
    except ValueError as e:
        return _text_result(f"Invalid argument: {str(e)}")
    except IPCConnectionError as e:
        return _text_result(f"Connection error: {str(e)}")
    except Exception as e:
        return _text_result(f"Error: {str(e)}")


async def _handle_runtime_load_config(arguments: Any) -> list:
    """Handle runtime_load_config tool."""
    config_path = arguments["config_path"]
    return await _ipc_bool_call(
        "load_config",
        config_path,
        success_msg=f"Configuration loaded: {config_path}",
        failure_msg=f"Failed to load configuration: {config_path}",
    )


async def _handle_runtime_debug_step_over(arguments: Any) -> list:
    """Handle runtime_debug_step_over tool."""
    return await _ipc_bool_call(
        "debug_step_over",
        success_msg="Stepped over subroutine.",
        failure_msg="Failed to step over. Is the debugger active?",
    )


# === Screenshot with Image Data ===


async def _handle_runtime_screenshot_view(arguments: Any) -> list:
    """Handle runtime_screenshot_view tool."""
    filename = arguments.get("filename")
    if not filename:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = str(SCREENSHOT_DIR / f"debug_{timestamp}.png")

    try:
        client = _get_ipc_client()
        success = await client.screenshot(filename)
        if success:
            screenshot_path = Path(filename)
            if screenshot_path.exists():
                image_data = await asyncio.to_thread(screenshot_path.read_bytes)

                # Detect format from magic bytes
                # Claude API only accepts: image/jpeg, image/png, image/gif, image/webp
                if image_data[:2] in (b'\xff\xd8',):
                    mime_type = "image/jpeg"
                elif image_data[:4] == b'GIF8':
                    mime_type = "image/gif"
                elif image_data[:4] == b'RIFF' and image_data[8:12] == b'WEBP':
                    mime_type = "image/webp"
                else:
                    # Amiberry saves PNG format - default to image/png
                    mime_type = "image/png"

                b64_data = base64.b64encode(image_data).decode("utf-8")

                if _HAS_IMAGE_CONTENT:
                    return [
                        TextContent(
                            type="text",
                            text=f"Screenshot saved to: {filename}",
                        ),
                        ImageContent(
                            type="image",
                            data=b64_data,
                            mimeType=mime_type,
                        ),
                    ]
                else:
                    return _text_result(
                        f"Screenshot saved to: {filename}\nUse the Read tool to view this image file."
                    )
            else:
                return _text_result(
                    f"Screenshot command succeeded but file not found at: {filename}"
                )
        else:
            return _text_result("Failed to take screenshot.")
    except IPCConnectionError as e:
        return _text_result(f"Connection error: {str(e)}")
    except Exception as e:
        return _text_result(f"Error: {str(e)}")


# === Log Tailing and Crash Detection ===


async def _handle_tail_log(arguments: Any) -> list:
    """Handle tail_log tool."""

    log_name = arguments["log_name"]
    try:
        log_path = normalize_log_path(log_name)
    except ValueError:
        return _text_result(f"Error: Invalid log name '{log_name}'")

    if not log_path.exists():
        return _text_result(
            f"Error: Log file not found: {log_name}\nAvailable logs in: {LOG_DIR}"
        )

    last_pos = _state.log_read_positions.get(log_name, 0)
    try:

        def _read_from_pos():
            with open(log_path, errors="replace") as f:
                file_size = f.seek(0, 2)  # Get file size
                pos = last_pos if last_pos <= file_size else 0
                f.seek(pos)
                content = f.read()
                return content, f.tell()

        new_content, new_pos = await asyncio.to_thread(_read_from_pos)
        _state.log_read_positions[log_name] = new_pos

        if new_content:
            line_count = new_content.count("\n")
            return _text_result(
                f"New log output ({line_count} lines):\n\n{new_content}"
            )
        else:
            return _text_result("No new log output since last read.")
    except Exception as e:
        return _text_result(f"Error reading log: {str(e)}")


async def _handle_wait_for_log_pattern(arguments: Any) -> list:
    """Handle wait_for_log_pattern tool."""
    log_name = arguments["log_name"]
    pattern = arguments["pattern"]
    timeout = arguments.get("timeout", 30)
    try:
        log_path = normalize_log_path(log_name)
    except ValueError:
        return _text_result(f"Error: Invalid log name '{log_name}'")

    try:
        compiled_pattern = re.compile(pattern)
    except re.error as e:
        return _text_result(f"Invalid regex pattern: {str(e)}")

    start_time = asyncio.get_running_loop().time()
    last_pos = _state.log_read_positions.get(log_name, 0)

    while True:
        elapsed = asyncio.get_running_loop().time() - start_time
        if elapsed >= timeout:
            _state.log_read_positions[log_name] = last_pos
            return _text_result(
                f"Timeout after {timeout}s. Pattern '{pattern}' not found in {log_name}."
            )

        if log_path.exists():
            try:

                def _read_and_match(_pos=last_pos):
                    with open(log_path, errors="replace") as f:
                        f.seek(_pos)
                        content = f.read()
                        new_p = f.tell()
                    if content:
                        for ln in content.splitlines():
                            if compiled_pattern.search(ln):
                                return ln, new_p
                    return None, new_p

                match_line, new_pos = await asyncio.to_thread(_read_and_match)

                if match_line is not None:
                    _state.log_read_positions[log_name] = new_pos
                    return _text_result(
                        f"Pattern '{pattern}' found after {elapsed:.1f}s:\n{match_line}"
                    )
                last_pos = new_pos
            except Exception:
                pass

        await asyncio.sleep(0.5)


async def _handle_get_crash_info(arguments: Any) -> list:
    """Handle get_crash_info tool."""

    result_parts = []

    # Check process state
    if _state.process is not None:
        returncode = _state.process.poll()
        if returncode is None:
            result_parts.append(f"Process: RUNNING (PID {_state.process.pid})")
        else:
            if returncode < 0:
                try:
                    sig = signal.Signals(-returncode)
                    result_parts.append(
                        f"CRASH DETECTED: Process killed by signal {sig.name} (code {returncode})"
                    )
                except ValueError:
                    result_parts.append(
                        f"CRASH DETECTED: Process killed by signal {-returncode}"
                    )
            elif returncode != 0:
                result_parts.append(
                    f"ABNORMAL EXIT: Process exited with code {returncode}"
                )
            else:
                result_parts.append("Process: Exited normally (code 0)")
    else:
        result_parts.append(
            "Process: Not tracked (launched externally or not yet launched)"
        )

    # Scan logs for crash patterns
    log_name = arguments.get("log_name")
    crash_patterns = [
        "Segmentation fault",
        "SIGSEGV",
        "SIGABRT",
        "Aborted",
        "core dumped",
        "assertion failed",
        "FATAL",
        "Bus error",
        "SIGBUS",
        "double free",
        "heap-buffer-overflow",
        "stack-buffer-overflow",
        "AddressSanitizer",
        "undefined behavior",
    ]

    log_files_to_scan: list[Path] = []
    if log_name:
        try:
            log_files_to_scan = [normalize_log_path(log_name)]
        except ValueError:
            return _text_result(f"Error: Invalid log name '{log_name}'")
    elif _state.log_path:
        log_files_to_scan = [_state.log_path]
    elif LOG_DIR.exists():

        def _find_latest_log():
            logs = []
            for p in LOG_DIR.glob("*.log"):
                try:
                    logs.append((p, p.stat().st_mtime))
                except OSError:
                    continue
            logs.sort(key=lambda x: x[1], reverse=True)
            return [p for p, _ in logs[:1]]

        log_files_to_scan = await asyncio.to_thread(_find_latest_log)

    for lp in log_files_to_scan:
        if lp.exists():
            try:
                content = await asyncio.to_thread(lp.read_text, errors="replace")
                found_crashes = []
                for i, line in enumerate(content.splitlines()):
                    for cp in crash_patterns:
                        if cp.lower() in line.lower():
                            found_crashes.append(f"  Line {i + 1}: {line.strip()}")
                            break

                if found_crashes:
                    result_parts.append(f"\nCrash indicators in {lp.name}:")
                    result_parts.extend(found_crashes[:20])
                    if len(found_crashes) > 20:
                        result_parts.append(f"  ... and {len(found_crashes) - 20} more")
                else:
                    result_parts.append(f"\nNo crash indicators found in {lp.name}")
            except Exception as e:
                result_parts.append(f"\nError reading {lp.name}: {str(e)}")

    if not log_files_to_scan:
        result_parts.append("\nNo log files found to scan.")

    return _text_result("\n".join(result_parts))


# === Workflow Tools ===


async def _handle_health_check(arguments: Any) -> list:
    """Handle health_check tool."""

    results = []

    # 1. Process check
    if _state.process is not None:
        rc = _state.process.poll()
        if rc is None:
            results.append(f"Process: RUNNING (PID {_state.process.pid})")
        else:
            results.append(f"Process: EXITED (code {rc})")
    else:
        results.append("Process: NOT TRACKED")

    # 2. IPC check
    try:
        client = _get_ipc_client()
        pong = await client.ping()
        if pong:
            results.append("IPC Ping: OK")

            # 3. Get status
            status = await client.get_status()
            if status:
                results.append(f"Paused: {status.get('Paused', '?')}")
                results.append(f"Config: {status.get('Config', '?')}")
                for key in ["Floppy0", "Floppy1", "Floppy2", "Floppy3"]:
                    val = status.get(key)
                    if val:
                        results.append(f"{key}: {val}")
            else:
                results.append("Status: Failed to query")

            # 4. FPS
            fps_info = await client.get_fps()
            if fps_info:
                results.append(
                    f"FPS: {fps_info.get('fps', '?')} (idle: {fps_info.get('idle', '?')}%)"
                )
        else:
            results.append("IPC Ping: FAILED (socket exists but not responding)")
    except IPCConnectionError:
        results.append("IPC: NOT CONNECTED (socket not found or connection refused)")
    except Exception as e:
        results.append(f"IPC: ERROR ({str(e)})")

    return _text_result("Health Check:\n" + "\n".join(f"  {r}" for r in results))


async def _handle_launch_and_wait_for_ipc(arguments: Any) -> list:
    """Handle launch_and_wait_for_ipc tool."""

    # Kill existing process if running
    if _state.process is not None and _state.process.poll() is None:
        await asyncio.to_thread(terminate_process, _state.process)

    # Build command
    config = arguments.get("config")
    model = arguments.get("model")
    disk_image = arguments.get("disk_image")
    lha_file = arguments.get("lha_file")
    autostart = arguments.get("autostart", True)
    timeout = arguments.get("timeout", 30)

    if not model and not config and not lha_file:
        return _text_result(
            "Error: Either 'model', 'config', or 'lha_file' must be specified"
        )

    # Resolve config path if specified
    config_path = None
    if config:
        config_path = _find_config_path(config)
        if not config_path:
            return _text_result(f"Error: Configuration '{config}' not found")

    # Validate LHA file
    if lha_file:
        lha_path = Path(lha_file)
        if not lha_path.exists():
            return _text_result(f"Error: LHA file not found: {lha_file}")

    cmd = build_launch_command(
        model=model,
        config_path=config_path,
        disk_image=disk_image,
        lha_file=lha_file,
        autostart=autostart,
        with_logging=True,
    )

    # Launch with logging
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_name = f"amiberry_{timestamp}.log"
    log_path = LOG_DIR / log_name

    try:
        _launch_and_store(cmd, log_path=log_path)
    except Exception as e:
        _state.close_log_handle()
        return _text_result(f"Error launching Amiberry: {str(e)}")

    # Wait for IPC socket to become available
    start_time = asyncio.get_running_loop().time()
    ipc_ready = False

    while True:
        elapsed = asyncio.get_running_loop().time() - start_time
        if elapsed >= timeout:
            break

        # Check if process died
        if _state.process.poll() is not None:
            rc = _state.process.returncode
            return _text_result(
                f"Amiberry exited before IPC became available (exit code: {rc}).\nLog file: {log_path}\nCommand: {' '.join(cmd)}"
            )

        try:
            client = _get_ipc_client()
            pong = await client.ping()
            if pong:
                ipc_ready = True
                break
        except Exception:
            pass

        await asyncio.sleep(0.5)

    if ipc_ready:
        return _text_result(
            f"Amiberry launched and IPC ready!\n  PID: {_state.process.pid}\n  Log: {log_path}\n  Command: {' '.join(cmd)}\n  IPC connected after {elapsed:.1f}s"
        )
    else:
        return _text_result(
            f"Amiberry launched but IPC not responding after {timeout}s.\n  PID: {_state.process.pid}\n  Log: {log_path}\n  Process still running: {_state.process.poll() is None}"
        )


# Tool dispatch dictionary
_TOOL_DISPATCH: dict[str, Any] = {
    "get_platform_info": _handle_get_platform_info,
    "list_configs": _handle_list_configs,
    "get_config_content": _handle_get_config_content,
    "list_disk_images": _handle_list_disk_images,
    "launch_amiberry": _handle_launch_amiberry,
    "list_savestates": _handle_list_savestates,
    "launch_with_logging": _handle_launch_with_logging,
    "parse_config": _handle_parse_config,
    "modify_config": _handle_modify_config,
    "create_config": _handle_create_config,
    "launch_whdload": _handle_launch_whdload,
    "launch_cd": _handle_launch_cd,
    "set_disk_swapper": _handle_set_disk_swapper,
    "list_cd_images": _handle_list_cd_images,
    "get_log_content": _handle_get_log_content,
    "list_logs": _handle_list_logs,
    "inspect_savestate": _handle_inspect_savestate,
    "list_roms": _handle_list_roms,
    "identify_rom": _handle_identify_rom,
    "get_amiberry_version": _handle_get_amiberry_version,
    "pause_emulation": _handle_pause_emulation,
    "resume_emulation": _handle_resume_emulation,
    "reset_emulation": _handle_reset_emulation,
    "runtime_screenshot": _handle_runtime_screenshot,
    "runtime_save_state": _handle_runtime_save_state,
    "runtime_load_state": _handle_runtime_load_state,
    "runtime_insert_floppy": _handle_runtime_insert_floppy,
    "runtime_insert_cd": _handle_runtime_insert_cd,
    "get_runtime_status": _handle_get_runtime_status,
    "runtime_get_config": _handle_runtime_get_config,
    "runtime_set_config": _handle_runtime_set_config,
    "check_ipc_connection": _handle_check_ipc_connection,
    "runtime_eject_floppy": _handle_runtime_eject_floppy,
    "runtime_eject_cd": _handle_runtime_eject_cd,
    "runtime_list_floppies": _handle_runtime_list_floppies,
    "runtime_list_configs": _handle_runtime_list_configs,
    "runtime_set_volume": _handle_runtime_set_volume,
    "runtime_get_volume": _handle_runtime_get_volume,
    "runtime_mute": _handle_runtime_mute,
    "runtime_unmute": _handle_runtime_unmute,
    "runtime_toggle_fullscreen": _handle_runtime_toggle_fullscreen,
    "runtime_set_warp": _handle_runtime_set_warp,
    "runtime_get_warp": _handle_runtime_get_warp,
    "runtime_get_version": _handle_runtime_get_version,
    "runtime_frame_advance": _handle_runtime_frame_advance,
    "runtime_send_mouse": _handle_runtime_send_mouse,
    "runtime_set_mouse_speed": _handle_runtime_set_mouse_speed,
    "runtime_send_key": _handle_runtime_send_key,
    "runtime_send_text": _handle_runtime_send_text,
    "runtime_ping": _handle_runtime_ping,
    "set_active_instance": _handle_set_active_instance,
    "get_active_instance": _handle_get_active_instance,
    "runtime_quicksave": _handle_runtime_quicksave,
    "runtime_quickload": _handle_runtime_quickload,
    "runtime_get_joyport_mode": _handle_runtime_get_joyport_mode,
    "runtime_set_joyport_mode": _handle_runtime_set_joyport_mode,
    "runtime_get_autofire": _handle_runtime_get_autofire,
    "runtime_set_autofire": _handle_runtime_set_autofire,
    "runtime_get_led_status": _handle_runtime_get_led_status,
    "runtime_list_harddrives": _handle_runtime_list_harddrives,
    "runtime_set_display_mode": _handle_runtime_set_display_mode,
    "runtime_get_display_mode": _handle_runtime_get_display_mode,
    "runtime_set_ntsc": _handle_runtime_set_ntsc,
    "runtime_get_ntsc": _handle_runtime_get_ntsc,
    "runtime_set_sound_mode": _handle_runtime_set_sound_mode,
    "runtime_get_sound_mode": _handle_runtime_get_sound_mode,
    "runtime_toggle_mouse_grab": _handle_runtime_toggle_mouse_grab,
    "runtime_get_mouse_speed": _handle_runtime_get_mouse_speed,
    "runtime_set_cpu_speed": _handle_runtime_set_cpu_speed,
    "runtime_get_cpu_speed": _handle_runtime_get_cpu_speed,
    "runtime_toggle_rtg": _handle_runtime_toggle_rtg,
    "runtime_set_floppy_speed": _handle_runtime_set_floppy_speed,
    "runtime_get_floppy_speed": _handle_runtime_get_floppy_speed,
    "runtime_disk_write_protect": _handle_runtime_disk_write_protect,
    "runtime_get_disk_write_protect": _handle_runtime_get_disk_write_protect,
    "runtime_toggle_status_line": _handle_runtime_toggle_status_line,
    "runtime_set_chipset": _handle_runtime_set_chipset,
    "runtime_get_chipset": _handle_runtime_get_chipset,
    "runtime_get_memory_config": _handle_runtime_get_memory_config,
    "runtime_get_fps": _handle_runtime_get_fps,
    "runtime_set_chip_mem": _handle_runtime_set_chip_mem,
    "runtime_set_fast_mem": _handle_runtime_set_fast_mem,
    "runtime_set_slow_mem": _handle_runtime_set_slow_mem,
    "runtime_set_z3_mem": _handle_runtime_set_z3_mem,
    "runtime_get_cpu_model": _handle_runtime_get_cpu_model,
    "runtime_set_cpu_model": _handle_runtime_set_cpu_model,
    "runtime_set_window_size": _handle_runtime_set_window_size,
    "runtime_get_window_size": _handle_runtime_get_window_size,
    "runtime_set_scaling": _handle_runtime_set_scaling,
    "runtime_get_scaling": _handle_runtime_get_scaling,
    "runtime_set_line_mode": _handle_runtime_set_line_mode,
    "runtime_get_line_mode": _handle_runtime_get_line_mode,
    "runtime_set_resolution": _handle_runtime_set_resolution,
    "runtime_get_resolution": _handle_runtime_get_resolution,
    "runtime_set_autocrop": _handle_runtime_set_autocrop,
    "runtime_get_autocrop": _handle_runtime_get_autocrop,
    "runtime_insert_whdload": _handle_runtime_insert_whdload,
    "runtime_eject_whdload": _handle_runtime_eject_whdload,
    "runtime_get_whdload": _handle_runtime_get_whdload,
    "runtime_debug_activate": _handle_runtime_debug_activate,
    "runtime_debug_deactivate": _handle_runtime_debug_deactivate,
    "runtime_debug_status": _handle_runtime_debug_status,
    "runtime_debug_step": _handle_runtime_debug_step,
    "runtime_debug_continue": _handle_runtime_debug_continue,
    "runtime_get_cpu_regs": _handle_runtime_get_cpu_regs,
    "runtime_get_custom_regs": _handle_runtime_get_custom_regs,
    "runtime_disassemble": _handle_runtime_disassemble,
    "runtime_set_breakpoint": _handle_runtime_set_breakpoint,
    "runtime_clear_breakpoint": _handle_runtime_clear_breakpoint,
    "runtime_list_breakpoints": _handle_runtime_list_breakpoints,
    "runtime_get_copper_state": _handle_runtime_get_copper_state,
    "runtime_get_blitter_state": _handle_runtime_get_blitter_state,
    "runtime_get_drive_state": _handle_runtime_get_drive_state,
    "runtime_get_audio_state": _handle_runtime_get_audio_state,
    "runtime_get_dma_state": _handle_runtime_get_dma_state,
    "check_process_alive": _handle_check_process_alive,
    "get_process_info": _handle_get_process_info,
    "kill_amiberry": _handle_kill_amiberry,
    "wait_for_exit": _handle_wait_for_exit,
    "restart_amiberry": _handle_restart_amiberry,
    "runtime_read_memory": _handle_runtime_read_memory,
    "runtime_write_memory": _handle_runtime_write_memory,
    "runtime_load_config": _handle_runtime_load_config,
    "runtime_debug_step_over": _handle_runtime_debug_step_over,
    "runtime_screenshot_view": _handle_runtime_screenshot_view,
    "tail_log": _handle_tail_log,
    "wait_for_log_pattern": _handle_wait_for_log_pattern,
    "get_crash_info": _handle_get_crash_info,
    "health_check": _handle_health_check,
    "launch_and_wait_for_ipc": _handle_launch_and_wait_for_ipc,
}


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list:
    """Handle tool execution via dispatch dict."""
    handler = _TOOL_DISPATCH.get(name)
    if handler is not None:
        return await handler(arguments)
    return _text_result(f"Unknown tool: {name}")


async def main():
    """Main entry point for the MCP server."""
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
