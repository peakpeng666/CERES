#!/usr/bin/env python3
"""Standalone genuine CVRPLIB CLI, distinct from synthetic CRC replay."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ceres_research.cvrplib.cli import configure, run
parser=argparse.ArgumentParser(description=__doc__)
configure(parser)
try:
    raise SystemExit(run(parser.parse_args()))
except (OSError, ValueError, RuntimeError) as exc:
    print(f'ERROR: {exc}', file=sys.stderr)
    raise SystemExit(2)
