'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { normalizeEnvironment } = require('./env-compat');
const { randomUUID } = require('node:crypto');

function dataHome(app, settings) {
  return path.resolve(settings?.get('dataRoot') || normalizeEnvironment().CERES_DATA_HOME || app.getPath('userData'));
}

function dataLocations(app, settings) {
  const root = dataHome(app, settings);
  const runtime = path.join(root, 'runtime');
  // Existing 0.2 development installations used runtime/archive. Reuse that database in both modes.
  const oldArchive = path.join(runtime, 'archive');
  const archive = fs.existsSync(path.join(oldArchive, 'research.sqlite3')) && !fs.existsSync(path.join(root, 'archive', 'research.sqlite3'))
    ? oldArchive : path.join(root, 'archive');
  return { root, runtime, archive, archiveDb: path.join(archive, 'research.sqlite3') };
}

function prepareDataHome(app, settings) {
  const locations = dataLocations(app, settings);
  for (const dir of [locations.root, locations.runtime, locations.archive]) fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  const file = path.join(locations.root, 'dataset.json');
  if (!fs.existsSync(file)) {
    try { fs.writeFileSync(file, JSON.stringify({ schema: 'ceres.dataset/1', dataset_id: randomUUID(), created_utc: new Date().toISOString() }), { flag: 'wx', mode: 0o600 }); }
    catch (error) { if (error.code !== 'EEXIST') throw error; }
  }
  const dataset = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (!['ceres.dataset/1', 'urp.dataset/1'].includes(dataset.schema) || typeof dataset.dataset_id !== 'string') throw new Error('数据目录的身份文件损坏，请从备份恢复到新目录');
  return { ...locations, dataset };
}

module.exports = { dataHome, dataLocations, prepareDataHome };
