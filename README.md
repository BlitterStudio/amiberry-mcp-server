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
- **Log Tailing**: Incremental log reading and pattern-based waiting
- **Crash Detection**: Automatic crash detection via process signals and log scanning
- **Config Editor**: Parse, modify, and create .uae configuration files
- **Savestate Inspector**: Read metadata from .uss savestate files
- **ROM Manager**: Identify and catalog Kickstart ROMs by checksum
- **Memory Access**: Read/write emulated Amiga memory for debugging

### Autonomous Troubleshooting
- **Process Lifecycle**: Track, monitor, kill, and restart Amiberry processes
- **Health Check**: Combined process + IPC + emulation status check
- **Launch and Wait**: Launch Amiberry and wait until IPC is ready for commands
- **Screenshot Analysis**: Capture screenshots with image data returned for AI analysis
- **Crash Recovery**: Detect crashes, analyze logs, restart automatically

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
| `runtime_quicksave` | Quick save to slot (0-9) |
| `runtime_quickload` | Quick load from slot (0-9) |

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
| `runtime_set_display_mode` | Set mode (0=window, 1=fullscreen, 2=fullwindow) |
| `runtime_get_display_mode` | Get current display mode |
| `runtime_set_ntsc` | Set video mode (0=PAL, 1=NTSC) |
| `runtime_get_ntsc` | Get current video mode (PAL/NTSC) |

#### Sound Control
| Tool | Description |
|------|-------------|
| `runtime_set_sound_mode` | Set mode (0=off, 1=normal, 2=stereo, 3=best) |
| `runtime_get_sound_mode` | Get current sound mode |

#### Joystick/Input Control
| Tool | Description |
|------|-------------|
| `runtime_get_joyport_mode` | Get port mode (0-3) |
| `runtime_set_joyport_mode` | Set port mode (0=default, 2=mouse, 3=joy, 7=cd32) |
| `runtime_get_autofire` | Get autofire mode for port |
| `runtime_set_autofire` | Set autofire (0=off, 1=normal, 2=toggle, 3=always, 4=toggle_noaf) |

#### Floppy Control
| Tool | Description |
|------|-------------|
| `runtime_set_floppy_speed` | Set floppy speed (0=turbo, 100=1x, 200=2x, 400=4x, 800=8x) |
| `runtime_get_floppy_speed` | Get current floppy speed |
| `runtime_disk_write_protect` | Set disk write protection for drive |
| `runtime_get_disk_write_protect` | Get disk write protection status |

#### Display Control (additional)
| Tool | Description |
|------|-------------|
| `runtime_toggle_rtg` | Toggle between RTG and chipset display |
| `runtime_toggle_status_line` | Cycle status line (off/chipset/rtg/both) |
| `runtime_get_fps` | Get current frame rate and idle percentage |

#### Input Control (additional)
| Tool | Description |
|------|-------------|
| `runtime_toggle_mouse_grab` | Toggle mouse capture/grab |
| `runtime_get_mouse_speed` | Get current mouse sensitivity |

#### Hardware/Chipset Control
| Tool | Description |
|------|-------------|
| `runtime_set_chipset` | Set chipset (OCS, ECS_AGNUS, ECS_DENISE, ECS, AGA) |
| `runtime_get_chipset` | Get current chipset |
| `runtime_set_cpu_speed` | Set CPU speed (-1=max, 0=cycle-exact, >0=%) |
| `runtime_get_cpu_speed` | Get current CPU speed setting |
| `runtime_get_memory_config` | Get all memory sizes (chip, fast, bogo, z3, rtg) |

#### Memory Configuration
| Tool | Description |
|------|-------------|
| `runtime_set_chip_mem` | Set Chip RAM size (256, 512, 1024, 2048, 4096, 8192 KB) |
| `runtime_set_fast_mem` | Set Fast RAM size (0, 1024, 2048, 4096, 8192 KB) |
| `runtime_set_slow_mem` | Set Slow/Bogo RAM size (0, 256, 512, 1024, 1792 KB) |
| `runtime_set_z3_mem` | Set Zorro III RAM size (0, 1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024 MB) |
| `runtime_get_cpu_model` | Get current CPU model (68000, 68010, 68020, 68030, 68040, 68060) |
| `runtime_set_cpu_model` | Set CPU model |

