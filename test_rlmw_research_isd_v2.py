import copy
import json
import tempfile
import unittest
from pathlib import Path

import rlmw_research_isd_v2 as isd

class ISDV2Tests(unittest.TestCase):
    def case(self): return copy.deepcopy(isd.FIXTURE_CASES['isdv2-fixture-hamming7'])
    def public(self, **kw):
        opts=dict(phase='tier_validation', seed_role='tier_validation_seed', seed_index=0, budget=16)
        opts.update(kw); return isd.make_public_input(self.case(), **opts)
    def config(self, **over):
        cfg=isd.algorithm_config('smoke', rank=3); cfg.update(over); return isd.resolved_config(cfg, rank=3)
    def record(self, **kw): return isd.run_record(self.public(**kw), self.config())

    def test_tiny_bruteforce_differential_and_verified_incumbent(self):
        for case in isd.FIXTURE_CASES.values():
            brute=isd.brute_force_min_weight(case['H_rows'])
            rec=isd.run_record(isd.make_public_input(case, phase='tier_validation', seed_role='tier_validation_seed', seed_index=0, budget=16), self.config())
            isd.validate_result_record(rec)
            self.assertIsNotNone(brute)
            if rec['best_weight'] is not None:
                self.assertGreaterEqual(rec['best_weight'], brute)
                rows,n=isd.parse_h_rows(case['H_rows'])
                self.assertEqual(isd.verify_nonzero_kernel_word(rows,n,isd.bits_to_word(rec['best_candidate_bits'])), rec['best_weight'])

    def test_collision_lists_and_bucket_matching_execute(self):
        rec=self.record()
        self.assertGreater(rec['list_entries_left'],0)
        self.assertGreater(rec['list_entries_right'],0)
        self.assertGreater(rec['bucket_probes'],0)
        self.assertGreater(rec['collision_pairs'],0)
        self.assertEqual(rec['candidate_evaluations'], rec['reconstructed_candidates'])

    def test_projected_collision_not_accepted_without_full_verification(self):
        cfg=self.config(projection_bits=0)
        rec=isd.run_record(self.public(budget=8), cfg)
        rows,n=isd.parse_h_rows(rec['H_rows'])
        self.assertGreater(rec['collision_pairs'],0)
        if rec['best_candidate_bits']:
            self.assertTrue(isd.syndrome_is_zero(rows, isd.bits_to_word(rec['best_candidate_bits'])))

    def test_original_coordinate_restoration(self):
        rows,n=isd.parse_h_rows(['110100','011010','001101'])
        indep,_=isd.rref_bit_rows(rows,n)
        sys=isd._systematic(indep, rows, n, [0,2,4])
        self.assertIsNotNone(sys)
        basis,info,parity=sys
        self.assertEqual(info, [1,3,5])
        for w in basis: self.assertGreater(isd.verify_nonzero_kernel_word(rows,n,w),0)

    def test_singular_information_set_retry(self):
        rows,n=isd.parse_h_rows(self.case()['H_rows']); indep,_=isd.rref_bit_rows(rows,n)
        self.assertIsNone(isd._systematic(indep, rows, n, [0,1,2]))
        rec=self.record(budget=1)
        self.assertGreaterEqual(rec['information_set_attempts'], rec['information_sets_accepted'])

    def test_deterministic_replay_byte_identical_core(self):
        a=self.record(); b=self.record()
        self.assertEqual(isd.canonical_json_bytes(isd.reproducible_core(a)), isd.canonical_json_bytes(isd.reproducible_core(b)))

    def test_budget_zero_one_and_off_by_one_validation(self):
        zero=self.record(budget=0); self.assertEqual(zero['candidate_evaluations'],0); isd.validate_result_record(zero)
        one=self.record(budget=1); self.assertLessEqual(one['candidate_evaluations'],1); isd.validate_result_record(one)
        bad=copy.deepcopy(one); bad['candidate_evaluations']=bad['budget']+1; bad['reproducible_core_sha256']=isd.compute_reproducible_core_sha256(bad)
        with self.assertRaises(isd.ISDValidationError): isd.validate_result_record(bad)

    def test_operation_cap_resource_limit(self):
        cfg=self.config(max_collision_pairs=0)
        rec=isd.run_record(self.public(budget=10), cfg)
        self.assertEqual(rec['termination_reason'],'resource_limit')
        self.assertGreater(rec['resource_limit_events'],0)
        isd.validate_result_record(rec)

    def test_duplicate_candidate_accounting(self):
        case={'case_id':'dup','H_rows':['1111'],'W':2}
        pi=isd.make_public_input(case, phase='tier_validation', seed_role='tier_validation_seed', seed_index=0, budget=6)
        cfg=self.config(projection_bits=0, information_set_limit=8)
        rec=isd.run_record(pi,cfg)
        self.assertGreaterEqual(rec['duplicate_candidates'],0)
        isd.validate_result_record(rec)

    def test_public_boundary_and_seed_rejections(self):
        with self.assertRaises(isd.ISDValidationError): self.public(seed_role='final_eval_seed')
        with self.assertRaises(isd.ISDValidationError): self.public(seed_index=8)
        with self.assertRaises(isd.ISDValidationError): self.public(seed_index=True)
        pi=self.public(); pi['family']='leak'
        with self.assertRaises(isd.ISDValidationError): isd.run_record(pi,self.config())
        with self.assertRaises(isd.ISDValidationError): isd.make_public_input(self.case(), phase='threshold_fit', seed_role='threshold_fit_seed', seed_index=0, budget=1, W=3)

    def test_schema_tamper_and_solver_assisted_separation(self):
        rec=self.record()
        for key,val in [('cp_sat_status','OPTIMAL'),('optimality_certified',True),('exact_distance',3)]:
            bad=copy.deepcopy(rec); bad[key]=val
            with self.assertRaises(isd.ISDValidationError): isd.validate_result_record(bad)
        bad=copy.deepcopy(rec); bad['best_candidate_bits']='1'+'0'*(bad['n']-1); bad['best_candidate_sha256']=isd.sha256_object({'candidate_bits':bad['best_candidate_bits']}); bad['best_weight']=1; bad['reproducible_core_sha256']=isd.compute_reproducible_core_sha256(bad)
        with self.assertRaises(isd.ISDValidationError): isd.validate_result_record(bad)

    def test_jsonl_canonical_duplicate_key_and_summary(self):
        rec=self.record()
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'r.jsonl'; isd.write_jsonl([rec],p)
            self.assertEqual(len(isd.read_validate_jsonl(p)),1)
            dup=Path(td)/'dup.jsonl'; dup.write_bytes(b'{"a":1,"a":2}\n')
            with self.assertRaises(isd.ISDValidationError): isd.read_validate_jsonl(dup)
            non=Path(td)/'non.jsonl'; non.write_text(json.dumps(rec, indent=2)+'\n')
            with self.assertRaises(isd.ISDValidationError): isd.read_validate_jsonl(non)

    def test_bad_integer_config_rejections(self):
        for value in [True, -1, 1.5, '1']:
            cfg=self.config(); cfg['left_weight']=value
            with self.assertRaises(isd.ISDValidationError): isd.resolved_config(cfg, rank=3)

    def test_observed_singular_retry_with_fake_rng(self):
        class FakeRng:
            randbits_calls=0; randbelow_calls=0; sha256_blocks_generated=0
            def __init__(self): self.calls=0
            def sample_subset(self,n,k):
                self.calls += 1
                return [0,1,2] if self.calls == 1 else [0,1,3]
        pi=self.public(budget=1)
        outcome=isd.run_stern_dumer(pi, self.config(), FakeRng())
        self.assertEqual(outcome.singular_information_sets, 1)
        self.assertGreaterEqual(outcome.information_sets_accepted, 1)

    def test_actual_duplicate_candidate_accounting_with_repeated_information_set(self):
        class RepeatRng:
            randbits_calls=0; randbelow_calls=0; sha256_blocks_generated=0
            def sample_subset(self,n,k): return [0,1,3]
        pi=self.public(budget=4)
        outcome=isd.run_stern_dumer(pi, self.config(information_set_limit=4), RepeatRng())
        self.assertGreater(outcome.duplicate_candidates, 0)

    def test_phase_seed_cross_pair_rejection(self):
        with self.assertRaises(isd.ISDValidationError):
            isd.make_public_input(self.case(), phase='threshold_fit', seed_role='tier_validation_seed', seed_index=0, budget=1)
        with self.assertRaises(isd.ISDValidationError):
            isd.make_public_input(self.case(), phase='tier_validation', seed_role='threshold_fit_seed', seed_index=0, budget=1)

    def test_protocol_implementation_source_termination_and_counter_tampering(self):
        rec=self.record()
        for key,value in [('candidate_protocol_version','bad'),('implementation_version','bad'),('termination_reason','candidate_budget_exhausted')]:
            bad=copy.deepcopy(rec); bad[key]=value; bad['reproducible_core_sha256']=isd.compute_reproducible_core_sha256(bad)
            with self.assertRaises(isd.ISDValidationError): isd.validate_result_record(bad)
        bad=copy.deepcopy(rec); bad['source']['isd_module_sha256']='0'*64; bad['reproducible_core_sha256']=isd.compute_reproducible_core_sha256(bad)
        with self.assertRaises(isd.ISDValidationError): isd.validate_result_record(bad, check_current_source=True)
        bad=copy.deepcopy(rec); bad['projection_operations']=bad['algorithm_config']['max_projection_operations']+1; bad['reproducible_core_sha256']=isd.compute_reproducible_core_sha256(bad)
        with self.assertRaises(isd.ISDValidationError): isd.validate_result_record(bad)

    def test_resource_cap_boundaries_zero_one_and_cap_plus_one(self):
        zero=isd.run_record(self.public(budget=4), self.config(max_collision_pairs=0))
        self.assertEqual(zero['collision_pairs'],0); self.assertEqual(zero['termination_reason'],'resource_limit')
        isd.validate_result_record(zero)
        one=isd.run_record(self.public(budget=4), self.config(max_collision_pairs=1))
        self.assertLessEqual(one['collision_pairs'],1); isd.validate_result_record(one)
        bad=copy.deepcopy(one); bad['collision_pairs']=bad['algorithm_config']['max_collision_pairs']+1; bad['reproducible_core_sha256']=isd.compute_reproducible_core_sha256(bad)
        with self.assertRaises(isd.ISDValidationError): isd.validate_result_record(bad)
        proj0=isd.run_record(self.public(budget=4), self.config(max_projection_operations=0))
        self.assertEqual(proj0['projection_operations'],0); self.assertEqual(proj0['termination_reason'],'resource_limit')

    def test_per_rank_calibration_projection_resolution_and_preflight(self):
        self.assertEqual(isd.algorithm_config('calibration', rank=3)['projection_bits'],3)
        self.assertEqual(isd.algorithm_config('calibration', rank=48)['projection_bits'],8)
        rows=isd.calibration_preflight()
        self.assertTrue(rows)
        for row in rows:
            self.assertGreaterEqual(row['max_candidate_capacity'], max(isd.BUDGET_LADDER))

    def test_w_independent_tier_validation_trajectory(self):
        low=isd.run_record(self.public(W=2), self.config())
        high=isd.run_record(self.public(W=10), self.config())
        for key in ['best_candidate_bits','best_weight','candidate_evaluations','collision_pairs','list_entries_left','list_entries_right','projection_operations']:
            self.assertEqual(low[key], high[key])
        self.assertNotEqual(low['threshold_witnesses_seen'], high['threshold_witnesses_seen'])

    def test_strict_integer_and_boolean_field_tampering(self):
        rec=self.record()
        for field,value in [('num_threads', True), ('n', float(rec['n'])), ('rank', float(rec['rank'])), ('best_weight', float(rec['best_weight']))]:
            bad=copy.deepcopy(rec); bad[field]=value; bad['reproducible_core_sha256']=isd.compute_reproducible_core_sha256(bad)
            with self.assertRaises(isd.ISDValidationError): isd.validate_result_record(bad)
        for field,value in [('witness_verified', 1), ('threshold_hit', 0)]:
            bad=copy.deepcopy(rec); bad[field]=value; bad['reproducible_core_sha256']=isd.compute_reproducible_core_sha256(bad)
            with self.assertRaises(isd.ISDValidationError): isd.validate_result_record(bad)
        cfg=self.config(); cfg['exhaust_candidate_budget']=1
        with self.assertRaises(isd.ISDValidationError): isd.resolved_config(cfg, rank=3)

    def test_all_counter_invariant_tampering_is_rejected(self):
        rec=self.record()
        cases=[
            ('singular_information_sets', rec['singular_information_sets']+1),
            ('reconstructed_candidates', rec['reconstructed_candidates']+1),
            ('list_entries_left', rec['list_entries_left']+1),
            ('bucket_probes', rec['bucket_probes']+1),
            ('skipped_collision_pairs', 2),
            ('threshold_witnesses_seen', 0),
            ('best_candidate_bits', None),
        ]
        for field,value in cases:
            bad=copy.deepcopy(rec); bad[field]=value
            if field == 'best_candidate_bits':
                bad['best_candidate_sha256']=None; bad['best_weight']=None; bad['witness_verified']=False
            bad['reproducible_core_sha256']=isd.compute_reproducible_core_sha256(bad)
            with self.subTest(field=field):
                with self.assertRaises(isd.ISDValidationError): isd.validate_result_record(bad)

    def test_strict_source_commit_and_fixture_option(self):
        rec=self.record()
        bad=copy.deepcopy(rec); bad['source']['source_commit']=None; bad['reproducible_core_sha256']=isd.compute_reproducible_core_sha256(bad)
        with self.assertRaises(isd.ISDValidationError): isd.validate_result_record(bad)
        isd.validate_result_record(bad, allow_missing_source=True)

    def test_missing_unknown_public_input_fields_and_concise_cli_failure(self):
        pi=self.public(); del pi['case_id']
        with self.assertRaises(isd.ISDValidationError): isd.run_record(pi,self.config())
        pi=self.public(); pi['unknown']='x'
        with self.assertRaises(isd.ISDValidationError): isd.run_record(pi,self.config())
        import subprocess, sys
        proc=subprocess.run([sys.executable,'rlmw_research_isd_v2.py','validate','/tmp/does-not-exist'], text=True, capture_output=True)
        self.assertNotEqual(proc.returncode,0)
        self.assertLess(len(proc.stderr),2000)
        self.assertNotIn('Traceback', proc.stderr)

if __name__ == '__main__':
    unittest.main()
