#!/usr/bin/env python3
"""Check the reviewed TurtleBot 4 reference without installing or starting it."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ceres_route_sim'))
from ceres_route_sim.upstream_core import main

if __name__ == '__main__':
    sys.exit(main())
