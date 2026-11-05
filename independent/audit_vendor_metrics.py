"""Recompute every X distance independently with Decimal; emulate native precision."""
from validate import *
import struct,math
def f32(x):return struct.unpack('f',struct.pack('f',float(x)))[0]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True,
                        help='New audit JSON; existing evidence is never overwritten')
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError(args.output)
    root=Path(__file__).resolve().parents[1]
    rows=[]
    for c in json.loads((root/'src/research/configs/cvrplib_cases.json').read_text())['cases']:
     if c['source_family']!='X':continue
     p=read_instance(root/c['instance_path']); errors=[]; edges=0;maximum=0
     for a in p['coordinates']:
      for b in p['coordinates']:
       expected=distance(a,b);maximum=max(maximum,expected)
       dx=f32(f32(a[0])-f32(b[0]));dy=f32(f32(a[1])-f32(b[1]))
       # SISR source uses float coordinates and float arithmetic before sqrt.
       float_cost=math.floor(f32(math.sqrt(f32(f32(dx*dx)+f32(dy*dy))))+0.5)
       double_cost=math.floor(math.hypot(float(a[0])-float(b[0]),float(a[1])-float(b[1]))+0.5)
       if expected!=float_cost or expected!=double_cost or not 0<=expected<=32767:errors.append([edges,expected,float_cost,double_cost])
       edges+=1
     rows.append({'case_id':c['case_id'],'edges':edges,'maximum_distance':maximum,'mismatches':errors})
    with args.output.open('x') as stream:
        stream.write(json.dumps(rows,indent=2)+'\n')
    print(json.dumps({'cases':len(rows),'edges':sum(r['edges'] for r in rows),'mismatches':sum(len(r['mismatches']) for r in rows)}))
    raise SystemExit(1 if any(r['mismatches'] for r in rows) else 0)


if __name__ == '__main__':
    main()
