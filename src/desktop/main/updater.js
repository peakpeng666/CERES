'use strict';
// Updates come from the repository's latest release. The state is pushed to the settings page; nothing restarts by itself.
const { autoUpdater } = require('electron-updater');

const CHECK_EVERY_MS = 6 * 3600_000;

class Updater {
  constructor(app, publish) {
    this.app = app;
    this.publish = publish;
    this.state = { status: app.isPackaged ? 'idle' : 'unavailable', version: null, percent: null, detail: null };
  }

  set(state) {
    this.state = { ...this.state, ...state };
    this.publish(this.state);
  }

  start() {
    if (!this.app.isPackaged || process.env.URP_NO_UPDATE === '1') return;
    // A test feed; releases use the address baked in by electron-builder.
    if (process.env.URP_UPDATE_URL) autoUpdater.setFeedURL({ provider: 'generic', url: process.env.URP_UPDATE_URL });
    autoUpdater.autoDownload = true;
    autoUpdater.autoInstallOnAppQuit = true;
    autoUpdater.logger = null;
    autoUpdater.on('checking-for-update', () => this.set({ status: 'checking', detail: null }));
    autoUpdater.on('update-not-available', () => this.set({ status: 'current', detail: null }));
    autoUpdater.on('update-available', (info) => this.set({ status: 'downloading', version: info.version, percent: 0 }));
    autoUpdater.on('download-progress', (p) => this.set({ status: 'downloading', percent: Math.round(p.percent) }));
    autoUpdater.on('update-downloaded', (info) => this.set({ status: 'ready', version: info.version, percent: 100 }));
    autoUpdater.on('error', (error) => this.set({ status: 'error', detail: String(error.message ?? error).split('\n')[0] }));
    setTimeout(() => void this.check(), 15_000);
    setInterval(() => void this.check(), CHECK_EVERY_MS).unref();
  }

  async check() {
    if (!this.app.isPackaged) return this.state;
    try {
      await autoUpdater.checkForUpdates();
    } catch (error) {
      this.set({ status: 'error', detail: String(error.message ?? error).split('\n')[0] });
    }
    return this.state;
  }

  install() {
    if (this.state.status === 'ready') autoUpdater.quitAndInstall();
  }
}

module.exports = { Updater };
