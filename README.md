# Amiberry MCP Server

An MCP (Model Context Protocol) server for controlling Amiberry, the Amiga emulator, through Claude AI.

## Features

### Core Features
- Browse and launch Amiberry configurations
- Search for disk images (ADF, HDF, DMS, LHA, ISO, CUE, CHD)
- List and manage savestates
- View and edit configuration file contents
- Launch emulator with specific models (A500, A500+, A600, A1200, A4000, CD32, CDTV)
- HTTP API for voice assistants (Siri, Google Assistant) and automation

### Runtime Control (NEW)
- **Pause/Resume**: Control running emulation via IPC
- **Save/Load State**: Save and restore states while running
- **Disk Swapping**: Insert floppy/CD images into running emulation
- **Live Configuration**: Query and modify config options at runtime
- **Screenshots**: Capture screenshots from running emulation
- **Cross-platform**: Works on Linux, macOS, and FreeBSD

### Developer/Debug Features
- **Log Capture**: Launch with logging enabled and capture output to files
- **Config Editor**: Parse, modify, and create .uae configuration files
- **Savestate Inspector**: Read metadata from .uss savestate files
- **ROM Manager**: Identify and catalog Kickstart ROMs by checksum

### Game Launcher Features
- **WHDLoad Launcher**: Search and launch WHDLoad games from LHA archives
- **CD Image Launcher**: Launch CD32/CDTV games with auto-detection
- **Multi-Disk Support**: Configure disk swapper for multi-disk games
- **Config Templates**: Generate configs from pre-made model templates

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
│       ├── config.py          # Shared configuration
│       ├── server.py          # MCP server (48 tools)
│       ├── http_server.py     # HTTP API server
│       ├── ipc_client.py      # IPC client for runtime control
│       ├── uae_config.py      # Config file parser/generator
│       ├── savestate.py       # Savestate metadata parser
│       └── rom_manager.py     # ROM identification
├── scripts/
│   ├── install.sh             # MCP server installer
│   ├── install_http_api.sh    # HTTP API installer
│   ├── start_http_api.sh      # HTTP API launcher
│   ├── uninstall.sh           # Uninstaller
│   └── test_http_api.sh       # HTTP API tests
├── tests/
│   ├── test_server.py         # Server unit tests
│   ├── test_mcp_connection.py
│   └── test_uae_config.py     # Config parser tests
├── docs/
│   ├── HTTP_API_GUIDE.md      # HTTP API documentation
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
├── Lha/              # .lha archives (WHDLoad games)
├── Savestates/       # .uss savestate files
├── Screenshots/
├── Kickstarts/       # Kickstart ROM files
└── logs/             # Captured log files
```

### Linux
```
~/Amiberry/
├── conf/             # .uae config files
├── floppies/         # .adf, .adz, .dms files
├── harddrives/       # .hdf, .hdz files
├── lha/              # .lha archives (WHDLoad games)
├── savestates/       # .uss savestate files
├── screenshots/
├── kickstarts/       # Kickstart ROM files
└── logs/             # Captured log files

