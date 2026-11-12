'use strict';
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

function normalizeEnvironment(env = process.env) {
  const result = { ...env };
  for (const [key, value] of Object.entries(env)) {
    if (key.startsWith('URP_') && !Object.hasOwn(result, `CERES_${key.slice(4)}`)) result[`CERES_${key.slice(4)}`] = value;
  }
  return result;
}

function configDirectory(env = process.env) {
  const currentEnv = normalizeEnvironment(env);
  if (currentEnv.CERES_CONFIG_DIR) return path.resolve(currentEnv.CERES_CONFIG_DIR);
  const current = path.join(os.homedir(), '.config', 'ceres-workbench');
  const legacy = path.join(os.homedir(), '.config', 'urp-workbench');
  return !fs.existsSync(current) && fs.existsSync(legacy) ? legacy : current;
}
module.exports = { normalizeEnvironment, configDirectory };