#### Window/Display Control
| Tool | Description |
|------|-------------|
| `runtime_set_window_size` | Set emulator window size (width x height) |
| `runtime_get_window_size` | Get current window dimensions |
| `runtime_set_scaling` | Set scaling mode (-1=auto, 0=nearest, 1=linear, 2=integer) |
| `runtime_get_scaling` | Get current scaling mode |
| `runtime_set_line_mode` | Set line mode (single, double, scanlines) |
| `runtime_get_line_mode` | Get current line mode |
| `runtime_set_resolution` | Set display resolution (lores, hires, superhires) |
| `runtime_get_resolution` | Get current resolution mode |
| `runtime_set_autocrop` | Enable/disable automatic display cropping |
| `runtime_get_autocrop` | Get current autocrop status |

#### WHDLoad Control
| Tool | Description |
|------|-------------|
| `runtime_insert_whdload` | Load a WHDLoad game (LHA archive or directory) |
| `runtime_eject_whdload` | Eject the currently loaded WHDLoad game |
| `runtime_get_whdload` | Get info about currently loaded WHDLoad game |

#### Debugging and Diagnostics
| Tool | Description |
|------|-------------|
| `runtime_debug_activate` | Activate the built-in debugger |
| `runtime_debug_deactivate` | Deactivate debugger and resume emulation |
| `runtime_debug_status` | Get debugger status (active/inactive) |
| `runtime_debug_step` | Single-step CPU instructions |
| `runtime_debug_continue` | Continue execution until next breakpoint |
| `runtime_get_cpu_regs` | Get all CPU registers (D0-D7, A0-A7, PC, SR, USP, ISP) |
| `runtime_get_custom_regs` | Get custom chip registers (DMACON, INTENA, INTREQ, etc.) |
| `runtime_disassemble` | Disassemble instructions at a memory address |
| `runtime_set_breakpoint` | Set a breakpoint at a memory address |
| `runtime_clear_breakpoint` | Clear a breakpoint or all breakpoints |
| `runtime_list_breakpoints` | List all active breakpoints |
| `runtime_get_copper_state` | Get Copper coprocessor state |
| `runtime_get_blitter_state` | Get Blitter state (busy, channels, dimensions) |
| `runtime_get_drive_state` | Get floppy drive state (track, side, motor) |
| `runtime_get_audio_state` | Get audio channel states |
| `runtime_get_dma_state` | Get DMA channel states |

#### Status
| Tool | Description |
|------|-------------|
| `runtime_get_led_status` | Get all LED states (power, floppy, HD, CD) |
| `runtime_list_harddrives` | List mounted hard drives/directories |

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

### Process Lifecycle Management
| Tool | Description |
|------|-------------|
| `check_process_alive` | Check if Amiberry process is running (PID, exit code, signal) |
| `get_process_info` | Detailed process info with crash detection |
| `kill_amiberry` | Force kill a running/hung Amiberry process |
| `wait_for_exit` | Wait for process to exit with configurable timeout |
| `restart_amiberry` | Kill and re-launch with same command |

### Memory Access
| Tool | Description |
|------|-------------|
| `runtime_read_memory` | Read emulated Amiga memory (1/2/4 bytes) |
| `runtime_write_memory` | Write emulated Amiga memory (1/2/4 bytes) |

### Runtime Configuration
| Tool | Description |
|------|-------------|
| `runtime_load_config` | Load a .uae config file into running emulation |
| `runtime_debug_step_over` | Step over subroutine calls (JSR/BSR) |

### Screenshot Analysis
| Tool | Description |
|------|-------------|
| `runtime_screenshot_view` | Take screenshot and return image data for AI analysis |

### Log Tailing & Crash Detection
| Tool | Description |
|------|-------------|
| `tail_log` | Get new log lines since last read (incremental) |
| `wait_for_log_pattern` | Wait for a regex pattern in log output |
| `get_crash_info` | Detect crashes via process state and log scanning |

