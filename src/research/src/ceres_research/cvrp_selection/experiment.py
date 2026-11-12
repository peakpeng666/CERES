"""Offline selector and monotone CRC-gate transfer. Standard library; no solver/API."""
from pathlib import Path
import json, math, statistics as st, time, csv, hashlib, collections, argparse, copy, platform, sys
from .preflight import ROOT, digest, load_json, validate
ALGOS=['multistart_vnd','parallel_savings','pyvrp_ils_s1e6']
PROFILES=['cpu025','cpu100']

def finite_number(value,label,minimum=None):
    try:valid=type(value) in (int,float) and math.isfinite(value) and (minimum is None or value>=minimum)
    except OverflowError:valid=False
    if not valid:
        raise ValueError(label+' must be a finite number'+(' >= '+str(minimum) if minimum is not None else ''))
    return value

def feature_values(record,dimension=None):
    values=record.get('values') if isinstance(record,dict) else None
    if not isinstance(values,list) or not values or (dimension is not None and len(values)!=dimension):
        raise ValueError('feature vectors must have the same nonzero dimension')
    for x in values:finite_number(x,'feature')
    return values

def prediction_contract(pred):
    if not isinstance(pred,dict) or any(not isinstance(pred.get(k),str) or not pred[k] for k in ('chosen','sbs')):
        raise ValueError('prediction must contain chosen and sbs algorithm names')
    finite_number(pred.get('score'),'ranking score',0)

def timed(fn,*a,**k):
    c=time.process_time();w=time.perf_counter();r=fn(*a,**k)
    return r,{'cpu_seconds':time.process_time()-c,'wall_seconds':time.perf_counter()-w}

def features(x):
    coords=x['coordinates'];all_demands=x['demands'];cap=x['capacity']
    if not isinstance(coords,list) or len(coords)<2 or not isinstance(all_demands,list) or len(coords)!=len(all_demands):
        raise ValueError('coordinates and demands must include a depot and at least one customer')
    finite_number(cap,'capacity',0)
    if cap==0:raise ValueError('capacity must be positive')
    for point in coords:
        if not isinstance(point,(list,tuple)) or len(point)!=2:raise ValueError('coordinates must be pairs')
        for value in point:finite_number(value,'coordinate')
    for demand in all_demands:
        finite_number(demand,'demand',0)
        if demand>cap:raise ValueError('demand exceeds capacity')
    if all_demands[0]!=0:raise ValueError('depot demand must be zero')
    demands=all_demands[1:];n=len(demands)
    radii=[math.dist(p,coords[0]) for p in coords[1:]];r=st.mean(radii)
    nearest=[min((math.dist(p,q) for j,q in enumerate(coords[1:]) if i!=j),default=0.) for i,p in enumerate(coords[1:])]
    width=max(p[0] for p in coords[1:])-min(p[0] for p in coords[1:])
    height=max(p[1] for p in coords[1:])-min(p[1] for p in coords[1:])
    f=[math.log(n),st.mean(demands)/cap,st.pstdev(demands)/cap,st.pstdev(radii)/r if r else 0.,st.mean(nearest)/r if r else 0.,min(width,height)/max(width,height) if max(width,height) else 0.]
    feature_values({'values':f})
    return {'values':f,'star_upper_bound':2*math.fsum(radii)}

