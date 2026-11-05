#!/usr/bin/env python3
"""Recover exact committed bytes only from declared checkout EOL conversions.

For newly created isolated source checkouts only. Unknown differences fail closed.
No index, attribute, sparse or permission changes. Strict verification remains intact.
"""
import hashlib, json, os, pathlib, stat, subprocess, sys
if __package__:
    from .verify_worktree import TARGET, verify
else:
    from verify_worktree import TARGET, verify

def materialize(root, expected=TARGET):
    root=pathlib.Path(root).resolve()
    def git(*args):return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.DEVNULL)
    if git('rev-parse','HEAD').decode().strip()!=expected:raise ValueError('source_revision_mismatch')
    if git('diff-index','--cached','--name-only','HEAD','--').strip():raise ValueError('index_revision_mismatch')
    if git('rev-parse','--show-object-format').decode().strip()!='sha1':raise ValueError('unsupported_git_object_format')
    repairs=[]
    for entry in git('ls-files','-t','--stage','-z').split(b'\0'):
        if not entry:continue
        meta,name=entry.split(b'\t',1);tag,mode,oid,stage=meta.split()
        if stage!=b'0':raise ValueError('unmerged_index')
        if tag==b'S':continue
        relative=pathlib.PurePosixPath(os.fsdecode(name))
        if relative.is_absolute() or '..' in relative.parts:raise ValueError('unsafe_path')
        path=root.joinpath(*relative.parts)
        for parent in path.parents:
            if parent==root:break
            if parent.is_symlink():raise ValueError('symlink_parent')
        info=path.lstat()
        if mode==b'120000':
            if not stat.S_ISLNK(info.st_mode):raise ValueError('tracked_symlink_changed')
            data=os.fsencode(os.readlink(path))
            if hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest().encode()!=oid:raise ValueError('tracked_symlink_changed')
            continue
        if mode not in (b'100644',b'100755') or not stat.S_ISREG(info.st_mode):raise ValueError('tracked_mode_mismatch')
        if bool(info.st_mode & 0o111)!=(mode==b'100755'):raise ValueError('tracked_mode_mismatch')
        data=path.read_bytes()
        if hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest().encode()==oid:continue
        raw=git('cat-file','blob',oid.decode())
        attributes=git('check-attr','-z','text','eol','--',os.fsdecode(name)).split(b'\0')
        attrs={attributes[i+1]:attributes[i+2] for i in range(0,len(attributes)-1,3)}
        if attrs.get(b'text') not in (b'set',b'auto') or attrs.get(b'eol') not in (b'lf',b'crlf'):raise ValueError('undeclared_byte_difference')
        canonical=raw.replace(b'\r\n',b'\n')
        converted=canonical if attrs[b'eol']==b'lf' else canonical.replace(b'\n',b'\r\n')
        if data!=converted:raise ValueError('unknown_byte_difference')
        repairs.append((path,raw,info.st_mode,data))
    # Validate every entry before writing any repair. Recheck bytes/mode at write.
    for path,raw,mode,before in repairs:
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode!=mode or path.read_bytes()!=before:raise ValueError('checkout_changed_during_validation')
        # This private freshly checked-out tree is not shared with any worker.
        with path.open('wb') as stream:stream.write(raw)
        if path.stat().st_mode!=mode:raise ValueError('tracked_mode_mismatch')
    result=verify(root,expected)
    return {**result,'declared_eol_materializations':len(repairs)}

if __name__=='__main__':print(json.dumps(materialize(sys.argv[1])))