### Workflow Automation
| Tool | Description |
|------|-------------|
| `health_check` | Combined check: process + IPC + emulation status + FPS |
| `launch_and_wait_for_ipc` | Launch Amiberry and wait until IPC socket is ready |

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
- "Quick save to slot 0"
- "Quick load from slot 1"
- "Insert disk 2 into drive DF0"
- "Eject the floppy from drive 0"
- "List what's in all the floppy drives"
- "List mounted hard drives"
- "What's the current emulation status?"
- "Set the floppy speed to 800"
- "Set the volume to 50%"
- "Mute the audio"
- "Toggle fullscreen mode"
- "Enable warp mode"
- "Switch to NTSC mode"
- "Set display to fullscreen mode"
- "Set sound mode to stereo"
- "Get joystick port 0 mode"
- "Set port 0 to joystick mode"
- "Enable autofire on port 0"
- "Get LED status"
- "Advance one frame"
- "What version of Amiberry is running?"
- "Ping Amiberry to check the connection"
- "Set floppy speed to maximum"
- "What's the current FPS?"
- "Toggle RTG display"
- "Switch to AGA chipset"
- "What's the CPU speed?"
- "Set CPU to maximum speed"
- "Show me the memory configuration"
- "Toggle mouse grab"
- "Protect disk in drive 0"
- "Set Chip RAM to 2MB"
- "Set Fast RAM to 8MB"
- "What CPU model is being used?"
- "Switch to 68030 CPU"
- "Set window size to 800x600"
- "Set scaling mode to linear"
- "Set line mode to scanlines"
- "Switch to hires resolution"
- "Enable autocrop"
- "Is autocrop enabled?"
- "Load the Turrican WHDLoad game"
- "What WHDLoad game is loaded?"
- "Eject the WHDLoad game"

### Debugging and Diagnostics
- "Activate the debugger"
- "What's the debugger status?"
- "Single-step 10 instructions"
- "Continue execution"
- "Deactivate the debugger"
- "Show me the CPU registers"
- "Disassemble at address 0xFC0000"
- "Get the custom chip registers"
- "Set a breakpoint at 0x400"
- "List all breakpoints"
- "Clear the breakpoint at 0x400"
- "Clear all breakpoints"
- "Get the Copper state"
- "Get the Blitter state"
- "Get floppy drive 0 state"
- "Get audio channel states"
- "Get DMA state"

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

# Display mode (0=window, 1=fullscreen, 2=fullwindow)
curl http://localhost:8080/runtime/display-mode
curl -X POST http://localhost:8080/runtime/display-mode \
  -H "Content-Type: application/json" \
  -d '{"mode": 1}'

# Video mode (PAL/NTSC)
curl http://localhost:8080/runtime/ntsc
curl -X POST http://localhost:8080/runtime/ntsc \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}'

# Sound mode (0=off, 1=normal, 2=stereo, 3=best)
curl http://localhost:8080/runtime/sound-mode
curl -X POST http://localhost:8080/runtime/sound-mode \
  -H "Content-Type: application/json" \
  -d '{"mode": 2}'

# Quick save/load (slots 0-9)
curl -X POST http://localhost:8080/runtime/quicksave \
  -H "Content-Type: application/json" \
  -d '{"slot": 0}'
curl -X POST http://localhost:8080/runtime/quickload \
  -H "Content-Type: application/json" \
  -d '{"slot": 0}'

# Joystick port control (port 0-3, mode: 0=default, 2=mouse, 3=joy, 7=cd32)
curl http://localhost:8080/runtime/joyport/0
curl -X POST http://localhost:8080/runtime/joyport \
  -H "Content-Type: application/json" \
  -d '{"port": 0, "mode": 3}'

# Autofire control (0=off, 1=normal, 2=toggle, 3=always)
curl http://localhost:8080/runtime/autofire/0
curl -X POST http://localhost:8080/runtime/autofire \
  -H "Content-Type: application/json" \
  -d '{"port": 0, "mode": 1}'

# Status
curl http://localhost:8080/runtime/led-status
curl http://localhost:8080/runtime/harddrives

# Floppy speed control
curl http://localhost:8080/runtime/floppy-speed
curl -X POST http://localhost:8080/runtime/floppy-speed \
  -H "Content-Type: application/json" \
  -d '{"speed": 800}'

# Disk write protection
curl http://localhost:8080/runtime/disk-write-protect/0
curl -X POST http://localhost:8080/runtime/disk-write-protect \
  -H "Content-Type: application/json" \
  -d '{"drive": 0, "protected": true}'

# RTG and status line
curl -X POST http://localhost:8080/runtime/toggle-rtg
curl -X POST http://localhost:8080/runtime/toggle-status-line

# FPS monitoring
curl http://localhost:8080/runtime/fps

# Mouse grab
curl -X POST http://localhost:8080/runtime/toggle-mouse-grab
curl http://localhost:8080/runtime/mouse-speed

# Chipset control
curl http://localhost:8080/runtime/chipset
curl -X POST http://localhost:8080/runtime/chipset \
  -H "Content-Type: application/json" \
  -d '{"chipset": "AGA"}'

# CPU speed
curl http://localhost:8080/runtime/cpu-speed
curl -X POST http://localhost:8080/runtime/cpu-speed \
  -H "Content-Type: application/json" \
  -d '{"speed": -1}'

