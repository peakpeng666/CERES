"""Read-only preflight for one official upstream reference; never starts ROS."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

from .scene_core import read_json


def manifest_path():
    source = Path(__file__).resolve().parents[1] / 'config' / 'upstream_turtlebot4_humble.json'
    if source.is_file():
        return source
    from ament_index_python.packages import get_package_share_directory
    return Path(get_package_share_directory('ceres_route_sim')) / 'config' / source.name


def git_blob_sha1(data):
    # Git object identity, not an authentication/signature check.
    return hashlib.sha1(b'blob ' + str(len(data)).encode('ascii') + b'\0' + data,
                        usedforsecurity=False).hexdigest()


def inspect_reference(share, manifest):
    """Check installed files against the reviewed upstream commit without import."""
    share = Path(share)
    results = []
    blockers = []
    for item in manifest['files']:
        relative = PurePosixPath(item['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('upstream manifest contains an unsafe relative path')
        path = share / relative
        result = {'path': item['path'], 'expected_git_blob_sha1': item['git_blob_sha1']}
        try:
            with path.open('rb') as stream:
                data = stream.read(10 * 1024 * 1024 + 1)
            if len(data) > 10 * 1024 * 1024:
                raise ValueError('source file exceeds 10 MiB')
            result['git_blob_sha1'] = git_blob_sha1(data)
            result['sha256'] = hashlib.sha256(data).hexdigest()
            result['matches_reviewed_source'] = result['git_blob_sha1'] == item['git_blob_sha1']
        except (OSError, ValueError) as exc:
            result.update(matches_reviewed_source=False, error=str(exc))
        if not result['matches_reviewed_source']:
            blockers.append('upstream_source_mismatch:' + item['path'])
        results.append(result)
    try:
        package = ET.parse(share / 'package.xml').getroot()
        version = package.findtext('version')
        if package.findtext('name') != manifest['package'] or version != manifest['package_version']:
            blockers.append('upstream_package_version_mismatch')
    except (ET.ParseError, OSError) as exc:
        version = None
        blockers.append('unreadable_upstream_package:' + str(exc))
    return {'files': results, 'package_version': version, 'blockers': blockers}


def check_upstream(manifest=None):
    manifest = read_json(manifest_path()) if manifest is None else manifest
    blockers = []
    try:
        os_release = platform.freedesktop_os_release()
    except OSError:
        os_release = {}
    actual_os = {'id': os_release.get('ID'), 'version_id': os_release.get('VERSION_ID')}
    if actual_os != manifest['os']:
        blockers.append('requires_Ubuntu_22.04')
    if os.environ.get('ROS_DISTRO') != manifest['ros_distro']:
        blockers.append('ROS_2_Humble_environment_not_sourced')
    executables = {name: shutil.which(name) is not None for name in ('ros2', 'ign')}
    blockers.extend('missing_executable:' + name for name, present in executables.items() if not present)
    gazebo_versions = None
    if executables['ign']:
        try:
            completed = subprocess.run(['ign', 'gazebo', '--versions'], capture_output=True,
                                       text=True, timeout=10, check=False)
            gazebo_versions = (completed.stdout + completed.stderr)[:8192]
            majors = re.findall(r'(?<![\d.])(\d+)\.\d+(?:\.\d+)?', gazebo_versions)
            if completed.returncode != 0 or set(majors) != {'6'}:
                blockers.append('Gazebo_Fortress_6_not_available')
        except (OSError, subprocess.TimeoutExpired) as exc:
            blockers.append('Gazebo_version_check_failed:' + type(exc).__name__)
    packages = {}
    share = None
    try:
        from ament_index_python.packages import get_package_share_directory, PackageNotFoundError
    except ImportError:
        blockers.append('ament_index_python_not_available')
        packages = {name: {'available': False} for name in manifest['required_packages']}
    else:
        for name in manifest['required_packages']:
            try:
                package_share = Path(get_package_share_directory(name))
                version = ET.parse(package_share / 'package.xml').getroot().findtext('version')
                packages[name] = {'available': True, 'version': version, 'share': str(package_share)}
                if name == manifest['package']:
                    share = package_share
            except (PackageNotFoundError, OSError, ET.ParseError) as exc:
                packages[name] = {'available': False, 'error': type(exc).__name__}
    missing = [name for name, status in packages.items() if not status['available']]
    if missing:
        blockers.append('required_ROS_packages_missing')
    layout = inspect_reference(share, manifest) if share else None
    if layout:
        blockers.extend(layout['blockers'])
    graphics = bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))
    if manifest['graphical_session_required'] and not graphics:
        blockers.append('official_launch_requires_graphical_session')
    return {
        'schema_version': 1, 'platform_id': manifest['platform_id'],
        'stage': 'preflight', 'status': 'blocked' if blockers else 'launch_dependencies_available',
        'launch_available': not blockers, 'runtime_executed': False, 'simulation_ready': False,
        'task_execution_verified': False, 'scope': 'OFFLINE_DEPENDENCY_AND_SOURCE_CHECK',
        'reference_commit': manifest['commit'], 'observed_os': actual_os,
        'observed_ros_distro': os.environ.get('ROS_DISTRO'), 'executables': executables,
        'gazebo_versions': gazebo_versions, 'packages': packages, 'missing_packages': missing,
        'reference_layout': layout, 'graphical_session_environment_present': graphics,
        'external_asset_uris': manifest['external_asset_uris'],
        'asset_versions_frozen': manifest['asset_versions_frozen'],
        'full_dependency_lock': manifest['full_dependency_lock'],
        'blockers': blockers,
        'remaining_acceptance': [
            'Freeze installed dependency versions and cached Fuel assets, including licenses and hashes.',
            'Run the official world/model/bridge and collect actual clock, sensors, TF and physics evidence.',
            'Define metric service poses, map registration, task executive and failure/stop behavior.',
            'Validate navigation, independent trajectory, service events and declared collision observation.',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', help='New evidence directory; an existing path is never overwritten')
    args = parser.parse_args()
    try:
        out = Path(args.output_dir).resolve() if args.output_dir else None
        if out:
            out.mkdir(parents=True, exist_ok=False)
        report = check_upstream()
        payload = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
        if out:
            with (out / 'preflight.json').open('x', encoding='utf-8') as stream:
                stream.write(payload)
        print(payload, end='')
    except (OSError, ValueError) as exc:
        print(json.dumps({'status': 'preflight_error', 'runtime_executed': False,
                          'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0 if report['launch_available'] else 2


if __name__ == '__main__':
    sys.exit(main())
