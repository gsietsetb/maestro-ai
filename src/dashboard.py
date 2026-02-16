"""Unified Interactive Dashboard – Command Center for Cursor Orchestrator.

Single-page dashboard at /dashboard with:
- System health overview (uptime, services, channels)
- Active tasks and recent task history
- Agent mesh status (connected PCs)
- Project cards with git status
- Live event feed
- GitHub + Vercel deployment status
- INTERACTIVE: Launch tasks, send commands, control agents
- Auto-refresh via polling (every 5s)

API endpoints:
- GET  /api/state          – full system state
- GET  /api/events         – recent events
- GET  /api/projects       – all project states
- GET  /api/tasks          – active + recent tasks
- POST /api/tasks/launch   – launch a new task (interactive)
- GET  /api/registry       – available projects for task launching
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from src.orchestrator.intent_parser import ParsedIntent
from src.events import EventType

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Load Dashboard HTML from file ─────────────────────────────────────────────

_DASHBOARD_PATH = Path(__file__).parent / "static" / "dashboard.html"


def _load_dashboard_html() -> str:
    """Load dashboard HTML from file (cached after first load)."""
    try:
        return _DASHBOARD_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.error("Dashboard HTML not found at %s", _DASHBOARD_PATH)
        return "<h1>Dashboard not found</h1>"


# Legacy: keep as fallback
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sierra Bot – Command Center</title>
<style>
:root {
  --bg: #06060b;
  --surface: #0d0d14;
  --surface2: #14142a;
  --surface3: #1c1c38;
  --border: #252545;
  --border-hover: #3a3a6a;
  --text: #e4e4f0;
  --text2: #7878a0;
  --text3: #5050708;
  --accent: #6c5ce7;
  --accent-light: #a29bfe;
  --green: #00b894;
  --green-dim: #00b89433;
  --yellow: #fdcb6e;
  --yellow-dim: #fdcb6e33;
  --red: #e17055;
  --red-dim: #e1705533;
  --blue: #74b9ff;
  --blue-dim: #74b9ff33;
  --purple: #a29bfe;
  --purple-dim: #a29bfe33;
  --cyan: #00cec9;
  --orange: #e17055;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'SF Pro', 'Segoe UI', system-ui, sans-serif;
  background: var(--bg);
  color: var(--text);
  min-height: 100vh;
  -webkit-font-smoothing: antialiased;
}

/* ── Header ──────────────────────────────────────────────────── */
.header {
  background: linear-gradient(135deg, var(--surface) 0%, var(--surface2) 100%);
  border-bottom: 1px solid var(--border);
  padding: 0.8rem 1.5rem;
  display: flex;
  justify-content: space-between;
  align-items: center;
  position: sticky;
  top: 0;
  z-index: 100;
  backdrop-filter: blur(20px);
}
.header h1 { font-size: 1.1rem; font-weight: 700; letter-spacing: -0.02em; }
.header h1 span { color: var(--accent); }
.header-stats {
  display: flex;
  gap: 0.6rem;
  font-size: 0.72rem;
  color: var(--text2);
  align-items: center;
}
.stat-badge {
  background: var(--surface);
  padding: 0.25rem 0.6rem;
  border-radius: 16px;
  border: 1px solid var(--border);
  display: flex;
  align-items: center;
  gap: 0.3rem;
}
.stat-badge .val { color: var(--accent-light); font-weight: 700; }
.live-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--green);
  animation: pulse-dot 2s infinite;
  display: inline-block;
}
@keyframes pulse-dot {
  0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(0,184,148,0.7); }
  50% { opacity: 0.7; box-shadow: 0 0 0 4px rgba(0,184,148,0); }
}

/* ── Health Bar ──────────────────────────────────────────────── */
.health-bar {
  display: flex;
  gap: 0.5rem;
  padding: 0.5rem 1.5rem;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  overflow-x: auto;
  flex-wrap: wrap;
}
.health-chip {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.2rem 0.6rem;
  border-radius: 8px;
  font-size: 0.7rem;
  font-weight: 500;
  border: 1px solid var(--border);
  white-space: nowrap;
}
.health-chip.ok { background: var(--green-dim); color: var(--green); border-color: var(--green); }
.health-chip.warn { background: var(--yellow-dim); color: var(--yellow); border-color: var(--yellow); }
.health-chip.off { background: var(--red-dim); color: var(--red); border-color: var(--red); }
.health-chip .dot {
  width: 5px; height: 5px; border-radius: 50%;
  display: inline-block;
}
.health-chip.ok .dot { background: var(--green); }
.health-chip.warn .dot { background: var(--yellow); }
.health-chip.off .dot { background: var(--red); }

/* ── Main Layout ─────────────────────────────────────────────── */
.main {
  display: grid;
  grid-template-columns: 1fr 1fr 340px;
  gap: 1rem;
  padding: 1rem 1.5rem;
  min-height: calc(100vh - 100px);
}

/* ── Cards ────────────────────────────────────────────────────── */
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
}
.card-header {
  padding: 0.6rem 0.8rem;
  border-bottom: 1px solid var(--border);
  font-size: 0.72rem;
  font-weight: 700;
  color: var(--text2);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: var(--surface2);
}
.card-header .count { color: var(--accent-light); font-weight: 700; }
.card-body { padding: 0.8rem; }

/* ── Tasks Panel ─────────────────────────────────────────────── */
.tasks-section { display: flex; flex-direction: column; gap: 1rem; }

.task-item {
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.7rem;
  transition: border-color 0.2s;
  margin-bottom: 0.5rem;
}
.task-item:hover { border-color: var(--border-hover); }
.task-item:last-child { margin-bottom: 0; }

.task-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.3rem;
}
.task-project {
  font-weight: 700;
  font-size: 0.82rem;
  color: var(--purple);
}
.task-status {
  font-size: 0.65rem;
  padding: 0.15rem 0.5rem;
  border-radius: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.task-status.running { background: var(--blue-dim); color: var(--blue); }
.task-status.pending { background: var(--yellow-dim); color: var(--yellow); }
.task-status.completed { background: var(--green-dim); color: var(--green); }
.task-status.failed { background: var(--red-dim); color: var(--red); }

.task-action {
  font-size: 0.72rem;
  color: var(--text2);
  margin-bottom: 0.2rem;
}
.task-prompt {
  font-size: 0.75rem;
  color: var(--text);
  line-height: 1.4;
  max-height: 3.2em;
  overflow: hidden;
  text-overflow: ellipsis;
}
.task-time {
  font-size: 0.65rem;
  color: var(--text2);
  margin-top: 0.3rem;
}
.task-output {
  font-size: 0.7rem;
  color: var(--text2);
  background: var(--bg);
  padding: 0.4rem 0.5rem;
  border-radius: 6px;
  margin-top: 0.3rem;
  max-height: 80px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace;
}

.tasks-list {
  max-height: 600px;
  overflow-y: auto;
}

/* ── Projects Grid ───────────────────────────────────────────── */
.projects-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 0.6rem;
}
.project-card {
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.7rem;
  transition: border-color 0.2s;
}
.project-card:hover { border-color: var(--accent); }
.project-name {
  font-weight: 700;
  font-size: 0.85rem;
  display: flex;
  align-items: center;
  gap: 0.3rem;
  margin-bottom: 0.3rem;
}
.project-meta {
  font-size: 0.68rem;
  color: var(--text2);
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
}
.tag {
  background: var(--bg);
  padding: 0.1rem 0.4rem;
  border-radius: 5px;
  border: 1px solid var(--border);
}
.branch { color: var(--blue); }
.changes { color: var(--yellow); }
.clean { color: var(--green); }
.error { color: var(--red); }
.commit-msg {
  font-size: 0.68rem;
  color: var(--text2);
  margin-top: 0.2rem;
  font-style: italic;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ── Events Feed ─────────────────────────────────────────────── */
.events-list { max-height: 500px; overflow-y: auto; }
.event-item {
  padding: 0.5rem 0;
  border-bottom: 1px solid var(--border);
  display: flex;
  gap: 0.5rem;
  align-items: flex-start;
  font-size: 0.75rem;
}
.event-item:last-child { border-bottom: none; }
.event-icon { font-size: 1rem; flex-shrink: 0; width: 1.3rem; text-align: center; }
.event-content { flex: 1; min-width: 0; }
.event-project { font-weight: 700; color: var(--purple); }
.event-msg { color: var(--text); margin-top: 0.05rem; word-break: break-word; }
.event-time { font-size: 0.62rem; color: var(--text2); }
.event-url { font-size: 0.62rem; color: var(--blue); text-decoration: none; }
.event-url:hover { text-decoration: underline; }

/* ── Right Column ────────────────────────────────────────────── */
.right-col { display: flex; flex-direction: column; gap: 1rem; }

/* ── Mesh / Agents ───────────────────────────────────────────── */
.agent-card {
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.6rem;
  margin-bottom: 0.4rem;
}
.agent-card:last-child { margin-bottom: 0; }
.agent-name {
  font-weight: 700;
  font-size: 0.8rem;
  color: var(--cyan);
  display: flex;
  align-items: center;
  gap: 0.3rem;
}
.agent-meta {
  font-size: 0.68rem;
  color: var(--text2);
  margin-top: 0.2rem;
}
.agent-caps {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
  margin-top: 0.3rem;
}
.cap-tag {
  font-size: 0.6rem;
  padding: 0.08rem 0.35rem;
  border-radius: 4px;
  background: var(--accent);
  color: white;
  font-weight: 600;
}

/* ── Monitor Rows ────────────────────────────────────────────── */
.monitor-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.35rem 0;
  border-bottom: 1px solid var(--border);
  font-size: 0.75rem;
}
.monitor-row:last-child { border-bottom: none; }
.status-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  margin-right: 0.35rem;
}
.status-dot.on { background: var(--green); }
.status-dot.off { background: var(--red); }
.status-dot.warn { background: var(--yellow); }

/* ── Responsive ──────────────────────────────────────────────── */
@media (max-width: 1200px) {
  .main { grid-template-columns: 1fr 1fr; }
  .right-col { grid-column: 1 / -1; }
}
@media (max-width: 768px) {
  .main { grid-template-columns: 1fr; padding: 0.8rem; gap: 0.8rem; }
  .header { flex-direction: column; gap: 0.4rem; padding: 0.6rem 1rem; }
  .health-bar { padding: 0.4rem 1rem; }
}

/* ── Scrollbar ───────────────────────────────────────────────── */
::-webkit-scrollbar { width: 4px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }
::-webkit-scrollbar-thumb:hover { background: var(--border-hover); }

/* ── Utils ───────────────────────────────────────────────────── */
.empty { color: var(--text2); font-size: 0.75rem; padding: 0.8rem; text-align: center; }
.uptime { font-family: 'SF Mono', Monaco, monospace; font-size: 0.7rem; color: var(--green); }

/* ── Command Bar ─────────────────────────────────────────────── */
.cmd-bar {
  display: flex;
  gap: 0.5rem;
  padding: 0.6rem 1.5rem;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  align-items: center;
}
.cmd-select {
  background: var(--surface2);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.5rem 0.6rem;
  font-size: 0.75rem;
  min-width: 160px;
  outline: none;
  cursor: pointer;
}
.cmd-select:focus { border-color: var(--accent); }
.cmd-select option { background: var(--surface2); color: var(--text); }
.cmd-input {
  flex: 1;
  background: var(--surface2);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.5rem 0.8rem;
  font-size: 0.8rem;
  outline: none;
  font-family: inherit;
}
.cmd-input:focus { border-color: var(--accent); box-shadow: 0 0 0 2px rgba(108,92,231,0.2); }
.cmd-input::placeholder { color: var(--text2); }
.cmd-btn {
  background: var(--accent);
  color: white;
  border: none;
  border-radius: 8px;
  padding: 0.5rem 1.2rem;
  font-size: 0.8rem;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}
.cmd-btn:hover { background: var(--accent-light); transform: translateY(-1px); }
.cmd-btn:active { transform: translateY(0); }
.cmd-btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
.cmd-feedback {
  padding: 0 1.5rem;
  font-size: 0.72rem;
  min-height: 0;
  transition: all 0.3s;
  overflow: hidden;
}
.cmd-feedback.show {
  padding: 0.4rem 1.5rem;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
}
.cmd-feedback.success { color: var(--green); }
.cmd-feedback.error { color: var(--red); }
.cmd-feedback.info { color: var(--blue); }
</style>
</head>
<body>

<!-- ── Header ────────────────────────────────────────────────── -->
<div class="header">
  <h1><span>Sierra</span>Bot – Command Center</h1>
  <div class="header-stats">
    <span class="stat-badge"><span class="live-dot"></span> LIVE</span>
    <span class="stat-badge">Uptime: <span class="val uptime" id="h-uptime">-</span></span>
    <span class="stat-badge">Tasks: <span class="val" id="h-tasks">-</span></span>
    <span class="stat-badge">Projects: <span class="val" id="h-projects">-</span></span>
    <span class="stat-badge">Events: <span class="val" id="h-events">-</span></span>
    <span class="stat-badge">Mesh: <span class="val" id="h-mesh">-</span></span>
  </div>
</div>

<!-- ── Health Bar ────────────────────────────────────────────── -->
<div class="health-bar" id="health-bar"></div>

<!-- ── Command Bar ────────────────────────────────────────────── -->
<div class="cmd-bar">
  <select id="cmd-project" class="cmd-select">
    <option value="">Auto-detect project</option>
  </select>
  <input id="cmd-input" class="cmd-input" type="text"
    placeholder="Escribe un comando... (ej: 'Investiga Apple Watch companion app para controlar el orchestrator')"
    autocomplete="off">
  <button id="cmd-send" class="cmd-btn" onclick="launchTask()">Launch</button>
</div>
<div class="cmd-feedback" id="cmd-feedback"></div>

<!-- ── Main Layout ───────────────────────────────────────────── -->
<div class="main">

  <!-- Col 1: Tasks -->
  <div class="tasks-section">
    <div class="card">
      <div class="card-header">
        Active Tasks
        <span class="count" id="active-count">0</span>
      </div>
      <div class="card-body">
        <div class="tasks-list" id="active-tasks"></div>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        Recent Tasks
        <span class="count" id="recent-count">0</span>
      </div>
      <div class="card-body">
        <div class="tasks-list" id="recent-tasks"></div>
      </div>
    </div>
  </div>

  <!-- Col 2: Projects + Events -->
  <div style="display:flex;flex-direction:column;gap:1rem;">
    <div class="card">
      <div class="card-header">
        Projects
        <span class="count" id="projects-count">0</span>
      </div>
      <div class="card-body">
        <div class="projects-grid" id="projects-grid"></div>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        Event Feed
        <span class="count" id="events-count">0</span>
      </div>
      <div class="card-body">
        <div class="events-list" id="events-list"></div>
      </div>
    </div>
  </div>

  <!-- Col 3: System + Agents + Deploys + Notifs -->
  <div class="right-col">
    <div class="card">
      <div class="card-header">Agent Mesh</div>
      <div class="card-body" id="mesh-panel"></div>
    </div>

    <div class="card">
      <div class="card-header">System Status</div>
      <div class="card-body" id="system-status"></div>
    </div>

    <div class="card">
      <div class="card-header">Recent Deploys</div>
      <div class="card-body" id="deploys-list"></div>
    </div>

    <div class="card">
      <div class="card-header">Notifications</div>
      <div class="card-body" id="notif-status"></div>
    </div>
  </div>

</div>

<script>
const API = '/api';

function formatUptime(seconds) {
  if (!seconds || seconds < 0) return '-';
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (d > 0) return d + 'd ' + h + 'h ' + m + 'm';
  if (h > 0) return h + 'h ' + m + 'm ' + s + 's';
  if (m > 0) return m + 'm ' + s + 's';
  return s + 's';
}

function timeAgo(isoStr) {
  if (!isoStr) return '';
  const d = new Date(isoStr);
  const now = new Date();
  const diff = Math.floor((now - d) / 1000);
  if (diff < 60) return 'ahora';
  if (diff < 3600) return Math.floor(diff/60) + 'm';
  if (diff < 86400) return Math.floor(diff/3600) + 'h';
  return Math.floor(diff/86400) + 'd';
}

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
}

async function refresh() {
  try {
    const [stateRes, eventsRes, tasksRes] = await Promise.all([
      fetch(API + '/state'),
      fetch(API + '/events?limit=50'),
      fetch(API + '/tasks'),
    ]);
    const state = await stateRes.json();
    const events = await eventsRes.json();
    const tasks = await tasksRes.json();

    renderHeader(state, tasks);
    renderHealthBar(state);
    renderTasks(tasks);
    renderProjects(state.projects || []);
    renderEvents(events.events || []);
    renderMesh(state);
    renderSystem(state);
    renderDeploys(events.events || []);
    renderNotifications(state);
  } catch (e) {
    console.error('Refresh error:', e);
  }
}

function renderHeader(state, tasks) {
  document.getElementById('h-uptime').textContent = formatUptime(state.uptime || 0);
  document.getElementById('h-tasks').textContent = (tasks.active || []).length + ' active';
  document.getElementById('h-projects').textContent = (state.projects || []).length;
  document.getElementById('h-events').textContent = state.event_count || 0;
  const mesh = state.mesh || {};
  document.getElementById('h-mesh').textContent = (mesh.connected || 0) + ' agents';
}

function renderHealthBar(state) {
  const el = document.getElementById('health-bar');
  const m = state.monitors || {};
  const ch = state.channels || {};
  const mesh = state.mesh || {};

  const chips = [
    { name: 'Server', ok: true },
    { name: 'Telegram', ok: ch.telegram },
    { name: 'WhatsApp', ok: ch.whatsapp },
    { name: 'GitHub', ok: m.github?.running },
    { name: 'Vercel', ok: m.vercel?.running },
    { name: 'Projects', ok: m.projects?.running },
    { name: 'Mesh', ok: (mesh.connected || 0) > 0 },
    { name: 'Cursor API', ok: state.cursor_enabled },
    { name: 'Domotica', ok: state.domotica_enabled },
  ];

  el.innerHTML = chips.map(c => {
    const cls = c.ok ? 'ok' : 'off';
    return '<span class="health-chip ' + cls + '"><span class="dot"></span>' + c.name + '</span>';
  }).join('');
}

function renderTasks(tasks) {
  // Active tasks
  const activeEl = document.getElementById('active-tasks');
  const active = tasks.active || [];
  document.getElementById('active-count').textContent = active.length;

  if (!active.length) {
    activeEl.innerHTML = '<div class="empty">Sin tareas activas</div>';
  } else {
    activeEl.innerHTML = active.map(renderTaskItem).join('');
  }

  // Recent tasks
  const recentEl = document.getElementById('recent-tasks');
  const recent = tasks.recent || [];
  document.getElementById('recent-count').textContent = recent.length;

  if (!recent.length) {
    recentEl.innerHTML = '<div class="empty">Sin historial</div>';
  } else {
    recentEl.innerHTML = recent.map(renderTaskItem).join('');
  }
}

function renderTaskItem(t) {
  const statusCls = t.status === 'completed' ? 'completed' :
                    t.status === 'failed' ? 'failed' :
                    t.status === 'pending' ? 'pending' : 'running';
  const icon = t.status === 'completed' ? (t.success ? '&#10003;' : '&#10007;') :
               t.status === 'failed' ? '&#10007;' :
               t.status === 'pending' ? '&#9711;' : '&#9881;';
  const ago = timeAgo(t.created_at);
  const output = t.output ? '<div class="task-output">' + esc(t.output.substring(0, 300)) + '</div>' : '';

  return '<div class="task-item">' +
    '<div class="task-header">' +
      '<span class="task-project">' + esc(t.project || 'global') + '</span>' +
      '<span class="task-status ' + statusCls + '">' + icon + ' ' + esc(t.status) + '</span>' +
    '</div>' +
    '<div class="task-action">' + esc(t.action || '') + ' &middot; ' + ago + '</div>' +
    (t.prompt ? '<div class="task-prompt">' + esc(t.prompt.substring(0, 150)) + '</div>' : '') +
    output +
    (t.completed_at ? '<div class="task-time">Completado: ' + new Date(t.completed_at).toLocaleString('es-ES') + '</div>' : '') +
  '</div>';
}

function renderProjects(projects) {
  const grid = document.getElementById('projects-grid');
  document.getElementById('projects-count').textContent = projects.length + ' repos';

  if (!projects.length) {
    grid.innerHTML = '<div class="empty">Sin proyectos escaneados</div>';
    return;
  }

  grid.innerHTML = projects.map(p => {
    const statusClass = p.error ? 'error' : p.has_uncommitted ? 'changes' : 'clean';
    const statusText = p.error ? 'Error' : p.has_uncommitted ? p.uncommitted_count + ' cambios' : 'Clean';
    const emoji = p.error ? '&#10067;' : p.has_uncommitted ? '&#128993;' : p.ahead > 0 ? '&#128309;' : '&#128994;';

    return '<div class="project-card">' +
      '<div class="project-name">' + emoji + ' ' + esc(p.name) + '</div>' +
      '<div class="project-meta">' +
        (p.branch ? '<span class="tag branch">&#9096; ' + esc(p.branch) + '</span>' : '') +
        '<span class="tag ' + statusClass + '">' + statusText + '</span>' +
        (p.stack ? '<span class="tag">' + esc(p.stack) + '</span>' : '') +
        (p.ahead > 0 ? '<span class="tag">&uarr;' + p.ahead + '</span>' : '') +
        (p.behind > 0 ? '<span class="tag">&darr;' + p.behind + '</span>' : '') +
      '</div>' +
      (p.last_commit_msg ? '<div class="commit-msg">' + esc(p.last_commit_msg.substring(0,50)) + '</div>' : '') +
    '</div>';
  }).join('');
}

function renderEvents(events) {
  const list = document.getElementById('events-list');
  document.getElementById('events-count').textContent = events.length;

  if (!events.length) {
    list.innerHTML = '<div class="empty">Esperando actividad...</div>';
    return;
  }

  list.innerHTML = events.slice().reverse().slice(0, 30).map(e => {
    const time = new Date(e.dt).toLocaleTimeString('es-ES', {hour:'2-digit',minute:'2-digit'});
    const url = e.metadata?.url || '';
    return '<div class="event-item">' +
      '<span class="event-icon">' + (e.icon || '&#128204;') + '</span>' +
      '<div class="event-content">' +
        '<span class="event-project">' + esc(e.project) + '</span> ' +
        '<span class="event-time">' + time + '</span>' +
        '<div class="event-msg">' + esc(e.message) + '</div>' +
        (url ? '<a class="event-url" href="' + esc(url) + '" target="_blank">Ver &rarr;</a>' : '') +
      '</div>' +
    '</div>';
  }).join('');
}

function renderMesh(state) {
  const el = document.getElementById('mesh-panel');
  const mesh = state.mesh || {};
  const agents = mesh.agents || [];

  if (!agents.length) {
    el.innerHTML = '<div class="empty">Sin agentes conectados</div>';
    return;
  }

  el.innerHTML = agents.map(a => {
    const caps = (a.capabilities || []).slice(0, 8);
    return '<div class="agent-card">' +
      '<div class="agent-name"><span class="status-dot on"></span>' + esc(a.name || a.hostname || 'agent') + '</div>' +
      '<div class="agent-meta">' +
        esc(a.os || '') + ' &middot; ' +
        (a.projects_count || 0) + ' projects &middot; ' +
        'max ' + (a.max_tasks || '?') + ' tasks' +
      '</div>' +
      (caps.length ? '<div class="agent-caps">' + caps.map(c =>
        '<span class="cap-tag">' + esc(c) + '</span>'
      ).join('') + '</div>' : '') +
    '</div>';
  }).join('');
}

function renderSystem(state) {
  const el = document.getElementById('system-status');
  const m = state.monitors || {};
  const gh = m.github || {};
  const vc = m.vercel || {};
  const pm = m.projects || {};
  const ch = state.channels || {};

  el.innerHTML =
    monitorRow('Telegram', ch.telegram, '') +
    monitorRow('WhatsApp', ch.whatsapp, '') +
    monitorRow('GitHub Monitor', gh.running, gh.repos_tracked ? gh.repos_tracked + ' repos' : '') +
    monitorRow('Vercel Monitor', vc.running, vc.vercel_projects ? vc.vercel_projects + ' projects' : '') +
    monitorRow('Project Scanner', pm.running, pm.total ? pm.total + ' projects' : '') +
    monitorRow('Agent Mesh', (state.mesh?.connected || 0) > 0, state.mesh?.connected ? state.mesh.connected + ' agents' : '') +
    monitorRow('Cursor API', state.cursor_enabled, '') +
    monitorRow('Domotica', state.domotica_enabled, '');
}

function monitorRow(name, active, detail) {
  const cls = active ? 'on' : 'off';
  return '<div class="monitor-row">' +
    '<span><span class="status-dot ' + cls + '"></span>' + name + '</span>' +
    '<span style="color:var(--text2)">' + (detail || (active ? 'Active' : 'Off')) + '</span>' +
  '</div>';
}

function renderDeploys(events) {
  const deploys = events.filter(e => e.type && e.type.startsWith('deploy_'));
  const el = document.getElementById('deploys-list');
  if (!deploys.length) {
    el.innerHTML = '<div class="empty">Sin deployments</div>';
    return;
  }

  el.innerHTML = deploys.slice(-8).reverse().map(e => {
    const time = new Date(e.dt).toLocaleTimeString('es-ES', {hour:'2-digit',minute:'2-digit'});
    const url = e.metadata?.url || '';
    return '<div class="monitor-row">' +
      '<span>' + (e.icon || '&#128640;') + ' ' + esc(e.project) + '</span>' +
      '<span style="color:var(--text2)">' + time +
      (url ? ' <a class="event-url" href="' + esc(url) + '" target="_blank">&nearr;</a>' : '') +
      '</span>' +
    '</div>';
  }).join('');
}

function renderNotifications(state) {
  const el = document.getElementById('notif-status');
  const n = state.notifications || {};
  el.innerHTML =
    monitorRow('Enabled', n.enabled, '') +
    monitorRow('Telegram', n.telegram_configured, n.chat_id ? 'Chat ' + n.chat_id : 'No chat ID') +
    monitorRow('WhatsApp', n.whatsapp_configured, n.wa_number ? '+' + n.wa_number : '');
}

// ── Command Bar ──────────────────────────────────────────────

async function loadProjects() {
  try {
    const res = await fetch(API + '/registry');
    const data = await res.json();
    const sel = document.getElementById('cmd-project');
    (data.projects || []).forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.name;
      opt.textContent = p.name + (p.stack ? ' (' + p.stack + ')' : '');
      sel.appendChild(opt);
    });
  } catch (e) { console.error('Load projects error:', e); }
}

async function launchTask() {
  const input = document.getElementById('cmd-input');
  const project = document.getElementById('cmd-project').value;
  const btn = document.getElementById('cmd-send');
  const feedback = document.getElementById('cmd-feedback');
  const msg = input.value.trim();

  if (!msg) { input.focus(); return; }

  btn.disabled = true;
  btn.textContent = 'Launching...';
  feedback.className = 'cmd-feedback show info';
  feedback.textContent = 'Parsing intent and launching task...';

  try {
    const body = { message: msg };
    if (project) body.project = project;

    const res = await fetch(API + '/tasks/launch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();

    if (res.ok) {
      feedback.className = 'cmd-feedback show success';
      feedback.textContent = 'Task launched: ' + data.action + ' on ' + data.project + ' (ID: ' + data.task_id.substring(0,8) + ')';
      input.value = '';
      setTimeout(refresh, 1000);
    } else {
      feedback.className = 'cmd-feedback show error';
      feedback.textContent = 'Error: ' + (data.error || data.detail || 'Unknown error');
    }
  } catch (e) {
    feedback.className = 'cmd-feedback show error';
    feedback.textContent = 'Network error: ' + e.message;
  }

  btn.disabled = false;
  btn.textContent = 'Launch';
  setTimeout(() => { feedback.className = 'cmd-feedback'; }, 8000);
}

// Enter key to launch
document.getElementById('cmd-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); launchTask(); }
});

// ── Auto-refresh ─────────────────────────────────────────────
loadProjects();
refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>"""


