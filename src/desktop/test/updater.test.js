'use strict';
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { test } = require('node:test');
const { desktopRelease, findDesktopRelease, Updater } = require('../main/updater');

const asset = (name, size = 20_000_000) => ({ name, size, state: 'uploaded' });
const release = (version, extra = {}) => ({
  tag_name: `v${version}`, draft: false, prerelease: false,
  assets: [asset('latest.yml', 500), asset('latest-linux.yml', 800),
    asset(`agri-route-workbench-${version}-windows-x64.exe`),
    asset(`agri-route-workbench-${version}-linux-x64.AppImage`),
    asset(`agri-route-workbench-${version}-linux-amd64.deb`)], ...extra,
});
const json = (data) => ({ ok: true, json: async () => data });
const archive = { tag_name: 'local-source-preservation-20261106', assets: [asset('legacy-inspection-tools.zip')] };
class FakeUpdater extends EventEmitter {
  feeds = [];
  checks = 0;
  installs = 0;
  setFeedURL(feed) { this.feeds.push(feed); }
  async checkForUpdates() {
    this.checks += 1;
    this.emit('checking-for-update');
    this.emit('update-not-available', { version: '0.3.0' });
    return { isUpdateAvailable: false };
  }
  quitAndInstall() { this.installs += 1; }
}
function fixture(extra = {}) {
  const native = new FakeUpdater();
  const events = [];
  const app = { isPackaged: true, getVersion: () => '0.2.0' };
  const updater = new Updater(app, (state) => events.push(state), {
    updater: native, platform: 'linux', arch: 'x64', env: {}, fetch: async () => json([release('0.3.0')]), ...extra,
  });
  return { updater, native, events };
}

test('discovery ignores latest archive releases and chooses the highest complete stable desktop version', async () => {
  const found = await findDesktopRelease(async () => json([
    archive, release('0.9.0'), release('0.10.0'), release('0.11.0', { assets: [] }),
    release('1.0.0', { draft: true }), release('2.0.0', { prerelease: true }),
    release('3.0.0', { tag_name: 'v3.0.0-beta.1' }), release('4.0.0', { tag_name: 'v04.0.0' }),
  ]), 'linux', 'x64');
  assert.equal(found.tag, 'v0.10.0');
  assert.equal(found.url, 'https://github.com/peakpeng666/URP/releases/download/v0.10.0/');
});

test('qualification requires uploaded metadata and full installers for the requested platform and architecture', () => {
  const good = release('0.3.0');
  assert.ok(desktopRelease(good, 'linux', 'x64'));
  assert.ok(desktopRelease(good, 'win32', 'x64'));
  assert.equal(desktopRelease(good, 'linux', 'arm64'), null);
  assert.equal(desktopRelease(good, 'darwin', 'x64'), null);
  for (const missing of ['latest-linux.yml', 'agri-route-workbench-0.3.0-linux-x64.AppImage', 'agri-route-workbench-0.3.0-linux-amd64.deb']) {
    assert.equal(desktopRelease({ ...good, assets: good.assets.filter((a) => a.name !== missing) }, 'linux', 'x64'), null);
  }
  assert.equal(desktopRelease({ ...good, assets: good.assets.map((a) => ({ ...a, size: 1 })) }, 'linux', 'x64'), null);
  assert.equal(desktopRelease({ ...good, assets: good.assets.map((a) => ({ ...a, state: 'new' })) }, 'linux', 'x64'), null);
  assert.equal(desktopRelease(release('0.3.0', { tag_name: 'v0.3.0/../../other' }), 'linux', 'x64'), null);
});

test('discovery follows bounded pages when archives obscure an older desktop release', async () => {
  const calls = [];
  const found = await findDesktopRelease(async (url, options) => {
    calls.push(url);
    assert.equal(options.headers.Accept, 'application/vnd.github+json');
    assert.ok(options.signal instanceof AbortSignal);
    return json(calls.length === 1 ? Array(100).fill(archive) : [release('0.3.0')]);
  }, 'linux', 'x64');
  assert.equal(found.tag, 'v0.3.0');
  assert.equal(calls.length, 2);
  assert.match(calls[1], /per_page=100&page=2$/);
});

test('a full pagination limit is an error instead of an incomplete latest-version claim', async () => {
  let calls = 0;
  await assert.rejects(findDesktopRelease(async () => { calls += 1; return json(Array(100).fill(release('0.3.0'))); }, 'linux', 'x64'), /1000/);
  assert.equal(calls, 10);
});

