"""Explicitly invoked builder. Never imported native modules; no acceptance claim."""
import json
import pathlib
import posixpath
import shutil
import subprocess
import zipfile

from guardlib import fresh_outputs, require, resolve_ref, sha256, verify_index
from build_guard import network_guard

EXTENSION_MEMBER = 'fields2cover/_fields2cover_python.so'
PROVIDER_DIRS = ('ortools/.libs', 'ortools.libs')
INSTALL_RPATH = ';'.join('$ORIGIN/' + posixpath.relpath(p, posixpath.dirname(EXTENSION_MEMBER)) for p in PROVIDER_DIRS)

def clean_environment(root, commands, lock):
    out = root / 'output'
    return {
        'PATH': str(out / 'build-venv/bin') + ':/usr/bin:/bin',
        'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
        'HOME': str(out / 'home'), 'TMPDIR': str(out / 'tmp'),
        'XDG_CACHE_HOME': str(out / 'cache'),
        'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1',
        'PIP_CONFIG_FILE': '/dev/null', 'PIP_REQUIRE_VIRTUALENV': '1',
        'PIP_NO_INDEX': '1', 'PIP_DISABLE_PIP_VERSION_CHECK': '1',
        'CC': str(commands['gcc']), 'CXX': str(commands['gxx']),
        'SOURCE_DATE_EPOCH': str(lock['build']['source_date_epoch']),
        'CMAKE_BUILD_PARALLEL_LEVEL': str(lock['build']['parallel_jobs']),
    }

def build_command(root, prefix, commands, lock):
    out = root / 'output'
    python = out / 'build-venv/bin/python'
    scopes = {row['name']: resolve_ref(root, prefix, row['ref']) for row in lock['inputs']['trees']}
    wheel = resolve_ref(root, prefix, lock['runtime']['extracted_ref'])
    providers = [wheel / p for p in PROVIDER_DIRS]
    # These are wheel-owned provider paths only. Jammy geospatial libraries are
    # system dependencies and are never copied to a competing bundled tree.
    flags = {
        'CMAKE_C_COMPILER': commands['gcc'], 'CMAKE_CXX_COMPILER': commands['gxx'],
        'Python_EXECUTABLE': python, 'SWIG_EXECUTABLE': out / 'build-venv/bin/swig',
        'CMAKE_MAKE_PROGRAM': out / 'build-venv/bin/ninja',
        'CMAKE_BUILD_TYPE': 'Release', 'CMAKE_EXPORT_COMPILE_COMMANDS': 'ON',
        'BUILD_PYTHON': 'ON', 'BUILD_SHARED_LIBS': 'OFF',
        'BUILD_TUTORIALS': 'OFF', 'BUILD_TESTING': 'OFF', 'BUILD_DOC': 'OFF',
        'USE_ORTOOLS_FETCH_SRC': 'OFF', 'USE_ORTOOLS_VENDOR': 'OFF',
        'FETCHCONTENT_FULLY_DISCONNECTED': 'ON', 'FETCHCONTENT_UPDATES_DISCONNECTED': 'ON',
        'FETCHCONTENT_SOURCE_DIR_STEERING_FUNCTIONS': scopes['steering_functions'],
        'FETCHCONTENT_SOURCE_DIR_MATPLOT': scopes['matplot'],
        'FETCHCONTENT_SOURCE_DIR_JSON': scopes['json'],
        'ortools_DIR': resolve_ref(root, prefix, lock['interface']['cmake_ref']),
        'CMAKE_PREFIX_PATH': '/usr',
        'GDAL_INCLUDE_DIR': '/usr/include/gdal',
        'GDAL_LIBRARY': '/usr/lib/x86_64-linux-gnu/libgdal.so',
        'FindGDAL_SKIP_GDAL_CONFIG': 'ON',
        'Eigen3_DIR': '/usr/share/eigen3/cmake',
        'GEOS_DIR': '/usr/lib/x86_64-linux-gnu/cmake/GEOS',
        'TBB_DIR': '/usr/lib/x86_64-linux-gnu/cmake/TBB',
        'ZLIB_INCLUDE_DIR': '/usr/include',
        # Preserve the prior reviewed OR-Tools wheel zlib provider. Do not
        # silently add a competing system libz to the explicit link interface.
        'ZLIB_LIBRARY': wheel / 'ortools/.libs/libz.so.1',
        'CMAKE_BUILD_RPATH': ';'.join(map(str, providers)),
        'CMAKE_INSTALL_RPATH': INSTALL_RPATH,
        'CMAKE_INSTALL_RPATH_USE_LINK_PATH': 'OFF',
        'CMAKE_BUILD_WITH_INSTALL_RPATH': 'OFF',
        'CMAKE_SHARED_LINKER_FLAGS': ' '.join('-Wl,-rpath-link,' + str(p) for p in providers),
        'CMAKE_MODULE_LINKER_FLAGS': ' '.join('-Wl,-rpath-link,' + str(p) for p in providers),
        'CMAKE_FIND_USE_PACKAGE_REGISTRY': 'OFF',
        'CMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY': 'OFF',
        'CMAKE_EXPORT_NO_PACKAGE_REGISTRY': 'ON',
        'MATPLOTPP_BUILD_EXPERIMENTAL_OPENGL_BACKEND': 'OFF',
        'MATPLOTPP_BUILD_EXAMPLES': 'OFF', 'MATPLOTPP_BUILD_TESTS': 'OFF',
        'MATPLOTPP_BUILD_INSTALLER': 'OFF', 'MATPLOTPP_BUILD_PACKAGE': 'OFF',
        'MATPLOTPP_WITH_OPENCV': 'OFF', 'MATPLOTPP_BUILD_WITH_SANITIZERS': 'OFF',
        'JSON_BuildTests': 'OFF', 'JSON_CI': 'OFF',
    }
    for name in ('JPEG', 'TIFF', 'PNG', 'BLAS', 'LAPACK', 'FFTW', 'Gnuplot'):
        flags['CMAKE_DISABLE_FIND_PACKAGE_' + name] = 'ON'
    argv = [str(python), '-I', '-B', '-m', 'build', '--wheel', '--no-isolation',
            str(scopes['fields2cover']), '--outdir', str(out / 'wheels'), '-Cbuild-dir=' + str(out / 'build')]
    argv.extend('-Ccmake.define.' + key + '=' + str(value) for key, value in flags.items())
    return argv