~/.config/amiberry/   # System configs (optional)
```

## Available MCP Tools

### Core Tools
| Tool | Description |
|------|-------------|
| `list_configs` | List available configuration files |
| `get_config_content` | View contents of a config file |
| `list_disk_images` | Search for disk images (ADF/HDF/DMS/LHA) |
| `launch_amiberry` | Launch with config, model, disk, or LHA file |
| `list_savestates` | List available savestate files |
| `get_platform_info` | Show platform and path information |

### Configuration Tools
| Tool | Description |
|------|-------------|
| `parse_config` | Parse .uae config file into structured data |
| `modify_config` | Change specific options in a config file |
| `create_config` | Generate new config from template |

### Launch Tools
| Tool | Description |
|------|-------------|
| `launch_with_logging` | Launch with --log flag and capture output |
| `launch_whdload` | Search and launch WHDLoad games |
| `launch_cd` | Launch CD images (ISO/CUE/CHD) |
| `set_disk_swapper` | Configure multi-disk game support |

### Media Tools
| Tool | Description |
|------|-------------|
| `list_cd_images` | List available CD images |
| `list_logs` | List captured log files |
| `get_log_content` | Read a captured log file |

### Analysis Tools
| Tool | Description |
|------|-------------|
| `inspect_savestate` | Read metadata from .uss savestate files |
| `list_roms` | List available ROMs with identification |
| `identify_rom` | Get ROM details by checksum |
| `get_amiberry_version` | Get Amiberry version info |

### Runtime Control Tools

#### Emulation Control
| Tool | Description |
|------|-------------|
| `pause_emulation` | Pause a running emulation |
| `resume_emulation` | Resume a paused emulation |
| `reset_emulation` | Soft or hard reset |
| `frame_advance` | Advance N frames when paused |

#### Media Control
| Tool | Description |
|------|-------------|
| `runtime_insert_floppy` | Insert floppy disk into drive |
| `runtime_eject_floppy` | Eject floppy from drive |
| `list_floppies` | List all floppy drives and contents |
| `runtime_insert_cd` | Insert CD image |
| `runtime_eject_cd` | Eject CD |

#### State Management
| Tool | Description |
|------|-------------|
| `runtime_screenshot` | Take a screenshot |
| `runtime_save_state` | Save state while running |
| `runtime_load_state` | Load a savestate |

#### Audio Control
| Tool | Description |
|------|-------------|
| `set_volume` | Set master volume (0-100) |
| `get_volume` | Get current volume |
| `mute` | Mute audio |
| `unmute` | Unmute audio |

#### Display Control
| Tool | Description |
|------|-------------|
| `toggle_fullscreen` | Toggle fullscreen/windowed mode |
| `set_warp` | Enable/disable warp mode |
| `get_warp` | Get warp mode status |

#### Configuration
| Tool | Description |
|------|-------------|
| `get_runtime_status` | Get emulation status |
| `runtime_get_config` | Get config option value |
| `runtime_set_config` | Set config option |
| `list_configs` | List available config files |

#### Input Control
| Tool | Description |
|------|-------------|
| `send_key` | Send keyboard input |
| `send_mouse` | Send mouse movement and buttons |
| `set_mouse_speed` | Set mouse sensitivity (10-200) |

#### Utility
| Tool | Description |
|------|-------------|
| `get_version` | Get Amiberry and SDL version info |
| `ping` | Test IPC connection (returns PONG) |
| `check_ipc_connection` | Check IPC availability |

> **Note:** Runtime control requires Amiberry built with `USE_IPC_SOCKET=ON`

## Usage Examples

### Basic Usage
Ask Claude:
- "List my Amiberry configurations"
- "Show me all Workbench disk images"
- "Launch Amiberry with the A1200 model"
- "What savestates do I have?"
- "Find disk images containing 'Shadow of the Beast'"

### WHDLoad Games
- "Launch the WHDLoad game 'Turrican'"
- "Search for WHDLoad games with 'Adventure' in the name"

### CD32/CDTV Games
- "List my CD images"
- "Launch the CD32 game from /path/to/game.iso"

### Configuration Management
- "Parse my A500 config file and show the memory settings"
- "Create a new A1200 config with 8MB Fast RAM"
- "Change the floppy speed to 800 in my gaming config"

### Debugging
- "Launch Amiberry with logging enabled"
- "Show me the last captured log file"
- "What ROMs do I have available?"
- "Inspect my savestate from yesterday"

### Savestate Analysis
- "What CPU and chipset is my savestate using?"
- "Show me metadata from my Shadow of the Beast savestate"

### Runtime Control
- "Pause the emulation"
- "Take a screenshot of the current state"
- "Save the game state to checkpoint.uss"
- "Insert disk 2 into drive DF0"
- "Eject the floppy from drive 0"
- "List what's in all the floppy drives"
- "What's the current emulation status?"
- "Set the floppy speed to 800"
- "Set the volume to 50%"
- "Mute the audio"
- "Toggle fullscreen mode"
- "Enable warp mode"
- "Advance one frame"
- "What version of Amiberry is running?"
- "Ping Amiberry to check the connection"

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
# Basic operations
curl -X POST http://localhost:8080/quick-launch/A500
curl -X POST http://localhost:8080/stop
curl http://localhost:8080/configs

# Launch with logging
curl -X POST http://localhost:8080/launch-with-logging \
  -H "Content-Type: application/json" \
  -d '{"model": "A500"}'

# Create a config
curl -X POST "http://localhost:8080/configs/create/my-config?model=A1200"

# Launch WHDLoad game
curl -X POST "http://localhost:8080/launch-whdload?search=Turrican"

# Launch CD image
curl -X POST http://localhost:8080/launch-cd \
  -H "Content-Type: application/json" \
  -d '{"cd_path": "/path/to/game.iso"}'

# Inspect savestate
curl http://localhost:8080/savestates/mysave.uss/inspect

# List ROMs
curl http://localhost:8080/roms

# Runtime control (requires Amiberry with USE_IPC_SOCKET=ON)
curl http://localhost:8080/runtime/status
curl -X POST http://localhost:8080/runtime/pause
curl -X POST http://localhost:8080/runtime/resume
curl -X POST http://localhost:8080/runtime/screenshot \
  -H "Content-Type: application/json" \
  -d '{"filename": "/tmp/screenshot.png"}'
curl -X POST http://localhost:8080/runtime/insert-floppy \
  -H "Content-Type: application/json" \
  -d '{"drive": 0, "image_path": "/path/to/disk2.adf"}'
curl -X POST http://localhost:8080/runtime/eject-floppy \
  -H "Content-Type: application/json" \
  -d '{"drive": 0}'
curl http://localhost:8080/runtime/list-floppies

# Audio control
curl http://localhost:8080/runtime/volume
curl -X POST http://localhost:8080/runtime/volume \
  -H "Content-Type: application/json" \
  -d '{"volume": 50}'
curl -X POST http://localhost:8080/runtime/mute
curl -X POST http://localhost:8080/runtime/unmute

# Display control
curl -X POST http://localhost:8080/runtime/fullscreen
curl http://localhost:8080/runtime/warp
curl -X POST http://localhost:8080/runtime/warp \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}'

# Utility
curl http://localhost:8080/runtime/version
curl http://localhost:8080/runtime/ping
```

