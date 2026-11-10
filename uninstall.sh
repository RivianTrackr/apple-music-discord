#!/bin/bash
LABEL="local.apple-music-discord"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
rm -rf "$HOME/Library/Application Support/apple-music-discord"
rm -f "$HOME/Library/Logs/apple-music-discord.log"
echo "Uninstalled."
