"""Service registry (CONTRACT §2.6): gateway, archive backend, solver/ROS/Fields2Cover checks,
the ROS simulation session and its RViz / Gazebo GUI windows, plus the log registry."""
from __future__ import annotations

import importlib.metadata
import json
import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from . import paths
from .events import EventBus, utc_now
from .jobs import new_id, worker_env

log = logging.getLogger('urp_lab.services')

ARCHIVE_PORT = 4180
SIM_PORT = 4191
ROS_SETUP = Path('/opt/ros/humble/setup.bash')
ROS_SHARE = Path('/opt/ros/humble/share')
SIM_SCRIPT_REL = 'src/lab/ros/urp_sim_session.py'
WORLDS = ('warehouse', 'depot', 'maze')
SIM_STOP_WAIT_S = 120.0
SIM_ESCALATION = ((signal.SIGINT, 15.0), (signal.SIGTERM, 10.0), (signal.SIGKILL, 5.0))
BRIDGE_CRASH = re.compile(r'free\(\): corrupted|corrupted unsorted chunks|process has died.*exit code -6|exit code -6')
PHASE_TEXT = {'stopped': '已停止', 'starting': '启动中', 'undocking': '离开充电座', 'localizing': '定位初始化',
              'nav2_starting': 'Nav2 启动中', 'ready': '就绪', 'executing': '执行中', 'stopping': '关闭中', 'failed': '失败'}
PHASE_SERVICE = {'starting': 'starting', 'undocking': 'starting', 'localizing': 'starting', 'nav2_starting': 'starting',
                 'ready': 'running', 'executing': 'running', 'stopping': 'stopping', 'failed': 'failed',
                 'stopped': 'stopping'}
SERVICE_STATES = {'running', 'stopped', 'starting', 'stopping', 'ready', 'degraded', 'failed', 'unavailable'}


def bridge_known_issues() -> list[str]:
    option = os.environ.get('URP_BRIDGE_FIX', '1')
    if option == '0':
        return ['URP_BRIDGE_FIX=0：使用未修复的系统 parameter_bridge，关闭竞态仍存在；仅供隔离对照。']
    if option != '1':
        return ['URP_BRIDGE_FIX 必须是 0 或 1；当前配置会阻止仿真启动。']
    return []


def bridge_note() -> str:
    return '；'.join(bridge_known_issues()) or (
        '已选择 parameter_bridge 源码关闭修复，启动时校验源码与依赖指纹；'
        '等待 ROS 信号线程结束后正常析构，不使用 _exit。')


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def http_json(method: str, url: str, timeout: float = 1.0, **kw):
    """Small JSON request helper; returns (status, data) or (None, error text)."""
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            r = client.request(method, url, **kw)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, None
    except httpx.HTTPError as exc:
        return None, f'{type(exc).__name__}: {exc}'


def pid_listening(port: int) -> int | None:
    """PID of a local process listening on 127.0.0.1:port (only own-user processes are visible)."""
    inodes = set()
    for table in ('/proc/net/tcp', '/proc/net/tcp6'):
        try:
            rows = Path(table).read_text().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            parts = row.split()
            if len(parts) > 9 and parts[3] == '0A' and int(parts[1].rsplit(':', 1)[1], 16) == port:
                inodes.add(f'socket:[{parts[9]}]')
    if not inodes:
        return None
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            for fd in (proc / 'fd').iterdir():
                if os.readlink(fd) in inodes:
                    return int(proc.name)
        except OSError:
            continue
    return None


def processes_with_env(key: str, value: str) -> list[int]:
    """Own processes whose environment carries key=value (finds session children in other process groups)."""
    needle = f'{key}={value}'.encode()
    found = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            if needle in (proc / 'environ').read_bytes().split(b'\0'):
                found.append(int(proc.name))
        except OSError:
            continue
    return found


def session_identity(pid: int, sid: str, runtime: Path) -> int | None:
    """Identify a live supervisor in this runtime, including its Linux start time."""
    proc = Path('/proc') / str(pid)
    try:
        stat = (proc / 'stat').read_text()
        fields = stat[stat.rfind(')') + 2:].split()
        if fields[0] == 'Z' or f'URP_SIM_SESSION={sid}'.encode() not in (proc / 'environ').read_bytes().split(b'\0'):
            return None
        args = (proc / 'cmdline').read_bytes().decode().split('\0')
        actual = Path(args[args.index('--runtime') + 1])
        if not actual.is_absolute():
            actual = (proc / 'cwd').resolve() / actual
        if actual.resolve() != (runtime / 'sim' / sid).resolve():
            return None
        return int(fields[19])
    except (OSError, ValueError, IndexError):
        return None


def tail_lines(path: Path, n: int) -> list[str]:
    n = max(1, min(int(n), 5000))
    with path.open('rb') as f:
        f.seek(0, os.SEEK_END)
        end = pos = f.tell()
        data = b''
        while pos > 0 and data.count(b'\n') <= n:
            step = min(65536, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step) + data
            if end - pos > 8 * 1024 * 1024:
                break
    lines = data.decode('utf-8', 'replace').splitlines()
    return lines[-n:]


def _version(dist: str) -> str | None:
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return None


def _run(cmd: list[str], timeout: float, **kw) -> tuple[int | None, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)


