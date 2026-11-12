'use strict';
// Gazebo and ROS run only on Linux, so they come in the published simulation image. In container mode the whole gateway
// runs inside it and the window points there; the native backend is stopped meanwhile.
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { healthy, request } = require('./backend');
const { dataLocations, prepareDataHome } = require('./data-home');

const { normalizeEnvironment, configDirectory } = require('./env-compat');

const REGISTRY = 'ghcr.io/peakpeng666/urp-sim';
const HOST_PORT = 4100;
const START_TIMEOUT_MS = 5 * 60_000;
const DOCKER_DOWNLOAD = process.platform === 'win32' ? 'https://docs.docker.com/desktop/setup/install/windows-install/' : 'https://docs.docker.com/engine/install/';

function run(command, args, { timeoutMs = 10_000, env, cwd, onLine } = {}) {
  return new Promise((resolve) => {
    let out = '';
    let child;
    try {
      // `cwd` matters for `python -m ceres_lab`: the installed app ships ceres_lab beside the runtime, not in site-packages.
      child = spawn(command, args, { cwd, env: env ?? process.env, windowsHide: true });
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
  constructor(app, { port = HOST_PORT, settings } = {}) {
    this.app = app;
    this.settings = settings;
    this.port = port;
    this.base = `http://127.0.0.1:${port}`;
    this.tag = app.isPackaged ? `v${app.getVersion()}` : 'deploy';
    const root = app.isPackaged ? path.join(process.resourcesPath, 'compose') : path.resolve(__dirname, '..', '..', '..', 'deploy');
    this.files = ['-f', path.join(root, 'docker-compose.sim.yml'), '-f', path.join(root, 'docker-compose.desktop.yml')];
  }

  env() {
    const data = dataLocations(this.app, this.settings);
    const user = os.userInfo();
    const env = normalizeEnvironment();
    return { ...env, CERES_IMAGE_TAG: env.CERES_IMAGE_TAG || this.tag, CERES_PORT: String(this.port),
      CERES_HOST_CONFIG: configDirectory(env),
      CERES_HOST_RUNTIME: data.runtime, CERES_HOST_ARCHIVE: data.archive,
      CERES_HOST_DATASET: path.join(data.root, 'dataset.json'),
      CERES_HOST_UID: String(user.uid >= 0 ? user.uid : 1000), CERES_HOST_GID: String(user.gid >= 0 ? user.gid : 1000) };
  }

  /** What this machine has, so the page can say exactly what is missing. */
  async status() {
    const image = `${REGISTRY}:${this.env().CERES_IMAGE_TAG}`;
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
    const data = prepareDataHome(this.app, this.settings);
    // Docker would create a missing mount point itself, owned by root, and the app could no longer save keys there.
    fs.mkdirSync(this.env().CERES_HOST_CONFIG, { recursive: true });
    const up = await run('docker', ['compose', ...this.files, 'up', '-d'], { timeoutMs: 0, env: this.env(), onLine });
    if (up.code !== 0) throw new Error(up.out.split('\n').slice(-4).join('\n') || 'docker compose 启动失败');
    const deadline = Date.now() + START_TIMEOUT_MS;
    while (Date.now() < deadline) {
      if (await healthy(this.base)) {
        const response = await request(`${this.base}/lab/api/health`);
        if (JSON.parse(response.body).dataset_id !== data.dataset.dataset_id) throw new Error('容器未使用当前数据集，已拒绝切换；请检查数据挂载和镜像版本');
        return this.base;
      }
      await new Promise((r) => setTimeout(r, 1000));
    }
    throw new Error('容器已启动，但工作台 5 分钟内没有就绪');
  }

  async stop() {
    const result = await run('docker', ['compose', ...this.files, 'stop', '--timeout', '180'], { timeoutMs: 200_000, env: this.env() });
    if (result.code !== 0) throw new Error(`容器未能停止，不能切换数据：${result.out.split('\n').slice(-3).join('\n')}`);
    return result;
  }
}

module.exports = { Container, run };