# ── API Routes ────────────────────────────────────────────────────────────────


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page():
    """Serve the visual flow dashboard."""
    return _load_dashboard_html()


@router.get("/api/state")
async def api_state(request: Request):
    """Full system state for dashboard."""
    components = request.app.state.components
    event_bus = components.get("event_bus")
    notifier = components.get("notifier")
    github_monitor = components.get("github_monitor")
    vercel_monitor = components.get("vercel_monitor")
    project_monitor = components.get("project_monitor")

    # Project states
    projects = []
    if project_monitor:
        projects = project_monitor.all_states()

    # Monitor states
    monitors = {}
    if github_monitor:
        monitors["github"] = github_monitor.status()
    if vercel_monitor:
        monitors["vercel"] = vercel_monitor.status()
    if project_monitor:
        monitors["projects"] = project_monitor.status_summary()

    # Mesh status
    mesh = components.get("agent_mesh")
    mesh_status = mesh.status_summary() if mesh else {}

    # Channels
    channels = {
        "telegram": bool(getattr(request.app.state, "tg_app", None)),
        "whatsapp": bool(getattr(request.app.state, "wa_handler", None))
        or bool(getattr(request.app.state, "wa_bridge", None)),
    }

    # Notification status
    notif_status = {}
    if notifier:
        notif_status = {
            "enabled": notifier._enabled,
            "telegram_configured": notifier.telegram_configured,
            "whatsapp_configured": notifier.whatsapp_configured,
            "chat_id": notifier._tg_chat_id,
            "wa_number": notifier._wa_number,
        }

    return {
        "projects": projects,
        "monitors": monitors,
        "mesh": mesh_status,
        "channels": channels,
        "notifications": notif_status,
        "event_count": len(event_bus.store.recent(limit=9999)) if event_bus else 0,
        "cursor_enabled": bool(components.get("cursor_executor")),
        "domotica_enabled": bool(components.get("ha_executor")),
        "uptime": int(time.time() - request.app.state.start_time)
        if hasattr(request.app.state, "start_time") else 0,
    }