def aggregate(rows,inputs):
    """Aggregate a verified historical panel; never silently drop failed rows."""
    groups=collections.defaultdict(list)
    for r in rows:
        if r.get('status')!='returned' or any(r.get(k) is not True for k in ('accepted','budget_conformant','scoreable')) or r.get('checker_errors'):
            raise ValueError('performance panel contains an unaccepted run; retain it in the failure ledger')
        groups[(r['profile'],r['instance_id'],r['algorithm'])].append(r)
    matrix={}
    for (p,i,a),rs in groups.items():
        costs=[finite_number(r['recomputed_cost'],'recomputed cost',0) for r in rs]
        scale=finite_number(inputs[i]['star_upper_bound'],'star upper bound',0)
        if scale==0 and any(costs):raise ValueError('positive cost with zero geometric upper bound')
        seeds=[r['solver_seed'] for r in rs]
        if len(set(seeds))!=len(seeds):raise ValueError('duplicate solver seed in performance cell')
        matrix.setdefault(p,{}).setdefault(i,{})[a]={
            'cost':st.mean(costs),
            'normalized_cost':st.mean(costs)/scale if scale else 0.,
            'solver_cpu_seconds':st.mean(r['solver_cpu_seconds'] for r in rs),
            'child_cpu_seconds':st.mean(r['child_total_cpu_seconds'] for r in rs),
            'wall_seconds':st.mean(r['total_wall_seconds'] for r in rs),
            'checker_cpu_seconds':st.mean(r['checker_cpu_seconds'] for r in rs),
            'supervisor_cpu_seconds':st.mean(r['supervisor_cpu_seconds'] for r in rs),
            'seeds':[r['solver_seed'] for r in rs]}
    return matrix

def fit(train_ids,feat,matrix):
    if not train_ids or any(not isinstance(i,str) for i in train_ids) or len(set(train_ids))!=len(train_ids):
        raise ValueError('training IDs must be nonempty and unique')
    train_ids=sorted(train_ids)
    dimension=len(feature_values(feat[train_ids[0]]))
    for i in train_ids:
        feature_values(feat[i],dimension)
        for a in ALGOS:finite_number(matrix[i][a]['normalized_cost'],'training cost',0)
    cols=list(zip(*(feat[i]['values'] for i in train_ids)))
    mu=list(map(st.mean,cols));sd=[st.pstdev(c) or 1. for c in cols]
    scaled={i:[(x-m)/s for x,m,s in zip(feat[i]['values'],mu,sd)] for i in train_ids}
    mean_cost={a:st.mean(matrix[i][a]['normalized_cost'] for i in train_ids) for a in ALGOS}
    sbs=min(ALGOS,key=lambda a:(mean_cost[a],a))
    return {'train_ids':train_ids,'mu':mu,'sd':sd,'scaled':scaled,'train_means':mean_cost,'sbs':sbs,
            'targets':{i:{a:matrix[i][a]['normalized_cost'] for a in ALGOS} for i in train_ids},'k':3}

def predict(model,i,feat):
    feature_values(feat[i],len(model['mu']))
    v=[(x-m)/s for x,m,s in zip(feat[i]['values'],model['mu'],model['sd'])]
    near=sorted(model['train_ids'],key=lambda t:(math.dist(v,model['scaled'][t]),t))[:model['k']]
    pred={a:st.mean(model['targets'][t][a] for t in near) for a in ALGOS}
    chosen=min(ALGOS,key=lambda a:(pred[a],a));sbs=model['sbs']
    score=(pred[sbs]-pred[chosen])/pred[sbs] if pred[sbs] else 0.
    return {'instance_id':i,'chosen':chosen,'sbs':sbs,'score':score,'predicted_costs':pred,'neighbor_ids':near}

def crc(cal,alpha):
    """Nonnegative gate parameter. Strict > ensures right-continuous losses."""
    finite_number(alpha,'alpha')
    if not 0<alpha<1:raise ValueError('alpha must be strictly between zero and one')
    if not isinstance(cal,(list,tuple)):raise ValueError('calibration records must be a list')
    for row in cal:
        prediction_contract(row)
        if type(row.get('harm')) is not bool:raise ValueError('calibration harm must be boolean')
    n=len(cal);trials=[]
    for threshold in sorted({0.}|{x['score'] for x in cal if x['score']>=0}):
        loss=sum(x['harm'] and x['chosen']!=x['sbs'] and x['score']>threshold for x in cal)
        corrected=(loss+1)/(n+1)
        trials.append({'threshold':threshold,'sum_harm':loss,'corrected_risk':corrected})
        if corrected<=alpha:
            return {'threshold':threshold,'safe_endpoint':False,'n_calibration':n,'alpha':alpha,'trials':trials,
                    'corrected_risk':corrected,'finite_sample_floor':1/(n+1)}
    return {'threshold':None,'safe_endpoint':True,'n_calibration':n,'alpha':alpha,'trials':trials,
            'corrected_risk':None,'finite_sample_floor':1/(n+1),'reason':'no finite threshold satisfies correction; structurally zero-risk fallback'}

