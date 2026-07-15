'use strict';

// ---- elements ----
const hud = document.getElementById('hud');
const backdrop = document.getElementById('backdrop');
const dock = document.getElementById('dock');
const panelTitle = document.getElementById('panel-title');
const panelFrontier = document.getElementById('panel-frontier');
const ctxLoc = document.getElementById('ctx-loc');
const ctxDeaths = document.getElementById('ctx-deaths');
const ctxChars = document.getElementById('ctx-chars');
const link = document.getElementById('link');
const timelineEl = document.getElementById('timeline');
const q = document.getElementById('q');
const send = document.getElementById('send');
const loreAnswer = document.getElementById('lore-answer');
const codexList = document.getElementById('codex-list');
const codexAnswer = document.getElementById('codex-answer');
const itemRead = document.getElementById('item-read');
const itemText = document.getElementById('item-text');

const MODULES = ['lore', 'story', 'trade', 'assist', 'codex', 'item'];
const TITLES = { lore: 'Lore', story: 'Story', trade: 'Trade', assist: 'Assistente', codex: 'Codex', item: 'Item Check' };

let ws = null, wsUrl = null, model = 'sonnet';
let openMod = null;
let streaming = false;
let streamTarget = null; // element receiving chunks
let characters = [];

// ---- init ----
async function init() {
  const cfg = await window.companion.getConfig();
  model = cfg.model || 'sonnet';
  wsUrl = cfg.serviceUrl.replace(/^http/, 'ws') + '/ws';
  connect();
  window.companion.onShown(() => { refresh(); if (openMod === 'lore') q.focus(); });
}

function connect() {
  try { ws = new WebSocket(wsUrl); } catch { setLink(false); return; }
  ws.onopen = () => { setLink(true); refresh(); };
  ws.onclose = () => { setLink(false); setTimeout(connect, 1500); };
  ws.onerror = () => {};
  ws.onmessage = (ev) => handle(ev.data);
}

function refresh() {
  sendMsg({ type: 'context' });
  sendMsg({ type: 'state' });
  sendMsg({ type: 'timeline' });
}

function sendMsg(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

function setLink(on) {
  link.textContent = on ? 'LINK ●' : 'LINK ○';
  link.style.color = on ? 'var(--cyan)' : 'var(--ink-dim)';
}

// ---- incoming ----
function handle(raw) {
  let m; try { m = JSON.parse(raw); } catch { return; }
  switch (m.type) {
    case 'context': renderContext(m); break;
    case 'state': characters = m.characters || []; if (openMod === 'codex') renderCodexList(); break;
    case 'timeline': renderTimeline(m.entries || []); break;
    case 'start':
      streaming = true; lockInput(true);
      if (panelFrontier) panelFrontier.textContent = m.frontier ? `▸ ${m.frontier}` : '';
      if (streamTarget) { streamTarget.className = 'answer caret'; streamTarget.textContent = ''; }
      break;
    case 'chunk':
      if (streamTarget) { streamTarget.textContent += m.text || ''; streamTarget.scrollTop = streamTarget.scrollHeight; }
      break;
    case 'done':
      streaming = false; lockInput(false);
      if (streamTarget) streamTarget.className = 'answer';
      break;
    case 'error':
      streaming = false; lockInput(false);
      if (streamTarget) { streamTarget.className = 'answer error'; streamTarget.textContent = '⚠ ' + (m.message || 'erro'); }
      break;
  }
}

function renderContext(m) {
  ctxLoc.textContent = m.location || '—';
  ctxDeaths.textContent = m.recentDeaths > 0 ? `☠ ${m.recentDeaths} recente(s)` : '';
  const chars = m.recentCharacters || [];
  ctxChars.textContent = chars.length ? `· viu ${chars.join(', ')}` : '';
}

const TL_ICONS = { act: '▸', endgame: '◈', death: '☠', beat: '“', level: '⬆' };
function renderTimeline(entries) {
  timelineEl.innerHTML = '';
  for (const e of entries) {
    const row = document.createElement('div');
    row.className = `tl-row ${e.kind}`;
    const ico = document.createElement('span');
    ico.className = `tl-ico ${e.kind}`; ico.textContent = TL_ICONS[e.kind] || '·';
    const txt = document.createElement('span');
    txt.className = 'tl-text'; txt.textContent = e.text;
    const loc = document.createElement('span');
    loc.className = 'tl-loc'; loc.textContent = e.location || '';
    row.append(ico, txt, loc);
    timelineEl.appendChild(row);
  }
  timelineEl.scrollTop = timelineEl.scrollHeight;
}

function renderCodexList() {
  codexList.innerHTML = '';
  if (!characters.length) {
    const hint = document.createElement('span');
    hint.className = 'hint'; hint.textContent = 'Nenhum personagem registrado ainda.';
    codexList.appendChild(hint);
    return;
  }
  for (const name of characters) {
    const chip = document.createElement('button');
    chip.className = 'chip'; chip.textContent = name;
    chip.addEventListener('click', () => {
      document.querySelectorAll('.chip').forEach((c) => c.classList.remove('active'));
      chip.classList.add('active');
      askInto(codexAnswer, `Quem é ${name}? Responda em até 3 frases, sem spoiler além do meu ponto atual.`);
    });
    codexList.appendChild(chip);
  }
}

// ---- asking ----
function askInto(target, question) {
  if (streaming) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    target.className = 'answer error';
    target.textContent = '⚠ serviço offline.';
    return;
  }
  streamTarget = target;
  sendMsg({ type: 'ask', question, model });
}

function lockInput(locked) { q.disabled = locked; send.disabled = locked; }

// ---- module navigation ----
function openModule(mod) {
  openMod = mod;
  panelTitle.textContent = TITLES[mod] || mod;
  panelFrontier.textContent = '';
  MODULES.forEach((x) => { const v = document.getElementById('m-' + x); if (v) v.hidden = x !== mod; });
  document.querySelectorAll('.dock-btn').forEach((b) => b.classList.toggle('active', b.dataset.mod === mod));
  hud.classList.add('panel-open');
  if (mod === 'lore') { sendMsg({ type: 'timeline' }); q.focus(); }
  else if (mod === 'codex') { sendMsg({ type: 'state' }); renderCodexList(); }
}

function closePanel() {
  openMod = null;
  hud.classList.remove('panel-open');
  document.querySelectorAll('.dock-btn').forEach((b) => b.classList.remove('active'));
}

// ---- events ----
dock.addEventListener('click', (e) => {
  const btn = e.target.closest('.dock-btn');
  if (!btn) return;
  const mod = btn.dataset.mod;
  if (openMod === mod) closePanel(); else openModule(mod);
});

document.getElementById('panel-close').addEventListener('click', closePanel);
backdrop.addEventListener('click', () => { if (openMod) closePanel(); else window.companion.hide(); });

send.addEventListener('click', () => {
  const question = q.value.trim();
  if (question) askInto(loreAnswer, question);
});
q.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); const v = q.value.trim(); if (v) askInto(loreAnswer, v); }
});

itemRead.addEventListener('click', () => {
  const t = (window.companion.clipboardRead() || '').trim();
  itemText.textContent = t || '(clipboard vazio — dê Ctrl-C num item no jogo e clique de novo)';
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    e.preventDefault();
    if (openMod) closePanel(); else window.companion.hide();
  }
});

init();
