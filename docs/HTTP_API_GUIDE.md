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

- `GET /status` - Check if Amiberry is running
- `POST /stop` - Stop Amiberry
- `GET /configs` - List all configurations
- `POST /launch` - Launch with config, model, or .lha file
- `POST /quick-launch/{name}` - Quick launch (A500, A1200, CD32, or config name)
- `POST /launch-lha` - Launch .lha archive file directly
- `GET /disk-images` - List disk images
- `GET /savestates` - List savestates
- `GET /platform` - Get platform information

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
  
  amiberry_launch_workbench:
    url: http://localhost:8080/quick-launch/Workbench
    method: POST
  
  amiberry_stop:
    url: http://localhost:8080/stop
    method: POST

sensor:
  - platform: rest
    name: Amiberry Status
    resource: http://localhost:8080/status
    value_template: '{{ value_json.data.running }}'
    scan_interval: 10
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
  - type: button
    name: Launch A500
    tap_action:
      action: call-service
      service: rest_command.amiberry_launch_a500
  - type: button
    name: Stop Amiberry
    tap_action:
      action: call-service
      service: rest_command.amiberry_stop
```

---

## Command Line (curl)

### Launch Amiga 500
```bash
curl -X POST http://localhost:8080/quick-launch/A500
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

### Stop Amiberry
```bash
curl -X POST http://localhost:8080/stop
```

### Check status
```bash
curl http://localhost:8080/status
```

### List configurations
```bash
curl http://localhost:8080/configs
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

# Launch Amiga 500
response = requests.post('http://localhost:8080/quick-launch/A500')
print(response.json())

# Launch with options
payload = {
    "model": "A1200",
    "lha_file": "/path/to/game.lha",
    "autostart": True
}
response = requests.post('http://localhost:8080/launch', json=payload)
print(response.json())

# Stop Amiberry
response = requests.post('http://localhost:8080/stop')
print(response.json())

# Check status
response = requests.get('http://localhost:8080/status')
print(response.json())
```

---

## Bash Scripts

### Simple launcher script

```bash
#!/bin/bash
# launch_amiga.sh

case "$1" in
  "a500")
    curl -X POST http://localhost:8080/quick-launch/A500
    ;;
  "a1200")
    curl -X POST http://localhost:8080/quick-launch/A1200
    ;;
  "workbench")
    curl -X POST http://localhost:8080/quick-launch/Workbench
    ;;
  "stop")
    curl -X POST http://localhost:8080/stop
    ;;
  *)
    echo "Usage: $0 {a500|a1200|workbench|stop}"
    exit 1
    ;;
esac
```

Usage:
```bash
./launch_amiga.sh a500
./launch_amiga.sh workbench
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
