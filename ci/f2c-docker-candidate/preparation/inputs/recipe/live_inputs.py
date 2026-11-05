"""Pinned public downloads, safe data extraction and portable measurement rows."""
import hashlib
import json
import os
import pathlib
import shutil
import stat
import subprocess
import tarfile
import urllib.parse
import urllib.request
import zipfile

from guardlib import digest, relative_path, require, sha256

DOWNLOAD_HOSTS = {'files.pythonhosted.org', 'codeload.github.com', 'github.com',
                  'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}

def approved_url(url):
    p = urllib.parse.urlparse(url)
    require(p.scheme == 'https' and p.hostname in DOWNLOAD_HOSTS and p.username is None and p.password is None, 'Unapproved source download URL')
    return url

class RestrictedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        approved_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def fetch(row, destination):
    digest(row['sha256'])
    approved_url(row['url'])
    require(isinstance(row['bytes'], int) and 0 < row['bytes'] <= 512 * 1024**2, 'Unbounded source download')
    if destination.exists() or destination.is_symlink():
        require(destination.is_file() and not destination.is_symlink() and destination.stat().st_size == row['bytes'] and sha256(destination) == row['sha256'], 'Existing download differs')
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(destination.name + '.partial')
    opener = urllib.request.build_opener(RestrictedRedirect())
    with opener.open(row['url'], timeout=90) as response, tmp.open('xb') as stream:
        approved_url(response.geturl())
        size = 0
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            require(size <= row['bytes'], 'Source download exceeds pinned size')
            stream.write(chunk)
    require(tmp.stat().st_size == row['bytes'] and sha256(tmp) == row['sha256'], 'Downloaded source hash/size mismatch')
    tmp.replace(destination)
    return destination

def fresh_directory(path):
    require(not path.exists() and not path.is_symlink(), 'Extraction destination already exists')
    path.mkdir(parents=True, mode=0o700)

def tar_data_only(archive_path, destination, interface_only=False):
    """Never call extractall; reject aliases/special files and bounded output."""
    fresh_directory(destination)
    names, roots, total = set(), set(), 0
    with tarfile.open(archive_path, 'r:gz') as archive:
        for member in archive:
            raw = member.name.rstrip('/')
            relative_path(raw)
            parts = pathlib.PurePosixPath(raw).parts
            roots.add(parts[0])
            require(len(roots) == 1, 'Archive has multiple top-level roots')
            if len(parts) == 1:
                require(member.isdir(), 'Archive root is not a directory')
                continue
            name = '/'.join(parts[1:])
            if interface_only and not (name.startswith('include/') or name.startswith('lib/cmake/') and name.endswith('.cmake')):
                continue
            require(name not in names, 'Duplicate archive member')
            names.add(name)
            target = destination / relative_path(name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            require(member.isfile() and not member.issym() and not member.islnk(), 'Nonregular source member')
            require(member.size <= 256 * 1024**2, 'Oversized archive member')
            total += member.size
            require(total <= 2 * 1024**3 and len(names) <= 100000, 'Unbounded archive extraction')
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
    require(names, 'No selected archive input files')

def wheel_data_only(wheel, destination):
    fresh_directory(destination)
    total, names = 0, set()
    with zipfile.ZipFile(wheel) as archive:
        for member in archive.infolist():
            name = member.filename.rstrip('/')
            relative_path(name)
            require(name not in names, 'Duplicate ZIP member')
            names.add(name)
            mode = (member.external_attr >> 16) & 0xffff
            kind = stat.S_IFMT(mode)
            require(kind in (0, stat.S_IFREG, stat.S_IFDIR), 'ZIP alias/special file refused')
            target = destination / relative_path(name)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            total += member.file_size
            require(member.file_size <= 256 * 1024**2 and total <= 2 * 1024**3 and len(names) <= 100000, 'Unbounded wheel extraction')
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output)
            target.chmod(0o755 if mode & 0o111 else 0o644)

def path_ref(path, root, prefix):
    path = pathlib.Path(path)
    for base, directory in (('candidate', root), ('python', prefix)):
        if path.is_relative_to(directory):
            return {'base': base, 'path': path.relative_to(directory).as_posix()}
    require(path.is_absolute(), 'Measurement path is not absolute')
    return {'base': 'system', 'path': path.relative_to('/').as_posix()}

def file_row(path, root, prefix):
    path = pathlib.Path(path)
    require(path.is_file(), 'Missing measurement input')
    return {'ref': path_ref(path, root, prefix), 'resolved_ref': path_ref(path.resolve(strict=True), root, prefix),
            'symlink_target': os.readlink(path) if path.is_symlink() else None, 'sha256': sha256(path)}

def write_json_new(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')

def verify_system_archive_bytes(archives, selected_paths):
    """Match selected installed command/provider bytes to authenticated .deb data.

    dpkg-deb only streams archive data; no package maintainer script runs here.
    All archive paths must already have passed the signed-snapshot hash gate.
    """
    wanted = {pathlib.Path(p).resolve(strict=True) for p in selected_paths}
    found = {}
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'}
    for archive in archives:
        proc = subprocess.Popen(['/usr/bin/dpkg-deb', '--fsys-tarfile', str(archive)], env=env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            with tarfile.open(fileobj=proc.stdout, mode='r|') as contents:
                for member in contents:
                    name = member.name.removeprefix('./').rstrip('/')
                    if not name or name == '.':
                        continue
                    relative_path(name)
                    path = (pathlib.Path('/') / name).resolve(strict=False)
                    if path not in wanted or not member.isfile():
                        continue
                    h = hashlib.sha256()
                    stream = contents.extractfile(member)
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        h.update(chunk)
                    measured = h.hexdigest()
                    require(sha256(path) == measured, 'Installed command/provider differs from official package bytes')
                    require(path not in found or found[path]['sha256'] == measured, 'Conflicting official package providers')
                    found[path] = {'sha256': measured, 'archive': pathlib.Path(archive).name, 'member': name}
            require(proc.wait(timeout=60) == 0, 'Failed to read official package payload')
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            proc.stdout.close()
    require(wanted.issubset(found), 'Missing official package byte provenance for selected command/provider')
    return {str(path): row for path, row in sorted(found.items())}
