/**
 * SierraOS Brain — Knowledge Seed
 * Seeds the Brain with base knowledge about the ecosystem.
 * Usage: npx tsx scripts/seed-brain.ts
 */
import { ingest, stats, sleep, DELAY_MS } from './config';

const KNOWLEDGE = [
  { content: 'Guille Sierra - Full-stack developer, Barcelona. 30 proyectos activos. Tech: React, TypeScript, Swift, Python, Cloudflare Workers, Supabase, Docker. Hobbies: buceo, musica. GitHub: gsietsetb.', tags: ['profile', 'about'], cat: 'personal' },
  { content: 'SierraOS es el sistema operativo personal de Guille Sierra. Incluye: Brain (memoria persistente con pgvector), Orchestrator (12 agentes), Home Server (Docker 24/7), Dashboard (sierrabot.pages.dev), Telegram Bot (@divenamicBot), y scripts de importacion (Obsidian, ChatGPT, CLI).', tags: ['sierraos', 'architecture'], cat: 'tech' },
  { content: 'Infraestructura: Cloudflare Workers (API), Cloudflare Pages (dashboards), Vercel (frontends), Supabase (2 proyectos: divenamic + planetmoji), Stripe (pagos), Docker Home Server (baileys-bridge, sierraos-brain, cloudflared tunnel).', tags: ['infra', 'servers'], cat: 'tech' },
  { content: 'Proyectos LIVE: Divenamic (buceo), DGFolio (portfolio), PlanetMoji (juegos emoji), TruesDate (dating), Cantafy (singing AI), Psyche (biometrics), GitStory (git visualization), Colexalia (game prices), Plinng (social discovery), Softwait (loading screens).', tags: ['projects', 'live'], cat: 'business' },
  { content: 'Orchestrator tiene 12 agentes: router, watch, home, scraper, content, music, biometric, researcher, enricher, notifier, brain, qa. Comandos: /buscar, /deploy, /status, /brain, /note, /idea, /qa, /infra, /billing.', tags: ['agents', 'orchestrator'], cat: 'tech' },
  { content: 'Brain API endpoints: POST /api/brain/ingest (crear nota), GET /api/brain/notes (listar), GET /api/brain/search?q= (buscar), GET /api/brain/stats (estadisticas). Auth: Bearer sierraos_brain_2026. Base: divenamic-api.divenamic.workers.dev', tags: ['brain', 'api'], cat: 'tech' },
  { content: 'Dashboard SierraBot: sierrabot.pages.dev. Tabs: Overview, Roadmap, Compare, Chatbot, Ollama, Deploys, Projects, Brain, Feedback, Events. Conectar con ?api=https://divenamic-api.divenamic.workers.dev', tags: ['dashboard', 'sierrabot'], cat: 'tech' },
  { content: 'Plan monetizacion: 1) Divenamic (comisiones buceo via Stripe), 2) TruesDate (freemium dating, expansion Asia con Carlos Marina Bay), 3) Cantafy (music learning), 4) PlanetMoji (in-app purchases), 5) Softwait (paid themes), 6) Plinng (promoted events).', tags: ['monetization', 'revenue'], cat: 'business' },
];

async function main() {
  const before = await stats();
  console.log(`\n🧠 SierraOS Brain Seed`);
  console.log(`📊 Before: ${before.total || 0} notes\n`);

  let ok = 0;
  for (const item of KNOWLEDGE) {
    const success = await ingest(item.content, 'seed', item.tags, item.cat);
    if (success) ok++;
    else console.log(`  ❌ Failed: ${item.tags[0]}`);
    await sleep(DELAY_MS);
  }

  const after = await stats();
  console.log(`\n📊 Seeded: ${ok}/${KNOWLEDGE.length}`);
  console.log(`📊 Brain after: ${after.total || 0} notes`);
}

main().catch(console.error);
