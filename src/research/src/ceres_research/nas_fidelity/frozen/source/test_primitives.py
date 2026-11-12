"""Synthetic parity tests only: no performance scores and no CIFAR loading."""
import importlib.util,json,unittest
import torch
from core import ROOT,setup,space,TinyCNN,macs,digest
from metrics import kendall,spearman

spec=importlib.util.spec_from_file_location("old_p4",ROOT/"evidence/original_p4/run_experiment.py")
old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)

class Tests(unittest.TestCase):
    def test_space_and_macs(self):
        cfg=json.loads((ROOT/"evidence/original_p4/config.json").read_text())
        self.assertEqual(space(),old.architecture_space(cfg))
        for a in space(): self.assertEqual(macs(a),old.forward_macs(a))
    def test_forward_and_update_parity(self):
        for a in space():
            torch.manual_seed(870001);new=TinyCNN(a)
            torch.manual_seed(870001);before=old.TinyCNN(a)
            x=torch.linspace(0,1,4*3*32*32).reshape(4,3,32,32);y=torch.arange(4)
            self.assertTrue(torch.equal(new(x),before(x)))
            for m in [new,before]:
                opt=torch.optim.Adam(m.parameters(),lr=.003,weight_decay=.0001)
                opt.zero_grad(set_to_none=True);torch.nn.functional.cross_entropy(m(x),y).backward();opt.step()
            for k,v in new.state_dict().items(): self.assertTrue(torch.equal(v,before.state_dict()[k]))
    def test_split_and_old_fence(self):
        split=json.loads((ROOT/"evidence/original_p4/artifacts/split.json").read_text())
        sets=[set(split[k]) for k in ['train','validation','reserved']]
        self.assertEqual([len(s) for s in sets],[4000,1000,45000]);self.assertEqual(len(set.union(*sets)),50000)
        self.assertFalse(sets[0]&sets[1] or sets[0]&sets[2] or sets[1]&sets[2])
        assessed=json.loads((ROOT/"evidence/original_p4/postselection/assessment_indices.json").read_text())
        self.assertEqual(len(set(assessed)),5000);self.assertTrue(set(assessed)<=sets[2])
        fence=json.loads((ROOT/"evidence/original_p4_fence.json").read_text())
        for p,h in fence['files'].items():self.assertEqual(digest(ROOT.parent/p),h)
    def test_seeds_and_costs(self):
        cfg=json.loads((ROOT/'config.json').read_text());self.assertEqual(len(cfg['run_order']),80)
        self.assertFalse(set(cfg['discovery_seeds'])&set(cfg['confirmation_seeds']))
        self.assertEqual(cfg['cost_budget']['epochs'],80*20)
        self.assertEqual(cfg['cost_budget']['train_presentations'],80*20*4000)
        self.assertEqual(len({(r['group'],r['seed'],str(r['architecture'])) for r in cfg['run_order']}),80)
    def test_rank_metrics(self):
        self.assertEqual(kendall([1,2,3],[1,2,3])['tau_b'],1.)
        self.assertEqual(kendall([1,2,3],[3,2,1])['tau_b'],-1.)
        self.assertEqual(spearman([1,1,3],[1,1,3]),1.)
        self.assertIsNone(kendall([1,1,1],[1,2,3])['tau_b'])
        self.assertAlmostEqual(kendall([1,1,3],[1,2,3])['tau_b'],2/(6**.5))

if __name__=='__main__':setup();unittest.main()
