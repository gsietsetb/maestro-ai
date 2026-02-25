#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
# start.sh – Lanza TODO el orchestrator con un solo comando
#
#   bash start.sh           → arranca server + daemon + caffeinate
#   bash start.sh stop      → para todo
#   bash start.sh status    → ver que esta corriendo
#   bash start.sh logs      → ver logs en tiempo real
#   bash start.sh awake     → solo mantener el Mac despierto
#   bash start.sh sleep-off → desactivar sleep permanentemente (pmset)
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

PIDFILE_SERVER="$PROJECT_DIR/.pid-server"
PIDFILE_DAEMON="$PROJECT_DIR/.pid-daemon"
PIDFILE_CAFFEINE="$PROJECT_DIR/.pid-caffeinate"
PIDFILE_WABRIDGE="$PROJECT_DIR/.pid-wabridge"
PIDFILE_TUNNEL="$PROJECT_DIR/.pid-tunnel"
LOGDIR="$PROJECT_DIR/logs"
mkdir -p "$LOGDIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Optional components (safe defaults for stability)
ENABLE_WAHA="${ENABLE_WAHA:-1}"
ENABLE_TUNNEL="${ENABLE_TUNNEL:-1}"
WATCHDOG_CHECK_TUNNEL="${WATCHDOG_CHECK_TUNNEL:-0}"

# ── Helpers ──────────────────────────────────────────────────────────────────

is_running() {
    local pidfile="$1"
    if [[ -f "$pidfile" ]]; then
        local pid
        pid=$(cat "$pidfile")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        rm -f "$pidfile"
    fi
    return 1
}

stop_process() {
    local pidfile="$1"
    local name="$2"
    if is_running "$pidfile"; then
        local pid
        pid=$(cat "$pidfile")
        echo -e "  Parando $name (PID $pid)..."
        kill "$pid" 2>/dev/null || true
        sleep 1
        kill -9 "$pid" 2>/dev/null || true
        rm -f "$pidfile"
        echo -e "  ${GREEN}$name parado.${NC}"
    else
        echo -e "  $name no estaba corriendo."
    fi
}

# ── Commands ─────────────────────────────────────────────────────────────────

