#!/usr/bin/env python3
"""Finite archive selection replay. Standard library only; no candidate execution.

Usage: python replay.py [--output results]
All paths are relative to this file. Inputs are verified against FREEZE.json.
Only JSON evidence and candidate strings are read. ast.parse inspects syntax,
never compiling to executable bytecode or importing a candidate.
"""
import argparse, ast, csv, hashlib, itertools, json, math, pathlib
from collections import defaultdict
from fractions import Fraction

ROOT = pathlib.Path(__file__).resolve().parent
HORIZON = 5

def digest(x):
    return hashlib.sha256(x).hexdigest()

def read(p):
    return json.loads((ROOT/p).read_text())

def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)+'\n')

def tie(c):
    return digest(('selection-replay-v1|'+c['id']).encode()), c['id']

def ranked(cs):
    return sorted(cs, key=lambda c:(-c['fitness'], tie(c)))

def verify_freeze():
    f=read('FREEZE.json')
    for p,h in f['file_sha256'].items():
        assert digest((ROOT/p).read_bytes())==h, ('freeze mismatch',p)
    m=read('INPUT_MANIFEST.json')
    for x in m['files']:
        assert digest((ROOT/x['copy_path']).read_bytes())==x['sha256'], x
    return digest((ROOT/'FREEZE.json').read_bytes())

def load_archive():
    ctrl=read('raw/controls/RESULTS.json')
    assert ctrl['state']=='completed'
    bf=sorted([x for x in ctrl['rows'] if x['policy']=='BF' and x['split']=='historical_design5000'],key=lambda x:x['instance_index'])
    assert [x['instance_index'] for x in bf]==list(range(5))
    assert all(x['state']=='completed' for x in bf)
    panel=[(x['instance_index'],x['input_sha256'],x['L1']) for x in bf]
    bf_bins=tuple(x['used_bins'] for x in bf)
    bf_trace=tuple(x['assignment_sha256'] for x in bf)
    bf_fit=sum((Fraction(x['L1'],x['used_bins']) for x in bf),Fraction())/5
    terminal=read('raw/phase0/terminal_report.json')
    assert terminal['status']=='phase0_complete' and terminal['verified_observations']==4
    rows=read('raw/phase0/rows.json')
    bundles={x['slot_id']:x for x in (json.loads(p.read_text()) for p in (ROOT/'raw/phase0/bundles').glob('*.json'))}
    phase_registry={x['slot_id']:x for x in read('raw/phase0/registry.json')['slots']}
    follow_registry={x['slot_id']:x for x in read('raw/followon/REGISTRY.json')['slots']}
    follow=read('raw/followon/RESULTS.json')
    assert follow['state']=='completed' and follow['holdout_reads']==0 and len(follow['rows'])==25
    candidates=[]
    def add(batch, id_, candidate, score_rows, claim, registry):
        code=candidate['code']; codehash=digest(code.encode())
        assert codehash==candidate['code_sha256']==registry['source_sha256']
        assert registry['claim_id']==claim['claim_id']
        ss=sorted(score_rows,key=lambda x:x['instance_index'])
        assert [(x['instance_index'],x['input_sha256'],x['L1']) for x in ss]==panel
        assert all(x['outcome']=='ok' for x in ss)
        assert all(x['source_sha256']==codehash for x in ss)
        bins=tuple(x['used_bins'] for x in ss)
        traces=tuple(x['assignment_sha256'] for x in ss)
        assert all(isinstance(b,int) and b>0 for b in bins)
        assert all(len(h)==64 for h in traces)
        fitness=sum((Fraction(x['L1'],x['used_bins']) for x in ss),Fraction())/5
        candidates.append({'id':id_, 'batch':batch, 'code_sha256':codehash,
            'ast_sha256':digest(ast.dump(ast.parse(code),include_attributes=False).encode()),
            'source_characters':len(code),'source_bytes':len(code.encode()),'source':code,
            'bins':bins,'trace':traces,'fitness':fitness,'bf_class':traces==bf_trace,
            'worse_than_bf':fitness<bf_fit,'strictly_more_bins_on_all_inputs':all(x>y for x,y in zip(bins,bf_bins)),
            'relative_extra_bins_mean':sum((Fraction(x-y,y) for x,y in zip(bins,bf_bins)),Fraction())/5,
            'archived_rows':ss,'claim_id':claim['claim_id']})
    for r in rows:
        if not r['candidate']:continue
        b=bundles[r['slot_id']]
        assert b['source_sha256']==r['candidate']['code_sha256']
        ss=[]
        for x in b['scores']:
            e=x['evidence']
            assert e['assignment_complete'] and e['checker_status']=='passed'
            assert e['registry_binding']['claim_id']==r['claim']['claim_id']
            assert e['registry_binding']['slot_id']==r['slot_id']
            ss.append({'instance_index':x['instance_index'],'input_sha256':x['input_sha256'],'L1':x['L1'],
                'used_bins':x['used_bins'],'assignment_sha256':e['assignment_sha256'], 'source_sha256':x['code_sha256'],
                'outcome':x['outcome'],'evidence_path':'raw/phase0/bundles/'+r['claim']['claim_id']+'.json'})
        add('phase0',r['slot_id'],r['candidate'],ss,r['claim'],phase_registry[r['slot_id']])
    for r in read('raw/followon/CANDIDATES.json'):
        ss=[x for x in follow['rows'] if x['slot_id']==r['slot_id']]
        assert all(x['feasible'] and x['state']=='completed' for x in ss)
        add('followon',r['slot_id'],r['candidate'],ss,r['claim'],follow_registry[r['slot_id']])
    assert len(candidates)==9
    assert sum(len(c['archived_rows']) for c in candidates)==45
    # Identity duplicates are permitted only if all recorded panel behavior agrees.
    seen={}
    for c in candidates:
        k=c['code_sha256']
        if k in seen: assert c['bins']==seen[k]['bins'] and c['trace']==seen[k]['trace']
        seen[k]=c
    return candidates, {'panel':panel,'bf_bins':bf_bins,'bf_trace':bf_trace,'bf_fitness':str(bf_fit),
        'phase0_generation_slots':len(rows),'phase0_eligible_slots':4,
        'followon_original_slots':len(follow['groups']),'followon_eligible_slots':5,
        'followon_excluded':[x for x in follow['groups'] if x['status']!='complete_feasible'],
        'source_hashes_verified':True,'common_panel_verified':True,'archived_trajectory_hashes_only':True}

