'use strict';
// Gazebo and ROS run only on Linux, so they come in the published simulation image. In container mode the whole gateway
// runs inside it and the window points there; the native backend is stopped meanwhile.
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { healthy } = require('./backend');

const REGISTRY = 'ghcr.io/peakpeng666/urp-sim';
const HOST_PORT = 4100;
const START_TIMEOUT_MS = 5 * 60_000;
const DOCKER_DOWNLOAD = process.platform === 'win32' ? 'https://docs.docker.com/desktop/setup/install/windows-install/' : 'https://docs.docker.com/engine/install/';

function run(command, args, { timeoutMs = 10_000, env, onLine } = {}) {
  return new Promise((resolve) => {
    let out = '';
    let child;
    try {
      child = spawn(command, args, { env: env ?? process.env, windowsHide: true });
    } catch (error) {
      resolve({ code: -1, out: error.message });
      return;
    }
    const timer = timeoutMs ? setTimeout(() => child.kill(), timeoutMs) : null;
    const collect = (chunk) => {
      const text = chunk.toString();
      out += text;
      if (onLine) for (const line of text.split(/\r?\n|\r/)) if (line.trim()) onLine(line.trim());
    };
    child.stdout.on('data', collect);
    child.stderr.on('data', collect);
    child.once('error', (error) => { if (timer) clearTimeout(timer); resolve({ code: -1, out: error.message }); });
    child.once('close', (code) => { if (timer) clearTimeout(timer); resolve({ code: code ?? -1, out: out.trim() }); });
  });
}

class Container {
  constructor(app, { port = HOST_PORT } = {}) {
    this.app = app;
    this.port = port;
    this.base = `http://127.0.0.1:${port}`;
    this.tag = app.isPackaged ? `v${app.getVersion()}` : 'deploy';
    const root = app.isPackaged ? path.join(process.resourcesPath, 'compose') : path.resolve(__dirname, '..', '..', '..', 'deploy');
    this.files = ['-f', path.join(root, 'docker-compose.sim.yml'), '-f', path.join(root, 'docker-compose.desktop.yml')];
  }

  env() {
    return { ...process.env, URP_IMAGE_TAG: process.env.URP_IMAGE_TAG || this.tag, URP_PORT: String(this.port), URP_HOST_CONFIG: path.join(os.homedir(), '.config', 'urp-workbench') };
  }

  /** What this machine has, so the page can say exactly what is missing. */
  async status() {
    const image = `${REGISTRY}:${this.env().URP_IMAGE_TAG}`;
    const server = await run('docker', ['version', '--format', '{{.Server.Version}}']);
    const dockerReady = server.code === 0;
    const compose = dockerReady ? await run('docker', ['compose', 'version', '--short']) : { code: 1, out: '' };
    const inspect = dockerReady ? await run('docker', ['image', 'inspect', image, '--format', '{{.Size}}']) : { code: 1, out: '' };
    const wsl = process.platform === 'win32' ? await run('wsl', ['--status']) : null;
    return {
      platform: process.platform, image, download: DOCKER_DOWNLOAD,
      docker: { ready: dockerReady, version: dockerReady ? server.out : null, detail: dockerReady ? null : (server.out.split('\n')[0] || '没有找到 Docker') },
      compose: { ready: compose.code === 0, version: compose.code === 0 ? compose.out : null },
      imagePresent: inspect.code === 0, imageBytes: inspect.code === 0 ? Number(inspect.out) : null,
      wsl: wsl ? { ready: wsl.code === 0 } : null,
      running: await healthy(this.base),
    };
  }

  async start(onLine = () => {}) {
    // Docker would create a missing mount point itself, owned by root, and the app could no longer save keys there.
    fs.mkdirSync(this.env().URP_HOST_CONFIG, { recursive: true });
    const up = await run('docker', ['compose', ...this.files, 'up', '-d'], { timeoutMs: 0, env: this.env(), onLine });
    if (up.code !== 0) throw new Error(up.out.split('\n').slice(-4).join('\n') || 'docker compose 启动失败');
    const deadline = Date.now() + START_TIMEOUT_MS;
    while (Date.now() < deadline) {
      if (await healthy(this.base)) return this.base;
      await new Promise((r) => setTimeout(r, 1000));
    }
    throw new Error('容器已启动，但工作台 5 分钟内没有就绪');
  }

  async stop() {
    return run('docker', ['compose', ...this.files, 'stop'], { timeoutMs: 120_000, env: this.env() });
  }
}

module.exports = { Container, run };
