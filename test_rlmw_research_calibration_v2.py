import copy, json, subprocess, sys, tempfile, unittest
from pathlib import Path

import rlmw_research_calibration_v2 as cal

class CalibrationV2OperationalTests(unittest.TestCase):
    def flow(self):
        man=cal.make_fixture_manifest(); fit=cal.build_threshold_fit_plan(man, profile_id=cal.FIXTURE_PROFILE_ID)
        fit_records=[cal.execute_run(r, fit) for r in fit['runs']]
        thresholds=cal.fit_thresholds(man, fit, fit_records)
        tier=cal.build_tier_reference_plan(man, thresholds, profile_id=cal.FIXTURE_PROFILE_ID, fit_plan=fit, fit_records=fit_records)
        tier_records=[cal.execute_run(r, tier) for r in tier['runs']]
        tiers=cal.validate_tiers(man, tier, thresholds, tier_records, fit_plan=fit, fit_records=fit_records)
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

    def test_production_n15_n16_budgets_and_fixture_profile(self):
        records=[]
        for n in (15,16):
            h=['1'+'0'*(n-1)]
            records.append({'case_id':f'prod-n{n}','H_rows':h,'public_h_sha256':cal.isd_v2.public_h_sha256(h),'n':n,'family_id':'exact-control','validation':{'known_distance':{'distance':1}}})
        man={'manifest_kind':'calibration_fixture_manifest','candidate_manifest_digest':'e'*64,'configuration_digest':cal.corpus_v2.config_digest(),'records':records}
        with self.assertRaises(cal.CalibrationV2Error): cal.build_threshold_fit_plan(man)
        fix=cal.build_threshold_fit_plan(man, profile_id=cal.FIXTURE_PROFILE_ID)
        self.assertEqual(fix['profile_id'], cal.FIXTURE_PROFILE_ID)
        self.assertTrue(all(r['budget']==cal.FIXTURE_BUDGETS[-1] for r in fix['runs']))
    def test_run_shard_nonempty_membership_overwrite_and_merge(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d); man=cal.make_fixture_manifest(); mp=d/'manifest.json'; cal.write_json(mp, man)
            fit=cal.build_threshold_fit_plan(man, profile_id=cal.FIXTURE_PROFILE_ID); pp=d/'fit.json'; cal.write_json(pp, fit)
            out0=d/'s0.jsonl'; out1=d/'s1.jsonl'
            self.assertEqual(cal.main(['run-shard',str(mp),str(pp),'--shard-index','0','--shard-count','2','--output',str(out0),'--allow-fixture']),0)
            self.assertGreater(len(cal.read_jsonl(out0)),0)
            with self.assertRaises(cal.CalibrationV2Error): cal.write_jsonl(out0, [cal.read_jsonl(out0)[0]])
            self.assertEqual(cal.main(['run-shard',str(mp),str(pp),'--shard-index','1','--shard-count','2','--output',str(out1),'--allow-fixture']),0)
            merged=cal.merge_shards(fit,[out0,out1]); self.assertEqual(len(merged), len(fit['runs']))
            with self.assertRaises(cal.CalibrationV2Error): cal.merge_shards(fit,[out0,out0])
    def test_threshold_policies_unknown_complete_and_incomplete_tier_replay(self):
        man, fit, fit_records, thresholds, *_ = self.flow()
        by = {r['case_id']: r for r in thresholds['thresholds']}
        self.assertEqual(by['calv2-fixture-even4']['threshold_source'], 'exact_control_replay')
        self.assertFalse(by['calv2-fixture-even4']['planted_threshold_artificial'])
        self.assertEqual(by['calv2-fixture-planted5']['threshold_source'], 'evaluator_planted_upper_bound')
        self.assertTrue(by['calv2-fixture-planted5']['planted_threshold_artificial'])

        # Remove evaluator controls before planning: this is a genuine unknown
        # case, fitted from complete production-validator result records.
        unknown = cal.make_fixture_manifest()
        unknown['records'][0]['validation'] = {}
        unknown['records'][0]['family_id'] = 'unknown'
        fit = cal.build_threshold_fit_plan(unknown, profile_id=cal.FIXTURE_PROFILE_ID)
        records = [cal.execute_run(run, fit) for run in fit['runs']]
        thresholds = cal.fit_thresholds(unknown, fit, records)
        row = thresholds['thresholds'][0]
        self.assertEqual(row['threshold_source'], 'solver_disabled_nearest_rank_40pct')
        self.assertIsInstance(row['W'], int)

        # Make availability genuinely insufficient while retaining complete,
        # semantically valid evidence for every planned run.
        for record in records[:17]:
            record.update({'termination_reason': 'resource_limit', 'completed_budget': False,
                           'resource_limit': True, 'candidate_evaluations': 0,
                           'objective_evaluations': 0, 'exact_verifications': 0,
                           'valid_codewords_seen': 0, 'threshold_witnesses_seen': 0,
                           'best_candidate_bits': None, 'best_candidate_sha256': None,
                           'best_weight': None, 'witness_verified': False,
                           'threshold_hit': False})
            record['record_sha256'] = cal.digest({k:v for k,v in record.items() if k != 'record_sha256'})
        thresholds = cal.fit_thresholds(unknown, fit, records)
        self.assertIsNone(thresholds['thresholds'][0]['W'])
        tier = cal.build_tier_reference_plan(unknown, thresholds, profile_id=cal.FIXTURE_PROFILE_ID,
                                             fit_plan=fit, fit_records=records)
        tier_records = [cal.execute_run(run, tier) for run in tier['runs']]
        tiers = cal.validate_tiers(unknown, tier, thresholds, tier_records, fit_plan=fit, fit_records=records)
        cal.validate_tier_artifact(tiers, manifest=unknown, plan=tier, thresholds=thresholds,
                                   records=tier_records, fit_plan=fit, fit_records=records)
        incomplete = {row['case_id']: row for row in tiers['tiers']}[unknown['records'][0]['case_id']]
        self.assertEqual(incomplete['reason'], 'missing_threshold')
        self.assertEqual(set(incomplete), {'case_id', 'tier', 'decision', 'reason', 'W', 'hit_rates',
                         'resource_limit_frequencies', 'iqr', 'algorithm_medians',
                         'algorithm_agreement_gap2', 'best_solver_disabled_upper_bound',
                         'certified_lower_bound', 'hard_gap_ok'})

    def test_percentiles_and_hard_gap_workflow_boundary(self):
        self.assertEqual(cal.nearest_rank([10,20,30,40,50], .40),20)
        self.assertEqual(cal.lower_median([4,1,3,2]),2)
        man, fit, fit_records, thresholds, tier, tier_records, _ = self.flow()
        # Alter authoritative evidence rather than repeating policy arithmetic:
        # a far incumbent rejects the hard rule, while the original workflow
        # remains accepted/replayable for its actual fixture cases.
        self.assertTrue(any(row['hard_gap_ok'] for row in cal.validate_tiers(man, tier, thresholds, tier_records, fit_plan=fit, fit_records=fit_records)['tiers'] if row['W'] is not None))
        target = next(r for r in tier_records if r['solver_stratum'] == cal.SOLVER_DISABLED and r['budget'] == tier['budgets'][-1])
        bad = copy.deepcopy(target); bad['best_weight'] = 999; bad['record_sha256'] = cal.digest({k:v for k,v in bad.items() if k != 'record_sha256'})
        with self.assertRaises(cal.CalibrationV2Error): cal.validate_tiers(man, tier, thresholds, [bad if r is target else r for r in tier_records], fit_plan=fit, fit_records=fit_records)

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
    def test_authoritative_replay_rejects_rehashed_semantic_tampering(self):
        man, fit, fit_records, thresholds, tier, tier_records, tiers = self.flow()
        cal.validate_threshold_artifact(thresholds, manifest=man, plan=fit, records=fit_records)
        cal.validate_tier_artifact(tiers, manifest=man, plan=tier, thresholds=thresholds,
                                   records=tier_records, fit_plan=fit, fit_records=fit_records)
        bad = copy.deepcopy(thresholds); bad['thresholds'][0]['W'] = 99
        bad['thresholds_sha256'] = cal.digest({k:v for k,v in bad.items() if k != 'thresholds_sha256'})
        with self.assertRaises(cal.CalibrationV2Error):
            cal.validate_threshold_artifact(bad, manifest=man, plan=fit, records=fit_records)
        bad_tier = copy.deepcopy(tiers); bad_tier['tiers'][0]['decision'] = 'incomplete'
        bad_tier['tiers_sha256'] = cal.digest({k:v for k,v in bad_tier.items() if k != 'tiers_sha256'})
        with self.assertRaises(cal.CalibrationV2Error):
            cal.validate_tier_artifact(bad_tier, manifest=man, plan=tier, thresholds=thresholds,
                                       records=tier_records, fit_plan=fit, fit_records=fit_records)

    def test_rehashed_plan_bindings_and_artifact_fields_reject(self):
        man, fit, fit_records, thresholds, tier, tier_records, tiers = self.flow()
        for field, value in [('fit_available', 0), ('fit_denominator', 1),
                             ('fit_contributing_algorithms', []), ('threshold_source', 'forged'),
                             ('decision', 'forged')]:
            forged = copy.deepcopy(thresholds); forged['thresholds'][0][field] = value
            forged['thresholds_sha256'] = cal.digest({k:v for k,v in forged.items() if k != 'thresholds_sha256'})
            with self.assertRaises(cal.CalibrationV2Error):
                cal.validate_threshold_artifact(forged, manifest=man, plan=fit, records=fit_records)
        for field, value in [('hit_rates', {}), ('resource_limit_frequencies', {}), ('iqr', 999),
                             ('algorithm_medians', {}), ('algorithm_agreement_gap2', False),
                             ('best_solver_disabled_upper_bound', 999), ('hard_gap_ok', False),
                             ('tier', 'forged'), ('decision', 'forged'), ('reason', 'forged')]:
            forged = copy.deepcopy(tiers); forged['tiers'][0][field] = value
            forged['tiers_sha256'] = cal.digest({k:v for k,v in forged.items() if k != 'tiers_sha256'})
            with self.assertRaises(cal.CalibrationV2Error):
                cal.validate_tier_artifact(forged, manifest=man, plan=tier, thresholds=thresholds,
                                           records=tier_records, fit_plan=fit, fit_records=fit_records)
        forged_plan = copy.deepcopy(tier); forged_plan['thresholds_sha256'] = '0' * 64
        forged_plan['plan_sha256'] = cal.digest({k:v for k,v in forged_plan.items() if k != 'plan_sha256'})
        with self.assertRaises(cal.CalibrationV2Error):
            cal.validate_tiers(man, forged_plan, thresholds, tier_records, fit_plan=fit, fit_records=fit_records)
        for plan, key in ((fit, 'unexpected'), (tier, 'unexpected')):
            forged = copy.deepcopy(plan); forged[key] = 1
            forged['plan_sha256'] = cal.digest({k:v for k,v in forged.items() if k != 'plan_sha256'})
            with self.assertRaises(cal.CalibrationV2Error): cal.validate_plan(forged)

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
