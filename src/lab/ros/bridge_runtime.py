"""Verified, process-scoped source fix for the parameter_bridge shutdown race.

The normal rclcpp shutdown joins the deferred signal thread before spdlog static
destruction. No _exit, status substitution, logging suppression or system edits.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess

LAB = Path(__file__).resolve().parents[1]
SOURCE = LAB / 'ros/native/bridge'
BUILD = LAB / 'runtime/native/bridge'
EXECUTABLE = BUILD / 'parameter_bridge'
SYSTEM_EXECUTABLE = Path('/opt/ros/humble/lib/ros_gz_bridge/parameter_bridge')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mapping_ok(mapping):
    for name, expected in mapping.items():
        path = Path(name)
        if not path.is_file() or sha(path) != expected:
            return False
    return True


def _source_map(manifest):
    return {str(SOURCE / key): value for key, value in manifest.get('sources', {}).items()}


def active_manifest():
    """Laptop audit wins whenever its system libraries are the ones on disk.

    A machine profile (written by deploy/record-sim-profiles.py) is consulted only
    when those libraries differ. It must carry the same source hashes, so a profile
    cannot authorize a different patch. CERES_BRIDGE_FIX=0 remains the explicit
    contrast switch and is not a fallback.
    """
    audited = SOURCE / 'manifest.json'
    if not audited.is_file():
        raise RuntimeError('桥接修复的源码/依赖指纹变化：缺少 manifest.json；需重新构建验收，不能静默回退')
    laptop = json.loads(audited.read_text())
    sources = _source_map(laptop)
    for name, expected in sources.items():
        path = Path(name)
        if not path.is_file() or sha(path) != expected:
            raise RuntimeError(f'桥接修复的源码/依赖指纹变化：{name}；需重新构建验收，不能静默回退')
    profile_path = BUILD / 'profile.json'
    profile = json.loads(profile_path.read_text()) if profile_path.is_file() else None

    def profile_ok():
        return (profile is not None and _source_map(profile) == sources and profile.get('executable_sha256')
                and _mapping_ok(profile.get('system_files', {})))

    if _mapping_ok(laptop.get('system_files', {})):
        # The audited binary wins whenever it is the file on disk. A different binary
        # is accepted only for an image profile, and only when CERES_SIM_PROFILE=image.
        if EXECUTABLE.is_file() and sha(EXECUTABLE) == laptop.get('executable_sha256'):
            return laptop
        if os.environ.get('CERES_SIM_PROFILE') == 'image' and profile_ok() and profile.get('scope') == 'image':
            return profile
        return laptop
    if profile_ok():
        return profile
    raise RuntimeError('桥接修复的源码/依赖指纹变化：系统库与笔记本验收指纹不一致，且没有与本机匹配的已登记指纹；需重新构建验收，不能静默回退')


def verified_bridge():
    manifest = active_manifest()
    BUILD.mkdir(parents=True, exist_ok=True)
    with (BUILD / 'build.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not EXECUTABLE.is_file():
            result = subprocess.run(['bash', str(SOURCE / 'build.sh')], capture_output=True, text=True, timeout=180)
            (BUILD / 'build.log').write_text(result.stdout + result.stderr)
            if result.returncode:
                raise RuntimeError(f'桥接修复构建失败（{result.returncode}），见 {BUILD / "build.log"}')
        if sha(EXECUTABLE) != manifest['executable_sha256']:
            raise RuntimeError('桥接可执行文件与验收指纹不同；需重新验收，不能使用未知二进制')
        # An ament package prefix selects this executable in official TB4 launch
        # files as well as ros2 run. All existing package resources are reused.
        prefix = BUILD / 'overlay'
        marker = prefix / 'share/ament_index/resource_index/packages/ros_gz_bridge'
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch(exist_ok=True)
        links = {prefix / 'share/ros_gz_bridge': Path('/opt/ros/humble/share/ros_gz_bridge'),
                 prefix / 'lib/ros_gz_bridge/parameter_bridge': EXECUTABLE}
        for link, target in links.items():
            link.parent.mkdir(parents=True, exist_ok=True)
            if link.is_symlink() and link.resolve() == target.resolve():
                continue
            if link.exists() or link.is_symlink():
                raise RuntimeError(f'桥接覆盖目录有冲突文件：{link}')
            link.symlink_to(os.path.relpath(target, link.parent))
    return prefix


def bridge_environment():
    option = os.environ.get('CERES_BRIDGE_FIX', '1')
    if option not in ('0', '1'):
        raise RuntimeError('CERES_BRIDGE_FIX 必须是 0 或 1')
    if option == '0':
        return {}
    prefix = verified_bridge()
    return {'AMENT_PREFIX_PATH': str(prefix) + ':' + os.environ.get('AMENT_PREFIX_PATH', '/opt/ros/humble')}


def bridge_provenance():
    enabled = os.environ.get('CERES_BRIDGE_FIX', '1') != '0'
    executable = EXECUTABLE if enabled else SYSTEM_EXECUTABLE
    return {'mode': 'source_shutdown_join' if enabled else 'system_unfixed',
            'executable': str(executable), 'sha256': sha(executable), 'exit_guard': False,
            'manifest': str(SOURCE / 'manifest.json') if enabled else None}


if __name__ == '__main__':
    print(verified_bridge())
