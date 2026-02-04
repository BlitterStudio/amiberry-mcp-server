#!/usr/bin/env python3
"""
MCP Server for Amiberry emulator control.
Enables Claude AI to interact with Amiberry through the Model Context Protocol.
"""

import asyncio
import datetime
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.types import Tool, TextContent
import mcp.server.stdio

from .config import (
    IS_LINUX,
    IS_MACOS,
    AMIBERRY_HOME,
    EMULATOR_BINARY,
    CONFIG_DIR,
    SYSTEM_CONFIG_DIR,
    SAVESTATE_DIR,
    DISK_IMAGE_DIRS,
    FLOPPY_EXTENSIONS,
    HARDFILE_EXTENSIONS,
    LHA_EXTENSIONS,
    SUPPORTED_MODELS,
    get_platform_info,
)
from .uae_config import (
    parse_uae_config,
    write_uae_config,
    modify_uae_config,
    create_config_from_template,
    get_config_summary,
)
from .savestate import (
    inspect_savestate,
    get_savestate_summary,
    list_savestate_chunks,
)
from .rom_manager import (
    identify_rom,
    scan_rom_directory,
    get_rom_summary,
)
from .ipc_client import (
    AmiberryIPCClient,
    IPCError,
    ConnectionError as IPCConnectionError,
    CommandError,
)

# ROM directory
ROM_DIR = AMIBERRY_HOME / "Kickstarts" if IS_MACOS else AMIBERRY_HOME / "kickstarts"

# CD image extensions
CD_EXTENSIONS = [".iso", ".cue", ".chd", ".bin", ".nrg"]

# Log directory for captured output
LOG_DIR = AMIBERRY_HOME / "logs"

app = Server("amiberry-emulator")


@app.list_tools()
async def list_tools() -> list[Tool]:
    """Define available tools."""
    return [
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
                        "enum": ["A500", "A500P", "A600", "A1200", "A4000", "CD32", "CDTV"],
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
            name="runtime_ping",
            description="Test the IPC connection to a running Amiberry instance.",
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
    ]


def _get_extensions_for_type(image_type: str) -> list[str]:
    """Get file extensions for a given image type."""
    if image_type == "floppy":
        return FLOPPY_EXTENSIONS
    elif image_type == "hardfile":
        return HARDFILE_EXTENSIONS
    elif image_type == "lha":
        return LHA_EXTENSIONS
    else:  # all
        return FLOPPY_EXTENSIONS + HARDFILE_EXTENSIONS + LHA_EXTENSIONS


def _classify_image_type(suffix: str) -> str:
    """Classify a disk image by its file extension."""
    suffix_lower = suffix.lower()
    if suffix_lower in FLOPPY_EXTENSIONS:
        return "floppy"
    elif suffix_lower in LHA_EXTENSIONS:
        return "lha"
    else:
        return "hardfile"


