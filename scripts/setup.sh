#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
# setup.sh – Setup completo del Cursor Orchestrator
#
# Ejecuta esto UNA VEZ y tendras todo listo.
# Despues solo necesitas: python -m src.main --polling
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}   Cursor Orchestrator – Setup Completo                       ${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo ""

# ── Step 1: Python venv ──────────────────────────────────────────────────────

echo -e "${YELLOW}[1/6] Python virtual environment...${NC}"
if [[ ! -d ".venv" ]]; then
    PYTHON=""
    for p in python3.13 python3.12 python3.11 python3; do
        if command -v "$p" &>/dev/null; then
            PYTHON="$p"
            break
        fi
    done

    if [[ -z "$PYTHON" ]]; then
        echo -e "${RED}Python 3.11+ no encontrado. Instala Python primero.${NC}"
        exit 1
    fi

    echo "  Creando venv con $PYTHON..."
    $PYTHON -m venv .venv
    source .venv/bin/activate
    pip install --upgrade pip -q
    pip install -r requirements.txt -q
    echo -e "${GREEN}  venv creado e instalado.${NC}"
else
    source .venv/bin/activate
    echo -e "${GREEN}  venv ya existe.${NC}"
fi

# ── Step 2: .env file ────────────────────────────────────────────────────────

echo ""
echo -e "${YELLOW}[2/6] Configuracion (.env)...${NC}"

if [[ ! -f ".env" ]]; then
    cp .env.example .env
    echo -e "  .env creado desde .env.example"
fi

# Check required keys
source_env() {
    set -a
    source .env 2>/dev/null || true
    set +a
}
source_env

MISSING=()

# Gemini
if [[ -z "${GEMINI_API_KEY:-}" || "${GEMINI_API_KEY}" == "your-gemini-api-key" ]]; then
    MISSING+=("GEMINI_API_KEY")
    echo ""
    echo -e "  ${RED}GEMINI_API_KEY no configurada.${NC}"
    echo -e "  Ve a: ${BLUE}https://aistudio.google.com/apikey${NC}"
    echo -e "  Crea una key y pegala aqui:"
    read -rp "  GEMINI_API_KEY: " gemini_key
    if [[ -n "$gemini_key" ]]; then
        if [[ "$(uname)" == "Darwin" ]]; then
            sed -i '' "s|GEMINI_API_KEY=.*|GEMINI_API_KEY=${gemini_key}|" .env
        else
            sed -i "s|GEMINI_API_KEY=.*|GEMINI_API_KEY=${gemini_key}|" .env
        fi
        echo -e "  ${GREEN}Guardada.${NC}"
    fi
fi

# Telegram
if [[ -z "${TELEGRAM_BOT_TOKEN:-}" || "${TELEGRAM_BOT_TOKEN}" == "your-telegram-bot-token" ]]; then
    echo ""
    echo -e "  ${YELLOW}TELEGRAM_BOT_TOKEN no configurada.${NC}"
    echo -e "  Pasos:"
    echo -e "    1. Abre Telegram y busca ${BLUE}@BotFather${NC}"
    echo -e "    2. Envia ${BLUE}/newbot${NC}"
    echo -e "    3. Pon un nombre (ej: Mi Orchestrator)"
    echo -e "    4. Pon un username (ej: mi_orchestrator_bot)"
    echo -e "    5. BotFather te dara un token. Pegalo aqui:"
    read -rp "  TELEGRAM_BOT_TOKEN: " tg_token
    if [[ -n "$tg_token" ]]; then
        if [[ "$(uname)" == "Darwin" ]]; then
            sed -i '' "s|TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=${tg_token}|" .env
        else
            sed -i "s|TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=${tg_token}|" .env
        fi
        echo -e "  ${GREEN}Guardada.${NC}"
    fi
fi

# Telegram user ID
if [[ -z "${TELEGRAM_ALLOWED_USER_IDS:-}" || "${TELEGRAM_ALLOWED_USER_IDS}" == "123456789" ]]; then
    echo ""
    echo -e "  ${YELLOW}Tu Telegram User ID${NC}"
    echo -e "  Para obtenerlo:"
    echo -e "    1. Abre Telegram y busca ${BLUE}@userinfobot${NC}"
    echo -e "    2. Envia ${BLUE}/start${NC}"
    echo -e "    3. Te dira tu ID (un numero). Pegalo aqui:"
    read -rp "  TELEGRAM_ALLOWED_USER_IDS: " tg_uid
    if [[ -n "$tg_uid" ]]; then
        if [[ "$(uname)" == "Darwin" ]]; then
            sed -i '' "s|TELEGRAM_ALLOWED_USER_IDS=.*|TELEGRAM_ALLOWED_USER_IDS=${tg_uid}|" .env
        else
            sed -i "s|TELEGRAM_ALLOWED_USER_IDS=.*|TELEGRAM_ALLOWED_USER_IDS=${tg_uid}|" .env
        fi
        echo -e "  ${GREEN}Guardada.${NC}"
    fi
fi

# WS_SECRET
if [[ "${WS_SECRET:-}" == "your-strong-shared-secret" || "${WS_SECRET:-}" == "change-me" ]]; then
    WS_SECRET_GEN=$(openssl rand -hex 16)
    if [[ "$(uname)" == "Darwin" ]]; then
        sed -i '' "s|WS_SECRET=.*|WS_SECRET=${WS_SECRET_GEN}|" .env
    else
        sed -i "s|WS_SECRET=.*|WS_SECRET=${WS_SECRET_GEN}|" .env
    fi
    echo -e "  ${GREEN}WS_SECRET generado automaticamente.${NC}"
fi

echo -e "${GREEN}  .env configurado.${NC}"

# ── Step 3: Database ─────────────────────────────────────────────────────────

echo ""
echo -e "${YELLOW}[3/6] Base de datos...${NC}"
mkdir -p data
echo -e "${GREEN}  Directorio data/ listo (SQLite se crea al arrancar).${NC}"

# ── Step 4: Verificacion ─────────────────────────────────────────────────────

echo ""
echo -e "${YELLOW}[4/6] Verificando instalacion...${NC}"
source .venv/bin/activate

python -c "
from src.config import get_settings
from src.main import build_components

s = get_settings()
print(f'  Telegram: {\"OK\" if s.telegram_enabled else \"NO configurado\"}')
print(f'  WhatsApp: {\"OK\" if s.whatsapp_enabled else \"NO configurado\"}')
print(f'  Gemini: {\"OK\" if s.gemini_configured else \"NO configurado\"}')
print(f'  Cursor API: {\"OK\" if s.cursor_api_key else \"NO configurado\"}')
print(f'  Home Assistant: {\"OK\" if s.ha_enabled else \"NO configurado\"}')
print()
print('  Importando todos los modulos...')
c = build_components(s)
print(f'  Proyectos: {len(c[\"registry\"].project_names())}')
print(f'  Router: OK')
print(f'  Agent Mesh: OK')
"

echo -e "${GREEN}  Todo verificado.${NC}"

# ── Step 5: Tests ────────────────────────────────────────────────────────────

echo ""
echo -e "${YELLOW}[5/6] Ejecutando tests...${NC}"
python -m pytest tests/ -q 2>&1 | tail -3
echo -e "${GREEN}  Tests completados.${NC}"

# ── Step 6: Instrucciones finales ────────────────────────────────────────────

echo ""
echo -e "${YELLOW}[6/6] Todo listo!${NC}"
echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  COMO USARLO:${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${YELLOW}1. Arrancar (modo desarrollo – polling):${NC}"
echo -e "     cd $PROJECT_DIR"
echo -e "     source .venv/bin/activate"
echo -e "     python -m src.main --polling"
echo ""
echo -e "  ${YELLOW}2. Arrancar el agent daemon (en otro terminal):${NC}"
echo -e "     cd $PROJECT_DIR"
echo -e "     source .venv/bin/activate"
echo -e "     python -m src.local_agent.daemon"
echo ""
echo -e "  ${YELLOW}3. Abre Telegram y busca tu bot${NC}"
echo -e "     Envia /start y empieza a mandar mensajes"
echo ""
echo -e "  ${YELLOW}4. Modo produccion (con Cloudflare Tunnel):${NC}"
echo -e "     bash scripts/setup_tunnel.sh"
echo -e "     python -m src.main"
echo ""
echo -e "  ${YELLOW}5. Instalar daemon como servicio (Mac):${NC}"
echo -e "     bash scripts/install_agent.sh"
echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
