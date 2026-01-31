# Amiberry HTTP API - Quick Reference

## Installation

```bash
./install_http_api.sh
```

## Start the API Server

```bash
./start_http_api.sh
```

Server runs on: `http://localhost:8080`  
API Docs: `http://localhost:8080/docs`

## Test the API

```bash
./test_http_api.sh
```

## Quick Examples

### Command Line (curl)

```bash
# Launch Amiga 500
curl -X POST http://localhost:8080/quick-launch/A500

# Launch specific config
curl -X POST http://localhost:8080/quick-launch/Workbench

# Stop Amiberry
curl -X POST http://localhost:8080/stop

# Check status
curl http://localhost:8080/status

# Launch .lha file
curl -X POST "http://localhost:8080/launch-lha?lha_path=/path/to/game.lha"
```

### Python

```python
import requests

# Launch A500
requests.post('http://localhost:8080/quick-launch/A500')

# Stop
requests.post('http://localhost:8080/stop')
```

### Siri Shortcuts (macOS/iOS)

1. Create shortcut
2. Add "Get Contents of URL"
   - URL: `http://localhost:8080/quick-launch/A500`
   - Method: POST
3. Add Siri phrase: "Launch Amiga 500"

### Google Assistant (Android/Linux)

Use Tasker + AutoVoice or IFTTT Webhooks
- URL: `http://YOUR_IP:8080/quick-launch/A500`
- Method: POST

### Home Assistant

```yaml
rest_command:
  amiberry_launch:
    url: http://localhost:8080/quick-launch/A500
    method: POST
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/status` | GET | Check if Amiberry is running |
| `/stop` | POST | Stop Amiberry |
| `/configs` | GET | List configurations |
| `/disk-images` | GET | List disk images |
| `/savestates` | GET | List savestates |
| `/launch` | POST | Launch with options (JSON body) |
| `/quick-launch/{name}` | POST | Quick launch model or config |
| `/launch-lha` | POST | Launch .lha archive |
| `/platform` | GET | Platform info |

## Files

- `amiberry_http_server.py` - FastAPI HTTP server
- `install_http_api.sh` - Installation script
- `start_http_api.sh` - Launch script
- `test_http_api.sh` - API testing script
- `HTTP_API_GUIDE.md` - Complete integration guide

## Auto-start (Optional)

**macOS:**
```bash
launchctl load ~/Library/LaunchAgents/com.amiberry.httpapi.plist
```

**Linux:**
```bash
systemctl --user enable amiberry-http-api.service
systemctl --user start amiberry-http-api.service
```

## Remote Access

**From another device on same network:**
1. Get your machine's IP: `ip addr` (Linux) or `ipconfig getifaddr en0` (macOS)
2. Use: `http://YOUR_IP:8080/...`

**Secure remote access:**
- Use SSH tunnel: `ssh -L 8080:localhost:8080 user@machine`
- Or VPN (Tailscale, WireGuard, etc.)

## Platform Support

- ✅ macOS (tested)
- ✅ Linux (tested)
- ✅ Works with: Siri, Google Assistant, Home Assistant, curl, Python, Node-RED, etc.

For complete documentation, see **HTTP_API_GUIDE.md**
