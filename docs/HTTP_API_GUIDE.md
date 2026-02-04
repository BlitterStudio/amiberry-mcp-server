# Amiberry HTTP API Integration Guide

This guide shows you how to control Amiberry using the HTTP API from various automation platforms.

## Overview

The Amiberry HTTP API is a REST API server that works on both macOS and Linux. It can be integrated with:

- **Siri Shortcuts** (macOS/iOS)
- **Google Assistant** (Android/Linux)
- **Home Assistant**
- **curl/wget** (command line)
- **Any HTTP client or automation tool**

## Prerequisites

1. The HTTP API server must be running (see installation below)
2. Your machine must be accessible (localhost for same device, or network IP for remote)
3. Automation tool of your choice

## API Server Endpoints

The API server provides these endpoints:

### Core Endpoints
- `GET /status` - Check if Amiberry is running
- `POST /stop` - Stop Amiberry
- `GET /configs` - List all configurations
- `POST /launch` - Launch with config, model, or .lha file
- `POST /quick-launch/{name}` - Quick launch (A500, A1200, CD32, or config name)
- `POST /launch-lha` - Launch .lha archive file directly
- `GET /disk-images` - List disk images
- `GET /savestates` - List savestates
- `GET /platform` - Get platform information

### Configuration Endpoints
- `GET /configs/{name}/parsed` - Get parsed config as JSON
- `POST /configs/create/{name}` - Create new config from template
- `PATCH /configs/{name}` - Modify existing config

### Launch Endpoints
- `POST /launch-with-logging` - Launch with log capture
- `POST /launch-whdload` - Launch WHDLoad game by search
- `POST /launch-cd` - Launch CD image (ISO/CUE/CHD)
- `POST /disk-swapper` - Configure multi-disk games

### Media Endpoints
- `GET /cd-images` - List CD images
- `GET /logs` - List captured log files
- `GET /logs/{name}` - Get log content

### Analysis Endpoints
- `GET /savestates/{name}/inspect` - Get savestate metadata
- `GET /roms` - List identified ROMs
- `POST /roms/identify` - Identify ROM by path
- `GET /version` - Get Amiberry version

Full API documentation available at: `http://localhost:8080/docs`

---

## Siri Shortcuts (macOS/iOS)

### 1. Launch Amiga 500

**Voice command:** "Hey Siri, launch Amiga 500"

**Shortcut steps:**
1. Open Shortcuts app
2. Create new shortcut named "Launch Amiga 500"
3. Add action: "Get Contents of URL"
   - URL: `http://localhost:8080/quick-launch/A500`
   - Method: POST
4. Add action: "Show Result" (optional, for confirmation)
5. In shortcut settings, add Siri phrase: "Launch Amiga 500"

### 2. Launch Specific Configuration

**Voice command:** "Hey Siri, launch Workbench"

**Shortcut steps:**
1. Create new shortcut named "Launch Workbench"
2. Add action: "Get Contents of URL"
   - URL: `http://localhost:8080/quick-launch/Workbench`
   - Method: POST
3. Add action: "Show Notification"
   - Title: "Amiberry"
   - Body: "Launching Workbench configuration"
4. Add Siri phrase: "Launch Workbench"

### 3. Stop Amiberry

**Voice command:** "Hey Siri, stop Amiberry"

**Shortcut steps:**
1. Create new shortcut named "Stop Amiberry"
2. Add action: "Get Contents of URL"
   - URL: `http://localhost:8080/stop`
   - Method: POST
3. Add action: "Show Notification"
   - Title: "Amiberry"
   - Body: "Stopping emulator"
4. Add Siri phrase: "Stop Amiberry"

### 4. Launch .lha Archive

**Voice command:** "Hey Siri, play Kick Off"

**Shortcut steps:**
1. Create new shortcut named "Play Kick Off"
2. Add action: "Get Contents of URL"
   - URL: `http://localhost:8080/launch-lha?lha_path=/Users/yourname/Amiberry/Lha/KickOff.lha`
   - Method: POST
3. Add action: "Show Notification"
4. Add Siri phrase: "Play Kick Off"

### 5. Launch WHDLoad Game by Search

**Voice command:** "Hey Siri, play Turrican"

**Shortcut steps:**
1. Create new shortcut named "Play Turrican"
2. Add action: "Get Contents of URL"
   - URL: `http://localhost:8080/launch-whdload?search=Turrican`
   - Method: POST
3. Add action: "Show Notification"
4. Add Siri phrase: "Play Turrican"

### Remote Access from iOS

**Option 1: Local Network**
1. Get your Mac/Linux machine's IP: `ip addr show` or `ipconfig getifaddr en0`
2. In iOS Shortcuts, use: `http://YOUR_IP:8080/...`