@router.get("/api/events")
async def api_events(request: Request, limit: int = 50, project: str | None = None):
    """Recent events for the dashboard feed."""
    event_bus = request.app.state.components.get("event_bus")
    if not event_bus:
        return {"events": []}

    events = event_bus.store.recent(limit=limit, project=project)
    return {
        "events": [e.to_dict() for e in events],
        "total": len(events),
    }


@router.get("/api/projects")
async def api_projects(request: Request):
    """All project states."""
    pm = request.app.state.components.get("project_monitor")
    if not pm:
        return {"projects": []}
    return {"projects": pm.all_states()}


@router.get("/api/tasks")
async def api_tasks(request: Request, limit: int = 30):
    """Active and recent tasks for the dashboard."""
    tracker = request.app.state.components.get("tracker")
    if not tracker:
        return {"active": [], "recent": []}

    try:
        active = await tracker.list_active()
        recent = await tracker.list_recent(limit=limit)
    except Exception as e:
        logger.warning("Error fetching tasks: %s", e)
        return {"active": [], "recent": []}

    return {
        "active": active,
        "recent": [t for t in recent if t.get("status") in ("completed", "failed", "cancelled")],
    }


# ── Interactive Task Launch ───────────────────────────────────────────────────


DASHBOARD_PASSWORD = "rotor"