def route(pred,gate=None):
    prediction_contract(pred)
    if gate is None:return pred['chosen'],pred['chosen']!=pred['sbs']
    if not isinstance(gate,dict) or type(gate.get('safe_endpoint')) is not bool:raise ValueError('invalid CRC gate')
    if not gate['safe_endpoint']:finite_number(gate.get('threshold'),'CRC threshold',0)
    accept=not gate['safe_endpoint'] and pred['chosen']!=pred['sbs'] and pred['score']>gate['threshold']
    return (pred['chosen'] if accept else pred['sbs']),accept

def fit_calibrate(profile,fold,manifest,feat,matrix):
    if type(fold) is not int or not 0<=fold<4:raise ValueError('fold must be 0..3')
    ids=[m['instance_id'] for m in manifest]
    if len(set(ids))!=len(ids):raise ValueError('duplicate instance IDs in split manifest')
    groups={}
    for m in manifest:
        replicate=m['replicate']
        if type(replicate) is not int or not 0<=replicate<4:raise ValueError('replicate must be 0..3')
        group=m.get('base_id') or m.get('parent_instance_id') or m['instance_id']
        if group in groups and groups[group]!=replicate:
            raise ValueError('related instances cross train/calibration/test partitions')
        groups[group]=replicate
    test=sorted(m['instance_id'] for m in manifest if m['replicate']==fold)
    cal=sorted(m['instance_id'] for m in manifest if m['replicate']==(fold+1)%4)
    train=sorted(m['instance_id'] for m in manifest if m['replicate'] not in [fold,(fold+1)%4])
    model,train_time=timed(fit,train,feat,matrix)
    cal_preds=[]
    c=time.process_time();w=time.perf_counter()
    for i in cal:
        pred=predict(model,i,feat)
        pred['harm']=matrix[i][pred['chosen']]['cost']>1.01*matrix[i][model['sbs']]['cost']
        cal_preds.append(pred)
    gates={str(a):crc(cal_preds,a) for a in [.1,.2]}
    cal_time={'cpu_seconds':time.process_time()-c,'wall_seconds':time.perf_counter()-w}
    outputs=[]
    for i in test:
        pred,inf_time=timed(predict,model,i,feat)
        pred['inference_time']=inf_time
        outputs.append(pred)
    return {'profile':profile,'fold':fold,'train_ids':train,'calibration_ids':cal,'test_ids':test,'model':model,
            'calibration_predictions':cal_preds,'gates':gates,'test_predictions':outputs,'training_time':train_time,'calibration_time':cal_time}

def decision_signature(f):
    return {'model':f['model'],'gates':f['gates'],
            'test_predictions':[{k:v for k,v in p.items() if k!='inference_time'} for p in f['test_predictions']]}