def invoke(argv, name, root, env):
    evidence = root / 'output/evidence'
    with (evidence / (name + '.log')).open('xb') as log:
        completed = subprocess.run(argv, cwd=root, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, close_fds=True, check=False)
    (evidence / (name + '.exit-code')).write_text(str(completed.returncode) + '\n')
    require(completed.returncode == 0, 'Offline stage failed: ' + name + '; preserve evidence, do not relax guards/tests')

def run(root, python, prefix, commands, lock, anchor):
    # Called only after complete verifier success. No alternate standalone mode.
    verify_index(root, anchor)
    network_guard(lock['network'])
    require(shutil.disk_usage(root).free >= lock['build']['minimum_free_bytes'], 'Insufficient free disk')
    fresh_outputs(root, create=True)
    env = clean_environment(root, commands, lock)
    out = root / 'output'
    build_python = out / 'build-venv/bin/python'
    invoke([str(python), '-I', '-B', '-m', 'venv', '--copies', str(out / 'build-venv')], 'create-venv', root, env)
    wheelhouse = next(row for row in lock['inputs']['trees'] if row['name'] == 'build-wheelhouse')
    invoke([str(build_python), '-I', '-B', '-m', 'pip', 'install', '--no-index',
            '--find-links', str(resolve_ref(root, prefix, wheelhouse['ref'])), '--require-hashes',
            '--only-binary=:all:', '-r', str(resolve_ref(root, prefix, lock['inputs']['build_requirements']['ref']))],
           'install-build-tools', root, env)
    network_guard(lock['network'])
    invoke(build_command(root, prefix, commands, lock), 'build', root, env)
    wheels = list((out / 'wheels').glob('*.whl'))
    require(len(wheels) == 1, 'Expected exactly one new wheel')
    with zipfile.ZipFile(wheels[0]) as archive:
        require(EXTENSION_MEMBER in archive.namelist(), 'New extension layout differs; re-review relative RPATH')
        extension_sha = __import__('hashlib').sha256(archive.read(EXTENSION_MEMBER)).hexdigest()
    result = {'built_not_accepted': True, 'artifact_index_sha256': anchor,
              'wheel': wheels[0].name, 'wheel_sha256': sha256(wheels[0]),
              'extension_sha256': extension_sha,
              'pending': ['complete ELF closure', 'relocated clean Jammy validation',
                          'unchanged mixed-native and three contract passes', 'full suite', 'reproducibility']}
    (out / 'evidence/build-result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))

if __name__ == '__main__':
    raise SystemExit('Use the externally anchored launcher, not this helper directly.')
