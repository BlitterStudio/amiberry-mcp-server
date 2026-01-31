#!/usr/bin/env python3
"""
MCP Server for Amiberry emulator control.
Enables Claude AI to interact with Amiberry through the Model Context Protocol.
"""

import asyncio
import datetime
import subprocess
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.types import Tool, TextContent
import mcp.server.stdio

from .config import (
    IS_LINUX,
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
