"""python -m urp_lab serve [--port 4100] [--open] [--no-archive] | doctor | solve --scenario ID --algorithm ID [--param k=v]"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

from . import paths

log = logging.getLogger('urp_lab')


def setup_logging(runtime):
    logs = runtime / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in (logging.FileHandler(logs / 'gateway.log', encoding='utf-8'), logging.StreamHandler(sys.stderr)):
        handler.setFormatter(fmt)
        root.addHandler(handler)
    logging.getLogger('uvicorn.access').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)


def open_browser(url: str):
    chrome = shutil.which('google-chrome') or shutil.which('google-chrome-stable') or shutil.which('chromium')
    cmd = [chrome, f'--app={url}'] if chrome else ['xdg-open', url]
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError as exc:
        log.warning('无法打开浏览器（%s）：%s；请手动访问 %s', cmd[0], exc, url)


def port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def serve(args) -> int:
    import uvicorn

    from .gateway import Config, create_app
    from .runtime_config import archive_autostart, archive_url, admin_token, extra_hosts
    from .services import http_json

    bind = args.host
    url = f'http://127.0.0.1:{args.port}/'
    status, data = http_json('GET', f'{url}lab/api/health', timeout=1.0)
    if status == 200 and isinstance(data, dict) and data.get('status') == 'ok':
        wanted = hashlib.sha256(str(paths.RUNTIME.resolve()).encode()).hexdigest()
        if data.get('runtime_id') != wanted:
            print(f'端口 {args.port} 上的工作台使用另一份或未标识的运行数据；请先有序关闭再切换，未改动现有服务。', file=sys.stderr)
            return 1
        print(f'工作台网关已在 {url} 运行。', flush=True)
        if args.open:
            open_browser(url)
        return 0
    if not port_free(bind, args.port):
        print(f'端口 {args.port} 已被其他进程占用，网关无法启动。', file=sys.stderr)
        return 1
    setup_logging(paths.RUNTIME)
    try:
        app = create_app(Config(port=args.port, bind_host=bind, archive_autostart=archive_autostart(args.no_archive),
                                archive_url=archive_url(), extra_hosts=extra_hosts(), admin_token=admin_token()))
    except RuntimeError as exc:
        print(f'网关无法启动：{exc}', file=sys.stderr)
        return 1
    lab = app.state.lab

    class Server(uvicorn.Server):
        def handle_exit(self, sig, frame):
            if self.should_exit:
                lab.services.hurry.set()
                log.warning('再次收到 %s：加快关闭（仿真会话将逐级发送信号）', signal.Signals(sig).name)
            else:
                log.info('收到 %s，正在有序关闭（仿真会话最长约 2.5 分钟）…', signal.Signals(sig).name)
            lab.bus.close()
            super().handle_exit(sig, frame)

    config = uvicorn.Config(app, host=bind, port=args.port, log_config=None, access_log=False,
                            timeout_graceful_shutdown=5, lifespan='on', server_header=False)
    server = Server(config)
    if hasattr(signal, 'SIGHUP'):
        signal.signal(signal.SIGHUP, server.handle_exit)  # terminal window closed

    def announce():
        while not server.started and not server.should_exit:
            time.sleep(0.1)
        if server.started:
            log.info('农业机器人路径规划工作台：%s（监听 %s，Ctrl+C 有序退出）', url, bind)
            if args.open:
                open_browser(url)
            while not server.should_exit:
                if lab.shutdown_event.wait(0.5):
                    log.info('面板请求有序关闭，保留各组件真实退出结果')
                    lab.bus.close()
                    server.should_exit = True
                    break
    threading.Thread(target=announce, daemon=True).start()

    def leave_with_parent():
        """The desktop app holds our stdin open; end of file means it is gone, however it went."""
        while os.read(0, 1024):     # the raw descriptor: a daemon thread blocked in sys.stdin would abort interpreter shutdown
            pass
        log.info('桌面应用已退出，网关随之有序关闭')
        lab.shutdown_event.set()
    if args.exit_with_stdin:
        threading.Thread(target=leave_with_parent, daemon=True).start()
    try:
        server.run()
    except KeyboardInterrupt:
        return 130
    return 0


def doctor(args) -> int:
    from .services import ServiceManager

    manager = ServiceManager(None)
    print(json.dumps({'services': manager.services(wait_checks=True)}, ensure_ascii=False, indent=2))
    return 0


def parse_param(text: str):
    key, sep, value = text.partition('=')
    if not sep or not key:
        raise argparse.ArgumentTypeError(f'参数格式应为 k=v：{text}')
    try:
        return key, json.loads(value)
    except json.JSONDecodeError:
        return key, value


def solve(args) -> int:
    from .jobs import JobManager, new_id, resolve_params, skeleton, wall_budget
    from .scenarios import get_scenario
    from .solvers import catalog
    from .store import Store

    try:
        scenario = get_scenario(args.scenario)
    except KeyError:
        print(f'没有场景 {args.scenario}', file=sys.stderr)
        return 2
    algorithm = {a['id']: a for a in catalog()}.get(args.algorithm)
    if algorithm is None:
        print(f'没有算法 {args.algorithm}', file=sys.stderr)
        return 2
    if scenario['problem_class'] not in algorithm.get('problem_classes', []):
        print(f"{algorithm['id']} 不适用于 {scenario['problem_class']}", file=sys.stderr)
        return 2
    if not algorithm.get('available'):
        print(f"{algorithm['id']} 不可用：{algorithm.get('unavailable_reason')}", file=sys.stderr)
        return 2
    try:
        params = resolve_params(algorithm, dict(args.param or []))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        store = Store(':memory:' if args.no_save else paths.RUNTIME / 'lab.sqlite3')
    except RuntimeError as exc:
        print(f'求解无法开始：{exc}', file=sys.stderr)
        return 1
    try:
        jobs = JobManager(store, None, concurrency=1, recover=False)
        instance = scenario['instance']
        sol = skeleton(new_id('job'), scenario, instance, algorithm, params, args.label)
        request = {'run_id': sol['run_id'], 'scenario': scenario, 'instance': instance,
                   'algorithm_id': algorithm['id'], 'params': params}
        jobs.execute(sol, request, wall_budget(params, algorithm, jobs.grace_s))
        print(json.dumps(sol, ensure_ascii=False, indent=None if args.compact else 1))
        return 0 if sol['state'] == 'done' else 1
    finally:
        store.close()


def main(argv=None) -> int:
    from .runtime_transfer import configure_cli, run_cli
    parser = argparse.ArgumentParser(prog='python -m urp_lab', description='农业机器人路径规划工作台')
    sub = parser.add_subparsers(dest='command', required=True)
    configure_cli(sub)
    p = sub.add_parser('serve', help='启动网关（默认只监听 127.0.0.1）')
    p.add_argument('--port', type=int, default=int(os.environ.get('URP_PORT', '4100')))
    p.add_argument('--host', default=os.environ.get('URP_BIND_HOST', '127.0.0.1'),
                   help='监听地址。笔记本保持 127.0.0.1；WSL 与容器设 URP_BIND_HOST=0.0.0.0')
    p.add_argument('--open', action='store_true', help='启动后用 Chrome 应用窗口打开面板')
    p.add_argument('--no-archive', action='store_true', help='不自动启动档案后端')
    p.add_argument('--exit-with-stdin', action='store_true', help='标准输入关闭时有序退出（桌面应用用来绑定生命周期）')
    sub.add_parser('doctor', help='打印服务检测结果（JSON）')
    p = sub.add_parser('solve', help='无界面求解一次，输出 Solution JSON')
    p.add_argument('--scenario', required=True)
    p.add_argument('--algorithm', required=True)
    p.add_argument('--param', action='append', type=parse_param, metavar='K=V')
    p.add_argument('--label')
    p.add_argument('--no-save', action='store_true', help='不写入运行历史')
    p.add_argument('--compact', action='store_true')
    args = parser.parse_args(argv)
    return {'serve': serve, 'doctor': doctor, 'solve': solve, 'data': run_cli}[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
