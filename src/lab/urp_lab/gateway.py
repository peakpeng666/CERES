# CERES 核心网关：挂载前端静态资源、/lab/api REST 接口、SSE 事件流与底层评测代理
from __future__ import annotations

import asyncio
import os
import re
import importlib
import hashlib
import json
import logging
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from fastapi import Body, FastAPI, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import paths
from . import events as sse
from .events import EventBus, LogFollower
from .jobs import JobManager, SourceChangedError
from .services import ARCHIVE_PORT, SIM_PORT, ServiceError, ServiceManager
from .simclient import SimClient
from .solve_request import SolveRequestError, check_label, check_requested, resolve_algorithms
from .store import Store

log = logging.getLogger('urp_lab.gateway')

try:
    from . import __version__ as VERSION
except ImportError:  # pragma: no cover
    VERSION = '0.1.0'

MAX_BODY = 8 * 1024 * 1024
MAX_UPLOAD = 768 * 1024 * 1024
UPLOAD_PATH = re.compile(r'^/lab/api/assets/(inspect|[a-z0-9-]+/replace)$')
WRITE_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}
LOCAL_HOSTS = {'127.0.0.1', 'localhost', '::1', '[::1]'}
HOP_BY_HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer', 'trailers',
              'transfer-encoding', 'upgrade', 'content-length'}
ASSET_CACHE = 'public, max-age=31536000, immutable'
CATALOG_TTL_S = 30.0


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({'error': code, 'message': message}, status_code=status)


class _RequestBodyError(StarletteHTTPException):
    # FastAPI preserves HTTPException during body parsing; ordinary exceptions
    # would be rewritten as a generic 400 before reaching the guard.
    def __init__(self, status: int, code: str, message: str):
        super().__init__(status_code=status, detail=message)
        self.code = code


@dataclass
class Config:
    port: int = 4100
    runtime: Path | None = None
    dist: Path | None = None
    archive_url: str = f'http://127.0.0.1:{ARCHIVE_PORT}'
    sim_port: int = SIM_PORT
    field_sim_port: int = 4192
    archive_autostart: bool = False
    concurrency: int | None = None
    grace_s: float = 2.0
    worker_command: list[str] | None = None
    background: bool = True          # service monitor + sim poller threads (tests may disable)
    extra_hosts: set[str] = field(default_factory=set)
    bind_host: str = '127.0.0.1'
    admin_token: str = ''
    agent_model: Any = None          # research agent model (CONTRACT §9); None reads URP_LLM_* from the environment


class Lab:
    """Process-wide state shared by the endpoints."""

    def __init__(self, config: Config):
        self.config = config
        self.runtime = Path(config.runtime or paths.RUNTIME)
        self.dist = Path(config.dist or paths.FRONTEND_DIST)
        self.bus = EventBus()
        self.store = Store(self.runtime / 'lab.sqlite3')
        try:
            self.jobs = JobManager(self.store, self.bus, concurrency=config.concurrency, grace_s=config.grace_s,
                                   command=config.worker_command)
            self.services = ServiceManager(self.bus, port=config.port, runtime=self.runtime,
                                           archive_url=config.archive_url, sim_port=config.sim_port, jobs=self.jobs)
            self.sim = SimClient(self.store, self.bus, self.services)
            from .fieldsim import FieldSimManager
            self.fieldsim = FieldSimManager(self.store, self.services, self.runtime, port=config.field_sim_port)
            from .studies import StudyManager
            self.studies = StudyManager(self.store, self.jobs, self.bus)
            from .physics import PhysicsManager
            self.physics = PhysicsManager(self.store, self.runtime, self.bus, self.fieldsim)
            from .datapacks import PackManager
            self.packs = PackManager(self.runtime, self.bus)
            from .agent import AgentManager
            self.research_agent = AgentManager(self, model=config.agent_model)
        except BaseException:
            self.store.close()
            raise
        self._catalog: tuple[float, list[dict]] | None = None
        self._catalog_lock = threading.Lock()
        self.shutdown_event = threading.Event()
        self.agent = None  # created on first /lab/api/agent request (urp_lab.agent.runner.AgentRunner)

    def start(self):
        self.jobs.start()
        if self.config.background:
            self.services.start_monitor()
            self.sim.start()
        if self.config.archive_autostart:
            try:
                self.services.start_archive()
            except Exception:
                log.exception('archive autostart failed')
        else:
            if os.environ.get('URP_DESKTOP') == '1':   # the source tree's start script is not installed
                self.services.start_archive = lambda: self.packs.start_archive(self.services)
            self.packs.on_installed = lambda: self.packs.start_archive(self.services)
            self.packs.start_archive(self.services)

    def shutdown(self):
        try:
            if getattr(self, 'agent', None) is not None:
                self.agent.shutdown()
            self.research_agent.shutdown()
            self.jobs.shutdown()
            self.physics.shutdown()
            self.fieldsim.shutdown()
            self.services.shutdown()
            self.sim.stop()
            for run in self.store.active_sim_runs():
                self.sim._finish_local(run, 'failed', '网关退出时仿真会话已关闭，执行中断')
        finally:
            self.store.close()

    def catalog(self) -> list[dict]:
        with self._catalog_lock:
            if self._catalog and time.monotonic() - self._catalog[0] < CATALOG_TTL_S:
                return self._catalog[1]
            items = core('solvers').catalog()
            self._catalog = (time.monotonic(), items)
            return items


def core(name: str):
    """Core modules are imported lazily so a broken module degrades one endpoint, not the gateway."""
    try:
        return importlib.import_module(f'urp_lab.{name}')
    except Exception as exc:
        log.exception('core module %s failed to import', name)
        raise ApiError(503, 'core_unavailable', f'核心模块 urp_lab.{name} 无法加载：{type(exc).__name__}: {exc}') from exc


def scenario_or_404(scenario_id: str) -> dict:
    if not isinstance(scenario_id, str) or not scenario_id:
        raise ApiError(400, 'invalid_request', '缺少 scenario_id')
    try:
        return core('scenarios').get_scenario(scenario_id)
    except KeyError:
        raise ApiError(404, 'unknown_scenario', f'没有场景 {scenario_id}') from None


def instance_for(scenario: dict, instance: Any) -> dict:
    from .planning import validate_frame
    try:
        validate_frame(scenario['problem_class'], scenario.get('frame') or {})
    except ValueError as exc:
        raise ApiError(400, 'invalid_frame', str(exc)) from None
    if instance is None:
        return scenario['instance']
    if not isinstance(instance, dict):
        raise ApiError(400, 'invalid_instance', 'instance 须为 JSON 对象')
    try:
        return core('scenarios').validate_instance(scenario['problem_class'], instance)
    except ValueError as exc:
        raise ApiError(400, 'invalid_instance', str(exc)) from None


