#!/bin/bash

# Quick test script for the Amiberry HTTP API server

echo "Testing Amiberry HTTP API Server..."
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

API_URL="http://localhost:8080"

# Check if server is running
echo "1. Checking if server is running..."
if curl -s "$API_URL/" > /dev/null 2>&1; then
    echo -e "${GREEN}[OK]${NC} Server is running"
else
    echo -e "${RED}[FAIL]${NC} Server is not running"
    echo "   Start it with: scripts/start_http_api.sh"
    exit 1
fi

echo ""
echo "2. Testing /status endpoint..."
STATUS=$(curl -s "$API_URL/status")
echo "   Response: $STATUS"

echo ""
echo "3. Testing /configs endpoint..."
CONFIGS=$(curl -s "$API_URL/configs" | python3 -m json.tool 2>/dev/null)
if [ $? -eq 0 ]; then
    echo -e "${GREEN}[OK]${NC} Configs endpoint working"
    CONFIG_COUNT=$(echo "$CONFIGS" | grep -c '"name"')
    echo "   Found $CONFIG_COUNT configuration(s)"
else
    echo -e "${RED}[FAIL]${NC} Configs endpoint failed"
fi

echo ""
echo "4. Testing /disk-images endpoint..."
IMAGES=$(curl -s "$API_URL/disk-images?type=all" | python3 -m json.tool 2>/dev/null)
if [ $? -eq 0 ]; then
    echo -e "${GREEN}[OK]${NC} Disk images endpoint working"
    IMAGE_COUNT=$(echo "$IMAGES" | grep -c '"name"')
    echo "   Found $IMAGE_COUNT disk image(s)"
else
    echo -e "${RED}[FAIL]${NC} Disk images endpoint failed"
fi

echo ""
echo "5. Testing /platform endpoint..."
PLATFORM=$(curl -s "$API_URL/platform")
echo "   Response: $PLATFORM"

echo ""
echo "6. Testing /savestates endpoint..."
SAVES=$(curl -s "$API_URL/savestates" | python3 -m json.tool 2>/dev/null)
if [ $? -eq 0 ]; then
    echo -e "${GREEN}[OK]${NC} Savestates endpoint working"
    SAVE_COUNT=$(echo "$SAVES" | grep -c '"name"')
    echo "   Found $SAVE_COUNT savestate(s)"
else
    echo -e "${RED}[FAIL]${NC} Savestates endpoint failed"
fi

echo ""
echo -e "${GREEN}API testing complete!${NC}"
echo ""
echo "View full API documentation at: $API_URL/docs"
echo ""
echo "Next steps:"
echo "  1. Open http://localhost:8080/docs in your browser"
echo "  2. Try the interactive API documentation"
echo "  3. Set up automations (see docs/HTTP_API_GUIDE.md)"
