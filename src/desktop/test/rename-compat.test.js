'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { normalizeEnvironment, configDirectory } = require('../main/env-compat');
const { prepareDataHome } = require('../main/data-home');

test('legacy environment is read and explicit CERES values win', () => {
  const env = normalizeEnvironment({ URP_PORT: '4200', URP_DATA_HOME: '/old', CERES_DATA_HOME: '' });
  assert.equal(env.CERES_PORT, '4200');
  assert.equal(env.CERES_DATA_HOME, '');
  assert.equal(configDirectory({ URP_CONFIG_DIR: '/legacy-config' }), path.resolve('/legacy-config'));
});

test('existing dataset identity survives the rename unchanged', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ceres-compat-'));
  try {
    const file = path.join(root, 'dataset.json');
    const original = JSON.stringify({ schema: 'urp.dataset/1', dataset_id: 'original-id' });
    fs.writeFileSync(file, original);
    const data = prepareDataHome({ getPath: () => root }, { get: () => root });
    assert.equal(data.dataset.dataset_id, 'original-id');
    assert.equal(fs.readFileSync(file, 'utf8'), original);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});
