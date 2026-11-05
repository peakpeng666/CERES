"""SQLite history of solver runs and simulation runs (full JSON + indexed columns; never deleted)."""
from __future__ import annotations

import json
import sqlite3
import sys
import threading
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, job_id TEXT, scenario_id TEXT, algorithm_id TEXT,
  state TEXT, status TEXT, created_utc TEXT, finished_utc TEXT, json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS runs_scenario ON runs(scenario_id, created_utc);
CREATE INDEX IF NOT EXISTS runs_job ON runs(job_id);
CREATE INDEX IF NOT EXISTS runs_created ON runs(created_utc);
CREATE TABLE IF NOT EXISTS sim_runs (
  sim_run_id TEXT PRIMARY KEY, run_id TEXT, session_id TEXT, world TEXT, mode TEXT,
  state TEXT, created_utc TEXT, finished_utc TEXT, json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS sim_runs_run ON sim_runs(run_id);
CREATE INDEX IF NOT EXISTS sim_runs_created ON sim_runs(created_utc);
CREATE TABLE IF NOT EXISTS studies (
  study_id TEXT PRIMARY KEY, created_utc TEXT, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS physics_runs (
  physics_run_id TEXT PRIMARY KEY, run_id TEXT, session_id TEXT, engine TEXT, state TEXT,
  created_utc TEXT, finished_utc TEXT, json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS physics_runs_run ON physics_runs(run_id, created_utc);
"""

RUN_ACTIVE = ('queued', 'running')
SIM_ACTIVE = ('queued', 'approach', 'executing')


class Store:
    def __init__(self, path: Path):
        raw_path = Path(path)
        # Resolve DB aliases before deriving the owner-lock path. Otherwise a
        # symlink to an active database could acquire a different .jobs.lock.
        self.path = raw_path if str(raw_path) == ':memory:' else raw_path.resolve()
        self._lock = threading.Lock()
        self._db = None
        self._owner_file = None
        if str(self.path) != ':memory:':
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # As with archive Jobs, claim the database before schema changes or
            # recovery. SQLite's write lock is not an execution-owner lock.
            self._owner_file = self.path.with_suffix(self.path.suffix + '.jobs.lock').open('a+b')
            try:
                if sys.platform == 'win32':
                    import msvcrt
                    self._owner_file.seek(0)
                    if not self._owner_file.read(1):
                        self._owner_file.write(b'\0')
                        self._owner_file.flush()
                    self._owner_file.seek(0)
                    msvcrt.locking(self._owner_file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._owner_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BaseException as exc:
                self._owner_file.close()
                self._owner_file = None
                raise RuntimeError(f'运行库 {self.path} 已有任务执行服务；请复用该网关或选择其他 runtime。') from exc
        try:
            self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=30)
            self._db.execute('PRAGMA journal_mode=WAL')
            self._db.execute('PRAGMA synchronous=NORMAL')
            self._db.executescript(_SCHEMA)
        except BaseException:
            self.close()
            raise

    def close(self):
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None
            if self._owner_file is not None:
                if sys.platform == 'win32':
                    import msvcrt
                    self._owner_file.seek(0)
                    msvcrt.locking(self._owner_file.fileno(), msvcrt.LK_UNLCK, 1)
                self._owner_file.close()
                self._owner_file = None

    def _query(self, sql: str, args=()) -> list[dict]:
        with self._lock:
            return [json.loads(row[0]) for row in self._db.execute(sql, args)]

    def put_run(self, sol: dict):
        algorithm = (sol.get('algorithm') or {}).get('id')
        with self._lock:
            self._db.execute(
                'INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?)',
                (sol['run_id'], sol.get('job_id'), sol.get('scenario_id'), algorithm, sol.get('state'),
                 sol.get('status'), sol.get('created_utc'), sol.get('finished_utc'),
                 json.dumps(sol, ensure_ascii=False)))

    def get_run(self, run_id: str) -> dict | None:
        rows = self._query('SELECT json FROM runs WHERE run_id=?', (run_id,))
        return rows[0] if rows else None

    def list_runs(self, scenario_id: str | None = None, job_id: str | None = None, limit: int = 100) -> list[dict]:
        where, args = [], []
        if scenario_id:
            where.append('scenario_id=?'); args.append(scenario_id)
        if job_id:
            where.append('job_id=?'); args.append(job_id)
        sql = 'SELECT json FROM runs' + (' WHERE ' + ' AND '.join(where) if where else '')
        return self._query(sql + ' ORDER BY created_utc DESC, run_id DESC LIMIT ?', (*args, int(limit)))

    def active_runs(self) -> list[dict]:
        return self._query('SELECT json FROM runs WHERE state IN (?,?)', RUN_ACTIVE)

    def put_sim_run(self, run: dict):
        with self._lock:
            self._db.execute(
                'INSERT OR REPLACE INTO sim_runs VALUES (?,?,?,?,?,?,?,?,?)',
                (run['sim_run_id'], run.get('run_id'), run.get('session_id'), run.get('world'), run.get('mode'),
                 run.get('state'), run.get('created_utc'), run.get('finished_utc'),
                 json.dumps(run, ensure_ascii=False)))

    def get_sim_run(self, sim_run_id: str) -> dict | None:
        rows = self._query('SELECT json FROM sim_runs WHERE sim_run_id=?', (sim_run_id,))
        return rows[0] if rows else None

    def list_sim_runs(self, run_id: str | None = None, limit: int = 100) -> list[dict]:
        if run_id:
            return self._query('SELECT json FROM sim_runs WHERE run_id=? ORDER BY created_utc DESC LIMIT ?',
                               (run_id, int(limit)))
        return self._query('SELECT json FROM sim_runs ORDER BY created_utc DESC, sim_run_id DESC LIMIT ?', (int(limit),))

    def active_sim_runs(self) -> list[dict]:
        return self._query('SELECT json FROM sim_runs WHERE state IN (?,?,?)', SIM_ACTIVE)

    def put_physics_run(self, run: dict):
        with self._lock:
            self._db.execute('INSERT OR REPLACE INTO physics_runs VALUES (?,?,?,?,?,?,?,?)',
                             (run['physics_run_id'], run.get('run_id'), run.get('session_id'), run.get('engine'), run.get('state'),
                              run.get('created_utc'), run.get('finished_utc'), json.dumps(run, ensure_ascii=False)))
            self._db.commit()

    def get_physics_run(self, physics_run_id: str) -> dict | None:
        rows = self._query('SELECT json FROM physics_runs WHERE physics_run_id = ?', (physics_run_id,))
        return rows[0] if rows else None

    def list_physics_runs(self, run_id: str | None = None, limit: int = 100) -> list[dict]:
        if run_id:
            return self._query('SELECT json FROM physics_runs WHERE run_id = ? ORDER BY created_utc DESC LIMIT ?', (run_id, limit))
        return self._query('SELECT json FROM physics_runs ORDER BY created_utc DESC LIMIT ?', (limit,))

    def put_study(self, study: dict):
        with self._lock:
            self._db.execute('INSERT OR REPLACE INTO studies VALUES (?,?,?)',
                             (study['study_id'], study.get('created_utc'), json.dumps(study, ensure_ascii=False)))

    def get_study(self, study_id: str) -> dict | None:
        rows = self._query('SELECT json FROM studies WHERE study_id=?', (study_id,))
        return rows[0] if rows else None

    def list_studies(self, limit: int = 200) -> list[dict]:
        return self._query('SELECT json FROM studies ORDER BY created_utc DESC LIMIT ?', (int(limit),))

    def run_digest(self, limit: int = 20000) -> list[dict]:
        """The few fields the algorithm library needs, newest first; the stored JSON is not parsed in Python."""
        sql = """SELECT run_id, scenario_id, algorithm_id, state, status, created_utc,
                      json_extract(json, '$.scenario_snapshot.title') AS title,
                      json_extract(json, '$.objective.value') AS value, json_extract(json, '$.objective.unit') AS unit,
                      json_extract(json, '$.metrics.wall_s') AS wall_s, json_extract(json, '$.params.time_limit_s') AS budget_s,
                      json_extract(json, '$.check.valid') AS valid, json_extract(json, '$.planning.comparison_key') AS key
                 FROM runs ORDER BY created_utc DESC LIMIT ?"""
        with self._lock:
            cursor = self._db.execute(sql, (limit,))
            names = [c[0] for c in cursor.description]
            return [dict(zip(names, row)) for row in cursor]

    def runs_by_ids(self, run_ids: list[str]) -> list[dict]:
        out = []
        for i in range(0, len(run_ids), 400):
            chunk = run_ids[i:i + 400]
            out += self._query(f"SELECT json FROM runs WHERE run_id IN ({','.join('?' * len(chunk))})", chunk)
        return out
