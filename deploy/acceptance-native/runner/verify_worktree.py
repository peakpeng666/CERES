#!/usr/bin/env python3
"""Verify checked-out tracked bytes against fixed HEAD, without Git clean filters.

Skip sparse-excluded S entries explicitly. This tolerates existing repository
CRLF/attributes disagreement only when the actual bytes equal the committed blob.
No reset, renormalization, index write or source mutation is performed.
"""
import hashlib, os, pathlib, subprocess, sys
TARGET = '3acf840d1a0c16ab1cfcca15744e4ea6609ddb35'

def verify(root, expected=TARGET):
    root = pathlib.Path(root).resolve()
    def git(*args):
        return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.DEVNULL)
    if git('rev-parse','HEAD').decode().strip() != expected:
        raise ValueError('source_revision_mismatch')
    if git('rev-parse','--show-object-format').decode().strip() != 'sha1':
        raise ValueError('unsupported_git_object_format')
    # Check index identities against HEAD; no staging can substitute different code.
    if git('diff-index','--cached','--name-only','HEAD','--').strip():
        raise ValueError('index_revision_mismatch')
    checked=skipped=0
    for entry in git('ls-files','-t','--stage','-z').split(b'\0'):
        if not entry: continue
        meta, name = entry.split(b'\t',1)
        tag, mode, oid, stage = meta.split()
        if stage != b'0': raise ValueError('unmerged_index')
        if tag == b'S':
            skipped += 1
            continue
        path=root/os.fsdecode(name)
        if mode == b'120000':
            if not path.is_symlink(): raise ValueError('tracked_symlink_changed')
            data=os.fsencode(os.readlink(path))
        elif mode in (b'100644',b'100755'):
            if path.is_symlink() or not path.is_file(): raise ValueError('tracked_file_missing')
            data=path.read_bytes()
        else: raise ValueError('unsupported_tracked_mode')
        actual=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest().encode()
        if actual != oid: raise ValueError('tracked_blob_mismatch')
        checked += 1
    if not checked: raise ValueError('empty_source_checkout')
    return {'tracked_blobs_verified':checked,'sparse_entries_skipped':skipped}

if __name__=='__main__':
    import json
    print(json.dumps(verify(sys.argv[1])))
