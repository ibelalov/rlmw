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

if __name__ == '__main__':
    unittest.main()