test('no complete desktop release is explicit and never checks the baked-in feed or reports current', async () => {
  const { updater, native, events } = fixture({ fetch: async () => json([archive]) });
  assert.equal((await updater.check()).status, 'no-release');
  assert.match(updater.state.detail, /尚未发布.*linux\/x64/);
  assert.equal(native.checks, 0);
  assert.deepEqual(native.feeds, []);
  assert.ok(!events.some((s) => ['current', 'ready'].includes(s.status)));
});

test('HTTP errors, network failures and invalid API data remain retryable errors', async () => {
  for (const fetch of [
    async () => ({ ok: false, status: 403 }),
    async () => ({ ok: false, status: 404 }),
    async () => { throw new Error('network unavailable'); },
    async () => { throw new Error('request timed out'); },
    async () => json({ message: 'bad payload' }),
  ]) {
    const { updater, native } = fixture({ fetch });
    assert.equal((await updater.check()).status, 'error');
    assert.ok(updater.state.detail);
    assert.equal(native.checks, 0);
    updater.options.fetch = async () => json([release('0.3.0')]);
    assert.equal((await updater.check()).status, 'current');
  }
});

test('every fresh check pins the discovered tag, so the baked-in current version does not freeze future updates', async () => {
  let version = '0.3.0';
  const { updater, native } = fixture({ fetch: async () => json([release(version)]) });
  await updater.check();
  version = '0.4.0';
  await updater.check();
  assert.deepEqual(native.feeds.map((f) => f.url), [
    'https://github.com/peakpeng666/URP/releases/download/v0.3.0/',
    'https://github.com/peakpeng666/URP/releases/download/v0.4.0/',
  ]);
  assert.equal(native.allowDowngrade, false);
  assert.equal(native.allowPrerelease, false);
});

test('overlapping checks share discovery and keep a download or ready update on its original provider', async () => {
  let finish;
  const response = new Promise((resolve) => { finish = resolve; });
  const { updater, native } = fixture({ fetch: () => response });
  const one = updater.check();
  const two = updater.check();
  finish(json([release('0.3.0')]));
  await Promise.all([one, two]);
  assert.equal(native.checks, 1);
  native.emit('update-available', { version: '0.3.0' });
  assert.equal((await updater.check()).status, 'downloading');
  updater.install();
  assert.equal(native.installs, 0);
  native.emit('update-downloaded', { version: '0.3.0' });
  assert.equal((await updater.check()).status, 'ready');
  assert.equal(native.checks, 1);
  updater.install();
  assert.equal(native.installs, 1);
});

test('a local QA feed bypasses discovery and reports manifest failures truthfully', async () => {
  const { updater, native } = fixture({ env: { CERES_UPDATE_URL: 'http://127.0.0.1:3210/' }, fetch: async () => { throw new Error('must not use GitHub'); } });
  native.checkForUpdates = async () => { throw new Error('Cannot find channel latest-linux.yml'); };
  assert.equal((await updater.check()).status, 'error');
  assert.match(updater.state.detail, /Cannot find channel/);
  assert.equal(native.feeds[0].url, 'http://127.0.0.1:3210/');
});

test('a failed automatic download is observed and cannot leave a ready or successful state', async () => {
  const { updater, native } = fixture();
  native.checkForUpdates = async () => {
    native.emit('update-available', { version: '0.3.0' });
    return { downloadPromise: Promise.reject(new Error('sha512 checksum mismatch')) };
  };
  await updater.check();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(updater.state.status, 'error');
  assert.match(updater.state.detail, /checksum/);
  assert.equal(updater.state.version, null);
  updater.install();
  assert.equal(native.installs, 0);
});

test('disabled updates, source runs and unsupported installation types never claim current', async () => {
  for (const options of [{ env: { CERES_NO_UPDATE: '1' } }, { platform: 'darwin' }]) {
    const { updater, native } = fixture(options);
    assert.equal((await updater.check()).status, 'unavailable');
    assert.equal(native.checks, 0);
  }
  const source = fixture();
  source.updater.app.isPackaged = false;
  assert.equal((await source.updater.check()).status, 'unavailable');
  assert.equal(source.native.checks, 0);
  const inactive = fixture();
  inactive.native.checkForUpdates = async () => null;
  assert.equal((await inactive.updater.check()).status, 'unavailable');
  assert.match(inactive.updater.state.detail, /安装方式/);
});