class TaskLaunchRequest(BaseModel):
    """Request body for launching a task from the dashboard."""
    message: str
    project: str | None = None
    password: str | None = None


async def _execute_task_bg(
    components: dict,
    task_id: str,
    intent: ParsedIntent,
    project_label: str,
) -> None:
    """Background coroutine to execute a task and update tracker."""
    router_obj = components["router"]
    tracker = components["tracker"]
    event_bus = components.get("event_bus")

    try:
        result = await router_obj.route(intent, task_id)
        await tracker.complete(task_id, success=result.success, output=result.output)

        if event_bus:
            etype = EventType.TASK_COMPLETED if result.success else EventType.TASK_FAILED
            await event_bus.emit(
                etype,
                project=project_label,
                message=f"Dashboard task {intent.action}: {'OK' if result.success else 'FAILED'}",
                source="dashboard",
                task_id=task_id,
                action=intent.action,
                pr_url=getattr(result, "pr_url", "") or "",
            )

        # Notify via Telegram
        notifier = components.get("notifier")
        if notifier:
            status_emoji = "OK" if result.success else "FAILED"
            summary = (result.output or "")[:300]
            pr_info = f"\nPR: {result.pr_url}" if getattr(result, "pr_url", None) else ""
            await notifier.send_telegram(
                f"Dashboard Task {status_emoji}\n"
                f"Project: {project_label}\n"
                f"Action: {intent.action}\n"
                f"{summary}{pr_info}"
            )
    except Exception as e:
        logger.exception("Background task %s failed", task_id)
        await tracker.complete(task_id, success=False, output=str(e))


