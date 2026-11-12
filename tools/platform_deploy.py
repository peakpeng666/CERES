#!/usr/bin/env python3
"""Download the pinned CERES image, check its bytes, and operate an owned container.

Linux x86_64 and a working local Docker Engine are required. No root installation,
host Python replacement, broad process killing, or automatic data deletion.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import posixpath
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import urllib.request

REPO = Path(__file__).resolve().parents[1]
ROOT = '/home/peakpeng/Desktop/CERES'
LABEL = 'org.urp.deployment'
SUPPLEMENT_MARKER = 'CERES_ADDITIONAL_SNAPSHOT.json'


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def inspect(kind, name):
    p = subprocess.run(['docker', kind, 'inspect', name], capture_output=True, text=True)
    return json.loads(p.stdout)[0] if p.returncode == 0 else None


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024**2), b''):
            h.update(block)
    return h.hexdigest()


def download(asset, directory, release):
    name = asset['name']
    if Path(name).name != name or not re.fullmatch(r'[a-zA-Z0-9_.-]+', name):
        raise ValueError('Invalid asset filename')
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    if target.exists():
        if target.stat().st_size == asset['bytes'] and digest(target) == asset['sha256']:
            return target
        raise ValueError(f'Existing file failed checksum: {target}; preserve it and choose a new --assets-dir')
    partial = directory / (name + '.partial')
    print(f'下载 {name} ({asset["bytes"] / 1e9:.2f} GB)', flush=True)
    url = f'https://github.com/peakpeng666/URP/releases/download/{release}/{name}'
    for attempt in range(6):
        offset = partial.stat().st_size if partial.exists() else 0
        if offset == asset['bytes']:
            break
        if offset > asset['bytes']:
            raise ValueError(f'Partial file exceeds expected size: {partial}')
        headers = {'User-Agent': 'CERES-reproduce/1'}
        if offset:
            headers['Range'] = f'bytes={offset}-'
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                if offset and response.status == 200:
                    # A server may decline range requests. Preserve the old partial
                    # and restart; never concatenate two complete responses.
                    partial.rename(directory / (name + f'.interrupted-{time.time_ns()}'))
                    offset = 0
                elif offset and (response.status != 206 or not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-')):
                    raise ValueError('Server returned an inconsistent byte range')
                with partial.open('ab' if offset else 'wb') as f:
                    shutil.copyfileobj(response, f, length=4 * 1024**2)
            if partial.stat().st_size == asset['bytes']:
                break
        except OSError as error:
            if attempt == 5:
                raise OSError(f'Download interrupted; rerun to resume {partial.name}') from error
        if attempt < 5:
            print(f'继续下载 {name}；已保留 {partial.stat().st_size if partial.exists() else 0} 字节', flush=True)
            time.sleep(min(2 ** attempt, 10))
    if partial.stat().st_size != asset['bytes'] or digest(partial) != asset['sha256']:
        raise ValueError(f'Download checksum failed: {partial}')
    partial.rename(target)
    return target


def download_image(catalog, directory):
    # Several modest independent pieces make interrupted or slow connections
    # recoverable without holding a multi-gigabyte HTTP request open.
    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(lambda asset: download(asset, directory, catalog['release_tag']), catalog['assets']))


def supplement_member(member):
    """Map an audited project snapshot into the container, never the host."""
    name = PurePosixPath(member.name)
    if name.is_absolute() or '..' in name.parts or '\\' in member.name or len(name.parts) < 2 or not member.name.startswith('project/'):
        raise ValueError(f'Invalid supplementary member: {member.name}')
    if not member.isfile() and not member.issym():
        raise ValueError(f'Unsupported supplementary member: {member.name}')
    if member.issym():
        if PurePosixPath(member.linkname).is_absolute():
            raise ValueError('Absolute supplementary link is not allowed')
        target = posixpath.normpath(posixpath.join(posixpath.dirname(member.name), member.linkname))
        for prefix, root in [('project/', ROOT + '/'), ('host/', '/'),
                             ('python/', '/home/peakpeng/.local/share/uv/python/')]:
            if target.startswith(prefix):
                member.linkname = root + target[len(prefix):]
                break
        else:
            raise ValueError('Supplementary link escapes the published layout')
    member.name = member.name[len('project/'):]
    member.uid = member.gid = 1000
    member.uname = member.gname = ''
    member.mode &= 0o777
    # PAX path/linkpath headers otherwise override the mapped names when tarfile
    # writes long paths again, silently restoring into an extra project/ folder.
    member.pax_headers = {}
    return member


def install_supplement(name, asset, archive):
    if digest(archive) != asset['sha256']:
        raise ValueError('Supplement checksum changed before installation')
    print('恢复旧基线的独立环境、JDK 与原始记录…', flush=True)
    process = subprocess.Popen(['docker', 'cp', '-a', '-', f'{name}:{ROOT}'], stdin=subprocess.PIPE)
    try:
        with tarfile.open(archive, 'r|gz') as source, tarfile.open(fileobj=process.stdin, mode='w|') as target:
            directories = set()
            for original in source:
                member = supplement_member(original)
                for parent in reversed(PurePosixPath(member.name).parents):
                    if str(parent) != '.' and str(parent) not in directories:
                        directory = tarfile.TarInfo(str(parent)); directory.type = tarfile.DIRTYPE
                        directory.mode = 0o755; directory.uid = directory.gid = 1000
                        target.addfile(directory); directories.add(str(parent))
                target.addfile(member, source.extractfile(original) if member.isfile() else None)
            marker = json.dumps({'asset': asset['name'], 'sha256': asset['sha256']}).encode()
            info = tarfile.TarInfo(SUPPLEMENT_MARKER); info.size = len(marker)
            info.uid = info.gid = 1000; info.mode = 0o644
            target.addfile(info, io.BytesIO(marker))
        process.stdin.close()
        if process.wait() != 0:
            raise RuntimeError('Supplement restore failed; preserve this container and retry with a new --name')
    finally:
        if process.poll() is None:
            process.terminate(); process.wait()


def verify_supplement(name, asset):
    copy = subprocess.run(['docker', 'cp', f'{name}:{ROOT}/{SUPPLEMENT_MARKER}', '-'], capture_output=True)
    if copy.returncode:
        raise ValueError('Supplement not initialized; preserve this container and use a new --name')
    with tarfile.open(fileobj=io.BytesIO(copy.stdout)) as archive:
        marker = json.load(archive.extractfile(archive.next()))
    if marker != {'asset': asset['name'], 'sha256': asset['sha256']}:
        raise ValueError('Container supplement version differs; use a new --name to preserve its data')


def stop_container(name, commit):
    # Use the gateway's existing orderly shutdown. Uvicorn re-raises a received
    # SIGTERM after cleanup, so docker stop alone normally yields 143, not zero.
    # Preserve that distinction: never relabel arbitrary 143 exits as success.
    script = (
        "import urllib.request; "
        "r=urllib.request.urlopen(urllib.request.Request("
        "'http://127.0.0.1:4100/lab/api/shutdown', data=b'{}', "
        "headers={'Content-Type':'application/json'}, method='POST'), timeout=5); "
        "assert r.status==202; print(r.read().decode())"
    )
    accepted = subprocess.run(['docker', 'exec', '-u', '1000:1000', name, '/usr/bin/python3', '-c', script],
                              capture_output=True, text=True)
    error = None
    if accepted.returncode:
        error = 'Gateway shutdown API unavailable; signal fallback was required'
    else:
        print(accepted.stdout.strip())
        try:
            run('docker', 'wait', name, capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            error = 'Gateway exceeded the 180 s orderly shutdown deadline'
    if error:
        run('docker', 'stop', '--time', '30', name)
    state = owned_container(name, commit)['State']
    print(json.dumps(state, ensure_ascii=False))
    if state['ExitCode'] != 0 or error:
        raise RuntimeError(f'{error or "Gateway failed"}; actual exit code {state["ExitCode"]}; inspect logs')
    return state


def owned_container(name, commit):
    item = inspect('container', name)
    if item and item['Config'].get('Labels', {}).get(LABEL) != commit:
        raise ValueError(f'Container {name} is not owned by this deployment; choose another --name')
    return item


def volume(name, commit):
    item = inspect('volume', name)
    if item:
        if (item.get('Labels') or {}).get(LABEL) != commit:
            raise ValueError(f'Volume {name} is not owned by this deployment')
    else:
        run('docker', 'volume', 'create', '--label', f'{LABEL}={commit}', name)


def available(port):
    with socket.socket() as s:
        # A clean gateway shutdown can leave TIME_WAIT connections. These are
        # not a listening service and must not prevent an immediate restart.
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(('127.0.0.1', port))
        except OSError:
            raise ValueError(f'Port {port} is occupied; stop the existing CERES session first') from None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', nargs='?', default='up', choices=['up', 'download', 'stop', 'status', 'logs', 'acceptance'])
    p.add_argument('--catalog', type=Path, default=REPO / 'setup/platform-image.json')
    p.add_argument('--assets-dir', type=Path, default=REPO / '.local-state/platform-image-download')
    p.add_argument('--name', default='ceres-platform')
    p.add_argument('--gui', action='store_true', help='Linux X11 native windows; requires an existing Xauthority file')
    p.add_argument('--acceptance', action='store_true', help='After startup run real Gazebo free-path and web checks')
    args = p.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,50}', args.name):
        p.error('Invalid container name')
    catalog = json.loads(args.catalog.read_text())
    if args.command == 'download':
        if catalog['schema'] not in ('urp.platform-image/1', 'urp.platform-snapshot/1'):
            raise ValueError('Unsupported download catalog')
        download_image(catalog, args.assets_dir)
        if catalog.get('supplement'):
            download(catalog['supplement'], args.assets_dir, catalog['release_tag'])
        print('清单内全部附件下载与 SHA256 校验通过。')
        return
    if catalog['schema'] != 'urp.platform-image/1':
        raise ValueError('Unsupported image catalog')
    commit, image = catalog['source_commit'], catalog['image_id']
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'amd64'):
        raise ValueError('This accepted image requires Linux x86_64; ARM/macOS/Windows need a compatible Linux VM, not native execution')
    host = subprocess.check_output(['docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'], text=True).strip()
    if not host.startswith('unix://') or os.environ.get('DOCKER_HOST', 'unix://').startswith(('tcp:', 'ssh:')):
        raise ValueError('Use a local Docker Engine: the gateway and desktop share host loopback')
    run('docker', 'info', '--format', '{{.OSType}}/{{.Architecture}}')
    item = owned_container(args.name, commit)
    if args.command == 'status':
        print(json.dumps(item['State'] if item else {'status': 'not_created'}, ensure_ascii=False, indent=2)); return
    if args.command == 'logs':
        if not item:
            raise ValueError('Deployment not created')
        run('docker', 'logs', '--tail', '100', args.name); return
    if args.command == 'stop':
        if item and item['State']['Running']:
            stop_container(args.name, commit)
        elif item and item['State']['ExitCode'] != 0:
            raise RuntimeError(f'Container already exited with code {item["State"]["ExitCode"]}; inspect logs before restarting')
        print('已停止；运行数据库、资产与实验数据保留在命名卷中。'); return
    if args.command == 'up':
        if item and item['Image'] != image:
            raise ValueError('Existing container uses a different image; choose a new --name to preserve its data')
        if item and catalog.get('supplement'):
            verify_supplement(args.name, catalog['supplement'])
        if not item or not item['State']['Running']:
            for port in (4100, 8765, 9002):
                available(port)
            if not inspect('image', image):
                parts = download_image(catalog, args.assets_dir)
                print('校验完成，正在载入固定镜像…', flush=True)
                load = subprocess.Popen(['docker', 'image', 'load'], stdin=subprocess.PIPE)
                try:
                    for part in parts:
                        with part.open('rb') as f:
                            shutil.copyfileobj(f, load.stdin, length=4 * 1024**2)
                    load.stdin.close()
                    if load.wait() != 0:
                        raise RuntimeError('Docker image load failed')
                finally:
                    if load.poll() is None:
                        load.terminate(); load.wait()
            if not inspect('image', image):
                raise ValueError('Loaded image ID differs from the Git-pinned catalog')
            if item:
                run('docker', 'start', args.name)
            else:
                supplement = download(catalog['supplement'], args.assets_dir, catalog['release_tag']) if catalog.get('supplement') else None
                command = ['docker', 'create', '--name', args.name, '--network', 'bridge', '--cap-add', 'NET_ADMIN', '--shm-size', '1g',
                           '--tmpfs', '/tmp:rw,nosuid,size=1g,mode=1777',
                           '-p', '127.0.0.1:4100:4100', '-p', '127.0.0.1:8765:8765', '-p', '127.0.0.1:9002:9002',
                           '--label', f'{LABEL}={commit}', '--stop-timeout', '180',
                           '-e', 'CERES_HOST_NETNS=' + os.readlink('/proc/self/ns/net')]
                for suffix, target in [('runtime', '/src/lab/runtime'), ('state', '/.local-state')]:
                    name = args.name + '-' + suffix
                    volume(name, commit)
                    command += ['--mount', f'type=volume,src={name},dst={ROOT}{target}']
                if args.gui:
                    display = os.environ.get('DISPLAY')
                    authority = Path(os.environ.get('XAUTHORITY', str(Path.home() / '.Xauthority')))
                    if not display or not authority.is_file():
                        raise ValueError('--gui requires DISPLAY and an existing XAUTHORITY; no xhost/security changes are made')
                    if not os.access(authority, os.R_OK):
                        raise ValueError('Cannot read XAUTHORITY')
                    command += ['-e', f'DISPLAY={display}', '-e', 'CERES_XAUTHORITY_INPUT=/tmp/ceres-xauthority-input',
                                '--mount', 'type=bind,src=/tmp/.X11-unix,dst=/tmp/.X11-unix,readonly',
                                '--mount', f'type=bind,src={authority.resolve()},dst=/tmp/ceres-xauthority-input,readonly']
                run(*command, image)
                if supplement:
                    install_supplement(args.name, catalog['supplement'], supplement)
                    verify_supplement(args.name, catalog['supplement'])
                run('docker', 'start', args.name)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            state = owned_container(args.name, commit)['State']
            if not state['Running']:
                run('docker', 'logs', '--tail', '40', args.name)
                raise RuntimeError(f'Gateway exited: {state["ExitCode"]}')
            try:
                with urllib.request.urlopen('http://127.0.0.1:4100/lab/api/health', timeout=2) as response:
                    if json.load(response).get('status') == 'ok':
                        break
            except (OSError, ValueError):
                pass
            time.sleep(1)
        else:
            raise TimeoutError('Gateway health did not become ready; inspect reproduce logs')
        print('CERES 已启动：http://127.0.0.1:4100/；数据库与完整历史已恢复。', flush=True)
    if args.command == 'acceptance' or args.acceptance:
        item = owned_container(args.name, commit)
        if not item or not item['State']['Running']:
            raise ValueError('Start the deployment before acceptance')
        evidence = ROOT + '/.local-state/container-acceptance-' + str(time.time_ns())
        run('docker', 'exec', '-u', '1000:1000', '-w', ROOT + '/src/lab', args.name, '.venv/bin/python',
            'e2e/field_backend.py', '--out', evidence, '--cases', 'free,web', '--headless')
        print(f'物理验收完成；容器内证据：{evidence}。可用 docker cp 导出。')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f'部署未完成：{error}', file=sys.stderr)
        sys.exit(1)
