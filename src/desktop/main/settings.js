'use strict';
// Window size, port, backend choice, and the look the next launch opens with. Nothing here is a secret.
const fs = require('node:fs');
const path = require('node:path');

class Settings {
  constructor(file) {
    this.file = file;
    try { this.data = JSON.parse(fs.readFileSync(file, 'utf8')); } catch { this.data = {}; }
  }

  get(key, fallback) { return this.data[key] ?? fallback; }

  set(key, value) {
    this.data[key] = value;
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    const tmp = `${this.file}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(this.data, null, 1));
    fs.renameSync(tmp, this.file);
  }
}

module.exports = { Settings };