PROBE = r'''
import importlib, importlib.metadata as md, json, sys
out = {"python": sys.version.split()[0], "packages": {}, "research": {}, "catalog": None, "f2c": None}
for d in ("fastapi", "uvicorn", "pyvrp", "ortools", "shapely", "numpy", "scipy", "httpx", "pillow"):
    try: out["packages"][d] = md.version(d)
    except md.PackageNotFoundError: out["packages"][d] = None
for m in ("urp_research.turn_modes.solver", "urp_research.turn_modes.oracle", "urp_research.turn_modes.checker",
          "urp_research.turn_modes.evaluate_candidate", "urp_research.turn_modes.generate_data",
          "urp_research.cvrp_selection.experiment"):
    try: importlib.import_module(m); out["research"][m] = None
    except Exception as e: out["research"][m] = f"{type(e).__name__}: {e}"
try:
    from urp_lab.solvers import catalog
    out["catalog"] = [{k: a.get(k) for k in ("id", "name", "source", "available", "unavailable_reason")} for a in catalog()]
except Exception as e:
    out["catalog_error"] = f"{type(e).__name__}: {e}"
try:
    from urp_lab.solvers import f2c_coverage
    r = f2c_coverage.available()
    if isinstance(r, dict): out["f2c"] = r
    elif isinstance(r, (tuple, list)): out["f2c"] = {"available": bool(r[0]), "reason": r[1] if len(r) > 1 else None}
    else: out["f2c"] = {"available": bool(r), "reason": None}
    try: out["f2c"].setdefault("version", md.version("fields2cover"))
    except Exception: pass
except Exception as e:
    out["f2c"] = {"available": False, "reason": f"{type(e).__name__}: {e}"}
print(json.dumps(out, ensure_ascii=False))
'''


def probe_lab() -> dict:
    python = paths.LAB / '.venv' / 'bin' / 'python'
    code, out = _run([str(python if python.exists() else sys.executable), '-c', PROBE], 120,
                     cwd=paths.LAB, env=worker_env())
    for line in reversed(out.splitlines()):
        if line.startswith('{'):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                break
    return {'error': f'探测进程失败（返回码 {code}）：{out[-600:]}'}


def solver_service(probe: dict) -> dict:
    facts, missing, issues = [], [], []
    if probe.get('error'):
        return _service('solver', '求解环境', 'check', 'failed', probe['error'])
    facts.append({'label': 'Python', 'value': probe.get('python') or '?'})
    for name, ver in (probe.get('packages') or {}).items():
        facts.append({'label': name, 'value': ver or '缺失'})
        if ver is None:
            missing.append(name)
    bad = {m: e for m, e in (probe.get('research') or {}).items() if e}
    facts.append({'label': '研究模块', 'value': f"{len(probe.get('research') or {}) - len(bad)}/{len(probe.get('research') or {})} 可导入"})
    for m, e in bad.items():
        issues.append(f'{m} 无法导入：{e}')
    node = shutil.which('node')
    rc, ver = _run([node, '--version'], 10) if node else (None, '')
    facts.append({'label': 'Node', 'value': ver if rc == 0 else '未找到（JS 基线不可用）'})
    js = paths.REPO / 'src/frontend/core/routing/cvrp-solver.js'
    facts.append({'label': '项目 JS 求解器', 'value': str(js.relative_to(paths.REPO)) if js.exists() else '缺失'})
    catalog = probe.get('catalog')
    if catalog is None:
        issues.append(f"算法目录不可用：{probe.get('catalog_error', '未知原因')}")
    else:
        ok = [a for a in catalog if a.get('available')]
        facts.append({'label': '可用算法', 'value': f'{len(ok)}/{len(catalog)}'})
        for a in catalog:
            if not a.get('available'):
                issues.append(f"{a['id']} 不可用：{a.get('unavailable_reason') or '未说明'}")
    if missing or catalog is None:
        state = 'failed'
        detail = '缺少软件包：' + ', '.join(missing) if missing else '算法目录无法加载'
    elif bad or rc != 0 or not js.exists() or any(not a.get('available') for a in catalog):
        state, detail = 'degraded', '部分算法或模块不可用，详见说明'
    else:
        state, detail = 'ready', f'{len(catalog)} 个算法可用；每次求解独立子进程'
    return _service('solver', '求解环境', 'check', state, detail, facts=facts, known_issues=issues)


def f2c_service(probe: dict) -> dict:
    f2c = probe.get('f2c') or {}
    if probe.get('error'):
        return _service('fields2cover', 'Fields2Cover', 'check', 'unavailable', probe['error'])
    facts = [{'label': '版本', 'value': str(f2c['version'])}] if f2c.get('version') else []
    if f2c.get('available'):
        return _service('fields2cover', 'Fields2Cover', 'check', 'ready', '第三方覆盖路径库可用（f2c.* 算法）', facts=facts)
    return _service('fields2cover', 'Fields2Cover', 'check', 'unavailable',
                    f"不可用：{f2c.get('reason') or '未说明'}；覆盖任务仍可用面板基线 panel.boustrophedon", facts=facts)


