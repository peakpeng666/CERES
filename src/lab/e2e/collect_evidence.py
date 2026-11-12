"""Copy CERES evidence without caches or credentials; never rewrite original run records."""
from pathlib import Path
import argparse,hashlib,json,shutil,sys,re,importlib.metadata as md
ROOT=Path(__file__).resolve().parents[3];lab=ROOT/'src/lab'
ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,default=ROOT/'setup/2026-10-15/integration/acceptance');ap.add_argument('--since',default='')
a=ap.parse_args();out=a.out;out.mkdir(parents=True,exist_ok=True)
def recent(src):
 m=re.search(r'\d{8}T\d{6}',src.name)
 return not a.since or bool(m and m.group()>=a.since)
for src in sorted((lab/'runtime/sim').glob('*')):
 if not src.is_dir() or not recent(src):continue
 dest=out/'sessions'/src.name;dest.mkdir(parents=True,exist_ok=True)
 for path in list(src.glob('*.json'))+list(src.glob('*.yaml')):
  shutil.copy2(path,dest/path.name)
 for name in ('logs','runs','ros-log'):
  if (src/name).is_dir():shutil.copytree(src/name,dest/name,dirs_exist_ok=True)
 control=lab/'runtime/logs'/f'sim-{src.name}.log'
 if control.exists():shutil.copy2(control,dest/'control.log')
for src in sorted((lab/'runtime/e2e').glob('*')):
 if recent(src) and any((src/name).is_file() for name in ('report.json','edge-cases.json','warehouse-smoke.json')):
  shutil.copytree(src,out/'api-loops'/src.name,dirs_exist_ok=True)
for src in sorted((lab/'runtime/runs').glob('*')):
 if src.is_dir() and recent(src):shutil.copytree(src,out/'solver-processes'/src.name,dirs_exist_ok=True)
files=['src/lab/uv.lock','src/lab/pyproject.toml','src/frontend/package-lock.json','src/frontend/package.json','setup/run.sh','src/lab/vendor/fields2cover-manifest.json']
metadata={'python':sys.version,'packages':{n:md.version(n) for n in ('pyvrp','ortools','fastapi','uvicorn','numpy','scipy','shapely','pytest')},'files':{f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in files}}
(out/'environment.json').write_text(json.dumps(metadata,indent=2))
manifest={str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob('*')) if p.is_file() and p.name!='manifest-sha256.json'}
(out/'manifest-sha256.json').write_text(json.dumps(manifest,indent=2));print('Evidence files:',len(manifest))