# Memory configuration
curl http://localhost:8080/runtime/memory-config

# Memory management (changes require reset)
curl -X POST http://localhost:8080/runtime/chip-mem \
  -H "Content-Type: application/json" \
  -d '{"size_kb": 2048}'
curl -X POST http://localhost:8080/runtime/fast-mem \
  -H "Content-Type: application/json" \
  -d '{"size_kb": 8192}'
curl -X POST http://localhost:8080/runtime/slow-mem \
  -H "Content-Type: application/json" \
  -d '{"size_kb": 512}'
curl -X POST http://localhost:8080/runtime/z3-mem \
  -H "Content-Type: application/json" \
  -d '{"size_mb": 64}'

# CPU model
curl http://localhost:8080/runtime/cpu-model
curl -X POST http://localhost:8080/runtime/cpu-model \
  -H "Content-Type: application/json" \
  -d '{"model": 68030}'

# Window size
curl http://localhost:8080/runtime/window-size
curl -X POST http://localhost:8080/runtime/window-size \
  -H "Content-Type: application/json" \
  -d '{"width": 800, "height": 600}'

# Scaling mode (-1=auto, 0=nearest, 1=linear, 2=integer)
curl http://localhost:8080/runtime/scaling
curl -X POST http://localhost:8080/runtime/scaling \
  -H "Content-Type: application/json" \
  -d '{"mode": 1}'

# Line mode (single, double, scanlines)
curl http://localhost:8080/runtime/line-mode
curl -X POST http://localhost:8080/runtime/line-mode \
  -H "Content-Type: application/json" \
  -d '{"mode": "scanlines"}'

# Resolution (lores, hires, superhires)
curl http://localhost:8080/runtime/resolution
curl -X POST http://localhost:8080/runtime/resolution \
  -H "Content-Type: application/json" \
  -d '{"mode": "hires"}'

# Autocrop
curl http://localhost:8080/runtime/autocrop
curl -X POST http://localhost:8080/runtime/autocrop \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}'

# WHDLoad
curl http://localhost:8080/runtime/whdload
curl -X POST http://localhost:8080/runtime/whdload \
  -H "Content-Type: application/json" \
  -d '{"path": "/path/to/game.lha"}'
curl -X DELETE http://localhost:8080/runtime/whdload

# Debugging and Diagnostics
curl -X POST http://localhost:8080/runtime/debug/activate
curl http://localhost:8080/runtime/debug/status
curl -X POST http://localhost:8080/runtime/debug/step \
  -H "Content-Type: application/json" \
  -d '{"count": 10}'
curl -X POST http://localhost:8080/runtime/debug/continue
curl -X POST http://localhost:8080/runtime/debug/deactivate
curl http://localhost:8080/runtime/cpu/regs
curl http://localhost:8080/runtime/custom/regs
curl -X POST http://localhost:8080/runtime/disassemble \
  -H "Content-Type: application/json" \
  -d '{"address": "0xFC0000", "count": 10}'
curl http://localhost:8080/runtime/breakpoints
curl -X POST http://localhost:8080/runtime/breakpoints \
  -H "Content-Type: application/json" \
  -d '{"address": "0x400"}'
curl -X DELETE http://localhost:8080/runtime/breakpoints \
  -H "Content-Type: application/json" \
  -d '{"address": "ALL"}'
curl http://localhost:8080/runtime/copper/state
curl http://localhost:8080/runtime/blitter/state
curl "http://localhost:8080/runtime/drive/state?drive=0"
curl http://localhost:8080/runtime/audio/state
curl http://localhost:8080/runtime/dma/state

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
| `/runtime/quicksave` | POST | Quick save to slot (0-9) |
| `/runtime/quickload` | POST | Quick load from slot (0-9) |

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
| `/runtime/display-mode` | GET | Get display mode |
| `/runtime/display-mode` | POST | Set mode (0=window, 1=fullscreen, 2=fullwindow) |
| `/runtime/ntsc` | GET | Get video mode (PAL/NTSC) |
| `/runtime/ntsc` | POST | Set video mode (0=PAL, 1=NTSC) |

**Sound Control**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/runtime/sound-mode` | GET | Get sound mode |
| `/runtime/sound-mode` | POST | Set mode (0=off, 1=normal, 2=stereo, 3=best) |

**Joystick/Input Control**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/runtime/joyport/{port}` | GET | Get port mode |
| `/runtime/joyport` | POST | Set port mode |
| `/runtime/autofire/{port}` | GET | Get autofire mode |
| `/runtime/autofire` | POST | Set autofire mode |