def ros_service() -> dict:
    facts, problems, optional = [], [], []
    if not ROS_SETUP.exists():
        return _service('ros', 'ROS 2 Humble', 'check', 'unavailable', '未找到 /opt/ros/humble/setup.bash')
    script = (f'source {ROS_SETUP} && exec bash setup/profile.sh simulation /usr/bin/python3 -c '
              + shlex.quote('import rclpy, os; print(os.environ.get("ROS_DISTRO"), os.environ.get("RMW_IMPLEMENTATION"))'))
    rc, out = _run(['bash', '-c', script], 30, cwd=paths.REPO)
    if rc == 0:
        distro, rmw = (out.splitlines()[-1].split() + ['?', '?'])[:2]
        facts += [{'label': 'rclpy', 'value': f'可导入（{distro}，/usr/bin/python3）'}, {'label': 'RMW', 'value': rmw}]
    else:
        problems.append(f'rclpy 无法导入：{out[-300:]}')
    ign = shutil.which('ign')
    rc, out = _run([ign, 'gazebo', '--versions'], 30) if ign else (None, '未找到 ign')
    if rc == 0 and out:
        facts.append({'label': 'Gazebo', 'value': f'Fortress（ign gazebo {out.splitlines()[-1]}）'})
    else:
        problems.append(f'Gazebo 不可用：{out[-200:]}')
    for pkg in ('turtlebot4_ignition_bringup', 'turtlebot4_navigation', 'nav2_bringup', 'ros_gz_bridge', 'rviz2'):
        if not (ROS_SHARE / pkg).exists():
            problems.append(f'缺少 ROS 包 {pkg}')
    xml = ROS_SHARE / 'ros_gz_bridge/package.xml'
    if xml.exists():
        m = re.search(r'<version>([^<]+)</version>', xml.read_text())
        facts.append({'label': 'ros_gz_bridge', 'value': m.group(1) if m else '?'})
    worlds = [w for w in WORLDS if (ROS_SHARE / f'turtlebot4_navigation/maps/{w}.yaml').exists()]
    facts.append({'label': '官方地图', 'value': ', '.join(worlds) or '无'})
    facts.append({'label': '图形显示', 'value': os.environ.get('DISPLAY') or '无 DISPLAY（无法打开 Gazebo/RViz 窗口）'})
    if not os.environ.get('DISPLAY'):
        optional.append('无 DISPLAY：只能无界面仿真')
    if not (paths.REPO / SIM_SCRIPT_REL).exists():
        optional.append(f'仿真会话脚本 {SIM_SCRIPT_REL} 不存在')
    if problems:
        state = 'unavailable' if any('rclpy' in p for p in problems) else 'degraded'
        return _service('ros', 'ROS 2 Humble', 'check', state, '；'.join(problems), facts=facts,
                        known_issues=problems[1:] + optional)
    state = 'degraded' if optional else 'ready'
    return _service('ros', 'ROS 2 Humble', 'check', state,
                    '；'.join(optional) or 'ROS 2 Humble + Gazebo Fortress + Nav2 + TurtleBot4 仿真可用',
                    facts=facts + [{'label': '桥接关闭', 'value': bridge_note()}],
                    known_issues=optional + bridge_known_issues())


def _service(sid, name, kind, state, detail, **kw) -> dict:
    out = {'id': sid, 'name': name, 'kind': kind, 'state': state, 'detail': detail, 'pid': None, 'port': None,
           'url': None, 'started_utc': None, 'controls': [], 'log': None, 'facts': [], 'known_issues': []}
    out.update(kw)
    return out


