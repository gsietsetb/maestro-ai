#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
# install_autostart.sh – Instala el Cursor Orchestrator COMPLETO como servicio
#
# Esto arranca TODO al hacer login en el Mac:
#   - Server (Telegram polling + FastAPI + Dashboard)
#   - Agent Daemon (mesh local)
#   - WhatsApp Web Bridge (Baileys)
#   - Caffeinate (Mac no duerme)
#
# Usage:
#   bash scripts/install_autostart.sh               # instalar y arrancar
#   bash scripts/install_autostart.sh uninstall      # desinstalar
#   bash scripts/install_autostart.sh status         # ver estado
#   bash scripts/install_autostart.sh logs           # ver logs
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

LABEL="com.cursor-orchestrator.autostart"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${PROJECT_DIR}/logs"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
LAUNCHER="${PROJECT_DIR}/scripts/launcher.sh"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# ── Uninstall ─────────────────────────────────────────────────────────────────

if [[ "${1:-}" == "uninstall" ]]; then
    echo ""
    echo -e "${YELLOW}Desinstalando Cursor Orchestrator autostart...${NC}"
    echo ""

    # Stop services first
    cd "${PROJECT_DIR}"
    bash start.sh stop 2>/dev/null || true

    # Remove LaunchAgent
    launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
    rm -f "${PLIST}"
    rm -f "${LAUNCHER}"

    # Remove old agent-only LaunchAgent if exists
    OLD_LABEL="com.cursor-orchestrator.agent"
    launchctl bootout "gui/$(id -u)/${OLD_LABEL}" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/${OLD_LABEL}.plist"

    echo -e "${GREEN}Desinstalado.${NC}"
    echo -e "  El orchestrator ya NO arrancara al encender el Mac."
    echo ""
    exit 0
fi

# ── Status ────────────────────────────────────────────────────────────────────

if [[ "${1:-}" == "status" ]]; then
    echo ""
    echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  Cursor Orchestrator – Autostart Status       ${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
    echo ""

    # Check LaunchAgent
    if [[ -f "${PLIST}" ]]; then
        echo -e "  LaunchAgent:  ${GREEN}INSTALADO${NC}"
        echo -e "  Plist:        ${PLIST}"
    else
        echo -e "  LaunchAgent:  ${RED}NO INSTALADO${NC}"
    fi

    # Check if loaded
    if launchctl print "gui/$(id -u)/${LABEL}" &>/dev/null; then
        echo -e "  Estado:       ${GREEN}CARGADO (activo)${NC}"
    else
        echo -e "  Estado:       ${YELLOW}NO CARGADO${NC}"
    fi

    echo ""

    # Delegate to start.sh status
    cd "${PROJECT_DIR}"
    bash start.sh status 2>/dev/null || true
    exit 0
fi

# ── Logs ──────────────────────────────────────────────────────────────────────

if [[ "${1:-}" == "logs" ]]; then
    echo -e "${YELLOW}Logs del autostart (Ctrl+C para salir):${NC}"
    echo ""
    tail -f "${LOG_DIR}/autostart.log" "${LOG_DIR}/server.log" "${LOG_DIR}/daemon.log" 2>/dev/null || echo "No hay logs aun."
    exit 0
fi

# ── Checks ────────────────────────────────────────────────────────────────────

if [[ "$(uname)" != "Darwin" ]]; then
    echo -e "${RED}Error: Este script solo funciona en macOS.${NC}"
    echo "  Para Linux, usa systemd: bash scripts/install_agent.sh --linux"
    exit 1
fi

if [[ ! -d "${PROJECT_DIR}/.venv" ]]; then
    echo -e "${RED}Error: .venv no encontrado. Ejecuta primero:${NC}"
    echo "  bash scripts/setup.sh"
    exit 1
fi

if [[ ! -f "${PROJECT_DIR}/.env" ]]; then
    echo -e "${RED}Error: .env no encontrado. Ejecuta primero:${NC}"
    echo "  bash scripts/setup.sh"
    exit 1
fi

mkdir -p "${LOG_DIR}"

# ── Create launcher script ────────────────────────────────────────────────────
# LaunchAgents need a simple script to launch. start.sh uses interactive
# features (colors, etc.) so we wrap it cleanly.

cat > "${LAUNCHER}" <<'LAUNCHER_SCRIPT'
#!/bin/bash
# launcher.sh – Called by macOS LaunchAgent at login
# Starts the full Cursor Orchestrator stack

export PATH="/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:$PATH"

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${PROJECT_DIR}/logs"
LOGFILE="${LOG_DIR}/autostart.log"

mkdir -p "${LOG_DIR}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "${LOGFILE}"
}

