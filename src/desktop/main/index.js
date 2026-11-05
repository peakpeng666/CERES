'use strict';
const { app, BrowserWindow, Menu, dialog, ipcMain, session, shell } = require('electron');
const path = require('node:path');
const { Backend } = require('./backend');
const { Container } = require('./container');
const { Settings } = require('./settings');
const { Updater } = require('./updater');

const TITLE = '农业机器人路径规划工作台';
const SPLASH = path.join(__dirname, '..', 'splash.html');

if (!app.requestSingleInstanceLock()) app.quit();
app.setAppUserModelId('com.peakpeng.agri-route-workbench');

let win = null;
let settings = null;
let backend = null;
let container = null;
let updater = null;
let origin = null;
let quitting = false;

const send = (channel, value) => { if (win && !win.isDestroyed()) win.webContents.send(channel, value); };
const status = (text) => send('boot:status', text);

function createWindow() {
  const bounds = settings.get('bounds', {});
  win = new BrowserWindow({
    width: bounds.width ?? 1360, height: bounds.height ?? 860, x: bounds.x, y: bounds.y, minWidth: 960, minHeight: 620,
    title: TITLE, backgroundColor: '#f7f5ef', autoHideMenuBar: true, show: false,
    icon: path.join(__dirname, 'icon.png'),
    webPreferences: { preload: path.join(__dirname, '..', 'preload.js'), contextIsolation: true, sandbox: true, nodeIntegration: false, spellcheck: false },
  });
  win.once('ready-to-show', () => win.show());
  win.on('close', () => settings.set('bounds', win.getNormalBounds()));
  win.on('closed', () => { win = null; });
  win.on('page-title-updated', (event) => { event.preventDefault(); win.setTitle(TITLE); });
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url) && !url.startsWith(origin ?? '\0')) void shell.openExternal(url);
    return { action: url.startsWith(origin ?? '\0') ? 'allow' : 'deny' };
  });
  win.webContents.on('will-navigate', (event, url) => {
    if (url.startsWith('file:') || url.startsWith(origin ?? '\0')) return;
    event.preventDefault();
    if (/^https?:/.test(url)) void shell.openExternal(url);
  });
  return win.loadFile(SPLASH);
}

/** Start whichever backend is chosen and show it. A failure stays on the splash page with what went wrong. */
async function boot() {
  const mode = settings.get('backend', 'native');
  try {
    if (mode === 'container') {
      status('正在启动仿真容器');
      origin = await container.start(status);
    } else {
      origin = await backend.start(status);
    }
    await win.loadURL(`${origin}/`);
  } catch (error) {
    send('boot:error', { message: String(error.message ?? error), log: backend.logPath, mode });
  }
}

async function switchBackend(mode) {
  settings.set('backend', mode);
  origin = null;
  await win.loadFile(SPLASH);
  if (mode === 'container') await backend.stop();
  else await container.stop();
  await boot();
  return { mode };
}

function trusted(event) {
  const url = event.senderFrame?.url ?? '';
  return url.startsWith('file:') || (origin !== null && url.startsWith(origin));
}

function handle(channel, fn) {
  ipcMain.handle(channel, (event, ...args) => {
    if (!trusted(event)) throw new Error('拒绝的来源');
    return fn(...args);
  });
}

function wire() {
  handle('app:info', () => ({ version: app.getVersion(), platform: process.platform, backend: settings.get('backend', 'native'), logs: path.dirname(backend.logPath), electron: process.versions.electron }));
  handle('app:open-logs', () => shell.openPath(path.dirname(backend.logPath)));
  handle('app:retry', () => { void boot(); });
  handle('app:use-native', () => switchBackend('native'));
  handle('update:state', () => updater.state);
  handle('update:check', () => updater.check());
  handle('update:install', () => updater.install());
  handle('sim:status', () => container.status());
  handle('sim:enable', () => switchBackend('container'));
  handle('sim:disable', () => switchBackend('native'));
}

function menu() {
  const template = [
    { label: '文件', submenu: [{ role: 'quit', label: '退出' }] },
    { label: '编辑', submenu: [{ role: 'undo', label: '撤销' }, { role: 'redo', label: '重做' }, { type: 'separator' }, { role: 'cut', label: '剪切' }, { role: 'copy', label: '复制' }, { role: 'paste', label: '粘贴' }, { role: 'selectAll', label: '全选' }] },
    { label: '视图', submenu: [{ role: 'reload', label: '重新加载' }, { type: 'separator' }, { role: 'resetZoom', label: '实际大小' }, { role: 'zoomIn', label: '放大' }, { role: 'zoomOut', label: '缩小' }, { type: 'separator' }, { role: 'togglefullscreen', label: '全屏' }] },
    { label: '帮助', submenu: [
      { label: '打开日志目录', click: () => shell.openPath(path.dirname(backend.logPath)) },
      { label: '项目主页', click: () => shell.openExternal('https://github.com/peakpeng666/URP') },
      { label: '关于', click: () => dialog.showMessageBox(win, { type: 'info', title: TITLE, message: TITLE, detail: `版本 ${app.getVersion()}` }) },
    ] },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

app.on('second-instance', () => {
  if (!win) return;
  if (win.isMinimized()) win.restore();
  win.focus();
});

app.whenReady().then(async () => {
  settings = new Settings(path.join(app.getPath('userData'), 'desktop.json'));
  backend = new Backend(app, settings);
  container = new Container(app);
  updater = new Updater(app, (state) => send('update:state', state));
  session.defaultSession.setPermissionRequestHandler((_wc, permission, done) => done(permission === 'clipboard-sanitized-write'));
  menu();
  wire();
  await createWindow();
  await boot();
  updater.start();
});

app.on('window-all-closed', () => app.quit());

app.on('before-quit', (event) => {
  if (quitting) return;
  event.preventDefault();
  quitting = true;
  // A container keeps running on purpose: a long simulation survives closing the window.
  backend.stop().finally(() => app.exit(0));
});
