#!/usr/bin/env python3
"""Read-only canonical target projection from pinned, authenticated public metadata.

No package-preparer imports, native commands, network, or installed-state reads.
The caller must verify this helper's source anchor before calling project().
"""
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import stat

SNAPSHOT = '20261021T000000Z'
RELEASE_HASHES = {
    'jammy': 'c14060cd8c6d625874dfcb9523a35a395bf4865c28b6b6a82569ef326fe92dc6',
    'jammy-updates': '34beb69f860de8eb98f2315b4c38c2542b1b005153652e399d898c892431adda',
    'jammy-security': 'eaa0a9cb0ad5ea72dcaf84e81a1f13c82a5afbb08700bee189b9b7e2c638c5a9',
}
RELEASE_SIZES = {'jammy': 270087, 'jammy-updates': 128049, 'jammy-security': 128927}
INDEX_STANZA_COUNTS = {
    ('jammy', 'main'): 6090, ('jammy', 'universe'): 58923,
    ('jammy-updates', 'main'): 21642, ('jammy-updates', 'universe'): 5960,
    ('jammy-security', 'main'): 20377, ('jammy-security', 'universe'): 5017,
}
INDEX_UNCOMPRESSED_SIZES = {
    ('jammy', 'main'): 6779186, ('jammy', 'universe'): 64332414,
    ('jammy-updates', 'main'): 22094694, ('jammy-updates', 'universe'): 7297214,
    ('jammy-security', 'main'): 20698545, ('jammy-security', 'universe'): 6214130,
}
DIAGNOSTIC_PREFIX = 'SAFE_PACKAGE_DIAGNOSTIC:'
REFERENCE_PREFIX = 'SAFE_PACKAGE_TARGET_REFERENCE:'
RECEIPT_KEYS = frozenset('schema code operation exception_class http_status errno returncode suite component'.split())
REFERENCE_KEYS = frozenset('schema snapshot suite component stanza'.split())
OUTPUT_KEYS = frozenset('canonical_package canonical_version canonical_architecture target_snapshot target_suite target_component target_stanza'.split())
MAX_INDEX_BYTES = 16 * 1024 * 1024


def _check(value):
    if not value:
        raise ValueError('Canonical target unavailable')


def _json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _check(key not in result)
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=unique)


def _read(path, limit, exact=None, tail=False):
    """Refuse aliases in every path component and never block on special files."""
    path = Path(path).absolute()
    _check('..' not in path.parts and len(path.parts) > 1)
    directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    fd = None
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        before = os.fstat(fd)
        _check(stat.S_ISREG(before.st_mode) and before.st_nlink == 1)
        _check(exact is None or before.st_size == exact)
        _check(tail or before.st_size <= limit)
        if tail and before.st_size > limit:
            os.lseek(fd, before.st_size - limit, os.SEEK_SET)
        chunks, total = [], 0
        while True:
            block = os.read(fd, min(65536, limit + 1 - total))
            if not block:
                break
            chunks.append(block)
            total += len(block)
            _check(total <= limit)
        after = os.fstat(fd)
        _check((before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_nlink) ==
               (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_nlink))
        return b''.join(chunks)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory)


def _valid_receipt(receipt):
    _check(type(receipt) is dict and set(receipt) == RECEIPT_KEYS)
    _check(type(receipt['schema']) is int and receipt['schema'] == 1)
    _check(receipt['code'] == 'APT_CLOSURE_DOWNGRADE_REFUSED')
    _check(receipt['operation'] == 'closure-provenance')
    _check(receipt['exception_class'] == 'ClosureProvenanceError')
    _check(all(receipt[key] is None for key in ('http_status', 'errno', 'suite', 'component')))
    rc = receipt['returncode']
    _check(type(rc) is int and -255 <= rc <= 255 and rc != 0)


def _fields(text):
    result, key = {}, None
    for line in text.splitlines():
        if line.startswith((' ', '\t')) and key:
            result[key] += '\n' + line
        elif ': ' in line:
            key, value = line.split(': ', 1)
            _check(key not in result)
            result[key] = value
    return result


