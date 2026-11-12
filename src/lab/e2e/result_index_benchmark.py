#!/usr/bin/env python3
"""Read-only timing of cold result parsing versus a warm compact summary index."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys
import threading
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ceres_lab.fieldsim import FieldSimManager

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--runtime', type=Path, required=True)
p.add_argument('--out', type=Path, required=True)
args = p.parse_args()
manager = FieldSimManager(None, SimpleNamespace(_lock=threading.RLock()), runtime=args.runtime)
files = {str(f.relative_to(args.runtime)): hashlib.sha256(f.read_bytes()).hexdigest()
         for f in (args.runtime / 'fieldsim').glob('*/result.json')}
cold, warm = [], []
reference = None
for _ in range(7):
    manager._result_cache.clear()
    t = time.perf_counter()
    a = manager.results()
    cold.append(1000 * (time.perf_counter() - t))
    t = time.perf_counter()
    b = manager.results()
    warm.append(1000 * (time.perf_counter() - t))
    assert a == b and (reference is None or reference == a), 'Results changed during benchmark'
    reference = a
assert all(hashlib.sha256((args.runtime / f).read_bytes()).hexdigest() == h for f, h in files.items())
result = {'scope': 'seven repetitions, filesystem warm; result listing only, not solver acceleration',
          'input_sha256': files, 'rows': len(reference), 'cold_ms': cold, 'warm_ms': warm,
          'median_cold_ms': statistics.median(cold), 'median_warm_ms': statistics.median(warm),
          'ratio': statistics.median(cold) / statistics.median(warm), 'identical_results': True}
args.out.write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({k: v for k, v in result.items() if k not in ('input_sha256', 'cold_ms', 'warm_ms')}))