**Option 2: SSH Tunnel**
1. Enable Remote Login/SSH
2. Create tunnel: `ssh -L 8080:localhost:8080 user@your-machine`
3. Use `http://localhost:8080/...` in shortcuts

---

## Google Assistant (Android/Linux)

### Using Tasker + AutoVoice

**1. Install Apps:**
- Tasker (automation app)
- AutoVoice (Google Assistant integration)

**2. Create Task:**
1. Create new Task in Tasker: "Launch Amiga 500"
2. Add action: Net → HTTP Request
   - Method: POST
   - URL: `http://localhost:8080/quick-launch/A500`
3. Save task

**3. Create AutoVoice Command:**
1. Create new Profile in Tasker
2. Event → Plugin → AutoVoice Recognized
3. Configuration: "launch amiga five hundred"
4. Link to "Launch Amiga 500" task

**4. Test:**
Say "OK Google, launch amiga five hundred"

### Using IFTTT

**1. Create Webhook:**
1. Go to IFTTT.com
2. Create new Applet
3. If: Google Assistant → Say a phrase ("Launch Amiga 500")
4. Then: Webhooks → Make a web request
   - URL: `http://YOUR_IP:8080/quick-launch/A500`
   - Method: POST
   - Content Type: application/json

**2. Test:**
Say "OK Google, launch Amiga 500"

---

## Home Assistant

### Configuration

Add to your `configuration.yaml`:

```yaml
rest_command:
  amiberry_launch_a500:
    url: http://localhost:8080/quick-launch/A500
    method: POST

  amiberry_launch_a1200:
    url: http://localhost:8080/quick-launch/A1200
    method: POST

  amiberry_launch_cd32:
    url: http://localhost:8080/quick-launch/CD32
    method: POST

  amiberry_launch_workbench:
    url: http://localhost:8080/quick-launch/Workbench
    method: POST

  amiberry_stop:
    url: http://localhost:8080/stop
    method: POST

  amiberry_launch_whdload:
    url: "http://localhost:8080/launch-whdload?search={{ search }}"
    method: POST

sensor:
  - platform: rest
    name: Amiberry Status
    resource: http://localhost:8080/status
    value_template: '{{ value_json.data.running }}'
    scan_interval: 10

  - platform: rest
    name: Amiberry Version
    resource: http://localhost:8080/version
    value_template: '{{ value_json.version }}'
    scan_interval: 3600
```

### Create Automation

```yaml
automation:
  - alias: "Launch Amiberry on button press"
    trigger:
      platform: state
      entity_id: input_button.amiga_button
    action:
      service: rest_command.amiberry_launch_a500
```

### Lovelace Card

```yaml
type: vertical-stack
cards:
  - type: entity
    entity: sensor.amiberry_status
  - type: entity
    entity: sensor.amiberry_version
  - type: button
    name: Launch A500
    tap_action:
      action: call-service
      service: rest_command.amiberry_launch_a500
  - type: button
    name: Launch A1200
    tap_action:
      action: call-service
      service: rest_command.amiberry_launch_a1200
  - type: button
    name: Launch CD32
    tap_action:
      action: call-service
      service: rest_command.amiberry_launch_cd32
  - type: button
    name: Stop Amiberry
    tap_action:
      action: call-service
      service: rest_command.amiberry_stop
```

---

## Command Line (curl)

### Basic Operations

```bash
# Launch Amiga 500
curl -X POST http://localhost:8080/quick-launch/A500

# Launch Amiga 1200
curl -X POST http://localhost:8080/quick-launch/A1200

# Launch CD32
curl -X POST http://localhost:8080/quick-launch/CD32

# Stop Amiberry
curl -X POST http://localhost:8080/stop

# Check status
curl http://localhost:8080/status

# List configurations
curl http://localhost:8080/configs
```

### Launch with .lha file

```bash
curl -X POST "http://localhost:8080/launch-lha?lha_path=/home/user/Amiberry/lha/game.lha"
```

### Launch with JSON payload

```bash
curl -X POST http://localhost:8080/launch \
  -H "Content-Type: application/json" \
  -d '{
    "model": "A500",
    "disk_image": "/path/to/disk.adf",
    "autostart": true
  }'
```

### Launch with Logging

```bash
# Launch with model and logging
curl -X POST http://localhost:8080/launch-with-logging \
  -H "Content-Type: application/json" \
  -d '{"model": "A500"}'

# Launch config with logging
curl -X POST http://localhost:8080/launch-with-logging \
  -H "Content-Type: application/json" \
  -d '{"config_name": "MyConfig"}'
```

### WHDLoad Games

```bash
# Search and launch WHDLoad game
curl -X POST "http://localhost:8080/launch-whdload?search=Turrican"

# Launch specific .lha file
curl -X POST "http://localhost:8080/launch-whdload?search=Turrican2.lha"
```