case "${1:-start}" in

    start)
        echo ""
        echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
        echo -e "${BLUE}  Cursor Orchestrator – Arrancando...         ${NC}"
        echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
        echo ""

        # Activate venv
        if [[ ! -d ".venv" ]]; then
            echo -e "${RED}Error: .venv no encontrado. Ejecuta primero: bash scripts/setup.sh${NC}"
            exit 1
        fi
        source .venv/bin/activate

        # Check .env
        if [[ ! -f ".env" ]]; then
            echo -e "${RED}Error: .env no encontrado. Ejecuta primero: bash scripts/setup.sh${NC}"
            exit 1
        fi
        set -a
        source .env
        set +a

        # Stop existing instances
        stop_process "$PIDFILE_CAFFEINE" "Caffeinate" 2>/dev/null || true
        stop_process "$PIDFILE_SERVER" "Server" 2>/dev/null || true
        stop_process "$PIDFILE_DAEMON" "Daemon" 2>/dev/null || true
        sleep 1

        # ── Keep Mac awake (caffeinate) ──────────────────────────────────
        if [[ "$(uname)" == "Darwin" ]]; then
            echo -e "${YELLOW}[0/3] Activando caffeinate (Mac no dormira)...${NC}"
            # -d prevent display sleep, -i prevent idle sleep, -s prevent system sleep on AC
            caffeinate -d -i -s &
            echo $! > "$PIDFILE_CAFFEINE"
            echo -e "  ${GREEN}caffeinate activo (PID $(cat "$PIDFILE_CAFFEINE"))${NC}"
        fi

        # Start server (polling mode – no tunnel needed)
        echo -e "${YELLOW}[1/3] Arrancando servidor (Telegram polling + API)...${NC}"
        nohup python -m src.main --polling \
            > "$LOGDIR/server.log" 2>&1 &
        echo $! > "$PIDFILE_SERVER"
        SERVER_PID=$(cat "$PIDFILE_SERVER")
        echo -e "  ${GREEN}Server arrancado (PID $SERVER_PID)${NC}"

        # Wait for server to be ready
        echo -e "  Esperando que el servidor este listo..."
        for i in $(seq 1 15); do
            if curl -s http://localhost:8000/health > /dev/null 2>&1; then
                break
            fi
            sleep 1
        done

        if curl -s http://localhost:8000/health > /dev/null 2>&1; then
            echo -e "  ${GREEN}Servidor listo en http://localhost:8000${NC}"
        else
            echo -e "  ${YELLOW}Servidor arrancando (puede tardar unos segundos mas)${NC}"
        fi

        # Start daemon (local agent)
        echo ""
        echo -e "${YELLOW}[2/3] Arrancando agent daemon...${NC}"
        nohup python -m src.local_agent.daemon \
            > "$LOGDIR/daemon.log" 2>&1 &
        echo $! > "$PIDFILE_DAEMON"
        DAEMON_PID=$(cat "$PIDFILE_DAEMON")
        echo -e "  ${GREEN}Daemon arrancado (PID $DAEMON_PID)${NC}"

        # Start WhatsApp via WAHA (Docker)
        echo ""
        echo -e "${YELLOW}[3/4] Arrancando WhatsApp WAHA (Docker)...${NC}"
        if [[ "$ENABLE_WAHA" != "1" ]]; then
            echo -e "  ${YELLOW}WAHA deshabilitado (ENABLE_WAHA=$ENABLE_WAHA).${NC}"
        elif ! command -v docker >/dev/null 2>&1; then
            echo -e "  ${YELLOW}Docker no instalado. WAHA omitido.${NC}"
        elif ! docker info >/dev/null 2>&1; then
            echo -e "  ${YELLOW}Docker daemon no disponible. WAHA omitido.${NC}"
        else
            WAHA_STARTED=0
            # Stop old Baileys bridge if running
            if [ -f "$PIDFILE_WABRIDGE" ]; then
                kill "$(cat $PIDFILE_WABRIDGE)" 2>/dev/null || true
                rm -f "$PIDFILE_WABRIDGE"
            fi
            # Ensure WAHA container exists and is running
            if docker ps -q -f name=waha | grep -q .; then
                echo -e "  ${GREEN}WAHA ya corriendo${NC}"
                WAHA_STARTED=1
            elif docker ps -aq -f name=waha | grep -q .; then
                if docker start waha > /dev/null 2>&1; then
                    echo -e "  ${GREEN}WAHA reiniciado${NC}"
                    WAHA_STARTED=1
                else
                    echo -e "  ${YELLOW}No se pudo arrancar WAHA. Continua sin WhatsApp.${NC}"
                fi
            else
                if docker run -d \
                    --name waha \
                    --platform linux/amd64 \
                    -p 3002:3000 \
                    -e WHATSAPP_HOOK_URL=http://host.docker.internal:${PORT:-8000}/wa-bridge/incoming \
                    -e WHATSAPP_HOOK_EVENTS=message,message.any,session.status \
                    -e WHATSAPP_DEFAULT_ENGINE=WEBJS \
                    -e WAHA_DASHBOARD_ENABLED=true \
                    -v "$PROJECT_DIR/waha-data:/tmp/waha-data" \
                    devlikeapro/waha:latest > /dev/null 2>&1; then
                    echo -e "  ${GREEN}WAHA creado y arrancado${NC}"
                    WAHA_STARTED=1
                else
                    echo -e "  ${YELLOW}No se pudo crear WAHA. Continua sin WhatsApp.${NC}"
                fi
            fi
            if [[ "$WAHA_STARTED" -eq 1 ]]; then
                # Check session status
                sleep 3
                WAHA_STATUS=$(curl -s http://localhost:3002/api/sessions/default -H "X-Api-Key: ${WAHA_API_KEY:-}" 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin).get('status','UNKNOWN'))" 2>/dev/null || echo "UNKNOWN")
                if [ "$WAHA_STATUS" = "WORKING" ]; then
                    echo -e "  ${GREEN}WhatsApp conectado y funcionando${NC}"
                elif [ "$WAHA_STATUS" = "SCAN_QR_CODE" ]; then
                    echo -e "  ${YELLOW}Escanea QR en: http://localhost:3002/dashboard${NC}"
                else
                    echo -e "  ${YELLOW}WAHA status: $WAHA_STATUS (espera unos segundos)${NC}"
                fi
            fi
        fi

        # Wait for daemon to connect
        sleep 3

        # ── Cloudflare Tunnel (global access) ────────────────────────────
        echo ""
        echo -e "${YELLOW}[4/4] Arrancando Cloudflare Tunnel (acceso global)...${NC}"
        TUNNEL_URL=""
        if [[ "$ENABLE_TUNNEL" != "1" ]]; then
            echo -e "  ${YELLOW}Tunnel deshabilitado (ENABLE_TUNNEL=$ENABLE_TUNNEL).${NC}"
        elif [[ -f "$HOME/.cloudflared/config.yml" ]] && [[ -f "$PROJECT_DIR/.tunnel-id" ]]; then
            # Permanent tunnel
            stop_process "$PIDFILE_TUNNEL" "Tunnel" 2>/dev/null || true
            TUNNEL_LOG="$LOGDIR/tunnel.log"
            TNAME="cursor-orchestrator"
            nohup cloudflared tunnel run "$TNAME" \
                > "$TUNNEL_LOG" 2>&1 &
            echo $! > "$PIDFILE_TUNNEL"
            if [[ -f "$PROJECT_DIR/.tunnel-url" ]]; then
                TUNNEL_URL=$(cat "$PROJECT_DIR/.tunnel-url")
            fi
            echo -e "  ${GREEN}Tunnel permanente arrancado (PID $(cat $PIDFILE_TUNNEL))${NC}"
        elif command -v cloudflared &>/dev/null; then
            # Quick tunnel (temporary URL)
            stop_process "$PIDFILE_TUNNEL" "Tunnel" 2>/dev/null || true
            TUNNEL_LOG="$LOGDIR/tunnel.log"
            nohup cloudflared tunnel --url "http://localhost:${PORT:-8000}" \
                > "$TUNNEL_LOG" 2>&1 &
            echo $! > "$PIDFILE_TUNNEL"
            # Wait for URL
            for i in $(seq 1 20); do
                TUNNEL_URL=$(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -1 || true)
                if [[ -n "${TUNNEL_URL:-}" ]]; then
                    echo "$TUNNEL_URL" > "$PROJECT_DIR/.tunnel-url"
                    break
                fi
                sleep 1
            done
            if [[ -n "$TUNNEL_URL" ]]; then
                echo -e "  ${GREEN}Quick tunnel activo (PID $(cat $PIDFILE_TUNNEL))${NC}"
            else
                echo -e "  ${YELLOW}Tunnel arrancando (URL pendiente, revisa logs)${NC}"
            fi
        else
            echo -e "  ${YELLOW}cloudflared no instalado. Dashboard solo local.${NC}"
            echo -e "  Instalar: brew install cloudflared"
        fi

        # Show status
        echo ""
        echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
        echo -e "${GREEN}  TODO LISTO${NC}"
        echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
        echo ""

        # Try to get health info
        HEALTH=$(curl -s http://localhost:8000/health 2>/dev/null || echo "{}")
        MESH_CONNECTED=$(echo "$HEALTH" | python -c "import sys,json; d=json.load(sys.stdin); print(d.get('mesh',{}).get('connected',0))" 2>/dev/null || echo "?")

        echo -e "  Server:    ${GREEN}PID $SERVER_PID${NC} → http://localhost:8000"
        echo -e "  Daemon:    ${GREEN}PID $DAEMON_PID${NC} → agentes conectados: $MESH_CONNECTED"
        echo -e "  Telegram:  ${GREEN}@sierraAIBot${NC} → abre y envia /start"
        if [ -f "$PIDFILE_WABRIDGE" ]; then
            echo -e "  WhatsApp:  ${GREEN}PID $(cat $PIDFILE_WABRIDGE)${NC} → bridge en localhost:3001"
        fi
        if [[ -n "$TUNNEL_URL" ]]; then
            echo -e "  Dashboard: ${GREEN}${TUNNEL_URL}/dashboard${NC} (acceso global)"

            # Push tunnel URL to Supabase (auto-discovery for dashboard)
            SUPA_URL="https://qaixgdxfnytwtfygbtac.supabase.co"
            SUPA_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFhaXhnZHhmbnl0d3RmeWdidGFjIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzAyMjcwMTIsImV4cCI6MjA4NTgwMzAxMn0.FVoHyOAMxSfhIuj8Rjq785lR7yURK0L0reDjlJkbAMA"
            curl -s -X PATCH "$SUPA_URL/rest/v1/system_config?key=eq.tunnel_url" \
                -H "apikey: $SUPA_KEY" \
                -H "Authorization: Bearer $SUPA_KEY" \
                -H "Content-Type: application/json" \
                -d "{\"value\": \"$TUNNEL_URL\", \"updated_at\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" > /dev/null 2>&1 || true
            echo -e "  ${GREEN}Tunnel URL pushed to Supabase (auto-discovery)${NC}"

            # Notify via Telegram
            TG_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
            TG_CHAT="${NOTIFICATION_TELEGRAM_CHAT_ID:-}"
            if [[ -n "$TG_TOKEN" && -n "$TG_CHAT" ]]; then
                MSG=$(printf '🚀 <b>Orchestrator Arrancado</b>\n\n📊 Dashboard: %s/dashboard\n❤️ Health: %s/health\n🤖 Telegram: activo\n📱 WhatsApp: activo\n🔗 Mesh: %s agentes' "$TUNNEL_URL" "$TUNNEL_URL" "$MESH_CONNECTED")
                curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
                    -d chat_id="${TG_CHAT}" \
                    -d text="${MSG}" \
                    -d parse_mode="HTML" > /dev/null 2>&1 || true
            fi
        else
            echo -e "  Dashboard: ${GREEN}http://localhost:8000/dashboard${NC} (solo local)"
        fi
        echo ""
        echo -e "  ${YELLOW}Comandos utiles:${NC}"
        echo -e "    bash start.sh logs     → ver logs en tiempo real"
        echo -e "    bash start.sh status   → ver estado"
        echo -e "    bash start.sh stop     → parar todo"
        echo -e "    bash start.sh watchdog → auto-restart si server muere"
        echo ""
        ;;

    stop)
        echo ""
        echo -e "${YELLOW}Parando Cursor Orchestrator...${NC}"
        stop_process "$PIDFILE_TUNNEL" "Tunnel"
        stop_process "$PIDFILE_WABRIDGE" "WA Bridge"
        stop_process "$PIDFILE_DAEMON" "Daemon"
        stop_process "$PIDFILE_SERVER" "Server"
        stop_process "$PIDFILE_CAFFEINE" "Caffeinate"
        echo -e "${GREEN}Todo parado.${NC}"
        echo ""
        ;;

    restart)
        bash "$0" stop
        sleep 1
        bash "$0" start
        ;;

    status)
        echo ""
        echo -e "${BLUE}Estado del Cursor Orchestrator:${NC}"
        echo ""

        if is_running "$PIDFILE_SERVER"; then
            echo -e "  Server:     ${GREEN}RUNNING${NC} (PID $(cat "$PIDFILE_SERVER"))"
        else
            echo -e "  Server:     ${RED}STOPPED${NC}"
        fi

        if is_running "$PIDFILE_DAEMON"; then
            echo -e "  Daemon:     ${GREEN}RUNNING${NC} (PID $(cat "$PIDFILE_DAEMON"))"
        else
            echo -e "  Daemon:     ${RED}STOPPED${NC}"
        fi

        if is_running "$PIDFILE_CAFFEINE"; then
            echo -e "  Caffeinate: ${GREEN}RUNNING${NC} (PID $(cat "$PIDFILE_CAFFEINE")) – Mac NO dormira"
        else
            echo -e "  Caffeinate: ${YELLOW}OFF${NC} – Mac puede dormir"
        fi

        if is_running "$PIDFILE_TUNNEL"; then
            TURL=$(cat "$PROJECT_DIR/.tunnel-url" 2>/dev/null || echo "?")
            echo -e "  Tunnel:     ${GREEN}RUNNING${NC} (PID $(cat "$PIDFILE_TUNNEL")) → $TURL"
        else
            echo -e "  Tunnel:     ${YELLOW}OFF${NC} – solo acceso local"
        fi

        # pmset info
        if [[ "$(uname)" == "Darwin" ]]; then
            SLEEP_VAL=$(pmset -g custom 2>/dev/null | grep -E "^\s*sleep\s" | awk '{print $2}' | head -1)
            if [[ "$SLEEP_VAL" == "0" ]]; then
                echo -e "  pmset:      ${GREEN}Sleep desactivado permanentemente${NC}"
            elif [[ -n "$SLEEP_VAL" ]]; then
                echo -e "  pmset:      ${YELLOW}Sleep en ${SLEEP_VAL} min${NC} (usa '$0 sleep-off' para desactivar)"
            fi
        fi

        # Health check
        if curl -s http://localhost:8000/health > /dev/null 2>&1; then
            HEALTH=$(curl -s http://localhost:8000/health)
            echo ""
            echo -e "  ${BLUE}Health:${NC}"
            echo "$HEALTH" | python -m json.tool 2>/dev/null || echo "$HEALTH"
        fi
        echo ""
        ;;

    logs)
        echo -e "${YELLOW}Logs en tiempo real (Ctrl+C para salir):${NC}"
        echo ""
        tail -f "$LOGDIR/server.log" "$LOGDIR/daemon.log" 2>/dev/null || echo "No hay logs aun."
        ;;

    logs-server)
        tail -f "$LOGDIR/server.log" 2>/dev/null
        ;;

    logs-daemon)
        tail -f "$LOGDIR/daemon.log" 2>/dev/null
        ;;

    awake)
        # Solo mantener el Mac despierto sin arrancar nada mas
        if [[ "$(uname)" != "Darwin" ]]; then
            echo "Este comando solo funciona en macOS."
            exit 1
        fi
        if is_running "$PIDFILE_CAFFEINE"; then
            echo -e "${GREEN}caffeinate ya esta activo (PID $(cat "$PIDFILE_CAFFEINE"))${NC}"
        else
            caffeinate -d -i -s &
            echo $! > "$PIDFILE_CAFFEINE"
            echo -e "${GREEN}caffeinate activado (PID $(cat "$PIDFILE_CAFFEINE")) – Mac NO dormira${NC}"
        fi
        ;;

    sleep-off)
        # Desactivar sleep permanentemente via pmset (requiere sudo)
        if [[ "$(uname)" != "Darwin" ]]; then
            echo "Este comando solo funciona en macOS."
            exit 1
        fi
        echo ""
        echo -e "${YELLOW}Desactivando sleep del Mac permanentemente...${NC}"
        echo -e "  (requiere sudo – afecta System Settings > Energy Saver)"
        echo ""

        # Prevent sleep on AC power
        sudo pmset -a displaysleep 0   # Display never sleeps
        sudo pmset -a sleep 0          # System never sleeps
        sudo pmset -a disksleep 0      # Disk never sleeps

        # Disable automatic power off
        sudo pmset -a autopoweroff 0
        sudo pmset -a standby 0

        # Prevent sleep when display is off (clamshell wakeup)
        sudo pmset -a powernap 1       # Allow Power Nap for updates

        echo -e "${GREEN}Sleep desactivado permanentemente.${NC}"
        echo ""
        echo -e "  Para restaurar valores por defecto:"
        echo -e "    sudo pmset restoredefaults"
        echo ""
        echo -e "  O manualmente desde:"
        echo -e "    System Settings > Energy Saver / Battery"
        echo ""

        # Verificar
        echo -e "${BLUE}Config actual:${NC}"
        pmset -g custom 2>/dev/null | head -20
        echo ""
        ;;

    sleep-on)
        # Restaurar sleep por defecto
        if [[ "$(uname)" != "Darwin" ]]; then
            echo "Este comando solo funciona en macOS."
            exit 1
        fi
        echo -e "${YELLOW}Restaurando configuracion de sleep por defecto...${NC}"
        sudo pmset restoredefaults
        echo -e "${GREEN}Sleep restaurado.${NC}"
        ;;

    watchdog)
        echo -e "${YELLOW}Watchdog activo – reiniciara server si muere (Ctrl+C para salir)${NC}"
        WATCHDOG_INTERVAL=10
        WATCHDOG_ALERT_COOLDOWN=300
        LAST_SERVER_DOWN_ALERT=0
        SERVER_WAS_DOWN=0

        # If nothing is running, do a full start first
        if ! is_running "$PIDFILE_SERVER"; then
            echo -e "  ${YELLOW}Server no detectado, haciendo arranque completo...${NC}"
            if ! bash "$0" start; then
                echo -e "  ${YELLOW}Arranque inicial con warnings. Watchdog continua.${NC}"
            fi
            sleep 5
        fi

        while true; do
            if ! is_running "$PIDFILE_SERVER"; then
                echo -e "  ${RED}[$(date +%H:%M:%S)] Server muerto! Reiniciando...${NC}"
                # Notify via Telegram
                [[ -f ".venv/bin/activate" ]] && source .venv/bin/activate 2>/dev/null || true
                [[ -f ".env" ]] && source .env 2>/dev/null || true
                TG_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
                TG_CHAT="${NOTIFICATION_TELEGRAM_CHAT_ID:-}"
                NOW_TS=$(date +%s)
                if [[ -n "$TG_TOKEN" && -n "$TG_CHAT" ]] && {
                    [[ "$SERVER_WAS_DOWN" -eq 0 ]] ||
                    [[ $((NOW_TS - LAST_SERVER_DOWN_ALERT)) -ge "$WATCHDOG_ALERT_COOLDOWN" ]]
                }; then
                    curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
                        -d chat_id="${TG_CHAT}" \
                        -d text="⚠️ Server caido detectado. Reiniciando automaticamente..." \
                        > /dev/null 2>&1 || true
                    LAST_SERVER_DOWN_ALERT="$NOW_TS"
                fi
                SERVER_WAS_DOWN=1
                if ! bash "$0" restart; then
                    echo -e "  ${YELLOW}Reinicio parcial con warnings. Reintentando en el siguiente ciclo.${NC}"
                fi
                sleep 5
                if is_running "$PIDFILE_SERVER"; then
                    if [[ -n "$TG_TOKEN" && -n "$TG_CHAT" ]]; then
                        curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
                            -d chat_id="${TG_CHAT}" \
                            -d text="✅ Server reiniciado por watchdog." \
                            > /dev/null 2>&1 || true
                    fi
                    SERVER_WAS_DOWN=0
                fi
            else
                SERVER_WAS_DOWN=0
            fi
            if ! is_running "$PIDFILE_DAEMON"; then
                echo -e "  ${RED}[$(date +%H:%M:%S)] Daemon muerto! Reiniciando...${NC}"
                [[ -f ".venv/bin/activate" ]] && source .venv/bin/activate 2>/dev/null || true
                if nohup python -m src.local_agent.daemon > "$LOGDIR/daemon.log" 2>&1 & then
                    echo $! > "$PIDFILE_DAEMON"
                    echo -e "  ${GREEN}Daemon reiniciado (PID $(cat $PIDFILE_DAEMON))${NC}"
                else
                    echo -e "  ${YELLOW}No se pudo reiniciar daemon en este ciclo.${NC}"
                fi
            fi
            if [[ "$WATCHDOG_CHECK_TUNNEL" == "1" ]] && [[ "$ENABLE_TUNNEL" == "1" ]]; then
                if ! is_running "$PIDFILE_TUNNEL"; then
                    echo -e "  ${RED}[$(date +%H:%M:%S)] Tunnel muerto! Reiniciando...${NC}"
                    if command -v cloudflared >/dev/null 2>&1; then
                        TUNNEL_LOG="$LOGDIR/tunnel.log"
                        nohup cloudflared tunnel --url "http://localhost:${PORT:-8000}" > "$TUNNEL_LOG" 2>&1 &
                        echo $! > "$PIDFILE_TUNNEL"
                        sleep 5
                        NEW_URL=$(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -1 || true)
                        if [[ -n "$NEW_URL" ]]; then
                            echo "$NEW_URL" > "$PROJECT_DIR/.tunnel-url"
                            echo -e "  ${GREEN}Tunnel reiniciado → $NEW_URL${NC}"
                        fi
                    else
                        echo -e "  ${YELLOW}cloudflared no disponible, omitiendo reinicio tunnel.${NC}"
                    fi
                fi
            fi
            sleep "$WATCHDOG_INTERVAL"
        done
        ;;

    *)
        echo ""
        echo "Uso: $0 {start|stop|restart|status|logs|watchdog|awake|sleep-off|sleep-on}"
        echo ""
        echo "  start     → Arranca server + daemon + caffeinate"
        echo "  stop      → Para todo"
        echo "  restart   → Reinicia todo"
        echo "  status    → Ver estado"
        echo "  logs      → Logs en tiempo real"
        echo "  watchdog  → Auto-restart si algun proceso muere"
        echo "  awake     → Solo caffeinate (Mac no duerme)"
        echo "  sleep-off → Desactivar sleep permanentemente (pmset + sudo)"
        echo "  sleep-on  → Restaurar sleep por defecto"
        echo ""
        ;;
esac
