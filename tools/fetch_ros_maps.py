#!/usr/bin/env python3
"""Fetch the pinned TurtleBot4 occupancy maps for planning tests."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'setup' / 'ros-maps.json'


def verify(content, item):
    if len(content) != item['bytes'] or hashlib.sha256(content).hexdigest() != item['sha256']:
        raise ValueError(f"Map checksum mismatch: {item['name']}")


def fetch(destination, manifest=MANIFEST):
    spec = json.loads(Path(manifest).read_text())
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    # Check all existing files before downloading or adding any new files.
    missing = []
    for item in spec['files']:
        path = destination / item['name']
        if path.exists():
            verify(path.read_bytes(), item)
        else:
            missing.append(item)
    with tempfile.TemporaryDirectory(prefix='.maps-', dir=destination) as temporary:
        staging = Path(temporary)
        for item in missing:
            url = f"{spec['raw_base_url']}/{item['name']}"
            with urlopen(url, timeout=30) as response:
                content = response.read(item['bytes'] + 1)
            verify(content, item)
            (staging / item['name']).write_bytes(content)
        for item in missing:
            # Exclusive creation keeps a concurrent user's file intact.
            with (destination / item['name']).open('xb') as stream:
                stream.write((staging / item['name']).read_bytes())
    return {'directory': str(destination), 'files': len(spec['files']), 'commit': spec['commit']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, default=ROOT / '.local-state' / 'ros-maps')
    args = parser.parse_args()
    print(json.dumps(fetch(args.destination), indent=2))


if __name__ == '__main__':
    main()
