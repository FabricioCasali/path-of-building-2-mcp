'use strict';

const { contextBridge, ipcRenderer } = require('electron');

// Minimal, safe bridge: the renderer talks to the C# service over a WebSocket by
// itself; the main process only supplies config and handles hide + focus.
contextBridge.exposeInMainWorld('companion', {
  getConfig: () => ipcRenderer.invoke('get-config'),
  hide: () => ipcRenderer.send('hide'),
  onFocusInput: (cb) => ipcRenderer.on('focus-input', () => cb()),
});
