#!/usr/bin/env python3
"""Assemble the self-contained backend that ships inside the installer.

The result keeps the repository's relative layout, so the gateway needs no packaged-mode paths. On Linux the Python runtime
sits at src/lab/.venv, which is where the gateway and its solver workers already look for it. ROS is not part of the bundle:
its bridge is compiled and fingerprinted per machine, so Gazebo reaches installed apps through the container backend.

    python3 src/desktop/scripts/stage_backend.py --target linux|win [--out src/desktop/build/backend]

Needs uv and a host CPython 3.12. The Windows tree can be staged from Linux because wheels are resolved for the target.
"""
import argparse
import compileall
import hashlib
import json
import os
import py_compile
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LAB = ROOT / 'src' / 'lab'
PYTHON = '3.12.13'
MSVC_RUNTIME = 'msvc-runtime==14.44.35112'     # redistributable; many wheels import msvcp140.dll from the system otherwise
TARGETS = {'linux': {'python': f'cpython-{PYTHON}-linux-x86_64-gnu', 'platform': 'x86_64-manylinux_2_28', 'site': 'lib/python3.12/site-packages'},
           'win': {'python': f'cpython-{PYTHON}-windows-x86_64-none', 'platform': 'x86_64-pc-windows-msvc', 'site': 'Lib/site-packages'}}

# Application tree: (source, destination relative to app/). Everything is read-only at run time.
TREE = ['src/lab/urp_lab', 'src/lab/models', 'src/lab/pyproject.toml', 'src/lab/uv.lock', 'src/research/src/urp_research', 'src/frontend/core/routing',
        'src/backend/backend', 'src/backend/config', 'tools/runtime_db.py']
SKIP_DIRS = {'__pycache__', '.pytest_cache', 'tests', 'test', 'testing', 'idlelib', 'tkinter', 'turtledemo', 'ensurepip', 'lib2to3', 'pydoc_data', 'include', 'tcl9.0', 'tk9.0', 'tcl8.6', 'tk8.6', 'itcl4.3.2', 'share', 'pkgconfig'}
# Imported or read at run time by the packages themselves. pyproj resolves proj.db from its bundled share/proj, and
# Transformer.from_crs (GeoJSON import, field projection) fails on machines without a system PROJ when it is trimmed.
KEEP_DIRS = {'numpy/testing', 'numpy/_core/tests', 'scipy/_lib/_test', 'pyproj/proj_dir/share'}
SKIP_SUFFIXES = ('.pyi', '.a', '.lib', '.pdb', '.c', '.cc', '.cpp', '.h', '.hpp', '.pxd', '.pyx', '.md', '.rst')


def run(cmd, **kw):
    print('+', ' '.join(map(str, cmd)), flush=True)
    return subprocess.run(cmd, check=True, **kw)


def uv_python(key: str, cache: Path) -> Path:
    """The relocatable CPython build uv manages, fetched for the target platform."""
    found = next((p for p in cache.glob(f'{key}*') if p.is_dir() and not p.is_symlink()), None)
    if found is not None:
        return found
    if 'windows' in key and os.name != 'nt':
        return download_python(key, cache)      # uv installs a foreign platform only as far as its symlink step
    run(['uv', 'python', 'install', key, '--install-dir', cache, '--no-bin', '--force'])
    return next(p for p in cache.glob(f'{key}*') if p.is_dir() and not p.is_symlink())


def download_python(key: str, cache: Path) -> Path:
    listing = json.loads(subprocess.check_output(['uv', 'python', 'list', '--all-platforms', '--output-format', 'json']))
    url = next(r['url'] for r in listing if r['key'] == key)
    archive = cache / f'{key}.tar.gz'
    if not archive.exists():
        run(['curl', '-fL', '--retry', '5', '--retry-all-errors', '-C', '-', '-o', f'{archive}.part', url])
        os.replace(f'{archive}.part', archive)
    dest = cache / key
    staging = cache / f'{key}.tmp'
    shutil.rmtree(staging, ignore_errors=True)
    with tarfile.open(archive) as tar:
        tar.extractall(staging, filter='data')
    os.replace(staging / 'python', dest)
    shutil.rmtree(staging)
    return dest


def slim(site: Path) -> int:
    """Test suites, headers and static libraries are dead weight at run time."""
    before = sum(f.stat().st_size for f in site.rglob('*') if f.is_file())
    for path in sorted(site.rglob('*'), key=lambda p: len(p.parts), reverse=True):
        rel = path.relative_to(site).as_posix()
        if path.is_dir():
            if path.name in SKIP_DIRS and not any(rel.endswith(k) or k in rel for k in KEEP_DIRS):
                shutil.rmtree(path, ignore_errors=True)
        elif path.exists() and path.suffix in SKIP_SUFFIXES:
            path.unlink()
    return before - sum(f.stat().st_size for f in site.rglob('*') if f.is_file())