### API Endpoints

#### Core Endpoints
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

#### Configuration Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/configs/{name}/parsed` | GET | Get parsed config as JSON |
| `/configs/create/{name}` | POST | Create new config from template |
| `/configs/{name}` | PATCH | Modify existing config |

#### Launch Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/launch-with-logging` | POST | Launch with log capture |
| `/launch-whdload` | POST | Launch WHDLoad game |
| `/launch-cd` | POST | Launch CD image |
| `/disk-swapper` | POST | Configure disk swapper |

#### Media Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/cd-images` | GET | List CD images |
| `/logs` | GET | List captured logs |
| `/logs/{name}` | GET | Get log content |

#### Analysis Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/savestates/{name}/inspect` | GET | Get savestate metadata |
| `/roms` | GET | List identified ROMs |
| `/roms/identify` | POST | Identify ROM by path |
| `/version` | GET | Get Amiberry version |

#### Runtime Control Endpoints

**Emulation Control**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/runtime/status` | GET | Get emulation status |
| `/runtime/pause` | POST | Pause emulation |
| `/runtime/resume` | POST | Resume emulation |
| `/runtime/reset` | POST | Soft or hard reset |
| `/runtime/quit` | POST | Quit Amiberry |
| `/runtime/frame-advance` | POST | Advance N frames when paused |

**Media Control**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/runtime/insert-floppy` | POST | Insert floppy disk |
| `/runtime/eject-floppy` | POST | Eject floppy from drive |
| `/runtime/list-floppies` | GET | List all floppy drives |
| `/runtime/insert-cd` | POST | Insert CD image |
| `/runtime/eject-cd` | POST | Eject CD |

**State Management**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/runtime/screenshot` | POST | Take a screenshot |
| `/runtime/save-state` | POST | Save state while running |
| `/runtime/load-state` | POST | Load a savestate |

**Audio Control**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/runtime/volume` | GET | Get current volume |
| `/runtime/volume` | POST | Set volume (0-100) |
| `/runtime/mute` | POST | Mute audio |
| `/runtime/unmute` | POST | Unmute audio |

**Display Control**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/runtime/fullscreen` | POST | Toggle fullscreen |
| `/runtime/warp` | GET | Get warp mode status |
| `/runtime/warp` | POST | Set warp mode |

**Configuration**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/runtime/config/{option}` | GET | Get config option value |
| `/runtime/config` | POST | Set config option |
| `/runtime/configs` | GET | List available configs |

**Input Control**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/runtime/key` | POST | Send keyboard input |
| `/runtime/mouse` | POST | Send mouse input |
| `/runtime/mouse-speed` | POST | Set mouse sensitivity |

**Utility**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/runtime/version` | GET | Get Amiberry version |
| `/runtime/ping` | GET | Test IPC connection |
| `/runtime/ipc-check` | GET | Check IPC availability |

> **Note:** Runtime endpoints require Amiberry built with `USE_IPC_SOCKET=ON`

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
pytest tests/
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

### View captured Amiberry logs
```bash
# macOS
ls ~/Amiberry/logs/

# Linux
ls ~/Amiberry/logs/
```

### Runtime control not working
- Ensure Amiberry was built with `USE_IPC_SOCKET=ON` (CMake option)
- Check if the socket exists: `ls /tmp/amiberry.sock` (or `$XDG_RUNTIME_DIR/amiberry.sock` on Linux)
- Verify Amiberry is running before using runtime control tools
- Test the socket directly: `echo "GET_STATUS" | nc -U /tmp/amiberry.sock`

## Uninstall

```bash
./scripts/uninstall.sh
```

## Contributing

Contributions welcome! Please open an issue or pull request.

## License

GPL-3.0 License - see [LICENSE](LICENSE) file.

## Resources

- [MCP Documentation](https://modelcontextprotocol.io)
- [Amiberry Project](https://github.com/BlitterStudio/amiberry)
- [Claude Desktop](https://claude.ai/download)
- [FastAPI Documentation](https://fastapi.tiangolo.com)
