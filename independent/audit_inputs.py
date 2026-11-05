"""Validate frozen input bytes and reference feasibility independently."""
from validate import *

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True,
                        help='New audit JSON; existing evidence is never overwritten')
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError(args.output)
    root=Path(__file__).resolve().parents[1]
    manifest=root/'src/research/configs/cvrplib_cases.json'
    cases=json.loads(manifest.read_text())['cases']
    reports=[]
    for c in cases:
        vrp=root/c['instance_path']; sol=root/c['reference']['path']
        p=read_instance(vrp); routes,cost=read_solution(sol)
        k=c['contract']['vehicle_limit'] or len(p['demands'])-1
        reports.append({'case_id':c['case_id'],'instance_hash_matches':sha(vrp)==c['instance_sha256'],
                        'reference_hash_matches':sha(sol)==c['reference']['sha256'],
                        'vehicle_limit':k,'reference_label':c['reference']['label'],
                        **check(p,routes,cost,k)})
    output={'manifest_sha256':sha(manifest),'cases':reports,'all_passed':all(r['valid'] and r['instance_hash_matches'] and r['reference_hash_matches'] for r in reports)}
    with args.output.open('x') as stream:
        stream.write(json.dumps(output,indent=2)+'\n')
    print(json.dumps({'cases':len(reports),'all_passed':output['all_passed'],'manifest_sha256':output['manifest_sha256']}))
    raise SystemExit(0 if output['all_passed'] else 1)


if __name__ == '__main__':
    main()