def copy_tree(source: Path, dest: Path):
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '.pytest_cache', 'build', 'node_modules', '*.sqlite3', 'runtime', '.venv')
    if source.is_dir():
        shutil.copytree(source, dest, ignore=ignore, dirs_exist_ok=True)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)


def stage(target: str, out: Path, cache: Path) -> dict:
    spec = TARGETS[target]
    home = out / target
    if home.exists():
        shutil.rmtree(home)
    app = home / 'app'
    runtime = app / 'src/lab/.venv' if target == 'linux' else home / 'python'
    runtime.parent.mkdir(parents=True)

    base = uv_python(spec['python'], cache)
    shutil.copytree(base, runtime, symlinks=True)
    for name in ('lib/python3.12/test', 'Lib/test'):
        shutil.rmtree(runtime / name, ignore_errors=True)
    # uv refuses to manage a site directory it does not own; --target only needs the path.
    site = runtime / spec['site']
    req = out / f'requirements-{target}.txt'
    run(['uv', 'export', '--project', LAB, '--frozen', '--no-dev', '--no-emit-project', '--no-hashes', '--no-annotate', '--no-header', '-o', req], stdout=subprocess.DEVNULL)
    run(['uv', 'pip', 'install', '--python-platform', spec['platform'], '--python-version', '3.12', '--target', site, '--no-deps', '--only-binary', ':all:', '-r', req])
    if target == 'win':
        extra = out / 'msvc-runtime'
        shutil.rmtree(extra, ignore_errors=True)
        run(['uv', 'pip', 'install', '--python-platform', spec['platform'], '--python-version', '3.12', '--target', extra, '--no-deps', '--only-binary', ':all:', MSVC_RUNTIME])
        dlls = list(extra.rglob('*.dll'))
        if not any(d.name.lower() == 'msvcp140.dll' for d in dlls):
            sys.exit('msvc-runtime 里没有 msvcp140.dll')
        for dll in dlls:
            shutil.copy2(dll, runtime / dll.name)
        shutil.rmtree(extra)
    for name in ('pip', 'setuptools', 'wheel'):
        for path in site.glob(f'{name}*'):
            shutil.rmtree(path, ignore_errors=True) if path.is_dir() else path.unlink()
    saved = slim(site) + slim(runtime / ('lib/python3.12' if target == 'linux' else 'Lib'))
    for path in runtime.glob('lib/*.a'):
        path.unlink()
    shutil.rmtree(runtime / 'include', ignore_errors=True)
    shutil.rmtree(runtime / 'share', ignore_errors=True)

    for item in TREE:
        copy_tree(ROOT / item, app / item)
    dist = ROOT / 'src/frontend/dist'
    if not (dist / 'index.html').is_file():
        sys.exit('前端尚未构建：先运行 npm run build --prefix src/frontend')
    for name in ('index.html', 'favicon.svg', 'assets'):
        copy_tree(dist / name, app / 'src/frontend/dist' / name)   # dist/data is the history pack, downloaded on demand

    # Hash-validated bytecode: the install is read-only and mtimes do not survive packaging.
    for tree in (site, runtime / ('lib/python3.12' if target == 'linux' else 'Lib'), app / 'src/lab/urp_lab', app / 'src/research/src/urp_research', app / 'src/backend/backend'):
        compileall.compile_dir(tree, quiet=2, invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH, workers=0, optimize=0)

    files = [f for f in home.rglob('*') if f.is_file() and not f.is_symlink()]
    lock = hashlib.sha256((LAB / 'uv.lock').read_bytes()).hexdigest()
    manifest = {'schema': 'urp.desktop-backend/1', 'target': target, 'python': PYTHON, 'uv_lock_sha256': lock, 'files': len(files), 'bytes': sum(f.stat().st_size for f in files),
                'trimmed_bytes': saved, 'staged_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    # A backend built from a local dirty tree must never be mistaken for a published commit.
    manifest['source_commit'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    manifest['source_dirty'] = bool(subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=ROOT, text=True).strip())
    manifest['source_files'] = {p.relative_to(app).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in sorted(app.rglob('*')) if p.is_file() and p.suffix in ('.py', '.js', '.json', '.toml', '.lock', '.html', '.css')
                                and '.venv' not in p.parts and '__pycache__' not in p.parts}
    (home / 'backend-manifest.json').write_text(json.dumps(manifest, indent=1))
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', choices=sorted(TARGETS), default='win' if os.name == 'nt' else 'linux')
    parser.add_argument('--out', type=Path, default=ROOT / 'src/desktop/build/backend')
    parser.add_argument('--cache', type=Path, default=ROOT / 'src/desktop/build/python-cache')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    print(json.dumps(stage(args.target, args.out.resolve(), args.cache.resolve()), indent=1))


if __name__ == '__main__':
    main()
