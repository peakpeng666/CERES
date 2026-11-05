"""Data-only ELF/provider inspection and a narrowly reviewed OR-Tools interface."""
import pathlib
import re
import subprocess

from guardlib import require, sha256
from live_inputs import file_row, write_json_new

CORE = {'libc.so.6', 'libm.so.6', 'libpthread.so.0', 'libdl.so.2', 'librt.so.1',
        'libresolv.so.2', 'libutil.so.1', 'ld-linux-x86-64.so.2', 'libstdc++.so.6', 'libgcc_s.so.1'}
ROOT_LIBRARIES = ('libortools.so.9', 'libgdal.so.30', 'libgeos_c.so.1', 'libtinyxml2.so.9', 'libtbb.so.12')

def parse_elf(dynamic, versions):
    definitions, needs, current, section = set(), {}, None, None
    for line in versions.splitlines():
        if line.startswith('Version definition'):
            section = 'definitions'
        elif line.startswith('Version needs'):
            section = 'needs'
        elif line.startswith('Version symbols'):
            section = 'symbols'
        if section == 'definitions':
            definitions.update(re.findall(r'Name: (\S+)', line))
        if section == 'needs':
            found = re.search(r'File: (\S+)', line)
            if found:
                current = found[1]
                needs[current] = []
            found = re.search(r'Name: (\S+)', line)
            if found and current:
                needs[current].append(found[1])
    return {'needed': re.findall(r'\(NEEDED\).*?\[(.*?)\]', dynamic),
            'sonames': re.findall(r'\(SONAME\).*?\[(.*?)\]', dynamic),
            'rpaths': re.findall(r'\((?:RUNPATH|RPATH)\).*?\[(.*?)\]', dynamic),
            'definitions': sorted(definitions), 'version_needs': needs}

def inspect(path, readelf='/usr/bin/readelf'):
    # readelf parses bytes. Neither the ELF loader nor the inspected binary runs.
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'}
    outputs = [subprocess.check_output([readelf, flag, str(path)], env=env, stdin=subprocess.DEVNULL,
                                      stderr=subprocess.PIPE, text=True, timeout=30) for flag in ('-d', '--version-info')]
    return parse_elf(*outputs)

def resolve_soname(name, wheel, system_dirs):
    require('/' not in name and name not in ('', '.', '..'), 'Unsafe NEEDED name')
    wheel_dirs = [wheel / 'ortools/.libs', wheel / 'ortools.libs']
    order = list(system_dirs) if name in CORE else wheel_dirs + list(system_dirs)
    for directory in order:
        candidate = directory / name
        if candidate.is_file():
            return candidate, 'wheel' if directory in wheel_dirs else 'system'
    raise RuntimeError('Unresolved ELF dependency: ' + name)

def target_properties(text, target):
    match = re.search(r'set_target_properties\(' + re.escape(target) + r' PROPERTIES\s+(.*?)\n\s*\)', text, re.S)
    require(match is not None, 'Missing reviewed CMake target: ' + target)
    return dict(re.findall(r'^\s*([A-Z_]+) "([^"\n]*)"', match[1], re.M))

def interface_flags(headers):
    tools = headers / 'lib/cmake/ortools/ortoolsTargets.cmake'
    props = target_properties(tools.read_text(), 'ortools::ortools')
    expected = 'OR_PROTO_DLL=;USE_MATH_OPT;USE_BOP;USE_CBC;USE_CLP;USE_GLOP;USE_HIGHS;USE_PDLP;USE_SCIP'
    require(props.get('INTERFACE_COMPILE_DEFINITIONS') == expected, 'OR-Tools interface definitions changed')
    require(props.get('INTERFACE_COMPILE_OPTIONS') == '-fwrapv;-Wno-range-loop-construct;-Wno-sign-compare', 'OR-Tools compile options changed')
    require('cxx_std_17' in props.get('INTERFACE_COMPILE_FEATURES', ''), 'Unexpected OR-Tools C++ feature contract')
    protobuf = []
    for path in headers.glob('lib/cmake/**/*.cmake'):
        content = path.read_text()
        if 'set_target_properties(protobuf::libprotobuf PROPERTIES' in content:
            target = target_properties(content, 'protobuf::libprotobuf')
            if 'INTERFACE_COMPILE_DEFINITIONS' in target:
                protobuf.append(target)
    require(len(protobuf) == 1 and protobuf[0].get('INTERFACE_COMPILE_DEFINITIONS') == 'PROTOBUF_USE_DLLS', 'Protobuf propagated DLL definitions changed')
    return expected + ';PROTOBUF_USE_DLLS;_GLIBCXX_USE_CXX11_ABI=1', props['INTERFACE_COMPILE_OPTIONS']

