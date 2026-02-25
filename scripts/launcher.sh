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

# Wait for network (best effort; don't block boot forever)
log "Comprobando red..."
NETWORK_OK=0
for i in $(seq 1 20); do
    if curl -sS --max-time 3 https://api.telegram.org >/dev/null 2>&1 || \
       curl -sS --max-time 3 https://www.cloudflare.com >/dev/null 2>&1; then
        NETWORK_OK=1
        log "Red disponible (intento $i)"
        break
    fi
    sleep 2
done
if [[ "$NETWORK_OK" -ne 1 ]]; then
    log "WARNING: red no confirmada tras timeout; continuo igualmente"
fi

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
