/**
 * SierraOS Brain — Transfer ALL agent knowledge to single source of truth
 * Consolidates: BRAIN.md, AGENTS.md (all projects), projects.yaml, architecture rules
 * Usage: npx tsx scripts/transfer-all-knowledge.ts
 */
import { ingest, stats, sleep, DELAY_MS } from './config';

interface KnowledgeItem {
  content: string;
  source: string;
  tags: string[];
  category: string;
}

const KNOWLEDGE: KnowledgeItem[] = [

  // ═══════════════════════════════════════════════════════════════
  // SECTION 1: SYSTEM & INFRASTRUCTURE (from BRAIN.md)
  // ═══════════════════════════════════════════════════════════════

  {
    content: `SierraOS System Overview:
- Name: Maestro AI / SierraBot
- Host: MacBook-Pro-de-Guillermo-2.local
- Local API: http://localhost:8000
- Dashboard: https://sierrabot-dashboard.vercel.app
- WebSocket: ws://localhost:8000/ws/agent
- Channels: Telegram @sierraAIBot (chat_id: 7247840174), WhatsApp 34692842705 (bridge: http://localhost:3002)
- Password for actions: rotor
- Priority order: critical > high > medium > low
- Every agent MUST read BRAIN.md or /api/brain before starting work`,
    source: 'brain-md', tags: ['system', 'infrastructure', 'sierraos'], category: 'tech'
  },

  {
    content: `LLMs disponibles en SierraOS:
1. Gemini gemini-2.5-flash — intent parsing, transcription, vision
2. Ollama (qwen3:4b) — local, free, private (http://localhost:11434)
3. Cursor Opus 4.6 Max — cloud code changes (via API)
4. Claude Code CLI — local code changes (via agent mesh)

Connected Agents: 2x MacBook-Pro-de-Guillermo-2.local, alive, load 0/3, 12 projects each.

Acceso: Telegram (@sierraAIBot), WhatsApp (+34692842705), Dashboard (tunnel/LAN URL), API (GET /api/brain), Ollama local chat, Gemini via Telegram/WhatsApp.`,
    source: 'brain-md', tags: ['llm', 'agents', 'ai'], category: 'tech'
  },

  {
    content: `Infraestructura SierraOS:
- Cloudflare Workers: API principal (divenamic-api.divenamic.workers.dev)
- Cloudflare Pages: dashboards (sierrabot.pages.dev)
- Vercel: frontends (luca.vercel.app, divenamic-ai.vercel.app, planetmoji.vercel.app)
- Supabase: 2 proyectos (divenamic + planetmoji)
- Stripe: pagos
- Docker Home Server: baileys-bridge, sierraos-brain, cloudflared tunnel
- Brain API: POST /api/brain/ingest, GET /api/brain/notes, GET /api/brain/search?q=, GET /api/brain/stats
- Brain base URL: divenamic-api.divenamic.workers.dev`,
    source: 'brain-md', tags: ['infra', 'servers', 'cloudflare', 'vercel', 'supabase'], category: 'tech'
  },

  // ═══════════════════════════════════════════════════════════════
  // SECTION 2: PROJECT REGISTRY (from projects.yaml + BRAIN.md)
  // ═══════════════════════════════════════════════════════════════

  {
    content: `Proyecto CRITICAL: cursor-orchestrator (60% ready)
- Maestro AI - orchestrator central, dashboard, Telegram bot
- Stack: fastapi | Path: ~/dev/cursor-orchestrator
- Repo: github.com/gsietsetb/cursor-orchestrator | URL: sierrabot-dashboard.vercel.app
- Status: Core functional - 132 uncommitted, needs commit cleanup
- Commands: dev="python -m src.main --polling", test="python -m pytest tests/ -v", start="bash start.sh"
- Aliases: orchestrator, nerve, brain, sistema, maestro
- 12 agentes: router, watch, home, scraper, content, music, biometric, researcher, enricher, notifier, brain, qa
- Comandos bot: /buscar, /deploy, /status, /brain, /note, /idea, /qa, /infra, /billing`,
    source: 'projects-yaml', tags: ['orchestrator', 'critical', 'project'], category: 'tech'
  },

  {
    content: `Proyecto HIGH: divenamic-ai (70% ready)
- Chatbot asistente de viajes de buceo
- Stack: vite-react | Path: ~/dev/divenamic-ai
- Repo: github.com/gsietsetb/divenamic | URL: divenamic-ai.vercel.app
- Status: AI chatbot deployed - SEO and formatting done
- Commands: dev="npm run dev", build="npm run build"
- Aliases: dive-ai, dive-connect
- Backend: Hono + Cloudflare Workers (api/)
- DB: Supabase | Maps: Mapbox + Dive Number
- Chatbot: Telegram @divenamicBot (LIVE), WhatsApp (pendiente Meta)`,
    source: 'projects-yaml', tags: ['divenamic', 'high', 'project', 'diving'], category: 'tech'
  },

  {
    content: `Proyecto HIGH: plinng-web (65% ready)
- Luca - Asesor financiero con IA para marketing agencies
- Stack: nextjs | Path: ~/dev/plinng-web
- Repo: github.com/G97-TECH-MKT/plinng-web | URL: luca.vercel.app
- Status: Active development - Luca asesor financiero
- Commands: dev="npm run dev", build="npm run build", deploy="vercel deploy --prod"
- Aliases: plinng, web, marketing, dashboard, luca
- Design system: Lime/Purple palette, Geist+Sen fonts, base-4 grid
- State: Zustand | API: centralized apiClient + useApiQuery hooks`,
    source: 'projects-yaml', tags: ['plinng', 'high', 'project', 'fintech'], category: 'tech'
  },

  {
    content: `Proyecto HIGH: plinng-api-MARKETIQ (75% ready)
- MARKETIQ API - benchmark, SEO analysis, competitor intelligence
- Stack: fastapi | Path: ~/dev/plinng-api-MARKETIQ
- Repo: github.com/G97-TECH-MKT/MARKETIQ
- Status: Bug fixing phase - geolocation, reviews, SSL
- Commands: dev="uvicorn src.marketiq.main:app --reload", test="pytest", deploy="docker build && push"
- Aliases: api, marketiq, backend, fastapi`,
    source: 'projects-yaml', tags: ['marketiq', 'high', 'project', 'api'], category: 'tech'
  },

  {
    content: `Proyecto MEDIUM: PlanetMoji (85% ready - most production ready!)
- Juego social de emojis
- Stack: react-native | Path: ~/dev/PlanetMoji
- Repo: github.com/gsietsetb/PlanetMoji | URL: planetmoji.vercel.app
- Status: Stable branch - UX pass done, ready for stores
- UI: Components in vite-web/src/components/ui/ (Button, Avatar, LangSwitcher)
- i18n: ES/EN, useTranslation(), locales/*.json
- Gradientes en ángulo (135deg), buen contraste WCAG
- DB: Supabase`,
    source: 'projects-yaml', tags: ['planetmoji', 'medium', 'project', 'gaming'], category: 'tech'
  },

  {
    content: `Proyecto MEDIUM: plinng-expo (50% ready)
- Plinng mobile app (Expo)
- Stack: expo | Path: ~/dev/plinng-expo
- Repo: github.com/G97-TECH-MKT/orbidi-mobile-expo
- Status: Mobile app - needs sync with web
- Commands: dev="npx expo start", build="eas build"
- Design system: Same Plinng palette (lime/purple/beige), plinngColors + pColors (legacy)
- Fonts: Geist (Light→Bold) + Sen (Regular→ExtraBold)
- Architecture: common/ (api, components, hooks, stores) + features/ (auth, brief, gallery...)`,
    source: 'projects-yaml', tags: ['plinng-expo', 'medium', 'project', 'mobile'], category: 'tech'
  },

  {
    content: `Proyectos secundarios:
1. divenamic (40% ready, MEDIUM) - marketplace cursos buceo, 32 commits behind, needs rebase. URL: divenamic-marketplace.vercel.app
2. watch-companion (10%, MEDIUM) - Apple Watch companion, SwiftUI, research phase
3. music-ai (10%, MEDIUM) - Coach de canto con análisis de pitch, FastAPI, MVP in progress
4. ella-neo-controller (10%, MEDIUM) - Biofeedback sensor BLE, vanilla-js, proof of concept
5. orbidi-mobile (40%, LOW) - Orbidi mobile, react-native, stale 4 months
6. laurel-gaming-mobile (30%, LOW) - Gaming platform, turborepo, stale 10 months
7. dopelist-react-native (20%, LOW) - Music discovery, expo, stale 8 months
8. plinng-mobile (5%, LOW) - RN legacy, deprecated for Expo`,
    source: 'projects-yaml', tags: ['projects', 'secondary', 'overview'], category: 'tech'
  },

  // ═══════════════════════════════════════════════════════════════
  // SECTION 3: DIVENAMIC ARCHITECTURE (from AGENTS.md)
  // ═══════════════════════════════════════════════════════════════

  {
    content: `Divenamic Product Goal (MVP):
- "Find a freediving/scuba course tomorrow near me and reserve"
- B2C flow: Landing → Location autocomplete → Select type+date → Results+map → Reserve
- B2B flow: "Add your center" → 60-second form → Center draft (Feria mode)
- Pages: / (landing), /search (list+map), /course/[id] (detail+reserve), /center/onboard
- Non-negotiable: WOW demo on mobile + 1 booking flow. Hardcoding allowed, security later.
- Monorepo: src/ (Vite+React frontend) + api/ (Hono+Cloudflare Workers) + packages/shared/ (Zod contracts) + supabase/`,
    source: 'divenamic-agents', tags: ['divenamic', 'product', 'mvp'], category: 'business'
  },

  {
    content: `Divenamic Data Sources:
1. Divessi/SSI REST API (GOLD) - Events search, centers by course, map results. Header: x-ssi-auth
2. Divessi Location Services - Dive sites by bounds (POST /api/locationServices.php). 100+ top sites cached.
3. SerpAPI - Autocomplete, Google Local (dive centers), Google Travel Explore (flights to dive destinations)
4. Dive Number API - Global dive site DB with embeddable map widget (Leaflet/OpenStreetMap)
5. Mapbox - Interactive maps (react-map-gl v7 + Mapbox GL JS v3)
6. Scubago - Scraping fallback (SPA, fragile)
7. Open-Meteo Marine API - Sea conditions (FREE, no API key): waves, swell, temp, wind, UV, 3-day forecast
8. Supabase - Events, bookings, dive sites DB`,
    source: 'divenamic-agents', tags: ['divenamic', 'data-sources', 'apis'], category: 'tech'
  },

  {
    content: `Divenamic Entity-Centric Architecture (13 entities):
Graph: dive_site ↔ center ↔ course ↔ trip ↔ liveaboard ↔ animal ↔ destination ↔ blog ↔ record ↔ athlete ↔ tool ↔ community ↔ marketplace
Every page is a graph node. Never dead ends. Min 3 relations per entity.
Key implementations: entity-links.ts (graph+resolvers+sitemap), RelatedContent.tsx, useUniversalSearch.ts (searches 13 sources)
Every detail page: 2+ related content sections + ExploreCTA (5 links) + breadcrumbs
~230 URLs total: 100 top dive sites, 80+ animals, 20+ directory centers, 10+ blog posts
Navigation: Explorar (search, divesites, top100, directory, animals, marketplace) | Aprender (blog, records, games, courses) | Herramientas (colorfix, reels, divelog) | Negocio (for-centers, onboard)`,
    source: 'divenamic-agents', tags: ['divenamic', 'architecture', 'entities'], category: 'tech'
  },

  {
    content: `Divenamic Chatbot Multicanal:
- Engine compartido: api/src/lib/chatbot-engine.ts (platform-agnostic)
- Telegram @divenamicBot: LIVE. Webhook: divenamic-api.divenamic.workers.dev/api/telegram/webhook
- Comandos: /start, /buscar [zona], /manana, /mar [lugar], /sitios [zona], /reservar, /reservas, /ayuda
- NLU: Detecta intents (buscar, reservar, mar, sitios, ayuda). Extrae ubicaciones. Multiidioma es/en/ca.
- Booking flow multi-paso: evento → personas → nombre → email → tel → tallas → notas → confirmar → Supabase
- Condiciones del mar: Open-Meteo Marine (GRATIS). 30+ destinos. Evaluación: Excelente/Bueno/Moderado/Difícil/No
- Estado conversación: in-memory Map por userId, TTL 30 min
- WhatsApp: pendiente aprobación Meta (App ID: 497373309790838)`,
    source: 'divenamic-agents', tags: ['divenamic', 'chatbot', 'telegram'], category: 'tech'
  },

  // ═══════════════════════════════════════════════════════════════
  // SECTION 4: SHARED ARCHITECTURE RULES (from all AGENTS.md)
  // ═══════════════════════════════════════════════════════════════

  {
    content: `Reglas de arquitectura compartidas (Plinng + Divenamic + PlanetMoji):
1. DRY absoluto: No duplicar lógica, texto, colores, endpoints, keys
2. i18n obligatorio: Todos los strings via useTranslation(), keys en es/en/ca
3. Tailwind-only styling: No hex hardcoded, no CSS custom. Colores = tokens en tailwind.config.js
4. Components dumb/stateless: Solo presentación. Business logic en hooks.
5. Hooks = business logic: Cada componente tiene hook dedicado que expone {data, isLoading, error, actions}
6. API centralizada: Un apiClient, un endpoints.ts, useApiQuery/useApiMutation
7. Base-4 spacing: Multiples de 4px. Usar Tailwind utilities.
8. Simpler > Clever: Clarity over cleverness, boring over complex, reuse over invention
9. No overengineering: Sin hexagonal, sin IoC, sin UseCase classes
10. No documentation files: Solo README.md raíz y AGENTS.md. Respuestas inline max 50 líneas.`,
    source: 'architecture-rules', tags: ['architecture', 'rules', 'frontend'], category: 'tech'
  },

  {
    content: `Plinng Design System (Web + Expo):
Color palette: Lime (100→700), Purple (100→800), Beige (25→300), Success, Warning, Alert, Grey
Primary: lime300Primary #BEFF50 | Purple: purple600Primary #883ae3 | Base bg: beige50BaseBg #F5F5EB
Fonts Web: Inter (Google Fonts) | Fonts Expo: Geist (Light→Bold) + Sen (Regular→ExtraBold)
Components: PlinngButton (primary/gradient/outline/ghost, sm/md/lg), Badge, Toast, PlinngLogo, PlinngIsotype
State: Zustand (global) + React state (local) + sessionStorage (cross-page)
Web structure: src/components/ (analyzing, brief, dashboard, landing, layout, seo, ui) + hooks/ + lib/ (api, firebase, stream) + pages/ + stores/ + types/
Expo structure: app/common/ (api, components, hooks, stores, utils) + features/ (auth, brief, gallery, home, inbox...)`,
    source: 'plinng-agents', tags: ['plinng', 'design-system', 'components'], category: 'tech'
  },

  // ═══════════════════════════════════════════════════════════════
  // SECTION 5: BUSINESS & MONETIZATION
  // ═══════════════════════════════════════════════════════════════

  {
    content: `Plan de monetización SierraOS ecosystem:
1. Divenamic: Comisiones en cursos de buceo via Stripe. Modelo marketplace. B2C bookings + B2B center onboarding.
2. TruesDate: Freemium dating app. Expansión Asia con Carlos (Marina Bay).
3. Cantafy/Music-AI: Music learning - coach de canto con análisis de pitch.
4. PlanetMoji: In-app purchases en juego social de emojis. 85% ready for stores.
5. Softwait: Paid themes para loading screens.
6. Plinng/Luca: Asesor financiero IA para marketing agencies. MARKETIQ API (SEO, benchmarks).
7. DGFolio: Portfolio personal.

Proyectos LIVE: Divenamic, DGFolio, PlanetMoji, TruesDate, Cantafy, Psyche, GitStory, Colexalia, Plinng, Softwait`,
    source: 'brain-md', tags: ['monetization', 'business', 'revenue'], category: 'business'
  },

  // ═══════════════════════════════════════════════════════════════
  // SECTION 6: SECRETS INVENTORY (locations only, NOT values)
  // ═══════════════════════════════════════════════════════════════

  {
    content: `Inventario de secrets y API keys (solo ubicaciones, NO valores):
- Gemini API key: BRAIN.md + .env
- Telegram Bot Token: BRAIN.md + api/.dev.vars + Cloudflare secrets
- Cursor API key: BRAIN.md
- GitHub PAT: BRAIN.md
- Vercel Token: BRAIN.md
- WS Secret: BRAIN.md
- Divessi x-ssi-auth: divenamic-ai/AGENTS.md (2 tokens diferentes para events vs locationServices)
- DiveNumber API key: divenamic-ai/AGENTS.md
- Supabase keys: .env files en divenamic + planetmoji
- WhatsApp verify token: divenamic_wa_verify_2026
- Stripe keys: .env files
ALERTA: BRAIN.md tiene secrets expuestos en texto plano. Migrar a variables de entorno.
Archivos .env activos: cursor-orchestrator, divenamic/api, divenamic, PlanetMoji, divenamic-ai, plinng-web, plinng-expo (dev/staging/prod), orbidi-mobile (dev/staging/prod), dopelist, laurel-gaming`,
    source: 'security-audit', tags: ['secrets', 'security', 'env'], category: 'tech'
  },

  // ═══════════════════════════════════════════════════════════════
  // SECTION 7: QA & DEPLOYMENT
  // ═══════════════════════════════════════════════════════════════

  {
    content: `QA Deploy Checklist (divenamic-ai skill):
Ubicación: ~/dev/divenamic-ai/.cursor/skills/qa-deploy-checklist/
Contiene: SKILL.md + QA-CHECKLIST.md + QA-TODOS.md
Propósito: Checklist de QA antes de cada deploy de Divenamic.

Cursor Skills disponibles (sistema):
1. create-rule: Crear reglas de Cursor (.cursor/rules/)
2. create-skill: Crear Agent Skills (SKILL.md)
3. update-cursor-settings: Modificar settings.json

Codex Skills:
1. skill-creator: Guía para crear skills de Codex
2. skill-installer: Instalar skills desde GitHub`,
    source: 'skills-inventory', tags: ['qa', 'deploy', 'skills'], category: 'tech'
  },

  // ═══════════════════════════════════════════════════════════════
  // SECTION 8: DIVENAMIC ADVANCED FEATURES
  // ═══════════════════════════════════════════════════════════════

  {
    content: `Divenamic UI Rules & MVP Status:
Landing: "Tomorrow you can be underwater." + location/category/date input + "Find a course" CTA
Search: Split view (list left, map right desktop; toggle mobile). Sticky search bar with chips.
Course detail: Hero image + center card + reserve button
Feria mode: /center/onboard, 60-second form, huge fonts, QR to claim later

MVP Definition of Done:
✅ Landing looks premium
✅ Search works for "tomorrow + freediving"
✅ Divessi centers appear in results (map pins)
✅ Map shows centers with clusters and popups
✅ BookingIntent created + confirmation
✅ Center onboard works
✅ Telegram Bot funcional
❌ WhatsApp Bot (pendiente Meta)

Types: DivenamicLocation, DivenamicCenter, DivenamicCourse, DivenamicEvent, DivenamicBookingIntent, SearchRequest/Response`,
    source: 'divenamic-agents', tags: ['divenamic', 'ui', 'mvp-status'], category: 'tech'
  },

  {
    content: `Divenamic Flight Search (SerpAPI Google Travel Explore):
- Endpoint: GET serpapi.com/search.json?engine=google_travel_explore
- Params: departure_id (airport code), arrival_id, type (1=roundtrip, 2=oneway), dates, currency EUR
- Response: destinations[] con name, country, gps, thumbnail, airport code, flight_price, hotel_price, duration, stops, airline
- Components: FlightDestinationCard, FlightOptionCard, FlightSummary, FlightSearchBar, DivingDestinationsWidget
- Regiones predefinidas: costaBrava, balearics, canarias, spain, mediterranean, redSea, egypt, caribbean, southeastAsia
- Dive sites hooks: useDiveSitesByBounds, useDiveSitesByRegion, useDiveSitesNearby, useMapDiveSites, useFilteredDiveSites`,
    source: 'divenamic-agents', tags: ['divenamic', 'flights', 'search'], category: 'tech'
  },

];

