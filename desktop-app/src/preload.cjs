'use strict';
const {contextBridge, ipcRenderer} = require('electron');
contextBridge.exposeInMainWorld('paperDesktop', Object.freeze({
  getInfo: () => ipcRenderer.invoke('desktop:info'),
  minimize: () => ipcRenderer.invoke('desktop:minimize'),
  toggleMaximize: () => ipcRenderer.invoke('desktop:toggle-maximize'),
  close: () => ipcRenderer.invoke('desktop:close'),
  selectWorkspace: () => ipcRenderer.invoke('desktop:select-workspace'),
  retry: () => ipcRenderer.invoke('desktop:retry')
}));
