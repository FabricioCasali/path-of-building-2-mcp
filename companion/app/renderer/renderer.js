'use strict';

const q = document.getElementById('q');
const send = document.getElementById('send');
const answer = document.getElementById('answer');
const frontier = document.getElementById('frontier');
const chars = document.getElementById('chars');

let ws = null;
let wsUrl = null;
let model = 'sonnet';
let streaming = false;

async function init() {
  const cfg = await window.companion.getConfig();
  model = cfg.model || 'sonnet';
  wsUrl = cfg.serviceUrl.replace(/^http/, 'ws') + '/ws';
  connect();

  window.companion.onFocusInput(() => {
    q.focus();
    q.select();
    requestState();
  });
}

function connect() {
  try {
    ws = new WebSocket(wsUrl);
  } catch {
    frontier.textContent = 'sem serviço';
    return;
  }
  ws.onopen = () => { setBadge('pronto'); requestState(); };
  ws.onclose = () => {
    setBadge('reconectando…');
    setTimeout(connect, 1500);
  };
  ws.onerror = () => { /* onclose will handle the retry */ };
  ws.onmessage = (ev) => handleMessage(ev.data);
}

function requestState() {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'state' }));
}

function setBadge(text) {
  frontier.textContent = text;
}

function handleMessage(raw) {
  let msg;
  try { msg = JSON.parse(raw); } catch { return; }

  switch (msg.type) {
    case 'state':
      setBadge(msg.frontier || '—');
      renderCharacters(msg.characters || []);
      break;
    case 'start':
      streaming = true;
      lockInput(true);
      setBadge(msg.frontier || frontier.textContent);
      answer.className = 'answer caret';
      answer.textContent = '';
      break;
    case 'chunk':
      answer.textContent += msg.text || '';
      answer.scrollTop = answer.scrollHeight;
      break;
    case 'done':
      streaming = false;
      lockInput(false);
      answer.className = 'answer';
      q.focus();
      break;
    case 'error':
      streaming = false;
      lockInput(false);
      answer.className = 'answer error';
      answer.textContent = '⚠ ' + (msg.message || 'erro desconhecido');
      break;
  }
}

function renderCharacters(list) {
  chars.textContent = list.length ? `${list.length} encontrados` : '';
  chars.title = list.join(', ');
}

function ask() {
  const question = q.value.trim();
  if (!question || streaming) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    answer.className = 'answer error';
    answer.textContent = '⚠ serviço offline — verifique se o PoeCompanion.Service está rodando.';
    return;
  }
  ws.send(JSON.stringify({ type: 'ask', question, model }));
}

function lockInput(locked) {
  q.disabled = locked;
  send.disabled = locked;
}

send.addEventListener('click', ask);
q.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); ask(); }
  else if (e.key === 'Escape') { e.preventDefault(); window.companion.hide(); }
});

init();
