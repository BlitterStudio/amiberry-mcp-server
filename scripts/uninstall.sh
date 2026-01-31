#!/bin/bash

echo "=================================="
echo "Amiberry MCP Server Uninstaller"
echo "=================================="
echo ""

# Detect OS
if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macOS"
    CONFIG_FILE="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    OS="Linux"
    CONFIG_FILE="$HOME/.config/Claude/claude_desktop_config.json"
else
    echo "Error: Unsupported operating system"
    exit 1
fi

echo "Detected OS: $OS"
echo ""

# Get the directory where this script is located (scripts/)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
# Get the project root directory (parent of scripts/)
PROJECT_DIR="$( cd "$SCRIPT_DIR/.." &> /dev/null && pwd )"

echo "Project directory: $PROJECT_DIR"
echo ""

echo "This will:"
echo "1. Remove the virtual environment"
echo "2. Remove the amiberry entry from Claude Desktop config"
echo "3. Remove auto-start configurations (if present)"
echo ""
read -p "Continue? (y/n): " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Uninstall cancelled"
    exit 0
fi

# Remove virtual environment
if [ -d "$PROJECT_DIR/venv" ]; then
    echo "Removing virtual environment..."
    rm -rf "$PROJECT_DIR/venv"
    echo "Virtual environment removed"
else
    echo "Virtual environment not found, skipping..."
fi

# Remove from Claude Desktop config
if [ -f "$CONFIG_FILE" ]; then
    echo "Updating Claude Desktop configuration..."

    # Backup existing config
    cp "$CONFIG_FILE" "$CONFIG_FILE.backup"
    echo "Backed up config to $CONFIG_FILE.backup"

    # Remove amiberry entry
    python3 << END
import json
import sys

config_file = "$CONFIG_FILE"

try:
    with open(config_file, 'r') as f:
        config = json.load(f)

    if 'mcpServers' in config and 'amiberry' in config['mcpServers']:
        del config['mcpServers']['amiberry']

        # Clean up empty mcpServers
        if not config['mcpServers']:
            del config['mcpServers']

        with open(config_file, 'w') as f:
            json.dump(config, f, indent=2)

        print('Removed amiberry from Claude Desktop configuration')
    else:
        print('Amiberry entry not found in config')
except Exception as e:
    print(f'Error updating config: {e}')
    sys.exit(1)
END
else
    echo "Claude Desktop config not found, skipping..."
fi

# Remove LaunchAgent (macOS)
if [[ "$OSTYPE" == "darwin"* ]]; then
    PLIST_PATH="$HOME/Library/LaunchAgents/com.amiberry.httpapi.plist"
    if [ -f "$PLIST_PATH" ]; then
        echo "Unloading and removing LaunchAgent..."
        launchctl unload "$PLIST_PATH" 2>/dev/null || true
        rm -f "$PLIST_PATH"
        echo "LaunchAgent removed"
    fi
fi

# Remove systemd service (Linux)
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    SERVICE_PATH="$HOME/.config/systemd/user/amiberry-http-api.service"
    if [ -f "$SERVICE_PATH" ]; then
        echo "Stopping and removing systemd service..."
        systemctl --user stop amiberry-http-api.service 2>/dev/null || true
        systemctl --user disable amiberry-http-api.service 2>/dev/null || true
        rm -f "$SERVICE_PATH"
        systemctl --user daemon-reload 2>/dev/null || true
        echo "Systemd service removed"
    fi
fi

echo ""
echo "=================================="
echo "Uninstall Complete!"
echo "=================================="
echo ""
echo "The source files in $PROJECT_DIR were NOT removed."
echo "To completely remove the project, run:"
echo "  rm -rf $PROJECT_DIR"
echo ""
echo "Remember to restart Claude Desktop!"
