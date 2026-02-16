#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
# install_tunnel.sh – Expone el dashboard y API globalmente via Cloudflare Tunnel
#
# Dos modos:
#   1. Quick (inmediato, URL temporal pero funcional):
#      bash scripts/install_tunnel.sh quick
#
#   2. Permanente (URL fija con tu dominio):
#      bash scripts/install_tunnel.sh setup
#      bash scripts/install_tunnel.sh permanent
#
# El tunnel se integra con el autostart para arrancar al encender el Mac.
#
# Usage:
#   bash scripts/install_tunnel.sh quick              # tunnel temporal (ahora)
#   bash scripts/install_tunnel.sh setup              # login CF + crear tunnel
#   bash scripts/install_tunnel.sh permanent          # arrancar tunnel permanente
#   bash scripts/install_tunnel.sh domain bot.g97.io  # asociar subdominio
#   bash scripts/install_tunnel.sh status             # ver estado
#   bash scripts/install_tunnel.sh stop               # parar tunnel
#   bash scripts/install_tunnel.sh uninstall          # eliminar todo
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${PROJECT_DIR}/logs"
PIDFILE="${PROJECT_DIR}/.pid-tunnel"
URL_FILE="${PROJECT_DIR}/.tunnel-url"
LOCAL_PORT="${PORT:-8000}"
TUNNEL_NAME="cursor-orchestrator"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

mkdir -p "${LOG_DIR}"

# Load .env for Telegram notification
if [[ -f "${PROJECT_DIR}/.env" ]]; then
    set -a
    source "${PROJECT_DIR}/.env"
    set +a
fi

# ── Helpers ───────────────────────────────────────────────────────────────────

is_running() {
    if [[ -f "$PIDFILE" ]]; then
        local pid
        pid=$(cat "$PIDFILE")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        rm -f "$PIDFILE"
    fi
    return 1
}

notify_telegram() {
    local msg="$1"
    local token="${TELEGRAM_BOT_TOKEN:-}"
    local chat="${NOTIFICATION_TELEGRAM_CHAT_ID:-}"
    if [[ -n "$token" && -n "$chat" ]]; then
        curl -s -X POST "https://api.telegram.org/bot${token}/sendMessage" \
            -d chat_id="${chat}" \
            -d text="${msg}" \
            -d parse_mode="HTML" \
            > /dev/null 2>&1 || true
    fi
}

stop_tunnel() {
    if is_running; then
        local pid
        pid=$(cat "$PIDFILE")
        echo -e "  Parando tunnel (PID $pid)..."
        kill "$pid" 2>/dev/null || true
        sleep 1
        kill -9 "$pid" 2>/dev/null || true
        rm -f "$PIDFILE"
        echo -e "  ${GREEN}Tunnel parado.${NC}"
    else
        echo -e "  Tunnel no estaba corriendo."
    fi
}

wait_for_url() {
    local logfile="$1"
    local max_wait=30
    local url=""

    for i in $(seq 1 $max_wait); do
        # cloudflared logs the URL in different formats
        url=$(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "$logfile" 2>/dev/null | head -1)
        if [[ -z "$url" ]]; then
            url=$(grep -oE 'https://[a-zA-Z0-9.-]+\.cfargotunnel\.com' "$logfile" 2>/dev/null | head -1)
        fi
        if [[ -n "$url" ]]; then
            echo "$url" > "$URL_FILE"
            echo "$url"
            return 0
        fi
        sleep 1
    done
    return 1
}

# ── Commands ──────────────────────────────────────────────────────────────────

