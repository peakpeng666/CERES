'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { layout, childEnv } = require('./backend');
const { run } = require('./container');
const { dataLocations, prepareDataHome } = require('./data-home');

class DataTransfer {
  constructor(app, settings) { this.app = app; this.settings = settings; }

  info() { return { ...prepareDataHome(this.app, this.settings), lastTransfer: this.settings.get('lastTransfer', null) }; }

  async command(args) {
    const { python, cwd } = layout(this.app);
    const env = { ...childEnv(this.app, 4100, this.settings), CERES_MODELS_REFRESH: '0' };
    // Same working directory as Backend.start(): childEnv() drops PYTHONPATH, so ceres_lab resolves only from the Lab folder.
    const result = await run(python, ['-m', 'ceres_lab', 'data', ...args], { cwd, env, timeoutMs: 0 });
    if (result.code !== 0) {
      this.settings.set('lastTransfer', { state: 'failed', message: result.out || '数据迁移失败，当前数据未被覆盖' });
      throw new Error(result.out || '数据迁移失败，当前数据未被覆盖');
    }
    try { return JSON.parse(result.out); } catch { throw new Error('数据迁移没有返回可校验的结果'); }
  }

  async export(file) {
    const data = this.info();
    const args = ['export', '--runtime', data.runtime, '--out', file];
    if (fs.existsSync(data.archiveDb)) args.push('--archive-db', data.archiveDb);
    const result = await this.command(args);
    this.settings.set('lastTransfer', { state: 'verified', message: `备份已校验：${result.path}`, sha256: result.sha256 });
    return result;
  }

  async restore(file, destination) {
    // Only advance the setting after a fully verified restore to a new location. The old directory stays intact.
    const result = await this.command(['restore', '--bundle', file, '--dest', destination]);
    const oldRoot = dataLocations(this.app, this.settings).root;
    this.settings.set('previousDataRoot', oldRoot);
    this.settings.set('dataRoot', path.resolve(destination));
    this.settings.set('lastTransfer', { state: 'verified', message: `恢复已校验：${destination}`, sha256: result.sha256 });
    return result;
  }
}

module.exports = { DataTransfer };
