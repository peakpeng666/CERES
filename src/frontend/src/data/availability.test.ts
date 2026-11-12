import { afterEach, expect, test } from 'vitest';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { copyPublicAssets } from '../../tools/optional-public.mjs';
import { explainLoadError } from './api';
import { fetchJSON } from '../../core/data-client.mjs';

const temporary: string[] = [];
afterEach(() => { for (const dir of temporary.splice(0)) rmSync(dir, { recursive: true, force: true }); });

test('a fresh checkout builds assets; restored exports are copied without changing the symlink', () => {
  const dir = mkdtempSync(join(tmpdir(), 'ceres-public-')); temporary.push(dir);
  const source = join(dir, 'public'); mkdirSync(source);
  writeFileSync(join(source, 'favicon.svg'), '<svg/>');
  symlinkSync('../exports', join(source, 'data'));
  copyPublicAssets(source, join(dir, 'first'));
  expect(readFileSync(join(dir, 'first/favicon.svg'), 'utf8')).toBe('<svg/>');
  mkdirSync(join(dir, 'exports'));
  writeFileSync(join(dir, 'exports/fixture.txt'), 'test-only bytes');
  copyPublicAssets(source, join(dir, 'restored'));
  expect(readFileSync(join(dir, 'restored/data/fixture.txt'), 'utf8')).toBe('test-only bytes');
});

test('missing metadata is honest absence, while corruption and network failure stay errors', () => {
  const missing = Object.assign(new Error('HTTP 404 while loading /data/meta.json'), { code: 'HTTP_ERROR', url: '/data/meta.json' });
  expect(explainLoadError(missing).title).toBe('无历史数据');
  for (const code of ['HASH_MISMATCH', 'INVALID_JSON', 'NETWORK_ERROR']) {
    expect(explainLoadError(Object.assign(new Error('failure'), { code, url: '/data/meta.json' })).title).not.toBe('无历史数据');
  }
});

test('JSON requests cannot fall back to the application HTML', async () => {
  await expect(fetchJSON('/data/meta.json', { fetchImpl: async (_url, options) => {
    expect(options?.headers).toEqual({ Accept: 'application/json' });
    return new Response('', { status: 404 });
  } })).rejects.toMatchObject({ code: 'HTTP_ERROR' });
});
