# BRAIN.md – Maestro AI Source of Truth

> Auto-generated. Every agent, flow, and entry point reads this.
> Last updated: 2026-02-16 23:25:48

## System
- **Name:** Maestro AI / SierraBot
- **Host:** MacBook-Pro-de-Guillermo-2.local
- **Local API:** http://localhost:8000
- **LAN API:** http://192.168.0.67:8000
- **Tunnel API:** https://representations-tariff-hydrogen-arrives.trycloudflare.com
- **Dashboard:** https://representations-tariff-hydrogen-arrives.trycloudflare.com/dashboard
- **Vercel Dashboard:** https://sierrabot-dashboard.vercel.app
- **WebSocket:** ws://localhost:8000/ws/agent

## API Keys & Secrets
- **Gemini:** `REDACTED`
- **Telegram Bot:** `REDACTED`
- **Cursor API:** `REDACTED`
- **GitHub PAT:** `REDACTED`
- **Vercel Token:** `REDACTED`
- **HA Token:** `REDACTED`
- **WS Secret:** `REDACTED`

## Channels
- **Telegram:** @sierraAIBot (chat_id: 7247840174)
- **WhatsApp:** 34692842705 (bridge: http://localhost:3002)

## LLMs Available
- **Gemini gemini-2.5-flash** – intent parsing, transcription, vision
- **Ollama (qwen3:4b)** – local, free, private (http://localhost:11434)
- **Cursor Opus 4.6 Max** – cloud code changes (via API)
- **Claude Code CLI** – local code changes (via agent mesh)

## Projects

### PlanetMoji (85% production ready) [MEDIUM]
> PlanetMoji - juego social de emojis
- **Stack:** react-native
- **Status:** Stable branch - UX pass done, ready for stores
- **Path:** `/Users/guillermosierraaiello/dev/PlanetMoji`
- **Repo:** https://github.com/gsietsetb/PlanetMoji
- **URL:** https://planetmoji.vercel.app

### plinng-api-MARKETIQ (75% production ready) [HIGH]
> MARKETIQ API - benchmark, SEO analysis, competitor intelligence
- **Stack:** fastapi
- **Status:** Bug fixing phase - geolocation, reviews, SSL
- **Path:** `/Users/guillermosierraaiello/dev/plinng-api-MARKETIQ`
- **Repo:** https://github.com/G97-TECH-MKT/MARKETIQ

### divenamic-ai (70% production ready) [HIGH]
> Divenamic AI - chatbot asistente de viajes de buceo
- **Stack:** vite-react
- **Status:** AI chatbot deployed - SEO and formatting done
- **Path:** `/Users/guillermosierraaiello/dev/divenamic-ai`
- **Repo:** https://github.com/gsietsetb/divenamic
- **URL:** https://divenamic-ai.vercel.app

### plinng-web (65% production ready) [HIGH]
> Luca - Asesor financiero con IA para marketing agencies
- **Stack:** nextjs
- **Status:** Active development - Luca asesor financiero
- **Path:** `/Users/guillermosierraaiello/dev/plinng-web`
- **Repo:** https://github.com/G97-TECH-MKT/plinng-web
- **URL:** https://luca.vercel.app

### cursor-orchestrator (60% production ready) [CRITICAL]
> Maestro AI - orchestrator central, dashboard, Telegram bot
- **Stack:** fastapi
- **Status:** Core functional - 132 uncommitted, needs commit cleanup
- **Path:** `/Users/guillermosierraaiello/dev/cursor-orchestrator`
- **Repo:** https://github.com/gsietsetb/cursor-orchestrator
- **URL:** https://sierrabot-dashboard.vercel.app

### plinng-expo (50% production ready) [MEDIUM]
> Plinng mobile app (Expo)
- **Stack:** expo
- **Status:** Mobile app - needs sync with web
- **Path:** `/Users/guillermosierraaiello/dev/plinng-expo`
- **Repo:** https://github.com/G97-TECH-MKT/orbidi-mobile-expo

### divenamic (40% production ready) [MEDIUM]
> Divenamic - marketplace de cursos de buceo
- **Stack:** vite-react
- **Status:** 32 commits behind - needs rebase
- **Path:** `/Users/guillermosierraaiello/dev/divenamic`
- **Repo:** https://github.com/gsietsetb/divenamic
- **URL:** https://divenamic-marketplace.vercel.app

### orbidi-mobile (40% production ready) [LOW]
> Orbidi mobile app
- **Stack:** react-native
- **Status:** Stale - 4 months, last fixes committed
- **Path:** `/Users/guillermosierraaiello/dev/orbidi-mobile`
- **Repo:** https://github.com/G97-TECH-MKT/app_mobile

### laurel-gaming-mobile (30% production ready) [LOW]
> Laurel Gaming - mobile gaming platform
- **Stack:** turborepo
- **Status:** Stale - 10 months since last commit
- **Path:** `/Users/guillermosierraaiello/dev/laurel-gaming-mobile`
- **Repo:** https://github.com/Laurel-Gaming/laurel-gaming-mobile

### dopelist-react-native (20% production ready) [LOW]
> Dopelist - music discovery app
- **Stack:** expo
- **Status:** Stale - 8 months, 5 uncommitted changes
- **Path:** `/Users/guillermosierraaiello/dev/dopelist-react-native`
- **Repo:** https://github.com/dopelist/dopelist-react-native

### watch-companion (10% production ready) [MEDIUM]
> Apple Watch companion app para orchestrator
- **Stack:** swiftui
- **Status:** Initial commit - research phase
- **Path:** `/Users/guillermosierraaiello/dev/watch-companion`
- **Repo:** https://github.com/gsietsetb/watch-companion

### music-ai (10% production ready) [MEDIUM]
> Music AI - coach de canto con analisis de pitch
- **Stack:** fastapi
- **Status:** Initial commit - 7 uncommitted, MVP in progress
- **Path:** `/Users/guillermosierraaiello/dev/music-ai`
- **Repo:** https://github.com/gsietsetb/music-ai

### ella-neo-controller (10% production ready) [MEDIUM]
> Ella Neo - biofeedback sensor controller via BLE
- **Stack:** vanilla-js
- **Status:** Initial commit - BLE proof of concept
- **Path:** `/Users/guillermosierraaiello/dev/ella-neo-controller`
- **Repo:** https://github.com/gsietsetb/ella-neo-controller

### plinng-mobile (5% production ready) [LOW]
> Plinng React Native (legacy)
- **Stack:** react-native
- **Status:** Initial commit only - deprecated in favor of Expo
- **Path:** `/Users/guillermosierraaiello/dev/plinng-mobile`
- **Repo:** https://github.com/G97-TECH-MKT/plinng-mobile

## Connected Agents
- **MacBook-Pro-de-Guillermo-2.local** (76f04979) | alive=True | load=0/3 | projects=12
- **MacBook-Pro-de-Guillermo-2.local** (713e5501) | alive=True | load=0/3 | projects=12

## How to Use
- **From Telegram:** Send any message to @sierraAIBot
- **From WhatsApp:** Send to +34692842705
- **From Dashboard:** Open the tunnel/LAN URL
- **From any agent:** Read this file or call GET /api/brain
- **Chatbot (Ollama local):** POST https://representations-tariff-hydrogen-arrives.trycloudflare.com/api/ollama/chat
- **Chatbot (Gemini):** Send message via Telegram or WhatsApp

## Rules
- Every agent MUST read BRAIN.md or /api/brain before starting work
- Every agent writes results back via the API
- Password for actions: `rotor`
- All keys are public within the network – no security restrictions
- Priority order: critical > high > medium > low