### CD Images

```bash
# List CD images
curl http://localhost:8080/cd-images

# Launch CD image
curl -X POST http://localhost:8080/launch-cd \
  -H "Content-Type: application/json" \
  -d '{"cd_path": "/path/to/game.iso"}'

# Launch with specific model
curl -X POST http://localhost:8080/launch-cd \
  -H "Content-Type: application/json" \
  -d '{"cd_path": "/path/to/game.cue", "model": "CDTV"}'
```

### Multi-Disk Games

```bash
# Configure disk swapper
curl -X POST http://localhost:8080/disk-swapper \
  -H "Content-Type: application/json" \
  -d '{
    "disk_paths": [
      "/path/to/disk1.adf",
      "/path/to/disk2.adf",
      "/path/to/disk3.adf"
    ]
  }'
```

### Configuration Management

```bash
# Get parsed config as JSON
curl http://localhost:8080/configs/MyConfig.uae/parsed

# Create new config from template
curl -X POST "http://localhost:8080/configs/create/my-new-config?model=A1200"

# Create config with options
curl -X POST "http://localhost:8080/configs/create/gaming-config?model=A1200" \
  -H "Content-Type: application/json" \
  -d '{"fastmem_size": "8", "cpu_speed": "max"}'

# Modify existing config
curl -X PATCH http://localhost:8080/configs/MyConfig.uae \
  -H "Content-Type: application/json" \
  -d '{"floppy_speed": "800", "gfx_width": "720"}'
```

### Logs

```bash
# List captured logs
curl http://localhost:8080/logs

# Get specific log content
curl http://localhost:8080/logs/amiberry_20240115_143022.log
```

### Savestate Analysis

```bash
# Inspect savestate metadata
curl http://localhost:8080/savestates/mysave.uss/inspect
```

### ROM Management

```bash
# List all identified ROMs
curl http://localhost:8080/roms

# Identify specific ROM
curl -X POST http://localhost:8080/roms/identify \
  -H "Content-Type: application/json" \
  -d '{"path": "/path/to/kick.rom"}'
```

### Version Info

```bash
# Get Amiberry version
curl http://localhost:8080/version
```

---

## Node-RED

### HTTP Request Node

1. Add "http request" node
2. Configure:
   - Method: POST
   - URL: `http://localhost:8080/quick-launch/A500`
3. Connect to trigger (button, time, etc.)

### Example Flow

```json
[{
    "id": "launch_a500",
    "type": "http request",
    "method": "POST",
    "url": "http://localhost:8080/quick-launch/A500",
    "name": "Launch A500"
}]
```

---

## Python Script

```python
import requests

BASE_URL = 'http://localhost:8080'

# Launch Amiga 500
response = requests.post(f'{BASE_URL}/quick-launch/A500')
print(response.json())

# Launch with options
payload = {
    "model": "A1200",
    "lha_file": "/path/to/game.lha",
    "autostart": True
}
response = requests.post(f'{BASE_URL}/launch', json=payload)
print(response.json())

# Launch with logging
payload = {"model": "A500"}
response = requests.post(f'{BASE_URL}/launch-with-logging', json=payload)
print(response.json())

# Launch WHDLoad game
response = requests.post(f'{BASE_URL}/launch-whdload?search=Turrican')
print(response.json())

# Launch CD image
payload = {"cd_path": "/path/to/game.iso"}
response = requests.post(f'{BASE_URL}/launch-cd', json=payload)
print(response.json())

# Create a new config
response = requests.post(f'{BASE_URL}/configs/create/my-config?model=A1200')
print(response.json())

# Get parsed config
response = requests.get(f'{BASE_URL}/configs/MyConfig.uae/parsed')
config = response.json()
print(f"Chip RAM: {config.get('chipmem_size', 'unknown')}")

# Modify config
changes = {"floppy_speed": "800"}
response = requests.patch(f'{BASE_URL}/configs/MyConfig.uae', json=changes)
print(response.json())

# Inspect savestate
response = requests.get(f'{BASE_URL}/savestates/mysave.uss/inspect')
metadata = response.json()
print(f"CPU: {metadata.get('cpu', {}).get('model', 'unknown')}")

# List ROMs
response = requests.get(f'{BASE_URL}/roms')
for rom in response.json().get('roms', []):
    if rom.get('identified'):
        print(f"{rom['filename']}: Kickstart {rom['version']} ({rom['model']})")

# Stop Amiberry
response = requests.post(f'{BASE_URL}/stop')
print(response.json())

# Check status
response = requests.get(f'{BASE_URL}/status')
print(response.json())
```

---

## Bash Scripts

### Simple launcher script

