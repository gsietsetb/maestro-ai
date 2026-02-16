#!/bin/bash
# setup_tunnel.sh – Install and configure Cloudflare Tunnel for webhook exposure.
#
# This replaces Railway/Fly.io entirely. Your PC runs the server,
# Cloudflare Tunnel exposes it to the internet for Telegram/WhatsApp webhooks.
#
# Cost: $0/month (Cloudflare Tunnels are free)
#
# Usage:
#   bash scripts/setup_tunnel.sh              # install + configure
#   bash scripts/setup_tunnel.sh start        # start the tunnel
#   bash scripts/setup_tunnel.sh service      # install as launchd service

set -euo pipefail

TUNNEL_NAME="cursor-orchestrator"
LOCAL_PORT="${PORT:-8000}"

# ── Install cloudflared ──────────────────────────────────────────────────────

if ! command -v cloudflared &> /dev/null; then
    echo "Installing cloudflared..."
    if [[ "$(uname)" == "Darwin" ]]; then
        brew install cloudflared
    else
        # Linux
        curl -L --output /tmp/cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
        sudo dpkg -i /tmp/cloudflared.deb
    fi
    echo "cloudflared installed."
else
    echo "cloudflared already installed."
fi

# ── Actions ──────────────────────────────────────────────────────────────────

case "${1:-setup}" in

    setup)
        echo ""
        echo "=== Cloudflare Tunnel Setup ==="
        echo ""
        echo "Step 1: Login to Cloudflare (opens browser)"
        cloudflared tunnel login

        echo ""
        echo "Step 2: Create tunnel '${TUNNEL_NAME}'"
        cloudflared tunnel create "${TUNNEL_NAME}" 2>/dev/null || echo "Tunnel may already exist."

        echo ""
        echo "Step 3: Get your tunnel URL"
        TUNNEL_ID=$(cloudflared tunnel list | grep "${TUNNEL_NAME}" | awk '{print $1}')
        echo ""
        echo "Your tunnel ID: ${TUNNEL_ID}"
        echo "Your public URL: https://${TUNNEL_ID}.cfargotunnel.com"
        echo ""
        echo "Add this to your .env:"
        echo "  WEBHOOK_URL=https://${TUNNEL_ID}.cfargotunnel.com"
        echo ""
        echo "Or configure a custom domain in the Cloudflare dashboard."
        echo ""
        echo "To start the tunnel:"
        echo "  bash scripts/setup_tunnel.sh start"
        ;;

    start)
        echo "Starting Cloudflare Tunnel -> localhost:${LOCAL_PORT}"
        cloudflared tunnel --url "http://localhost:${LOCAL_PORT}" --name "${TUNNEL_NAME}"
        ;;

    quick)
        # Quick tunnel (temporary URL, no login needed)
        echo "Starting quick tunnel -> localhost:${LOCAL_PORT}"
        echo "(Temporary URL – good for testing)"
        cloudflared tunnel --url "http://localhost:${LOCAL_PORT}"
        ;;

    service)
        echo "Installing Cloudflare Tunnel as a service..."

        # Create config file
        TUNNEL_ID=$(cloudflared tunnel list | grep "${TUNNEL_NAME}" | awk '{print $1}')
        CONFIG_DIR="$HOME/.cloudflared"
        mkdir -p "${CONFIG_DIR}"

        cat > "${CONFIG_DIR}/config.yml" <<EOF
tunnel: ${TUNNEL_ID}
credentials-file: ${CONFIG_DIR}/${TUNNEL_ID}.json

ingress:
  - service: http://localhost:${LOCAL_PORT}
EOF

        echo "Config written to ${CONFIG_DIR}/config.yml"

        if [[ "$(uname)" == "Darwin" ]]; then
            cloudflared service install
            echo "Service installed. Start with:"
            echo "  launchctl start com.cloudflare.cloudflared"
        else
            sudo cloudflared service install
            sudo systemctl enable cloudflared
            sudo systemctl start cloudflared
            echo "systemd service installed and started."
        fi
        ;;

    *)
        echo "Usage: $0 {setup|start|quick|service}"
        ;;
esac
