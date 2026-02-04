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
