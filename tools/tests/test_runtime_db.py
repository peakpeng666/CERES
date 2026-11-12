"""Runtime data isolation and launcher regressions; no repository database writes."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('runtime_db', ROOT / 'tools/runtime_db.py')
runtime_db = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime_db)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create_database(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE records (id INTEGER PRIMARY KEY, value TEXT)')
        connection.execute('INSERT INTO records(value) VALUES (?)', (value,))


class RuntimeDatabaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'historical.sqlite3'
        self.destination = self.root / 'runtime/research.sqlite3'
        create_database(self.source, 'historical record')

    def test_first_copy_preserves_source_and_is_independently_writable(self):
        before = digest(self.source)
        self.assertTrue(runtime_db.initialize_runtime_db(self.source, self.destination))
        with sqlite3.connect(self.destination) as connection:
            self.assertEqual(connection.execute('SELECT value FROM records').fetchall(),
                             [('historical record',)])
            connection.execute("INSERT INTO records(value) VALUES ('new runtime job')")
        self.assertEqual(digest(self.source), before)
        with sqlite3.connect(f'{self.source.as_uri()}?mode=ro', uri=True) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM records').fetchone()[0], 1)
        self.assertFalse(os.path.samefile(self.source, self.destination))

    def test_existing_database_is_never_overwritten(self):
        create_database(self.destination, 'user job retained')
        before = (digest(self.source), digest(self.destination))
        self.assertFalse(runtime_db.initialize_runtime_db(self.source, self.destination))
        self.assertEqual((digest(self.source), digest(self.destination)), before)

    def test_committed_wal_records_are_copied_without_changing_archive_bytes(self):
        connection = sqlite3.connect(self.source)
        try:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute("INSERT INTO records(value) VALUES ('committed WAL record')")
            connection.commit()
            before = digest(self.source)
            self.assertTrue(runtime_db.initialize_runtime_db(self.source, self.destination))
            self.assertEqual(digest(self.source), before)
            with sqlite3.connect(self.destination) as copied:
                self.assertEqual(copied.execute('SELECT value FROM records ORDER BY id').fetchall(),
                                 [('historical record',), ('committed WAL record',)])
        finally:
            connection.close()

    def test_absent_archive_does_not_create_a_replacement(self):
        self.source.unlink()
        self.assertFalse(runtime_db.initialize_runtime_db(self.source, self.destination))
        self.assertFalse(self.destination.exists())

    def test_corrupt_archive_fails_without_publishing_a_database(self):
        self.source.write_bytes(b'corrupt archived sqlite data')
        before = digest(self.source)
        with self.assertRaises(sqlite3.DatabaseError):
            runtime_db.initialize_runtime_db(self.source, self.destination)
        self.assertFalse(self.destination.exists())
        self.assertEqual(digest(self.source), before)
        self.assertEqual(list(self.destination.parent.iterdir()), [])

    def test_parallel_initialization_has_one_winner_and_no_partial_database(self):
        barrier = threading.Barrier(4)
        link = os.link

        def publish(source, destination):
            barrier.wait(timeout=10)
            return link(source, destination)

        before = digest(self.source)
        with patch.object(runtime_db.os, 'link', side_effect=publish), ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: runtime_db.initialize_runtime_db(
                self.source, self.destination), range(4)))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(digest(self.source), before)
        with sqlite3.connect(self.destination) as connection:
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchall(), [('ok',)])
            self.assertEqual(connection.execute('SELECT value FROM records').fetchall(),
                             [('historical record',)])
        self.assertEqual(list(self.destination.parent.iterdir()), [self.destination])

    def test_runtime_aliases_of_the_archive_are_rejected(self):
        self.destination.parent.mkdir()
        before = digest(self.source)
        self.destination.symlink_to(self.source)
        with self.assertRaises(ValueError):
            runtime_db.initialize_runtime_db(self.source, self.destination)
        self.destination.unlink()
        os.link(self.source, self.destination)
        with self.assertRaises(ValueError):
            runtime_db.initialize_runtime_db(self.source, self.destination)
        self.assertEqual(digest(self.source), before)


class StartupPathTests(unittest.TestCase):
    def test_relative_interpreter_and_explicit_database_resolve_before_cd(self):
        with tempfile.TemporaryDirectory() as temporary:
            caller = Path(temporary)
            executable = caller / 'bin/capture-python'
            executable.parent.mkdir()
            executable.write_text(f'#!{sys.executable}\nimport json,os,sys\n'
                                  'print(json.dumps({"cwd":os.getcwd(),"args":sys.argv[1:]}))\n')
            executable.chmod(0o700)
            env = dict(os.environ, CERES_PYTHON='bin/capture-python', CERES_API_DB='state/custom.sqlite3')
            result = subprocess.run(['bash', str(ROOT / 'src/start.sh'), 'backend'], cwd=caller,
                                    env=env, text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            # backend imports packages, then replaces itself with serve. Both use the
            # interpreter and database resolved before the directory change.
            captured = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
            self.assertEqual(len(captured), 2, result.stdout)
            database = str((caller / 'state/custom.sqlite3').resolve())
            package_root = (ROOT / 'data/packages').resolve()
            for invocation in captured:
                self.assertEqual(invocation['cwd'], str(ROOT / 'src/backend'))
                self.assertEqual(invocation['args'][:4], ['-m', 'backend', '--db', database])
            bootstrap, serve = captured
            self.assertEqual(bootstrap['args'][4], 'bootstrap')
            self.assertEqual(
                Path(bootstrap['args'][bootstrap['args'].index('--package-root') + 1]).resolve(),
                package_root)
            self.assertEqual(serve['args'][4], 'serve')
            self.assertEqual(
                Path(serve['args'][serve['args'].index('--import-root') + 1]).resolve(),
                package_root)

    def test_default_database_stays_local_when_historical_archive_appears(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary)
            (checkout / 'src/backend').mkdir(parents=True)
            shutil.copy2(ROOT / 'src/start.sh', checkout / 'src/start.sh')
            archive = checkout / 'data/backend/research.sqlite3'
            create_database(archive, 'historical archive')
            runtime = checkout / '.local-state/research.sqlite3'
            create_database(runtime, 'previous user job')
            before = (digest(archive), digest(runtime))
            executable = checkout / 'capture-python'
            executable.write_text(f'#!{sys.executable}\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
            executable.chmod(0o700)
            env = dict(os.environ, CERES_PYTHON='./capture-python')
            env.pop('CERES_API_DB', None)
            result = subprocess.run(['bash', str(checkout / 'src/start.sh'), 'backend'],
                                    cwd=checkout, env=env, text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            arguments = json.loads(result.stdout.splitlines()[-1])
            self.assertEqual(arguments[:3], ['-m', 'backend', '--db'])
            self.assertEqual(Path(arguments[3]).resolve(), runtime)
            self.assertEqual((digest(archive), digest(runtime)), before)


if __name__ == '__main__':
    unittest.main()
