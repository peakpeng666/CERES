import type { IncomingHttpHeaders, ServerResponse } from 'node:http';
import type { Plugin, ProxyOptions } from 'vite';

/** Only an exact same-origin loopback request may reach the local API. */
export function allowedApiRequest(headers: IncomingHttpHeaders, port: number): boolean {
  const host = headers.host;
  if (!host || !new RegExp(`^(127\\.0\\.0\\.1|localhost|\\[::1\\]):${port}$`).test(host)) return false;
  if (headers['sec-fetch-site'] === 'cross-site') return false;
  if (headers.origin !== undefined && headers.origin !== `http://${host}`) return false;
  return true;
}

export function localApi(port: number, labPort = 4100): { plugin: Plugin; proxy: Record<string, ProxyOptions> } {
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('CERES_API_PORT must be a port in 1..65535');
  if (!Number.isInteger(labPort) || labPort < 1 || labPort > 65535) throw new Error('CERES_LAB_PORT must be a port in 1..65535');
  const reject = (res: ServerResponse) => {
    res.statusCode = 403; res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({ error: 'cross_origin', message: '本地 API 仅接受本站请求。' }));
  };
  const plugin: Plugin = {
    name: 'ceres-local-api-boundary',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (!req.url?.startsWith('/api/') && !req.url?.startsWith('/lab/api/')) return next();
        const address = server.httpServer?.address();
        if (!address || typeof address === 'string' || !allowedApiRequest(req.headers, address.port)) return reject(res);
        next();
      });
    },
    configurePreviewServer(server) {
      server.middlewares.use((req, res, next) => {
        if (!req.url?.startsWith('/api/') && !req.url?.startsWith('/lab/api/')) return next();
        const address = server.httpServer.address();
        if (!address || typeof address === 'string' || !allowedApiRequest(req.headers, address.port)) return reject(res);
        next();
      });
    },
  };
  const proxyTo = (destination: number, timeout: number): ProxyOptions => {
    const target = `http://127.0.0.1:${destination}`;
    return {
    target, changeOrigin: true, ws: false, timeout, proxyTimeout: timeout,
    configure(proxy) {
      proxy.on('proxyReq', (outbound, request) => {
        // Both Vite entrypoints reject mismatched Origin before this hook.
        if (request.headers.origin) outbound.setHeader('Origin', target);
      });
    },
    };
  };
  return { plugin, proxy: { '/api/': proxyTo(port, 10000), '/lab/api/': proxyTo(labPort, 0) } };
}
