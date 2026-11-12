"""Rebuild reports from immutable per-run evidence; never trains or chooses by test."""
import csv,json,sys,time
import numpy as np
from core import ROOT,space,aid,save,digest,stamp
from metrics import ranks,average_ranks,interval

def main():
    cfg=json.loads((ROOT/'config.json').read_text());selected=json.loads((ROOT/'artifacts/selection_freeze.json').read_text())
    ids=[aid(a) for a in space()];runs=[]
    arrays={}
    for group in ['discovery','confirmation']:
        arr=np.empty((5,8,20),dtype=np.int64)
        for si,s in enumerate(cfg[group+'_seeds']):
            for ai,a in enumerate(space()):
                r=json.loads((ROOT/'raw'/f'{group}_{s}_{aid(a)}.json').read_text());runs.append(r)
                assert len(r['epochs'])==20
                arr[si,ai]=[e['validation']['correct'] for e in r['epochs']]
        arrays[group]=arr
    # Integer sums ensure exact handling of equal means and predefined tie-breaks.
    low=arrays['discovery'][:,:,1].sum(0);high=arrays['discovery'][:,:,-1].sum(0);ref=arrays['confirmation'][:,:,-1].sum(0)
    li=ids.index(selected['low_selected']);hi=ids.index(selected['high_selected']);ri=int(np.argmax(ref))
    assert ids[sorted(range(8),key=lambda j:(-low[j],ids[j]))[0]]==selected['low_selected']
    assert ids[sorted(range(8),key=lambda j:(-high[j],ids[j]))[0]]==selected['high_selected']
    corr={'discovery_2_vs_20':ranks(low,high),'discovery_2_vs_confirmation_20':ranks(low,ref),'discovery_20_vs_confirmation_20':ranks(high,ref)}
    by_seed=[]
    for i,s in enumerate(cfg['discovery_seeds']):
        l=arrays['discovery'][i,:,1];h=arrays['discovery'][i,:,-1]
        a=sorted(range(8),key=lambda j:(-l[j],ids[j]))[0];b=sorted(range(8),key=lambda j:(-h[j],ids[j]))[0]
        by_seed.append({'seed':s,'low_top1':ids[a],'high_top1':ids[b],'rank_correlation':ranks(l,h),
           'low_top1_confirmation_mean_accuracy':float(ref[a]/5000),'low_top1_confirmation_empirical_regret_pp':float((ref.max()-ref[a])/50)})
    rng=np.random.default_rng(cfg['bootstrap']['seed']);boot={k:[] for k in ['low_regret_pp','high_regret_pp','high_minus_low_pp']}
    for k in corr:
        boot[k+'_tau']=[];boot[k+'_rho']=[]
    for _ in range(cfg['bootstrap']['replicates']):
        ia=rng.integers(0,5,5);ib=rng.integers(0,5,5)
        l=arrays['discovery'][ia,:,1].sum(0);h=arrays['discovery'][ia,:,-1].sum(0);r=arrays['confirmation'][ib,:,-1].sum(0)
        for k,(x,y) in zip(corr,[(l,h),(l,r),(h,r)]):
            z=ranks(x,y);boot[k+'_tau'].append(z['kendall']['tau_b']);boot[k+'_rho'].append(z['spearman_rho'])
        boot['low_regret_pp'].append(float((r.max()-r[li])/50));boot['high_regret_pp'].append(float((r.max()-r[hi])/50));boot['high_minus_low_pp'].append(float((r[hi]-r[li])/50))
    rows=[]
    for j,id in enumerate(ids):
        row={'architecture_id':id,'parameters':next(r['parameters'] for r in runs if r['architecture_id']==id),
           'discovery_2_mean_accuracy':float(low[j]/5000),'discovery_20_mean_accuracy':float(high[j]/5000),'confirmation_20_mean_accuracy':float(ref[j]/5000),
           'discovery_2_seed_std':float(arrays['discovery'][:,j,1].std(ddof=1)/1000),'discovery_20_seed_std':float(arrays['discovery'][:,j,-1].std(ddof=1)/1000),'confirmation_20_seed_std':float(arrays['confirmation'][:,j,-1].std(ddof=1)/1000),
           'discovery_2_rank':float(average_ranks(-low)[j]),'discovery_20_rank':float(average_ranks(-high)[j]),'confirmation_20_rank':float(average_ranks(-ref)[j])}
        rows.append(row)
    costs={}
    for group in ['discovery','confirmation']:
        subset=[r for r in runs if r['group']==group];ee=[e for r in subset for e in r['epochs']]
        costs[group]={'runs':len(subset),'epochs':len(ee),'optimizer_steps':sum(e['optimizer_steps'] for e in ee),'train_presentations':sum(e['train_presentations'] for e in ee),
            'validation_presentations':sum(e['validation_presentations'] for e in ee),'train_wall_seconds':sum(e['train_wall_seconds'] for e in ee),'train_cpu_seconds':sum(e['train_cpu_seconds'] for e in ee),
            'validation_wall_seconds':sum(e['validation_wall_seconds'] for e in ee),'validation_cpu_seconds':sum(e['validation_cpu_seconds'] for e in ee),
            'run_wall_seconds':sum(r['total_wall_seconds'] for r in subset),'run_cpu_seconds':sum(r['total_cpu_seconds'] for r in subset),'timing_complete':all(r['timing_complete'] for r in subset),
            'train_forward_conv_linear_macs':sum(r['forward_conv_linear_macs_per_image']*4000*20 for r in subset),
            'validation_forward_conv_linear_macs':sum(r['forward_conv_linear_macs_per_image']*1000*20 for r in subset)}
    logical={}
    for budget in [2,20]:
        sub=[r for r in runs if r['group']=='discovery']
        logical[str(budget)]={'scope':'nested-prefix counterfactual endpoint-only scoring; not extra actual training',
          'epochs':40*budget,'train_presentations':40*budget*4000,'endpoint_validation_presentations':40000,
          'wall_seconds':sum(r['build_wall_seconds']+sum(e['train_wall_seconds'] for e in r['epochs'][:budget])+r['epochs'][budget-1]['validation_wall_seconds'] for r in sub),
          'cpu_seconds':sum(r['build_cpu_seconds']+sum(e['train_cpu_seconds'] for e in r['epochs'][:budget])+r['epochs'][budget-1]['validation_cpu_seconds'] for r in sub),
          'omits':'checkpoint write/logging/Python startup; actual experiment includes every-epoch diagnostics and is costed separately'}
    attempts=[json.loads(line) for line in (ROOT/'raw/attempts.jsonl').read_text().splitlines()]
    accounted=[r for r in attempts if r['status'] in ['failed','completed']]
    attempt_cost={'started_count':sum(r['status']=='started' for r in attempts),'completed_count':sum(r['status']=='completed' for r in attempts),
        'failed_count':sum(r['status']=='failed' for r in attempts),'resumed_events':sum(r['status'] in ['resumed','restarted_without_completed_epoch'] for r in attempts),
        'known_attempt_wall_seconds':sum(r['wall_seconds'] for r in accounted),'known_attempt_cpu_seconds':sum(r['cpu_seconds'] for r in accounted),
        'failed_wall_seconds':sum(r['wall_seconds'] for r in accounted if r['status']=='failed'),'failed_cpu_seconds':sum(r['cpu_seconds'] for r in accounted if r['status']=='failed')}
    attempt_cost['orphan_started_count']=attempt_cost['started_count']-attempt_cost['completed_count']-attempt_cost['failed_count']
    attempt_cost['complete']=attempt_cost['orphan_started_count']==0 and attempt_cost['resumed_events']==0
    attempt_cost['scope']='all logged failed and completed attempts; orphan/partial-epoch cost unknown, never zero; completed-epoch presentation totals are lower bounds if recovery occurred'
    summary={'at_utc':stamp(),'scope':cfg['scope'],'architectures':rows,'correlations':corr,'per_discovery_seed':by_seed,
       'selection':{'low':ids[li],'high':ids[hi],'same_top1':li==hi,'confirmation_empirical_best':ids[ri],
          'low_confirmation_accuracy':float(ref[li]/5000),'high_confirmation_accuracy':float(ref[hi]/5000),'reference_accuracy':float(ref.max()/5000),
          'low_regret_pp':float((ref.max()-ref[li])/50),'high_regret_pp':float((ref.max()-ref[hi])/50),'high_minus_low_pp':float((ref[hi]-ref[li])/50),
          'uniform_random_mean_accuracy':float(ref.mean()/5000),'uniform_random_expected_empirical_regret_pp':float((ref.max()-ref.mean())/50)},
       'confirmation_paired_seed_differences':[{'seed':s,'low_selected_correct':int(arrays['confirmation'][i,li,-1]),'high_selected_correct':int(arrays['confirmation'][i,hi,-1]),
          'high_minus_low_pp':float((arrays['confirmation'][i,hi,-1]-arrays['confirmation'][i,li,-1])/10)} for i,s in enumerate(cfg['confirmation_seeds'])],
       'bootstrap_intervals':{k:interval(v) for k,v in boot.items()},'bootstrap_scope':cfg['bootstrap'],'costs':costs,'logical_selection_cost':logical,
       'attempt_cost_accounting':attempt_cost,
       'official_test_evaluations':0,'selected_validation_n':1000,'independent_training_seeds':10,'independent_data_splits':1,
       'pre_run_freeze_sha256':digest(ROOT/'artifacts/pre_run_freeze.json'),'selection_freeze_sha256':digest(ROOT/'artifacts/selection_freeze.json')}
    save(ROOT/'reports/summary.json',summary)
    with open(ROOT/'reports/architecture_scores.csv','w') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    with open(ROOT/'reports/all_epoch_scores.csv','w') as f:
        fields=['run_id','group','seed','architecture_id','epoch','online_train_loss','online_train_correct','validation_correct','validation_accuracy','validation_loss','train_wall_seconds','train_cpu_seconds','validation_wall_seconds','validation_cpu_seconds']
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for r in runs:
            for e in r['epochs']:
                row={k:e[k] for k in fields if k in e};row.update(validation_correct=e['validation']['correct'],validation_accuracy=e['validation']['accuracy'],validation_loss=e['validation']['loss']);w.writerow(row)
    print(json.dumps(summary['selection'],indent=2));print(json.dumps(corr,indent=2))

if __name__=='__main__':main()