def dedup(cs,key):
    d={}
    for c in sorted(cs,key=tie):d.setdefault(c[key],c)
    return list(d.values())

def softmax(xs,tau):
    assert tau>0
    mx=max(xs);v=[math.exp((x-mx)/tau) for x in xs];s=math.fsum(v)
    return [x/s for x in v]

def cluster_distribution(cs,mode,within,tau=None):
    groups=defaultdict(list)
    for c in sorted(cs,key=tie):groups[c['bins']].append(c)
    items=sorted(groups.items(), key=lambda kv:(-kv[1][0]['fitness'],kv[0]))
    qualities=[v[0]['fitness'] for k,v in items]
    distinct=sorted(set(qualities),reverse=True)
    if mode=='rank':
        cw=[1/(distinct.index(q)+1) for q in qualities];s=math.fsum(cw);cw=[x/s for x in cw]
    elif mode=='softmax':cw=softmax([float(x) for x in qualities],tau)
    else:cw=[1/len(items)]*len(items)
    out={}
    for (_,members),mass in zip(items,cw):
        assert len({c['fitness'] for c in members})==1
        if within=='length':
            ls=[c['source_characters'] for c in members]
            pw=softmax([-(x-min(ls))/(max(ls)+1e-6) for x in ls],1)
        else:pw=[1/len(members)]*len(members)
        for c,p in zip(members,pw):out[c['id']]=mass*p
    return out

def distributions(cs):
    unique=dedup(cs,'code_sha256'); result=[]; sensitivity=[]
    for kind,pool in [('code',unique),('scalar',dedup(unique,'fitness'))]:
        for k in [3,6]:
            order=ranked(pool);chosen=order[:k]
            result.append({'method':f'{kind}_topk_{k}','family':kind+'_topk','k':k,'tau':None,
                'distribution':{c['id']:1/len(chosen) for c in chosen}})
            # Exhaust all deterministic boundary-tie subsets; diagnostic only.
            target=min(k,len(order));cut=order[target-1]['fitness']
            above=[c for c in order if c['fitness']>cut];ties=[c for c in order if c['fitness']==cut]
            choices=list(itertools.combinations(ties,target-len(above)))
            sensitivity.append({'method':f'{kind}_topk_{k}','boundary_combinations':len(choices),
                'selected_sets':[[c['id'] for c in above]+[c['id'] for c in combo] for combo in choices]})
    for name,mode,within,tau,archive in [
        ('rank_length','rank','length',None,unique),('softmax_length_tau0.1','softmax','length',0.1,unique),
        ('uniform_uniform','uniform','uniform',None,unique),('rank_uniform','rank','uniform',None,unique),
        ('softmax_uniform_tau0.1','softmax','uniform',0.1,unique),
        *[(f'softmax_length_tau{t}','softmax','length',t,unique) for t in [.01,.03,.3,1.0]],
        ('raw_slot_softmax_length_tau0.1','softmax','length',.1,cs)]:
        result.append({'method':name,'family':'full_archive_'+mode,'k':None,'tau':tau,
            'distribution':cluster_distribution(archive,mode,within,tau)})
    return result,sensitivity

def masses(dist,lookup,key):
    out=defaultdict(float)
    for id_,p in dist.items():out[lookup[id_][key]]+=p
    return list(out.values())

