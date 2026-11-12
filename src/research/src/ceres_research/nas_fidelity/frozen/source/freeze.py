"""Fail closed on data/source mismatch before recording the local execution freeze."""
import json,platform,sys
import numpy as np
import torch
from core import ROOT,TRAIN_FILES,digest,save,stamp

out=ROOT/'artifacts/pre_run_freeze.json'
if out.exists():raise FileExistsError('Freeze already exists')
cfg=json.loads((ROOT/'config.json').read_text());old=ROOT/'evidence/original_p4/artifacts'
manifest=json.loads((old/'data_manifest.json').read_text());split=json.loads((old/'split.json').read_text())
assert digest(old/'split.json')==manifest['split_sha256']==cfg['split_sha256']
labels=[]
for n in TRAIN_FILES:
    assert digest(ROOT/'data'/n)==manifest['extracted_files'][n]['sha256']
    labels.append(np.memmap(ROOT/'data'/n,mode='r',dtype=np.uint8,shape=(10000,3073))[:,0])
labels=np.concatenate(labels)
assert sorted(i for k in ['train','validation','reserved'] for i in split[k])==list(range(50000))
counts={k:np.bincount(labels[split[k]],minlength=10).tolist() for k in ['train','validation','reserved']}
assert counts==manifest['class_counts']
fence=json.loads((ROOT/'evidence/original_p4_fence.json').read_text())
assert all(digest(ROOT.parent/p)==h for p,h in fence['files'].items())
assert not list((ROOT/'data').glob('*test*'))
assert torch.__version__=='2.14.1+cpu' and np.__version__=='2.5.3'
save(ROOT/'artifacts/split_verification.json',{'at_utc':stamp(),'class_counts':counts,'split_sha256':digest(old/'split.json'),
 'all_indices_unique_and_partition_complete':True,'original_p4_files_unchanged':len(fence['files']),
 'subset_index_sha256':{k:__import__('hashlib').sha256(np.asarray(split[k],dtype='<i8').tobytes()).hexdigest() for k in split},
 'official_test_sample_reads':0,'train_files_only':TRAIN_FILES})
paths=[ROOT/'config.json',ROOT/'PROTOCOL_中文.md',ROOT/'requirements.lock.txt',ROOT/'evidence/original_p4_fence.json']
paths+=sorted((ROOT/'source').glob('*.py'))+sorted((ROOT/'audit').glob('*.py'))
paths+=[ROOT/'artifacts'/n for n in ['data_recovery.json','split_verification.json','smoke_timing.json','pre_run_tests.txt','recovery_fault_tests.txt']]
paths+=[old/'split.json',old/'data_manifest.json']
save(out,{'frozen_at_utc':stamp(),'files':{str(p.relative_to(ROOT)):digest(p) for p in paths},
 'authorization':'Parent approved 80 runs / 1600 epochs / original 4000-1000 split / roughly one hour single-thread CPU before scoring',
 'no_new_cifar_training_started':not (ROOT/'raw/attempts.jsonl').exists(),'no_new_experiment_cifar_scores_seen':True,
 'torch':torch.__version__,'numpy':np.__version__,'python':sys.version,'platform':platform.platform(),
 'dataset_verified_against_original_manifest':True,'official_test_used':False})
print(json.dumps({'freeze_sha256':digest(out),'frozen_at_utc':json.loads(out.read_text())['frozen_at_utc'],'files':len(paths)}))
