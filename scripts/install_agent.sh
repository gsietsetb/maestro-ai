#!/bin/bash
# install_agent.sh – Install the agent daemon on any PC (Mac/Linux).
#
# Each PC in the mesh runs this daemon. It auto-discovers projects
# and capabilities, then connects to the orchestrator.
#
# Usage:
#   bash scripts/install_agent.sh                     # install & start (macOS)
#   bash scripts/install_agent.sh --linux              # install (systemd)
#   bash scripts/install_agent.sh uninstall            # remove

set -euo pipefail

LABEL="com.cursor-orchestrator.agent"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PYTHON="${PROJECT_DIR}/.venv/bin/python"
LOG_DIR="${PROJECT_DIR}/logs"

# ── Uninstall ────────────────────────────────────────────────────────────────

if [[ "${1:-}" == "uninstall" ]]; then
    echo "Uninstalling agent..."
    if [[ "$(uname)" == "Darwin" ]]; then
        launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
        rm -f "$HOME/Library/LaunchAgents/${LABEL}.plist"
    else
        sudo systemctl stop cursor-orchestrator-agent 2>/dev/null || true
        sudo systemctl disable cursor-orchestrator-agent 2>/dev/null || true
        sudo rm -f /etc/systemd/system/cursor-orchestrator-agent.service
        sudo systemctl daemon-reload
    fi
    echo "Done."
    exit 0
fi

# ── Checks ───────────────────────────────────────────────────────────────────

if [[ ! -f "${VENV_PYTHON}" ]]; then
    echo "ERROR: venv not found. Run:"
    echo "  cd ${PROJECT_DIR} && python3.13 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

if [[ ! -f "${PROJECT_DIR}/.env" ]]; then
    echo "ERROR: .env not found. Copy .env.example and configure WS_URL + WS_SECRET."
    exit 1
fi

mkdir -p "${LOG_DIR}"

# ── macOS (launchd) ──────────────────────────────────────────────────────────

if [[ "${1:-}" != "--linux" && "$(uname)" == "Darwin" ]]; then
    PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

    cat > "${PLIST}" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${VENV_PYTHON}</string>
        <string>-m</string>
        <string>src.local_agent.daemon</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${PROJECT_DIR}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    </dict>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/agent-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/agent-stderr.log</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
PLIST

    launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "${PLIST}"

    echo "macOS agent installed and started."
    echo "  Logs: tail -f ${LOG_DIR}/agent-stdout.log"
    echo "  Stop: launchctl bootout gui/$(id -u)/${LABEL}"
    exit 0
fi

# ── Linux (systemd) ──────────────────────────────────────────────────────────

SERVICE="/etc/systemd/system/cursor-orchestrator-agent.service"

sudo tee "${SERVICE}" > /dev/null <<SERVICE
[Unit]
Description=Cursor Orchestrator Agent
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=${PROJECT_DIR}
ExecStart=${VENV_PYTHON} -m src.local_agent.daemon
Restart=always
RestartSec=10
Environment=PATH=/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=multi-user.target
SERVICE

sudo systemctl daemon-reload
sudo systemctl enable cursor-orchestrator-agent
sudo systemctl start cursor-orchestrator-agent

echo "Linux agent installed and started."
echo "  Logs: journalctl -u cursor-orchestrator-agent -f"
echo "  Stop: sudo systemctl stop cursor-orchestrator-agent"
