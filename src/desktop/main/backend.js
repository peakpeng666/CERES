'use strict';
// The bundled gateway: one child process, started with the app and stopped with it.
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const http = require('node:http');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { dataLocations, prepareDataHome } = require('./data-home');

const PREFERRED_PORT = 4100;
const START_TIMEOUT_MS = 120_000;
const STOP_TIMEOUT_MS = 180_000;

function request(url, { method = 'GET', timeoutMs = 1500 } = {}) {
  return new Promise((resolve) => {
    const body = method === 'POST' ? '{}' : undefined;
    const req = http.request(url, { method, timeout: timeoutMs, headers: body ? { 'Content-Type': 'application/json', 'Content-Length': 2 } : {} }, (res) => {
      let data = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => resolve({ status: res.statusCode, body: data }));
    });
    req.on('timeout', () => { req.destroy(); resolve({ status: 0, body: '' }); });
    req.on('error', () => resolve({ status: 0, body: '' }));
    if (body) req.write(body);
    req.end();
  });
}

async function healthy(base) {
  const r = await request(`${base}/lab/api/health`);
  if (r.status !== 200) return false;
  try { return JSON.parse(r.body).status === 'ok'; } catch { return false; }
}

function portIsFree(port) {
  return new Promise((resolve) => {
    const probe = net.createServer();
    probe.once('error', () => resolve(false));
    probe.listen(port, '127.0.0.1', () => probe.close(() => resolve(true)));
  });
}

/** A stable port keeps the page origin, and with it the window's saved layout, across launches. */
async function choosePort(saved) {
  for (const port of [saved, PREFERRED_PORT]) {
    if (port && await portIsFree(port)) return port;
  }
  return new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once('error', reject);
    probe.listen(0, '127.0.0.1', () => { const { port } = probe.address(); probe.close(() => resolve(port)); });
  });
}

function layout(app) {
  if (!app.isPackaged) {
    const repo = path.resolve(__dirname, '..', '..', '..');
    return { python: path.join(repo, 'src', 'lab', '.venv', 'bin', 'python'), cwd: path.join(repo, 'src', 'lab') };
  }
  const root = path.join(process.resourcesPath, 'backend');
  const lab = path.join(root, 'app', 'src', 'lab');
  const python = process.platform === 'win32' ? path.join(root, 'python', 'python.exe') : path.join(lab, '.venv', 'bin', 'python');
  return { python, cwd: lab };
}

function childEnv(app, port, settings) {
  const env = { ...process.env };
  // The AppImage runtime exports its own library path; the bundled Python must not inherit it.
  for (const name of ['LD_LIBRARY_PATH', 'PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV']) delete env[name];
  const config = path.join(os.homedir(), '.config', 'urp-workbench');
  const data = dataLocations(app, settings);
  return {
    ...env,
    URP_DATA_HOME: data.root,
    URP_LAB_RUNTIME: data.runtime,
    URP_API_DB: data.archiveDb,
    URP_CONFIG_DIR: config,
    URP_LLM_CONFIG: path.join(config, 'llm.json'),
    URP_LLM_CREDENTIALS: path.join(config, 'credentials.json'),
    URP_PORT: String(port),
    URP_MODELS_REFRESH: '1',
    URP_DESKTOP: '1',
    // Source Electron uses this checkout's ROS setup; packaged apps must use their accepted simulation image.
    // Derive this here so an inherited development flag cannot enable host ROS in an installed app.
    URP_DESKTOP_SOURCE: app.isPackaged ? '0' : '1',
    URP_APP_VERSION: app.getVersion(),
    // The JS baselines run on this binary, so the installer does not carry a second Node.
    URP_NODE: process.execPath,
    ELECTRON_RUN_AS_NODE: '1',
    PYTHONDONTWRITEBYTECODE: '1',
    PYTHONUNBUFFERED: '1',
    PYTHONUTF8: '1',
  };
}