@router.post("/api/tasks/launch")
async def api_launch_task(request: Request, body: TaskLaunchRequest):
    """Launch a new task from the dashboard – requires password."""
    if not body.password or body.password != DASHBOARD_PASSWORD:
        return JSONResponse(
            status_code=403,
            content={"error": "Password incorrecto. Acceso denegado."},
        )

    components = request.app.state.components
    parser = components["parser"]
    registry = components["registry"]
    tracker = components["tracker"]
    event_bus = components.get("event_bus")

    # Parse intent
    try:
        intent = await parser.parse(
            body.message,
            registry.project_names(),
        )
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"error": f"Intent parsing failed: {e}"},
        )

    # Override project if specified
    if body.project:
        intent.project = body.project

    project_label = intent.project or "global"

    # Create task
    task_id = str(uuid.uuid4())
    await tracker.create(task_id, intent, status="running")

    # Emit event
    if event_bus:
        await event_bus.emit(
            EventType.TASK_STARTED,
            project=project_label,
            message=f"Dashboard: {intent.action} – {(intent.prompt or body.message)[:80]}",
            source="dashboard",
            task_id=task_id,
            action=intent.action,
        )

    # Execute in background (don't block the response)
    asyncio.create_task(
        _execute_task_bg(components, task_id, intent, project_label)
    )

    return {
        "task_id": task_id,
        "action": intent.action,
        "project": project_label,
        "status": "running",
        "message": f"Task launched: {intent.action} on {project_label}",
    }