log "═══════════════════════════════════════════════"
log "Cursor Orchestrator – Autostart iniciado"
log "═══════════════════════════════════════════════"

cd "${PROJECT_DIR}" || { log "ERROR: No se pudo acceder a ${PROJECT_DIR}"; exit 1; }

# Wait for network (important on boot)
log "Esperando red..."
for i in $(seq 1 30); do
    if ping -c 1 -W 2 api.telegram.org &>/dev/null; then
        log "Red disponible (intento $i)"
        break
    fi
    sleep 2
done

# Source the venv
if [[ -f ".venv/bin/activate" ]]; then
    source .venv/bin/activate
    log "venv activado"
else
    log "ERROR: .venv no encontrado"
    exit 1
fi

# Load .env
if [[ -f ".env" ]]; then
    set -a
    source .env
    set +a
    log ".env cargado"
fi

# Stop any existing instances cleanly
log "Limpiando instancias previas..."
bash start.sh stop >> "${LOGFILE}" 2>&1 || true
sleep 2

# Start everything via start.sh
log "Arrancando stack completo..."
bash start.sh >> "${LOGFILE}" 2>&1

# Verify health
sleep 5
if curl -s http://localhost:${PORT:-8000}/health > /dev/null 2>&1; then
    HEALTH=$(curl -s http://localhost:${PORT:-8000}/health 2>/dev/null)
    log "Server OK – health check passed"
    log "Dashboard: http://localhost:${PORT:-8000}/dashboard"
else
    log "WARNING: Server no responde aun (puede tardar)"
fi

log "Autostart completado"
log "═══════════════════════════════════════════════"
LAUNCHER_SCRIPT

chmod +x "${LAUNCHER}"

# ── Create LaunchAgent plist ──────────────────────────────────────────────────

cat > "${PLIST}" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${LAUNCHER}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${PROJECT_DIR}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
        <key>HOME</key>
        <string>${HOME}</string>
    </dict>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/autostart-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/autostart-stderr.log</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
PLIST

# ── Remove old agent-only LaunchAgent if exists ──────────────────────────────

OLD_LABEL="com.cursor-orchestrator.agent"
if [[ -f "$HOME/Library/LaunchAgents/${OLD_LABEL}.plist" ]]; then
    echo -e "  ${YELLOW}Desinstalando LaunchAgent antiguo (solo daemon)...${NC}"
    launchctl bootout "gui/$(id -u)/${OLD_LABEL}" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/${OLD_LABEL}.plist"
    echo -e "  ${GREEN}Antiguo eliminado.${NC}"
fi

# ── Load the LaunchAgent ──────────────────────────────────────────────────────

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
sleep 1
launchctl bootstrap "gui/$(id -u)" "${PLIST}"

# ── Output ────────────────────────────────────────────────────────────────────

echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Cursor Orchestrator – Autostart Instalado               ${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${GREEN}Al encender/reiniciar el Mac, arrancara automaticamente:${NC}"
echo ""
echo -e "    ${CYAN}Server${NC}      → Telegram + FastAPI + Dashboard"
echo -e "    ${CYAN}Daemon${NC}      → Agent mesh local"
echo -e "    ${CYAN}WA Bridge${NC}   → WhatsApp Web (Baileys)"
echo -e "    ${CYAN}Caffeinate${NC}  → Mac no duerme"
echo ""
echo -e "  ${BLUE}Dashboard:${NC}    http://localhost:8000/dashboard"
echo -e "  ${BLUE}Health:${NC}       http://localhost:8000/health"
echo ""
echo -e "  ${YELLOW}Comandos utiles:${NC}"
echo -e "    bash scripts/install_autostart.sh status     → ver estado"
echo -e "    bash scripts/install_autostart.sh logs       → ver logs"
echo -e "    bash scripts/install_autostart.sh uninstall  → desinstalar"
echo ""
echo -e "  ${YELLOW}Ficheros:${NC}"
echo -e "    LaunchAgent: ${PLIST}"
echo -e "    Launcher:    ${LAUNCHER}"
echo -e "    Logs:        ${LOG_DIR}/autostart.log"
echo ""
echo -e "  ${GREEN}El orchestrator se ha lanzado ahora mismo.${NC}"
echo -e "  Espera unos 10 segundos y comprueba: ${CYAN}bash start.sh status${NC}"
echo ""
