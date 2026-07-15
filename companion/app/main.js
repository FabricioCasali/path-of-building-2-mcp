'use strict';

const { app, BrowserWindow, globalShortcut, ipcMain, screen } = require('electron');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const CONFIG = loadConfig();
let win = null;
let service = null;

function loadConfig() {
  const defaults = {
    clientTxtPath: '',
    model: 'sonnet',
    hotkey: 'CommandOrControl+Shift+Space',
    serviceUrl: 'http://127.0.0.1:8848',
    spawnService: true,
  };
  try {
    const raw = fs.readFileSync(path.join(__dirname, 'config.json'), 'utf8');
    return { ...defaults, ...JSON.parse(raw) };
  } catch {
    return defaults;
  }
}

// Start the C# service (already-built binary) as a child so the app is one click.
// If the binary isn't built yet, we skip and just connect to a running instance.
function startService() {
  if (!CONFIG.spawnService) return;
  const binDir = path.join(__dirname, '..', 'service', 'bin', 'Debug', 'net10.0');
  const dll = path.join(binDir, 'PoeCompanion.Service.dll');
  if (!fs.existsSync(dll)) {
    console.warn(`[companion] serviço não compilado (${dll}). ` +
      `Rode "dotnet build" em companion/service, ou suba o serviço à parte.`);
    return;
  }
  service = spawn('dotnet', [dll], {
    cwd: binDir,
    env: {
      ...process.env,
      Companion__ClientTxtPath: CONFIG.clientTxtPath || '',
      Companion__Model: CONFIG.model || 'sonnet',
    },
    // windowsHide + piped stdio: no separate dotnet console window pops up.
    // We forward the service's logs into our own stdout instead.
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  service.stdout.on('data', (d) => process.stdout.write(`[svc] ${d}`));
  service.stderr.on('data', (d) => process.stderr.write(`[svc] ${d}`));
  service.on('error', (err) => console.error('[companion] falha ao iniciar serviço:', err.message));
  service.on('exit', (code) => console.log('[companion] serviço encerrou com código', code));
}

function createWindow() {
  // Cover the whole display: the hub is a full-screen overlay (bottom dock +
  // top context strip + a rising panel), mostly transparent so the game shows.
  const { x, y, width, height } = screen.getPrimaryDisplay().bounds;
  win = new BrowserWindow({
    x, y, width, height,
    show: false,
    frame: false,
    transparent: true,
    resizable: false,
    movable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    hasShadow: false,
    roundedCorners: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  // Float above fullscreen-ish windows (game must run in borderless/windowed).
  win.setAlwaysOnTop(true, 'screen-saver');
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
}

function toggleWindow() {
  if (!win) return;
  if (win.isVisible()) {
    win.hide();
  } else {
    win.show();
    win.focus();
    win.webContents.send('shown');
  }
}

app.whenReady().then(() => {
  startService();
  createWindow();

  const ok = globalShortcut.register(CONFIG.hotkey, toggleWindow);
  if (!ok) console.error(`[companion] não consegui registrar o hotkey "${CONFIG.hotkey}".`);

  ipcMain.handle('get-config', () => ({
    serviceUrl: CONFIG.serviceUrl,
    model: CONFIG.model,
    hotkey: CONFIG.hotkey,
  }));
  ipcMain.on('hide', () => win && win.hide());
});

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
  if (service && !service.killed) {
    try { service.kill(); } catch { /* ignore */ }
  }
});

// Overlay app: no dock/taskbar lifecycle; stay alive with no windows open.
app.on('window-all-closed', (e) => e.preventDefault());
