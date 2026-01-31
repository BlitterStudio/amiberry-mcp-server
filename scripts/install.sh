#!/bin/bash

set -e  # Exit on error

echo "=================================="
echo "Amiberry MCP Server Installer"
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

# Check for Python 3
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is not installed"
    echo "Please install Python 3 first:"
    if [[ "$OS" == "macOS" ]]; then
        echo "  brew install python3"
    else
        echo "  sudo apt install python3 python3-venv  # Debian/Ubuntu"
        echo "  sudo dnf install python3  # Fedora"
    fi
    exit 1
fi

PYTHON_VERSION=$(python3 --version)
echo "Found: $PYTHON_VERSION"
echo ""

# Get the directory where this script is located (scripts/)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
# Get the project root directory (parent of scripts/)
PROJECT_DIR="$( cd "$SCRIPT_DIR/.." &> /dev/null && pwd )"

echo "Project directory: $PROJECT_DIR"
echo ""

# Create virtual environment in project root
echo "Creating virtual environment..."
if [ -d "$PROJECT_DIR/venv" ]; then
    echo "Virtual environment already exists, skipping..."
else
    python3 -m venv "$PROJECT_DIR/venv"
    echo "Created virtual environment"
fi
echo ""

# Activate virtual environment and install dependencies
echo "Installing dependencies..."
source "$PROJECT_DIR/venv/bin/activate"
pip install --quiet --upgrade pip
pip install --quiet -e "$PROJECT_DIR"
echo "Dependencies installed"
echo ""

# Check if server module exists
if [ ! -f "$PROJECT_DIR/src/amiberry_mcp/server.py" ]; then
    echo "Error: src/amiberry_mcp/server.py not found in $PROJECT_DIR"
    echo "Please ensure the package is properly structured"
    exit 1
fi

echo "Server module found"
echo ""

# Configure Claude Desktop
echo "Configuring Claude Desktop..."

# Create config directory if it doesn't exist
CLAUDE_CONFIG_DIR=$(dirname "$CONFIG_FILE")
mkdir -p "$CLAUDE_CONFIG_DIR"

# Check if config file exists
if [ -f "$CONFIG_FILE" ]; then
    echo "Claude Desktop config file found"

    # Check if amiberry entry already exists
    if grep -q '"amiberry"' "$CONFIG_FILE"; then
        echo "Amiberry MCP server is already configured in Claude Desktop"
        echo "  Config file: $CONFIG_FILE"
        echo ""
        read -p "Do you want to update the configuration? (y/n): " -n 1 -r
        echo ""
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            echo "Skipping configuration update"
            CONFIG_UPDATED=false
        else
            CONFIG_UPDATED=true
        fi
    else
        CONFIG_UPDATED=true
    fi

    if [ "$CONFIG_UPDATED" = true ]; then
        # Backup existing config
        cp "$CONFIG_FILE" "$CONFIG_FILE.backup"
        echo "Backed up existing config to $CONFIG_FILE.backup"

        # Add amiberry entry to existing config
        python3 << END
import json
import sys

config_file = "$CONFIG_FILE"
project_dir = "$PROJECT_DIR"

try:
    with open(config_file, 'r') as f:
        config = json.load(f)

    if 'mcpServers' not in config:
        config['mcpServers'] = {}

    config['mcpServers']['amiberry'] = {
        'command': f'{project_dir}/venv/bin/python',
        'args': ['-m', 'amiberry_mcp.server']
    }

    with open(config_file, 'w') as f:
        json.dump(config, f, indent=2)

    print('Updated Claude Desktop configuration')
except Exception as e:
    print(f'Error updating config: {e}')
    print('You may need to manually edit the config file')
    sys.exit(1)
END
    fi
else
    echo "Creating new Claude Desktop config file..."
    cat > "$CONFIG_FILE" << END
{
  "mcpServers": {
    "amiberry": {
      "command": "$PROJECT_DIR/venv/bin/python",
      "args": ["-m", "amiberry_mcp.server"]
    }
  }
}
END
    echo "Created Claude Desktop configuration"
fi

echo ""
echo "=================================="
echo "Installation Complete!"
echo "=================================="
echo ""
echo "Next steps:"
echo "1. Restart Claude Desktop completely (Quit and reopen)"
echo "2. Look for the hammer icon in Claude's input area"
echo "3. Try asking: 'What Amiberry configurations do I have?'"
echo ""
echo "Configuration file: $CONFIG_FILE"
echo "Project location: $PROJECT_DIR"
echo ""
echo "To uninstall, run: $SCRIPT_DIR/uninstall.sh"
echo ""
echo "For more information, see README.md"
