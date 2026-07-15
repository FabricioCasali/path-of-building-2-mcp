'use strict';

const { contextBridge, ipcRenderer, clipboard } = require('electron');

// Minimal, safe bridge: the renderer talks to the C# service over a WebSocket by
// itself; the main process supplies config, handles hide + "shown", and reads the
// clipboard for the Item Check action (PoE copies an item's text with Ctrl-C).
contextBridge.exposeInMainWorld('companion', {
  getConfig: () => ipcRenderer.invoke('get-config'),
  hide: () => ipcRenderer.send('hide'),
  clipboardRead: () => clipboard.readText(),
  onShown: (cb) => ipcRenderer.on('shown', () => cb()),
});
