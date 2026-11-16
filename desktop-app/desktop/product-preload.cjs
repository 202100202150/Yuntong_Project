const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('yuntongDesktop', Object.freeze({
  platform: process.platform,
  minimize: () => ipcRenderer.invoke('yuntong:window-minimize'),
  maximize: () => ipcRenderer.invoke('yuntong:window-maximize'),
  close: () => ipcRenderer.invoke('yuntong:window-close'),
  windowState: () => ipcRenderer.invoke('yuntong:window-state'),
  onWindowState: (callback) => {
    if (typeof callback !== 'function') throw new TypeError('Window state listener must be a function');
    const listener = (_event, state) => callback(state);
    ipcRenderer.on('yuntong:window-state-changed', listener);
    return () => { ipcRenderer.removeListener('yuntong:window-state-changed', listener); };
  },
}));