def _release_entries(data, suite):
    text = data.decode('utf-8')
    _check(text.startswith('-----BEGIN PGP SIGNED MESSAGE-----\n'))
    body = text.split('\n\n', 1)[1].split('-----BEGIN PGP SIGNATURE-----', 1)[0]
    body = '\n'.join(line[2:] if line.startswith('- ') else line for line in body.splitlines())
    header = _fields(body.split('MD5Sum:', 1)[0])
    _check(header.get('Origin') == 'Ubuntu' and header.get('Suite') == suite and header.get('Codename') == 'jammy')
    entries, active = {}, False
    for line in body.splitlines():
        if line == 'SHA256:':
            _check(not active and not entries)
            active = True
        elif active and line.startswith(' '):
            digest, size, name = line.split()
            _check(re.fullmatch('[0-9a-f]{64}', digest) and re.fullmatch('[0-9]{1,10}', size))
            _check(name not in entries)
            entries[name] = (digest, int(size))
        elif active:
            active = False
    return entries


def _decompress(compressed, expected_size):
    """Authenticated indexes can concatenate XZ streams, including an empty one."""
    remaining, chunks, total, streams = compressed, [], 0, 0
    while remaining:
        streams += 1
        _check(streams <= 8)
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=128 * 1024 * 1024)
        block = decoder.decompress(remaining, max_length=expected_size - total + 1)
        total += len(block)
        _check(total <= expected_size and decoder.eof)
        chunks.append(block)
        remaining = decoder.unused_data
    _check(streams > 0 and total == expected_size)
    return b''.join(chunks)


def project(root, receipt, log_path):
    """Return seven canonical public fields, or {} on any unavailable/invalid proof."""
    try:
        _valid_receipt(receipt)
        root = Path(root)
        log = _read(log_path, 4096, tail=True)
        _check(log.endswith(b'\n'))
        lines = log.splitlines()
        _check(len(lines) >= 2 and len(lines[-1]) <= 1024)
        reference_line = lines[-2].decode('ascii')
        terminal = lines[-1].decode('ascii')
        _check(reference_line.startswith(REFERENCE_PREFIX) and terminal.startswith(DIAGNOSTIC_PREFIX))
        digest = reference_line[len(REFERENCE_PREFIX):]
        _check(re.fullmatch('[0-9a-f]{64}', digest))
        terminal_receipt = _json(terminal[len(DIAGNOSTIC_PREFIX):])
        _valid_receipt(terminal_receipt)
        _check(terminal_receipt == receipt)
        raw = _read(root / 'package-target-reference.json', 512)
        _check(hashlib.sha256(raw).hexdigest() == digest)
        ref = _json(raw)
        _check(type(ref) is dict and set(ref) == REFERENCE_KEYS)
        _check(type(ref['schema']) is int and ref['schema'] == 1 and ref['snapshot'] == SNAPSHOT)
        suite, component, ordinal = ref['suite'], ref['component'], ref['stanza']
        _check(type(suite) is str and type(component) is str)
        key = (suite, component)
        _check(key in INDEX_STANZA_COUNTS and type(ordinal) is int and 0 <= ordinal < INDEX_STANZA_COUNTS[key])
        metadata = root / 'metadata' / ('snapshot-' + SNAPSHOT)
        release = _read(metadata / (suite + '.InRelease'), 300000, exact=RELEASE_SIZES[suite])
        _check(hashlib.sha256(release).hexdigest() == RELEASE_HASHES[suite])
        entries = _release_entries(release, suite)
        relative = component + '/binary-amd64/Packages'
        expected_hash, expected_size = entries[relative + '.xz']
        decoded_hash, decoded_size = entries[relative]
        _check(0 < expected_size <= MAX_INDEX_BYTES and decoded_size == INDEX_UNCOMPRESSED_SIZES[key])
        compressed = _read(metadata / (suite + '.' + component + '.amd64.Packages.xz'), MAX_INDEX_BYTES, exact=expected_size)
        _check(hashlib.sha256(compressed).hexdigest() == expected_hash)
        data = _decompress(compressed, decoded_size)
        _check(hashlib.sha256(data).hexdigest() == decoded_hash)
        target, count = None, 0
        for stanza in data.decode('utf-8').split('\n\n'):
            row = _fields(stanza)
            if 'Package' in row:
                if count == ordinal:
                    target = row
                count += 1
        _check(count == INDEX_STANZA_COUNTS[key] and target is not None)
        name, version, architecture = target['Package'], target['Version'], target['Architecture']
        _check(2 <= len(name) <= 60 and re.fullmatch('[a-z0-9][a-z0-9+.-]+', name))
        _check(1 <= len(version) <= 54 and re.fullmatch('[0-9][0-9A-Za-z.+:~-]*', version))
        _check(architecture in ('amd64', 'all'))
        return {'canonical_package': name, 'canonical_version': version, 'canonical_architecture': architecture,
                'target_snapshot': SNAPSHOT, 'target_suite': suite, 'target_component': component, 'target_stanza': ordinal}
    except Exception:
        return {}
