'use strict';
const { app, BrowserWindow, Menu, dialog, ipcMain, session, shell } = require('electron');
const path = require('node:path');
const { Backend } = require('./backend');
const { Container } = require('./container');
const { Settings } = require('./settings');
const { Updater } = require('./updater');
const { DataTransfer } = require('./data-transfer');

const TITLE = '农业机器人路径规划工作台';
const SPLASH = path.join(__dirname, '..', 'splash.html');

if (!app.requestSingleInstanceLock()) app.quit();
app.setAppUserModelId('com.peakpeng.agri-route-workbench');

let win = null;
let settings = null;
let backend = null;
let container = null;
let updater = null;
let dataTransfer = null;
let transitioning = false;
let origin = null;
let quitting = false;

const send = (channel, value) => { if (win && !win.isDestroyed()) win.webContents.send(channel, value); };
const status = (text) => send('boot:status', text);
const atOrigin = (url) => { try { return origin !== null && new URL(url).origin === origin; } catch { return false; } };

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
    if (/^https?:/.test(url) && !atOrigin(url)) void shell.openExternal(url);
    return { action: atOrigin(url) ? 'allow' : 'deny' };
  });
  win.webContents.on('will-navigate', (event, url) => {
    if (url.startsWith('file:') || atOrigin(url)) return;
    event.preventDefault();
    if (/^https?:/.test(url)) void shell.openExternal(url);
  });
  return win.loadFile(SPLASH);
}

/** Start whichever backend is chosen and show it. A failure stays on the splash page with what went wrong. */
async function boot(hash = '') {
  const mode = settings.get('backend', 'native');
  try {
    if (mode === 'container') {
      status('正在启动仿真容器');
      origin = await container.start(status);
    } else {
      origin = await backend.start(status);
    }
    await win.loadURL(`${origin}/${hash.startsWith('#/') ? hash : ''}`);
  } catch (error) {
    send('boot:error', { message: String(error.message ?? error), log: backend.logPath, mode });
  }
}

async function switchBackend(mode) {
  if (transitioning) throw new Error('正在切换或迁移，请稍候');
  transitioning = true;
  try {
  origin = null;
  await win.loadFile(SPLASH);
  if (mode === 'container') await backend.stop();
  else await container.stop();
  settings.set('backend', mode);
  await boot();
  return { mode };
  } finally { transitioning = false; }
}

async function transferData(action) {
  if (transitioning) throw new Error('正在切换或迁移，请稍候');
  const date = new Date().toISOString().replace(/[:.]/g, '-');
  let file, destination;
  if (action === 'export') {
    const picked = await dialog.showSaveDialog(win, { title: '备份工作台数据', defaultPath: `urp-data-${date}.zip`, filters: [{ name: 'URP 数据包', extensions: ['zip'] }] });
    if (picked.canceled) return { canceled: true };
    file = picked.filePath;
  } else {
    const picked = await dialog.showOpenDialog(win, { title: '选择工作台数据包', properties: ['openFile'], filters: [{ name: 'URP 数据包', extensions: ['zip'] }] });
    if (picked.canceled) return { canceled: true };
    file = picked.filePaths[0];
    const folder = await dialog.showOpenDialog(win, { title: '选择恢复位置的上级目录（将在其中新建数据目录）', properties: ['openDirectory', 'createDirectory'] });
    if (folder.canceled) return { canceled: true };
    destination = path.join(folder.filePaths[0], `urp-data-${date}`);
  }
  // Dialogs are asynchronous; recheck before taking ownership of the transition.
  if (transitioning) throw new Error('正在切换或迁移，请稍候');
  transitioning = true;
  const hash = new URL(win.webContents.getURL()).hash;
  try {
    if (settings.get('backend', 'native') === 'container') await container.stop();
    else await backend.stop();
    status(action === 'export' ? '正在备份并校验数据' : '正在校验并恢复到新目录');
    return await (action === 'export' ? dataTransfer.export(file) : dataTransfer.restore(file, destination));
  } finally {
    transitioning = false;
    await boot(hash);
  }
}

function trusted(event) {
  const url = event.senderFrame?.url ?? '';
  const { pathToFileURL } = require('node:url');
  return url === pathToFileURL(SPLASH).href || atOrigin(url);
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
  handle('data:info', () => dataTransfer.info());
  handle('data:export', () => transferData('export'));
  handle('data:restore', () => transferData('restore'));
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
  container = new Container(app, { settings });
  dataTransfer = new DataTransfer(app, settings);
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
