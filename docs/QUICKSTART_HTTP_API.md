# Amiberry HTTP API - Quick Reference

## Installation

```bash
./scripts/install_http_api.sh
```

## Start the API Server

```bash
./scripts/start_http_api.sh
```

Server runs on: `http://localhost:8080`
API Docs: `http://localhost:8080/docs`

## Test the API

```bash
./scripts/test_http_api.sh
```

## Quick Examples

### Command Line (curl)

```bash
# Launch Amiga models
curl -X POST http://localhost:8080/quick-launch/A500
curl -X POST http://localhost:8080/quick-launch/A1200
curl -X POST http://localhost:8080/quick-launch/CD32

# Launch specific config
curl -X POST http://localhost:8080/quick-launch/Workbench

# Stop Amiberry
curl -X POST http://localhost:8080/stop

# Check status
curl http://localhost:8080/status

# Launch .lha file (WHDLoad)
curl -X POST "http://localhost:8080/launch-whdload?search=Turrican"

# Launch CD image
curl -X POST http://localhost:8080/launch-cd \
  -H "Content-Type: application/json" \
  -d '{"cd_path": "/path/to/game.iso"}'

# Launch with logging
curl -X POST http://localhost:8080/launch-with-logging \
  -H "Content-Type: application/json" \
  -d '{"model": "A500"}'

# Create new config
curl -X POST "http://localhost:8080/configs/create/my-config?model=A1200"

# List ROMs
curl http://localhost:8080/roms

# Inspect savestate
curl http://localhost:8080/savestates/mysave.uss/inspect
```

### Python

```python
import requests

BASE = 'http://localhost:8080'

# Launch A500
requests.post(f'{BASE}/quick-launch/A500')

# Launch WHDLoad game
requests.post(f'{BASE}/launch-whdload?search=Turrican')

# Launch CD image
requests.post(f'{BASE}/launch-cd', json={'cd_path': '/path/to/game.iso'})

# Create config
requests.post(f'{BASE}/configs/create/my-config?model=A1200')

# Inspect savestate
r = requests.get(f'{BASE}/savestates/mysave.uss/inspect')
print(r.json())

# Stop
requests.post(f'{BASE}/stop')
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
  amiberry_launch_a500:
    url: http://localhost:8080/quick-launch/A500
    method: POST
  amiberry_launch_whdload:
    url: "http://localhost:8080/launch-whdload?search={{ search }}"
    method: POST
  amiberry_stop:
    url: http://localhost:8080/stop
    method: POST
```

## API Endpoints

### Core Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/status` | GET | Check if Amiberry is running |
| `/stop` | POST | Stop Amiberry |
| `/configs` | GET | List configurations |
| `/disk-images` | GET | List disk images |
| `/savestates` | GET | List savestates |
| `/launch` | POST | Launch with options (JSON body) |
| `/quick-launch/{name}` | POST | Quick launch model or config |
| `/platform` | GET | Platform info |

### Configuration Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/configs/{name}/parsed` | GET | Get parsed config as JSON |
| `/configs/create/{name}` | POST | Create new config |
| `/configs/{name}` | PATCH | Modify config |

### Launch Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/launch-with-logging` | POST | Launch with log capture |
| `/launch-whdload` | POST | Launch WHDLoad game |
| `/launch-cd` | POST | Launch CD image |
| `/disk-swapper` | POST | Multi-disk setup |

### Media Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/cd-images` | GET | List CD images |
| `/logs` | GET | List captured logs |
| `/logs/{name}` | GET | Get log content |

### Analysis Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/savestates/{name}/inspect` | GET | Savestate metadata |
| `/roms` | GET | List identified ROMs |
| `/roms/identify` | POST | Identify ROM |
| `/version` | GET | Amiberry version |

## Files

- `src/amiberry_mcp/http_server.py` - FastAPI HTTP server
- `scripts/install_http_api.sh` - Installation script
- `scripts/start_http_api.sh` - Launch script
- `scripts/test_http_api.sh` - API testing script
- `docs/HTTP_API_GUIDE.md` - Complete integration guide

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

- macOS (tested)
- Linux (tested)
- Works with: Siri, Google Assistant, Home Assistant, curl, Python, Node-RED, etc.

For complete documentation, see **HTTP_API_GUIDE.md**