def run(out):
    out.mkdir(parents=True,exist_ok=True)
    c0=time.process_time();w0=time.perf_counter()
    preflight=validate(write=False)
    plan=load_json(ROOT/'PLAN.json');manifest=load_json(ROOT/'data/instance_manifest.json')['instances']
    raw=[json.loads(x) for x in (ROOT/'data/raw.jsonl').read_text().splitlines()]
    feat={};feature_times={}
    for m in manifest:
        i=m['instance_id'];feat[i],feature_times[i]=timed(features,load_json(ROOT/'data/instances'/m['file']))
    matrix=aggregate(raw,feat);folds=[];rows=[];leakage=[]
    byid={m['instance_id']:m for m in manifest}
    for p in PROFILES:
        for f in range(4):
            fold=fit_calibrate(p,f,manifest,feat,matrix[p]);folds.append(fold)
            # Adversarially corrupt *all held-out performance labels*, then refit and recalibrate.
            mutated=copy.deepcopy(matrix[p])
            for j,i in enumerate(fold['test_ids']):
                for k,a in enumerate(ALGOS):
                    mutated[i][a]['cost']=(j+1)*(k+2)*1234567.
                    mutated[i][a]['normalized_cost']=(j+1)*(k+2)*9999.
            other=fit_calibrate(p,f,manifest,feat,mutated)
            same=decision_signature(fold)==decision_signature(other)
            assert same,'held-out-label leakage'
            leakage.append({'profile':p,'fold':f,'all_test_algorithm_costs_corrupted':True,'training_scaling_SBS_neighbors_and_CRC_unchanged':same})
            for pred in fold['test_predictions']:
                i=pred['instance_id'];sbs=pred['sbs'];oracle=min(ALGOS,key=lambda a:(matrix[p][i][a]['cost'],a))
                strategies={'train_sbs':(sbs,False),'knn':route(pred),'crc_alpha_0.10':route(pred,fold['gates']['0.1']),'crc_alpha_0.20_sensitivity':route(pred,fold['gates']['0.2']),'estimated_mean_oracle':(oracle,oracle!=sbs)}
                for name,(a,accepted) in strategies.items():
                    val=matrix[p][i][a];baseline=matrix[p][i][sbs]['cost'];vbs=matrix[p][i][oracle]['cost']
                    rows.append({'profile':p,'fold':f,'instance_id':i,'family':byid[i]['family'],'n_customers':byid[i]['n_customers'],
                        'strategy':name,'selected_algorithm':a,'sbs_algorithm':sbs,'oracle_algorithm':oracle,'accepted_alternative':accepted,
                        'route_length':val['cost'],'normalized_cost':val['normalized_cost'],'improvement_over_sbs_pct':100*(baseline-val['cost'])/baseline,
                        'regret_vs_mean_oracle_pct':100*(val['cost']/vbs-1),'harm_gt_1pct':val['cost']>1.01*baseline,
                        'historical_solver_cpu_seconds':val['solver_cpu_seconds'],'historical_child_cpu_seconds':val['child_cpu_seconds'],
                        'historical_call_wall_seconds':val['wall_seconds'],'historical_checker_cpu_seconds':val['checker_cpu_seconds'],
                        'feature_cpu_seconds':feature_times[i]['cpu_seconds'] if name not in ['train_sbs','estimated_mean_oracle'] else 0,
                        'inference_cpu_seconds':pred['inference_time']['cpu_seconds'] if name not in ['train_sbs','estimated_mean_oracle'] else 0,
                        'score':pred['score']})
    summaries=[]
    for p in PROFILES:
        for name in ['train_sbs','knn','crc_alpha_0.10','crc_alpha_0.20_sensitivity','estimated_mean_oracle']:
            sub=[r for r in rows if r['profile']==p and r['strategy']==name];accepted=[r for r in sub if r['accepted_alternative']]
            summaries.append({'profile':p,'strategy':name,'n_instances':len(sub),'mean_improvement_over_sbs_pct':st.mean(r['improvement_over_sbs_pct'] for r in sub),
                'mean_regret_vs_mean_oracle_pct':st.mean(r['regret_vs_mean_oracle_pct'] for r in sub),'mean_normalized_cost':st.mean(r['normalized_cost'] for r in sub),
                'mean_route_length':st.mean(r['route_length'] for r in sub),'alternative_coverage':len(accepted)/len(sub),'alternative_count':len(accepted),
                'harmful_instance_count':sum(r['harm_gt_1pct'] for r in sub),'all_instance_harm_frequency':st.mean(r['harm_gt_1pct'] for r in sub),
                'conditional_accepted_harm_frequency':st.mean(r['harm_gt_1pct'] for r in accepted) if accepted else None,
                'selected_algorithms':dict(collections.Counter(r['selected_algorithm'] for r in sub)),
                'mean_historical_solver_cpu_seconds':st.mean(r['historical_solver_cpu_seconds'] for r in sub),
                'mean_historical_child_cpu_seconds':st.mean(r['historical_child_cpu_seconds'] for r in sub),
                'mean_historical_call_wall_seconds':st.mean(r['historical_call_wall_seconds'] for r in sub),
                'mean_new_feature_and_inference_cpu_seconds':st.mean(r['feature_cpu_seconds']+r['inference_cpu_seconds'] for r in sub)})
    oracle_audit=[]
    for p in PROFILES:
        data=matrix[p];global_sbs=min(ALGOS,key=lambda a:(st.mean(x[a]['normalized_cost'] for x in data.values()),a))
        oracle_audit.append({'profile':p,'posthoc_full_panel_sbs':global_sbs,'best_mean_algo_counts':dict(collections.Counter(min(ALGOS,key=lambda a:x[a]['cost']) for x in data.values())),
            'global_SBS_excess_over_estimated_mean_oracle_pct':100*st.mean(x[global_sbs]['cost']/min(v['cost'] for v in x.values())-1 for x in data.values()),
            'mean_headroom_denominator_SBS_pct':100*st.mean(1-min(v['cost'] for v in x.values())/x[global_sbs]['cost'] for x in data.values()),
            'seed_hindsight_oracle_length':st.mean(min(r['recomputed_cost'] for r in raw if r['instance_id']==i and r['profile']==p) for i in data),
            'estimated_mean_oracle_length':st.mean(min(v['cost'] for v in x.values()) for x in data.values()),
            'scope':'both are descriptive post-hoc oracles; seed oracle selects algorithm AND lucky seed and is not deployable single-run routing'})
    summary={'scope':plan['status'],'primary':'CRC alpha .10; alpha .20 is predeclared sensitivity only','n_instances':24,'historical_real_solver_runs':432,'new_solver_runs':0,'new_api_calls':0,
       'strategies':summaries,'oracle_audit':oracle_audit,'primary_crc_all_safe_fallback':all(f['gates']['0.1']['safe_endpoint'] for f in folds),
       'leakage_tests':{'count':len(leakage),'passed':all(x['training_scaling_SBS_neighbors_and_CRC_unchanged'] for x in leakage)},
       'cost_ledger':{'historical_source_solver_cpu_seconds':sum(r['solver_cpu_seconds'] for r in raw),'historical_source_child_cpu_seconds':sum(r['child_total_cpu_seconds'] for r in raw),
       'historical_source_supervisor_cpu_seconds':sum(r['supervisor_cpu_seconds'] for r in raw),'historical_source_total_call_wall_seconds':sum(r['total_wall_seconds'] for r in raw),
       'new_feature_extraction_cpu_seconds_once_per_instance':sum(t['cpu_seconds'] for t in feature_times.values()),
       'new_feature_extraction_wall_seconds_once_per_instance':sum(t['wall_seconds'] for t in feature_times.values()),
       'new_training_cpu_seconds_8_models':sum(f['training_time']['cpu_seconds'] for f in folds),'new_training_wall_seconds_8_models':sum(f['training_time']['wall_seconds'] for f in folds),
       'new_calibration_prediction_and_gating_cpu_seconds':sum(f['calibration_time']['cpu_seconds'] for f in folds),'new_test_prediction_cpu_seconds':sum(p['inference_time']['cpu_seconds'] for f in folds for p in f['test_predictions']),
       'new_preflight_cpu_seconds':preflight['cpu_seconds'],'new_analysis_cpu_seconds_before_write':time.process_time()-c0,'new_analysis_wall_seconds_before_write':time.perf_counter()-w0,
       'caveat':'new and historical timings measured on potentially different workloads/machines; no exact combined end-to-end runtime claim; offline original data collection charged in full; no cost assigned to VBS as deployable algorithm'},
       'environment':{'python':sys.version,'platform':platform.platform()}}
    for name,value in [('summary.json',summary),('folds.json',folds),('features.json',{'features':feat,'timings':feature_times}),('performance_matrix.json',matrix),('leakage_tests.json',leakage)]:
        (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False))
    with (out/'out_of_fold.csv').open('w') as h:
        w=csv.DictWriter(h,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    (out/'decision_fingerprint.json').write_text(json.dumps({'sha256':hashlib.sha256(json.dumps([decision_signature(f) for f in folds],sort_keys=True,separators=(',',':')).encode()).hexdigest()},indent=2))
    return summary