def attachment(data: bytes, media_type: str, filename: str) -> Response:
    ascii_name = filename.encode('ascii', 'replace').decode().replace('?', '_').replace('"', '')
    disposition = f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename)}'
    return Response(data, media_type=media_type, headers={'Content-Disposition': disposition, 'Cache-Control': 'no-store'})


def export_with(fn, *args) -> Response:
    try:
        data, media_type, filename = fn(*args)
    except ValueError as exc:
        raise ApiError(400, 'invalid_format', str(exc)) from None
    return attachment(data, media_type, filename)


def client_host(request: Request) -> str:
    return request.client.host if request.client is not None else ''


def is_loopback(host: str) -> bool:
    host = (host or '').strip().lower()
    if host.startswith('::ffff:'):
        host = host[7:]
    if host in {'127.0.0.1', '::1', 'localhost'}:
        return True
    try:
        import ipaddress
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def presented_admin_token(request: Request) -> str:
    header = request.headers.get('x-urp-admin-token', '')
    if header:
        return header
    auth = request.headers.get('authorization', '')
    prefix = 'Bearer '
    return auth[len(prefix):] if auth.startswith(prefix) else ''


def shutdown_permitted(request: Request, token: str) -> bool:
    """Loopback may shut the gateway down. A token, once set, is required for every client."""
    if token:
        return presented_admin_token(request) == token
    return is_loopback(client_host(request))


class Guard:
    """Host allow-list (DNS rebinding), cross-origin write rejection, JSON-only POST and body size limit."""

    def __init__(self, app, extra_hosts: set[str] = frozenset()):
        self.app = app
        self.hosts = LOCAL_HOSTS | set(extra_hosts)

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        denied = self.check(scope)
        if denied is not None:
            await denied(scope, receive, send)
            return
        headers = Headers(scope=scope)
        limit = MAX_UPLOAD if self.is_upload(scope, headers) else MAX_BODY
        received = 0
        failure = None
        response_started = False

        async def limited_receive():
            nonlocal received, failure
            if failure is not None:
                raise failure
            message = await receive()
            if message['type'] == 'http.request':
                received += len(message.get('body', b''))
                if received > limit:
                    failure = _RequestBodyError(413, 'payload_too_large', f'请求体超过 {limit // (1024 * 1024)} MiB')
                elif expected is not None and (received > expected or (not message.get('more_body', False) and received != expected)):
                    failure = _RequestBodyError(400, 'invalid_content_length', '请求体长度与 Content-Length 不一致')
                if failure is not None:
                    raise failure
            return message

        async def tracked_send(message):
            nonlocal response_started
            if message['type'] == 'http.response.start':
                response_started = True
            await send(message)

        try:
            expected = self.content_length(headers, limit)
            # Count only as the consumer reads: multipart files retain their
            # streaming/spooled handling instead of being buffered here.
            await self.app(scope, limited_receive, tracked_send)
        except _RequestBodyError as exc:
            if response_started:
                raise
            await error(exc.status_code, exc.code, exc.detail)(scope, receive, send)

    @staticmethod
    def is_upload(scope, headers):
        ctype = headers.get('content-type', '').split(';')[0].strip().lower()
        return (scope['method'] == 'POST' and ctype == 'multipart/form-data'
                and UPLOAD_PATH.match(scope['path']) is not None and headers.get('x-workbench-upload') == '1')

    @staticmethod
    def content_length(headers, limit):
        lengths = headers.getlist('content-length')
        if not lengths:
            return None
        length = lengths[0].strip(' \t')
        if len(lengths) != 1 or 'transfer-encoding' in headers or re.fullmatch(r'[0-9]+', length) is None:
            raise _RequestBodyError(400, 'invalid_content_length', 'Content-Length 须为唯一的非负整数，且不能与 Transfer-Encoding 同时声明')
        # Avoid converting an attacker-sized decimal string with int().
        normalized = length.lstrip('0') or '0'
        maximum = str(limit)
        if len(normalized) > len(maximum) or (len(normalized) == len(maximum) and normalized > maximum):
            raise _RequestBodyError(413, 'payload_too_large', f'请求体超过 {limit // (1024 * 1024)} MiB')
        return int(normalized)

    def check(self, scope):
        headers = Headers(scope=scope)
        host = headers.get('host', '')
        if urlsplit(f'//{host}').hostname not in {h.strip('[]') for h in self.hosts}:
            return error(403, 'invalid_host', f'拒绝的 Host：{host}（网关只服务本机地址）')
        method = scope['method']
        if scope['path'].startswith('/api/'):
            # The archive proxy rewrites Origin only after validating this boundary.
            # Preserve the archive's same-origin read/write policy through the gateway.
            origins = headers.getlist('origin')
            if (len(headers.getlist('host')) != 1 or len(origins) > 1
                    or headers.get('sec-fetch-site') == 'cross-site'):
                return error(403, 'cross_origin', '拒绝跨源档案请求')
            if origins and origins[0] != f'{scope.get("scheme", "http")}://{host}':
                return error(403, 'cross_origin', '拒绝跨源档案请求')
        if method not in WRITE_METHODS:
            return None
        origin = headers.get('origin')
        if origin is not None and (origin == 'null' or urlsplit(origin).netloc.lower() != host.lower()):
            return error(403, 'cross_origin', '拒绝跨源写入请求')
        ctype = headers.get('content-type', '').split(';')[0].strip().lower()
        # Asset file uploads are multipart; a custom header (which cross-site forms cannot send) keeps them CSRF-safe.
        upload = self.is_upload(scope, headers)
        if method != 'DELETE' and scope['path'].startswith('/lab/api/'):
            if ctype != 'application/json' and not upload:
                return error(415, 'unsupported_media_type', 'POST 只接受 application/json')
        return None


