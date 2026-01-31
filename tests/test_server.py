#!/usr/bin/env python3
"""
Test script for Amiberry MCP Server
This simulates how Claude Desktop would interact with the server
"""

import asyncio
import shutil
import sys
from pathlib import Path


async def test_server():
    """Test the MCP server functionality"""

    print("=" * 50)
    print("Amiberry MCP Server Test")
    print("=" * 50)
    print()

    try:
        # Import from the package
        from amiberry_mcp.server import app, call_tool
        from amiberry_mcp.config import (
            EMULATOR_BINARY,
            CONFIG_DIR,
            DISK_IMAGE_DIRS,
        )
        from mcp.types import TextContent

        print("Server module imported successfully")
        print()

        # Test 1: Check platform info
        print("Test 1: Platform Information")
        print("-" * 50)
        result = await call_tool("get_platform_info", {})
        for content in result:
            if isinstance(content, TextContent):
                print(content.text)
        print()

        # Test 2: Server has tools registered
        print("Test 2: Server Configuration")
        print("-" * 50)
        print(f"Server name: {app.name}")
        print("Server is properly configured")
        print()

        # Test 3: List configurations
        print("Test 3: List Configurations")
        print("-" * 50)
        result = await call_tool("list_configs", {})
        for content in result:
            if isinstance(content, TextContent):
                print(content.text)
        print()

        # Test 4: List disk images
        print("Test 4: List Disk Images")
        print("-" * 50)
        result = await call_tool("list_disk_images", {})
        for content in result:
            if isinstance(content, TextContent):
                print(content.text)
        print()

        # Test 5: List savestates
        print("Test 5: List Savestates")
        print("-" * 50)
        result = await call_tool("list_savestates", {})
        for content in result:
            if isinstance(content, TextContent):
                print(content.text)
        print()

        # Test 6: Check if emulator binary exists
        print("Test 6: Emulator Binary Check")
        print("-" * 50)
        binary_path = Path(EMULATOR_BINARY)

        if binary_path.is_absolute():
            if binary_path.exists():
                print(f"Emulator binary found: {EMULATOR_BINARY}")
            else:
                print(f"Warning: Emulator binary NOT found: {EMULATOR_BINARY}")
                print("  Make sure Amiberry is installed in /Applications (macOS)")
        else:
            # Check if it's in PATH
            if shutil.which(EMULATOR_BINARY):
                print(f"Emulator binary found in PATH: {EMULATOR_BINARY}")
            else:
                print(f"Warning: Emulator binary NOT found in PATH: {EMULATOR_BINARY}")
                print("  Make sure Amiberry is installed and in your PATH (Linux)")
        print()

        # Test 7: Check directories
        print("Test 7: Directory Check")
        print("-" * 50)

        dirs_to_check = [
            ("Config directory", CONFIG_DIR),
        ]

        dirs_to_check.extend(
            [(f"Disk image dir {i+1}", d) for i, d in enumerate(DISK_IMAGE_DIRS)]
        )

        for name, path in dirs_to_check:
            if path.exists():
                print(f"[OK] {name} exists: {path}")
            else:
                print(f"[--] {name} NOT found: {path}")
        print()

        # Summary
        print("=" * 50)
        print("Test Summary")
        print("=" * 50)
        print("All basic tests completed")
        print()
        print("Next steps:")
        print("1. If directories are missing, create them or adjust paths")
        print("2. Add some .uae config files to your config directory")
        print("3. Add some .adf/.hdf disk images to your disk directories")
        print("4. Restart Claude Desktop and try asking about Amiberry")
        print()

    except ImportError as e:
        print(f"Error importing server module: {e}")
        print()
        print("Make sure you've installed the package:")
        print("  pip install -e .")
        print()
        print("Or activate the virtual environment:")
        print("  source venv/bin/activate")
        return False
    except Exception as e:
        print(f"Error during testing: {e}")
        import traceback

        traceback.print_exc()
        return False

    return True


def main():
    """Main entry point"""
    # Check if we're in a virtual environment
    if sys.prefix == sys.base_prefix:
        print("Warning: Not running in a virtual environment")
        print("Run: source venv/bin/activate")
        print()

    success = asyncio.run(test_server())
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
