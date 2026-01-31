# Amiberry MCP Server

An MCP (Model Context Protocol) server for controlling Amiberry, the Amiga emulator, through Claude AI.

## Features

- Browse and launch Amiberry configurations
- Search for disk images (ADF, HDF, DMS, LHA)
- List and manage savestates
- View configuration file contents
- Launch emulator with specific models (A500, A1200, CD32)
- HTTP API for voice assistants (Siri, Google Assistant) and automation

## Requirements

- Python 3.10 or higher
- Amiberry emulator installed:
  - **macOS**: Amiberry.app in `/Applications`
  - **Linux**: `amiberry` command in PATH
- Claude Desktop application (for MCP integration)

## Project Structure

```
amiberry-mcp-server/
├── src/
│   └── amiberry_mcp/
│       ├── __init__.py
│       ├── config.py        # Shared configuration
│       ├── server.py        # MCP server
│       └── http_server.py   # HTTP API server
├── scripts/
│   ├── install.sh           # MCP server installer
│   ├── install_http_api.sh  # HTTP API installer
│   ├── start_http_api.sh    # HTTP API launcher
│   ├── uninstall.sh         # Uninstaller
│   └── test_http_api.sh     # HTTP API tests
├── tests/
│   ├── test_server.py       # Server unit tests
│   └── test_mcp_connection.py
├── docs/
│   ├── HTTP_API_GUIDE.md    # HTTP API documentation
│   └── QUICKSTART_HTTP_API.md
├── pyproject.toml
├── README.md
└── LICENSE
```

## Installation

### Quick Install (Recommended)

```bash
# Clone the repository
git clone https://github.com/midwan/amiberry-mcp-server.git
cd amiberry-mcp-server

# Run the installer
./scripts/install.sh
```

The installer will:
1. Create a Python virtual environment
2. Install dependencies
3. Configure Claude Desktop automatically

### Manual Installation

```bash
# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install the package
pip install -e .

# Configure Claude Desktop manually (see below)
```

### Claude Desktop Configuration

Edit your Claude Desktop configuration file:

**macOS:**
```bash
nano ~/Library/Application\ Support/Claude/claude_desktop_config.json
```

**Linux:**
```bash
nano ~/.config/Claude/claude_desktop_config.json
```

Add this configuration:
```json
{
  "mcpServers": {
    "amiberry": {
      "command": "/path/to/amiberry-mcp-server/venv/bin/python",
      "args": ["-m", "amiberry_mcp.server"]
    }
  }
}
```

Then restart Claude Desktop.

## Verification

After installation, restart Claude Desktop. You should see a hammer icon in the input area indicating MCP tools are available.

Try asking Claude:
- "What Amiberry configurations do I have?"
- "Show me my disk images"
- "Launch Amiberry with the A500 model"

## Default Directory Structure

### macOS
```
~/Amiberry/
├── Configurations/    # .uae config files
├── Floppies/         # .adf, .adz, .dms files
├── Harddrives/       # .hdf, .hdz files
├── Lha/              # .lha archives
├── Savestates/       # .uss savestate files
└── Screenshots/
```

### Linux
```
~/Amiberry/
├── conf/             # .uae config files
├── floppies/         # .adf, .adz, .dms files
├── harddrives/       # .hdf, .hdz files
├── lha/              # .lha archives
├── savestates/       # .uss savestate files
└── screenshots/

~/.config/amiberry/   # System configs (optional)
```

## Available MCP Tools

| Tool | Description |
|------|-------------|
| `list_configs` | List available configuration files |
| `get_config_content` | View contents of a config file |
| `list_disk_images` | Search for disk images (ADF/HDF/DMS/LHA) |
| `launch_amiberry` | Launch with config, model, disk, or LHA file |
| `list_savestates` | List available savestate files |
| `get_platform_info` | Show platform and path information |

## Usage Examples

Ask Claude:
- "List my Amiberry configurations"
- "Show me all Workbench disk images"
- "Launch Amiberry with the A1200 model"
- "Launch this LHA file: ~/Amiberry/Lha/game.lha"
- "What savestates do I have?"
- "Find disk images containing 'Shadow of the Beast'"

## HTTP API

Control Amiberry via REST API for voice assistants and automation.

### Quick Start

```bash
# Install HTTP API dependencies
./scripts/install_http_api.sh

# Start the server
./scripts/start_http_api.sh
```

The API runs on `http://localhost:8080`. View documentation at `http://localhost:8080/docs`.

### Example Commands

**Siri/Voice:**
- "Hey Siri, launch Amiga 500"
- "Hey Siri, stop Amiberry"

**curl:**
```bash
curl -X POST http://localhost:8080/quick-launch/A500
curl -X POST http://localhost:8080/stop
curl http://localhost:8080/configs
```

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/status` | GET | Check if Amiberry is running |
| `/stop` | POST | Stop all Amiberry instances |
| `/configs` | GET | List configurations |
| `/disk-images` | GET | List disk images |
| `/savestates` | GET | List savestates |
| `/launch` | POST | Launch with full options |
| `/quick-launch/{name}` | POST | Quick launch by model/config |
| `/platform` | GET | Get platform info |

See [docs/HTTP_API_GUIDE.md](docs/HTTP_API_GUIDE.md) for complete documentation.

### Auto-start

**macOS:**
```bash
launchctl load ~/Library/LaunchAgents/com.amiberry.httpapi.plist
```

**Linux:**
```bash
systemctl --user enable amiberry-http-api.service
systemctl --user start amiberry-http-api.service
```

## Development

```bash
# Clone and setup
git clone https://github.com/midwan/amiberry-mcp-server.git
cd amiberry-mcp-server

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install with dev dependencies
pip install -e ".[all]"

# Run tests
python tests/test_server.py
python tests/test_mcp_connection.py
```

## Troubleshooting

### MCP tools not appearing in Claude
- Restart Claude Desktop completely (quit and reopen)
- Check paths in `claude_desktop_config.json`
- Verify the virtual environment exists

### "Command not found" errors
- **Linux**: Ensure `amiberry` is in your PATH
- **macOS**: Verify Amiberry.app is in `/Applications`

### Permission errors
- Check that scripts are executable: `chmod +x scripts/*.sh`

### View logs (macOS)
```bash
tail -f ~/Library/Logs/Claude/mcp*.log
```

## Uninstall

```bash
./scripts/uninstall.sh
```

## Contributing

Contributions welcome! Please open an issue or pull request.

## License

MIT License - see [LICENSE](LICENSE) file.

## Resources

- [MCP Documentation](https://modelcontextprotocol.io)
- [Amiberry Project](https://github.com/BlitterStudio/amiberry)
- [Claude Desktop](https://claude.ai/download)
- [FastAPI Documentation](https://fastapi.tiangolo.com)