async function main() {
  const before = await stats();
  console.log(`\n🧠 SierraOS Brain — Knowledge Transfer`);
  console.log(`📊 Brain before: ${before.total || 0} notes`);
  console.log(`📦 Items to transfer: ${KNOWLEDGE.length}\n`);

  let ok = 0;
  let failed = 0;

  for (let i = 0; i < KNOWLEDGE.length; i++) {
    const item = KNOWLEDGE[i];
    const preview = item.content.slice(0, 60).replace(/\n/g, ' ');
    process.stdout.write(`  ${String(i + 1).padStart(2)}/${KNOWLEDGE.length} [${item.source}] ${preview}...`);

    const success = await ingest(item.content, item.source, item.tags, item.category);
    if (success) {
      ok++;
      console.log(' ✅');
    } else {
      failed++;
      console.log(' ❌');
    }
    await sleep(DELAY_MS);
  }

  const after = await stats();
  console.log(`\n${'═'.repeat(50)}`);
  console.log(`📊 Transferred: ${ok}/${KNOWLEDGE.length} (${failed} failed)`);
  console.log(`📊 Brain total: ${after.total || 0} notes`);
  console.log(`\nSources consolidated:`);
  console.log(`  • BRAIN.md (system, infra, LLMs)`);
  console.log(`  • projects.yaml (14 projects registry)`);
  console.log(`  • divenamic-ai/AGENTS.md (product, data sources, entities, chatbot, flights)`);
  console.log(`  • plinng-web/AGENTS.md (design system, architecture)`);
  console.log(`  • plinng-expo/AGENTS.md (mobile architecture)`);
  console.log(`  • PlanetMoji/AGENTS.md (component library)`);
  console.log(`  • Security audit (secrets locations)`);
  console.log(`  • Skills inventory (QA, Cursor, Codex)`);
  console.log(`  • Architecture rules (shared across projects)`);
  console.log(`  • Business & monetization plan`);
}

main().catch(console.error);