def create_app(config: Config | None = None) -> FastAPI:
    config = config or Config()
    archive = urlsplit(config.archive_url)
    archive_port = archive.port or (443 if archive.scheme == 'https' else 80)
    if archive.hostname in LOCAL_HOSTS and archive_port == config.port:
        raise RuntimeError('网关端口与档案后端地址冲突；请更换 --port 或 URP_ARCHIVE_URL')
    lab = Lab(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=2.0), trust_env=False)
        await run_in_threadpool(lab.start)
        log.info('gateway ready on %s:%s (runtime %s)', config.bind_host, config.port, lab.runtime)
        try:
            yield
        finally:
            lab.bus.close()
            await app.state.http.aclose()
            await asyncio.to_thread(lab.shutdown)
            log.info('gateway stopped')

    app = FastAPI(title='CERES 灵畴 · 农业机器人规划调度与自主执行工作台', lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url='/lab/api/openapi.json')
    app.state.lab = lab
    app.add_middleware(Guard, extra_hosts=config.extra_hosts)

    @app.exception_handler(ApiError)
    async def _api_error(request, exc: ApiError):
        return error(exc.status, exc.code, exc.message)

    @app.exception_handler(_RequestBodyError)
    async def _request_body_error(request, exc: _RequestBodyError):
        return error(exc.status_code, exc.code, exc.detail)

    @app.exception_handler(ServiceError)
    async def _service_error(request, exc: ServiceError):
        return error(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation(request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        where = '.'.join(str(x) for x in first.get('loc', ()) if x != 'body')
        return error(400, 'invalid_request', f"请求格式不正确：{where or '正文'} {first.get('msg', '')}".strip())

    @app.exception_handler(StarletteHTTPException)
    async def _http(request, exc: StarletteHTTPException):
        codes = {404: ('not_found', '没有这个接口或资源'), 405: ('method_not_allowed', '不支持该请求方法')}
        code, message = codes.get(exc.status_code, ('http_error', str(exc.detail)))
        return error(exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def _internal(request, exc: Exception):
        log.exception('unhandled error on %s %s', request.method, request.url.path)
        return error(500, 'internal_error', f'网关内部错误：{type(exc).__name__}: {exc}')

    def body_dict(payload: Any) -> dict:
        if payload is None:
            return {}
        if not isinstance(payload, dict):
            raise ApiError(400, 'invalid_request', '请求体须为 JSON 对象')
        return payload

    api = '/lab/api'

    # ------------------------------------------------------------ status --
    @app.get(f'{api}/health')
    def health():
        try:
            identity = json.loads((lab.runtime.parent / 'dataset.json').read_text(encoding='utf-8'))
            dataset_id = identity['dataset_id'] if identity.get('schema') == 'urp.dataset/1' else None
        except (OSError, ValueError, KeyError, TypeError):
            dataset_id = None
        return {'status': 'ok', 'product': 'CERES', 'executes_solvers': True,
                'runtime_id': hashlib.sha256(str(lab.runtime.resolve()).encode()).hexdigest(), 'dataset_id': dataset_id,
                'archive_required': config.archive_autostart}

    @app.get(f'{api}/fieldsim/physics')
    def field_physics_profile():
        from .field_physics import profile
        from .field_nonideal import GROUND_PRESETS, LOCALIZATION_PRESETS, PROVENANCE
        return {'default': profile(), 'override_keys': ['max_step_size_s', 'collision_detector', 'ground_friction', 'wheel_friction', 'payload_kg'],
                'execution': {'ground_presets': GROUND_PRESETS, 'localization_presets': LOCALIZATION_PRESETS,
                              'provenance': PROVENANCE,
                              'scope': '非理想执行：地面为摩擦分区代理、定位为合成误差模型；均未标定，不构成真实土壤或传感器结论'},
                'scope': 'synthetic rigid-ground simulation verification; not real-machine calibration'}

    @app.post(f'{api}/shutdown', status_code=202)
    def shutdown_gateway(request: Request):
        # Bound to loopback on the laptop, so any local client may close the process.
        # When the socket is reachable from another machine, only that loopback (or a
        # configured token) may call this. The panel does not use it; lab-stop does.
        if not shutdown_permitted(request, config.admin_token):
            raise ApiError(403, 'shutdown_forbidden', '关闭接口只接受本机连接')
        lab.shutdown_event.set()
        return {'accepted': True, 'message': '正在有序关闭求解、仿真与网关；日志保留'}

    def installed_rows(rows: list[dict]) -> list[dict]:
        """In an installed app the archive backend comes from the history pack, not from a source tree's virtual environment."""
        if os.environ.get('URP_DESKTOP') == '1':
            for row in rows:
                if row['id'] == 'archive' and row['state'] == 'unavailable':
                    if lab.packs.installed('history'):
                        row.update(state='stopped', detail='未运行；历史档案视图需要它', controls=['start'])
                    else:
                        row['detail'] = '下载历史档案后自动启动，见 设置 → 数据'
        return rows

    @app.get(f'{api}/services')
    def services(refresh: bool = False):
        if refresh:
            lab.services.refresh_checks_async()
        return {'services': installed_rows(lab.services.services())}

    @app.post(f'{api}/services/{{sid}}/{{action}}')
    def service_action(sid: str, action: str, payload: Any = Body(None)):
        return {'services': installed_rows(lab.services.action(sid, action, body_dict(payload)))}

    @app.get(f'{api}/logs')
    def logs():
        return {'logs': lab.services.logs()}

    @app.get(f'{api}/logs/{{name:path}}')
    def log_tail(name: str, tail: int = Query(400, ge=1, le=5000)):
        return lab.services.tail(name, tail)

    @app.get(f'{api}/events')
    async def events(request: Request, logs: str | None = None):
        followers = []
        for name in filter(None, (logs or '').split(',')):
            followers.append(LogFollower(name, lab.services.log_path(name)))
        initial = [('services', {'services': await run_in_threadpool(lab.services.services)}),
                   ('sim_state', lab.sim.current_state())]
        return StreamingResponse(sse.stream(lab.bus, initial, followers), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    # --------------------------------------------------------- scenarios --
    @app.get(f'{api}/scenarios')
    def scenarios():
        return {'scenarios': core('scenarios').list_scenarios()}

    @app.get(f'{api}/scenarios/{{scenario_id}}')
    def scenario(scenario_id: str):
        return scenario_or_404(scenario_id)

    def _existing_user_scenario(sid: Any) -> dict | None:
        if not isinstance(sid, str) or not sid.strip():
            return None
        try:
            found = core('scenarios').get_scenario(sid.strip().lower())
        except (KeyError, ValueError):
            return None
        return found if found.get('origin') == 'user' else None

    def _normalized_instance(pc: Any, instance: Any) -> Any:
        try:
            return core('scenarios').validate_instance(pc, instance)
        except (ValueError, KeyError, TypeError):
            return None

    def _same_import(module: Any, existing: dict, body: dict) -> bool:
        prepared = module.prepare_import(body)
        return existing.get('revision') == 1 and {**prepared, 'revision': 1} == existing

    def _same_fork(existing: dict, sc: dict, source_id: str) -> bool:
        instance = _normalized_instance(sc.get('problem_class'), sc.get('instance'))
        return (instance is not None and existing.get('revision') == 1
                and (existing.get('provenance') or {}).get('forked_from') == source_id
                and existing.get('problem_class') == sc.get('problem_class')
                and existing.get('title') == sc.get('title') and existing.get('instance') == instance)

    @app.post(f'{api}/scenarios', status_code=201)
    def import_scenario(payload: Any = Body(None)):
        module = core('scenarios')
        fn = getattr(module, 'import_scenario', None) or getattr(module, 'save_scenario', None)
        if fn is None:
            raise ApiError(501, 'not_implemented', '当前版本不支持导入场景')
        body = body_dict(payload)
        try:
            return fn(body)
        except module.ScenarioConflict as exc:
            # Idempotent retry: the same id with identical content is the earlier successful write.
            existing = _existing_user_scenario(body.get('id'))
            if existing is not None and _same_import(module, existing, body):
                return JSONResponse(module.summary(existing), status_code=200)
            raise ApiError(409, 'scenario_conflict', str(exc)) from None
        except ValueError as exc:
            raise ApiError(400, 'invalid_scenario', str(exc)) from None

    @app.get(f'{api}/maps/{{world}}.png')
    def map_png(world: str):
        try:
            data = core('scenarios').map_png(world)
        except (KeyError, FileNotFoundError, ValueError):
            raise ApiError(404, 'unknown_map', f'没有地图 {world}') from None
        return Response(data, media_type='image/png', headers={'Cache-Control': 'public, max-age=3600'})

    @app.get(f'{api}/maps/{{world}}')
    def map_meta(world: str):
        try:
            return core('scenarios').world_map(world)
        except (KeyError, FileNotFoundError, ValueError):
            raise ApiError(404, 'unknown_map', f'没有地图 {world}') from None

    # ------------------------------------------------------- algorithms --
    @app.get(f'{api}/algorithms')
    def algorithms():
        return {'algorithms': lab.catalog()}

    @app.get(f'{api}/algorithms/stats')
    def algorithm_stats():
        return {'stats': core('algorithm_stats').summaries(lab.store.run_digest())}

    @app.get(f'{api}/algorithms/{{algorithm_id}}/history')
    def algorithm_history(algorithm_id: str):
        if not any(a['id'] == algorithm_id for a in lab.catalog()):
            raise ApiError(404, 'unknown_algorithm', f'没有算法 {algorithm_id}')
        return core('algorithm_stats').history(lab.store.run_digest(), algorithm_id, lab.store.list_physics_runs(limit=2000))

    # ---------------------------------------------------------- physics --
    def physics_call(fn, *args, **kwargs):
        from .physics import PhysicsError
        try:
            return fn(*args, **kwargs)
        except PhysicsError as exc:
            raise ApiError(exc.status, exc.code, exc.message) from None

    @app.get(f'{api}/physics/engines')
    def physics_engines():
        return {'engines': [{'id': 'mujoco', 'name': 'MuJoCo', **core('physics').available()}], 'gazebo_gap': paths.gazebo_gap()}

    @app.post(f'{api}/physics/runs', status_code=202)
    def physics_submit(payload: Any = Body(None)):
        body = body_dict(payload)
        if not body.get('run_id') and not body.get('session_id'):
            raise ApiError(400, 'invalid_request', '需要 run_id 或 session_id')
        return physics_call(lab.physics.submit, run_id=body.get('run_id'), session_id=body.get('session_id'), traffic=body.get('traffic') or 'serial')

    @app.post(f'{api}/physics/batches', status_code=202)
    def physics_batch(payload: Any = Body(None)):
        body = body_dict(payload)
        if not body.get('run_id'):
            raise ApiError(400, 'invalid_request', '需要 run_id')
        unknown = set(body) - {'run_id', 'traffic', 'runs', 'seed', 'intensity', 'factors', 'wall_limit_s'}
        if unknown:
            raise ApiError(400, 'invalid_request', f'未知字段 {sorted(unknown)}')
        return physics_call(lab.physics.submit_batch, **body)

    @app.get(f'{api}/physics/runs')
    def physics_runs(run_id: str | None = None, limit: int = 100):
        return {'runs': lab.store.list_physics_runs(run_id, max(1, min(limit, 500)))}

    @app.get(f'{api}/physics/runs/{{physics_run_id}}')
    def physics_run(physics_run_id: str):
        rec = lab.physics.get(physics_run_id)
        if not rec:
            raise ApiError(404, 'unknown_physics_run', f'没有物理运行 {physics_run_id}')
        return rec

    @app.post(f'{api}/physics/runs/{{physics_run_id}}/cancel')
    def physics_cancel(physics_run_id: str):
        rec = lab.physics.cancel(physics_run_id)
        if not rec:
            raise ApiError(404, 'unknown_physics_run', f'没有物理运行 {physics_run_id}')
        return rec

    # ------------------------------------------------------ data packs --
    def pack_call(fn, *args):
        from .datapacks import PackError
        try:
            return fn(*args)
        except PackError as exc:
            raise ApiError(exc.status, exc.code, exc.message) from None

    @app.get(f'{api}/packs')
    def packs():
        return {'packs': lab.packs.list()}

    @app.post(f'{api}/packs/{{pack_id}}/install', status_code=202)
    def pack_install(pack_id: str):
        return pack_call(lab.packs.install, pack_id)

    @app.post(f'{api}/packs/{{pack_id}}/cancel')
    def pack_cancel(pack_id: str):
        return pack_call(lab.packs.cancel, pack_id)

    @app.post(f'{api}/packs/{{pack_id}}/remove')
    def pack_remove(pack_id: str):
        return pack_call(lab.packs.remove, pack_id)

    @app.post(f'{api}/recommend')
    def recommend(payload: Any = Body(None)):
        body = body_dict(payload)
        sc = scenario_or_404(body.get('scenario_id'))
        return core('recommend').recommend(sc, instance_for(sc, body.get('instance')))

    @app.post(f'{api}/planning/validate')
    def validate_planning_input(payload: Any = Body(None)):
        body = body_dict(payload)
        sc = scenario_or_404(body.get('scenario_id'))
        instance = instance_for(sc, body.get('instance'))
        from .planning import context, validate_frame
        try:
            validate_frame(sc['problem_class'], sc['frame'])
        except ValueError as exc:
            raise ApiError(400, 'invalid_frame', str(exc)) from None
        return {'instance': instance, 'planning': context(sc, instance)}

    @app.post(f'{api}/solve', status_code=202)
    def solve(payload: Any = Body(None)):
        body = body_dict(payload)
        sc = scenario_or_404(body.get('scenario_id'))
        instance = instance_for(sc, body.get('instance'))
        try:
            requested = check_requested(body.get('algorithms'))
            label = check_label(body.get('label'))
            resolved = resolve_algorithms(lab.catalog(), sc['problem_class'], requested)
        except SolveRequestError as exc:
            raise ApiError(exc.status, exc.code, exc.message) from None
        try:
            job_id, run_ids = lab.jobs.submit(sc, instance, resolved, label)
        except SourceChangedError as exc:
            raise ApiError(409, 'source_changed', str(exc)) from None
        except RuntimeError as exc:
            raise ApiError(503, 'shutting_down', str(exc)) from None
        return {'job_id': job_id, 'runs': run_ids}

    @app.get(f'{api}/jobs/{{job_id}}')
    def job(job_id: str):
        runs = lab.store.list_runs(job_id=job_id, limit=100)
        if not runs:
            raise ApiError(404, 'not_found', f'没有任务 {job_id}')
        return {'job_id': job_id, 'runs': [{'run_id': r['run_id'], 'algorithm_id': r['algorithm']['id'],
                                            'state': r['state'], 'status': r['status']} for r in runs]}

    @app.get(f'{api}/runs')
    def runs(scenario_id: str | None = None, limit: int = Query(100, ge=1, le=1000)):
        return JSONResponse({'runs': lab.store.list_runs(scenario_id=scenario_id, limit=limit)},
                            headers={'Cache-Control': 'no-store'})

    def run_or_404(run_id: str) -> dict:
        sol = lab.store.get_run(run_id)
        if sol is None:
            raise ApiError(404, 'not_found', f'没有运行记录 {run_id}')
        return sol

    @app.get(f'{api}/runs/{{run_id}}')
    def run(run_id: str):
        return JSONResponse(run_or_404(run_id), headers={'Cache-Control': 'no-store'})

    @app.post(f'{api}/runs/{{run_id}}/cancel')
    def cancel_run(run_id: str, payload: Any = Body(None)):
        run_or_404(run_id)
        return lab.jobs.cancel(run_id)

    @app.get(f'{api}/runs/{{run_id}}/export')
    def export_run(run_id: str, format: str = Query('json', description='json / csv (geometry) / tasks-csv (fleet task services) / geojson')):
        return export_with(core('export').export_run, run_or_404(run_id), format)

    @app.get(f'{api}/runs/{{run_id}}/record')
    def experiment_record(run_id: str):
        # Complete records are already JSON-native. Encode in this sync worker;
        # recursive encoding on the event loop can starve controls and health
        # requests when a history contains millions of trajectory samples.
        return JSONResponse(core('experiment').record(lab, run_or_404(run_id)),
                            headers={'Cache-Control': 'no-store'})

    @app.post(f'{api}/compare/export')
    def export_compare(payload: Any = Body(None)):
        body = body_dict(payload)
        ids = body.get('run_ids')
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
            raise ApiError(400, 'invalid_request', 'run_ids 须为非空字符串数组')
        return export_with(core('export').export_compare, [run_or_404(i) for i in ids], body.get('format', 'csv'))

    # ------------------------------------------------- field simulation --
    @app.get(f'{api}/fieldsim')
    def fieldsim_state():
        return lab.fieldsim.state()

    def require_gazebo():
        gap = paths.gazebo_gap()
        if gap:
            raise ApiError(503, 'simulator_unavailable', gap)

    @app.post(f'{api}/fieldsim', status_code=202)
    def fieldsim_start(payload: Any = Body(None)):
        require_gazebo()
        return lab.fieldsim.start(body_dict(payload))

    @app.post(f'{api}/fieldsim/stop', status_code=202)
    def fieldsim_stop():
        return lab.fieldsim.stop()

    @app.post(f'{api}/fieldsim/window')
    def fieldsim_window(payload: Any = Body(None)):
        body = body_dict(payload)
        return lab.fieldsim.window(str(body.get('kind')), bool(body.get('on', True)))

    @app.get(f'{api}/fieldsim/results')
    def fieldsim_results(run_id: str | None = None):
        return {'results': lab.fieldsim.results(run_id)}

    @app.get(f'{api}/fieldsim/results/{{session_id}}')
    def fieldsim_result(session_id: str, evidence: bool = False):
        result = lab.fieldsim.result(session_id)
        if evidence:
            # The live panel needs the verdict, not megabytes of sampled trajectories.
            # Keep the full endpoint unchanged for replay, download and independent checks.
            keys = ('session_id', 'run_id', 'state', 'check', 'physics', 'physics_audit', 'physics_runtime',
                    'truth_timing', 'energy', 'session_report', 'execution', 'execution_report', 'ground_contact_observations',
                    'traffic_mode', 'traffic', 'time_windows')
            result = {key: result.get(key) for key in keys}
        # Already JSON-native evidence. Serialize in this worker, avoiding FastAPI's
        # recursive encoder on the event loop while other clients control the session.
        return JSONResponse(result, headers={'Cache-Control': 'no-store'})

    # ------------------------------------------------------- simulation --
    @app.get(f'{api}/sim/state')
    def sim_state():
        return JSONResponse(lab.sim.current_state(), headers={'Cache-Control': 'no-store'})

    @app.post(f'{api}/sim/session', status_code=202)
    def sim_start(payload: Any = Body(None)):
        require_gazebo()
        lab.services.start_sim(body_dict(payload))
        return lab.sim.current_state()

    @app.delete(f'{api}/sim/session', status_code=202)
    def sim_stop():
        if lab.services.sim_alive() and not lab.services._busy.get('sim'):
            lab.services.stop_sim_async()
            time.sleep(0.05)
        return lab.sim.current_state()

    @app.post(f'{api}/sim/gui')
    def sim_gui(payload: Any = Body(None)):
        return lab.sim.set_window('gui', bool(body_dict(payload).get('on')))

    @app.post(f'{api}/sim/rviz')
    def sim_rviz(payload: Any = Body(None)):
        return lab.sim.set_window('rviz', bool(body_dict(payload).get('on')))

    @app.post(f'{api}/sim/runs', status_code=202)
    def sim_run_start(payload: Any = Body(None)):
        body = body_dict(payload)
        sol = run_or_404(body.get('run_id') or '')
        try:
            sc = sol.get('scenario_snapshot') or core('scenarios').get_scenario(sol['scenario_id'])
        except KeyError:
            raise ApiError(422, 'not_simulatable', f"运行所属场景 {sol['scenario_id']} 已不存在") from None
        return lab.sim.submit(sol, sc, body.get('mode') or 'follow_path', autostart=bool(body.get('autostart')),
                              gui=body.get('gui'), rviz=body.get('rviz'), budget_s=body.get('budget_s'),
                              time_basis=body.get('time_basis'), wall_watchdog_s=body.get('wall_watchdog_s'))

    @app.get(f'{api}/sim/runs')
    def sim_runs(run_id: str | None = None, limit: int = Query(100, ge=1, le=1000)):
        return JSONResponse({'sim_runs': lab.store.list_sim_runs(run_id=run_id, limit=limit)},
                            headers={'Cache-Control': 'no-store'})

    def sim_run_or_404(sim_run_id: str) -> dict:
        run = lab.sim.get_run(sim_run_id)
        if run is None:
            raise ApiError(404, 'not_found', f'没有仿真运行 {sim_run_id}')
        return run

    @app.get(f'{api}/sim/runs/{{sim_run_id}}')
    def sim_run(sim_run_id: str):
        return JSONResponse(sim_run_or_404(sim_run_id), headers={'Cache-Control': 'no-store'})

    @app.post(f'{api}/sim/runs/{{sim_run_id}}/cancel')
    def sim_run_cancel(sim_run_id: str, payload: Any = Body(None)):
        return lab.sim.cancel(sim_run_id)

    @app.get(f'{api}/sim/runs/{{sim_run_id}}/export')
    def sim_run_export(sim_run_id: str, format: str = 'json'):
        record = sim_run_or_404(sim_run_id)
        if format == 'json':
            record = {**record, 'planning_solution': run_or_404(record['run_id'])}
        return export_with(core('export').export_sim_run, record, format)

    # ------------------------------------------------- field operations --
    @app.get(f'{api}/agri/catalog')
    def agri_catalog():
        return core('agri.catalog').load()

    @app.post(f'{api}/agri/compile')
    def agri_compile(payload: Any = Body(None)):
        try:
            return core('agri.api').compile_preview(body_dict(payload))
        except (ValueError, KeyError, TypeError) as exc:
            raise ApiError(400, 'invalid_mission', str(exc)) from None

    @app.post(f'{api}/agri/missions', status_code=201)
    def agri_create(payload: Any = Body(None)):
        module = core('scenarios')
        try:
            scenario = core('agri.api').scenario(body_dict(payload))
            return module.import_scenario(scenario)
        except (ValueError, KeyError, TypeError) as exc:
            raise ApiError(400, 'invalid_mission', str(exc)) from None

    # ------------------------------------------------------- compose --
    @app.get(f'{api}/compose/examples')
    def compose_examples():
        return {'examples': core('compose').EXAMPLES}

    @app.post(f'{api}/compose/parse')
    def compose_parse(payload: Any = Body(None)):
        body = body_dict(payload)
        nl = core('compose.nl')
        parsed = nl.parse(str(body.get('text') or ''))
        spec, defaults = nl.fill_defaults(parsed['spec'])
        return {'spec': spec, 'found': parsed['found'], 'defaults': defaults, 'unsupported': parsed['unsupported'], 'cards': core('compose').describe(spec)}

    @app.post(f'{api}/compose', status_code=201)
    def compose(payload: Any = Body(None)):
        body = body_dict(payload)
        comp = core('compose')
        text = str(body.get('text') or '')[:600]
        seed = body.get('seed')
        try:
            if body.get('field_id'):
                sc = comp.from_field(str(body['field_id']), text, seed)
            else:
                sc = comp.compose(text, int(seed) if seed is not None else None, body.get('overrides') or None)
            return core('scenarios').save_user_scenario(sc, overwrite=False)
        except (ValueError, KeyError) as exc:
            raise ApiError(400, 'compose_failed', f'无法生成地图：{exc}') from None

    @app.post(f'{api}/fleet/lanes')
    def fleet_lanes(payload: Any = Body(None)):
        """Crop-row lanes of a (possibly unsaved) task map, for the editor overlay."""
        inst = body_dict(payload).get('instance')
        if not isinstance(inst, dict):
            raise ApiError(400, 'invalid_instance', '需要 instance（多车任务地图）')
        from shapely.errors import GEOSException
        try:
            return core('lanes').preview(inst)
        except (ValueError, KeyError, TypeError, GEOSException) as exc:
            raise ApiError(400, 'invalid_instance', f'无法生成作物行：{exc}') from None

    def user_scenario_or_403(scenario_id: str) -> dict:
        sc = scenario_or_404(scenario_id)
        if sc.get('origin') != 'user':
            raise ApiError(403, 'read_only', '内置或资源库场景不能直接修改，请先“另存为我的场景”')
        return sc

    @app.put(f'{api}/scenarios/{{scenario_id}}')
    def update_scenario(scenario_id: str, payload: Any = Body(None)):
        body = body_dict(payload)
        sc = user_scenario_or_403(scenario_id)
        revision = body.get('base_revision')
        if revision is not None and (isinstance(revision, bool) or not isinstance(revision, int) or revision < 0):
            raise ApiError(400, 'invalid_revision', 'base_revision 须为非负整数')
        for key in ('title', 'summary', 'instance', 'layers'):
            if key in body:
                sc[key] = body[key]
        try:
            return core('scenarios').save_user_scenario(sc, overwrite=True, expected_revision=revision)
        except ValueError as exc:
            if isinstance(exc, core('scenarios').ScenarioConflict):
                raise ApiError(409, 'scenario_conflict', str(exc)) from None
            raise ApiError(400, 'invalid_instance', str(exc)) from None

    @app.post(f'{api}/scenarios/{{scenario_id}}/fork', status_code=201)
    def fork_scenario(scenario_id: str, payload: Any = Body(None)):
        body = body_dict(payload) if payload is not None else {}
        sc = scenario_or_404(scenario_id)
        from .planning import without_stale_reference
        sc = without_stale_reference(sc, body.get('instance') or sc['instance'])
        import re as _re
        from datetime import datetime as _dt, timezone as _tz
        stamp = _dt.now(_tz.utc).strftime('%m%d%H%M%S')
        import secrets
        client_id = body.get('client_id')
        if client_id is not None and (not isinstance(client_id, str) or not core('scenarios').ID_RE.match(client_id)):
            raise ApiError(400, 'invalid_request', 'client_id 须为 2–64 位小写字母、数字、-、_、.（以字母或数字开头）')
        sc['id'] = client_id or _re.sub(r'[^a-z0-9_.-]', '-', f"{scenario_id[:36]}-my-{stamp}-{secrets.token_hex(2)}".lower())
        sc['title'] = str(body.get('title') or f"{sc.get('title', scenario_id)}（我的）")[:60]
        if body.get('instance') is not None:
            sc['instance'] = body['instance']
        sc.pop('library', None)
        sc['provenance'] = {**(sc.get('provenance') or {}), 'forked_from': scenario_id}
        try:
            return core('scenarios').save_user_scenario(sc, overwrite=False)
        except ValueError as exc:
            existing = _existing_user_scenario(client_id) if client_id else None
            if existing is None:
                raise ApiError(400, 'invalid_instance', str(exc)) from None
            if _same_fork(existing, sc, scenario_id):
                return JSONResponse(existing, status_code=200)
            raise ApiError(409, 'scenario_conflict', f'场景 {client_id} 已存在且内容不同；未覆盖') from None

    @app.post(f'{api}/scenarios/import/geojson', status_code=201)
    def import_geojson(payload: Any = Body(None)):
        body = body_dict(payload)
        try:
            sc = core('compose.geo').import_geojson(body.get('geojson') or {}, body.get('title'), body.get('vehicles'))
            return core('scenarios').save_user_scenario(sc, overwrite=False)
        except (ValueError, KeyError, TypeError) as exc:
            raise ApiError(400, 'invalid_geojson', f'GeoJSON 无法导入：{exc}') from None

    @app.get(f'{api}/scenarios/{{scenario_id}}/export')
    def export_scenario(scenario_id: str, format: str = 'geojson'):
        sc = scenario_or_404(scenario_id)
        if format == 'json':
            return attachment(__import__('json').dumps(sc, ensure_ascii=False, indent=1).encode(), 'application/json', f'{scenario_id}.json')
        if format == 'geojson' and sc['problem_class'] == 'fleet_routing':
            return attachment(core('compose.geo').export_geojson(sc), 'application/geo+json', f'{scenario_id}.geojson')
        raise ApiError(400, 'invalid_format', '场景导出格式为 json，多车任务地图另支持 geojson')

    # ------------------------------------------------------- library --
    @app.get(f'{api}/library')
    def library_overview():
        return core('library').overview()

    @app.get(f'{api}/library/cvrplib/sets')
    def cvrplib_sets():
        return {'sets': core('library.cvrplib').sets()}

    @app.get(f'{api}/library/cvrplib/instances')
    def cvrplib_instances(set: str | None = None, q: str | None = None):
        return {'instances': core('library.cvrplib').instances(set, q)}

    @app.get(f'{api}/library/fields')
    def fields_list(country: str | None = None, q: str | None = None):
        lib = core('library.fields')
        return {'status': lib.status(), 'fields': lib.listing(country, q)}

    @app.post(f'{api}/library/fields/fetch')
    def fields_fetch(payload: Any = Body(None)):
        body = body_dict(payload) if payload is not None else {}
        try:
            return core('library.fields').fetch(images=bool(body.get('images')))
        except OSError as exc:
            raise ApiError(502, 'download_failed', f'下载 Fields2Benchmark 失败：{exc}') from None

    @app.get(f'{api}/library/fields/{{field_id}}/image')
    def field_image(field_id: str):
        img = core('library.fields').image(field_id)
        if img is None:
            raise ApiError(404, 'not_found', '没有该田块的卫星影像（影像包未下载）')
        return Response(img[0], media_type=img[1], headers={'Cache-Control': 'max-age=86400'})

    @app.get(f'{api}/library/fields/{{field_id}}/outline.svg')
    def field_outline(field_id: str):
        try:
            return Response(core('library.fields').thumbnail_svg(field_id), media_type='image/svg+xml', headers={'Cache-Control': 'max-age=86400'})
        except (KeyError, StopIteration):
            raise ApiError(404, 'not_found', f'没有田块 {field_id}') from None

    @app.get(f'{api}/references')
    def references(topic: str | None = None, q: str | None = None):
        refs = core('library.references')
        return {'references': refs.listing(topic, q), 'topics': refs.TOPICS}

    @app.get(f'{api}/references.bib')
    def references_bib(keys: str | None = None):
        data = core('library.references').bibtex([k for k in (keys or '').split(',') if k] or None)
        return Response(data, media_type='application/x-bibtex; charset=utf-8',
                        headers={'Content-Disposition': 'attachment; filename="references.bib"'})

    @app.get(f'{api}/source')
    def source(path: str):
        allowed = ('src/research/src/urp_research/', 'src/lab/urp_lab/', 'src/lab/ros/', 'src/frontend/core/routing/')
        clean = PurePosixPath(path)
        if clean.is_absolute() or '..' in clean.parts or not any(str(clean).startswith(a) for a in allowed):
            raise ApiError(403, 'forbidden', '只能查看算法与集成源码')
        target = paths.REPO / clean
        if not target.is_file() or target.stat().st_size > 512 * 1024:
            raise ApiError(404, 'not_found', f'没有文件 {path}')
        return {'path': str(clean), 'language': target.suffix.lstrip('.'), 'text': target.read_text(encoding='utf-8', errors='replace')}

    # ---------------------------------------------------- ROS robot models --
    @app.get(f'{api}/ros/robots/{{model}}.urdf')
    def robot_urdf(model: str):
        try:
            text = core('robots').urdf(model)
        except KeyError:
            raise ApiError(404, 'unknown_robot', f'没有机器人模型 {model}') from None
        except (FileNotFoundError, RuntimeError, OSError) as exc:
            raise ApiError(503, 'robot_unavailable', f'无法展开 {model} 的 URDF：{exc}') from None
        return Response(text, media_type='application/xml', headers={'Cache-Control': 'max-age=3600'})

    @app.get(f'{api}/ros/package/{{package}}/{{rel:path}}')
    def ros_package_file(package: str, rel: str):
        try:
            target, media = core('robots').package_file(package, rel)
        except PermissionError:
            raise ApiError(403, 'forbidden', '只能读取机器人描述包里的模型文件') from None
        except FileNotFoundError:
            raise ApiError(404, 'not_found', f'没有文件 package://{package}/{rel}') from None
        return FileResponse(target, media_type=media, headers={'Cache-Control': 'max-age=86400'})

    # ------------------------------------------------------- studies --
    @app.post(f'{api}/studies', status_code=201)
    def create_study(payload: Any = Body(None)):
        body = body_dict(payload)
        try:
            return lab.studies.create(body, core('scenarios').get_scenario, lab.catalog())
        except KeyError as exc:
            raise ApiError(404, 'unknown_scenario', f'没有场景 {exc}') from None
        except ValueError as exc:
            raise ApiError(400, 'invalid_study', str(exc)) from None
        except RuntimeError as exc:
            raise ApiError(503, 'shutting_down', str(exc)) from None

    @app.get(f'{api}/studies')
    def list_studies():
        return {'studies': lab.studies.list()}

    def study_or_404(study_id: str) -> dict:
        s = lab.studies.get(study_id)
        if s is None:
            raise ApiError(404, 'not_found', f'没有课题 {study_id}')
        return s

    @app.get(f'{api}/studies/{{study_id}}')
    def get_study(study_id: str):
        return study_or_404(study_id)

    @app.post(f'{api}/studies/{{study_id}}/cancel')
    def cancel_study(study_id: str, payload: Any = Body(None)):
        study_or_404(study_id)
        return lab.studies.cancel(study_id)

    @app.get(f'{api}/studies/{{study_id}}/analysis')
    def study_analysis(study_id: str, alpha: float = 0.05):
        study_or_404(study_id)
        return core('analysis').analyze(lab.studies.runs(study_id), alpha)

    @app.get(f'{api}/studies/{{study_id}}/instance-space')
    def study_space(study_id: str):
        study_or_404(study_id)
        return core('analysis').instance_space(lab.studies.runs(study_id))

    @app.get(f'{api}/studies/{{study_id}}/export')
    def study_export(study_id: str, format: str = 'csv'):
        study = study_or_404(study_id)
        runs = lab.studies.runs(study_id)
        an = core('analysis')
        if format == 'latex':
            return attachment(an.latex_table(an.analyze(runs), study['title']).encode(), 'application/x-tex', f'{study_id}.tex')
        if format == 'json':
            data = __import__('json').dumps({'study': study, 'analysis': an.analyze(runs)}, ensure_ascii=False, indent=1, default=str)
            return attachment(data.encode(), 'application/json', f'{study_id}.json')
        if format == 'csv':
            return export_with(core('export').export_compare, runs, 'csv')
        raise ApiError(400, 'invalid_format', '课题导出格式为 csv / json / latex')

    @app.post(f'{api}/analysis')
    def analysis(payload: Any = Body(None)):
        body = body_dict(payload)
        ids = body.get('run_ids')
        if not isinstance(ids, list) or not ids:
            raise ApiError(400, 'invalid_request', 'run_ids 须为非空数组')
        return core('analysis').analyze(lab.store.runs_by_ids([str(i) for i in ids]), float(body.get('alpha') or 0.05))

    # ------------------------------------------------------------ assets --
    # Asset library (maps, vehicles, facilities, task-map templates): self-contained package, see urp_lab/assets.
    from .assets.api import router as assets_router
    app.include_router(assets_router, prefix=api)
    # Research figures in publication formats (urp_lab/figures): drawn only from recorded runs / simulations / studies.
    from .figures.api import router as figures_router
    app.include_router(figures_router, prefix=api)
    # Task agent (urp_lab/agent): natural-language request → confirmed task contract → validated tool calls.
    from .agent.api import router as agent_router
    app.include_router(agent_router, prefix=api)
    # Research agent (CONTRACT §9): LLM planning over the same tools, checks and records as the panel.
    from .agent.routes import install as install_agent
    install_agent(app, lab, api)

    @app.api_route(f'{api}/{{rest:path}}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'], include_in_schema=False)
    def api_not_found(rest: str):
        raise ApiError(404, 'not_found', f'没有接口 /lab/api/{rest}')

    # ------------------------------------------------------ archive proxy --
    @app.api_route('/api/{rest:path}', methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'], include_in_schema=False)
    async def archive_proxy(request: Request, rest: str):
        url = f'{config.archive_url}/api/{rest}'
        if request.url.query:
            url += f'?{request.url.query}'
        headers = [(k, v) for k, v in request.headers.raw
                   if k.decode('latin-1').lower() not in HOP_BY_HOP | {'host', 'origin'}]
        if 'origin' in request.headers:
            target = urlsplit(config.archive_url)
            headers.append((b'origin', f'{target.scheme}://{target.netloc}'.encode('ascii')))
        client: httpx.AsyncClient = request.app.state.http
        upstream = client.build_request(request.method, url, headers=headers, content=await request.body())
        try:
            resp = await client.send(upstream, stream=True)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            return error(503, 'archive_offline', '档案后端未运行：下载历史档案后自动启动，见 设置 → 数据' if os.environ.get('URP_DESKTOP') else '档案后端未运行，可在概览页启动')
        except httpx.HTTPError as exc:
            return error(502, 'archive_error', f'档案后端请求失败：{type(exc).__name__}')
        out = StreamingResponse(resp.aiter_raw(), status_code=resp.status_code, background=BackgroundTask(resp.aclose))
        out.raw_headers = [(k, v) for k, v in resp.headers.raw if k.decode('latin-1').lower() not in HOP_BY_HOP - {'content-length'}]
        return out

    # ------------------------------------------------------------ static --
    def static_file(rel: str) -> Path | None:
        parts = PurePosixPath(rel).parts
        if not parts or any(p in ('..', '.') or p.startswith('/') for p in parts):
            return None
        path = lab.dist.joinpath(*parts)
        return path if path.is_file() else None

    def index() -> Response:
        page = lab.dist / 'index.html'
        if not page.is_file():
            return HTMLResponse('<!doctype html><meta charset="utf-8"><title>农业机器人路径规划工作台</title>'
                                '<p>前端尚未构建。请在项目根目录运行 <code>bash setup/run.sh lab-setup</code>，'
                                '或 <code>bash setup/run.sh build</code>，然后刷新。</p>', status_code=503)
        return FileResponse(page, headers={'Cache-Control': 'no-cache'})

    @app.get('/', include_in_schema=False)
    def root():
        return index()

    @app.get('/data/{rest:path}', include_in_schema=False)
    def data(rest: str):
        path = static_file(f'data/{rest}') or lab.packs.web_file(rest)
        if path is None:
            return error(404, 'not_found', '这份历史数据还没有下载，可在 设置 → 数据 下载历史档案')
        return FileResponse(path, headers={'Cache-Control': 'no-cache'})

    @app.get('/{rest:path}', include_in_schema=False)
    def static(request: Request, rest: str):
        path = static_file(rest)
        if path is not None:
            cache = ASSET_CACHE if rest.startswith('assets/') else 'no-cache'
            return FileResponse(path, headers={'Cache-Control': cache})
        if 'text/html' in request.headers.get('accept', '') and not rest.startswith('assets/'):
            return index()
        return error(404, 'not_found', f'没有文件 /{rest}')

    return app
