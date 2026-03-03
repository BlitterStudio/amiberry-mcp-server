#!/usr/bin/env python3
"""
Test MCP server connection via stdio
This tests the actual MCP protocol communication using the MCP SDK client
"""

import sys
from pathlib import Path

import pytest

# Try to import MCP client SDK
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    MCP_CLIENT_AVAILABLE = True
except ImportError:
    MCP_CLIENT_AVAILABLE = False


@pytest.mark.skipif(not MCP_CLIENT_AVAILABLE, reason="MCP client SDK not installed")
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
        pytest.skip(f"Virtual environment Python not found: {python_path}")

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
        pytest.fail(f"MCP protocol test failed: {e}")


def main():
    """Main entry point"""
    sys.exit(pytest.main([__file__, "-v"]))


if __name__ == "__main__":
    main()
