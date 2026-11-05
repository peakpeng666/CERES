"""Provision one derived Jammy CA alias, never new trust roots.

Only the trusted preparation CLI writes. verify() is read-only. The root/owner/
SSL arguments exist for pure temporary-tree fixtures, not CLI configuration.
"""
import hashlib
import json
import os
from pathlib import Path
import ssl
import stat
import sys

BUNDLE = '/etc/ssl/certs/ca-certificates.crt'
ALIAS = '/usr/lib/ssl/cert.pem'
RECEIPT = '/opt/urp-bootstrap-evidence/system-ca-default.json'
MAX_BUNDLE = 4 * 1024 * 1024
MAX_RECEIPT = 16 * 1024
DIRECTORIES = ('etc', 'etc/ssl', 'etc/ssl/certs', 'usr', 'usr/lib',
               'usr/lib/ssl', 'opt', 'opt/urp-bootstrap-evidence')


class Failure(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__('SYSTEM_CA_INVARIANT')


def require(condition, code):
    if not condition:
        raise Failure(code)


def stable(s):
    return (s.st_dev, s.st_ino, s.st_mode, s.st_nlink, s.st_uid, s.st_gid,
            s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def layout(root, owner_uid, owner_gid):
    require(root.is_absolute(), 71)
    require(not any(k in os.environ for k in ('SSL_CERT_FILE', 'SSL_CERT_DIR')), 71)
    try:
        for path in (root, *(root / p for p in DIRECTORIES)):
            s = path.lstat()
            require(stat.S_ISDIR(s.st_mode) and s.st_uid == owner_uid
                    and s.st_gid == owner_gid and not s.st_mode & 0o022, 71)
    except OSError:
        raise Failure(71) from None


def read_regular(path, limit, owner_uid, owner_gid, code):
    try:
        before = path.lstat()
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                and before.st_uid == owner_uid and before.st_gid == owner_gid
                and not before.st_mode & 0o022 and 0 < before.st_size <= limit, code)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            require(stable(os.fstat(fd)) == stable(before), code)
            with os.fdopen(fd, 'rb', closefd=False) as f:
                data = f.read(limit + 1)
            require(len(data) == before.st_size and len(data) <= limit, code)
            require(stable(os.fstat(fd)) == stable(before)
                    and stable(path.lstat()) == stable(before), code)
        finally:
            os.close(fd)
    except OSError:
        raise Failure(code) from None
    return data


def observation(root, owner_uid, owner_gid, ssl_api):
    layout(root, owner_uid, owner_gid)
    try:
        require(ssl_api.get_default_verify_paths().openssl_cafile == ALIAS, 74)
    except Failure:
        raise
    except Exception:
        raise Failure(74) from None
    bundle = root / BUNDLE.lstrip('/')
    data = read_regular(bundle, MAX_BUNDLE, owner_uid, owner_gid, 72)
    try:
        context = ssl_api.SSLContext(ssl_api.PROTOCOL_TLS_CLIENT)
        # Load only the held distribution bundle, not seed/host/default roots.
        context.load_verify_locations(cadata=data.decode('ascii'))
        count = context.cert_store_stats()['x509_ca']
        require(type(count) is int and 0 < count <= 10000, 73)
    except Failure:
        raise
    except Exception:
        raise Failure(73) from None
    require(read_regular(bundle, MAX_BUNDLE, owner_uid, owner_gid, 72) == data, 72)
    return {'bundle_path': BUNDLE, 'bundle_sha256': hashlib.sha256(data).hexdigest(),
            'bundle_bytes': len(data), 'ca_certificates_count': count,
            'alias_path': ALIAS, 'alias_target': BUNDLE}


def alias_state(root, owner_uid, owner_gid, absent_allowed):
    path = root / ALIAS.lstrip('/')
    try:
        s = path.lstat()
    except FileNotFoundError:
        require(absent_allowed, 75)
        return 'absent'
    except OSError:
        raise Failure(75) from None
    try:
        require(stat.S_ISLNK(s.st_mode) and s.st_nlink == 1
                and s.st_uid == owner_uid and s.st_gid == owner_gid
                and os.readlink(path) == BUNDLE, 75)
    except OSError:
        raise Failure(75) from None
    return 'already_correct'


def provision(root=Path('/'), owner_uid=0, owner_gid=0, ssl_api=ssl):
    root = Path(root)
    observed = observation(root, owner_uid, owner_gid, ssl_api)
    receipt = root / RECEIPT.lstrip('/')
    require(not os.path.lexists(receipt), 77)
    action = alias_state(root, owner_uid, owner_gid, True)
    if action == 'absent':
        try:
            os.symlink(BUNDLE, root / ALIAS.lstrip('/'))
        except OSError:
            raise Failure(76) from None
        action = 'created'
    alias_state(root, owner_uid, owner_gid, False)
    require(observation(root, owner_uid, owner_gid, ssl_api) == observed, 72)
    record = {'schema': 1, 'status': 'verified-distribution-ca-alias',
              'provenance': 'generated-bundle-and-derived-alias-not-archive-owned',
              'alias_action': action, **observed}
    raw = (json.dumps(record, sort_keys=True, indent=2) + '\n').encode('ascii')
    require(len(raw) <= MAX_RECEIPT, 77)
    try:
        fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, 'wb') as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        raise Failure(77) from None
    require(verify(root, owner_uid, owner_gid, ssl_api) == record, 77)
    return record


def verify(root=Path('/'), owner_uid=0, owner_gid=0, ssl_api=ssl):
    root = Path(root)
    observed = observation(root, owner_uid, owner_gid, ssl_api)
    alias_state(root, owner_uid, owner_gid, False)
    raw = read_regular(root / RECEIPT.lstrip('/'), MAX_RECEIPT, owner_uid, owner_gid, 77)
    try:
        def unique(pairs):
            value = {}
            for key, item in pairs:
                require(key not in value, 77)
                value[key] = item
            return value
        record = json.loads(raw, object_pairs_hook=unique)
        require(type(record) is dict and record.get('alias_action') in ('created', 'already_correct'), 77)
        expected = {'schema': 1, 'status': 'verified-distribution-ca-alias',
                    'provenance': 'generated-bundle-and-derived-alias-not-archive-owned',
                    'alias_action': record['alias_action'], **observed}
        require(record == expected and type(record.get('schema')) is int
                and type(record.get('bundle_bytes')) is int
                and type(record.get('ca_certificates_count')) is int, 77)
    except Failure:
        raise
    except Exception:
        raise Failure(77) from None
    return record


def main():
    if sys.argv[1:] != ['--install-default-alias']:
        return 78
    try:
        provision()
    except Failure as error:
        return error.code if type(error.code) is int and 71 <= error.code <= 78 else 78
    except Exception:
        return 78
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
