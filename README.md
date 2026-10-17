# Amiberry MCP Server

An [MCP](https://modelcontextprotocol.io) (Model Context Protocol) server for
[Amiberry](https://github.com/BlitterStudio/amiberry), the Amiga emulator. It
lets an AI assistant list and edit configurations, launch games and disk
images, control a running emulation, and debug Amiga code.

The server speaks plain MCP over stdio, so any MCP client can use it: Claude
Desktop, Claude Code, Codex, Gemini (Antigravity), Cursor, Cline, Windsurf, and
others. The installer configures Claude Desktop, Claude Code, Codex, and
Gemini/Antigravity. For other clients, see [Other MCP clients](#other-mcp-clients).

A separate HTTP API exposes the same functions as REST endpoints for scripts,
home automation, and voice assistants.

## Features

**Files and launching**
- List and launch Amiberry configurations, or launch a Quickstart model
  (`A1000`, `A500`, `A500P`, `A600`, `A2000`, `A3000`, `A1200`, `A4000`,
  `CD32`, `CDTV`)
- Search disk images (ADF, HDF, DMS, LHA, ISO, CUE, CHD) and launch WHDLoad
  games from LHA archives
- Launch CD32 and CDTV images, and set up the disk swapper for multi-disk games
- Parse, edit, and create `.uae` configuration files from model templates
- Read metadata from `.uss` savestates and identify Kickstart ROMs by checksum

**Runtime control** (needs Amiberry built with IPC support)
- Pause, resume, reset, frame-advance, save and load states
- Swap floppies, CDs, and WHDLoad games in a running emulation
- Change chipset, CPU, memory, display, sound, and input settings at runtime
- Send keystrokes, text, and mouse input
- Take screenshots and click, drag, or hover at pixels picked from a screenshot
- Control one of several running Amiberry instances

**Debugging**
- Launch with logging, tail logs, and wait for a log pattern
- Detect crashes from the process exit status and log contents; kill and restart
- Read and write emulated memory, inspect CPU and custom chip registers,
  disassemble, single-step, and set breakpoints
- Use the GDB monitor command set (watchpoints, conditions, task and segment
  lists) without a GDB client

## Requirements

- Python 3.10 or newer
- Amiberry:
  - **macOS**: `Amiberry.app` in `/Applications`
  - **Linux and FreeBSD**: `amiberry` on `PATH`
  - **Windows**: installed by the Amiberry installer or `Amiberry.exe` on
    `PATH`. The server finds the installer's copy through its registry
    entry (per-user under `%LOCALAPPDATA%\Programs\amiberry`, all-users
    under `%ProgramFiles%\amiberry`) without any PATH setup.
- An MCP client

The server runs on Linux, FreeBSD, macOS, and Windows, and raises
`RuntimeError` on any other platform.

Runtime control talks to Amiberry over IPC, which needs Amiberry built with
`USE_IPC_SOCKET=ON`. That is the default on all four platforms since Windows
gained the named-pipe transport after 8.4.1. On Windows the endpoint is the
named pipe `\\.\pipe\amiberry` (further instances use `\\.\pipe\amiberry_1`
through `\\.\pipe\amiberry_9`). Elsewhere it is the Unix socket
`$XDG_RUNTIME_DIR/amiberry.sock`, or `/tmp/amiberry.sock` when
`XDG_RUNTIME_DIR` is unset.

## Installation

### Installer (macOS, Linux, FreeBSD)

```bash
git clone https://github.com/BlitterStudio/amiberry-mcp-server.git
cd amiberry-mcp-server
./scripts/install.sh
```

The installer:
1. Creates a virtual environment in `venv/`
2. Installs the package and its dependencies
3. Adds an `amiberry` entry to the Claude Desktop config, creating the file if
   needed
4. Adds the same entry for Claude Code, Codex, and Gemini (Antigravity) when it
   finds them installed

Other clients need the entry added by hand. See
[Other MCP clients](#other-mcp-clients).

### Manual installation

The installer is a shell script and does not run on Windows. Install manually
there, or on any platform where you prefer to:

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -e .                # or ".[http]" to include the HTTP API
```

Then add the server to your client as described below.

## Client configuration

Every stdio MCP client needs the same command and arguments:

| Field     | Value                                                    |
|-----------|----------------------------------------------------------|
| Command   | `/path/to/amiberry-mcp-server/venv/bin/python`           |
|           | Windows: `C:\path\to\amiberry-mcp-server\venv\Scripts\python.exe` |
| Args      | `["-m", "amiberry_mcp.server"]`                          |
| Transport | stdio                                                    |

### Claude Desktop

Edit the config file for your platform:

| Platform | Config file |
|----------|-------------|
| macOS    | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Linux    | `~/.config/Claude/claude_desktop_config.json` |
| Windows  | `%APPDATA%\Claude\claude_desktop_config.json` |

Add the server under `mcpServers`:

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

On Windows, escape the backslashes in the JSON path
(`"C:\\path\\to\\amiberry-mcp-server\\venv\\Scripts\\python.exe"`).
Restart Claude Desktop afterwards.

### Other MCP clients

Add an entry with the same command and arguments:

| Client | Where |
|--------|-------|
| Claude Code | `~/.claude.json` (set by the installer) |
| Codex | `~/.codex/config.toml`, as a `[mcp_servers.amiberry]` table (set by the installer) |
| Gemini / Antigravity | `~/.gemini/antigravity/mcp_config.json` (set by the installer) |
| Cursor | `~/.cursor/mcp.json`, or `.cursor/mcp.json` inside a project |
| Cline, Continue, Windsurf | the client's MCP settings |
| Any other MCP client | run `python -m amiberry_mcp.server` over stdio |

### Checking the setup

Restart the client. The Amiberry tools should appear in its tool list (the
`/mcp` command, a tools icon, or a tool inspector, depending on the client).
Then ask something like:

- "What Amiberry configurations do I have?"
- "Show me my disk images"
- "Launch Amiberry with the A500 model"

## Amiberry directories

The server reads and writes the same directories Amiberry uses. It also
creates a `logs` directory under the content root for logs it captures.

| Platform | Content root | Settings (`amiberry.conf`, `amiberry.ini`) |
|----------|--------------|----------------------------------------------|
| macOS | `~/Documents/Amiberry` | `~/Library/Application Support/Amiberry` |
| Linux, FreeBSD | `~/Amiberry` | `$XDG_CONFIG_HOME/amiberry` (default `~/.config/amiberry`) |
| Windows | `%USERPROFILE%\Amiberry` | `%LOCALAPPDATA%\Amiberry`, falling back to `%APPDATA%\Amiberry` |

Inside the content root:

```
Amiberry/
├── Configurations/    # .uae config files
├── Floppies/          # .adf, .adz, .dms
├── HardDrives/        # .hdf, .hdz
├── LHA/               # .lha archives (WHDLoad games)
├── CDROMs/            # ISO, CUE, CHD
├── SaveStates/        # .uss savestates
├── Screenshots/
├── ROMs/              # Kickstart ROMs
└── logs/              # logs captured by this server
```

The settings directory holds settings only; it is not a second `.uae` config
tree.

Overrides:

- `AMIBERRY_BINARY` points the server at a specific Amiberry executable, on
  any platform, ahead of the automatic discovery (`PATH`, the Windows
  installer's registry entry, and the default Windows install directories).
  Handy for a portable copy or a development build.
- `AMIBERRY_HOME_DIR` replaces the content root, unless portable mode is on.
- An `amiberry.portable` file next to the Amiberry binary switches Amiberry to
  portable mode, and the server follows that root. macOS ignores the marker.
- `AMIBERRY_CONFIG_DIR` replaces the configurations directory on Linux and
  FreeBSD. Windows does not honor it: configurations are always in
  `<content root>\Configurations`.

ROM, savestate, and disk listings also search the pre-8.4 directory names that
Amiberry still migrates (`kickstarts`, `roms`, `savestates`, `floppies`,
`harddrives`, `lha`, and their case variants), so files copied from an older
tree still show up.

## MCP tools

The server exposes 150 tools. Tools whose names start with `runtime_` act on a
running emulation over IPC; the rest work on files and processes.

### Configurations and files

| Tool | Description |
|------|-------------|
| `list_configs` | List configuration files |
| `get_config_content` | Show the contents of a config file |
| `parse_config` | Parse a `.uae` file into structured data |
| `modify_config` | Change options in a config file |
| `create_config` | Create a config from a model template |
| `list_disk_images` | Search disk images (ADF, HDF, DMS, LHA) |
| `list_cd_images` | List CD images |
| `list_savestates` | List savestate files |
| `inspect_savestate` | Read metadata from a `.uss` savestate |
| `list_roms` | List ROMs with identification |
| `identify_rom` | Identify a ROM by checksum |
| `get_amiberry_version` | Show the installed Amiberry version |
| `get_platform_info` | Show platform and path information |

### Launching

| Tool | Description |
|------|-------------|
| `launch_amiberry` | Launch with a config, model, disk, or LHA file |
| `launch_with_logging` | Launch with `--log` and capture the output to a file |
| `launch_and_wait_for_ipc` | Launch and wait until the IPC endpoint is ready |
| `launch_whdload` | Search for and launch a WHDLoad game |
| `launch_cd` | Launch a CD image (ISO, CUE, CHD) |
| `set_disk_swapper` | Set up the disk swapper for a multi-disk game |

### Processes, logs, and crashes

| Tool | Description |
|------|-------------|
| `check_process_alive` | Check whether Amiberry is running (PID, exit code, signal) |
| `get_process_info` | Process details with crash detection |
| `kill_amiberry` | Force-kill a running or hung Amiberry |
| `wait_for_exit` | Wait for the process to exit, with a timeout |
| `restart_amiberry` | Kill and relaunch with the same command |
| `health_check` | Check process, IPC, emulation status, and FPS in one call |
| `list_logs` | List captured log files |
| `get_log_content` | Read a captured log file |
| `tail_log` | Return log lines added since the last read |
| `wait_for_log_pattern` | Wait for a regex to appear in the log |
| `get_crash_info` | Detect a crash from the process state and log contents |

### Runtime: emulation and state

| Tool | Description |
|------|-------------|
| `get_runtime_status` | Get emulation status |
| `pause_emulation` | Pause emulation |
| `resume_emulation` | Resume emulation |
| `reset_emulation` | Soft or hard reset |
| `runtime_frame_advance` | Advance N frames while paused |
| `runtime_quit` | Ask Amiberry to quit over IPC (not a process kill) |
| `runtime_save_state` | Save state while running |
| `runtime_load_state` | Load a savestate |
| `runtime_quicksave` | Quick save to slot 0-9 |
| `runtime_quickload` | Quick load from slot 0-9 |
| `runtime_screenshot` | Save a screenshot to a file |
| `runtime_get_config` | Get a config option value |
| `runtime_set_config` | Set a config option |
| `runtime_load_config` | Load a `.uae` file into the running emulation |
| `runtime_list_configs` | List config files known to the running emulator |
| `runtime_get_led_status` | Get LED states (power, floppy, HD, CD) |
| `runtime_get_fps` | Get frame rate and idle percentage |

### Runtime: media

| Tool | Description |
|------|-------------|
| `runtime_insert_floppy` | Insert a floppy image into a drive |
| `runtime_eject_floppy` | Eject a floppy |
| `runtime_list_floppies` | List floppy drives and their contents |
| `runtime_disk_swap` | Insert a disk from the swapper list into a drive |
| `runtime_query_disk_swap` | Show which swapper slot is in a drive (`-1` if none) |
| `runtime_set_floppy_speed` | Set floppy speed (0=turbo, 100=1x, 200=2x, 400=4x, 800=8x) |
| `runtime_get_floppy_speed` | Get floppy speed |
| `runtime_disk_write_protect` | Set write protection for a drive |
| `runtime_get_disk_write_protect` | Get write protection for a drive |
| `runtime_insert_cd` | Insert a CD image |
| `runtime_eject_cd` | Eject the CD |
| `runtime_list_harddrives` | List mounted hard drives and directories |
| `runtime_insert_whdload` | Load a WHDLoad game (LHA archive or directory) |
| `runtime_eject_whdload` | Eject the loaded WHDLoad game |
| `runtime_get_whdload` | Show the loaded WHDLoad game |

### Runtime: hardware

| Tool | Description |
|------|-------------|
| `runtime_set_chipset` | Set chipset (OCS, ECS_AGNUS, ECS_DENISE, ECS, AGA) |
| `runtime_get_chipset` | Get chipset |
| `runtime_set_cpu_model` | Set CPU model (68000, 68010, 68020, 68030, 68040, 68060) |
| `runtime_get_cpu_model` | Get CPU model |
| `runtime_set_cpu_speed` | Set CPU speed (-1=max, 0=cycle-exact, >0=percent) |
| `runtime_get_cpu_speed` | Get CPU speed |
| `runtime_get_memory_config` | Get memory sizes (chip, fast, bogo, Z3, RTG) |
| `runtime_set_chip_mem` | Set Chip RAM (256, 512, 1024, 2048, 4096, 8192 KB) |
| `runtime_set_fast_mem` | Set Fast RAM (0, 1024, 2048, 4096, 8192 KB) |
| `runtime_set_slow_mem` | Set Slow (Bogo) RAM (0, 256, 512, 1024, 1792 KB) |
| `runtime_set_z3_mem` | Set Zorro III RAM (0, 1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024 MB) |

### Runtime: display and sound

| Tool | Description |
|------|-------------|
| `runtime_toggle_fullscreen` | Toggle fullscreen and windowed mode |
| `runtime_set_display_mode` | Set display mode (0=window, 1=fullscreen, 2=fullwindow) |
| `runtime_get_display_mode` | Get display mode |
| `runtime_set_window_size` | Set window size (width x height) |
| `runtime_get_window_size` | Get window size |
| `runtime_set_scaling` | Set scaling (-1=auto, 0=nearest, 1=linear, 2=integer) |
| `runtime_get_scaling` | Get scaling mode |
| `runtime_set_line_mode` | Set line mode (single, double, scanlines) |
| `runtime_get_line_mode` | Get line mode |
| `runtime_set_resolution` | Set resolution (lores, hires, superhires) |
| `runtime_get_resolution` | Get resolution |
| `runtime_set_autocrop` | Turn automatic cropping on or off |
| `runtime_get_autocrop` | Get autocrop state |
| `runtime_set_ntsc` | Set video mode (0=PAL, 1=NTSC) |
| `runtime_get_ntsc` | Get video mode |
| `runtime_toggle_rtg` | Switch between RTG and chipset display |
| `runtime_toggle_status_line` | Cycle the status line (off, chipset, RTG, both) |
| `runtime_set_warp` | Turn warp mode on or off |
| `runtime_get_warp` | Get warp mode |
| `runtime_set_volume` | Set master volume (0-100) |
| `runtime_get_volume` | Get volume |
| `runtime_mute` | Mute audio |
| `runtime_unmute` | Unmute audio |
| `runtime_set_sound_mode` | Set sound mode (0=off, 1=normal, 2=stereo, 3=best) |
| `runtime_get_sound_mode` | Get sound mode |

### Runtime: input

| Tool | Description |
|------|-------------|
| `runtime_send_key` | Press, release, or tap a key by name (`space`, `return`, `f1`) or scancode |
| `runtime_send_text` | Type a string, adding Shift for capitals and symbols |
| `runtime_send_mouse` | Send mouse movement and button presses |
| `runtime_set_mouse_speed` | Set mouse sensitivity (10-200) |
| `runtime_get_mouse_speed` | Get mouse sensitivity |
| `runtime_toggle_mouse_grab` | Toggle mouse capture |
| `runtime_set_joyport_mode` | Set port mode (0=default, 2=mouse, 3=joystick, 7=CD32 pad) |
| `runtime_get_joyport_mode` | Get port mode |
| `runtime_set_autofire` | Set autofire (0=off, 1=normal, 2=toggle, 3=always, 4=toggle_noaf) |
| `runtime_get_autofire` | Get autofire mode for a port |

### Runtime: screenshot-driven GUI automation

| Tool | Description |
|------|-------------|
| `runtime_screenshot_view` | Return the screenshot image plus capture metadata for GUI actions |
| `runtime_gui_move` | Move or hover at a pixel in that screenshot |
| `runtime_gui_click` | Click or double-click a mouse button at a pixel |
| `runtime_gui_drag` | Drag with a mouse button between two pixels |

See [Screenshot-driven GUI automation](#screenshot-driven-gui-automation).

### Runtime: debugging

| Tool | Description |
|------|-------------|
| `runtime_debug_activate` | Enter the built-in debugger |
| `runtime_debug_deactivate` | Leave the debugger and resume emulation |
| `runtime_debug_status` | Get debugger status |
| `runtime_debug_step` | Single-step CPU instructions |
| `runtime_debug_step_over` | Step over subroutine calls (JSR, BSR) |
| `runtime_debug_continue` | Run until the next breakpoint |
| `runtime_get_cpu_regs` | Get CPU registers (D0-D7, A0-A7, PC, SR, USP, ISP) |
| `runtime_get_custom_regs` | Get custom chip registers (DMACON, INTENA, INTREQ, and others) |
| `runtime_disassemble` | Disassemble at an address |
| `runtime_set_breakpoint` | Set a breakpoint. `slot` (0-19) is optional; without it the first free slot is used |
| `runtime_clear_breakpoint` | Clear one breakpoint or all of them |
| `runtime_list_breakpoints` | List breakpoints |
| `runtime_read_memory` | Read 1, 2, or 4 bytes of emulated memory |
| `runtime_write_memory` | Write 1, 2, or 4 bytes of emulated memory |
| `runtime_get_copper_state` | Get Copper state |
| `runtime_get_blitter_state` | Get Blitter state (busy, channels, size) |
| `runtime_get_drive_state` | Get floppy drive state (track, side, motor) |
| `runtime_get_audio_state` | Get audio channel states |
| `runtime_get_dma_state` | Get DMA channel states |

### Runtime: debugger monitor

`MONITOR` runs the GDB monitor command set without a GDB client. The typed
tools below build the command, check the arguments, and parse the JSON reply.
`runtime_monitor` sends one raw command string. The HTTP API exposes the raw
command as `POST /runtime/debug/monitor`.

| Tool | Actions |
|------|---------|
| `runtime_monitor` | Raw monitor text |
| `runtime_execution_status` | CPU, pause, frame, and beam |
| `runtime_tasks` | Exec task lists |
| `runtime_segments` | AmigaDOS segments |
| `runtime_memory` | `map`, `check` |
| `runtime_exception` | `status`, `mask` |
| `runtime_watch` | `add`, `range`, `list`, `remove`, `events`, `last`, `clear-events` |
| `runtime_dma_watch` | `add`, `list`, `remove` |
| `runtime_condition` | `add`, `list`, `remove` |
| `runtime_history` | `on`, `off`, `read` |
| `runtime_guest_output` | `on`, `off`, `read`, `clear` |
| `runtime_process_break` | `name`, `address`, `status`, `clear` |
| `runtime_input` | `event`, `release`, `status` |

Read-only queries work while the guest runs, except guest RAM reads when
`cpu_threaded=true`.

These actions change debugger state and need the emulation paused (`PAUSE`) or
the native debugger stopped:

- watch `add`, `range`, `remove`, `clear-events`
- DMA watch `add`, `remove`
- condition `add`, `remove`
- exception `mask`
- history `on`, `off`
- guest output `on`, `off`, `clear`
- process break `name`, `address`, `clear`

A watch, condition, exception, or process-entry hit stops in the native
debugger. `runtime_debug_status` then reports `stopped=1` and a `reason=`
(`T05awatch:...`, `T05breakpoint:N;`, `T05winuae-exception:...`,
`T05winuae-entry:...`). `DEBUG_CONTINUE` resumes. The monitor refuses
`step-over`, `checkpoint`, and `input sequence`.

Watch `add` and `range` take nine fields: address, size, access, source mask,
value enabled, value, value mask, change-only, and log-only. Numbers are
hexadecimal. Access is 1 for read, 2 for write, 3 for both; a range can also
set bit 2 for instruction fetch. Change-only requires a write. An exact watch
cannot include the CPU instruction source.

### Instances and IPC

| Tool | Description |
|------|-------------|
| `get_active_instance` | Show which Amiberry instance the server controls |
| `set_active_instance` | Choose an instance (0, 1, 2, ...; `null` to auto-discover) |
| `check_ipc_connection` | Check whether the IPC endpoint is reachable |
| `runtime_ping` | Test the IPC connection (returns `PONG`) |
| `runtime_get_version` | Get Amiberry and SDL versions from the running emulator |
| `runtime_help` | List the IPC commands the running Amiberry accepts |

## Screenshot-driven GUI automation

GUI actions use coordinates taken from a screenshot. Call
`runtime_screenshot_view`, look at the returned image, and pass pixel
coordinates from that exact image to `runtime_gui_move`, `runtime_gui_click`,
or `runtime_gui_drag`. Every action also takes the returned `capture_id` and a
`request_id` that the caller generates.

The screenshot metadata declares `coordinate_space="screenshot_pixels"`, the
image size, and a half-open `actionable_bounds` rectangle. Points must be
integer pixels inside both the image and those bounds. The server translates
them for scaling, HiDPI, viewport, and monitor layout; callers should not
convert coordinates themselves.

| Tool | Required fields | Optional fields |
|------|-----------------|-----------------|
| `runtime_gui_move` | `capture_id`, `request_id`, `x`, `y` | `dwell_ms` (default `0`, range `0..5000`) |
| `runtime_gui_click` | `capture_id`, `request_id`, `x`, `y` | `button` (`left`, `right`, or `middle`; default `left`), `click_count` (`1` or `2`; default `1`) |
| `runtime_gui_drag` | `capture_id`, `request_id`, `start_x`, `start_y`, `end_x`, `end_y` | `button` (`left`, `right`, or `middle`; default `left`) |

`request_id` is 1-128 characters from `A-Z`, `a-z`, `0-9`, `.`, `_`, `:`, and
`-`. Retrying with the same ID and payload returns the original result with
`deduplicated=true`. Reusing an ID with a different payload returns
`request_id_conflict`. The server keeps only a bounded number of past results,
so an ID deduplicates retries within that window, not forever.

Each action returns a versioned result with `ok`, `code`, `message`,
`controller_id`, `request_id`, `action`, `execution_state`, `failure_phase`,
`next_action`, `retryable`, `recapture_required`, `cleanup_state`,
`cleanup_context`, `deduplicated`, and capture, runtime, and coordinate
context. Follow `next_action` and `recapture_required` when recovering from a
failure. `cleanup_unconfirmed` means the outcome is unknown: check what is on
screen before deciding whether to repeat the action.

Captures and request IDs belong to one controller process. A capture stays
valid only while its instance, runtime identity, monitor, and geometry
revision still match; take a new screenshot whenever the result asks for one.
Use either the MCP server or the HTTP server as the controller for a given
Amiberry instance, not both at once.

Older Amiberry builds still return a screenshot, but the metadata has
`actionable=false`, `capture_id=null`, and `next_action="upgrade_runtime"`.
Actionable captures need Amiberry's SDL or OpenGL renderer. The Vulkan
renderer returns plain screenshots and rejects GUI actions, because it cannot
yet tie screenshot pixels to the geometry of the presented frame. There is no
fallback to relative coordinates. GUI automation is supported on Linux,
macOS, and Windows. The Windows named-pipe path was validated live against
an installed Amiberry build: actionable capture, the guarded configuration
compare-and-set, clicks, drags, and stale-capture rejection after a window
resize and after a restart all behaved as specified, with held buttons read
back from the emulated CIA registers. Automated tests cover the action
contract and coordinate geometry; each release is also checked against a
live Amiberry separately.

## Example prompts

**Library and launching**
- "List my Amiberry configurations"
- "Find disk images containing 'Shadow of the Beast'"
- "Launch Amiberry with the A1200 model"
- "Launch the WHDLoad game 'Turrican'"
- "Launch the CD32 game from /path/to/game.iso"
- "Create a new A1200 config with 8MB Fast RAM"
- "What CPU and chipset does my savestate use?"

**Running emulation**
- "Pause the emulation" / "Take a screenshot"
- "Quick save to slot 0"
- "Insert disk 2 into drive DF0"
- "Set the floppy speed to 800" / "Enable warp mode"
- "Switch to the AGA chipset and a 68030 CPU"
- "Set Chip RAM to 2MB"
- "Type 'dir' and press Return"

**Debugging**
- "Launch Amiberry with logging enabled and show me the log"
- "Activate the debugger and show me the CPU registers"
- "Disassemble at 0xFC0000"
- "Set a breakpoint at 0x400 and continue"
- "Get the Copper and Blitter state"

## HTTP API

The HTTP server (FastAPI) exposes the same functions as REST endpoints for
scripts, home automation, and voice assistants such as Siri Shortcuts.

```bash
./scripts/install_http_api.sh   # install the HTTP dependencies
./scripts/start_http_api.sh     # start the server
```

The API listens on `http://localhost:8080`; interactive docs are at
`http://localhost:8080/docs`. [docs/HTTP_API_GUIDE.md](docs/HTTP_API_GUIDE.md)
has request formats, curl examples for every endpoint, and voice assistant
setup. [docs/QUICKSTART_HTTP_API.md](docs/QUICKSTART_HTTP_API.md) is a short
reference.

A few examples:

```bash
# Launch a model, list configs, stop Amiberry
curl -X POST http://localhost:8080/quick-launch/A500
curl http://localhost:8080/configs
curl -X POST http://localhost:8080/stop

# Launch a WHDLoad game
curl -X POST "http://localhost:8080/launch-whdload?search=Turrican"

# Control a running emulation
curl -X POST http://localhost:8080/runtime/pause
curl -X POST http://localhost:8080/runtime/insert-floppy \
  -H "Content-Type: application/json" \
  -d '{"drive": 0, "image_path": "/path/to/disk2.adf"}'
curl -X POST http://localhost:8080/runtime/type \
  -H "Content-Type: application/json" \
  -d '{"text": "dir\n", "delay_ms": 50}'

# Debugger
curl -X POST http://localhost:8080/runtime/breakpoints \
  -H "Content-Type: application/json" \
  -d '{"address": "0x400", "slot": 2}'
curl http://localhost:8080/runtime/cpu/regs
```

### Endpoints

Endpoints under `/runtime` need a running Amiberry with IPC support.

#### Library, launching, and processes

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | API name, version, platform, status |
| `/status` | GET | Check whether Amiberry is running |
| `/stop` | POST | Stop all Amiberry instances |
| `/platform` | GET | Platform info |
| `/version` | GET | Amiberry version |
| `/configs` | GET | List configurations |
| `/configs/{name}` | GET | Raw config file contents |
| `/configs/{name}/parsed` | GET | Parsed config as JSON |
| `/configs/create/{name}` | POST | Create a config from a template |
| `/configs/{name}` | PATCH | Modify a config |
| `/disk-images` | GET | List disk images |
| `/cd-images` | GET | List CD images |
| `/savestates` | GET | List savestates |
| `/savestates/{name}/inspect` | GET | Savestate metadata |
| `/roms` | GET | List identified ROMs |
| `/roms/identify` | GET | Identify a ROM by the `rom_path` query parameter (must be inside the Amiberry home directory) |
| `/launch` | POST | Launch with full options |
| `/quick-launch/{name}` | POST | Launch by model or config name |
| `/launch-with-logging` | POST | Launch with log capture |
| `/launch-lha` | POST | Launch an `.lha` archive |
| `/launch-whdload` | POST | Launch a WHDLoad game |
| `/launch-cd` | POST | Launch a CD image |
| `/launch-and-wait` | POST | Launch and wait until IPC is ready |
| `/disk-swapper` | POST | Set up the disk swapper |
| `/logs` | GET | List captured logs |
| `/logs/{name}` | GET | Log contents |
| `/logs/tail` | POST | Log lines added since the last read |
| `/logs/wait-for-pattern` | POST | Wait for a regex in the log |
| `/health` | GET | Process, IPC, and emulation status in one call |
| `/process/alive` | GET | Check whether the process is running |
| `/process/info` | GET | Process details with crash detection |
| `/process/kill` | POST | Force-kill the process |
| `/process/wait-for-exit` | POST | Wait for the process to exit |
| `/process/restart` | POST | Kill and relaunch with the same command |
| `/process/crash-info` | POST | Detect a crash from process state and logs |

#### Runtime: emulation and state

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/runtime/status` | GET | Emulation status |
| `/runtime/pause` | POST | Pause |
| `/runtime/resume` | POST | Resume |
| `/runtime/reset` | POST | Soft or hard reset |
| `/runtime/quit` | POST | Quit Amiberry |
| `/runtime/frame-advance` | POST | Advance N frames while paused |
| `/runtime/screenshot` | POST | Save a screenshot |
| `/runtime/screenshot-view` | POST | Screenshot bytes plus capture metadata |
| `/runtime/save-state` | POST | Save state |
| `/runtime/load-state` | POST | Load a savestate |
| `/runtime/quicksave` | POST | Quick save to slot 0-9 |
| `/runtime/quickload` | POST | Quick load from slot 0-9 |
| `/runtime/config/{option}` | GET | Get a config option |
| `/runtime/config` | POST | Set a config option |
| `/runtime/configs` | GET | List configs known to the emulator |
| `/runtime/load-config` | POST | Load a `.uae` file into the running emulation |
| `/runtime/led-status` | GET | LED states |
| `/runtime/fps` | GET | Frame rate and idle percentage |

#### Runtime: media

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/runtime/insert-floppy` | POST | Insert a floppy |
| `/runtime/eject-floppy` | POST | Eject a floppy |
| `/runtime/list-floppies` | GET | List floppy drives and their contents |
| `/runtime/disk-swap` | POST | Insert a swapper disk (`{"disk_num", "drive"}`) |
| `/runtime/disk-swap/{drive}` | GET | Swapper slot in a drive |
| `/runtime/floppy-speed` | GET | Floppy speed |
| `/runtime/floppy-speed` | POST | Set floppy speed (0=turbo, 100=1x, 200=2x, 400=4x, 800=8x) |
| `/runtime/disk-write-protect/{drive}` | GET | Write protection for a drive |
| `/runtime/disk-write-protect` | POST | Set write protection |
| `/runtime/insert-cd` | POST | Insert a CD image |
| `/runtime/eject-cd` | POST | Eject the CD |
| `/runtime/harddrives` | GET | List mounted hard drives |
| `/runtime/whdload` | GET | Loaded WHDLoad game |
| `/runtime/whdload` | POST | Load a WHDLoad game (LHA or directory) |
| `/runtime/whdload` | DELETE | Eject the WHDLoad game |

#### Runtime: hardware

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/runtime/chipset` | GET | Chipset |
| `/runtime/chipset` | POST | Set chipset (OCS, ECS_AGNUS, ECS_DENISE, ECS, AGA) |
| `/runtime/cpu-model` | GET | CPU model |
| `/runtime/cpu-model` | POST | Set CPU model (68000, 68010, 68020, 68030, 68040, 68060) |
| `/runtime/cpu-speed` | GET | CPU speed |
| `/runtime/cpu-speed` | POST | Set CPU speed (-1=max, 0=cycle-exact, >0=percent) |
| `/runtime/memory-config` | GET | Memory sizes (chip, fast, bogo, Z3, RTG) |
| `/runtime/chip-mem` | POST | Set Chip RAM (256, 512, 1024, 2048, 4096, 8192 KB) |
| `/runtime/fast-mem` | POST | Set Fast RAM (0, 1024, 2048, 4096, 8192 KB) |
| `/runtime/slow-mem` | POST | Set Slow (Bogo) RAM (0, 256, 512, 1024, 1792 KB) |
| `/runtime/z3-mem` | POST | Set Zorro III RAM (0-1024 MB) |

Memory changes take effect after a reset.

#### Runtime: display and sound

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/runtime/fullscreen` | POST | Toggle fullscreen |
| `/runtime/display-mode` | GET | Display mode |
| `/runtime/display-mode` | POST | Set display mode (0=window, 1=fullscreen, 2=fullwindow) |
| `/runtime/window-size` | GET | Window size |
| `/runtime/window-size` | POST | Set window size (width x height) |
| `/runtime/scaling` | GET | Scaling mode |
| `/runtime/scaling` | POST | Set scaling (-1=auto, 0=nearest, 1=linear, 2=integer) |
| `/runtime/line-mode` | GET | Line mode |
| `/runtime/line-mode` | POST | Set line mode (single, double, scanlines) |
| `/runtime/resolution` | GET | Resolution |
| `/runtime/resolution` | POST | Set resolution (lores, hires, superhires) |
| `/runtime/autocrop` | GET | Autocrop state |
| `/runtime/autocrop` | POST | Turn autocrop on or off |
| `/runtime/ntsc` | GET | Video mode (PAL or NTSC) |
| `/runtime/ntsc` | POST | Set video mode |
| `/runtime/rtg` | POST | Switch between RTG and chipset display |
| `/runtime/status-line` | POST | Cycle the status line (off, chipset, RTG, both) |
| `/runtime/warp` | GET | Warp mode |
| `/runtime/warp` | POST | Set warp mode |
| `/runtime/volume` | GET | Volume |
| `/runtime/volume` | POST | Set volume (0-100) |
| `/runtime/mute` | POST | Mute |
| `/runtime/unmute` | POST | Unmute |
| `/runtime/sound-mode` | GET | Sound mode |
| `/runtime/sound-mode` | POST | Set sound mode (0=off, 1=normal, 2=stereo, 3=best) |

#### Runtime: input and GUI automation

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/runtime/key` | POST | Send a key (keycode and state) |
| `/runtime/type` | POST | Type a string |
| `/runtime/mouse` | POST | Send mouse input |
| `/runtime/mouse-speed` | GET | Mouse sensitivity |
| `/runtime/mouse-speed` | POST | Set mouse sensitivity |
| `/runtime/mouse-grab` | POST | Toggle mouse capture |
| `/runtime/joyport/{port}` | GET | Port mode |
| `/runtime/joyport` | POST | Set port mode |
| `/runtime/autofire/{port}` | GET | Autofire mode |
| `/runtime/autofire` | POST | Set autofire mode |
| `/runtime/gui/move` | POST | Move or hover at a screenshot pixel |
| `/runtime/gui/click` | POST | Click or double-click at a screenshot pixel |
| `/runtime/gui/drag` | POST | Drag between screenshot pixels |

#### Runtime: debugging

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/runtime/debug/activate` | POST | Enter the debugger |
| `/runtime/debug/deactivate` | POST | Leave the debugger and resume |
| `/runtime/debug/status` | GET | Debugger status |
| `/runtime/debug/step` | POST | Single-step instructions |
| `/runtime/debug/step-over` | POST | Step over subroutine calls (JSR, BSR) |
| `/runtime/debug/continue` | POST | Continue execution |
| `/runtime/debug/monitor` | POST | Run one GDB monitor command (`{"text": "tasks"}`) |
| `/runtime/cpu/regs` | GET | CPU registers |
| `/runtime/custom/regs` | GET | Custom chip registers |
| `/runtime/disassemble` | POST | Disassemble at an address |
| `/runtime/breakpoints` | GET | List breakpoints |
| `/runtime/breakpoints` | POST | Set a breakpoint (`address`, optional `slot` 0-19) |
| `/runtime/breakpoints` | DELETE | Clear breakpoints |
| `/runtime/memory/read` | POST | Read 1, 2, or 4 bytes of memory |
| `/runtime/memory/write` | POST | Write 1, 2, or 4 bytes of memory |
| `/runtime/copper/state` | GET | Copper state |
| `/runtime/blitter/state` | GET | Blitter state |
| `/runtime/drive/state` | GET | Floppy drive state |
| `/runtime/audio/state` | GET | Audio channel states |
| `/runtime/dma/state` | GET | DMA channel states |

#### Runtime: instances and IPC

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/runtime/active-instance` | GET | Instance being controlled |
| `/runtime/active-instance` | POST | Choose an instance (`null` to auto-discover) |
| `/runtime/ipc-check` | GET | Check IPC availability |
| `/runtime/ping` | GET | Test the IPC connection |
| `/runtime/version` | GET | Amiberry version from the running emulator |
| `/runtime/help` | GET | List IPC commands |

### Starting at login

`install_http_api.sh` creates a LaunchAgent on macOS and a systemd user unit
on Linux. Enable them with:

```bash
# macOS
launchctl load ~/Library/LaunchAgents/com.amiberry.httpapi.plist

# Linux
systemctl --user enable --now amiberry-http-api.service
```

## Troubleshooting

### The tools do not appear in the client
- Quit and reopen the client. Most clients load MCP servers only at startup.
- Check that the `command` path points at this project's `venv/bin/python`
  (`venv\Scripts\python.exe` on Windows) and that the file exists.
- Run `python -m amiberry_mcp.server` from the virtual environment. It should
  wait silently for input on stdin; if it prints an error, fix that first.
- Read the client's MCP log:
  - Claude Desktop (macOS): `~/Library/Logs/Claude/mcp*.log`
  - Claude Code: `claude mcp list`, then check the server's stderr
  - Cursor: View → Output → "MCP" channel

### "Command not found" when launching
- **Linux and FreeBSD**: put `amiberry` on `PATH`
- **macOS**: install `Amiberry.app` in `/Applications`
- **Windows**: install Amiberry with its installer, or put `Amiberry.exe` on
  `PATH`. The server checks `PATH` first, then the installer's registry entry
  (HKCU, then HKLM), then `%LOCALAPPDATA%\Programs\amiberry\Amiberry.exe` and
  `%ProgramFiles%\amiberry\Amiberry.exe`. Set `AMIBERRY_BINARY` to point at
  any other copy, such as a portable one.

### Permission errors running the scripts
Make them executable: `chmod +x scripts/*.sh`

### Where captured logs go
The `logs` directory under the content root: `~/Documents/Amiberry/logs` on
macOS, `~/Amiberry/logs` on Linux and FreeBSD, `%USERPROFILE%\Amiberry\logs`
on Windows.

### Runtime control does not work
- Check that Amiberry is running and was built with `USE_IPC_SOCKET=ON`
  (a CMake option).
- On Linux, FreeBSD, and macOS, check that the socket exists:
  `ls "${XDG_RUNTIME_DIR:-/tmp}/amiberry.sock"`. Test it directly with
  `echo "GET_STATUS" | nc -U "${XDG_RUNTIME_DIR:-/tmp}/amiberry.sock"`.
- With several instances running, instance 0 uses `amiberry.sock` and the
  others use `amiberry_1.sock`, `amiberry_2.sock`, and so on up to instance 9.
  Pick one with `set_active_instance`.
- On Windows, list the pipes with
  `Get-ChildItem \\.\pipe\ | Where-Object Name -like 'amiberry*'`. Do not use
  `Test-Path`: it opens a client connection to the pipe.
- The Windows pipe transport needs asyncio's Proactor event loop, which is the
  default on Windows. If a dependency switches to a selector loop, the server
  reports an error saying so.

## Development

```bash
git clone https://github.com/BlitterStudio/amiberry-mcp-server.git
cd amiberry-mcp-server
python3 -m venv venv
source venv/bin/activate
pip install -e ".[all]"         # HTTP, D-Bus, and dev dependencies

pytest tests/ -v                # run the tests
ruff check src/ tests/          # lint
ruff format src/ tests/         # format
```

Project layout:

```
amiberry-mcp-server/
├── src/amiberry_mcp/
│   ├── config.py          # platform detection and paths
│   ├── common.py          # shared helpers (launch, scan, validation)
│   ├── shared_state.py    # process state, IPC client caching, launch helpers
│   ├── server.py          # MCP server (150 tools)
│   ├── http_server.py     # HTTP API (FastAPI)
│   ├── ipc_client.py      # IPC client (Unix socket, named pipe, D-Bus)
│   ├── monitor.py         # MONITOR command builders and reply parsing
│   ├── gui_automation.py  # screenshot capture registry and GUI actions
│   ├── uae_config.py      # .uae parser and generator
│   ├── savestate.py       # .uss metadata parser
│   └── rom_manager.py     # ROM identification
├── scripts/               # install, uninstall, and HTTP API scripts
├── tests/                 # pytest suite, one file per module
├── docs/                  # HTTP API guide and quick reference
└── AGENTS.md              # architecture and code conventions
```

## Uninstall

```bash
./scripts/uninstall.sh
```

## Contributing

Issues and pull requests are welcome. [AGENTS.md](AGENTS.md) describes the
architecture and code conventions: ruff settings, type annotations, error
handling, and test layout.

## License

GPL-3.0. See [LICENSE](LICENSE).

## Links

- [Model Context Protocol](https://modelcontextprotocol.io)
- [Amiberry](https://github.com/BlitterStudio/amiberry)
- [FastAPI](https://fastapi.tiangolo.com)
- MCP clients: [Claude Desktop](https://claude.ai/download),
  [Claude Code](https://claude.com/claude-code),
  [Codex](https://github.com/openai/codex), [Cursor](https://cursor.com),
  [Cline](https://github.com/cline/cline), [Continue](https://continue.dev),
  [Windsurf](https://codeium.com/windsurf)