def zlib_provider_alternative(root, prefix, wheel, system_dirs, selected):
    """The sole allowed first-loaded-provider alternative; never a generic exemption.

    Both exact files must satisfy every observed versioned zlib requirement.
    Actual one-provider mapping and unversioned ABI behavior remain runtime gates.
    The returned system file must also pass authenticated package-byte proof.
    """
    soname = 'libz.so.1'
    require(soname in selected and selected[soname]['origin'] == 'wheel', 'Missing link-time wheel zlib selection')
    wheel_path = wheel / 'ortools/.libs' / soname
    system_path = next((directory / soname for directory in system_dirs if (directory / soname).is_file()), None)
    require(system_path is not None, 'Missing actual Jammy system zlib alternative')
    require(wheel_path.resolve(strict=True) != system_path.resolve(strict=True), 'System zlib aliases the wheel provider')
    wheel_row, system_row = file_row(wheel_path, root, prefix), file_row(system_path, root, prefix)
    require(wheel_row['ref']['base'] == 'candidate' and system_row['ref']['base'] == 'system', 'Zlib alternative has wrong provenance scope')
    require(wheel_row['sha256'] == selected[soname]['file']['sha256'], 'Wheel zlib differs from selected provider')
    system_data = inspect(system_path)
    required_versions = {version for row in selected.values() for version in row['version_needs'].get(soname, [])}
    for label, data in (('wheel', selected[soname]), ('system', system_data)):
        require(data['sonames'] == [soname], 'Unexpected zlib alternative SONAME: ' + label)
        require(required_versions.issubset(data['definitions']), 'Zlib alternative lacks required symbol versions: ' + label)
        require(set(data['needed']).issubset(selected), 'Zlib alternative expands unmeasured dependency closure: ' + label)
        for dependency, versions in data['version_needs'].items():
            require(dependency in selected and set(versions).issubset(selected[dependency]['definitions']),
                    'Zlib alternative dependency versions are unsatisfied: ' + label)
            for version in versions:
                if re.fullmatch(r'GLIBC_\d+\.\d+(?:\.\d+)?', version):
                    require(tuple(map(int, version.split('_', 1)[1].split('.'))) <= (2, 35), 'Zlib alternative exceeds Jammy GLIBC floor')
    return {soname: {'policy': 'exactly-one-first-loaded-compatible-provider', 'wheel': wheel_row, 'system': system_row}}

def prepare_runtime(root, prefix, wheel, headers):
    system_dirs = [pathlib.Path(p) for p in ('/usr/lib/x86_64-linux-gnu', '/usr/lib',
                   '/usr/lib/x86_64-linux-gnu/blas', '/usr/lib/x86_64-linux-gnu/lapack')]
    queue, selected = list(ROOT_LIBRARIES), {}
    while queue:
        name = queue.pop()
        if name in selected:
            continue
        path, origin = resolve_soname(name, wheel, system_dirs)
        data = inspect(path)
        require(not data['sonames'] or data['sonames'] == [name], 'Unexpected SONAME alias: ' + name)
        require(not (name.startswith('libortools') and origin != 'wheel'), 'Non-wheel OR-Tools provider')
        selected[name] = {'file': file_row(path, root, prefix), 'origin': origin, **data}
        queue.extend(data['needed'])
        require(len(selected) <= 512, 'Unbounded ELF dependency closure')
    for name, data in selected.items():
        for dependency, versions in data['version_needs'].items():
            require(dependency in selected, 'Missing versioned dependency')
            require(set(versions).issubset(selected[dependency]['definitions']), 'ELF symbol-version requirement unsatisfied: ' + name)
            for version in versions:
                if re.fullmatch(r'GLIBC_\d+\.\d+(?:\.\d+)?', version):
                    require(tuple(map(int, version.split('_', 1)[1].split('.'))) <= (2, 35), 'GLIBC floor exceeds Jammy')
    alternatives = zlib_provider_alternative(root, prefix, wheel, system_dirs, selected)
    definitions, options = interface_flags(headers)
    shim = root / 'interface/cmake/ortools'
    shim.mkdir(parents=True)
    # Resolve from file refs rather than relying on native archive's imported paths.
    links = [str(root / row['file']['ref']['path']) for name, row in sorted(selected.items()) if row['origin'] == 'wheel' and name != 'libortools.so.9']
    text = '''# Generated by reviewed data-only preparer. Structural ABI is NOT proven.
# Link-time wheel zlib does NOT pin the runtime libz.so.1 SONAME provider.
# Only the two authenticated libz alternatives in runtime-interface.json are allowed;
# each runtime process must map exactly one compatible provider, never both.
set(ORTOOLS_VERSION "9.15.6755")
set(ortools_FOUND TRUE)
if(NOT TARGET ortools::ortools)
  add_library(ortools::ortools SHARED IMPORTED)
  set_target_properties(ortools::ortools PROPERTIES
    IMPORTED_LOCATION "%s"
    IMPORTED_SONAME "libortools.so.9"
    INTERFACE_INCLUDE_DIRECTORIES "%s"
    INTERFACE_COMPILE_DEFINITIONS "%s"
    INTERFACE_COMPILE_FEATURES "cxx_std_17"
    INTERFACE_COMPILE_OPTIONS "%s"
    INTERFACE_LINK_LIBRARIES "%s"
    INTERFACE_POSITION_INDEPENDENT_CODE ON)
endif()
''' % (wheel / 'ortools/.libs/libortools.so.9', headers / 'include', definitions, options, ';'.join(links))
    (shim / 'ortoolsConfig.cmake').write_text(text)
    evidence = root / 'preparation-evidence/runtime-interface.json'
    write_json_new(evidence, {'schema': 1, 'libraries': selected, 'provider_alternatives': alternatives, 'interface_metadata_checked': True,
                              'structural_abi_proven': False, 'native_execution_performed': False})
    system_files = [row['file'] for row in selected.values() if row['origin'] == 'system']
    # prepare_live uses this return list for official archive-byte proof and both
    # host_files/runtime.system_libraries, including the unselected alternative.
    system_files.append(alternatives['libz.so.1']['system'])
    return shim, system_files, evidence
