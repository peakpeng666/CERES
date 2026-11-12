// Reproducible, version/byte-checked repair to the pinned gzweb distribution.
// No global WebSocket/FileReader/console overrides. npm ci reapplies this patch.
import { readFileSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const file = fileURLToPath(new URL('../node_modules/gzweb/dist/gzweb.module.js', import.meta.url));
const pkg = JSON.parse(readFileSync(new URL('../node_modules/gzweb/package.json', import.meta.url), 'utf8'));
const marker = '// URP gzweb lifecycle/5';
let source = readFileSync(file, 'utf8');
if (pkg.version !== '3.0.2') throw new Error('Review the gzweb lifecycle patch before changing its pinned version');
if (!source.startsWith(marker)) {
  const original = createHash('sha256').update(source).digest('hex');
  if (original !== 'd3d7ab3f660f059c7c3e6d1fd924a0ed63717c24fac78b8d5c0ff6f744675624') throw new Error(`Unexpected gzweb distribution: ${original}`);
  function replaceOnce(before, after) {
    if (source.split(before).length !== 2) throw new Error('gzweb patch context no longer unique: ' + before.slice(0, 70));
    source = source.replace(before, after);
  }
  const begin = source.indexOf('class Transport {');
  const end = source.indexOf('\nclass ', begin + 1);
  let transport = source.slice(begin, end);
  const connectStart = transport.indexOf('    connect(url, key) {');
  const connectEnd = transport.indexOf('    /**\n     * Advertise a topic.', connectStart);
  transport = transport.slice(0, connectStart) + `    connect(url, key) {
        this.disconnect();
        this.lastError = null;
        const ws = new WebSocket(url);
        this.ws = ws;
        // Synchronous decoding preserves WebSocket wire order and has no late FileReader callbacks.
        ws.binaryType = "arraybuffer";
        this.status$.next("connecting");
        const current = () => this.ws === ws;
        ws.onopen = () => { if (current()) this.onOpen(key); };
        ws.onclose = () => { if (current()) this.disconnect(); };
        ws.onmessage = event => { if (current()) this.onMessage(event); };
        ws.onerror = event => { if (current()) this.onError(event); };
    }
    disconnect() {
        const ws = this.ws;
        this.ws = null; // Invalidate callbacks synchronously, before close dispatches asynchronously.
        if (ws) {
            ws.onopen = ws.onclose = ws.onmessage = ws.onerror = null;
            ws.close();
        }
        this.onClose();
    }
` + transport.slice(connectEnd);
  transport = transport.replace('        this.topicMap.clear();', '        this.topicMap.clear();\n        this.assetMap.clear();\n        this.world = "";');
  const messageStart = transport.indexOf('    onMessage(event) {');
  const binaryStart = transport.indexOf('            // Decode as UTF-8 to get the header.', messageStart);
  const binaryEnd = transport.indexOf('        };\n        // Read the blob data as an array buffer.', binaryStart);
  const errorEnd = transport.indexOf('    /**\n     * Helper function to build a message.', binaryEnd);
  let binary = transport.slice(binaryStart, binaryEnd).replaceAll('fileReader.result', 'data');
  const payloadStart = binary.indexOf('            // do not decode image msg');
  if (payloadStart < 0) throw new Error('Missing Gazebo frame patch context');
  binary = `            const buffer = new Uint8Array(data);
            const parts = [];
            let offset = 0;
            const decoder = new TextDecoder("utf-8", { fatal: true });
            for (let i = 0; i < 3; i++) {
                const end = buffer.indexOf(44, offset);
                if (end < 0) throw new Error("Malformed Gazebo frame header");
                parts.push(decoder.decode(buffer.subarray(offset, end)));
                offset = end + 1;
            }
            const frameParts = parts;
            const msgType = this.root.lookup(frameParts[2]);
            const msgData = buffer.subarray(offset);
            let msg;
` + binary.slice(payloadStart);
  // Upstream dereferences the missing callback in the error branch itself.
  binary = binary.replace('`No resource callback for ${this.assetMap.get(frameParts[1]).uri}`', '`No resource callback for ${frameParts[1]}`');
  transport = transport.slice(0, messageStart) + `    onMessage(event) {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
        try {
            const data = typeof event.data === "string" ? new TextEncoder().encode(event.data).buffer : event.data;
            if (!(data instanceof ArrayBuffer)) throw new Error("Unexpected Gazebo WebSocket frame type");
            if (!this.root) {
                const content = new TextDecoder("utf-8", { fatal: true }).decode(data);
                if (content === "authorized") { this.sendMessage(["protos", "", "", ""]); return; }
                if (content === "invalid") throw new Error("Gazebo authorization rejected");
                this.root = parse(content, { keepCase: true }).root;
                this.sendMessage(["topics-types", "", "", ""]);
                this.sendMessage(["worlds", "", "", ""]);
                this.status$.next("connected");
                return;
            }
            var _a, _b;
${binary}
        } catch (error) { this.onError(error); }
    }
    onError(event) {
        this.lastError = event instanceof Error ? event.message : "Gazebo WebSocket connection failed";
        this.disconnect();
        this.status$.next("error");
        console.error("Gazebo transport:", this.lastError);
    }
` + transport.slice(errorEnd);
  source = source.slice(0, begin) + transport + source.slice(end);
  replaceOnce('            cb: _cb,', '            cb: _cb,\n            requestedAt: Date.now(), status: "loading",');
  replaceOnce('                    this.assetMap.get(frameParts[1]).cb(msg["data"], error);',
    '                    const asset = this.assetMap.get(frameParts[1]);\n                    asset.status = error ? "failed" : "received";\n                    asset.cb(msg["data"], error);\n                    asset.status = error ? "failed" : "ready";');
  replaceOnce('    getConnectionStatus() {\n        return this.connectionStatus;\n    }',
    `    getConnectionStatus() {\n        return this.connectionStatus;\n    }
    getConnectionError() { return this.transport.lastError ?? null; }
    getResourceStatus() {
        const assets = [...this.transport.assetMap.values()];
        return { total: assets.length, ready: assets.filter(a => a.status === "ready").length,
            failed: assets.filter(a => a.status === "failed" || Date.now() - a.requestedAt > 15000 && a.status !== "ready").map(a => a.uri) };
    }`);
  replaceOnce('(_b = this.scene.scene.renderer)', '(_b = this.scene && this.scene.renderer)');
  replaceOnce('this.sceneElement.removeChild(this.scene.scene.renderer.domElement);', 'this.scene.renderer.domElement.remove();');
  // Controls attach document listeners that renderer.dispose does not remove.
  replaceOnce('        this.renderer.renderLists.dispose();', '        this.controls?.dispose();\n        this.renderer.renderLists.dispose();');
  // Gazebo absolute paths are server-side files, not URLs on the panel HTTP server.
  // Use the authenticated asset channel directly; normalize the custom STL loader's
  // byte-view contract and deliver a Mesh consistently for both loading paths.
  const stlStart = source.indexOf('    loadSTL(uri, submesh, centerSubmesh, onLoad, onError) {');
  const stlEnd = source.indexOf('    /**\n     * Load GLTF/GLB file', stlStart);
  if (stlStart < 0 || stlEnd < 0) throw new Error('Missing STL patch context');
  source = source.slice(0, stlStart) + `    loadSTL(uri, submesh, centerSubmesh, onLoad, onError) {
        const manager = this.stlLoader.manager;
        const done = value => {
            const mesh = value.isMesh ? value : new THREE.Mesh(value);
            mesh.castShadow = mesh.receiveShadow = true;
            mesh.name = uri;
            this.meshes.set(uri, mesh);
            const copy = mesh.clone();
            if (!submesh || this.useSubMesh(copy, submesh, centerSubmesh)) onLoad(copy);
        };
        const fallback = () => {
            this.findResourceCb(uri, (raw, error) => {
                if (error !== undefined) {
                    manager.markAsError(uri);
                    onError?.(new Error("Gazebo asset unavailable: " + uri));
                    return;
                }
                done(this.stlLoader.parse(raw));
                manager.markAsDone(uri);
            });
        };
        if (this.findResourceCb && (uri.startsWith("/") || uri.startsWith("file://"))) {
            manager.itemStart(uri);
            manager.itemError(uri); // Retain the item until the WebSocket reply.
            fallback();
        } else {
            this.stlLoader.load(uri, done, undefined, error => {
                if (this.findResourceCb) fallback();
                else onError?.(error);
            });
        }
    }
` + source.slice(stlEnd);
  replaceOnce('        const binData = ensureBinary(data);',
    '        const binary = ensureBinary(data);\n        const binData = binary instanceof ArrayBuffer ? new Uint8Array(binary) : binary;');
  // Mesh HTTP-load failures can ask findResourceCb for a WebSocket fallback long after
  // the original scene was destroyed. Scope both the request and its callback to that socket.
  replaceOnce(`        function findAsset(_uri, _cb) {
            that.transport.getAsset(_uri, _cb);
        }`, `        const connection = this.transport.ws;
        function findAsset(_uri, _cb) {
            if (!connection || that.transport.ws !== connection || connection.readyState !== WebSocket.OPEN) return;
            that.transport.getAsset(_uri, (...args) => {
                if (that.transport.ws === connection && connection.readyState === WebSocket.OPEN) _cb(...args);
            });
        }`);
  writeFileSync(file, marker + '\n' + source);
}
if (createHash('sha256').update(readFileSync(file)).digest('hex') !== 'ccb7a8cbdd06ae0d32e7c76210dde90b770f061f52ef00d2767bc03eb8217d56') {
  throw new Error('Modified gzweb patch output; refuse an unverified dependency');
}
console.log('gzweb 3.0.2: verified CERES transport lifecycle repair');