**Floppy Control**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/runtime/floppy-speed` | GET | Get current floppy speed |
| `/runtime/floppy-speed` | POST | Set floppy speed (0=turbo, 100=1x, 200=2x, 400=4x, 800=8x) |
| `/runtime/disk-write-protect/{drive}` | GET | Get disk write protection status |
| `/runtime/disk-write-protect` | POST | Set disk write protection for drive |

**Display Control (additional)**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/runtime/toggle-rtg` | POST | Toggle between RTG and chipset display |
| `/runtime/toggle-status-line` | POST | Cycle status line (off/chipset/rtg/both) |
| `/runtime/fps` | GET | Get current frame rate and idle percentage |

**Input Control (additional)**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/runtime/toggle-mouse-grab` | POST | Toggle mouse capture/grab |
| `/runtime/mouse-speed` | GET | Get current mouse sensitivity |

**Hardware/Chipset Control**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/runtime/chipset` | GET | Get current chipset |
| `/runtime/chipset` | POST | Set chipset (OCS, ECS_AGNUS, ECS_DENISE, ECS, AGA) |
| `/runtime/cpu-speed` | GET | Get current CPU speed setting |
| `/runtime/cpu-speed` | POST | Set CPU speed (-1=max, 0=cycle-exact, >0=%) |
| `/runtime/memory-config` | GET | Get all memory sizes (chip, fast, bogo, z3, rtg) |

**Memory Configuration**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/runtime/chip-mem` | POST | Set Chip RAM size (256, 512, 1024, 2048, 4096, 8192 KB) |
| `/runtime/fast-mem` | POST | Set Fast RAM size (0, 1024, 2048, 4096, 8192 KB) |
| `/runtime/slow-mem` | POST | Set Slow/Bogo RAM size (0, 256, 512, 1024, 1792 KB) |
| `/runtime/z3-mem` | POST | Set Zorro III RAM size (0-1024 MB) |
| `/runtime/cpu-model` | GET | Get current CPU model |
| `/runtime/cpu-model` | POST | Set CPU model (68000, 68010, 68020, 68030, 68040, 68060) |

**Window/Display Control**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/runtime/window-size` | GET | Get current window dimensions |
| `/runtime/window-size` | POST | Set window size (width x height) |
| `/runtime/scaling` | GET | Get current scaling mode |
| `/runtime/scaling` | POST | Set scaling mode (-1=auto, 0=nearest, 1=linear, 2=integer) |
| `/runtime/line-mode` | GET | Get current line mode |
| `/runtime/line-mode` | POST | Set line mode (single, double, scanlines) |
| `/runtime/resolution` | GET | Get current resolution mode |
| `/runtime/resolution` | POST | Set resolution (lores, hires, superhires) |
| `/runtime/autocrop` | GET | Get current autocrop status |
| `/runtime/autocrop` | POST | Enable/disable automatic display cropping |

**WHDLoad Control**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/runtime/whdload` | GET | Get currently loaded WHDLoad game info |
| `/runtime/whdload` | POST | Load a WHDLoad game (LHA or directory) |
| `/runtime/whdload` | DELETE | Eject the currently loaded WHDLoad game |

**Debugging and Diagnostics**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/runtime/debug/activate` | POST | Activate the built-in debugger |
| `/runtime/debug/deactivate` | POST | Deactivate debugger and resume |
| `/runtime/debug/status` | GET | Get debugger status |
| `/runtime/debug/step` | POST | Single-step CPU instructions |
| `/runtime/debug/continue` | POST | Continue execution |
| `/runtime/cpu/regs` | GET | Get all CPU registers |
| `/runtime/custom/regs` | GET | Get custom chip registers |
| `/runtime/disassemble` | POST | Disassemble at address |
| `/runtime/breakpoints` | GET | List all breakpoints |
| `/runtime/breakpoints` | POST | Set a breakpoint |
| `/runtime/breakpoints` | DELETE | Clear breakpoint(s) |
| `/runtime/copper/state` | GET | Get Copper state |
| `/runtime/blitter/state` | GET | Get Blitter state |
| `/runtime/drive/state` | GET | Get floppy drive state |
| `/runtime/audio/state` | GET | Get audio channel states |
| `/runtime/dma/state` | GET | Get DMA channel states |

**Status**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/runtime/led-status` | GET | Get all LED states |
| `/runtime/harddrives` | GET | List mounted hard drives |

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
