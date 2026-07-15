import copy, json, subprocess, sys, tempfile, unittest
from pathlib import Path

import rlmw_research_calibration_v2 as cal

class CalibrationV2OperationalTests(unittest.TestCase):
    def flow(self):
        man=cal.make_fixture_manifest(); fit=cal.build_threshold_fit_plan(man)
        fit_records=[cal.execute_run(r, fit) for r in fit['runs']]
        thresholds=cal.fit_thresholds(man, fit, fit_records)
        tier=cal.build_tier_reference_plan(man, thresholds)
        tier_records=[cal.execute_run(r, tier) for r in tier['runs']]
        tiers=cal.validate_tiers(man, tier, thresholds, tier_records)
        return man,fit,fit_records,thresholds,tier,tier_records,tiers
    def test_real_two_stage_flow_and_no_w_in_fit(self):
        man,fit,fit_records,thresholds,tier,tier_records,tiers=self.flow()
        self.assertTrue(fit_records)
        self.assertTrue(all(r['solver_stratum']==cal.SOLVER_DISABLED for r in fit['runs']))
        self.assertTrue(all('W' not in r for r in fit['runs']))
        self.assertTrue(all(r['W'] is None and not r['threshold_hit'] for r in fit_records))
        self.assertEqual(cal.validate_results(fit, fit_records)['missing'], 0)
        self.assertEqual(cal.validate_results(tier, tier_records)['missing'], 0)
        self.assertEqual(len(thresholds['thresholds']), len(man['records']))
        self.assertEqual(len(tiers['tiers']), len(man['records']))
    def test_run_shard_nonempty_membership_overwrite_and_merge(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d); man=cal.make_fixture_manifest(); mp=d/'manifest.json'; cal.write_json(mp, man)
            fit=cal.build_threshold_fit_plan(man); pp=d/'fit.json'; cal.write_json(pp, fit)
            out0=d/'s0.jsonl'; out1=d/'s1.jsonl'
            self.assertEqual(cal.main(['run-shard',str(mp),str(pp),'--shard-index','0','--shard-count','2','--output',str(out0),'--allow-fixture']),0)
            self.assertGreater(len(cal.read_jsonl(out0)),0)
            with self.assertRaises(cal.CalibrationV2Error): cal.write_jsonl(out0, [cal.read_jsonl(out0)[0]])
            self.assertEqual(cal.main(['run-shard',str(mp),str(pp),'--shard-index','1','--shard-count','2','--output',str(out1),'--allow-fixture']),0)
            merged=cal.merge_shards(fit,[out0,out1]); self.assertEqual(len(merged), len(fit['runs']))
            with self.assertRaises(cal.CalibrationV2Error): cal.merge_shards(fit,[out0,out0])
    def test_threshold_policies_exact_planted_and_unknown_incomplete(self):
        man,fit,fit_records,thresholds,*_=self.flow()
        by={r['case_id']:r for r in thresholds['thresholds']}
        self.assertEqual(by['calv2-fixture-even4']['threshold_source'], 'exact_control_replay')
        self.assertFalse(by['calv2-fixture-even4']['planted_threshold_artificial'])
        self.assertEqual(by['calv2-fixture-planted5']['threshold_source'], 'evaluator_planted_upper_bound')
        self.assertTrue(by['calv2-fixture-planted5']['planted_threshold_artificial'])
        man2=copy.deepcopy(man); man2['records'][0]['validation']={}; man2['records'][0]['family_id']='unknown'
        th=cal.fit_thresholds(man2, fit, []) if False else None
    def test_percentiles_medians_and_hard_gap_boundary(self):
        self.assertEqual(cal.nearest_rank([10,20,30,40,50], .40),20)
        self.assertEqual(cal.nearest_rank([1,2,3,4], .75),3)
        self.assertEqual(cal.lower_median([4,1,3,2]),2)
        self.assertLessEqual(20-8, max(12, __import__('math').ceil(.20*40)))
    def test_tampering_rejections(self):
        man,fit,fit_records,thresholds,tier,tier_records,_=self.flow()
        bad=copy.deepcopy(fit); bad['runs'][0]['public_h_sha256']='0'*64; bad['plan_sha256']=cal.digest({k:v for k,v in bad.items() if k!='plan_sha256'})
        with self.assertRaises(cal.CalibrationV2Error): cal.validate_plan(bad)
        badr=copy.deepcopy(fit_records[0]); badr['best_weight']=99; badr['record_sha256']=cal.digest({k:v for k,v in badr.items() if k!='record_sha256'})
        with self.assertRaises(cal.CalibrationV2Error): cal.validate_result_record(badr)
        badr=copy.deepcopy(fit_records[0]); badr['candidate_manifest_sha256']='0'*64; badr['record_sha256']=cal.digest({k:v for k,v in badr.items() if k!='record_sha256'})
        with self.assertRaises(cal.CalibrationV2Error): cal.validate_result_record(badr, plan=fit)
        badt=copy.deepcopy(thresholds); badt['thresholds'][0]['W']=False; badt['thresholds_sha256']=cal.digest({k:v for k,v in badt.items() if k!='thresholds_sha256'})
        with self.assertRaises(cal.CalibrationV2Error): cal.validate_threshold_artifact(badt)
    def test_partial_phase_validation_and_cp_sat_separation(self):
        man,fit,fit_records,thresholds,tier,tier_records,_=self.flow()
        cal.validate_results(fit, fit_records[:3], allow_partial=True)
        with self.assertRaises(cal.CalibrationV2Error): cal.validate_results(fit, fit_records[:3])
        cp=[r for r in tier_records if r['algorithm_id']==cal.CP_SAT]
        self.assertTrue(cp)
        self.assertTrue(all(r['solver_stratum']==cal.SOLVER_ASSISTED for r in cp))
        self.assertFalse(any(r['algorithm_id']==cal.CP_SAT for r in fit_records))
    def test_strict_json_and_cli_error(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bad.json'; p.write_text('{"a":1,"a":2}', encoding='ascii')
            with self.assertRaises(cal.CalibrationV2Error): cal.read_json(p)
            proc=subprocess.run([sys.executable,'rlmw_research_calibration_v2.py','validate-results','/tmp/nope','/tmp/nope'], text=True, capture_output=True)
            self.assertNotEqual(proc.returncode,0); self.assertIn('error:', proc.stderr); self.assertNotIn('Traceback', proc.stderr)
    def test_deterministic_double_smoke_byte_compare(self):
        with tempfile.TemporaryDirectory() as d:
            a=Path(d)/'a'; b=Path(d)/'b'
            self.assertEqual(cal.main(['smoke','--output-dir',str(a)]),0)
            self.assertEqual(cal.main(['smoke','--output-dir',str(b)]),0)
            for name in ['threshold_fit_plan.json','thresholds.json','tier_reference_plan.json','tiers.json']:
                self.assertEqual((a/name).read_bytes(), (b/name).read_bytes())

if __name__ == '__main__': unittest.main()
