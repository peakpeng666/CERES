#!/usr/bin/env python3
"""Offline, relocated validation only. No production manifest or publication writes."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

EXCLUDES = {'.git', '.venv', '__pycache__', '.pytest_cache', 'node_modules', 'runtime'}
MANIFEST = 'src/lab/vendor/fields2cover-manifest.json'
EXTENSION = 'fields2cover/_fields2cover_python.so'
ORTOOLS_SHA = '5190e6d7e163ac2975abb9d582b6d405396621f8a15f2ac7c3de3d50d6276887'
SOURCE_COMMIT = '18557cfc61f291e8ac5fa4a80d9df212e1df0030'
RPATH = ['$ORIGIN/../ortools/.libs', '$ORIGIN/../ortools.libs']

def require(ok, message):
    if not ok:
        raise RuntimeError(message)

def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def tree(root, allowed=None, excludes=EXCLUDES):
    root = Path(root); allowed = Path(allowed or root).resolve()
    require(root.is_dir() and not root.is_symlink() and root.resolve() == root, 'Unsafe tree root')
    rows = {}
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(n for n in dirs if n not in excludes)
        for n in sorted(dirs + files):
            if n in excludes:
                continue
            p = Path(base) / n; key = p.relative_to(root).as_posix()
            if p.is_symlink():
                require(p.resolve(strict=True).is_relative_to(allowed), 'Escaping source symlink')
                rows[key] = 'symlink:' + os.readlink(p)
            elif p.is_file():
                rows[key] = sha(p)
            elif p.is_dir():
                rows[key] = 'directory'
            else:
                raise RuntimeError('Special input file')
    return rows

def clean(config):
    out = Path(config['output']); py = out / 'checkout/src/lab/.venv/bin/python'
    return {'PATH': str(py.parent) + ':' + str(Path(config['tools']['python']['path']).parent) + ':' + str(Path(config['tools']['node']['path']).parent) + ':/usr/bin:/bin',
            'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'TZ': 'UTC', 'HOME': str(out / 'home'),
            'TMPDIR': str(out / 'tmp'), 'XDG_CACHE_HOME': str(out / 'cache'),
            'MPLCONFIGDIR': str(out / 'cache/matplotlib'), 'PYTHONDONTWRITEBYTECODE': '1',
            'PYTHONNOUSERSITE': '1', 'PYTHONPATH': str(out / 'checkout/src/lab'),
            'URP_LAB_RUNTIME': str(out / 'runtime'), 'URP_ROS_MAPS': str(out / 'maps')}

def anchored(config_path, expected):
    p = Path(config_path)
    require(p.is_file() and not p.is_symlink() and sha(p) == expected, 'Validation config anchor mismatch')
    c = json.loads(p.read_text())
    require(c['schema'] == 1 and c['source_commit'] == SOURCE_COMMIT, 'Wrong validation source')
    for path, digest in c['harness'].items():
        require(sha(path) == digest and not Path(path).is_symlink(), 'Harness changed')
    require(str(Path(__file__).resolve()) in c['harness'], 'Runner not anchored')
    for item in c['tools'].values():
        require(str(Path(item['path']).resolve()) == item['resolved'] and sha(item['path']) == item['sha256'], 'Tool changed')
    require(sys.version_info[:3] == (3, 12, 13), 'Require CPython3.12.13')
    require(sha(c['wheel']['path']) == c['wheel']['sha256'], 'Candidate wheel changed')
    require(c['wheel']['sha256'] != '0fe53e459024b282be455b2a4bdbca3d93efa029539ff87c88c1105361344c19', 'Historical wheel prohibited')
    root = Path(c['candidate_root']); out = Path(c['output'])
    require(out.is_absolute() and not out.is_relative_to(root) and not root.is_relative_to(out), 'Validation must be relocated outside build tree')
    for parent in (out.parent, *out.parent.parents):
        require(not parent.is_symlink(), 'Output ancestor alias')
    require(out.parent.stat().st_uid == os.geteuid(), 'Output parent not owned')
    for item in c['trees']:
        target = PurePosixPath(item['destination'])
        require(not target.is_absolute() and all(p not in ('', '.', '..') for p in item['destination'].split('/')), 'Unsafe copy destination')
        original = Path(item['source'])
        require(not out.is_relative_to(original) and not original.is_relative_to(out), 'Validation output aliases input tree')
    # Bootstrap only the anchored guard files; no ambient Python path/imports.
    for name in ['guardlib', 'build_guard']:
        q = root / 'recipe' / (name + '.py')
        require(str(q) in c['harness'], 'Guard is not anchored')
        spec = importlib.util.spec_from_file_location(name, q)
        module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
    sys.modules['build_guard'].network_guard(c['network'])
    # Full build input closure, not just the index file itself.
    check = root / 'recipe/verify_offline_inputs.py'
    require(str(check) in c['harness'], 'Build verifier not anchored')
    verify_build_closure(c)
    return c

def verify_build_closure(c):
    helper = Path(c['candidate_root']) / 'recipe/verify_validation_build.py'
    require(str(helper) in c['harness'] and sha(helper) == c['harness'][str(helper)], 'Base closure helper not anchored')
    result = subprocess.run([c['tools']['python']['path'], '-I', '-B', str(helper),
        '--root', c['candidate_root'], '--python', c['tools']['python']['path'],
        '--expected-index-sha256', c['build_index_sha256']], env=clean(c),
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True, text=True)
    require(result.returncode == 0, 'Base-interpreter build closure failed: ' + result.stderr)

def manifest_bytes(c):
    manifest = dict(c['manifest'])
    manifest.update(wheel_sha256=c['wheel']['sha256'], binary_sha256=c['wheel']['extension_sha256'])
    require(manifest['acceptance_state'] == 'candidate_under_validation_not_production_accepted', 'Not a candidate manifest')
    return (json.dumps(manifest, indent=2) + '\n').encode()

def check_inputs(c, copied=False):
    out = Path(c['output'])
    for item in c['trees']:
        p = out / item['destination'] if copied else Path(item['source'])
        allowed = out / 'checkout' if copied and item['destination'].startswith('checkout/') else p
        if not copied:
            allowed = Path(item.get('allowed_root', item['source']))
        expected = dict(item['entries'])
        if copied and item['destination'] == 'checkout/src/lab':
            expected['vendor/fields2cover-manifest.json'] = hashlib.sha256(manifest_bytes(c)).hexdigest()
            expected['vendor/fields2cover-historical-manifest.json'] = c['historical_manifest_sha256']
        require(tree(p, allowed, EXCLUDES if item['destination'].startswith('checkout/') else set()) == expected, 'Input membership/content drift: ' + item['destination'])
    require(sha(Path(c['source_root']) / MANIFEST) == c['historical_manifest_sha256'], 'Historical manifest changed')

def wheel_files(path):
    result = {}
    with zipfile.ZipFile(path) as z:
        for item in z.infolist():
            n = item.filename; p = PurePosixPath(n)
            require(n not in result and not p.is_absolute() and all(x not in ('', '.', '..') for x in n.split('/')), 'Unsafe wheel member')
            require(p.parts[0] in {'fields2cover', 'fields2cover-2.1.0.dist-info'}, 'Unexpected wheel layout')
            require(not stat.S_ISLNK(item.external_attr >> 16) and not item.is_dir(), 'Wheel special/directory entry')
            result[n] = z.read(n)
    require(EXTENSION in result, 'Candidate extension absent')
    return result

def installed(c):
    out = Path(c['output']); site = out / 'checkout/src/lab/.venv/lib/python3.12/site-packages'
    require(sha(out / 'checkout/src/lab/.venv/bin/python') == c['tools']['python']['sha256'], 'Relocated interpreter changed')
    require((out / 'candidate-manifest.json').read_bytes() == manifest_bytes(c), 'Candidate manifest changed')
    expected = dict(next(x for x in c['trees'] if x['destination'] == 'dependency-site')['entries'])
    for n, data in wheel_files(c['wheel']['path']).items():
        require(n not in expected, 'Dependency overlaps F2C wheel')
        expected[n] = hashlib.sha256(data).hexdigest()
        for p in PurePosixPath(n).parents:
            if str(p) != '.':
                expected[str(p)] = 'directory'
    require(tree(site, excludes=set()) == expected, 'Installed dependency/wheel tree drift')
    require(sha(site / EXTENSION) == c['wheel']['extension_sha256'], 'Wrong installed extension')
    return site

def is_elf(path):
    with path.open('rb') as f:
        return f.read(4) == b'\x7fELF'

def elf(path, readelf):
    def call(*args):
        return subprocess.check_output([readelf, *args, str(path)], env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'}, text=True, stderr=subprocess.PIPE)
    d = call('-dW'); v = call('--version-info', '-W'); symbols = call('--dyn-syms', '-W')
    needs = {}; current = None; section = ''
    definitions = set()
    for line in v.splitlines():
        if 'Version needs section' in line: section = 'needs'
        elif 'Version definition section' in line: section = 'definitions'
        elif 'Version symbols section' in line: section = 'symbols'
        m = re.search(r'File: (\S+)', line)
        if m: current = m.group(1)
        m = re.search(r'Name: (\S+)', line)
        if m and section == 'needs' and current: needs.setdefault(current, set()).add(m.group(1))
        if m and section == 'definitions': definitions.add(m.group(1))
    defined = set(); undefined = set(); defined_versions = {}; undefined_versions = {}
    for line in symbols.splitlines():
        parts = line.split()
        if len(parts) >= 8 and parts[0].endswith(':') and parts[4] in {'GLOBAL', 'WEAK'}:
            token = parts[7]; name = token.split('@')[0]
            version = token.split('@')[-1] if '@' in token else ''
            if parts[6] != 'UND':
                defined.add(name); defined_versions.setdefault(name, set()).add(version)
                if '@@' in token: defined_versions[name].add('')
            elif parts[4] == 'GLOBAL':
                undefined.add(name); undefined_versions.setdefault(name, set()).add(version)
    return {'path': str(path), 'sha256': sha(path), 'needed': re.findall(r'\(NEEDED\).*?\[(.*?)\]', d),
            'soname': re.findall(r'\(SONAME\).*?\[(.*?)\]', d),
            'rpath': re.findall(r'\((?:RPATH|RUNPATH)\).*?\[(.*?)\]', d),
            'needs': needs, 'definitions': definitions, 'defined': defined, 'undefined': undefined, 'defined_versions': defined_versions, 'undefined_versions': undefined_versions}

def libz_alternatives(c, site):
    policy = c['provider_alternatives']
    require(set(policy) == {'libz.so.1'}, 'Only libz alternate providers are allowed')
    row = policy['libz.so.1']
    require(row['policy'] == 'exactly-one-first-loaded-compatible-provider', 'Unknown libz policy')
    require(row['wheel']['relative_path'] == 'ortools/.libs/libz.so.1', 'Wrong wheel libz location')
    wheel = (site / row['wheel']['relative_path']).resolve(strict=True)
    system = Path(row['system']['path'])
    require(wheel.is_relative_to(site) and system.resolve(strict=True) == system and not system.is_relative_to(site), 'libz provider path alias')
    require(sha(wheel) == row['wheel']['sha256'] and sha(system) == row['system']['sha256'], 'libz provider bytes changed')
    return [str(wheel), str(system)]

def check_libz_symbols(seen, alternatives):
    libs = [seen[path] for path in alternatives]
    require(all(e['soname'] == ['libz.so.1'] for e in libs), 'Alternative is not libz.so.1')
    exports = set().union(*(e['defined'] for e in libs))
    required = {}
    for e in seen.values():
        for name, versions in e['undefined_versions'].items():
            if name in exports or any(version.startswith('ZLIB_') for version in versions):
                required.setdefault(name, set()).update(versions)
    require(required, 'No measured zlib symbol requirement found')
    for lib in libs:
        for name, versions in required.items():
            require(versions <= lib['defined_versions'].get(name, set()), 'libz alternate lacks required strong/versioned symbol: ' + name)
    return {name: sorted(versions) for name, versions in sorted(required.items())}

def static_review(c):
    site = installed(c); readelf = c['tools']['readelf']['path']; extension = site / EXTENSION
    first = elf(extension, readelf)
    require(first['rpath'] == [':'.join(RPATH)], 'F2C RUNPATH is not the exact relative wheel layout')
    require('libgdal.so.30' in first['needed'] and 'libtinyxml2.so.9' in first['needed'], 'Not a Jammy system ABI candidate')
    alternatives = libz_alternatives(c, site)
    # The locked interpreter prefix contains stdlib consumers such as zlib,
    # which zipfile may load before any mixed-import probe. Their full symbol
    # obligations belong in the same pre-load provider proof.
    native_roots = [site, Path(c['python_prefix'])]
    native_files = [p for root in native_roots for p in root.rglob('*') if p.is_file() and '.so' in p.name and is_elf(p)]
    queue = [extension, Path(c['tools']['python']['resolved'])] + [Path(p) for p in alternatives] + native_files
    seen = {}; providers = {}
    while queue:
        path = queue.pop().resolve()
        if str(path) in seen: continue
        e = elf(path, readelf); seen[str(path)] = e
        search = []
        for value in e['rpath']:
            for entry in value.split(':'):
                require(entry and (entry.startswith(('$ORIGIN', '${ORIGIN}')) or entry.startswith('/')), 'Unreviewed loader path')
                q = Path(entry.replace('${ORIGIN}', str(path.parent)).replace('$ORIGIN', str(path.parent))).resolve()
                require(q.is_relative_to(site) or str(q) in c['system_library_dirs'] or q.is_relative_to(Path(c['python_prefix'])), 'Loader path leaves approved runtime roots')
                search.append(q)
        search += [Path(x) for x in c['system_library_dirs']]
        for name in e['needed']:
            require('/' not in name, 'Absolute/path DT_NEEDED rejected')
            matches = [q / name for q in search if (q / name).is_file()]
            require(matches, 'Unresolved ELF dependency: ' + name)
            target = matches[0].resolve()
            if name == 'libz.so.1':
                require(str(target) in alternatives, 'Unapproved alternate libz provider')
            else:
                if name in providers: require(providers[name] == str(target), 'Competing SONAME provider: ' + name)
                providers[name] = str(target)
            queue.append(target)
    for e in seen.values():
        for name, versions in e['needs'].items():
            targets = alternatives if name == 'libz.so.1' else [providers.get(name)]
            require(all(target in seen and versions <= seen[target]['definitions'] for target in targets), 'Missing ELF symbol versions: ' + name)
    zlib_symbols = check_libz_symbols(seen, alternatives)
    definitions = set().union(*(e['defined'] for e in seen.values()))
    missing = set().union(*(e['undefined'] for e in seen.values())) - definitions
    require(not missing, 'Unresolved strong dynamic symbols: ' + repr(sorted(missing)[:20]))
    ort = providers.get('libortools.so.9')
    require(ort == str((site / 'ortools/.libs/libortools.so.9').resolve()) and sha(ort) == ORTOOLS_SHA, 'Wrong static OR-Tools provider')
    report = {'passed': True, 'providers': providers, 'provider_alternatives': {'libz.so.1': alternatives}, 'required_zlib_symbols': zlib_symbols, 'required_zlib_versions': sorted(set().union(*(e['needs'].get('libz.so.1', set()) for e in seen.values()))), 'approved_zlib_exports': {p: {name: sorted(seen[p]['defined_versions'][name]) for name in zlib_symbols} for p in alternatives}, 'files': {p: e['sha256'] for p,e in seen.items()}, 'needed': {p:e['needed'] for p,e in seen.items()}, 'sonames': {p:e['soname'] for p,e in seen.items()}}
    return report

def prepare(c):
    check_inputs(c); out = Path(c['output'])
    require(not out.exists() and not out.is_symlink(), 'Never reuse validation output')
    out.mkdir(mode=0o700)
    for name in ['home', 'tmp', 'cache', 'evidence']: (out / name).mkdir(mode=0o700)
    for item in c['trees']:
        dst = out / item['destination']; dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(item['source'], dst, symlinks=True, ignore=(lambda _, names: [n for n in names if n in EXCLUDES]) if item['destination'].startswith('checkout/') else None)
    source = out / 'checkout' / MANIFEST
    backup = source.with_name('fields2cover-historical-manifest.json')
    require(not backup.exists() and not backup.is_symlink(), 'Historical backup collision')
    shutil.copyfile(source, backup)
    text = manifest_bytes(c); source.write_bytes(text); (out / 'candidate-manifest.json').write_bytes(text)
    venv = out / 'checkout/src/lab/.venv'
    subprocess.run([c['tools']['python']['path'], '-I', '-B', '-m', 'venv', '--without-pip', '--copies', str(venv)], env=clean(c), check=True, stdin=subprocess.DEVNULL, close_fds=True)
    site = venv / 'lib/python3.12/site-packages'
    require(not list(site.iterdir()), 'Nonempty initial venv')
    shutil.copytree(out / 'dependency-site', site, dirs_exist_ok=True, symlinks=True)
    for name, data in wheel_files(c['wheel']['path']).items():
        p = site / name; p.parent.mkdir(parents=True, exist_ok=True)
        with p.open('xb') as f: f.write(data)
    check_inputs(c, copied=True); installed(c)
    report = static_review(c)
    (out / 'evidence/static-elf.json').write_text(json.dumps(report, indent=2) + '\n')
    (out / 'evidence/prepared.json').write_text(json.dumps({'prepared': True, 'native_imported': False, 'static_elf_sha256': sha(out / 'evidence/static-elf.json')}) + '\n')

def validate_mapped_libz(maps, static, sonames=None):
    paths = [p for p in maps if ('libz.so.1' in sonames.get(p, []))] if sonames is not None else [p for p in maps if Path(p).name.startswith('libz.so.1')]
    require(len(paths) == 1 and paths[0] in static['provider_alternatives']['libz.so.1'] and maps[paths[0]] == static['files'][paths[0]], 'Multiple/wrong mapped libz providers')
    return paths[0]

def mapped_providers(c, binary, static):
    maps = {}
    for line in Path('/proc/self/maps').read_text().splitlines():
        parts = line.split(maxsplit=5)
        if len(parts) != 6 or not parts[5].startswith('/'): continue
        require(not parts[5].endswith(' (deleted)'), 'Deleted mapped native file')
        p = Path(parts[5]).resolve()
        if p.is_file() and '.so' in p.name: maps[str(p)] = sha(p)
    sonames = {p: static['sonames'][p] if p in static['sonames'] else elf(Path(p), c['tools']['readelf']['path'])['soname'] for p in maps}
    ort = [p for p in maps if any(name.startswith('libortools.so') for name in sonames[p])]
    require(ort == [static['providers']['libortools.so.9']] and maps[ort[0]] == ORTOOLS_SHA, 'Multiple/wrong mapped OR-Tools providers')
    validate_mapped_libz(maps, static, sonames)
    require(maps.get(str(binary)) == c['wheel']['extension_sha256'], 'Mapped extension differs')
    names = {Path(p).name for p in static['files']}
    for p, digest in maps.items():
        if Path(p).name in names: require(static['files'].get(p) == digest, 'Mapped provider drift')
    return maps

def probe(c, order):
    site = installed(c); static = static_review(c); objects = []
    def others():
        from ortools.constraint_solver import pywrapcp
        import pyvrp
        from ompl import base as ob, geometric as og
        solver = pywrapcp.Solver('jammy-candidate'); model = pyvrp.Model(); model.add_depot(model.add_location(0, 0))
        space = ob.RealVectorStateSpace(2); bounds = ob.RealVectorBounds(2); bounds.setLow(0); bounds.setHigh(1); space.setBounds(bounds)
        objects.extend([solver, model, space, bounds, og.SimpleSetup(space)])
    def coverage():
        import fields2cover as f2c
        point = f2c.Point(1., 2.); require((point.getX(), point.getY()) == (1., 2.), 'Point roundtrip failed'); objects.append(point)
    if order == 'others-first': others(); coverage()
    else: coverage(); others()
    maps = mapped_providers(c, (site / EXTENSION).resolve(), static)
    with (Path(c['output']) / 'evidence/native' / (order + '-maps.json')).open('x') as f:
        json.dump({'passed': True, 'mapped_libz': [p for p in maps if Path(p).name.startswith('libz.so.1')], 'mapped_libortools': [p for p in maps if Path(p).name.startswith('libortools.so')], 'mapped_native_sha256': maps}, f, indent=2)

def assert_junit(path, count):
    root = ET.parse(path).getroot(); cases = root.findall('.//testcase')
    bad_nodes = any(root.findall('.//' + tag) for tag in ['skipped', 'error', 'failure'])
    suites = [root] if root.tag == 'testsuite' else root.findall('.//testsuite')
    bad_counts = any(int(suite.get(key, '0')) != 0 for suite in suites for key in ['errors', 'failures', 'skipped'])
    require(len(cases) == count and not bad_nodes and not bad_counts, 'Test count/failure/skip mismatch')

def run(c, args):
    out = Path(c['output']); evidence = out / 'evidence/native'; failure = None; commands = []
    try:
        check_inputs(c); check_inputs(c, copied=True); installed(c); static_review(c)
        evidence.mkdir(mode=0o700)
        py = str(out / 'checkout/src/lab/.venv/bin/python'); lab = out / 'checkout/src/lab'
        def command(label, argv):
            with (evidence / (label + '.log')).open('xb') as log:
                p = subprocess.run(argv, cwd=lab, env=clean(c), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, close_fds=True)
            commands.append({'stage': label, 'exit_code': p.returncode, 'log_sha256': sha(evidence / (label + '.log'))})
            (evidence / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
            require(p.returncode == 0, 'Native validation stage failed: ' + label)
        for order in ['others-first', 'f2c-first']:
            command(order, [py, '-I', '-B', str(Path(__file__).resolve()), 'probe', '--config', args.config, '--expected-config-sha256', args.expected_config_sha256, '--allow-native-execution', '--order', order])
        command('unchanged-mixed-smoke', [py, '-B', str(out / 'checkout/deploy/check-fields2cover.py')])
        geometry = ['tests/test_coverage_contract.py::test_native_full_pipeline_includes_the_last_state_endpoint', 'tests/test_extended.py::test_f2c_segments_curvature_and_execution_legs']
        for label, tests, count in [('geometry', geometry, 3), ('full-suite', [], 602)]:
            xml = evidence / (label + '.xml')
            command(label, [py, '-B', '-m', 'pytest', '-q', '-ra', '-o', 'cache_dir=' + str(out / 'cache' / label), '--junitxml=' + str(xml)] + tests)
            assert_junit(xml, count)
    except BaseException as exc:
        failure = repr(exc)
        raise
    finally:
        checks = {}
        for label, fn in [('frozen-build-and-harness', lambda: anchored(args.config, args.expected_config_sha256)), ('original-inputs', lambda: check_inputs(c)), ('copied-inputs', lambda: check_inputs(c, True)), ('installed-bytes', lambda: installed(c))]:
            try: fn(); checks[label] = 'passed'
            except Exception as exc: checks[label] = repr(exc)
        (out / 'evidence/post-run-integrity.json').write_text(json.dumps({'failure': failure, 'checks': checks}, indent=2) + '\n')
        if failure is None: require(set(checks.values()) == {'passed'}, 'Final integrity failed')
    # Bounded public summary: never includes raw logs, paths, map or test data.
    summary = {'schema': 1, 'status': 'jammy_candidate_validated_not_production_accepted', 'geometry_passed': 3, 'full_passed': 602, 'skipped': 0, 'mixed_import_orders_passed': 2, 'wheel_sha256': c['wheel']['sha256']}
    (out / 'public-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary))

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['check', 'prepare', 'probe', 'run']); p.add_argument('--config', required=True)
    p.add_argument('--expected-config-sha256', required=True); p.add_argument('--allow-native-execution', action='store_true')
    p.add_argument('--order', choices=['others-first', 'f2c-first'])
    a = p.parse_args()
    if a.mode in {'run', 'probe'}: require(a.allow_native_execution, 'Native execution not authorized')
    if a.mode == 'probe': require(a.order is not None, 'Missing import order')
    c = anchored(a.config, a.expected_config_sha256)
    if a.mode == 'check': check_inputs(c)
    elif a.mode == 'prepare': prepare(c)
    elif a.mode == 'probe': probe(c, a.order)
    else: run(c, a)

if __name__ == '__main__': main()
