#!/bin/bash
# Installs Apple Music Discord Presence as a background LaunchAgent.
set -e

LABEL="local.apple-music-discord"
INSTALL_DIR="$HOME/Library/Application Support/apple-music-discord"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/apple-music-discord.log"
SRC="$(cd "$(dirname "$0")" && pwd)/music_discord.py"

if [[ "$(uname)" != "Darwin" ]]; then
  echo "This only works on macOS." && exit 1
fi

if [[ ! -f "$SRC" ]]; then
  echo "Can't find music_discord.py next to install.sh." && exit 1
fi

CLIENT_ID="${1:-$DISCORD_CLIENT_ID}"
if [[ -z "$CLIENT_ID" ]]; then
  echo "Paste your Discord Application ID"
  echo "(create one at https://discord.com/developers/applications):"
  read -r CLIENT_ID
fi
if ! [[ "$CLIENT_ID" =~ ^[0-9]+$ ]]; then
  echo "That doesn't look like an Application ID (it should be all numbers)." && exit 1
fi

echo
echo "What should your status line show after \"Listening to\"?"
echo "  1) Artist name (default)"
echo "  2) Song title"
echo "  3) Apple Music"
read -r CHOICE
case "$CHOICE" in
  2) DISPLAY=2 ;;
  3) DISPLAY=0 ;;
  *) DISPLAY=1 ;;
esac

mkdir -p "$INSTALL_DIR" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cp "$SRC" "$INSTALL_DIR/music_discord.py"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>$INSTALL_DIR/music_discord.py</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>DISCORD_CLIENT_ID</key>
        <string>$CLIENT_ID</string>
        <key>DISCORD_STATUS_DISPLAY</key>
        <string>$DISPLAY</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$LOG</string>
    <key>StandardErrorPath</key>
    <string>$LOG</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo
echo "Installed and running. It'll start automatically every time you log in."
echo "Play a song in Apple Music. macOS will ask to let python3 control Music, click Allow."
echo "Logs: tail -f \"$LOG\""
