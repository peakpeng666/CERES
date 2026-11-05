"""Durable, serial CVRP jobs using the reviewed numerical research runner.

The HTTP contract never accepts a Python path, command, worker, model endpoint,
reference optimum or candidate route. Historical experiment revisions remain
separate from this engineering validation ledger.
"""
from importlib import import_module, util
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import sys
import threading
import uuid

from .store import StoreError, canonical, identifier, now, revision_number, sha

PROBLEM_FIELDS = ('coordinates', 'demands', 'capacity', 'K')
PAYLOAD_FIELDS = (*PROBLEM_FIELDS, 'algorithm', 'seed', 'budget_seconds')
ALGORITHMS = ('savings', 'T', 'TRS', 'TSR', 'pyvrp')
TERMINAL_STATES = ('completed', 'failed', 'interrupted')
MAX_NODES = 300
MAX_PENDING = 32
_RUN_LOCK = threading.Lock()
_RESEARCH_ROOT = Path(__file__).resolve().parents[2] / 'research' / 'src'
_CVRP_ROOT = _RESEARCH_ROOT / 'urp_research' / 'cvrplib'


def _source_manifest():
    files = {str(p.relative_to(_RESEARCH_ROOT)): sha(p.read_bytes())
             for p in sorted(_CVRP_ROOT.rglob('*.py'))}
    required = ('runner.py', 'worker.py', 'io.py', 'audit.py',
                'frozen/pyvrp/independent_review/independent_validator.py',
                'frozen/vnd/capacity_solver.py', 'frozen/vnd/engine.py')
    if any(not (_CVRP_ROOT / name).is_file() for name in required):
        raise StoreError('runner_unavailable', '缺少已审阅的CVRP执行源码。', 503)
    files['backend/jobs.py'] = sha(Path(__file__).read_bytes())
    try:
        pyvrp_version = version('pyvrp')
    except PackageNotFoundError:
        pyvrp_version = None
    identity = {'files': files, 'python_version': sys.version,
                'pyvrp_version': pyvrp_version}
    return {**identity, 'sha256': sha(canonical(identity).encode())}


