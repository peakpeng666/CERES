#!/usr/bin/env python3
"""Seed an independent runtime SQLite database without changing its archive."""
import argparse
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time


def _existing_runtime(source: Path, destination: Path) -> bool:
    if not (destination.exists() or destination.is_symlink()):
        return False
    if destination.is_symlink() or not destination.is_file():
        raise ValueError('Runtime database must be a regular file, not a symlink or directory.')
    if source.exists() and os.path.samefile(source, destination):
        raise ValueError('Runtime database must be independent of the historical database.')
    return True


def initialize_runtime_db(source: Path, destination: Path) -> bool:
    """Copy a consistent SQLite snapshot only when no runtime database exists.

    Return True only for the process that publishes the first copy. An absent
    archive leaves normal empty-database creation to the backend. A corrupt or
    inaccessible archive fails explicitly and never becomes an empty snapshot.
    """
    source, destination = Path(source).absolute(), Path(destination).absolute()
    if source.resolve() == destination.resolve():
        raise ValueError('Runtime database must differ from the historical database.')
    if _existing_runtime(source, destination):
        return False
    if not source.exists():
        if source.is_symlink():
            raise FileNotFoundError(f'Historical database symlink is broken: {source}')
        return False
    if not source.is_file():
        raise ValueError(f'Historical database is not a regular file: {source}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f'.{destination.name}.', suffix='.tmp', dir=destination.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        # mode=ro includes committed WAL records while refusing writes to the
        # historical database. immutable=1 would incorrectly ignore its WAL.
        deadline = time.monotonic() + 30

        def progress(_status, _remaining, _total):
            if time.monotonic() > deadline:
                raise TimeoutError('Historical database backup exceeded 30 seconds.')

        original = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=20)
        try:
            snapshot = sqlite3.connect(temporary)
            try:
                original.backup(snapshot, pages=256, progress=progress, sleep=0.05)
            finally:
                snapshot.close()
        finally:
            original.close()
        with temporary.open('r+b') as stream:   # Windows can only flush a handle opened for writing
            os.fsync(stream.fileno())
        try:
            # Link an already-complete file into place. Unlike replace/rename,
            # this refuses to overwrite a database published by another start.
            os.link(temporary, destination)
        except FileExistsError:
            if not _existing_runtime(source, destination):
                raise RuntimeError('Runtime database disappeared during initialization.')
            return False
        return True
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    try:
        copied = initialize_runtime_db(args.source, args.destination)
    except (OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
        parser.exit(1, f'Runtime database initialization failed: {exc}\n')
    if copied:
        print(f'Initialized independent runtime database: {args.destination}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
