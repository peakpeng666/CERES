'use strict';
// Archive releases share this repository. Resolve a complete desktop release before configuring the updater.
const CHECK_EVERY_MS = 6 * 3600_000;
const RELEASES_API = 'https://api.github.com/repos/peakpeng666/URP/releases';
const RELEASE_DOWNLOADS = 'https://github.com/peakpeng666/URP/releases/download';
const MAX_RELEASE_PAGES = 10; // At most 1,000 releases; reaching the limit is an error, never a false "current".

function stableVersion(tag) {
  if (typeof tag !== 'string' || !/^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(tag)) return null;
  const parts = tag.slice(1).split('.').map(Number);
  return parts.every(Number.isSafeInteger) ? parts : null;
}

function desktopRelease(release, platform, arch) {
  const version = stableVersion(release?.tag_name);
  if (!version || release.draft || release.prerelease || !Array.isArray(release.assets)) return null;
  const assets = new Map(release.assets.filter((a) => a.state === 'uploaded').map((a) => [a.name, a.size]));
  const prefix = `agri-route-workbench-${release.tag_name.slice(1)}`;
  let manifest, installers;
  if (platform === 'win32') {
    manifest = 'latest.yml';
    installers = [[`${prefix}-windows-${arch}.exe`]];
  } else if (platform === 'linux') {
    manifest = `latest-linux${arch === 'x64' ? '' : `-${arch}`}.yml`;
    installers = [
      [`${prefix}-linux-${arch}.AppImage`, ...(arch === 'x64' ? [`${prefix}-linux-x86_64.AppImage`] : [])],
      [`${prefix}-linux-${arch}.deb`, ...(arch === 'x64' ? [`${prefix}-linux-amd64.deb`] : [])],
    ];
  } else return null;
  if (!(assets.get(manifest) > 0) || !installers.every((names) => names.some((name) => assets.get(name) >= 10_000_000))) return null;
  return { tag: release.tag_name, version, url: `${RELEASE_DOWNLOADS}/${release.tag_name}/` };
}

async function findDesktopRelease(fetch, platform, arch) {
  let selected = null;
  for (let page = 1; page <= MAX_RELEASE_PAGES; page += 1) {
    const response = await fetch(`${RELEASES_API}?per_page=100&page=${page}`, {
      headers: { Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28' },
      signal: AbortSignal.timeout(15_000),
    });
    if (!response.ok) throw new Error(`GitHub 发布查询失败（HTTP ${response.status}），请稍后重试。`);
    const releases = await response.json();
    if (!Array.isArray(releases)) throw new Error('GitHub 发布查询返回了无效数据。');
    for (const release of releases) {
      const candidate = desktopRelease(release, platform, arch);
      if (!candidate) continue;
      const difference = selected ? candidate.version.map((n, i) => n - selected.version[i]).find((n) => n !== 0) : 1;
      if (difference > 0) selected = candidate;
    }
    if (releases.length < 100) return selected;
  }
  throw new Error(`GitHub 发布超过 ${MAX_RELEASE_PAGES * 100} 条查询上限，无法确认最新桌面版本。`);
}

class Updater {
  constructor(app, publish, options = {}) {
    this.app = app;
    this.publish = publish;
    this.options = options;
    this.env = options.env ?? process.env;
    this.platform = options.platform ?? process.platform;
    this.arch = options.arch ?? process.arch;
    this.state = { status: this.disabledReason() ? 'unavailable' : 'idle', version: null, percent: null, detail: this.disabledReason() };
  }

  disabledReason() {
    if (!this.app.isPackaged) return '开发版本不检查更新。';
    if (this.env.URP_NO_UPDATE === '1') return '更新已由 URP_NO_UPDATE 禁用。';
    if (!['linux', 'win32'].includes(this.platform)) return '此平台尚未提供桌面自动更新。';
    return null;
  }

  set(state) {
    this.state = { ...this.state, ...state };
    this.publish(this.state);
  }

  fail(error) {
    this.set({ status: 'error', version: null, percent: null, detail: String(error.message ?? error).split('\n')[0] });
  }

  configure() {
    if (this.updater) return;
    // Lazy loading also lets the release resolver be tested without launching Electron.
    const updater = this.updater = this.options.updater ?? require('electron-updater').autoUpdater;
    updater.autoDownload = true;
    updater.autoInstallOnAppQuit = true;
    updater.allowPrerelease = false;
    updater.allowDowngrade = false;
    updater.logger = null;
    updater.on('checking-for-update', () => this.set({ status: 'checking', version: null, percent: null, detail: null }));
    updater.on('update-not-available', () => this.set({ status: 'current', version: null, percent: null, detail: null }));
    updater.on('update-available', (info) => this.set({ status: 'downloading', version: info.version, percent: 0, detail: null }));
    updater.on('download-progress', (p) => this.set({ status: 'downloading', percent: Math.round(p.percent) }));
    updater.on('update-downloaded', (info) => this.set({ status: 'ready', version: info.version, percent: 100, detail: null }));
    updater.on('error', (error) => this.fail(error));
  }

  start() {
    if (this.started || this.disabledReason()) return;
    this.started = true;
    this.configure();
    setTimeout(() => void this.check(), 15_000).unref();
    setInterval(() => void this.check(), CHECK_EVERY_MS).unref();
  }

  async check() {
    const disabled = this.disabledReason();
    if (disabled) {
      this.set({ status: 'unavailable', version: null, percent: null, detail: disabled });
      return this.state;
    }
    if (this.pending) return this.pending;
    // Do not change the provider while a verified update is downloading or waiting to be installed.
    if (['downloading', 'ready'].includes(this.state.status)) return this.state;
    this.pending = this.performCheck();
    try { return await this.pending; }
    finally { this.pending = null; }
  }

  async performCheck() {
    this.set({ status: 'checking', version: null, percent: null, detail: null });
    try {
      let url = this.env.URP_UPDATE_URL; // Explicit local feed for installer QA.
      if (!url) {
        const fetch = this.options.fetch ?? ((...args) => require('electron').net.fetch(...args));
        const release = await findDesktopRelease(fetch, this.platform, this.arch);
        if (!release) {
          this.set({ status: 'no-release', detail: `尚未发布适用于 ${this.platform}/${this.arch} 的完整桌面更新。` });
          return this.state;
        }
        url = release.url;
      }
      this.configure();
      this.updater.setFeedURL({ provider: 'generic', url, useMultipleRangeRequest: false });
      const result = await this.updater.checkForUpdates();
      if (result == null) this.set({ status: 'unavailable', detail: '此安装方式不支持自动更新，请使用正式桌面安装包。' });
      // checkForUpdates resolves before autoDownload. Observe its rejection as well as the error event.
      result?.downloadPromise?.catch((error) => this.fail(error));
    } catch (error) {
      this.fail(error);
    }
    return this.state;
  }

  install() {
    if (this.state.status === 'ready') this.updater.quitAndInstall();
  }
}

module.exports = { Updater, desktopRelease, findDesktopRelease };
