import unittest, copy, math
from ceres_research.cvrp_selection.experiment import crc, route, fit, predict, features, aggregate, fit_calibrate, ALGOS
class MechanismTests(unittest.TestCase):
    def record(self,score,harm=True):return {'score':score,'harm':harm,'chosen':'a','sbs':'b'}
    def test_sample_floor_forces_safety(self):
        g=crc([self.record(.1,False) for _ in range(6)],.1)
        self.assertTrue(g['safe_endpoint']);self.assertAlmostEqual(g['finite_sample_floor'],1/7)
    def test_nine_safe_cal_points_allow_finite_threshold(self):
        g=crc([self.record(.1,False) for _ in range(9)],.1)
        self.assertFalse(g['safe_endpoint']);self.assertEqual(g['threshold'],0)
    def test_small_sample_sensitive_alpha_is_distinct(self):
        g=crc([self.record(.1,False) for _ in range(6)],.2)
        self.assertEqual(g['threshold'],0)
    def test_strict_threshold_and_monotonicity(self):
        cal=[self.record(.2),self.record(.4)]+[self.record(.1,False) for _ in range(4)]
        g=crc(cal,.2);self.assertEqual(g['threshold'],.4)
        self.assertFalse(route(self.record(.4),g)[1]);self.assertTrue(route(self.record(.5),g)[1])
        losses=[sum(x['harm'] and x['score']>t for x in cal) for t in [0,.1,.2,.3,.4,1]]
        self.assertEqual(losses,sorted(losses,reverse=True))
    def test_all_fallback_never_has_harm(self):
        g={'safe_endpoint':True,'threshold':None}
        self.assertEqual(route(self.record(10000),g),('b',False))
    def test_no_misnamed_probability(self):
        # Ranking margins are only scores and can never certify class confidence.
        self.assertNotIn('probability',crc([self.record(.5,False)]*6,.2))
    def test_empty_calibration_safe_fallback(self):self.assertTrue(crc([],.1)['safe_endpoint'])
    def test_inference_does_not_accept_any_performance_labels(self):
        feat={str(i):{'values':[i,1]} for i in range(4)}
        mat={str(i):{a:{'normalized_cost':1+i*.1+k} for k,a in enumerate(ALGOS)} for i in range(3)}
        model=fit(['0','1','2'],feat,mat);result=predict(model,'3',feat)
        self.assertEqual(result['chosen'],ALGOS[0]);self.assertNotIn('3',model['targets'])

    def test_invalid_calibration_parameters_never_open_the_gate(self):
        for alpha in (-.1,0,1,2,True,float('nan'),float('inf')):
            with self.subTest(alpha=alpha),self.assertRaises(ValueError):crc([self.record(.1)],alpha)
        for score in (float('nan'),float('inf'),-.1,True,'0.1'):
            with self.subTest(score=score),self.assertRaises(ValueError):crc([self.record(score)],.2)
            with self.assertRaises(ValueError):route(self.record(score),{'safe_endpoint':True,'threshold':None})
        with self.assertRaises(ValueError):crc([self.record(.1,'false')],.2)

    def test_degenerate_geometry_produces_finite_features(self):
        for coords in ([[0,0],[1,1]],[[0,0],[0,0],[0,0]]):
            record=features({'coordinates':coords,'demands':[0]+[1]*(len(coords)-1),'capacity':2})
            self.assertEqual(len(record['values']),6)
            self.assertTrue(all(math.isfinite(value) for value in record['values']))
        self.assertEqual(record['star_upper_bound'],0)

    def test_zero_cost_training_has_zero_ranking_margin(self):
        feat={str(i):{'values':[i,1]} for i in range(4)}
        matrix={str(i):{a:{'normalized_cost':0} for a in ALGOS} for i in range(3)}
        model=fit(['0','1','2'],feat,matrix)
        self.assertEqual(predict(model,'3',feat)['score'],0)
        with self.assertRaises(ValueError):fit(['0','0'],feat,matrix)
        feat['3']['values']=[1]
        with self.assertRaises(ValueError):predict(model,'3',feat)

    def test_related_instance_metadata_cannot_cross_partitions(self):
        manifest=[{'instance_id':str(i),'replicate':i,'base_id':str(i)} for i in range(4)]
        feat={str(i):{'values':[i,1]} for i in range(4)}
        matrix={str(i):{a:{'cost':i+1,'normalized_cost':i+1} for a in ALGOS} for i in range(4)}
        result=fit_calibrate('cpu025',0,manifest,feat,matrix)
        self.assertEqual(result['test_ids'],['0'])
        manifest[1]['base_id']=manifest[0]['base_id']
        with self.assertRaisesRegex(ValueError,'related instances'):fit_calibrate('cpu025',0,manifest,feat,matrix)

    def test_aggregation_preserves_failure_and_duplicate_boundaries(self):
        row={'instance_id':'x','profile':'cpu025','algorithm':ALGOS[0],'solver_seed':0,
             'status':'returned','accepted':True,'budget_conformant':True,'scoreable':True,
             'checker_errors':[],'recomputed_cost':0,'solver_cpu_seconds':0,
             'child_total_cpu_seconds':0,'total_wall_seconds':0,'checker_cpu_seconds':0,'supervisor_cpu_seconds':0}
        inputs={'x':{'star_upper_bound':0}}
        self.assertEqual(aggregate([row],inputs)['cpu025']['x'][ALGOS[0]]['normalized_cost'],0)
        with self.assertRaises(ValueError):aggregate([row,row],inputs)
        with self.assertRaises(ValueError):aggregate([{**row,'accepted':False}],inputs)
        with self.assertRaises(ValueError):aggregate([{**row,'recomputed_cost':1}],inputs)
if __name__=='__main__':unittest.main(verbosity=2)
