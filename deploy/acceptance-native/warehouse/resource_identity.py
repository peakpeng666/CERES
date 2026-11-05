"""Compare resource bytes independent of the machine-specific cache root."""
import re
from pathlib import PurePosixPath
from historical_input import canonical_sha


def normalized_files(manifest):
    if manifest.get('complete') is not True or manifest.get('missing') != []: raise ValueError('resources_incomplete')
    result = {}
    for name, digest in manifest.get('files', {}).items():
        if not isinstance(name, str) or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest): raise ValueError('resource_hash_invalid')
        if '/fuel.gazebosim.org/' in name:
            key = 'fuel.gazebosim.org/' + name.split('/fuel.gazebosim.org/', 1)[1]
        elif name.startswith('/opt/ros/humble/share/'):
            key = 'ros_share/' + name.removeprefix('/opt/ros/humble/share/')
        else: raise ValueError('resource_path_unrecognized')
        if '..' in PurePosixPath(key).parts or key in result: raise ValueError('resource_key_collision')
        result[key] = digest
    if not result: raise ValueError('resource_manifest_empty')
    return result


def compare(historical, current):
    old = normalized_files(historical); new = normalized_files(current)
    missing = old.keys() - new.keys(); added = new.keys() - old.keys()
    changed = sum(old[k] != new[k] for k in old.keys() & new.keys())
    return {'resources_match_historical': not missing and not added and changed == 0,
            'historical_resource_manifest_sha256': canonical_sha(old),
            'current_resource_manifest_sha256': canonical_sha(new),
            'historical_resource_count': len(old), 'current_resource_count': len(new),
            'missing_resource_count': len(missing), 'additional_resource_count': len(added),
            'changed_resource_count': changed}
