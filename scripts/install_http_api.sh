#!/bin/bash

# Installation script for Amiberry HTTP API Server

set -e

echo "Installing Amiberry HTTP API Server..."
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get the directory where this script is located (scripts/)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# Get the project root directory (parent of scripts/)
PROJECT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"

echo "Project directory: $PROJECT_DIR"
echo ""

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: Python 3 is not installed.${NC}"
    echo "Please install Python 3:"
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "  brew install python3"
    else
        echo "  sudo apt install python3 python3-venv  # Debian/Ubuntu"
        echo "  sudo dnf install python3  # Fedora"
    fi
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d ' ' -f 2)
echo -e "${GREEN}Found Python $PYTHON_VERSION${NC}"

# Check if virtual environment exists
if [ ! -d "$PROJECT_DIR/venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$PROJECT_DIR/venv"
    echo -e "${GREEN}Virtual environment created${NC}"
else
    echo -e "${GREEN}Virtual environment already exists${NC}"
fi

# Activate virtual environment
echo "Activating virtual environment..."
source "$PROJECT_DIR/venv/bin/activate"

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip > /dev/null 2>&1

# Install the package with HTTP dependencies
echo "Installing dependencies..."
pip install -e "$PROJECT_DIR[http]" > /dev/null 2>&1
echo -e "${GREEN}Dependencies installed${NC}"

# Create Amiberry directories if they don't exist
echo "Setting up Amiberry directories..."

if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS
    AMIBERRY_HOME="$HOME/Amiberry"
    mkdir -p "$AMIBERRY_HOME/Configurations"
    mkdir -p "$AMIBERRY_HOME/Savestates"
    mkdir -p "$AMIBERRY_HOME/Screenshots"
    mkdir -p "$AMIBERRY_HOME/Floppies"
    mkdir -p "$AMIBERRY_HOME/Harddrives"
    mkdir -p "$AMIBERRY_HOME/Lha"
    echo -e "${GREEN}Created directories in $AMIBERRY_HOME${NC}"
else
    # Linux
    AMIBERRY_HOME="$HOME/Amiberry"
    mkdir -p "$AMIBERRY_HOME/conf"
    mkdir -p "$AMIBERRY_HOME/savestates"
    mkdir -p "$AMIBERRY_HOME/screenshots"
    mkdir -p "$AMIBERRY_HOME/floppies"
    mkdir -p "$AMIBERRY_HOME/harddrives"
    mkdir -p "$AMIBERRY_HOME/lha"
    echo -e "${GREEN}Created directories in $AMIBERRY_HOME${NC}"
fi

# Create a launch script for the API server
echo "Creating launch script..."
cat > "$SCRIPT_DIR/start_http_api.sh" << EOF
#!/bin/bash
SCRIPT_DIR="\$( cd "\$( dirname "\${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="\$( cd "\$SCRIPT_DIR/.." && pwd )"
cd "\$PROJECT_DIR"
source venv/bin/activate
python -m amiberry_mcp.http_server
EOF
chmod +x "$SCRIPT_DIR/start_http_api.sh"
echo -e "${GREEN}Created start_http_api.sh${NC}"

# Create a launchd plist for auto-start (macOS only)
if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "Creating LaunchAgent for auto-start..."
    PLIST_PATH="$HOME/Library/LaunchAgents/com.amiberry.httpapi.plist"
    mkdir -p "$HOME/Library/LaunchAgents"

    cat > "$PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.amiberry.httpapi</string>
    <key>ProgramArguments</key>
    <array>
        <string>$SCRIPT_DIR/start_http_api.sh</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$HOME/Library/Logs/amiberry-http-api.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/Library/Logs/amiberry-http-api-error.log</string>
    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>
</dict>
</plist>
EOF

    echo -e "${GREEN}Created LaunchAgent plist${NC}"
    echo -e "${YELLOW}To enable auto-start, run:${NC}"
    echo "     launchctl load $PLIST_PATH"
fi

# Create systemd service for Linux
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    echo "Creating systemd service..."
    SERVICE_PATH="$HOME/.config/systemd/user/amiberry-http-api.service"
    mkdir -p "$HOME/.config/systemd/user"

    cat > "$SERVICE_PATH" << EOF
[Unit]
Description=Amiberry HTTP API Server
After=network.target

[Service]
Type=simple
WorkingDirectory=$PROJECT_DIR
ExecStart=$SCRIPT_DIR/start_http_api.sh
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF

    echo -e "${GREEN}Created systemd service${NC}"
    echo -e "${YELLOW}To enable auto-start, run:${NC}"
    echo "     systemctl --user enable amiberry-http-api.service"
    echo "     systemctl --user start amiberry-http-api.service"
fi

echo ""
echo -e "${GREEN}Installation complete!${NC}"
echo ""
echo "Next steps:"
echo "  1. Start the HTTP API server:"
echo "     $SCRIPT_DIR/start_http_api.sh"
echo ""
echo "  2. Test the API in your browser:"
echo "     http://localhost:8080/docs"
echo ""
echo "  3. Set up automations (Siri Shortcuts, Google Assistant, Home Assistant, etc.):"
echo "     See docs/HTTP_API_GUIDE.md for examples"
echo ""
echo "  4. (Optional) Enable auto-start at login"
if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "     launchctl load ~/Library/LaunchAgents/com.amiberry.httpapi.plist"
else
    echo "     systemctl --user enable amiberry-http-api.service"
fi
echo ""
echo "Documentation:"
echo "   - README.md - General MCP server and HTTP API info"
echo "   - docs/HTTP_API_GUIDE.md - HTTP API integration guide"
echo ""

deactivate
