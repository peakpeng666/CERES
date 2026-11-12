"""The archive backend, started from an installed history pack without shell scripts: seed a private database once,
import the record packages, then serve. The pack itself stays read-only."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from . import paths


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--pack', type=Path, required=True)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args(argv)
    sys.path.insert(0, str(paths.REPO / 'tools'))
    from runtime_db import initialize_runtime_db
    initialize_runtime_db(args.pack / 'archive' / 'research.sqlite3', args.db)
    backend = paths.REPO / 'src' / 'backend'
    packages = args.pack / 'archive' / 'packages'
    head = [sys.executable, '-m', 'backend', '--db', str(args.db)]
    subprocess.run([*head, 'bootstrap', '--package-root', str(packages)], cwd=backend, check=True)
    return subprocess.call([*head, 'serve', '--import-root', str(packages), '--port', str(args.port)], cwd=backend)


if __name__ == '__main__':
    sys.exit(main())