def _load_runner():
    if sys.platform != 'linux':
        raise StoreError('runner_unavailable', 'CVRP进程监督需要Linux。', 503)
    root = str(_RESEARCH_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    runner = import_module('urp_research.cvrplib.runner')
    if Path(runner.__file__).resolve() != (_CVRP_ROOT / 'runner.py').resolve():
        raise StoreError('runner_unavailable', '研究模块路径与固定执行源码不一致。', 503)
    contract = import_module('urp_research.cvrplib.io')
    return runner.run_payload, contract.validate_payload, contract.digest


class JobManager:
    """One owner per database; one serial worker thread per owner."""

    def __init__(self, store):
        self.store = store
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._submission_lock = threading.Lock()
        self._closed = False
        self._worker_failure = None
        self._lock_file = None
        self._runner = None
        self._source = None
        self._unavailable_reason = None
        self._thread = None
        if sys.platform == 'linux':
            import fcntl
            self._lock_file = self.store.path.with_suffix(self.store.path.suffix+'.jobs.lock').open('a+b')
            try:
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                self._lock_file.close()
                self._lock_file = None
                raise StoreError('job_worker_active', '此数据库已有任务执行服务，请复用已有服务。', 503) from exc
        try:
            with self.store.connect() as db:
                db.executescript('''
                    CREATE TABLE IF NOT EXISTS job_scenes (
                      scene_id TEXT NOT NULL, revision INTEGER NOT NULL,
                      input_sha256 TEXT NOT NULL, problem TEXT NOT NULL,
                      created_at TEXT NOT NULL, PRIMARY KEY(scene_id,revision));
                    CREATE TABLE IF NOT EXISTS jobs (
                      sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                      id TEXT NOT NULL UNIQUE, status TEXT NOT NULL,
                      scene_id TEXT NOT NULL, scene_revision INTEGER NOT NULL,
                      input_sha256 TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
                      source_sha256 TEXT NOT NULL, source_manifest TEXT NOT NULL,
                      request TEXT NOT NULL, created_at TEXT NOT NULL,
                      started_at TEXT, finished_at TEXT, result TEXT, error TEXT,
                      result_status TEXT, feasible INTEGER, cost INTEGER,
                      FOREIGN KEY(scene_id,scene_revision)
                        REFERENCES job_scenes(scene_id,revision));
                    CREATE INDEX IF NOT EXISTS jobs_state_sequence ON jobs(status,sequence);
                ''')
            # An accepted attempt is never silently rerun under another process.
            self._interrupt_pending('SERVER_RESTARTED', '服务重启，先前未完成的任务已保留为中断。')
            try:
                self._source = _source_manifest()
                self._runner, self._validate_payload, self._digest = _load_runner()
                if _source_manifest()['sha256'] != self._source['sha256']:
                    raise StoreError('source_changed', '加载期间源码改变，请重启服务。', 503)
            except (ImportError, OSError, RuntimeError, StoreError) as exc:
                self._runner = None
                self._unavailable_reason = str(exc)
            self._thread = threading.Thread(target=self._work, name='urp-cvrp-jobs', daemon=True)
            self._thread.start()
        except BaseException:
            if self._lock_file:
                self._lock_file.close()
            raise

    def algorithms(self):
        available = self._runner is not None and not self._stopping.is_set()
        reason = self._unavailable_reason or self._worker_failure
        if available:
            try:
                self._check_source()
            except (StoreError, OSError) as exc:
                available, reason = False, str(exc)
        if self._stopping.is_set() and not reason:
            reason = '任务服务正在停止。'
        package = self._source.get('pyvrp_version') if self._source else None
        try:
            pyvrp_present = util.find_spec('pyvrp') is not None
        except (ImportError, ValueError):
            pyvrp_present = False
        rows = []
        for name in ALGORITHMS:
            ok = available and (name != 'pyvrp' or (package == '0.14.0' and pyvrp_present))
            rows.append({'id': name, 'available': ok, 'deterministic': name != 'pyvrp',
                         'reason': None if ok else reason or '需要已安装的PyVRP 0.14.0。',
                         'version': package if name == 'pyvrp' else None,
                         'seed_role': ('随机数种子；墙钟截止可能影响复现' if name == 'pyvrp'
                                       else '未使用；更换seed不构成独立随机重复')})
        return {'schema_version': 1, 'algorithms': rows,
                'constraints': {'problem': 'CVRP', 'min_nodes': 2, 'max_nodes': MAX_NODES,
                    'budget_seconds': [1, 5], 'max_pending_jobs': MAX_PENDING,
                    'execution': 'serial', 'depot_index': 0,
                    'distance': 'EUC_2D: floor(Euclidean distance + 0.5) per edge',
                    'vehicle_limit': 'at most K nonempty routes',
                    'coordinate_units': 'user-supplied benchmark units; no meter transform',
                    'physical_navigation': False},
                'purpose': 'engineering_validation',
                'source_sha256': self._source['sha256'] if self._source else None}

    def _check_source(self):
        if not self._source or _source_manifest()['sha256'] != self._source['sha256']:
            raise StoreError('source_changed', '执行源码或依赖版本已改变，请重启服务后重新提交。', 409)

    def submit(self, request):
        if not isinstance(request, dict) or set(request)-set(PAYLOAD_FIELDS)-{'scene_id','scene_revision'}:
            raise StoreError('invalid_job', '任务仅接受CVRP数值字段、算法、预算、种子和场景身份。')
        if not set(PAYLOAD_FIELDS) <= set(request):
            raise StoreError('invalid_job', '任务缺少必需的CVRP字段。')
        if self._runner is None:
            raise StoreError('runner_unavailable', self._unavailable_reason or 'CVRP执行模块不可用。', 503)
        payload = {key: request[key] for key in PAYLOAD_FIELDS}
        if not isinstance(payload['algorithm'],str) or payload['algorithm'] not in ALGORITHMS:
            raise StoreError('invalid_job', '此任务API尚未注册所选算法。')
        try:
            self._validate_payload(payload)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise StoreError('invalid_job', 'CVRP输入无效：'+str(exc)) from exc
        if len(payload['coordinates']) > MAX_NODES or payload['budget_seconds'] not in (1, 5):
            raise StoreError('invalid_job', '任务上限300节点（含仓库），预算仅1或5秒。')
        self._check_source()
        option = next(row for row in self.algorithms()['algorithms'] if row['id'] == payload['algorithm'])
        if not option['available']:
            raise StoreError('algorithm_unavailable', option['reason'] or '算法当前不可用。', 422)
        # Detach lists from the caller before hashing and queuing.
        payload = json.loads(canonical(payload))
        problem = {key: payload[key] for key in PROBLEM_FIELDS}
        input_sha = self._digest(problem)
        scene_id = identifier(request.get('scene_id', 'cvrp-'+input_sha[:32]))
        revision = revision_number(request.get('scene_revision', 1))
        if revision is None:
            raise StoreError('invalid_revision', '场景版本必须为正整数。')
        job_id, created = uuid.uuid4().hex, now()
        with self._submission_lock:
            if self._stopping.is_set():
                raise StoreError('runner_unavailable', '任务服务正在停止。', 503)
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                pending = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
                if pending >= MAX_PENDING:
                    raise StoreError('job_queue_full', '任务队列已满，请等待已有任务结束。', 503)
                scene = db.execute('SELECT input_sha256 FROM job_scenes WHERE scene_id=? AND revision=?',
                                   (scene_id, revision)).fetchone()
                if scene and scene['input_sha256'] != input_sha:
                    raise StoreError('scene_revision_conflict', '同一场景版本不能对应不同输入；请增加scene_revision。', 409)
                if not scene:
                    db.execute('INSERT INTO job_scenes VALUES(?,?,?,?,?)',
                               (scene_id, revision, input_sha, canonical(problem), created))
                db.execute('''INSERT INTO jobs
                    (id,status,scene_id,scene_revision,input_sha256,payload_sha256,
                     source_sha256,source_manifest,request,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?)''',
                    (job_id, 'queued', scene_id, revision, input_sha, self._digest(payload),
                     self._source['sha256'], canonical(self._source), canonical(payload), created))
            # The 202 receipt refers to the committed queued attempt, even if the
            # worker claims it immediately after notification.
            try:
                receipt = self.get(job_id)
            finally:
                # A committed attempt must remain runnable even if reading its
                # HTTP receipt encounters a transient storage error.
                self._wake.set()
        return receipt

    def get(self, job_id):
        identifier(job_id)
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise StoreError('not_found', '没有这个任务。', 404)
        result = dict(row)
        for key in ('request','source_manifest','result','error'):
            result[key] = json.loads(result[key]) if result[key] is not None else None
        result['feasible'] = bool(result['feasible']) if result['feasible'] is not None else None
        return {'schema_version': 1, 'purpose': 'engineering_validation', **result}

    def list(self, limit=100, offset=0):
        if type(limit) is not int or not 1 <= limit <= 500 or type(offset) is not int or not 0 <= offset <= 2**31-1:
            raise StoreError('invalid_pagination', 'limit须为1到500，offset须为非负整数。')
        with self.store.connect() as db:
            db.execute('BEGIN')
            counts = {row['status']: row['n'] for row in db.execute('SELECT status,COUNT(*) AS n FROM jobs GROUP BY status')}
            rows = db.execute('''SELECT sequence,id,status,scene_id,scene_revision,input_sha256,
                payload_sha256,source_sha256,request,created_at,started_at,finished_at,error,
                result_status,feasible,cost FROM jobs ORDER BY sequence DESC LIMIT ? OFFSET ?''',
                (limit, offset)).fetchall()
        jobs = []
        for row in rows:
            item = dict(row)
            request = json.loads(item.pop('request'))
            item.update({key: request[key] for key in ('algorithm','seed','budget_seconds')})
            item['node_count'] = len(request['coordinates'])
            item['error'] = json.loads(item['error']) if item['error'] else None
            item['feasible'] = bool(item['feasible']) if item['feasible'] is not None else None
            jobs.append(item)
        all_counts = {state: counts.get(state, 0) for state in ('queued','running',*TERMINAL_STATES)}
        return {'schema_version': 1, 'purpose': 'engineering_validation', 'jobs': jobs,
                'counts': all_counts, 'total': sum(counts.values()), 'limit': limit, 'offset': offset}

    def _interrupt_pending(self, code, message):
        with self.store.connect() as db:
            db.execute("UPDATE jobs SET status='interrupted',finished_at=?,error=? WHERE status IN ('queued','running')",
                       (now(), canonical({'code': code, 'message': message})))

    def _claim(self):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT id,request FROM jobs WHERE status='queued' ORDER BY sequence LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET status='running',started_at=? WHERE id=? AND status='queued'", (now(),row['id']))
        return row['id'], json.loads(row['request'])

    def _execute(self, job_id, payload):
        result, error, state = None, None, 'failed'
        try:
            with _RUN_LOCK:
                self._check_source()
                result = self._runner(payload)
            if (not isinstance(result, dict) or type(result.get('feasible')) is not bool
                    or not isinstance(result.get('status'), str) or not result['status']):
                raise StoreError('invalid_runner_result', '执行结果缺少明确的状态或布尔可行性。', 500)
            if (result['status'] in {'OK', 'TIMEOUT_WITH_FEASIBLE_INCUMBENT'} and not result['feasible']
                    or result['status'] in {'NO_SOLUTION', 'TIMEOUT_NO_SOLUTION'} and result['feasible']):
                raise StoreError('invalid_runner_result', '执行结果状态与可行性矛盾。', 500)
            cost = result.get('cost')
            if (result['feasible'] and (type(cost) is not int or not 0 <= cost <= 2**63-1)
                    or not result['feasible'] and cost is not None):
                raise StoreError('invalid_runner_result', '执行结果成本必须与可行性一致。', 500)
            if result.get('payload_sha256') != self._digest(payload):
                raise StoreError('result_identity_mismatch', '执行结果不属于已接受的输入。', 409)
            if result.get('status') in {'OK','TIMEOUT_WITH_FEASIBLE_INCUMBENT','TIMEOUT_NO_SOLUTION','NO_SOLUTION'}:
                state = 'completed'
            else:
                error = {'code': result.get('status','RUNNER_ERROR'),
                         'message': '数值执行未成功；完整状态与checker证据保存在result中。'}
            canonical(result)
        except Exception as exc:
            # A failed attempt still has its accepted payload and source identity.
            state = 'failed'
            error = {'code': getattr(exc, 'code', 'RUNNER_ERROR'),
                     'type': type(exc).__name__, 'message': str(exc)}
            result = None
        with self.store.connect() as db:
            db.execute('''UPDATE jobs SET status=?,finished_at=?,result=?,error=?,
                result_status=?,feasible=?,cost=? WHERE id=? AND status='running' ''',
                (state, now(), canonical(result) if result is not None else None,
                 canonical(error) if error else None, result.get('status') if result else None,
                 int(result['feasible']) if result is not None else None,
                 result.get('cost') if result else None, job_id))

    def _work(self):
        try:
            while not self._stopping.is_set():
                self._wake.wait()
                self._wake.clear()
                while not self._stopping.is_set():
                    claim = self._claim()
                    if claim is None:
                        break
                    self._execute(*claim)
        except Exception as exc:
            self._worker_failure = type(exc).__name__+': '+str(exc)
            self._stopping.set()
            try:
                self._interrupt_pending('WORKER_STOPPED', '执行服务异常停止；任务记录已保留。')
            except Exception:
                # A storage outage is recovered from the durable running rows at
                # the next successful startup; do not invent completion records.
                pass

    def close(self):
        with self._submission_lock:
            if self._closed:
                return
            self._closed = True
            self._stopping.set()
            self._wake.set()
        if self._thread:
            self._thread.join()
        try:
            self._interrupt_pending('SERVER_STOPPED', '服务停止，尚未执行的任务已保留为中断。')
        finally:
            if self._lock_file:
                self._lock_file.close()