class ManagedProcess:
    def __init__(self, name: str, argv: list[str], cwd: Path, log_path: Path, env: dict | None = None):
        self.name, self.argv, self.cwd, self.log_path, self.env = name, argv, cwd, log_path, env
        self.proc: subprocess.Popen | None = None
        self.started_utc: str | None = None
        self.started_at = 0.0
        self.stop_requested = False

    def start(self):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open('ab') as f:
            f.write(f'\n=== {utc_now()} 网关启动 {self.name}: {shlex.join(self.argv)} ===\n'.encode())
            self.proc = subprocess.Popen(self.argv, cwd=self.cwd, env=self.env, stdin=subprocess.DEVNULL,
                                         stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
        self.started_utc, self.started_at = utc_now(), time.monotonic()

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def returncode(self):
        return None if self.proc is None else self.proc.poll()

    def note(self, text: str):
        try:
            with self.log_path.open('a', encoding='utf-8') as f:
                f.write(f'=== {utc_now()} 网关：{text}\n')
        except OSError:
            pass

    def signal_group(self, sig) -> bool:
        try:
            os.killpg(self.proc.pid, sig)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    def wait(self, timeout: float, hurry: threading.Event | None = None) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not self.alive:
                return True
            if hurry is not None and hurry.is_set():
                end = min(end, time.monotonic() + 1.0)
            time.sleep(0.2)
        return not self.alive


class ServiceManager:
    def __init__(self, bus: EventBus | None = None, *, port: int = 4100, runtime: Path | None = None,
                 archive_url: str = f'http://127.0.0.1:{ARCHIVE_PORT}', sim_port: int = SIM_PORT,
                 jobs=None):
        self.bus, self.port, self.jobs = bus, port, jobs
        self.runtime = Path(runtime or paths.RUNTIME)
        self.logs_dir = self.runtime / 'logs'
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.archive_url, self.sim_port = archive_url.rstrip('/'), sim_port
        self.archive_port = urlsplit(self.archive_url).port or 80
        self.sim_url = f'http://127.0.0.1:{sim_port}'
        self.sim_client = None  # set by the gateway (simclient.SimClient)
        self.started_utc = utc_now()
        self.archive: ManagedProcess | None = None
        self.archive_failed: str | None = None
        self.sim: ManagedProcess | None = None
        self.sim_meta: dict | None = None      # {session_id, world, spawn, gui, rviz}
        self.sim_adopted: dict | None = None   # session found running on the port without a handle
        self.sim_stop_report: list[dict] = []
        self.sim_exit: str | None = None
        self._busy: dict[str, str] = {}
        self._checks: dict[str, tuple[float, dict]] = {}
        self._check_lock = threading.Lock()
        self._lock = threading.RLock()
        self._last_json = None
        self._stop = threading.Event()
        self.hurry = threading.Event()
        self._monitor: threading.Thread | None = None

    # ------------------------------------------------------------ checks --
    def _cached(self, key: str, ttl: float, compute, refresh: bool = False) -> dict | None:
        hit = self._checks.get(key)
        if hit and not refresh and time.monotonic() - hit[0] < ttl:
            return hit[1]
        if not self._check_lock.acquire(blocking=hit is None or refresh):
            return hit[1] if hit else None
        try:
            hit = self._checks.get(key)
            if hit and not refresh and time.monotonic() - hit[0] < ttl:
                return hit[1]
            value = compute()
            self._checks[key] = (time.monotonic(), value)
            return value
        finally:
            self._check_lock.release()

    def probe(self, refresh=False) -> dict:
        return self._cached('probe', 600, probe_lab, refresh)

    def ros(self, refresh=False) -> dict:
        return self._cached('ros', 60, ros_service, refresh)

    def refresh_checks_async(self):
        def run():
            self.probe(refresh=True)
            self.ros(refresh=True)
            self.changed()
        threading.Thread(target=run, name='urp-checks', daemon=True).start()

    def _pending_check(self, sid, name) -> dict:
        return _service(sid, name, 'check', 'starting', '正在检测…')

    # ----------------------------------------------------------- services --
    def services(self, wait_checks: bool = False) -> list[dict]:
        if wait_checks:
            probe, ros = self.probe(), self.ros()
        else:
            probe = self._checks.get('probe', (0, None))[1]
            ros = self._checks.get('ros', (0, None))[1]
        out = [self._gateway(), self._archive_service(),
               solver_service(probe) if probe else self._pending_check('solver', '求解环境'),
               f2c_service(probe) if probe else self._pending_check('fields2cover', 'Fields2Cover'),
               ros or self._pending_check('ros', 'ROS 2 Humble')]
        out += self._sim_services(ros)
        return out

    def _gateway(self) -> dict:
        dist = paths.FRONTEND_DIST / 'index.html'
        facts = [{'label': 'Python', 'value': sys.version.split()[0]},
                 {'label': 'FastAPI', 'value': _version('fastapi') or '?'},
                 {'label': '运行目录', 'value': str(self.runtime)},
                 {'label': '前端构建', 'value': time.strftime('%Y-%m-%d %H:%M', time.localtime(dist.stat().st_mtime))
                  if dist.exists() else '未构建'}]
        if self.jobs:
            a = self.jobs.active()
            facts.append({'label': '求解并发', 'value': str(a['concurrency'])})
        state, detail = ('running', f'127.0.0.1:{self.port}，提供面板、/lab/api 与 /api 代理') if dist.exists() else \
            ('degraded', '前端未构建：运行 bash setup/run.sh lab-setup')
        return _service('gateway', '工作台网关', 'process', state, detail, pid=os.getpid(), port=self.port,
                        url=f'http://127.0.0.1:{self.port}/', started_utc=self.started_utc, log='gateway', facts=facts)

    def _archive_service(self) -> dict:
        status, health = http_json('GET', f'{self.archive_url}/api/health', timeout=0.6)
        ok = status == 200 and isinstance(health, dict) and health.get('status') == 'ok'
        proc = self.archive
        ours = proc is not None and proc.alive
        base = dict(port=self.archive_port, url=f'{self.archive_url}/api/health', log='archive',
                    known_issues=[])
        facts = []
        if ok:
            facts += [{'label': '模式', 'value': str(health.get('mode'))},
                      {'label': '执行实验', 'value': str(health.get('executes_experiments'))}]
        busy = self._busy.get('archive')
        if busy:
            return _service('archive', '档案后端', 'process', busy, '处理中…', pid=proc.proc.pid if ours else None,
                            facts=facts, **base)
        if ok and ours:
            return _service('archive', '档案后端', 'process', 'running', '由网关启动；面板经 /api 同源代理访问',
                            pid=proc.proc.pid, started_utc=proc.started_utc, controls=['stop', 'restart'],
                            facts=facts, **base)
        if ok:
            return _service('archive', '档案后端', 'process', 'running',
                            '外部进程（非本网关启动），网关不会停止它', pid=pid_listening(self.archive_port), facts=facts, **base)
        if ours:
            if time.monotonic() - proc.started_at < 30:
                return _service('archive', '档案后端', 'process', 'starting', '等待健康检查…', pid=proc.proc.pid,
                                started_utc=proc.started_utc, controls=['stop'], **base)
            return _service('archive', '档案后端', 'process', 'degraded', f'进程在运行但健康检查失败：{health}',
                            pid=proc.proc.pid, started_utc=proc.started_utc, controls=['stop', 'restart'], **base)
        if proc is not None and not proc.stop_requested and proc.returncode not in (None, 0):
            return _service('archive', '档案后端', 'process', 'failed',
                            f'进程已退出（返回码 {proc.returncode}），见日志 archive', controls=['start'], **base)
        holder = pid_listening(self.archive_port)
        if holder:
            return _service('archive', '档案后端', 'process', 'degraded',
                            f'端口 {self.archive_port} 被进程 {holder} 占用但健康检查失败', pid=holder, **base)
        # Use the launcher's resolver rather than guessing a historical venv.
        # Only diagnose dependencies after live/failed states, retaining real exits.
        resolved = subprocess.run(['bash', 'src/start.sh', 'python-path'], cwd=paths.REPO,
                                  capture_output=True, text=True, timeout=5)
        if resolved.returncode:
            return _service('archive', '档案后端', 'process', 'unavailable',
                            resolved.stderr.strip() or 'Python 解释器不可用', **base)
        return _service('archive', '档案后端', 'process', 'stopped', '未运行；历史档案视图需要它', controls=['start'], **base)

    def sim_alive(self) -> bool:
        if self.sim is not None and self.sim.alive:
            return True
        adopted = self.sim_adopted
        return bool(adopted and session_identity(adopted['pid'], adopted['session_id'], self.runtime)
                    == adopted['start_ticks'])

    def _sim_services(self, ros: dict | None) -> list[dict]:
        state = self.sim_client.state if self.sim_client else None
        meta = self.sim_meta or self.sim_adopted or {}
        sid = meta.get('session_id')
        log_name = f'sim-{sid}' if sid and (self.logs_dir / f'sim-{sid}.log').exists() else None
        facts = []
        if meta:
            facts += [{'label': '会话', 'value': sid or '?'}, {'label': '世界', 'value': str(meta.get('world'))},
                      {'label': '出生点', 'value': json.dumps(meta.get('spawn'), ensure_ascii=False)}]
        for step in self.sim_stop_report:
            facts.append({'label': f"停止 {step['t'][11:19]}", 'value': step['text']})
        script = paths.REPO / SIM_SCRIPT_REL
        facts.append({'label': '桥接关闭', 'value': bridge_note()})
        common = dict(port=self.sim_port, log=log_name, facts=facts, known_issues=bridge_known_issues())
        pid = self.sim.proc.pid if self.sim is not None and self.sim.alive else (meta.get('pid') if self.sim_adopted else None)
        started = self.sim.started_utc if self.sim is not None else None
        busy = self._busy.get('sim')
        phase = (state or {}).get('phase') if state and state.get('session_id') else None
        if busy:
            sim = _service('sim', '仿真会话', 'process', busy, f"{PHASE_TEXT.get(busy, busy)}…", pid=pid,
                           started_utc=started, **common)
        elif self.sim_alive():
            sstate = PHASE_SERVICE.get(phase, 'starting')
            detail = f"{meta.get('world')} · {PHASE_TEXT.get(phase, '等待会话控制接口')}"
            if self.sim_adopted:
                detail += '（接管已在运行的会话）'
            errors = (state or {}).get('errors') or []
            if errors:
                detail += '；' + errors[-1]
            sim = _service('sim', '仿真会话', 'process', sstate, detail, pid=pid, started_utc=started,
                           controls=['stop', 'restart'] if sstate in ('running', 'failed') else ['stop'], **common)
        elif ros is not None and ros['state'] == 'unavailable':
            sim = _service('sim', '仿真会话', 'process', 'unavailable', f"ROS 不可用：{ros['detail']}", **common)
        elif not script.exists():
            sim = _service('sim', '仿真会话', 'process', 'unavailable', f'仿真会话脚本 {SIM_SCRIPT_REL} 不存在', **common)
        elif self.sim_exit:
            sim = _service('sim', '仿真会话', 'process', 'failed', self.sim_exit, controls=['start'], **common)
        else:
            detail = '未运行；可选择世界启动（官方 TurtleBot4 + Gazebo Fortress + Nav2）'
            if self.sim_stop_report:
                detail = '已停止；' + self.sim_stop_report[-1]['text']
            sim = _service('sim', '仿真会话', 'process', 'stopped', detail, controls=['start'], **common)
        out = [sim]
        components = (state or {}).get('components') or {}
        ready = self.sim_alive() and phase in ('ready', 'executing')
        for cid, key, name in (('rviz', 'rviz', 'RViz2'), ('gazebo_gui', 'gui', 'Gazebo 界面')):
            comp = components.get(key)
            if not self.sim_alive():
                out.append(_service(cid, name, 'process', 'unavailable', '仿真会话未运行', log=None))
                continue
            st = {'running': 'running', 'off': 'stopped', 'starting': 'starting', 'stopping': 'stopping',
                  'failed': 'failed'}.get(comp, 'degraded' if comp else 'starting')
            controls = [] if not ready else (['start'] if st in ('stopped', 'failed') else ['stop', 'restart'])
            busy = self._busy.get(cid)
            out.append(_service(cid, name, 'process', busy or st, f'会话组件状态：{comp or "未知"}',
                                controls=[] if busy else controls,
                                log=f'sim-{sid}/{key}.log' if sid and self._child_log(sid, key) else None))
        return out

    def _child_log(self, sid: str, stem: str) -> bool:
        return (self.runtime / 'sim' / sid / 'logs' / f'{stem}.log').exists()

    def get(self, sid: str) -> dict:
        for s in self.services():
            if s['id'] == sid:
                return s
        raise ServiceError(404, 'unknown_service', f'没有服务 {sid}')

    def changed(self):
        try:
            services = self.services()
        except Exception:
            log.exception('services snapshot failed')
            return
        text = json.dumps(services, ensure_ascii=False, sort_keys=True)
        if text != self._last_json:
            self._last_json = text
            if self.bus:
                self.bus.publish('services', {'services': services})

    def start_monitor(self, interval: float = 2.0):
        def loop():
            self.probe()
            self.ros()
            while not self._stop.is_set():
                self.changed()
                self._stop.wait(interval)
        self._monitor = threading.Thread(target=loop, name='urp-services', daemon=True)
        self._monitor.start()

    # ------------------------------------------------------------ actions --
    def action(self, sid: str, action: str, body: dict | None = None) -> list[dict]:
        body = body or {}
        if action not in ('start', 'stop', 'restart'):
            raise ServiceError(404, 'unknown_action', f'不支持的操作 {action}')
        current = self.get(sid)
        if action not in current['controls']:
            raise ServiceError(409, 'not_controllable', f"{current['name']} 当前（{current['state']}）不能执行 {action}")
        if sid == 'archive':
            {'start': self.start_archive, 'stop': self.stop_archive_async,
             'restart': self.restart_archive_async}[action]()
        elif sid == 'sim':
            if action == 'start':
                self.start_sim(body)
            elif action == 'stop':
                self.stop_sim_async()
            else:
                self.restart_sim_async(body)
        elif sid in ('rviz', 'gazebo_gui'):
            key = 'rviz' if sid == 'rviz' else 'gui'
            self.toggle_window_async(sid, key, action)
        self.changed()
        return self.services()

    def _set_busy(self, sid, state):
        with self._lock:
            if state:
                self._busy[sid] = state
            else:
                self._busy.pop(sid, None)
        self.changed()

    def start_archive(self):
        with self._lock:
            if self.archive is not None and self.archive.alive:
                return
            status, _ = http_json('GET', f'{self.archive_url}/api/health', timeout=0.6)
            if status == 200:
                return  # externally started backend: leave it alone
            self.archive = ManagedProcess('archive', ['bash', 'src/start.sh', 'backend'], paths.REPO,
                                          self.logs_dir / 'archive.log')
            self.archive.start()
            log.info('archive backend started pid=%s', self.archive.proc.pid)

    def stop_archive(self):
        proc = self.archive
        if proc is None or not proc.alive:
            return
        proc.stop_requested = True
        for sig, wait in ((signal.SIGINT, 5.0), (signal.SIGTERM, 5.0), (signal.SIGKILL, 3.0)):
            proc.note(f'发送 {sig.name} 到档案后端进程组 {proc.proc.pid}')
            proc.signal_group(sig)
            if proc.wait(wait):
                break
        proc.note(f'档案后端已退出，返回码 {proc.returncode}')
        log.info('archive backend stopped rc=%s', proc.returncode)

    def _background(self, sid, busy, fn, *args):
        def run():
            self._set_busy(sid, busy)
            try:
                fn(*args)
            except ServiceError as exc:
                log.warning('%s %s: %s', sid, busy, exc.message)
            except Exception:
                log.exception('%s %s failed', sid, busy)
            finally:
                self._set_busy(sid, None)
        t = threading.Thread(target=run, name=f'urp-{sid}-{busy}', daemon=True)
        t.start()
        return t

    def stop_archive_async(self):
        return self._background('archive', 'stopping', self.stop_archive)

    def restart_archive_async(self):
        def restart():
            self.stop_archive()
            self.start_archive()
        return self._background('archive', 'stopping', restart)

    # ---------------------------------------------------------- simulation --
    def sim_params(self, body: dict) -> dict:
        world = body.get('world') or 'warehouse'
        if world not in WORLDS or not (ROS_SHARE / f'turtlebot4_navigation/maps/{world}.yaml').exists():
            raise ServiceError(400, 'invalid_world', f'不支持的仿真世界 {world!r}（可选：{", ".join(WORLDS)}）')
        from .scenarios.maps import default_spawn
        spawn = body.get('spawn') or default_spawn(world)
        try:
            spawn = {k: float(spawn.get(k, 0.0)) for k in ('x', 'y', 'yaw')}
        except (TypeError, ValueError, AttributeError):
            raise ServiceError(400, 'invalid_spawn', '出生点需为 {"x","y","yaw"} 数值') from None
        import math
        from .scenarios.maps import load, dock_clearance
        if not all(math.isfinite(v) for v in spawn.values()):
            raise ServiceError(400, 'invalid_spawn', '出生点必须为有限数值')
        grid = load(world)
        if grid.clearance_at(spawn['x'], spawn['y']) < .225 or dock_clearance(grid,spawn)<.05:
            raise ServiceError(422, 'invalid_spawn', '出生点/充电座与地图障碍物冲突')
        from .worlds import inspect_world
        assets = inspect_world(world)
        if not assets['complete']:
            raise ServiceError(422, 'missing_world_assets', '世界资源不完整：' + '; '.join(assets['missing']))
        localization=body.get('localization','amcl')
        if localization not in ('ground_truth','amcl'):
            raise ServiceError(400,'invalid_localization','定位必须为 ground_truth 或 amcl')
        return {'world': world, 'spawn': spawn, 'gui': bool(body.get('gui', False)), 'rviz': bool(body.get('rviz', False)), 'localization':localization}

    def sim_command(self, session_id: str, p: dict, runtime_dir: Path) -> list[str]:
        s = p['spawn']
        args = ['--session-id', session_id, '--world', p['world'], '--localization',p.get('localization','ground_truth'), f"--spawn={s['x']:g},{s['y']:g},{s['yaw']:g}",
                '--runtime', str(runtime_dir), '--port', str(self.sim_port)]
        args += ['--gui'] if p['gui'] else []
        args += ['--rviz'] if p['rviz'] else []
        inner = (f'source {ROS_SETUP} && exec bash setup/profile.sh simulation /usr/bin/python3 {SIM_SCRIPT_REL} '
                 + shlex.join(args))
        return ['bash', '-lc', inner]

    def start_sim(self, body: dict) -> dict:
        p = self.sim_params(body)
        with self._lock:
            if self.sim_alive() or self._busy.get('sim'):
                raise ServiceError(409, 'sim_running', '已有仿真会话在运行或正在关闭；请先停止')
            field = getattr(self, 'field_sim', None)
            if field is not None and field.alive:
                raise ServiceError(409, 'fieldsim_running', '田间多车仿真正在运行，请先停止')
            ros = self.ros()
            if ros['state'] == 'unavailable':
                raise ServiceError(503, 'ros_unavailable', f"ROS 不可用：{ros['detail']}")
            if not (paths.REPO / SIM_SCRIPT_REL).exists():
                raise ServiceError(503, 'sim_unavailable', f'仿真会话脚本 {SIM_SCRIPT_REL} 不存在')
            status, st = http_json('GET', f'{self.sim_url}/state', timeout=0.8)
            if status is not None:
                raise ServiceError(409, 'sim_port_busy', f'端口 {self.sim_port} 已有进程响应（可能是未关闭的会话），请先停止它')
            session_id = new_id('sim')
            runtime_dir = self.runtime / 'sim' / session_id
            runtime_dir.mkdir(parents=True, exist_ok=True)
            env = dict(os.environ, URP_SIM_SESSION=session_id)
            self.sim = ManagedProcess(f'sim-{session_id}', self.sim_command(session_id, p, runtime_dir), paths.REPO,
                                      self.logs_dir / f'sim-{session_id}.log', env=env)
            self.sim_meta = {'session_id': session_id, **p}
            self.sim_adopted = None
            self.sim_exit = None
            self.sim_stop_report = []
            self.sim.start()
            log.info('sim session %s started pid=%s world=%s', session_id, self.sim.proc.pid, p['world'])
        self.changed()
        return self.sim_meta

    def adopt_sim(self, state: dict):
        """A session answers on the control port but this gateway has no handle (e.g. after a gateway crash)."""
        with self._lock:
            if self.sim is not None and self.sim.alive:
                return
            pid = pid_listening(self.sim_port)
            ticks = session_identity(pid, str(state.get('session_id')), self.runtime) if pid else None
            if ticks is None:
                return  # Another runtime is not ours to adopt or stop.
            self.sim = None  # A previous dead child cannot supply this owner's exit code.
            self.sim_adopted = {'session_id': state.get('session_id'), 'world': state.get('world'),
                                'spawn': state.get('spawn'), 'pid': pid, 'start_ticks': ticks}
            self.sim_meta = None
            self.sim_exit = None
            log.warning('adopted running sim session %s (pid %s)', state.get('session_id'), pid)
        self.changed()

    def note_sim_exit(self):
        """Called by the sim client when the session process is gone without a stop request."""
        with self._lock:
            if self.sim is not None and not self.sim.alive and not self.sim.stop_requested and self.sim_exit is None:
                rc = self.sim.returncode
                self.sim_exit = f'仿真会话进程意外退出（返回码 {rc}），见日志 sim-{self.sim_meta["session_id"]}'
                self.sim.note(self.sim_exit)
                log.warning(self.sim_exit)
            if self.sim_adopted is not None and not self.sim_alive():
                self.sim_exit = '接管的仿真会话已退出或身份改变；网关未持有原始退出码，请查看会话报告'
                self.sim_adopted = None

    def _record(self, text: str):
        entry = {'t': utc_now(), 'text': text}
        self.sim_stop_report.append(entry)
        if self.sim is not None:
            self.sim.note(text)
        log.info('sim stop: %s', text)
        self.changed()

    def stop_sim(self):
        meta = self.sim_meta or self.sim_adopted
        if not self.sim_alive():
            return
        sid = (meta or {}).get('session_id')
        self.sim_stop_report = []
        if self.sim is not None:
            self.sim.stop_requested = True
        t0 = time.monotonic()
        status, data = http_json('POST', f'{self.sim_url}/shutdown', timeout=5, json={})
        self._record(f'POST /shutdown → {status if status is not None else "无响应"}'
                     + (f'（{data}）' if status is None else ''))
        gone = self._sim_wait(SIM_STOP_WAIT_S)
        if gone:
            self._record(f'会话在 {time.monotonic() - t0:.1f} s 内退出，返回码 {self._sim_rc()}')
            if self._sim_rc() not in (0, None):
                self.sim_exit = f'会话关闭失败：真实退出码 {self._sim_rc()}，请检查 session-report.json'
        else:
            self._record(f'会话 {SIM_STOP_WAIT_S:.0f} s 内未退出，开始逐级发送信号')
            for sig, wait in SIM_ESCALATION:
                if self._sim_signal(sig):
                    self._record(f'发送 {sig.name} 到会话进程组')
                if self._sim_wait(wait):
                    self._record(f'{sig.name} 后 {wait:g} s 内退出，返回码 {self._sim_rc()}')
                    break
                self._record(f'{sig.name} 后 {wait:g} s 仍未退出')
        if sid:
            leftovers = processes_with_env('URP_SIM_SESSION', sid)
            if leftovers:
                self.sim_exit = '会话关闭后仍有残留进程，已进入强制清理；本次关闭不通过'
                self._record(f'会话退出后仍有 {len(leftovers)} 个子进程（{", ".join(map(str, leftovers[:8]))}），发送 SIGKILL')
                for pid in leftovers:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass
            self._scan_bridge_crash(sid)
            session_report = self.runtime / 'sim' / sid / 'session-report.json'
            if session_report.is_file():
                report_data = json.loads(session_report.read_text())
                if report_data.get('status') != 'PASS':
                    self.sim_exit = '会话清理未通过：' + str(session_report)
            else:
                self.sim_exit = '缺少会话关闭报告，不能确认干净退出'
            report = self.runtime / 'sim' / sid / 'gateway-stop.json'
            try:
                report.write_text(json.dumps({'session_id': sid, 'steps': self.sim_stop_report,
                                              'returncode': self._sim_rc()}, ensure_ascii=False, indent=1))
            except OSError:
                pass
        with self._lock:
            self.sim_adopted = None

    def _sim_rc(self):
        return self.sim.returncode if self.sim is not None else None

    def _sim_signal(self, sig) -> bool:
        if self.sim is not None and self.sim.alive:
            return self.sim.signal_group(sig)
        pid = (self.sim_adopted or {}).get('pid')
        if pid and self.sim_alive():
            try:
                os.kill(pid, sig)  # The verified supervisor owns its child cleanup.
                return True
            except OSError:
                return False
        return False

    def _sim_wait(self, timeout: float) -> bool:
        if self.sim is not None and self.sim.proc is not None and self.sim_adopted is None:
            return self.sim.wait(timeout, self.hurry)
        pid = (self.sim_adopted or {}).get('pid')
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if pid is None:
                if http_json('GET', f'{self.sim_url}/state', timeout=0.5)[0] is None:
                    return True
            elif not self.sim_alive():
                return True
            if self.hurry.is_set():
                end = min(end, time.monotonic() + 1.0)
            time.sleep(0.5)
        return False

    def _scan_bridge_crash(self, sid: str):
        files = [self.logs_dir / f'sim-{sid}.log'] + sorted((self.runtime / 'sim' / sid / 'logs').glob('*'))
        hits = []
        for f in files:
            try:
                if f.is_file() and f.stat().st_size < 64 * 1024 * 1024:
                    text = f.read_text(errors='replace')
                    if BRIDGE_CRASH.search(text) and 'bridge' in text:
                        hits.append(f.name)
            except OSError:
                continue
        if hits:
            self.sim_exit = '检测到桥接堆损坏/异常退出，本次关闭未通过，见日志：' + ', '.join(hits)
            self._record(self.sim_exit)

    def stop_sim_async(self):
        return self._background('sim', 'stopping', self.stop_sim)

    def restart_sim_async(self, body: dict):
        params = {**(self.sim_meta or {}), **(body or {})}
        p = self.sim_params(params)

        def restart():
            self.stop_sim()
            self._set_busy('sim', None)
            self.start_sim(p)
        return self._background('sim', 'stopping', restart)

    def toggle_window_async(self, sid: str, key: str, action: str):
        def run():
            if action in ('stop', 'restart'):
                self.sim_window(key, False)
            if action in ('start', 'restart'):
                self.sim_window(key, True)
        return self._background(sid, 'starting' if action != 'stop' else 'stopping', run)

    def sim_window(self, key: str, on: bool) -> dict:
        if not self.sim_alive():
            raise ServiceError(409, 'sim_not_running', '仿真会话未运行')
        status, data = http_json('POST', f'{self.sim_url}/{key}', timeout=60, json={'on': bool(on)})
        if status is None or status >= 400:
            msg = (data or {}).get('message') if isinstance(data, dict) else data
            raise ServiceError(502 if status is None else status, 'sim_session_error',
                               f'会话拒绝切换 {key}：{msg or status}')
        return data if isinstance(data, dict) else {}

    # ---------------------------------------------------------------- logs --
    def log_registry(self) -> dict[str, Path]:
        reg = {'gateway': self.logs_dir / 'gateway.log', 'archive': self.logs_dir / 'archive.log'}
        sims = sorted(self.logs_dir.glob('sim-*.log'), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in sims:
            sid = path.stem[4:]
            reg[path.stem] = path
            child = self.runtime / 'sim' / sid / 'logs'
            if child.is_dir():
                for f in sorted(child.iterdir()):
                    if f.is_file():
                        reg[f'sim-{sid}/{f.name}'] = f
        return reg

    def logs(self) -> list[dict]:
        out = []
        for name, path in self.log_registry().items():
            if path.exists():
                out.append({'log': name, 'path': str(path), 'bytes': path.stat().st_size})
        return out

    def log_path(self, name: str) -> Path:
        path = self.log_registry().get(name)
        if path is None:
            raise ServiceError(404, 'unknown_log', f'没有日志 {name}')
        return path

    def tail(self, name: str, n: int = 400) -> dict:
        path = self.log_path(name)
        if not path.exists():
            raise ServiceError(404, 'log_missing', f'日志 {name} 尚未生成')
        return {'log': name, 'path': str(path), 'lines': tail_lines(path, n)}

    # ------------------------------------------------------------ shutdown --
    def shutdown(self):
        self._stop.set()
        if self.sim_alive() and not self._busy.get('sim'):
            log.info('gateway exit: stopping simulation session')
            self.stop_sim()
        elif self._busy.get('sim'):
            end = time.monotonic() + SIM_STOP_WAIT_S + 40
            while self._busy.get('sim') and time.monotonic() < end:
                time.sleep(0.5)
        if self.archive is not None and self.archive.alive:
            log.info('gateway exit: stopping archive backend started by the gateway')
            self.stop_archive()