class Backend {
  constructor(app, settings) {
    this.app = app;
    this.settings = settings;
    this.child = null;
    this.starting = null;
    this.base = null;
    this.logPath = path.join(app.getPath('userData'), 'logs', 'backend.log');
  }

  tail(lines = 12) {
    try {
      return fs.readFileSync(this.logPath, 'utf8').trim().split('\n').slice(-lines).join('\n');
    } catch {
      return '';
    }
  }

  async start(onStatus = () => {}) {
    if (this.starting) return this.starting;
    this.starting = this._start(onStatus);
    try { return await this.starting; }
    finally { this.starting = null; }
  }

  async _start(onStatus) {
    if (this.child && this.child.exitCode === null && this.child.signalCode == null) {
      throw new Error('现有后端进程还未退出，不能重复启动；请重试以先停止原进程');
    }
    prepareDataHome(this.app, this.settings);
    const { python, cwd } = layout(this.app);
    if (!fs.existsSync(python)) throw new Error(`找不到内置 Python：${python}`);
    const port = await choosePort(this.settings.get('port'));
    this.settings.set('port', port);
    this.base = `http://127.0.0.1:${port}`;
    fs.mkdirSync(path.dirname(this.logPath), { recursive: true });
    const log = fs.openSync(this.logPath, 'a');
    fs.writeSync(log, `\n=== ${new Date().toISOString()} 启动后端 ${this.app.getVersion()} 端口 ${port} ===\n`);
    onStatus('正在启动后端');
    // Stdin stays open for the app's lifetime; the gateway leaves by itself if the app dies without saying goodbye.
    this.child = spawn(python, ['-m', 'urp_lab', 'serve', '--port', String(port), '--no-archive', '--exit-with-stdin'],
      { cwd, env: childEnv(this.app, port, this.settings), stdio: ['pipe', log, log], windowsHide: true });
    fs.closeSync(log);
    const child = this.child;
    const exited = new Promise((resolve) => child.once('exit', (code) => resolve(code ?? -1)));
    child.once('error', (error) => fs.appendFileSync(this.logPath, `无法启动：${error.message}\n`));
    try {
      const deadline = Date.now() + START_TIMEOUT_MS;
      while (Date.now() < deadline) {
        if (await healthy(this.base)) return this.base;
        const code = await Promise.race([exited, new Promise((r) => setTimeout(() => r(null), 300))]);
        if (code !== null) throw new Error(`后端退出（返回码 ${code}）\n${this.tail()}`);
      }
      throw new Error(`后端 ${START_TIMEOUT_MS / 1000} 秒内没有就绪\n${this.tail()}`);
    } catch (error) {
      // A failed launch still owns its child. Retire it before returning to the recovery UI;
      // stop() retains the handle if termination fails, so retry cannot orphan another process.
      try { await this.stop(); }
      catch (stopError) {
        throw new Error(`${error.message}\n启动失败后的清理未完成：${stopError.message}`, { cause: error });
      }
      throw error;
    }
  }

  /** Orderly first, so running solves and simulations close and keep their records; force only after that fails. */
  async stop() {
    const child = this.child;
    if (!child || child.exitCode !== null || child.signalCode != null) {
      if (this.child === child) this.child = null;
      return;
    }
    const gone = new Promise((resolve) => child.once('exit', resolve));
    await request(`${this.base}/lab/api/shutdown`, { method: 'POST', timeoutMs: 3000 });
    child.stdin?.end();
    const clean = await Promise.race([gone.then(() => true), new Promise((r) => setTimeout(() => r(false), STOP_TIMEOUT_MS))]);
    if (clean) {
      if (this.child === child) this.child = null;
      return;
    }
    if (process.platform === 'win32') spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true });
    else child.kill('SIGKILL');
    const stopped = await Promise.race([gone.then(() => true), new Promise((r) => setTimeout(() => r(false), 5000))]);
    if (!stopped) throw new Error('后端还未退出，不能切换或迁移数据');
    if (this.child === child) this.child = null;
  }
}

module.exports = { Backend, healthy, request, portIsFree, choosePort, layout, childEnv };
