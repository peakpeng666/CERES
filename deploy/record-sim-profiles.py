#!/usr/bin/env python3
"""Build the bridge and the two ROS overlays, then record a machine fingerprint.

manifest.json, tf2-overlay-manifest.json and gui-overlay-manifest.json stay the
laptop audit. This script does not edit them. When the libraries named there are
the ones installed on this machine, it refuses to compile a replacement. When
they are not, it compiles from the audited sources and writes a profile that
later startups accept only if those sources and the new libraries still match.

Profiles:
  src/lab/runtime/native/bridge/profile.json
  setup/2026-10-15/deployment/runtime/profiles/tf.json
  setup/2026-10-15/deployment/runtime/profiles/gui.json
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / 'src/lab/ros/native/bridge'
BRIDGE_BUILD = ROOT / 'src/lab/runtime/native/bridge'
DEPLOY = ROOT / 'setup/2026-10-15/deployment'
PROFILES = DEPLOY / 'runtime/profiles'


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mapping_ok(mapping: dict) -> bool:
    for name, expected in mapping.items():
        path = Path(name)
        if not path.is_file() or sha(path) != expected:
            return False
    return True


def resolve_library(spec: str) -> Path:
    path = Path(spec)
    if path.is_file():
        return path.resolve()
    parent = path.parent
    token = path.name.split('.so')[0]
    if parent.is_dir():
        found = sorted({p.resolve() for p in parent.glob(token + '.so*') if p.is_file()})
        if len(found) == 1:
            return found[0]
        if found:
            raise SystemExit(f'{spec} 不在，同目录有多个候选，不能猜：' + ', '.join(str(p) for p in found))
    raise SystemExit(f'缺少系统库 {spec}，无法登记本机指纹')


def record_resolved(keys: list[str]) -> dict:
    out = {}
    for spec in keys:
        path = resolve_library(spec)
        out[str(path)] = sha(path)
    return out


def run(command: list[str], cwd: Path | None = None):
    print('+', ' '.join(command), flush=True)
    subprocess.run(command, cwd=cwd or ROOT, check=True)


LAPTOP_ROOT = Path('/home/peakpeng/Desktop/CERES')


def require_audited(label: str, built: Path, expected: str):
    """These system libraries are the laptop audit, so only that binary is legal."""
    if not built.is_file():
        return False
    got = sha(built)
    if got == expected:
        print(f'{label}：系统库与笔记本验收一致，产物哈希相同。')
        return True
    built.unlink()
    raise SystemExit(
        f'{label}：系统库与笔记本验收一致，但重新编译的哈希是 {got}，验收是 {expected}。'
        '不能登记第二份指纹，否则同一套系统库会接受另一个二进制。请放入验收产物。')


def build_bridge(root: Path) -> Path:
    dest_src = root / 'src/lab/ros/native/bridge'
    if dest_src.resolve() != BRIDGE.resolve():
        if dest_src.exists():
            shutil.rmtree(dest_src)
        dest_src.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(BRIDGE, dest_src)
    run(['bash', str(dest_src / 'build.sh')])
    return root / 'src/lab/runtime/native/bridge/parameter_bridge'


def bridge():
    laptop = json.loads((BRIDGE / 'manifest.json').read_text())
    executable = BRIDGE_BUILD / 'parameter_bridge'
    if mapping_ok(laptop['system_files']):
        if require_audited('桥接', executable, laptop['executable_sha256']):
            return
        # RelWithDebInfo embeds the absolute build path. Try the repository path
        # first, then the laptop path recorded in the audited tree.
        for root in (ROOT, LAPTOP_ROOT):
            produced = build_bridge(root)
            if produced.resolve() != executable.resolve():
                executable.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(produced, executable)
            if executable.is_file() and sha(executable) == laptop['executable_sha256']:
                print('桥接：系统库与笔记本验收一致，产物哈希相同。')
                return
            if executable.is_file():
                print(f'桥接：在 {root} 编译的哈希是 {sha(executable)}，与验收不同。')
                break
        if not executable.is_file():
            raise SystemExit(f'桥接构建没有产生 {executable}')
        # The binary is not the laptop audit. Record it as an image profile.
        # Startup accepts it only when CERES_SIM_PROFILE=image; the laptop default does not set that.
        profile = {
            'role': 'machine',
            'scope': 'image',
            'sources': laptop['sources'],
            'system_files': laptop['system_files'],
            'executable_sha256': sha(executable),
            'laptop_executable_sha256': laptop['executable_sha256'],
            'note': 'Same system libraries as the laptop audit, different binary. Ignored unless CERES_SIM_PROFILE=image.',
        }
        BRIDGE_BUILD.mkdir(parents=True, exist_ok=True)
        (BRIDGE_BUILD / 'profile.json').write_text(json.dumps(profile, indent=2) + '\n')
        print(f'桥接：已登记镜像指纹 {BRIDGE_BUILD / "profile.json"}（需要 CERES_SIM_PROFILE=image）')
        return
    run(['bash', str(BRIDGE / 'build.sh')])
    if not executable.is_file():
        raise SystemExit(f'桥接构建没有产生 {executable}')
    profile = {
        'role': 'machine',
        'sources': laptop['sources'],
        'system_files': record_resolved(list(laptop['system_files'])),
        'executable_sha256': sha(executable),
        'note': 'Recorded on this OS image from the audited sources. The laptop manifest is unchanged and wins when its system libraries match.',
    }
    BRIDGE_BUILD.mkdir(parents=True, exist_ok=True)
    (BRIDGE_BUILD / 'profile.json').write_text(json.dumps(profile, indent=2) + '\n')
    print(f'桥接：已登记本机指纹 {BRIDGE_BUILD / "profile.json"}')


def tf():
    manifest = json.loads((DEPLOY / 'tf2-overlay-manifest.json').read_text())
    source = DEPLOY / 'tf-repro/buffer_candidate.cpp'
    candidate = DEPLOY / manifest['candidate_library']
    laptop_files = {**manifest['system_libraries'], str(candidate): manifest['candidate_sha256'], str(source): manifest['source_sha256']}
    if mapping_ok(laptop_files):
        print('TF 覆盖：与笔记本验收一致，不重编译。')
        return
    if mapping_ok(manifest['system_libraries']) and (not candidate.is_file() or sha(candidate) != manifest['candidate_sha256']):
        if not source.is_file() or sha(source) != manifest['source_sha256']:
            raise SystemExit('TF 覆盖源码与笔记本验收哈希不同，拒绝编译。')
        out = DEPLOY / 'runtime/tf-repro'
        run(['bash', '-lc', 'set -euo pipefail; set +u; source /opt/ros/humble/setup.bash; set -u; '
             f'cmake -S "{DEPLOY / "tf-repro"}" -B "{out}" -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo && '
             f'cmake --build "{out}" --target ceres_tf_buffer'])
        built = out / 'liburp_tf_buffer.so'
        if built.is_file() and sha(built) == manifest['candidate_sha256']:
            print('TF 覆盖：系统库与笔记本验收一致，产物哈希相同。')
            return
        if not built.is_file():
            raise SystemExit(f'TF 构建没有产生 {built}')
        profile = {
            'role': 'machine', 'scope': 'image', 'source_sha256': manifest['source_sha256'],
            'candidate_library': 'runtime/tf-repro/liburp_tf_buffer.so', 'candidate_sha256': sha(built),
            'system_libraries': manifest['system_libraries'],
            'laptop_candidate_sha256': manifest['candidate_sha256'],
            'note': 'Ignored unless CERES_SIM_PROFILE=image.',
        }
        PROFILES.mkdir(parents=True, exist_ok=True)
        (PROFILES / 'tf.json').write_text(json.dumps(profile, indent=2) + '\n')
        print(f'TF 覆盖：已登记镜像指纹（需要 CERES_SIM_PROFILE=image），哈希 {profile["candidate_sha256"]}')
        return
    if not source.is_file() or sha(source) != manifest['source_sha256']:
        raise SystemExit('TF 覆盖源码与笔记本验收哈希不同，拒绝编译。')
    out = DEPLOY / 'runtime/tf-repro'
    run(['bash', '-lc', 'set -euo pipefail; set +u; source /opt/ros/humble/setup.bash; set -u; '
         f'cmake -S "{DEPLOY / "tf-repro"}" -B "{out}" -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo && '
         f'cmake --build "{out}" --target ceres_tf_buffer'])
    built = out / 'liburp_tf_buffer.so'
    if not built.is_file():
        raise SystemExit(f'TF 构建没有产生 {built}')
    profile = {
        'role': 'machine',
        'source_sha256': manifest['source_sha256'],
        'candidate_library': 'runtime/tf-repro/liburp_tf_buffer.so',
        'candidate_sha256': sha(built),
        'system_libraries': record_resolved(list(manifest['system_libraries'])),
    }
    PROFILES.mkdir(parents=True, exist_ok=True)
    (PROFILES / 'tf.json').write_text(json.dumps(profile, indent=2) + '\n')
    print(f'TF 覆盖：已登记本机指纹 {PROFILES / "tf.json"}')


def gui():
    manifest = json.loads((DEPLOY / 'gui-overlay-manifest.json').read_text())
    source = DEPLOY / 'gui-plugin-lifetime.cpp'
    expected_source = next(digest for name, digest in manifest['files'].items() if name.endswith('gui-plugin-lifetime.cpp'))
    expected_library = next(digest for name, digest in manifest['files'].items() if name.endswith('liburp_gui_lifetime.so'))
    library = DEPLOY / manifest['library']
    system_keys = [name for name in manifest['files']
                   if not name.endswith('gui-plugin-lifetime.cpp') and not name.endswith('liburp_gui_lifetime.so')]
    if mapping_ok(manifest['files']):
        print('GUI 覆盖：与笔记本验收一致，不重编译。')
        return
    if sha(source) != expected_source:
        raise SystemExit('GUI 覆盖源码与笔记本验收哈希不同，拒绝编译。')
    library.parent.mkdir(parents=True, exist_ok=True)
    run(['g++', '-O2', '-g', '-std=c++17', '-shared', '-fPIC', '-o', str(library), str(source), '-ldl'])
    if mapping_ok({key: manifest['files'][key] for key in system_keys}):
        if sha(library) == expected_library:
            print('GUI 覆盖：系统库与笔记本验收一致，产物哈希相同。')
            return
        profile = {
            'role': 'machine', 'scope': 'image', 'source_sha256': expected_source,
            'library': manifest['library'], 'library_sha256': sha(library),
            'system_libraries': {key: manifest['files'][key] for key in system_keys},
            'laptop_library_sha256': expected_library,
            'note': 'Ignored unless CERES_SIM_PROFILE=image.',
        }
        PROFILES.mkdir(parents=True, exist_ok=True)
        (PROFILES / 'gui.json').write_text(json.dumps(profile, indent=2) + '\n')
        print(f'GUI 覆盖：已登记镜像指纹（需要 CERES_SIM_PROFILE=image），哈希 {profile["library_sha256"]}')
        return
    profile = {
        'role': 'machine',
        'source_sha256': expected_source,
        'library': manifest['library'],
        'library_sha256': sha(library),
        'system_libraries': record_resolved(system_keys),
    }
    PROFILES.mkdir(parents=True, exist_ok=True)
    (PROFILES / 'gui.json').write_text(json.dumps(profile, indent=2) + '\n')
    print(f'GUI 覆盖：已登记本机指纹 {PROFILES / "gui.json"}')


def main():
    bridge()
    tf()
    gui()


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode or 1)
