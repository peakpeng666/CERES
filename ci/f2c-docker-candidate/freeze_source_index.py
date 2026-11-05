"""Local review packaging, never a substitute for independent source review."""
import argparse
import hashlib
import json
from pathlib import Path
import stat

def freeze(root):
    root=root.resolve();target=root/'source-index.json'
    if target.exists():raise RuntimeError('never replace reviewed source index')
    rows={}
    for path in sorted(root.rglob('*')):
        relative=path.relative_to(root)
        if '__pycache__' in relative.parts:continue
        if relative.parts[0]=='validation' or relative.parts[:2]==('preparation','runtime'):continue
        if path.is_symlink():raise RuntimeError('source alias prohibited')
        if path.is_dir():continue
        if not stat.S_ISREG(path.stat().st_mode):raise RuntimeError('source member must be regular')
        rows[relative.as_posix()]={'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    index={'schema':1,'status':'reviewed-source','scope':'compiler-build-only','excluded_pending_subtrees':['validation','preparation/runtime'],'application_commit':'3acf840d1a0c16ab1cfcca15744e4ea6609ddb35','files':rows}
    target.write_text(json.dumps(index,sort_keys=True,indent=2)+'\n')
    return hashlib.sha256(target.read_bytes()).hexdigest()
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True)
    print(freeze(parser.parse_args().root))
