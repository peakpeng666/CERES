#!/usr/bin/env python3
"""Freeze an audited Linux deployment into a userspace image, without host secrets.

Release maintainer tool. Requires the already verified/sanitized snapshot, the
matching Git commit and dpkg package versions on this Linux machine. Does not
install packages, alter the host, or include /etc/shadow, login homes or drivers.
Consumers load the published image with platform_deploy.py instead.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import posixpath
import subprocess
import tarfile

ROOT = '/home/peakpeng/Desktop/CERES'
USER_HOME = '/home/peakpeng'
ETC_ALLOWED = ('/etc/fonts/', '/etc/ssl/certs/', '/etc/alternatives/', '/etc/python3',
               '/etc/ld.so.conf', '/etc/ImageMagick-6/')
ETC_FILES = {'/etc/os-release', '/etc/debian_version', '/etc/mime.types', '/etc/services',
             '/etc/protocols', '/etc/nsswitch.conf'}


def output(*args):
    return subprocess.check_output(args, text=True).strip()


def walk(root):
    for folder, dirs, files in os.walk(root, followlinks=False):
        for name in list(dirs):
            p = Path(folder) / name
            if p.is_symlink():
                yield p
                dirs.remove(name)
        for name in files:
            yield Path(folder) / name


def canonical(path):
    for alias in ('/bin/', '/sbin/', '/lib/', '/lib64/'):
        if path.startswith(alias):
            return '/usr' + path
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restored', type=Path, required=True)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    restored = args.restored.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    metadata = restored / 'metadata'
    packages = {p['name'] + ':' + p['architecture']: p for p in json.loads((metadata / 'system-dependencies.json').read_text())}
    essentials = output('dpkg-query', '-W', '-f=${binary:Package}\t${Essential}\n')
    extra = {s.split('\t')[0] for s in essentials.splitlines() if s.endswith('\tyes')}
    extra.update({'bash', 'dash', 'git', 'procps', 'iproute2', 'libbpf0', 'libmnl0', 'zstd', 'mawk', 'findutils', 'tar', 'xz-utils'})
    for name in sorted(extra):
        fields = output('dpkg-query', '-W', '-f=${Package}\t${Version}\t${Architecture}', name).split('\t')
        packages[fields[0] + ':' + fields[2]] = dict(zip(('name', 'version', 'architecture'), fields))
    files = {}
    versions = dict(line.split('\t', 1) for line in output('dpkg-query', '-W', '-f=${Package}:${Architecture}\t${Version}\n').splitlines())
    for package_id, package in packages.items():
        if versions.get(package_id) != package['version']:
            raise ValueError(f"Package changed since snapshot: {package_id}")
    for name in output('dpkg-query', '-L', *sorted(packages)).splitlines():
        allowed = name.startswith(('/usr/', '/lib/', '/lib64/', '/bin/', '/sbin/', '/opt/ros/'))
        allowed = allowed or name in ETC_FILES or name.startswith(ETC_ALLOWED)
        if not allowed:
            continue
        path = Path(name)
        if path.is_file() or path.is_symlink():
            files[canonical(name)] = path
    # Runtime-generated alternatives and CA links are not listed by dpkg -L.
    for directory in ('/etc/alternatives', '/etc/ssl/certs', '/etc/fonts'):
        for path in walk(directory):
            if path.is_symlink() or path.is_file():
                files[str(path)] = path
    mappings = [(restored / 'project', ROOT), (restored / 'host', ''),
                (restored / 'python', USER_HOME + '/.local/share/uv/python'),
                (metadata, '/usr/share/ceres-snapshot')]
    for source, prefix in mappings:
        for path in walk(source):
            files[canonical(prefix + '/' + path.relative_to(source).as_posix())] = path
    node = Path(output('which', 'node')).resolve().parent.parent
    for path in walk(node):
        files['/opt/ceres-node/' + path.relative_to(node).as_posix()] = path
    files['/usr/local/bin/uv'] = Path(output('which', 'uv')).resolve()
    # update-alternatives / ldconfig create links not owned by a .deb, e.g.
    # libblas.so.3. Include only links resolving to an already selected file.
    for directory in ('/usr/bin', '/usr/sbin', '/usr/lib', '/usr/lib64'):
        for path in walk(directory):
            if path.is_symlink() and canonical(str(path.resolve())) in files:
                files.setdefault(canonical(str(path)), path)
    # Materialize link targets needed by alternatives/ELF loaders, never whole homes.
    for _ in range(5):
        additions = {}
        for name, path in files.items():
            if path.is_symlink() and not path.is_relative_to(restored):
                target = Path(os.path.abspath(path.parent / os.readlink(path)))
                if str(target).startswith(('/usr/', '/bin/', '/lib/', '/lib64/', '/opt/ros/')) and target.is_file():
                    if canonical(str(target)) not in files:
                        additions[canonical(str(target))] = target
        if not additions:
            break
        files.update(additions)
    seen, dirs, manifest = set(), set(), []
    with tarfile.open(args.output / 'rootfs.tar', 'w') as tar:
        def parents(name):
            parent = posixpath.dirname(name)
            if parent and parent not in dirs:
                parents(parent)
                info = tarfile.TarInfo(parent)
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                info.uid = info.gid = 1000 if parent.startswith('home/peakpeng') else 0
                tar.addfile(info)
                dirs.add(parent)

        def add(name, data=None, path=None, link=None, mode=0o644, mtime=0):
            name = name.lstrip('/')
            if name in seen:
                return
            seen.add(name)
            parents(name)
            info = tarfile.TarInfo(name)
            info.uid = info.gid = 1000 if name.startswith('home/peakpeng/') else 0
            info.mode, info.mtime = mode & 0o777, mtime
            row = {'path': '/' + name}
            if link is not None:
                info.type, info.linkname = tarfile.SYMTYPE, link
                tar.addfile(info)
                row['link'] = link
            else:
                if path is not None:
                    data = path.read_bytes()
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
                row.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            manifest.append(row)

        for alias in ('bin', 'sbin', 'lib', 'lib64'):
            add(alias, link='usr/' + alias)
        for name, path in sorted(files.items()):
            link = None
            if path.is_symlink():
                link = os.readlink(path)
                if path.is_relative_to(restored):
                    target = Path(os.path.abspath(path.parent / link))
                    for source, prefix in mappings:
                        if target.is_relative_to(source):
                            link = prefix + '/' + target.relative_to(source).as_posix()
                            break
                    else:
                        raise ValueError(f'Unmapped restored link: {path}')
            st = path.lstat()
            add(name, path=path, link=link, mode=st.st_mode, mtime=int(st.st_mtime))
        proc = subprocess.Popen(['git', '-C', str(args.repo), 'archive', args.commit], stdout=subprocess.PIPE)
        with tarfile.open(fileobj=proc.stdout, mode='r|') as code:
            for item in code:
                if item.isfile():
                    add(ROOT + '/' + item.name, data=code.extractfile(item).read(), mode=item.mode, mtime=item.mtime)
                elif item.issym():
                    add(ROOT + '/' + item.name, link=item.linkname)
        if proc.wait() != 0:
            raise RuntimeError('git archive failed')
        add('/etc/passwd', data=b'root:x:0:0:root:/root:/bin/bash\npeakpeng:x:1000:1000:CERES:/home/peakpeng:/bin/bash\n')
        add('/etc/group', data=b'root:x:0:\npeakpeng:x:1000:\n')
        add('/etc/os-release', data=(metadata / 'os-release').read_bytes())
        add('/usr/share/ceres-snapshot/image-packages.json', data=json.dumps(list(packages.values()), indent=2).encode())
        add(ROOT + '/CERES_SOURCE.json', data=json.dumps({'commit': args.commit, 'snapshot': 'published, authentication scrubbed'}).encode())
        # /tmp must remain usable by the unprivileged application account.
        info = tarfile.TarInfo('tmp'); info.type = tarfile.DIRTYPE; info.mode = 0o1777; tar.addfile(info)
    report = {'source_commit': args.commit, 'packages': len(packages), 'files': len(manifest), 'files_manifest': manifest}
    (args.output / 'image-build.json').write_text(json.dumps(report, ensure_ascii=False))
    print(json.dumps({k: v for k, v in report.items() if k != 'files_manifest'}))


if __name__ == '__main__':
    main()
