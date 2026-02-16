# Cursor Orchestrator

Orquestador self-hosted para gestionar todos tus proyectos de Cursor desde el movil via **Telegram** y **WhatsApp** (texto, voz, imagenes).

## Coste: $0/mes

Corre en tus propios PCs + Cloudflare Tunnel (gratis). Sin Railway, sin Fly.io, sin AWS.

## Arquitectura

```
Telegram/WhatsApp ──→ Cloudflare Tunnel ──→ FastAPI (tu PC)
                                                │
                     ┌──────────────────────────┤
                     │                          │
               Gemini API                  Agent Mesh (WebSocket)
          (intent parse + voz)          ┌──────┼──────┐
                                        │      │      │
                                      Mac   Linux   PC-3
                                      ├─ Claude Code   ├─ Docker
                                      ├─ Git           ├─ Git
                                      └─ 11 proyectos  └─ CI/CD
                                        │
                                Cursor Background Agent API
                                   (Opus 4.6 Max)
```

## Stack

| Componente | Tecnologia |
|---|---|
| Backend | FastAPI (Python 3.13) |
| Intent parsing | Gemini 2.5 Flash (structured JSON) |
| Transcripcion voz | Gemini multimodal nativo |
| Cambios de codigo | Cursor Background Agent (Opus 4.6 Max) |
| Analisis/Queries | Claude Code CLI (pagado empresa) |
| Operaciones locales | Agent mesh multi-PC |
| Domotica | Home Assistant + MQTT (Docker) |
| Webhook exposure | Cloudflare Tunnel (gratis) |
| BBDD | SQLite (aiosqlite) |

## Setup rapido

### 1. Clonar y configurar

```bash
cd ~/dev
git clone <repo> cursor-orchestrator && cd cursor-orchestrator
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # editar con tus API keys
```

### 2. Configurar Cloudflare Tunnel (reemplaza Railway)

```bash
bash scripts/setup_tunnel.sh          # login + crear tunnel
bash scripts/setup_tunnel.sh start    # iniciar tunnel
# Tu URL publica: https://xxx.cfargotunnel.com
```

### 3. Instalar el agente daemon en cada PC

```bash
# En tu Mac principal:
bash scripts/install_agent.sh

# En un PC Linux:
bash scripts/install_agent.sh --linux
```

El daemon auto-descubre:
- Proyectos en `~/dev` (package.json, pyproject.toml, etc.)
- Capabilities: claude_code, git, docker, node, python, npm, etc.

### 4. Arrancar Home Assistant (domotica)

```bash
docker compose up -d              # Arranca HA + MQTT
# Abre http://localhost:8123 y configura tus dispositivos
# Crea un Long-Lived Access Token: Perfil → Seguridad → Tokens
# Anade a .env: HA_URL=http://localhost:8123  HA_TOKEN=tu-token
```

HA integra automaticamente: Google Home, Alexa, luces, altavoces, y 2000+ dispositivos.

### 5. Arrancar el servidor

```bash
# Produccion (con CF Tunnel):
python -m src.main

# Desarrollo (polling, sin tunnel):
python -m src.main --polling
```

## Uso desde el movil

### Preguntas libres (ask anything)
- "Como funciona la autenticacion en la API?"
- "Que diferencia hay entre React Server Components y Client Components?"
- "Explica la arquitectura de divenamic"

### Cambios de codigo (Cursor Opus 4.6 Max)
- "Anade dark mode a plinng-web"
- "Refactoriza el componente de checkout en la expo app"
- "Fix el bug de login en la API"

### Operaciones
- "Corre los tests de la API"
- "Haz deploy de plinng-web"
- "Git status de divenamic"

### Voz
Envia un audio desde Telegram/WhatsApp con tu instruccion hablada.

### Imagenes
Envia una captura con una pregunta o instruccion sobre el codigo.

### Domotica (smart home)
- "Enciende las luces del salon"
- "Apaga la TV"
- "Pon modo pelicula"
- "Sube el volumen del altavoz"
- "Buenas noches" (activa escena)
- "Estado de la casa"
- "Di por el altavoz: la cena esta lista"

## Agent Mesh (multi-PC)

El orchestrator soporta N agentes en diferentes PCs:

```
GET /health → muestra todos los agentes, carga, proyectos, capabilities
```

El router escoge automaticamente el mejor agente basandose en:
1. Que PC tiene el proyecto en disco
2. Carga actual (menos ocupado primero)
3. Capabilities necesarias (claude_code, docker, etc.)
4. Estado de conexion (heartbeat cada 30s)

### Variables por agente (en cada PC)

```bash
WS_URL=ws://tu-pc-principal:8000/ws/agent
WS_SECRET=tu-secreto
AGENT_NAME=mi-macbook        # nombre amigable
AGENT_MAX_TASKS=3             # max tareas simultaneas
PROJECTS_DIR=/Users/yo/dev    # donde buscar proyectos
```

## Tests

```bash
python -m pytest tests/ -v  # 70 tests
```

## Estructura

```
src/
├── bot/                    # Telegram + WhatsApp handlers
│   ├── handlers.py         # Telegram commands y mensajes
│   ├── whatsapp_handlers.py # WhatsApp webhooks
│   ├── voice_parser.py     # Transcripcion via Gemini
│   ├── keyboards.py        # Botones inline
│   └── formatters.py       # Formato MarkdownV2
├── executors/
│   ├── agent_mesh.py       # Multi-PC agent mesh + smart routing
│   ├── cursor_executor.py  # Cursor Background Agent (Opus 4.6)
│   ├── claude_code_executor.py # Claude Code CLI wrapper
│   └── homeassistant_executor.py # Home Assistant REST API (domotica)
├── local_agent/
│   ├── daemon.py           # Agent daemon (corre en cada PC)
│   └── process_manager.py  # Subprocess manager async
├── orchestrator/
│   ├── intent_parser.py    # Gemini-based intent classification
│   ├── router.py           # Smart action router
│   ├── project_registry.py # YAML project registry
│   └── task_tracker.py     # SQLite task tracking + audit
├── providers/
│   └── gemini.py           # Gemini API client (REST)
├── config.py               # Settings from .env
└── main.py                 # Entry point (FastAPI + polling)
scripts/
├── setup_tunnel.sh         # Cloudflare Tunnel setup ($0/mes)
└── install_agent.sh        # Agent daemon installer (Mac/Linux)
docker-compose.yml            # Home Assistant + Mosquitto MQTT
ha-config/                    # HA configuration (auto-generated)
mqtt-config/                  # Mosquitto config
```