```bash
#!/bin/bash
# launch_amiga.sh

BASE_URL="http://localhost:8080"

case "$1" in
  "a500")
    curl -s -X POST $BASE_URL/quick-launch/A500
    ;;
  "a1200")
    curl -s -X POST $BASE_URL/quick-launch/A1200
    ;;
  "cd32")
    curl -s -X POST $BASE_URL/quick-launch/CD32
    ;;
  "workbench")
    curl -s -X POST $BASE_URL/quick-launch/Workbench
    ;;
  "stop")
    curl -s -X POST $BASE_URL/stop
    ;;
  "status")
    curl -s $BASE_URL/status | jq .
    ;;
  "configs")
    curl -s $BASE_URL/configs | jq .
    ;;
  "roms")
    curl -s $BASE_URL/roms | jq .
    ;;
  "version")
    curl -s $BASE_URL/version | jq .
    ;;
  "whdload")
    if [ -z "$2" ]; then
      echo "Usage: $0 whdload <search-term>"
      exit 1
    fi
    curl -s -X POST "$BASE_URL/launch-whdload?search=$2"
    ;;
  "cd")
    if [ -z "$2" ]; then
      echo "Usage: $0 cd <path-to-iso>"
      exit 1
    fi
    curl -s -X POST $BASE_URL/launch-cd \
      -H "Content-Type: application/json" \
      -d "{\"cd_path\": \"$2\"}"
    ;;
  *)
    echo "Usage: $0 {a500|a1200|cd32|workbench|stop|status|configs|roms|version|whdload <term>|cd <path>}"
    exit 1
    ;;
esac
```

Usage:
```bash
./launch_amiga.sh a500
./launch_amiga.sh whdload Turrican
./launch_amiga.sh cd /path/to/game.iso
./launch_amiga.sh status
./launch_amiga.sh stop
```

---

## Troubleshooting

### Connection Refused

1. Check if server is running:
   ```bash
   curl http://localhost:8080/status
   ```

2. Check firewall settings (allow port 8080)

3. For remote access, use machine's IP address instead of localhost

### API Returns 404

1. Verify endpoint URL is correct
2. Check config/model name spelling
3. Use `/configs` to list available options

### Authentication Issues

The API currently has no authentication. For security:
- Only bind to localhost if not needed remotely
- Use SSH tunneling for remote access
- Or add reverse proxy with authentication (nginx, Caddy)

---

## Security Considerations

### Local Use Only

By default, the server binds to `0.0.0.0` (all interfaces). To restrict to localhost only:

Edit `amiberry_http_server.py`:
```python
uvicorn.run(app, host="127.0.0.1", port=8080)  # localhost only
```

### Remote Access with Authentication

Use a reverse proxy like Caddy or nginx with basic auth:

**Caddy example:**
```
localhost:8081 {
    basicauth {
        user $2a$14$Zkx19XLiW6VYouLHR5NmfOFU0z2GTNmpkT/5qqR7hx7wNOjqBqHCu
    }
    reverse_proxy localhost:8080
}
```

### VPN/Tailscale

For secure remote access, use a VPN like Tailscale:
1. Install Tailscale on both devices
2. Use Tailscale IP instead of public IP
3. Traffic is encrypted automatically

---

## Advanced Integration

### systemd Timer (Linux)

Launch Amiberry at specific times:

```ini
# ~/.config/systemd/user/amiberry-morning.timer
[Unit]
Description=Launch Amiberry every morning

[Timer]
OnCalendar=*-*-* 08:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

```ini
# ~/.config/systemd/user/amiberry-morning.service
[Unit]
Description=Launch Amiberry Workbench

[Service]
Type=oneshot
ExecStart=/usr/bin/curl -X POST http://localhost:8080/quick-launch/Workbench
```

Enable:
```bash
systemctl --user enable amiberry-morning.timer
systemctl --user start amiberry-morning.timer
```

### cron Job

```bash
# Launch Amiberry every day at 8 AM
0 8 * * * curl -X POST http://localhost:8080/quick-launch/Workbench
```

---

## Example Use Cases

### Morning Routine (Linux + systemd)
- Automatically launch Workbench at 8 AM
- Stop at 6 PM to save power

### Gaming Station (Raspberry Pi)
- Physical button to launch different configs
- Home Assistant integration
- Voice control via Google Assistant

### Demo Mode (macOS + Siri)
- "Hey Siri, demo mode" launches CD32 with specific game
- Automated presentation setup

### Development Workflow
- Launch specific config for testing
- Capture logs for debugging
- Inspect savestates
- Automated via shell script
- Integrated with VS Code tasks

---

## API Reference

For complete API documentation with interactive testing, visit:
**http://localhost:8080/docs**

The API follows OpenAPI 3.0 specification and includes:
- All available endpoints
- Request/response schemas
- Try-it-now functionality
- Authentication details (when enabled)
