#!/usr/bin/env python3
"""Fail Docker health checks on HTTP errors or a non-healthy JSON response."""
import json
import os
import urllib.request


def check(url=None):
    port = int(os.environ.get('URP_PORT', '4100'))
    def healthy(target):
        with urllib.request.urlopen(target, timeout=2) as response:
            payload = json.load(response)
        if not isinstance(payload, dict) or payload.get('status') != 'ok':
            raise RuntimeError(f'Service is not healthy at {target}: {payload!r}')
        return payload

    gateway = healthy(url or f'http://127.0.0.1:{port}/lab/api/health')
    if url:  # Explicit single-endpoint probe retained for callers/tests.
        return
    # Read the effective server configuration, not this process's environment:
    # --no-archive and URP_ARCHIVE_AUTOSTART=0 both select Lab-only readiness.
    required = gateway.get('archive_required', True)
    if type(required) is not bool:
        raise RuntimeError('Gateway archive_required must be a boolean')
    if required:
        healthy(f'http://127.0.0.1:{port}/api/health')


if __name__ == '__main__':
    check()