def summarize(dist,lookup):
    assert abs(math.fsum(dist.values())-1)<1e-12 and all(p>0 for p in dist.values())
    r={'support_slots':len(dist)}
    for label,key in [('source','code_sha256'),('ast','ast_sha256'),('scalar','fitness'),('bin_signature','bins'),('trace_signature','trace')]:
        ps=masses(dist,lookup,key)
        r['support_'+label]=len(ps)
        r['effective_'+label]=1/math.fsum(p*p for p in ps)
        r['expected_distinct_'+label+'_5']=math.fsum(1-(1-p)**HORIZON for p in ps)
    r['expected_distinct_slot_5']=math.fsum(1-(1-p)**HORIZON for p in dist.values())
    for flag,lab in [('bf_class','bf'),('worse_than_bf','worse'),('strictly_more_bins_on_all_inputs','strictly_worse_all_inputs')]:
        p=math.fsum(p for id_,p in dist.items() if lookup[id_][flag]);r['p_'+lab]=p
    r['p_at_least_one_worse_5']=1-(1-r['p_worse'])**HORIZON
    r['expected_parent_fitness']=math.fsum(p*float(lookup[id_]['fitness']) for id_,p in dist.items())
    r['expected_relative_extra_bins']=math.fsum(p*float(lookup[id_]['relative_extra_bins_mean']) for id_,p in dist.items())
    r['expected_source_characters']=math.fsum(p*lookup[id_]['source_characters'] for id_,p in dist.items())
    return r

def exhaustive_verify(dist,lookup,analytic):
    # Independent direct enumeration, not the occupancy formula used above.
    ids=list(dist);ps=[dist[x] for x in ids];acc=defaultdict(list)
    for combo in itertools.product(range(len(ids)),repeat=HORIZON):
        w=math.prod(ps[i] for i in combo);picked=[lookup[ids[i]] for i in combo]
        acc['mass'].append(w)
        for label,key in [('source','code_sha256'),('bin_signature','bins'),('trace_signature','trace')]:
            acc['expected_distinct_'+label+'_5'].append(w*len({c[key] for c in picked}))
        acc['expected_distinct_slot_5'].append(w*len(set(combo)))
        acc['p_at_least_one_worse_5'].append(w*any(c['worse_than_bf'] for c in picked))
    exact={k:math.fsum(v) for k,v in acc.items()}
    errors={k:abs(v-(1 if k=='mass' else analytic[k])) for k,v in exact.items()}
    assert max(errors.values())<1e-10,errors
    return {'ordered_outcomes':len(ids)**HORIZON,'max_absolute_error':max(errors.values()),'passed':True,'enumerated_metrics':exact}

def candidate_export(c):
    return {k:(str(v) if isinstance(v,Fraction) else v) for k,v in c.items()}

def run(out):
    freeze_hash=verify_freeze();cs,evidence=load_archive();lookup={c['id']:c for c in cs}
    report={'schema':'selection-replay-results-v1','freeze_sha256':freeze_hash,'new_model_calls':0,'new_candidate_executions':0,
        'evidence':evidence,'candidates':[candidate_export(c) for c in cs],'populations':{},'results':[],'tie_sensitivity':[],'verification':[]}
    for name,pop in [('phase0',[c for c in cs if c['batch']=='phase0']),('followon',[c for c in cs if c['batch']=='followon']),('pooled_posthoc',cs)]:
        report['populations'][name]={'slots':len(pop),**{k:len({c[f] for c in pop}) for k,f in [('source','code_sha256'),('ast','ast_sha256'),('scalar','fitness'),('bin_signature','bins'),('trace_signature','trace')]},'bf_slots':sum(c['bf_class'] for c in pop),'worse_slots':sum(c['worse_than_bf'] for c in pop)}
        methods,ties=distributions(pop)
        for d in methods:
            summary=summarize(d['distribution'],lookup)
            report['results'].append({'population':name,**d,**summary})
            report['verification'].append({'population':name,'method':d['method'],**exhaustive_verify(d['distribution'],lookup,summary)})
        for t in ties:
            rs=[summarize({x:1/len(ids) for x in ids},lookup) for ids in t['selected_sets']]
            report['tie_sensitivity'].append({'population':name,**t,'ranges':{k:[min(r[k] for r in rs),max(r[k] for r in rs)] for k in rs[0]}})
    out.mkdir(exist_ok=True,parents=True)
    write_json(out/'RESULTS.json',report)
    scalarrows=[{k:v for k,v in r.items() if k!='distribution'} for r in report['results']]
    with (out/'selection_metrics.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(scalarrows[0]));w.writeheader();w.writerows(scalarrows)
    with (out/'parent_probabilities.csv').open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['population','method','candidate_id','probability','bf_class','bins'])
        for r in report['results']:
            for id_,p in r['distribution'].items():w.writerow([r['population'],r['method'],id_,format(p,'.17g'),lookup[id_]['bf_class'],str(lookup[id_]['bins'])])
    write_json(out/'EXHAUSTIVE_VALIDATION.json',{'all_passed':all(x['passed'] for x in report['verification']),'comparisons':report['verification'],'no_statistical_significance_claim':True})
    print(json.dumps({'populations':report['populations'],'method_population_combinations':len(report['results']),'enumerated_outcomes':sum(x['ordered_outcomes'] for x in report['verification']),'max_error':max(x['max_absolute_error'] for x in report['verification'])},indent=2))
