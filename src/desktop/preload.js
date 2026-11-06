'use strict';
// The only door between the page and the app. Keys never pass through here.
const { contextBridge, ipcRenderer } = require('electron');

const listen = (channel) => (fn) => {
  const handler = (_event, value) => fn(value);
  ipcRenderer.on(channel, handler);
  return () => ipcRenderer.removeListener(channel, handler);
};

contextBridge.exposeInMainWorld('desktop', {
  info: () => ipcRenderer.invoke('app:info'),
  openLogs: () => ipcRenderer.invoke('app:open-logs'),
  retry: () => ipcRenderer.invoke('app:retry'),
  useNative: () => ipcRenderer.invoke('app:use-native'),
  setTheme: (theme) => ipcRenderer.invoke('app:theme', theme),
  setMotion: (motion) => ipcRenderer.invoke('app:motion', motion),
  calm: process.argv.includes('--lab-calm'),
  onBootStatus: listen('boot:status'),
  onBootReady: listen('boot:ready'),
  onBootError: listen('boot:error'),
  update: {
    state: () => ipcRenderer.invoke('update:state'),
    check: () => ipcRenderer.invoke('update:check'),
    install: () => ipcRenderer.invoke('update:install'),
    onChange: listen('update:state'),
  },
  sim: {
    status: () => ipcRenderer.invoke('sim:status'),
    enable: () => ipcRenderer.invoke('sim:enable'),
    disable: () => ipcRenderer.invoke('sim:disable'),
  },
  data: {
    info: () => ipcRenderer.invoke('data:info'),
    export: () => ipcRenderer.invoke('data:export'),
    restore: () => ipcRenderer.invoke('data:restore'),
  },
});
