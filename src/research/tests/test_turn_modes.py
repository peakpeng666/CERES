import copy,json,unittest
from pathlib import Path
from ceres_research.turn_modes import checker,solver,oracle,evaluate_candidate
from ceres_research.turn_modes.generate_data import fixtures,grid
class Tests(unittest.TestCase):
    def test_exact_solvers_reject_finite_fleet_limit(self):
        inst=fixtures()[1]
        unrestricted,table=solver.solve(inst,'mode')
        self.assertEqual(unrestricted['status'],'optimal')
        self.assertEqual(oracle.solve(inst)['status'],'optimal')
        for limit in (0,1,2):
            limited=copy.deepcopy(inst);limited['fleet_limit']=limit
            for kind in ('mode','reset'):
                with self.subTest(limit=limit,method=kind), self.assertRaisesRegex(ValueError,'fleet_limit'):
                    solver.solve(limited,kind)
            with self.subTest(limit=limit,method='subset_dp'), self.assertRaisesRegex(ValueError,'fleet_limit'):
                solver.subset_dp(limited,table)
            with self.subTest(limit=limit,method='oracle'), self.assertRaisesRegex(ValueError,'fleet_limit'):
                oracle.solve(limited)

    def test_known_counterexample_and_infeasible(self):
        dead,cycle,free=fixtures()
        for inst,expected in ((dead,None),(cycle,3)):
            m,t=solver.solve(inst,'mode');r,_=solver.solve(inst,'reset');o=oracle.solve(inst)
            self.assertEqual(m['cost'],expected);self.assertEqual(m['cost'],o['cost'])
            self.assertEqual(r['cost'],2);self.assertFalse(checker.check(inst,r)['valid'])
        a,_=solver.solve(free,'mode');b,_=solver.solve(free,'reset');self.assertEqual(a['cost'],b['cost']);self.assertTrue(checker.check(free,b)['valid'])
    def test_mutation_rejections(self):
        inst=fixtures()[1];good,_=solver.solve(inst,'mode');self.assertTrue(checker.check(inst,good)['valid'])
        mutations={}
        v=copy.deepcopy(good);v['routes'][0]['services']=[];mutations['missing']=v
        v=copy.deepcopy(good);v['routes'][0]['services']*=2;mutations['duplicate']=v
        v=copy.deepcopy(good);v['routes'][0]['nodes']=[0,1,0];v['routes'][0]['cost']=2;v['cost']=2;mutations['forbidden_turn']=v
        v=copy.deepcopy(good);v['routes'][0]['nodes'][1]=999;mutations['broken_arc']=v
        v=copy.deepcopy(good);v['routes'][0]['services'][0]['position']=0;mutations['wrong_service_location']=v
        v=copy.deepcopy(good);v['cost']-=1;mutations['forged_total']=v
        v=copy.deepcopy(good);v['routes'][0]['cost']-=1;mutations['forged_route_cost']=v
        for label,value in mutations.items():
            with self.subTest(label=label):self.assertFalse(checker.check(inst,value)['valid'])
        limited=copy.deepcopy(inst);limited['capacity']=0;self.assertIn('route0:capacity',checker.check(limited,good)['errors'])
    def test_raw_oracle_matches_mode_smoke(self):
        for seed in range(8000,8006):
            inst=grid(3,3,5,seed,['uturn','left_cost','left_ban'][seed%3]);a,tab=solver.solve(inst,'mode');b=oracle.solve(inst)
            self.assertEqual(a['cost'],b['cost']);self.assertTrue(checker.check(inst,a)['valid']);self.assertTrue(checker.check(inst,b)['valid'])
            for seq in ([0],[0,1],[2,1,3],[4,3,2,1]):
                d=solver.fixed_sequence(inst,tab,seq);cost,_=solver.brute_modes(tab,seq);self.assertEqual(d['cost'],cost)
    def test_candidate_adapter(self):
        dead,cycle,_=fixtures()
        r=evaluate_candidate.evaluate(cycle,{'service_sequences':[[0]]});self.assertTrue(r['accepted']);self.assertEqual(r['solution']['cost'],3)
        self.assertFalse(evaluate_candidate.evaluate(dead,{'service_sequences':[[0]]})['accepted'])
        self.assertFalse(evaluate_candidate.evaluate(cycle,{'service_sequences':[[0,0]]})['accepted'])
        self.assertFalse(evaluate_candidate.evaluate(cycle,{'service_sequences':[[99]]})['accepted'])
        self.assertFalse(evaluate_candidate.evaluate(cycle,{'service_sequences':[['0']]})['accepted'])
    def test_service_does_not_reset_heading(self):
        # With U-turn prohibited, the only legal return takes the third node.
        inst=fixtures()[1];good,_=solver.solve(inst,'mode');self.assertEqual(good['routes'][0]['nodes'],[0,1,2,0])
    def test_pass_through_does_not_service(self):
        inst=fixtures()[1];inst['customers'].append({'id':1,'node':2,'demand':1});inst['capacity']=2
        route={'status':'optimal','cost':3,'routes':[{'nodes':[0,1,2,0],'cost':3,'services':[{'customer':0,'position':1}]}]}
        self.assertIn('exact_once_service',checker.check(inst,route)['errors'])

    def test_boolean_aliases_and_malformed_witnesses_are_rejected(self):
        inst=fixtures()[1];good,_=solver.solve(inst,'mode')
        variants=[None,[],{'status':'optimal','routes':None,'cost':3}]
        for bad in (None,[],1):
            value=copy.deepcopy(good);value['routes']=[bad];variants.append(value)
        for field,bad in (('nodes',[0,True,2,0]),('nodes',None),('services',None),
                          ('services',[None]),('services',[{'customer':False,'position':1}]),
                          ('services',[{'customer':[],'position':1}]),('cost',3.0)):
            value=copy.deepcopy(good);value['routes'][0][field]=bad;variants.append(value)
        for value in variants:
            with self.subTest(value=value):self.assertFalse(checker.check(inst,value)['valid'])

    def test_graph_contract_rejects_ambiguous_identifiers(self):
        inst=fixtures()[1];good,_=solver.solve(inst,'mode')
        duplicate=copy.deepcopy(inst);duplicate['arcs'].append([0,1,99])
        self.assertFalse(checker.check(duplicate,good)['valid'])
        duplicate=copy.deepcopy(inst);duplicate['customers'].append(copy.deepcopy(inst['customers'][0]))
        self.assertFalse(checker.check(duplicate,good)['valid'])

    def test_colocated_customers_are_separate_service_events(self):
        inst=fixtures()[1]
        inst['customers'].append({'id':1,'node':1,'demand':1});inst['capacity']=2
        mode,_=solver.solve(inst,'mode');raw=oracle.solve(inst)
        self.assertEqual(mode['cost'],3);self.assertEqual(raw['cost'],mode['cost'])
        for solution in (mode,raw):
            self.assertTrue(checker.check(inst,solution)['valid'])
            self.assertEqual(sorted(s['customer'] for r in solution['routes'] for s in r['services']),[0,1])

    def test_unreachable_customers_return_infeasible_candidates(self):
        for incoming in ([],[[98,99,1],[99,98,1]]):
            for first in (True,False):
                inst=copy.deepcopy(fixtures()[1]);inst['arcs'].extend(incoming)
                if first:
                    inst['customers'][0]['node']=99;sequence=[0]
                else:
                    inst['customers'].append({'id':1,'node':99,'demand':1});inst['capacity']=2;sequence=[0,1]
                result=evaluate_candidate.evaluate(inst,{'service_sequences':[sequence]})
                self.assertFalse(result['accepted'])
                self.assertEqual(result['reason'],'no_turn_feasible_realization')

    def test_empty_sequence_and_invalid_candidate_contract(self):
        inst=fixtures()[1];_,table=solver.solve(inst,'mode')
        self.assertEqual(solver.fixed_sequence(inst,table,[])['cost'],0)
        self.assertEqual(solver.brute_modes(table,[]),(0,1))
        for value in (None,[],1):self.assertFalse(evaluate_candidate.evaluate(inst,value)['accepted'])
        with self.assertRaises(ValueError):solver.preprocess(inst,'unknown')
        with self.assertRaises(ValueError):solver.fixed_sequence(inst,table,[True])
if __name__=='__main__':unittest.main(verbosity=2)
