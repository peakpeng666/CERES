#!/usr/bin/env python3
"""Publication gate: actual workflow must use the indexed tested bootstrap exactly.
Run against the actual workflow before publication; no workflow is required in
an index-only runtime tree. Fixtures test this gate using synthetic workflow text.
"""
import argparse
from pathlib import Path
import textwrap

BOOTSTRAP = "import hashlib,json,os,pathlib\ntry:\n    root=pathlib.Path(os.environ['CANDIDATE_ROOT'])\n    raw=(root/'source-index.json').read_bytes()\n    assert hashlib.sha256(raw).hexdigest()==os.environ['REVIEWED_SOURCE_INDEX_SHA256']\n    index=json.loads(raw)\n    helper=root/'recipe/report_candidate_failure.py'\n    data=helper.read_bytes()\n    assert not helper.is_symlink() and hashlib.sha256(data).hexdigest()==index['files']['recipe/report_candidate_failure.py']['sha256']\n    exec(compile(data,str(helper),'exec'),{'__name__':'verified_candidate_report','__file__':str(helper)})\nexcept Exception:\n    print('code=UNKNOWN')\n"

def verify_text(workflow):
    start = "<<'PY_FAILURE'\n"
    end = '\n          PY_FAILURE'
    if workflow.count(start) != 1 or workflow.count(end) != 1:
        raise ValueError('Workflow projection bootstrap mismatch')
    body = textwrap.dedent(workflow.split(start, 1)[1].split(end, 1)[0]) + '\n'
    if body != BOOTSTRAP:
        raise ValueError('Workflow projection bootstrap mismatch')

def main():
    p=argparse.ArgumentParser();p.add_argument('--workflow',required=True)
    args=p.parse_args()
    verify_text(Path(args.workflow).read_text())
    print('Workflow projection parity PASS')

if __name__ == '__main__':
    main()
