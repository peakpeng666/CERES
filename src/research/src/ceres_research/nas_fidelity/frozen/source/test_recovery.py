"""Fault injection on synthetic tensors in temp directories, never CIFAR results."""
import json,tempfile,unittest
from pathlib import Path
import torch
import run_fidelity as runner
from core import setup

class Recovery(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.original_root=runner.ROOT
        self.root=Path(self.tmp.name);runner.ROOT=self.root
        for name in ['raw','checkpoints','artifacts']:(self.root/name).mkdir()
        self.a={'depth':2,'width':8,'kernel':3};self.seed=870007;self.name='discovery_870007_d2_w8_k3'
        x=torch.linspace(0,1,256*3*32*32).reshape(256,3,32,32);y=torch.arange(256)%10
        self.data={'train':(x,y),'validation':(x[:20],y[:20])}
        self.cfg={'lr':.003,'weight_decay':.0001,'batch_size':128,'low_epochs':1,'high_epochs':2,'split_sha256':'synthetic-test-only'}
    def tearDown(self):runner.ROOT=self.original_root;self.tmp.cleanup()
    def execute(self,resume=False):runner.run_one(self.a,self.seed,'discovery',self.data,self.cfg,'synthetic-freeze',resume)
    def test_missing_first_epoch_journal_recovers(self):
        original=runner.append;triggered=False
        def faulty(path,row):
            nonlocal triggered
            if Path(path).name=='epochs.jsonl' and not triggered:
                triggered=True;raise RuntimeError('injected after atomic checkpoint before first journal row')
            original(path,row)
        runner.append=faulty
        try:
            with self.assertRaises(RuntimeError):self.execute()
        finally:runner.append=original
        self.assertFalse((self.root/'raw/epochs.jsonl').exists());self.execute(True)
        result=json.loads((self.root/'raw'/f'{self.name}.json').read_text())
        self.assertEqual(len(result['epochs']),2);self.assertFalse(result['timing_complete'])
        self.assertEqual([json.loads(x)['epoch'] for x in (self.root/'raw/epochs.jsonl').read_text().splitlines()],[1,2])
    def test_no_checkpoint_restart_is_not_complete_timing(self):
        runner.append(self.root/'raw/attempts.jsonl',{'run_id':self.name,'status':'started'})
        self.execute(True)
        result=json.loads((self.root/'raw'/f'{self.name}.json').read_text())
        self.assertFalse(result['timing_complete']);self.assertEqual(result['resumed_attempt_count'],1)
    def test_corrupted_same_length_journal_rejected(self):
        original=runner.append;triggered=False
        def faulty(path,row):
            nonlocal triggered
            original(path,row)
            if Path(path).name=='epochs.jsonl' and not triggered:
                triggered=True;raise RuntimeError('injected after journal write')
        runner.append=faulty
        try:
            with self.assertRaises(RuntimeError):self.execute()
        finally:runner.append=original
        p=self.root/'raw/epochs.jsonl';row=json.loads(p.read_text());row['online_train_correct']+=1;p.write_text(json.dumps(row)+'\n')
        with self.assertRaises(ValueError):self.execute(True)

if __name__=='__main__':setup();unittest.main()
