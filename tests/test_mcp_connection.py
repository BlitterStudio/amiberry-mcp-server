#!/usr/bin/env python3
"""
Test MCP server connection via stdio
This tests the actual MCP protocol communication using the MCP SDK client
"""

import asyncio
import sys
from pathlib import Path

# Try to import MCP client SDK
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    MCP_CLIENT_AVAILABLE = True
except ImportError:
    MCP_CLIENT_AVAILABLE = False
    print("=" * 50)
    print("MCP Client SDK not found!")
    print("=" * 50)
    print()
    print("The MCP client SDK needs to be installed to run this test.")
    print()
    print("To install, run:")
    print("  scripts/install.sh")
    print()
    print("Or manually install in your virtual environment:")
    print("  source venv/bin/activate")
    print("  pip install -e .")
    print()
    sys.exit(1)


async def test_mcp_protocol():
    """Test the MCP protocol communication using the MCP SDK client"""

    print("=" * 50)
    print("MCP Protocol Connection Test")
    print("=" * 50)
    print()

    # Get the project root directory
    test_dir = Path(__file__).parent
    project_dir = test_dir.parent
    python_path = project_dir / "venv" / "bin" / "python"

    if not python_path.exists():
        print(f"Error: Virtual environment Python not found: {python_path}")
        print("  Run scripts/install.sh first")
        return False

    print(f"Starting server from: {project_dir}")
    print()

    try:
        # Configure server parameters - use module invocation
        server_params = StdioServerParameters(
            command=str(python_path), args=["-m", "amiberry_mcp.server"], env=None
        )

        # Connect to the server using MCP SDK client
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                # Test 1: Initialize
                print("Test 1: Initialize connection")
                print("-" * 50)
                init_result = await session.initialize()
                print("Server initialized successfully")
                print(
                    f"  Server: {init_result.serverInfo.name} v{init_result.serverInfo.version}"
                )
                print()

                # Test 2: List tools
                print("Test 2: List available tools")
                print("-" * 50)
                tools = await session.list_tools()
                print(f"Found {len(tools.tools)} tools:")
                for tool in tools.tools:
                    print(f"  - {tool.name}: {tool.description}")
                print()

                # Test 3: Call a tool
                print("Test 3: Call get_platform_info tool")
                print("-" * 50)
                result = await session.call_tool("get_platform_info", {})
                print("Tool executed successfully:")
                from mcp.types import TextContent

                for content in result.content:
                    if isinstance(content, TextContent):
                        print(content.text)
                print()

                print("=" * 50)
                print("MCP Protocol Test Completed Successfully")
                print("=" * 50)
                print()
                print("Your server is ready to use with Claude Desktop!")
                print()

                return True

    except Exception as e:
        print(f"Error during testing: {e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    """Main entry point"""
    success = asyncio.run(test_mcp_protocol())
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
