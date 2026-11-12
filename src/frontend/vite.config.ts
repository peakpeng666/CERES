/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { copyPublicAssets } from './tools/optional-public.mjs';
import { localApi } from './server/local-api.ts';

const root = fileURLToPath(new URL('.', import.meta.url));
const api = localApi(Number(process.env.CERES_API_PORT ?? 4180), Number(process.env.CERES_LAB_PORT ?? 4100));

// Files on a WSL-mounted Windows drive (/mnt/<letter>/...) do not raise inotify
// events when edited from Windows, so the dev server polls there. Override with
// BPP_WATCH_POLL=0 or BPP_WATCH_POLL=1.
const pollEnv = process.env.BPP_WATCH_POLL;
const usePolling = pollEnv === '1' || (pollEnv !== '0' && root.startsWith('/mnt/'));

// Large generated or research folders never need watching (public/data alone is
// ~2000 files); polling them would waste CPU.
const ignored = [
  '**/public/data/**',
  '**/research/**',
  '**/inputs/**',
  '**/.venv/**',
  '**/artifacts/**',
  '**/delivery/**',
  '**/tools/**',
  '**/backups/**',
  '**/dist/**',
];

export default defineConfig({
  plugins: [react(), api.plugin, {
    name: 'optional-historical-exports',
    apply: 'build',
    writeBundle(options) {
      copyPublicAssets(resolve(root, 'public'), resolve(root, options.dir ?? 'dist'));
    },
  }],
  resolve: {
    alias: {
      '@core': `${root}core`,
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    cors: false,
    proxy: api.proxy,
    watch: { usePolling, interval: 400, ignored },
  },
  preview: {
    host: '127.0.0.1',
    port: 4173,
    strictPort: true,
    cors: false,
    proxy: api.proxy,
  },
  build: {
    copyPublicDir: false,
    outDir: 'dist',
    target: 'es2022',
    chunkSizeWarningLimit: 900,
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});
