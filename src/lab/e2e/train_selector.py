"""Reproducible local training study; protocol written before any solver labels exist."""
import argparse,copy,hashlib,json,math,os,platform,random,statistics,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ceres_lab import learned
from ceres_lab.jobs import instance_sha256
from ceres_lab.scenarios import benchmark
from ceres_lab import paths

def dump(path,obj):path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False))
def generate(seed):
    r=random.Random(seed);n=r.choice([20,50,100]);family=r.choice(['uniform','clustered'])
    centres=[(r.uniform(15,85),r.uniform(15,85)) for _ in range(4)]
    pts=[[50.,50.]]
    for _ in range(n):
        if family=='uniform':p=[r.uniform(0,100),r.uniform(0,100)]
        else:
            c=r.choice(centres);p=[min(100,max(0,r.gauss(v,8))) for v in c]
        pts.append(p)
    return family,dict(coordinates=pts,demands=[0]+[r.randint(1,10) for _ in range(n)],capacity=50,depot=0,
        fleet='unlimited_identical_vehicles',objective='sum_unrounded_euclidean_distance')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=False)
    protocol=dict(id='lab.cvrp.knn_crc.v1',train=60,calibration=30,test=30,instance_seeds=list(range(160000,160120)),
        portfolio=learned.ALGOS,budget_s=learned.BUDGET,solver_seeds=[11,29],k=3,alpha=.10,harm_threshold=1.01,
        metric='各算法两次重复的原始欧氏成本均值（PyVRP 使用两种子，OR-Tools 未使用种子）；检查所有路线恰好访问一次并满足容量',
        scope='IID generator: uniform/clustered × n20/50/100, capacity50, demands1..10; synthetic benchmark only',
        selection='模型/特征归一化/SBS仅训练集；门控仅校准集；测试集仅报告，不调参、不重训',
        generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        model_source_sha256=hashlib.sha256(Path(learned.__file__).read_bytes()).hexdigest(),
        hardware=platform.platform(),runtime='sequential subprocess; wall stop excludes interpreter startup; OR-Tools seed currently unused')
    dump(a.out/'protocol.json',protocol);records=[];start=time.time()
    for i,seed in enumerate(protocol['instance_seeds']):
        family,inst=generate(seed);sid=f'local-study-{seed}';split='train' if i<60 else 'calibration' if i<90 else 'test'
        row=dict(id=sid,split=split,seed=seed,family=family,instance=inst,instance_sha256=instance_sha256(inst),features=learned.features(inst),costs={})
        rawdir=a.out/sid;rawdir.mkdir();dump(rawdir/'instance.json',row)
        sc=dict(id=sid,problem_class='euclidean_cvrp',frame=dict(kind='benchmark',units='unitless'),cost_unit='euclidean')
        order=learned.ALGOS if i%2==0 else list(reversed(learned.ALGOS))
        for algo in order:
            costs=[]
            for solver_seed in protocol['solver_seeds']:
                request=dict(scenario=sc,instance=inst,algorithm_id=algo,params=dict(time_limit_s=learned.BUDGET,seed=solver_seed))
                stem=rawdir/f'{algo}-{solver_seed}';dump(stem.with_suffix(stem.suffix+'.request.json'),request)
                p=subprocess.run([sys.executable,'-m','ceres_lab.worker'],input=json.dumps(request),text=True,capture_output=True,cwd=paths.LAB,timeout=15)
                stem.with_suffix(stem.suffix+'.stdout.json').write_text(p.stdout);stem.with_suffix(stem.suffix+'.stderr.log').write_text(p.stderr)
                r=json.loads(p.stdout)
                if p.returncode or not r.get('check',{}).get('valid'):raise RuntimeError(f'{sid}/{algo} failed: {r}')
                costs.append(r['check']['computed_cost'])
            row['costs'][algo]=statistics.mean(costs)
        records.append(row);dump(a.out/'records.json',records)
        if i%10==9:print(f'{i+1}/120 solved, {time.time()-start:.1f}s',flush=True)
    artifact=learned.build(records)
    mutated=copy.deepcopy(records)
    for r in mutated:
        if r['split']=='test':r['costs']={a:(j+1)*1234567 for j,a in enumerate(learned.ALGOS)}
    assert artifact==learned.build(mutated),'test label leakage'
    evalrows=[]
    for r in records:
        if r['split']!='test':continue
        p=learned.predict(artifact['model'],r['instance']);selected,accepted=learned.route(p,artifact['gate'])
        sbs=artifact['model']['sbs'];c=r['costs'];best=min(c.values())
        evalrows.append(dict(id=r['id'],costs=c,prediction=p,selected=selected,accepted=accepted,
            harm=c[selected]>1.01*c[sbs],improvement_pct=100*(1-c[selected]/c[sbs]),
            regret_pct=100*(c[selected]/best-1),sbs_regret_pct=100*(c[sbs]/best-1)))
    evaluation=dict(test_n=len(evalrows),train_n=60,calibration_n=30,sbs=artifact['model']['sbs'],
        harmful_instances=sum(r['harm'] for r in evalrows),alternative_count=sum(r['accepted'] for r in evalrows),
        mean_improvement_pct=statistics.mean(r['improvement_pct'] for r in evalrows),mean_regret_pct=statistics.mean(r['regret_pct'] for r in evalrows),
        sbs_mean_regret_pct=statistics.mean(r['sbs_regret_pct'] for r in evalrows),held_out_label_corruption_test=True,
        distinct_input_hashes=len(set(r['instance_sha256'] for r in records)),wall_s=time.time()-start,
        caveat='本机小样本合成分布独立留出评估；无跨机器/分布保证；0 次伤害不等于真实风险为零')
    document=dict(status='evaluated',artifact=artifact,artifact_sha256=hashlib.sha256(json.dumps(artifact,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                  evaluation=evaluation,protocol=protocol,evidence=str(a.out))
    dump(a.out/'evaluation.json',dict(summary=evaluation,rows=evalrows));dump(a.out/'model.json',document)
    dump(learned.MODEL,document);print(json.dumps(evaluation,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
