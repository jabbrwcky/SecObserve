#!/bin/sh
# Install (or reinstall) the host SSH agent bridge as a macOS LaunchAgent so it
# starts at login. Generates the plist from the template, writes it to
# ~/Library/LaunchAgents and (re)loads it via launchctl.
#
# Usage:   sh bin/install-agent-bridge-launchagent.sh
# Remove:  sh bin/install-agent-bridge-launchagent.sh --uninstall
set -eu

LABEL="com.secobserve.devcontainer-agent-bridge"
PLIST_DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"
DOMAIN="gui/$(id -u)"

# Resolve repo paths from this script's location.
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
BRIDGE_SCRIPT="$REPO_ROOT/bin/devcontainer-host-agent-bridge.sh"
TEMPLATE="$REPO_ROOT/docker/devcontainer/${LABEL}.plist"
LOG_DIR="$HOME/Library/Logs"

if [ "${1:-}" = "--uninstall" ]; then
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST_DEST"
    echo "Uninstalled $LABEL"
    exit 0
fi

[ -f "$TEMPLATE" ] || { echo "error: template not found: $TEMPLATE" >&2; exit 1; }
[ -f "$BRIDGE_SCRIPT" ] || { echo "error: bridge script not found: $BRIDGE_SCRIPT" >&2; exit 1; }

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
sed -e "s|__SCRIPT_PATH__|$BRIDGE_SCRIPT|g" \
    -e "s|__LOG_DIR__|$LOG_DIR|g" \
    "$TEMPLATE" > "$PLIST_DEST"

# Reload cleanly (ignore errors if not already loaded).
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST_DEST"
launchctl enable "$DOMAIN/$LABEL"

echo "Installed and loaded $LABEL"
echo "  plist: $PLIST_DEST"
echo "  log:   $LOG_DIR/devcontainer-agent-bridge.log"
