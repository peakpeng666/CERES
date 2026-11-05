#!/usr/bin/env python3
"""Read-only build closure proof in the exact base interpreter, after build exists."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--python', required=True)
    p.add_argument('--expected-index-sha256', required=True)
    a = p.parse_args(); root = a.root
    raw = (root / 'artifact-index.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != a.expected_index_sha256:
        raise RuntimeError('Build index anchor mismatch')
    index = json.loads(raw)
    verifier_path = root / 'recipe/verify_offline_inputs.py'
    if verifier_path.is_symlink() or hashlib.sha256(verifier_path.read_bytes()).hexdigest() != index['files']['recipe/verify_offline_inputs.py']['sha256']:
        raise RuntimeError('Build verifier anchor mismatch')
    spec = importlib.util.spec_from_file_location('base_build_verifier', verifier_path)
    verifier = importlib.util.module_from_spec(spec); spec.loader.exec_module(verifier)
    g = verifier.anchored_import(root, a.expected_index_sha256)
    g.verify_index(root, a.expected_index_sha256)
    lock = g.read_json(root / 'reviewed-inputs.lock.json')
    guard = verifier.load('build_guard', root / 'recipe/build_guard.py')
    guard.network_guard(lock['network'])
    verifier.verify_prepared(root, a.python, lock, g)
    g.verify_index(root, a.expected_index_sha256)
    guard.network_guard(lock['network'])
    print(json.dumps({'build_closure_verified': True, 'base_interpreter_verified': True, 'build_executed': False}))

if __name__ == '__main__': main()