@router.get("/api/registry")
async def api_registry(request: Request):
    """Available projects for task launching."""
    registry = request.app.state.components.get("registry")
    if not registry:
        return {"projects": []}

    all_projects = registry.all_projects()
    return {
        "projects": [
            {
                "name": name,
                "repo": info.get("repo", ""),
                "stack": info.get("stack", ""),
                "path": info.get("path", ""),
            }
            for name, info in all_projects.items()
        ]
    }


# ── Ollama Local LLM ─────────────────────────────────────────────────────────


@router.get("/api/ollama/status")
async def api_ollama_status():
    """Check Ollama status and available models."""
    import httpx

    from src.config import get_settings
    s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"{s.ollama_url}/api/tags")
            data = r.json()
            models = [m["name"] for m in data.get("models", [])]
            return {"online": True, "url": s.ollama_url, "models": models, "default": s.ollama_model}
    except Exception:
        return {"online": False, "url": s.ollama_url, "models": [], "default": s.ollama_model}


@router.post("/api/ollama/chat")
async def api_ollama_chat(request: Request, body: dict):
    """Chat with local Ollama model (no password needed – local only)."""
    import httpx

    from src.config import get_settings
    s = get_settings()
    msg = body.get("message", "")
    model = body.get("model", s.ollama_model)
    if not msg:
        return {"error": "No message"}
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(f"{s.ollama_url}/api/generate", json={
                "model": model, "prompt": msg, "stream": False,
                "options": {"num_predict": 512},
            })
            data = r.json()
            resp = data.get("response", "")
            thinking = data.get("thinking", "")
            return {"response": resp, "thinking": thinking[:200] if thinking else "", "model": model}
    except Exception as e:
        return {"error": str(e)}


# ── Feedback ──────────────────────────────────────────────────────────────────

_feedback_store: list[dict] = []


class FeedbackRequest(BaseModel):
    task_id: str = ""
    rating: str  # positive, negative, suggestion
    comment: str = ""


@router.post("/api/feedback")
async def api_feedback(body: FeedbackRequest):
    """Submit feedback on a task or the system."""
    _feedback_store.append({
        "task_id": body.task_id,
        "rating": body.rating,
        "comment": body.comment,
        "ts": time.time(),
    })
    return {"ok": True, "total": len(_feedback_store)}


@router.get("/api/feedback")
async def api_get_feedback():
    """Get all feedback."""
    return {"feedback": _feedback_store[-50:], "total": len(_feedback_store)}