case "${1:-quick}" in

    quick)
        echo ""
        echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
        echo -e "${BLUE}  Cloudflare Quick Tunnel – Acceso Global      ${NC}"
        echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
        echo ""

        # Check server is running
        if ! curl -s "http://localhost:${LOCAL_PORT}/health" > /dev/null 2>&1; then
            echo -e "${YELLOW}Server no detectado en puerto ${LOCAL_PORT}.${NC}"
            echo -e "Arrancando servidor primero..."
            cd "${PROJECT_DIR}"
            bash start.sh 2>/dev/null &
            sleep 10
        fi

        # Stop existing tunnel
        stop_tunnel 2>/dev/null || true

        # Start quick tunnel (temporary URL, no login needed)
        echo -e "${YELLOW}Arrancando tunnel...${NC}"
        TUNNEL_LOG="${LOG_DIR}/tunnel.log"
        nohup cloudflared tunnel --url "http://localhost:${LOCAL_PORT}" \
            > "${TUNNEL_LOG}" 2>&1 &
        echo $! > "$PIDFILE"

        # Wait for URL
        echo -e "  Esperando URL publica..."
        TUNNEL_URL=$(wait_for_url "$TUNNEL_LOG")

        if [[ -n "$TUNNEL_URL" ]]; then
            echo ""
            echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
            echo -e "${GREEN}  TUNNEL ACTIVO                                ${NC}"
            echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
            echo ""
            echo -e "  ${CYAN}Dashboard:${NC}  ${TUNNEL_URL}/dashboard"
            echo -e "  ${CYAN}Health:${NC}     ${TUNNEL_URL}/health"
            echo -e "  ${CYAN}API:${NC}        ${TUNNEL_URL}/api/state"
            echo ""
            echo -e "  ${YELLOW}PID:${NC} $(cat $PIDFILE)"
            echo -e "  ${YELLOW}Log:${NC} tail -f ${TUNNEL_LOG}"
            echo ""
            echo -e "  ${RED}NOTA:${NC} Esta URL es temporal y cambia al reiniciar."
            echo -e "  Para URL permanente: ${CYAN}bash scripts/install_tunnel.sh setup${NC}"
            echo ""

            # Notify via Telegram
            notify_telegram "$(printf '🌐 <b>Dashboard Global Activo</b>\n\n📊 Dashboard: %s/dashboard\n❤️ Health: %s/health\n\n⚠️ URL temporal (quick tunnel)' "$TUNNEL_URL" "$TUNNEL_URL")"
        else
            echo -e "${RED}No se pudo obtener la URL. Revisa: tail -f ${TUNNEL_LOG}${NC}"
        fi
        ;;

    setup)
        echo ""
        echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
        echo -e "${BLUE}  Cloudflare Tunnel – Setup Permanente         ${NC}"
        echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
        echo ""
        echo -e "${YELLOW}Paso 1: Login en Cloudflare (abre navegador)${NC}"
        echo ""
        cloudflared tunnel login

        echo ""
        echo -e "${YELLOW}Paso 2: Creando tunnel '${TUNNEL_NAME}'${NC}"
        cloudflared tunnel create "${TUNNEL_NAME}" 2>/dev/null || echo -e "  ${YELLOW}Tunnel ya existe (OK).${NC}"

        TUNNEL_ID=$(cloudflared tunnel list --output json 2>/dev/null | python3 -c "
import sys, json
tunnels = json.load(sys.stdin)
for t in tunnels:
    if t.get('name') == '${TUNNEL_NAME}':
        print(t['id'])
        break
" 2>/dev/null || echo "")

        if [[ -z "$TUNNEL_ID" ]]; then
            # Fallback: parse text output
            TUNNEL_ID=$(cloudflared tunnel list 2>/dev/null | grep "${TUNNEL_NAME}" | awk '{print $1}')
        fi

        if [[ -n "$TUNNEL_ID" ]]; then
            echo ""
            echo -e "${GREEN}Tunnel creado.${NC}"
            echo -e "  ID:  ${TUNNEL_ID}"
            echo -e "  URL: https://${TUNNEL_ID}.cfargotunnel.com"
            echo ""

            # Create config
            CONFIG_DIR="$HOME/.cloudflared"
            mkdir -p "${CONFIG_DIR}"
            cat > "${CONFIG_DIR}/config.yml" <<EOF
tunnel: ${TUNNEL_ID}
credentials-file: ${CONFIG_DIR}/${TUNNEL_ID}.json

ingress:
  - service: http://localhost:${LOCAL_PORT}
EOF
            echo -e "  Config: ${CONFIG_DIR}/config.yml"
            echo ""
            echo -e "${YELLOW}Paso 3: Asociar un subdominio (opcional)${NC}"
            echo ""
            echo -e "  Para asociar un dominio que tengas en Cloudflare:"
            echo -e "    ${CYAN}bash scripts/install_tunnel.sh domain bot.g97.io${NC}"
            echo -e "    ${CYAN}bash scripts/install_tunnel.sh domain cmd.divenamic.com${NC}"
            echo ""
            echo -e "  O arrancar sin dominio custom:"
            echo -e "    ${CYAN}bash scripts/install_tunnel.sh permanent${NC}"
            echo ""

            # Save tunnel ID
            echo "$TUNNEL_ID" > "${PROJECT_DIR}/.tunnel-id"
        else
            echo -e "${RED}Error: No se pudo crear/encontrar el tunnel.${NC}"
            exit 1
        fi
        ;;

    domain)
        DOMAIN="${2:-}"
        if [[ -z "$DOMAIN" ]]; then
            echo -e "${RED}Uso: bash scripts/install_tunnel.sh domain <subdominio>${NC}"
            echo -e "  Ejemplo: bash scripts/install_tunnel.sh domain bot.g97.io"
            exit 1
        fi

        TUNNEL_ID=""
        if [[ -f "${PROJECT_DIR}/.tunnel-id" ]]; then
            TUNNEL_ID=$(cat "${PROJECT_DIR}/.tunnel-id")
        else
            TUNNEL_ID=$(cloudflared tunnel list --output json 2>/dev/null | python3 -c "
import sys, json
tunnels = json.load(sys.stdin)
for t in tunnels:
    if t.get('name') == '${TUNNEL_NAME}':
        print(t['id'])
        break
" 2>/dev/null || echo "")
        fi

        if [[ -z "$TUNNEL_ID" ]]; then
            echo -e "${RED}No hay tunnel creado. Ejecuta primero: bash scripts/install_tunnel.sh setup${NC}"
            exit 1
        fi

        echo ""
        echo -e "${YELLOW}Asociando ${DOMAIN} al tunnel ${TUNNEL_NAME}...${NC}"
        cloudflared tunnel route dns "${TUNNEL_NAME}" "${DOMAIN}"

        # Update config
        CONFIG_DIR="$HOME/.cloudflared"
        cat > "${CONFIG_DIR}/config.yml" <<EOF
tunnel: ${TUNNEL_ID}
credentials-file: ${CONFIG_DIR}/${TUNNEL_ID}.json

ingress:
  - hostname: ${DOMAIN}
    service: http://localhost:${LOCAL_PORT}
  - service: http_status:404
EOF

        # Update .env with permanent URL
        PERM_URL="https://${DOMAIN}"
        cd "${PROJECT_DIR}"
        if grep -q "^WEBHOOK_URL=" .env 2>/dev/null; then
            sed -i '' "s|^WEBHOOK_URL=.*|WEBHOOK_URL=${PERM_URL}|" .env
        fi

        # Save URL
        echo "${PERM_URL}" > "$URL_FILE"

        echo ""
        echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
        echo -e "${GREEN}  Dominio configurado                          ${NC}"
        echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
        echo ""
        echo -e "  ${CYAN}URL permanente:${NC} https://${DOMAIN}"
        echo -e "  ${CYAN}Dashboard:${NC}      https://${DOMAIN}/dashboard"
        echo -e "  ${CYAN}Health:${NC}         https://${DOMAIN}/health"
        echo ""
        echo -e "  Ahora arranca el tunnel:"
        echo -e "    ${CYAN}bash scripts/install_tunnel.sh permanent${NC}"
        echo ""
        ;;

    permanent)
        echo ""
        echo -e "${BLUE}Arrancando tunnel permanente...${NC}"

        stop_tunnel 2>/dev/null || true

        TUNNEL_LOG="${LOG_DIR}/tunnel.log"

        # Check if config exists
        if [[ -f "$HOME/.cloudflared/config.yml" ]]; then
            nohup cloudflared tunnel run "${TUNNEL_NAME}" \
                > "${TUNNEL_LOG}" 2>&1 &
            echo $! > "$PIDFILE"

            sleep 3

            # Get URL
            if [[ -f "$URL_FILE" ]]; then
                TUNNEL_URL=$(cat "$URL_FILE")
            else
                TUNNEL_URL="https://$(cat "${PROJECT_DIR}/.tunnel-id" 2>/dev/null).cfargotunnel.com"
            fi

            echo ""
            echo -e "${GREEN}Tunnel permanente activo.${NC}"
            echo -e "  ${CYAN}URL:${NC}       ${TUNNEL_URL}"
            echo -e "  ${CYAN}Dashboard:${NC} ${TUNNEL_URL}/dashboard"
            echo -e "  ${CYAN}PID:${NC}       $(cat $PIDFILE)"
            echo -e "  ${CYAN}Log:${NC}       tail -f ${TUNNEL_LOG}"
            echo ""

            notify_telegram "$(printf '🌐 <b>Dashboard Global UP</b>\n\n📊 %s/dashboard\n❤️ %s/health\n\n✅ Tunnel permanente activo' "$TUNNEL_URL" "$TUNNEL_URL")"
        else
            echo -e "${RED}No hay config de tunnel. Ejecuta primero:${NC}"
            echo -e "  bash scripts/install_tunnel.sh setup"
            exit 1
        fi
        ;;

    status)
        echo ""
        echo -e "${BLUE}Cloudflare Tunnel – Estado${NC}"
        echo ""

        if is_running; then
            echo -e "  Tunnel:  ${GREEN}RUNNING${NC} (PID $(cat $PIDFILE))"
        else
            echo -e "  Tunnel:  ${RED}STOPPED${NC}"
        fi

        if [[ -f "$URL_FILE" ]]; then
            echo -e "  URL:     ${CYAN}$(cat $URL_FILE)${NC}"
            echo -e "  Dashboard: $(cat $URL_FILE)/dashboard"
        fi

        if [[ -f "${PROJECT_DIR}/.tunnel-id" ]]; then
            echo -e "  Tunnel ID: $(cat "${PROJECT_DIR}/.tunnel-id")"
        fi

        if [[ -f "$HOME/.cloudflared/config.yml" ]]; then
            echo -e "  Config: ${GREEN}OK${NC} ($HOME/.cloudflared/config.yml)"
        else
            echo -e "  Config: ${YELLOW}No permanente (usa quick)${NC}"
        fi

        # Named tunnels
        echo ""
        echo -e "  ${BLUE}Tunnels registrados:${NC}"
        cloudflared tunnel list 2>/dev/null || echo "  (no logueado)"
        echo ""
        ;;

    stop)
        stop_tunnel
        ;;

    uninstall)
        echo -e "${YELLOW}Eliminando tunnel...${NC}"
        stop_tunnel
        rm -f "$URL_FILE" "${PROJECT_DIR}/.tunnel-id"

        if cloudflared tunnel list 2>/dev/null | grep -q "${TUNNEL_NAME}"; then
            echo -e "  Eliminando tunnel '${TUNNEL_NAME}'..."
            cloudflared tunnel delete "${TUNNEL_NAME}" 2>/dev/null || true
        fi

        rm -f "$HOME/.cloudflared/config.yml"
        echo -e "${GREEN}Tunnel eliminado.${NC}"
        ;;

    *)
        echo ""
        echo "Uso: $0 {quick|setup|permanent|domain|status|stop|uninstall}"
        echo ""
        echo "  quick                    → Tunnel temporal (funciona YA, sin login)"
        echo "  setup                    → Login CF + crear tunnel permanente"
        echo "  domain <subdomain>       → Asociar subdominio (ej: bot.g97.io)"
        echo "  permanent                → Arrancar tunnel permanente"
        echo "  status                   → Ver estado"
        echo "  stop                     → Parar tunnel"
        echo "  uninstall                → Eliminar todo"
        echo ""
        ;;
esac
