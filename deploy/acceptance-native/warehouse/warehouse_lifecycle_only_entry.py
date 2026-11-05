#!/usr/bin/env python3
"""Startup/shutdown diagnostic only. Never a warehouse route acceptance run."""
from warehouse_acceptance import main

if __name__ == '__main__':
    raise SystemExit(main(lifecycle_only=True))