def _find_config_path(config_name: str) -> Path | None:
    """Find a configuration file by name, checking user and system directories."""
    config_path = CONFIG_DIR / config_name
    if config_path.exists():
        return config_path

    if IS_LINUX and SYSTEM_CONFIG_DIR:
        config_path = SYSTEM_CONFIG_DIR / config_name
        if config_path.exists():
            return config_path

    return None


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    """Handle tool execution."""

    if name == "get_platform_info":
        info = get_platform_info()
        lines = [f"{key}: {value}" for key, value in info.items()]
        return [TextContent(type="text", text="\n".join(lines))]

    elif name == "list_configs":
        configs = []

        # User configs
        if CONFIG_DIR.exists():
            user_configs = [(f.name, "user", str(f)) for f in CONFIG_DIR.glob("*.uae")]
            configs.extend(user_configs)

        # System configs (Linux only)
        if IS_LINUX and arguments.get("include_system", False):
            if SYSTEM_CONFIG_DIR and SYSTEM_CONFIG_DIR.exists():
                sys_configs = [
                    (f.name, "system", str(f))
                    for f in SYSTEM_CONFIG_DIR.glob("*.uae")
                ]
                configs.extend(sys_configs)

        if not configs:
            return [TextContent(type="text", text="No configuration files found.")]

        result = f"Found {len(configs)} configuration(s):\n\n"
        for cfg_name, source, path in sorted(configs):
            result += f"- {cfg_name} ({source})\n  Path: {path}\n"

        return [TextContent(type="text", text=result)]

    elif name == "get_config_content":
        config_name = arguments["config_name"]
        config_path = _find_config_path(config_name)

        if not config_path:
            return [
                TextContent(
                    type="text", text=f"Error: Configuration '{config_name}' not found"
                )
            ]

        try:
            content = config_path.read_text()
            return [
                TextContent(
                    type="text", text=f"Configuration: {config_name}\n\n{content}"
                )
            ]
        except Exception as e:
            return [TextContent(type="text", text=f"Error reading config: {str(e)}")]

    elif name == "list_disk_images":
        search_term = arguments.get("search_term", "").lower()
        image_type = arguments.get("image_type", "all")
        images = []

        extensions = _get_extensions_for_type(image_type)

        for directory in DISK_IMAGE_DIRS:
            if directory.exists():
                for ext in extensions:
                    # Search both lowercase and uppercase extensions
                    for pattern in [f"**/*{ext}", f"**/*{ext.upper()}"]:
                        for img in directory.glob(pattern):
                            if not search_term or search_term in img.name.lower():
                                images.append(
                                    {
                                        "path": str(img),
                                        "name": img.name,
                                        "type": _classify_image_type(img.suffix),
                                    }
                                )

        # Remove duplicates (from case-insensitive matching)
        seen = set()
        unique_images = []
        for img in images:
            if img["path"] not in seen:
                seen.add(img["path"])
                unique_images.append(img)
        images = unique_images

        if not images:
            msg = "No disk images found"
            if search_term:
                msg += f' matching "{search_term}"'
            return [TextContent(type="text", text=f"{msg}.")]

        result = f"Found {len(images)} disk image(s):\n\n"
        for img in sorted(images, key=lambda x: x["name"].lower()):
            result += f"- {img['name']} ({img['type']})\n  {img['path']}\n"

        return [TextContent(type="text", text=result)]

    elif name == "launch_amiberry":
        model = arguments.get("model")
        config = arguments.get("config")
        lha_file = arguments.get("lha_file")

        # Validate that at least one of model, config, or lha_file is specified
        if not model and not config and not lha_file:
            return [
                TextContent(
                    type="text",
                    text="Error: Either 'model', 'config', or 'lha_file' must be specified",
                )
            ]

        # Build command
        cmd = [EMULATOR_BINARY]

        # Add model or config (optional if lha_file is provided)
        if model:
            cmd.extend(["--model", model])
        elif config:
            config_path = _find_config_path(config)
            if not config_path:
                return [
                    TextContent(
                        type="text", text=f"Error: Configuration '{config}' not found"
                    )
                ]
            cmd.extend(["-f", str(config_path)])

        # Add disk image if specified
        if "disk_image" in arguments and arguments["disk_image"]:
            cmd.extend(["-0", arguments["disk_image"]])

        # Add .lha file if specified (Amiberry auto-extracts and mounts)
        if lha_file:
            lha_path = Path(lha_file)
            if not lha_path.exists():
                return [
                    TextContent(
                        type="text", text=f"Error: LHA file not found: {lha_file}"
                    )
                ]
            cmd.append(str(lha_path))

        # Add autostart flag
        if arguments.get("autostart", True):
            cmd.append("-G")

        try:
            # Launch in background
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            if model:
                result = f"Launched Amiberry with model: {model}"
            elif config:
                result = f"Launched Amiberry with config: {config}"
            elif lha_file:
                result = f"Launched Amiberry with LHA: {Path(lha_file).name}"
            else:
                result = "Launched Amiberry"

            if "disk_image" in arguments and arguments["disk_image"]:
                result += f"\n  Disk in DF0: {Path(arguments['disk_image']).name}"

            if lha_file and (model or config):
                result += f"\n  LHA: {Path(lha_file).name}"

            return [TextContent(type="text", text=result)]
        except Exception as e:
            return [
                TextContent(type="text", text=f"Error launching Amiberry: {str(e)}")
            ]

    elif name == "list_savestates":
        search_term = arguments.get("search_term", "").lower()
        savestates = []

        if SAVESTATE_DIR.exists():
            for state in SAVESTATE_DIR.glob("**/*.uss"):
                if not search_term or search_term in state.name.lower():
                    mtime = state.stat().st_mtime
                    timestamp = datetime.datetime.fromtimestamp(mtime).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )

                    savestates.append(
                        {
                            "path": str(state),
                            "name": state.name,
                            "modified": timestamp,
                        }
                    )

        if not savestates:
            msg = "No savestates found"
            if search_term:
                msg += f' matching "{search_term}"'
            return [TextContent(type="text", text=f"{msg}.")]

        result = f"Found {len(savestates)} savestate(s):\n\n"
        for state in sorted(savestates, key=lambda x: x["name"]):
            result += f"- {state['name']}\n  Modified: {state['modified']}\n  Path: {state['path']}\n"

        return [TextContent(type="text", text=result)]

    elif name == "launch_with_logging":
        model = arguments.get("model")
        config = arguments.get("config")
        lha_file = arguments.get("lha_file")

        if not model and not config and not lha_file:
            return [
                TextContent(
                    type="text",
                    text="Error: Either 'model', 'config', or 'lha_file' must be specified",
                )
            ]

        # Create log directory if needed
        LOG_DIR.mkdir(parents=True, exist_ok=True)

        # Generate log filename
        log_name = arguments.get("log_name")
        if not log_name:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            log_name = f"amiberry_{timestamp}.log"
        if not log_name.endswith(".log"):
            log_name += ".log"

        log_path = LOG_DIR / log_name

        # Build command
        cmd = [EMULATOR_BINARY, "--log"]

        if model:
            cmd.extend(["--model", model])
        elif config:
            config_path = _find_config_path(config)
            if not config_path:
                return [
                    TextContent(
                        type="text", text=f"Error: Configuration '{config}' not found"
                    )
                ]
            cmd.extend(["-f", str(config_path)])

        if "disk_image" in arguments and arguments["disk_image"]:
            cmd.extend(["-0", arguments["disk_image"]])

        if lha_file:
            lha_path = Path(lha_file)
            if not lha_path.exists():
                return [
                    TextContent(
                        type="text", text=f"Error: LHA file not found: {lha_file}"
                    )
                ]
            cmd.append(str(lha_path))

        if arguments.get("autostart", True):
            cmd.append("-G")

        try:
            with open(log_path, "w") as log_file:
                subprocess.Popen(
                    cmd,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )

            result = f"Launched Amiberry with logging enabled\n"
            result += f"Log file: {log_path}\n"
            result += f"Command: {' '.join(cmd)}"

            return [TextContent(type="text", text=result)]
        except Exception as e:
            return [
                TextContent(type="text", text=f"Error launching Amiberry: {str(e)}")
            ]

    elif name == "parse_config":
        config_name = arguments["config_name"]
        config_path = _find_config_path(config_name)

        if not config_path:
            return [
                TextContent(
                    type="text", text=f"Error: Configuration '{config_name}' not found"
                )
            ]

        try:
            config = parse_uae_config(config_path)
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

            result += f"Graphics: {summary['graphics']['width']}x{summary['graphics']['height']}"
            if summary["graphics"]["fullscreen"]:
                result += " (fullscreen)"
            result += "\n"

            if arguments.get("include_raw", False):
                result += "\n=== Raw Configuration ===\n"
                result += json.dumps(config, indent=2)

            return [TextContent(type="text", text=result)]
        except Exception as e:
            return [TextContent(type="text", text=f"Error parsing config: {str(e)}")]

    elif name == "modify_config":
        config_name = arguments["config_name"]
        modifications = arguments["modifications"]
        config_path = _find_config_path(config_name)

        if not config_path:
            return [
                TextContent(
                    type="text", text=f"Error: Configuration '{config_name}' not found"
                )
            ]

        try:
            updated_config = modify_uae_config(config_path, modifications)

            result = f"Modified configuration: {config_name}\n\n"
            result += "Changes applied:\n"
            for key, value in modifications.items():
                if value is None:
                    result += f"  - Removed: {key}\n"
                else:
                    result += f"  - {key} = {value}\n"

            return [TextContent(type="text", text=result)]
        except Exception as e:
            return [TextContent(type="text", text=f"Error modifying config: {str(e)}")]

    elif name == "create_config":
        config_name = arguments["config_name"]
        template = arguments.get("template", "A500")
        overrides = arguments.get("overrides", {})

        if not config_name.endswith(".uae"):
            config_name += ".uae"

        config_path = CONFIG_DIR / config_name

        if config_path.exists():
            return [
                TextContent(
                    type="text",
                    text=f"Error: Configuration '{config_name}' already exists. Use modify_config to update it.",
                )
            ]

        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            config = create_config_from_template(config_path, template, overrides)

            result = f"Created configuration: {config_name}\n"
            result += f"Based on template: {template}\n"
            result += f"Path: {config_path}\n"

            if overrides:
                result += "\nCustom overrides:\n"
                for key, value in overrides.items():
                    result += f"  - {key} = {value}\n"

            return [TextContent(type="text", text=result)]
        except ValueError as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error creating config: {str(e)}")]

    elif name == "launch_whdload":
        search_term = arguments.get("search_term", "").lower()
        exact_path = arguments.get("exact_path")
        model = arguments.get("model", "A1200")  # A1200 is typical for WHDLoad

        lha_path = None

        if exact_path:
            lha_path = Path(exact_path)
            if not lha_path.exists():
                return [
                    TextContent(
                        type="text", text=f"Error: LHA file not found: {exact_path}"
                    )
                ]
        elif search_term:
            # Search for the LHA file
            lha_files = []
            for directory in DISK_IMAGE_DIRS:
                if directory.exists():
                    for pattern in ["**/*.lha", "**/*.LHA"]:
                        for lha in directory.glob(pattern):
                            if search_term in lha.name.lower():
                                lha_files.append(lha)

            if not lha_files:
                return [
                    TextContent(
                        type="text",
                        text=f"No WHDLoad games found matching '{search_term}'",
                    )
                ]

            if len(lha_files) > 1:
                result = f"Found {len(lha_files)} matches for '{search_term}':\n\n"
                for lha in sorted(lha_files, key=lambda x: x.name.lower())[:10]:
                    result += f"- {lha.name}\n  {lha}\n"
                if len(lha_files) > 10:
                    result += f"\n... and {len(lha_files) - 10} more"
                result += "\n\nPlease specify exact_path or use a more specific search term."
                return [TextContent(type="text", text=result)]

            lha_path = lha_files[0]
        else:
            return [
                TextContent(
                    type="text",
                    text="Error: Either 'search_term' or 'exact_path' must be specified",
                )
            ]

        # Build and launch command
        cmd = [EMULATOR_BINARY, "--model", model, str(lha_path)]

        if arguments.get("autostart", True):
            cmd.append("-G")

        try:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            return [
                TextContent(
                    type="text",
                    text=f"Launched WHDLoad game: {lha_path.name}\nModel: {model}",
                )
            ]
        except Exception as e:
            return [
                TextContent(type="text", text=f"Error launching WHDLoad game: {str(e)}")
            ]

    elif name == "launch_cd":
        cd_image = arguments.get("cd_image")
        search_term = arguments.get("search_term", "").lower()
        model = arguments.get("model", "CD32")

        cd_path = None

        if cd_image:
            cd_path = Path(cd_image)
            if not cd_path.exists():
                return [
                    TextContent(
                        type="text", text=f"Error: CD image not found: {cd_image}"
                    )
                ]
        elif search_term:
            # Search for CD images
            cd_files = []
            search_dirs = DISK_IMAGE_DIRS + [AMIBERRY_HOME / "CD"]
            if IS_MACOS:
                search_dirs.append(AMIBERRY_HOME / "CDs")

            for directory in search_dirs:
                if directory.exists():
                    for ext in CD_EXTENSIONS:
                        for pattern in [f"**/*{ext}", f"**/*{ext.upper()}"]:
                            for cd in directory.glob(pattern):
                                if search_term in cd.name.lower():
                                    cd_files.append(cd)

            # Remove duplicates
            cd_files = list(set(cd_files))

            if not cd_files:
                return [
                    TextContent(
                        type="text",
                        text=f"No CD images found matching '{search_term}'",
                    )
                ]

            if len(cd_files) > 1:
                result = f"Found {len(cd_files)} CD images matching '{search_term}':\n\n"
                for cd in sorted(cd_files, key=lambda x: x.name.lower())[:10]:
                    result += f"- {cd.name}\n  {cd}\n"
                if len(cd_files) > 10:
                    result += f"\n... and {len(cd_files) - 10} more"
                result += "\n\nPlease specify cd_image path or use a more specific search term."
                return [TextContent(type="text", text=result)]

            cd_path = cd_files[0]
        else:
            return [
                TextContent(
                    type="text",
                    text="Error: Either 'cd_image' or 'search_term' must be specified",
                )
            ]

        # Build and launch command
        cmd = [EMULATOR_BINARY, "--model", model, "--cdimage", str(cd_path)]

        if arguments.get("autostart", True):
            cmd.append("-G")

        try:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            return [
                TextContent(
                    type="text",
                    text=f"Launched CD image: {cd_path.name}\nModel: {model}",
                )
            ]
        except Exception as e:
            return [
                TextContent(type="text", text=f"Error launching CD image: {str(e)}")
            ]

    elif name == "set_disk_swapper":
        disk_images = arguments.get("disk_images", [])

        if not disk_images:
            return [
                TextContent(
                    type="text", text="Error: No disk images specified"
                )
            ]

        if len(disk_images) < 2:
            return [
                TextContent(
                    type="text",
                    text="Error: Disk swapper requires at least 2 disk images",
                )
            ]

        # Verify all disk images exist
        verified_paths = []
        for img in disk_images:
            img_path = Path(img)
            if not img_path.exists():
                return [
                    TextContent(
                        type="text", text=f"Error: Disk image not found: {img}"
                    )
                ]
            verified_paths.append(str(img_path))

        model = arguments.get("model")
        config = arguments.get("config")

        # Build command
        cmd = [EMULATOR_BINARY]

        if model:
            cmd.extend(["--model", model])
        elif config:
            config_path = _find_config_path(config)
            if not config_path:
                return [
                    TextContent(
                        type="text", text=f"Error: Configuration '{config}' not found"
                    )
                ]
            cmd.extend(["-f", str(config_path)])
        else:
            cmd.extend(["--model", "A500"])  # Default to A500 for disk games

        # Add first disk to DF0
        cmd.extend(["-0", verified_paths[0]])

        # Add disk swapper with all disks
        cmd.append(f"-diskswapper={','.join(verified_paths)}")

        if arguments.get("autostart", True):
            cmd.append("-G")

        try:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            result = f"Launched with disk swapper ({len(verified_paths)} disks):\n"
            for i, path in enumerate(verified_paths):
                result += f"  Disk {i + 1}: {Path(path).name}\n"

            return [TextContent(type="text", text=result)]
        except Exception as e:
            return [
                TextContent(type="text", text=f"Error launching with disk swapper: {str(e)}")
            ]

    elif name == "list_cd_images":
        search_term = arguments.get("search_term", "").lower()
        cd_files = []

        search_dirs = DISK_IMAGE_DIRS + [AMIBERRY_HOME / "CD"]
        if IS_MACOS:
            search_dirs.append(AMIBERRY_HOME / "CDs")

        for directory in search_dirs:
            if directory.exists():
                for ext in CD_EXTENSIONS:
                    for pattern in [f"**/*{ext}", f"**/*{ext.upper()}"]:
                        for cd in directory.glob(pattern):
                            if not search_term or search_term in cd.name.lower():
                                cd_files.append(
                                    {
                                        "path": str(cd),
                                        "name": cd.name,
                                        "type": cd.suffix.lower().lstrip("."),
                                    }
                                )

        # Remove duplicates
        seen = set()
        unique_cds = []
        for cd in cd_files:
            if cd["path"] not in seen:
                seen.add(cd["path"])
                unique_cds.append(cd)
        cd_files = unique_cds

        if not cd_files:
            msg = "No CD images found"
            if search_term:
                msg += f' matching "{search_term}"'
            return [TextContent(type="text", text=f"{msg}.")]

        result = f"Found {len(cd_files)} CD image(s):\n\n"
        for cd in sorted(cd_files, key=lambda x: x["name"].lower()):
            result += f"- {cd['name']} ({cd['type']})\n  {cd['path']}\n"

        return [TextContent(type="text", text=result)]

    elif name == "get_log_content":
        log_name = arguments["log_name"]
        tail_lines = arguments.get("tail_lines")

        if not log_name.endswith(".log"):
            log_name += ".log"

        log_path = LOG_DIR / log_name

        if not log_path.exists():
            return [
                TextContent(type="text", text=f"Error: Log file not found: {log_name}")
            ]

        try:
            content = log_path.read_text(errors="replace")

            if tail_lines and tail_lines > 0:
                lines = content.splitlines()
                content = "\n".join(lines[-tail_lines:])
                result = f"Last {min(tail_lines, len(lines))} lines of {log_name}:\n\n{content}"
            else:
                result = f"Log file: {log_name}\n\n{content}"

            return [TextContent(type="text", text=result)]
        except Exception as e:
            return [TextContent(type="text", text=f"Error reading log: {str(e)}")]

    elif name == "list_logs":
        if not LOG_DIR.exists():
            return [TextContent(type="text", text="No log directory found.")]

        logs = []
        for log in LOG_DIR.glob("*.log"):
            mtime = log.stat().st_mtime
            timestamp = datetime.datetime.fromtimestamp(mtime).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            size = log.stat().st_size
            logs.append(
                {
                    "name": log.name,
                    "modified": timestamp,
                    "size": size,
                }
            )

        if not logs:
            return [TextContent(type="text", text="No log files found.")]

        result = f"Found {len(logs)} log file(s):\n\n"
        for log in sorted(logs, key=lambda x: x["modified"], reverse=True):
            size_str = f"{log['size']} bytes"
            if log["size"] > 1024:
                size_str = f"{log['size'] / 1024:.1f} KB"
            result += f"- {log['name']}\n  Modified: {log['modified']} ({size_str})\n"

        return [TextContent(type="text", text=result)]

    # Phase 2 tools
    elif name == "inspect_savestate":
        savestate_path = arguments["savestate_path"]

        # If just a filename, look in default savestates directory
        path = Path(savestate_path)
        if not path.is_absolute():
            path = SAVESTATE_DIR / savestate_path

        if not path.exists():
            return [
                TextContent(
                    type="text", text=f"Error: Savestate not found: {savestate_path}"
                )
            ]

        try:
            metadata = inspect_savestate(path)
            summary = get_savestate_summary(metadata)

            result = summary + "\n\n"
            result += f"Chunks: {', '.join(metadata.get('chunks', []))}"

            return [TextContent(type="text", text=result)]
        except ValueError as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]
        except Exception as e:
            return [
                TextContent(type="text", text=f"Error inspecting savestate: {str(e)}")
            ]

    elif name == "list_roms":
        directory = arguments.get("directory")

        if directory:
            rom_dir = Path(directory)
        else:
            rom_dir = ROM_DIR

        if not rom_dir.exists():
            return [
                TextContent(
                    type="text",
                    text=f"ROM directory not found: {rom_dir}\n\nCreate this directory and add your Kickstart ROMs.",
                )
            ]

        try:
            roms = scan_rom_directory(rom_dir)

            if not roms:
                return [
                    TextContent(
                        type="text",
                        text=f"No ROM files found in {rom_dir}\n\nAdd Kickstart ROM files (.rom, .bin) to this directory.",
                    )
                ]

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

            return [TextContent(type="text", text=result)]
        except Exception as e:
            return [TextContent(type="text", text=f"Error scanning ROMs: {str(e)}")]

    elif name == "identify_rom":
        rom_path = arguments["rom_path"]
        path = Path(rom_path)

        if not path.exists():
            return [
                TextContent(type="text", text=f"Error: ROM file not found: {rom_path}")
            ]

        try:
            rom_info = identify_rom(path)
            summary = get_rom_summary(rom_info)

            return [TextContent(type="text", text=summary)]
        except Exception as e:
            return [TextContent(type="text", text=f"Error identifying ROM: {str(e)}")]

    elif name == "get_amiberry_version":
        try:
            # Try running amiberry --help to get version info
            result = subprocess.run(
                [EMULATOR_BINARY, "--help"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            output = result.stdout + result.stderr

            # Parse version from output
            version_info = {
                "binary": str(EMULATOR_BINARY),
                "available": True,
            }

            # Look for version string
            for line in output.split("\n"):
                line_lower = line.lower()
                if "version" in line_lower or "amiberry" in line_lower:
                    version_info["version_line"] = line.strip()
                    break

            result_text = f"Amiberry Binary: {version_info['binary']}\n"
            if version_info.get("version_line"):
                result_text += f"Version: {version_info['version_line']}\n"
            result_text += f"Status: Available\n"

            # Check for common features based on help output
            features = []
            if "--log" in output:
                features.append("Console logging")
            if "--model" in output:
                features.append("Model presets")
            if "--cdimage" in output or "cdimage" in output:
                features.append("CD image support")
            if "lua" in output.lower():
                features.append("Lua scripting")

            if features:
                result_text += f"Features detected: {', '.join(features)}"

            return [TextContent(type="text", text=result_text)]

        except subprocess.TimeoutExpired:
            return [
                TextContent(
                    type="text",
                    text=f"Amiberry binary found at {EMULATOR_BINARY} but timed out getting version info.",
                )
            ]
        except FileNotFoundError:
            return [
                TextContent(
                    type="text",
                    text=f"Amiberry binary not found at {EMULATOR_BINARY}.\n\nPlease install Amiberry or check the path.",
                )
            ]
        except Exception as e:
            return [
                TextContent(
                    type="text", text=f"Error getting Amiberry version: {str(e)}"
                )
            ]

    # Runtime control tools (IPC)
    elif name == "pause_emulation":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.pause()
            if success:
                return [TextContent(type="text", text="Emulation paused.")]
            else:
                return [TextContent(type="text", text="Failed to pause emulation.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "resume_emulation":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.resume()
            if success:
                return [TextContent(type="text", text="Emulation resumed.")]
            else:
                return [TextContent(type="text", text="Failed to resume emulation.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "reset_emulation":
        hard = arguments.get("hard", False)
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.reset(hard=hard)
            reset_type = "hard" if hard else "soft"
            if success:
                return [TextContent(type="text", text=f"Emulation {reset_type} reset performed.")]
            else:
                return [TextContent(type="text", text=f"Failed to perform {reset_type} reset.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_screenshot":
        filename = arguments["filename"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.screenshot(filename)
            if success:
                return [TextContent(type="text", text=f"Screenshot saved to: {filename}")]
            else:
                return [TextContent(type="text", text="Failed to take screenshot.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_save_state":
        state_file = arguments["state_file"]
        config_file = arguments["config_file"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.save_state(state_file, config_file)
            if success:
                return [TextContent(type="text", text=f"State saved:\n  State: {state_file}\n  Config: {config_file}")]
            else:
                return [TextContent(type="text", text="Failed to save state.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_load_state":
        state_file = arguments["state_file"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.load_state(state_file)
            if success:
                return [TextContent(type="text", text=f"Loading state: {state_file}")]
            else:
                return [TextContent(type="text", text="Failed to load state.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_insert_floppy":
        drive = arguments["drive"]
        image_path = arguments["image_path"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.insert_floppy(drive, image_path)
            if success:
                return [TextContent(type="text", text=f"Inserted {Path(image_path).name} into DF{drive}:")]
            else:
                return [TextContent(type="text", text=f"Failed to insert disk into DF{drive}:.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except ValueError as e:
            return [TextContent(type="text", text=f"Invalid argument: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_insert_cd":
        image_path = arguments["image_path"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.insert_cd(image_path)
            if success:
                return [TextContent(type="text", text=f"Inserted CD: {Path(image_path).name}")]
            else:
                return [TextContent(type="text", text="Failed to insert CD.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "get_runtime_status":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            status = await client.get_status()

            result = "Amiberry Runtime Status:\n\n"
            result += f"  Paused: {status.get('Paused', 'Unknown')}\n"
            result += f"  Config: {status.get('Config', 'Unknown')}\n"

            # Show mounted floppies
            for i in range(4):
                key = f"Floppy{i}"
                if key in status:
                    result += f"  DF{i}: {status[key]}\n"

            return [TextContent(type="text", text=result)]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except CommandError as e:
            return [TextContent(type="text", text=f"Command error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_config":
        option = arguments["option"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            value = await client.get_config(option)
            if value is not None:
                return [TextContent(type="text", text=f"{option} = {value}")]
            else:
                return [TextContent(type="text", text=f"Unknown or unavailable option: {option}")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_set_config":
        option = arguments["option"]
        value = arguments["value"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.set_config(option, value)
            if success:
                return [TextContent(type="text", text=f"Set {option} = {value}")]
            else:
                return [TextContent(type="text", text=f"Failed to set {option}. Unknown option or invalid value.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "check_ipc_connection":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)

            result = "Amiberry IPC Connection Check:\n\n"
            result += f"  Transport: {client.transport}\n"
            result += f"  Socket available: {client.is_available()}\n"

            if client.is_available():
                # Try to get status to verify connection works
                try:
                    status = await client.get_status()
                    result += f"  Connection: OK\n"
                    result += f"  Emulation paused: {status.get('Paused', 'Unknown')}\n"
                except Exception as e:
                    result += f"  Connection: Failed ({str(e)})\n"
            else:
                result += f"  Connection: Not available\n"
                result += f"\nAmiberry may not be running, or was not built with USE_IPC_SOCKET=ON."

            return [TextContent(type="text", text=result)]
        except Exception as e:
            return [TextContent(type="text", text=f"Error checking IPC: {str(e)}")]

    # New runtime control tools
    elif name == "runtime_eject_floppy":
        drive = arguments["drive"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.eject_floppy(drive)
            if success:
                return [TextContent(type="text", text=f"Ejected disk from DF{drive}:")]
            else:
                return [TextContent(type="text", text=f"Failed to eject disk from DF{drive}:.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except ValueError as e:
            return [TextContent(type="text", text=f"Invalid argument: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_eject_cd":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.eject_cd()
            if success:
                return [TextContent(type="text", text="CD ejected.")]
            else:
                return [TextContent(type="text", text="Failed to eject CD.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_list_floppies":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            drives = await client.list_floppies()

            result = "Floppy Drives:\n\n"
            for drive, path in sorted(drives.items()):
                result += f"  {drive}: {path}\n"

            return [TextContent(type="text", text=result)]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except CommandError as e:
            return [TextContent(type="text", text=f"Command error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_list_configs":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            configs = await client.list_configs()

            if not configs:
                return [TextContent(type="text", text="No configuration files found.")]

            result = f"Found {len(configs)} configuration file(s):\n\n"
            for cfg in sorted(configs):
                result += f"  - {cfg}\n"

            return [TextContent(type="text", text=result)]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_set_volume":
        volume = arguments["volume"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.set_volume(volume)
            if success:
                return [TextContent(type="text", text=f"Volume set to {volume}%")]
            else:
                return [TextContent(type="text", text="Failed to set volume.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except ValueError as e:
            return [TextContent(type="text", text=f"Invalid argument: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_volume":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            volume = await client.get_volume()
            if volume is not None:
                return [TextContent(type="text", text=f"Current volume: {volume}%")]
            else:
                return [TextContent(type="text", text="Failed to get volume.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_mute":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.mute()
            if success:
                return [TextContent(type="text", text="Audio muted.")]
            else:
                return [TextContent(type="text", text="Failed to mute audio.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_unmute":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.unmute()
            if success:
                return [TextContent(type="text", text="Audio unmuted.")]
            else:
                return [TextContent(type="text", text="Failed to unmute audio.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_toggle_fullscreen":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.toggle_fullscreen()
            if success:
                return [TextContent(type="text", text="Fullscreen mode toggled.")]
            else:
                return [TextContent(type="text", text="Failed to toggle fullscreen.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_set_warp":
        enabled = arguments["enabled"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.set_warp(enabled)
            status = "enabled" if enabled else "disabled"
            if success:
                return [TextContent(type="text", text=f"Warp mode {status}.")]
            else:
                return [TextContent(type="text", text=f"Failed to {status[:-1]} warp mode.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_warp":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            enabled = await client.get_warp()
            if enabled is not None:
                status = "enabled" if enabled else "disabled"
                return [TextContent(type="text", text=f"Warp mode is {status}.")]
            else:
                return [TextContent(type="text", text="Failed to get warp mode status.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_version":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            info = await client.get_version()

            result = "Amiberry Version Info:\n\n"
            for key, value in info.items():
                result += f"  {key}: {value}\n"

            return [TextContent(type="text", text=result)]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except CommandError as e:
            return [TextContent(type="text", text=f"Command error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_frame_advance":
        frames = arguments.get("frames", 1)
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.frame_advance(frames)
            if success:
                return [TextContent(type="text", text=f"Advanced {frames} frame(s).")]
            else:
                return [TextContent(type="text", text="Failed to advance frames. Is emulation paused?")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except ValueError as e:
            return [TextContent(type="text", text=f"Invalid argument: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_send_mouse":
        dx = arguments["dx"]
        dy = arguments["dy"]
        buttons = arguments.get("buttons", 0)
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.send_mouse(dx, dy, buttons)
            if success:
                return [TextContent(type="text", text=f"Mouse input sent: dx={dx}, dy={dy}, buttons={buttons}")]
            else:
                return [TextContent(type="text", text="Failed to send mouse input.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_set_mouse_speed":
        speed = arguments["speed"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.set_mouse_speed(speed)
            if success:
                return [TextContent(type="text", text=f"Mouse speed set to {speed}.")]
            else:
                return [TextContent(type="text", text="Failed to set mouse speed.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except ValueError as e:
            return [TextContent(type="text", text=f"Invalid argument: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_ping":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.ping()
            if success:
                return [TextContent(type="text", text="PONG - Amiberry IPC connection is working.")]
            else:
                return [TextContent(type="text", text="Ping failed - no response from Amiberry.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    # Round 2 runtime control tools
    elif name == "runtime_quicksave":
        slot = arguments.get("slot", 0)
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.quicksave(slot)
            if success:
                return [TextContent(type="text", text=f"Quick saved to slot {slot}.")]
            else:
                return [TextContent(type="text", text=f"Failed to quick save to slot {slot}.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except ValueError as e:
            return [TextContent(type="text", text=f"Invalid argument: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_quickload":
        slot = arguments.get("slot", 0)
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.quickload(slot)
            if success:
                return [TextContent(type="text", text=f"Quick loading from slot {slot}.")]
            else:
                return [TextContent(type="text", text=f"Failed to quick load from slot {slot}.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except ValueError as e:
            return [TextContent(type="text", text=f"Invalid argument: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_joyport_mode":
        port = arguments["port"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            result = await client.get_joyport_mode(port)
            if result:
                mode, mode_name = result
                return [TextContent(type="text", text=f"Port {port} mode: {mode} ({mode_name})")]
            else:
                return [TextContent(type="text", text=f"Failed to get port {port} mode.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except ValueError as e:
            return [TextContent(type="text", text=f"Invalid argument: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_set_joyport_mode":
        port = arguments["port"]
        mode = arguments["mode"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.set_joyport_mode(port, mode)
            if success:
                return [TextContent(type="text", text=f"Port {port} mode set to {mode}.")]
            else:
                return [TextContent(type="text", text=f"Failed to set port {port} mode.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except ValueError as e:
            return [TextContent(type="text", text=f"Invalid argument: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_autofire":
        port = arguments["port"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            mode = await client.get_autofire(port)
            if mode is not None:
                modes = {0: "off", 1: "normal", 2: "toggle", 3: "always", 4: "toggle (no autofire)"}
                mode_name = modes.get(mode, "unknown")
                return [TextContent(type="text", text=f"Port {port} autofire: {mode} ({mode_name})")]
            else:
                return [TextContent(type="text", text=f"Failed to get port {port} autofire mode.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except ValueError as e:
            return [TextContent(type="text", text=f"Invalid argument: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_set_autofire":
        port = arguments["port"]
        mode = arguments["mode"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.set_autofire(port, mode)
            if success:
                return [TextContent(type="text", text=f"Port {port} autofire set to {mode}.")]
            else:
                return [TextContent(type="text", text=f"Failed to set port {port} autofire.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except ValueError as e:
            return [TextContent(type="text", text=f"Invalid argument: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_led_status":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            status = await client.get_led_status()

            result = "LED Status:\n\n"
            for key, value in sorted(status.items()):
                result += f"  {key}: {value}\n"

            return [TextContent(type="text", text=result)]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except CommandError as e:
            return [TextContent(type="text", text=f"Command error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_list_harddrives":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            drives = await client.list_harddrives()

            if not drives or (len(drives) == 1 and "<no harddrives mounted>" in str(drives)):
                return [TextContent(type="text", text="No hard drives mounted.")]

            result = "Mounted Hard Drives:\n\n"
            for key, value in sorted(drives.items()):
                result += f"  {key}: {value}\n"

            return [TextContent(type="text", text=result)]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except CommandError as e:
            return [TextContent(type="text", text=f"Command error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_set_display_mode":
        mode = arguments["mode"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.set_display_mode(mode)
            modes = {0: "window", 1: "fullscreen", 2: "fullwindow"}
            if success:
                return [TextContent(type="text", text=f"Display mode set to {modes.get(mode, mode)}.")]
            else:
                return [TextContent(type="text", text="Failed to set display mode.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except ValueError as e:
            return [TextContent(type="text", text=f"Invalid argument: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_display_mode":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            result = await client.get_display_mode()
            if result:
                mode, mode_name = result
                return [TextContent(type="text", text=f"Display mode: {mode} ({mode_name})")]
            else:
                return [TextContent(type="text", text="Failed to get display mode.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_set_ntsc":
        enabled = arguments["enabled"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.set_ntsc(enabled)
            mode = "NTSC" if enabled else "PAL"
            if success:
                return [TextContent(type="text", text=f"Video mode set to {mode}.")]
            else:
                return [TextContent(type="text", text=f"Failed to set video mode to {mode}.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_ntsc":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            result = await client.get_ntsc()
            if result:
                is_ntsc, mode_name = result
                return [TextContent(type="text", text=f"Video mode: {mode_name}")]
            else:
                return [TextContent(type="text", text="Failed to get video mode.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_set_sound_mode":
        mode = arguments["mode"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.set_sound_mode(mode)
            modes = {0: "off", 1: "normal", 2: "stereo", 3: "best"}
            if success:
                return [TextContent(type="text", text=f"Sound mode set to {modes.get(mode, mode)}.")]
            else:
                return [TextContent(type="text", text="Failed to set sound mode.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except ValueError as e:
            return [TextContent(type="text", text=f"Invalid argument: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_sound_mode":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            result = await client.get_sound_mode()
            if result:
                mode, mode_name = result
                return [TextContent(type="text", text=f"Sound mode: {mode} ({mode_name})")]
            else:
                return [TextContent(type="text", text="Failed to get sound mode.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    # Round 3 runtime control tools
    elif name == "runtime_toggle_mouse_grab":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.toggle_mouse_grab()
            if success:
                return [TextContent(type="text", text="Mouse grab toggled.")]
            else:
                return [TextContent(type="text", text="Failed to toggle mouse grab.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_mouse_speed":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            speed = await client.get_mouse_speed()
            if speed is not None:
                return [TextContent(type="text", text=f"Mouse speed: {speed}")]
            else:
                return [TextContent(type="text", text="Failed to get mouse speed.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_set_cpu_speed":
        speed = arguments["speed"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.set_cpu_speed(speed)
            if success:
                return [TextContent(type="text", text=f"CPU speed set to {speed}.")]
            else:
                return [TextContent(type="text", text="Failed to set CPU speed.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_cpu_speed":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            result = await client.get_cpu_speed()
            if result:
                speed, desc = result
                return [TextContent(type="text", text=f"CPU speed: {speed} ({desc})")]
            else:
                return [TextContent(type="text", text="Failed to get CPU speed.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_toggle_rtg":
        monid = arguments.get("monid", 0)
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            result = await client.toggle_rtg(monid)
            if result:
                return [TextContent(type="text", text=f"Display mode: {result}")]
            else:
                return [TextContent(type="text", text="Failed to toggle RTG.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_set_floppy_speed":
        speed = arguments["speed"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.set_floppy_speed(speed)
            if success:
                desc = {0: "turbo", 100: "1x", 200: "2x", 400: "4x", 800: "8x"}.get(speed, str(speed))
                return [TextContent(type="text", text=f"Floppy speed set to {speed} ({desc}).")]
            else:
                return [TextContent(type="text", text="Failed to set floppy speed.")]
        except ValueError as e:
            return [TextContent(type="text", text=f"Invalid argument: {str(e)}")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_floppy_speed":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            result = await client.get_floppy_speed()
            if result:
                speed, desc = result
                return [TextContent(type="text", text=f"Floppy speed: {speed} ({desc})")]
            else:
                return [TextContent(type="text", text="Failed to get floppy speed.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_disk_write_protect":
        drive = arguments["drive"]
        protect = arguments["protect"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.disk_write_protect(drive, protect)
            if success:
                status = "protected" if protect else "writable"
                return [TextContent(type="text", text=f"Drive DF{drive} set to {status}.")]
            else:
                return [TextContent(type="text", text="Failed to set write protection.")]
        except ValueError as e:
            return [TextContent(type="text", text=f"Invalid argument: {str(e)}")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_disk_write_protect":
        drive = arguments["drive"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            result = await client.get_disk_write_protect(drive)
            if result:
                is_protected, status = result
                return [TextContent(type="text", text=f"Drive DF{drive}: {status}")]
            else:
                return [TextContent(type="text", text="Failed to get write protection status.")]
        except ValueError as e:
            return [TextContent(type="text", text=f"Invalid argument: {str(e)}")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_toggle_status_line":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            result = await client.toggle_status_line()
            if result:
                mode, mode_name = result
                return [TextContent(type="text", text=f"Status line: {mode_name}")]
            else:
                return [TextContent(type="text", text="Failed to toggle status line.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_set_chipset":
        chipset = arguments["chipset"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.set_chipset(chipset)
            if success:
                return [TextContent(type="text", text=f"Chipset set to {chipset}.")]
            else:
                return [TextContent(type="text", text="Failed to set chipset.")]
        except ValueError as e:
            return [TextContent(type="text", text=f"Invalid argument: {str(e)}")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_chipset":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            result = await client.get_chipset()
            if result:
                mask, name = result
                return [TextContent(type="text", text=f"Chipset: {name} (mask={mask})")]
            else:
                return [TextContent(type="text", text="Failed to get chipset.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_memory_config":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            config = await client.get_memory_config()
            result = "Memory configuration:\n"
            for key, value in config.items():
                result += f"  {key}: {value}\n"
            return [TextContent(type="text", text=result)]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_fps":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            info = await client.get_fps()
            result = "Performance info:\n"
            for key, value in info.items():
                result += f"  {key}: {value}\n"
            return [TextContent(type="text", text=result)]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    # Round 4 runtime control tools - Memory and Window Control
    elif name == "runtime_set_chip_mem":
        size_kb = arguments["size_kb"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.set_chip_mem(size_kb)
            if success:
                return [TextContent(type="text", text=f"Chip RAM set to {size_kb} KB. Reset required for changes to take effect.")]
            else:
                return [TextContent(type="text", text="Failed to set Chip RAM size.")]
        except ValueError as e:
            return [TextContent(type="text", text=f"Invalid argument: {str(e)}")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_set_fast_mem":
        size_kb = arguments["size_kb"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.set_fast_mem(size_kb)
            if success:
                return [TextContent(type="text", text=f"Fast RAM set to {size_kb} KB. Reset required for changes to take effect.")]
            else:
                return [TextContent(type="text", text="Failed to set Fast RAM size.")]
        except ValueError as e:
            return [TextContent(type="text", text=f"Invalid argument: {str(e)}")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_set_slow_mem":
        size_kb = arguments["size_kb"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.set_slow_mem(size_kb)
            if success:
                return [TextContent(type="text", text=f"Slow RAM set to {size_kb} KB. Reset required for changes to take effect.")]
            else:
                return [TextContent(type="text", text="Failed to set Slow RAM size.")]
        except ValueError as e:
            return [TextContent(type="text", text=f"Invalid argument: {str(e)}")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_set_z3_mem":
        size_mb = arguments["size_mb"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.set_z3_mem(size_mb)
            if success:
                return [TextContent(type="text", text=f"Z3 Fast RAM set to {size_mb} MB. Reset required for changes to take effect.")]
            else:
                return [TextContent(type="text", text="Failed to set Z3 Fast RAM size.")]
        except ValueError as e:
            return [TextContent(type="text", text=f"Invalid argument: {str(e)}")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_cpu_model":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            info = await client.get_cpu_model()
            result = "CPU Model:\n"
            for key, value in info.items():
                result += f"  {key}: {value}\n"
            return [TextContent(type="text", text=result)]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_set_cpu_model":
        model = arguments["model"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.set_cpu_model(model)
            if success:
                return [TextContent(type="text", text=f"CPU model set to {model}. Reset required for changes to take effect.")]
            else:
                return [TextContent(type="text", text="Failed to set CPU model.")]
        except ValueError as e:
            return [TextContent(type="text", text=f"Invalid argument: {str(e)}")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_set_window_size":
        width = arguments["width"]
        height = arguments["height"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.set_window_size(width, height)
            if success:
                return [TextContent(type="text", text=f"Window size set to {width}x{height}.")]
            else:
                return [TextContent(type="text", text="Failed to set window size.")]
        except ValueError as e:
            return [TextContent(type="text", text=f"Invalid argument: {str(e)}")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_window_size":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            info = await client.get_window_size()
            width = info.get("width", "?")
            height = info.get("height", "?")
            return [TextContent(type="text", text=f"Window size: {width}x{height}")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_set_scaling":
        mode = arguments["mode"]
        mode_names = ["auto", "nearest", "linear", "integer"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.set_scaling(mode)
            if success:
                mode_index = mode + 1  # -1..2 -> 0..3
                mode_name = mode_names[mode_index] if 0 <= mode_index < len(mode_names) else str(mode)
                return [TextContent(type="text", text=f"Scaling mode set to {mode_name}.")]
            else:
                return [TextContent(type="text", text="Failed to set scaling mode.")]
        except ValueError as e:
            return [TextContent(type="text", text=f"Invalid argument: {str(e)}")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_scaling":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            info = await client.get_scaling()
            result = "Scaling:\n"
            for key, value in info.items():
                result += f"  {key}: {value}\n"
            return [TextContent(type="text", text=result)]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_set_line_mode":
        mode = arguments["mode"]
        mode_names = ["single", "double", "scanlines"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.set_line_mode(mode)
            if success:
                mode_name = mode_names[mode] if 0 <= mode < len(mode_names) else str(mode)
                return [TextContent(type="text", text=f"Line mode set to {mode_name}.")]
            else:
                return [TextContent(type="text", text="Failed to set line mode.")]
        except ValueError as e:
            return [TextContent(type="text", text=f"Invalid argument: {str(e)}")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_line_mode":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            info = await client.get_line_mode()
            result = "Line mode:\n"
            for key, value in info.items():
                result += f"  {key}: {value}\n"
            return [TextContent(type="text", text=result)]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_set_resolution":
        mode = arguments["mode"]
        mode_names = ["lores", "hires", "superhires"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.set_resolution(mode)
            if success:
                mode_name = mode_names[mode] if 0 <= mode < len(mode_names) else str(mode)
                return [TextContent(type="text", text=f"Resolution set to {mode_name}.")]
            else:
                return [TextContent(type="text", text="Failed to set resolution.")]
        except ValueError as e:
            return [TextContent(type="text", text=f"Invalid argument: {str(e)}")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_resolution":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            result = await client.get_resolution()
            if result:
                mode, mode_name = result
                return [TextContent(type="text", text=f"Resolution: {mode_name} ({mode})")]
            else:
                return [TextContent(type="text", text="Failed to get resolution.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    # Round 5 - Autocrop and WHDLoad
    elif name == "runtime_set_autocrop":
        enabled = arguments["enabled"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.set_autocrop(enabled)
            if success:
                return [TextContent(type="text", text=f"Autocrop {'enabled' if enabled else 'disabled'}.")]
            else:
                return [TextContent(type="text", text="Failed to set autocrop.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_autocrop":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            result = await client.get_autocrop()
            if result is not None:
                return [TextContent(type="text", text=f"Autocrop: {'enabled' if result else 'disabled'}")]
            else:
                return [TextContent(type="text", text="Failed to get autocrop status.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_insert_whdload":
        path = arguments["path"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.insert_whdload(path)
            if success:
                return [TextContent(type="text", text=f"WHDLoad game loaded: {path}\nNote: A reset may be required for the game to start.")]
            else:
                return [TextContent(type="text", text="Failed to load WHDLoad game.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_eject_whdload":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.eject_whdload()
            if success:
                return [TextContent(type="text", text="WHDLoad game ejected.")]
            else:
                return [TextContent(type="text", text="Failed to eject WHDLoad game.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_whdload":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            info = await client.get_whdload()
            if info:
                if info.get("loaded") == "0":
                    return [TextContent(type="text", text="No WHDLoad game loaded.")]
                result = "WHDLoad game:\n"
                for key, value in info.items():
                    if value:  # Only show non-empty values
                        result += f"  {key}: {value}\n"
                return [TextContent(type="text", text=result)]
            else:
                return [TextContent(type="text", text="Failed to get WHDLoad info.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    # Round 6 - Debugging and Diagnostics
    elif name == "runtime_debug_activate":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.debug_activate()
            if success:
                return [TextContent(type="text", text="Debugger activated.")]
            else:
                return [TextContent(type="text", text="Failed to activate debugger. Amiberry may not be built with debugger support.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_debug_deactivate":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.debug_deactivate()
            if success:
                return [TextContent(type="text", text="Debugger deactivated, emulation resumed.")]
            else:
                return [TextContent(type="text", text="Failed to deactivate debugger.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_debug_status":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            info = await client.debug_status()
            if info:
                result = "Debugger status:\n"
                for key, value in info.items():
                    result += f"  {key}: {value}\n"
                return [TextContent(type="text", text=result)]
            else:
                return [TextContent(type="text", text="Failed to get debugger status.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_debug_step":
        count = arguments.get("count", 1)
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.debug_step(count)
            if success:
                return [TextContent(type="text", text=f"Stepped {count} instruction(s).")]
            else:
                return [TextContent(type="text", text="Failed to step. Debugger may not be active.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_debug_continue":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.debug_continue()
            if success:
                return [TextContent(type="text", text="Execution continued.")]
            else:
                return [TextContent(type="text", text="Failed to continue execution.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_cpu_regs":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            info = await client.get_cpu_regs()
            if info:
                result = "CPU Registers:\n"
                # Format nicely: D0-D7 on one section, A0-A7 on another
                data_regs = [f"  {k}: {v}" for k, v in info.items() if k.startswith("D")]
                addr_regs = [f"  {k}: {v}" for k, v in info.items() if k.startswith("A")]
                other_regs = [f"  {k}: {v}" for k, v in info.items() if not k.startswith("D") and not k.startswith("A")]
                result += "Data registers:\n" + "\n".join(data_regs) + "\n"
                result += "Address registers:\n" + "\n".join(addr_regs) + "\n"
                result += "Other:\n" + "\n".join(other_regs)
                return [TextContent(type="text", text=result)]
            else:
                return [TextContent(type="text", text="Failed to get CPU registers.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_custom_regs":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            info = await client.get_custom_regs()
            if info:
                result = "Custom Chip Registers:\n"
                for key, value in info.items():
                    result += f"  {key}: {value}\n"
                return [TextContent(type="text", text=result)]
            else:
                return [TextContent(type="text", text="Failed to get custom registers.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_disassemble":
        address = arguments["address"]
        count = arguments.get("count", 10)
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            lines = await client.disassemble(address, count)
            if lines:
                result = f"Disassembly at {address}:\n"
                for line in lines:
                    result += f"  {line}\n"
                return [TextContent(type="text", text=result)]
            else:
                return [TextContent(type="text", text="No disassembly returned.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_set_breakpoint":
        address = arguments["address"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.set_breakpoint(address)
            if success:
                return [TextContent(type="text", text=f"Breakpoint set at {address}.")]
            else:
                return [TextContent(type="text", text="Failed to set breakpoint. Maximum 20 breakpoints allowed.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_clear_breakpoint":
        address = arguments["address"]
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            success = await client.clear_breakpoint(address)
            if success:
                if address.upper() == "ALL":
                    return [TextContent(type="text", text="All breakpoints cleared.")]
                else:
                    return [TextContent(type="text", text=f"Breakpoint at {address} cleared.")]
            else:
                return [TextContent(type="text", text="Failed to clear breakpoint.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_list_breakpoints":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            breakpoints = await client.list_breakpoints()
            if breakpoints:
                result = "Active breakpoints:\n"
                for bp in breakpoints:
                    result += f"  {bp}\n"
                return [TextContent(type="text", text=result)]
            else:
                return [TextContent(type="text", text="No active breakpoints.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_copper_state":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            info = await client.get_copper_state()
            if info:
                result = "Copper State:\n"
                for key, value in info.items():
                    result += f"  {key}: {value}\n"
                return [TextContent(type="text", text=result)]
            else:
                return [TextContent(type="text", text="Failed to get Copper state.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_blitter_state":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            info = await client.get_blitter_state()
            if info:
                result = "Blitter State:\n"
                for key, value in info.items():
                    result += f"  {key}: {value}\n"
                return [TextContent(type="text", text=result)]
            else:
                return [TextContent(type="text", text="Failed to get Blitter state.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_drive_state":
        drive = arguments.get("drive")
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            info = await client.get_drive_state(drive)
            if info:
                result = f"Drive State{' (DF' + str(drive) + ')' if drive is not None else ''}:\n"
                for key, value in info.items():
                    result += f"  {key}: {value}\n"
                return [TextContent(type="text", text=result)]
            else:
                return [TextContent(type="text", text="Failed to get drive state.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_audio_state":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            info = await client.get_audio_state()
            if info:
                result = "Audio State:\n"
                for key, value in info.items():
                    result += f"  {key}: {value}\n"
                return [TextContent(type="text", text=result)]
            else:
                return [TextContent(type="text", text="Failed to get audio state.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    elif name == "runtime_get_dma_state":
        try:
            client = AmiberryIPCClient(prefer_dbus=False)
            info = await client.get_dma_state()
            if info:
                result = "DMA State:\n"
                for key, value in info.items():
                    result += f"  {key}: {value}\n"
                return [TextContent(type="text", text=result)]
            else:
                return [TextContent(type="text", text="Failed to get DMA state.")]
        except IPCConnectionError as e:
            return [TextContent(type="text", text=f"Connection error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


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
