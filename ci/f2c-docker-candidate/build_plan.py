"""Reviewed compiler plan port; only output-root API changed. No execution."""
from guardlib import resolve_ref
from pathlib import Path
INSTALL_RPATH = "$ORIGIN/../ortools/.libs;$ORIGIN/../ortools.libs"
PROVIDER_DIRS = ("ortools/.libs", "ortools.libs")
ORIGINAL_SOURCE_SHA256 = '4a25d5502957518c121fce1a9fc0d7907a052210bd9a0444e417077f84a46c3d'

def build_command(root, prefix, commands, lock, out):
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
