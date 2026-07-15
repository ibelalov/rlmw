import copy, json, tempfile, unittest
from pathlib import Path

import rlmw_research_calibration_v2 as cal

class CalibrationV2Tests(unittest.TestCase):
    def case(self):
        h=["1111"]
        return {"case_id":"c","H_rows":h,"public_h_sha256":cal.isd_v2.public_h_sha256(h)}
    def plan_records(self, weights=None, completed=True):
        c=self.case(); mods=cal.module_digests(); base={"case_id":"c","public_h_sha256":c["public_h_sha256"],"candidate_manifest_sha256":"a"*64,"calibration_source_commit":"b"*40,**mods}
        plan={"schema":cal.PLAN_SCHEMA,"protocol_id":cal.PROTOCOL_ID,"candidate_manifest_sha256":"a"*64,"runs":[]}
        for alg in cal.ALGORITHMS:
            for seed in range(8): plan["runs"].append({**base,"solver_stratum":cal.SOLVER_DISABLED,"algorithm_id":alg,"phase":cal.FIT_PHASE,"seed_role":cal.FIT_ROLE,"seed_index":seed,"budget":cal.BUDGETS[-1]})
            for b in cal.BUDGETS:
                for seed in range(8): plan["runs"].append({**base,"solver_stratum":cal.SOLVER_DISABLED,"algorithm_id":alg,"phase":cal.TIER_PHASE,"seed_role":cal.TIER_ROLE,"seed_index":seed,"budget":b})
        recs=[]; weights=weights or [2]
        for i,r in enumerate(plan["runs"]):
            w=weights[i%len(weights)]
            rec={"result_schema":cal.RESULT_SCHEMA,**r,"H_rows":c["H_rows"],"W":None if r["phase"]==cal.FIT_PHASE else 3,"completed_budget":completed,"resource_limit":not completed,"best_candidate_bits":"1100","best_weight":2,"witness_verified":True,"threshold_hit":False,"runtime_s":0.0,"calibration_result_sha256":""}
            if r["phase"]==cal.FIT_PHASE:
                rec["best_weight"]=w
            else:
                rec["threshold_hit"]=rec["best_weight"]<=rec["W"]
            rec["calibration_result_sha256"]=cal.digest({k:v for k,v in rec.items() if k!="calibration_result_sha256"})
            recs.append(rec)
        return plan,recs
    def test_nearest_rank_and_lower_median(self):
        self.assertEqual(cal.nearest_rank([10,20,30,40,50], .40), 20)
        self.assertEqual(cal.nearest_rank([1,2,3,4], .25), 1)
        self.assertEqual(cal.nearest_rank([1,2,3,4], .75), 3)
        self.assertEqual(cal.lower_median([4,1,3,2]), 2)
    def test_two_pass_fit_and_tier(self):
        plan,recs=self.plan_records([5,1,4,3,2,6,7,8])
        th=cal.fit_thresholds(plan,recs)
        self.assertEqual(th["thresholds"][0]["W"], 4)
        ti=cal.validate_tiers(plan,th,recs)
        self.assertEqual(ti["tiers"][0]["tier"], "calibration_incomplete")
        self.assertTrue(ti["tiers"][0]["algorithm_agreement_gap2"])
    def test_unavailable_denominator(self):
        plan,recs=self.plan_records(completed=False)
        th=cal.fit_thresholds(plan,recs)
        self.assertIsNone(th["thresholds"][0]["W"])
        self.assertEqual(th["thresholds"][0]["denominator"], 32)
    def test_algorithm_agreement_false(self):
        plan,recs=self.plan_records()
        for r in recs:
            if r["phase"]==cal.TIER_PHASE and r["budget"]==cal.BUDGETS[-1]:
                r["best_weight"]={cal.ALGORITHMS[0]:1,cal.ALGORITHMS[1]:10,cal.ALGORITHMS[2]:20,cal.ALGORITHMS[3]:30}[r["algorithm_id"]]
        th=cal.fit_thresholds(plan,recs); ti=cal.validate_tiers(plan,th,recs)
        self.assertFalse(ti["tiers"][0]["algorithm_agreement_gap2"])
    def test_missing_duplicate_extra_wrong_seed(self):
        plan,recs=self.plan_records()
        cal.validate_results(plan,recs)
        with self.assertRaises(cal.CalibrationV2Error): cal.validate_results(plan,recs[:-1])
        with self.assertRaises(cal.CalibrationV2Error): cal.validate_results(plan,recs+[copy.deepcopy(recs[0])])
        extra=copy.deepcopy(recs[0]); extra["seed_index"]=7; extra["phase"]=cal.TIER_PHASE; extra["seed_role"]=cal.FIT_ROLE
        with self.assertRaises(cal.CalibrationV2Error): cal.validate_result_record(extra)
    def test_solver_stratum_separation(self):
        plan,recs=self.plan_records(); bad=copy.deepcopy(recs[0]); bad["algorithm_id"]=cal.CP_SAT_ALGORITHM; bad["solver_stratum"]=cal.SOLVER_DISABLED
        with self.assertRaises(cal.CalibrationV2Error): cal.validate_result_record(bad)
    def test_public_payload_leakage_and_w_independent(self):
        c=self.case(); leaked={**c,"family_id":"x"}
        with self.assertRaises(cal.CalibrationV2Error): cal.public_payload(leaked, manifest_digest="a"*64, phase=cal.FIT_PHASE, seed_role=cal.FIT_ROLE, seed_index=0, budget=1, algorithm_id=cal.ALGORITHMS[0])
        p=cal.public_payload(c, manifest_digest="a"*64, phase=cal.FIT_PHASE, seed_role=cal.FIT_ROLE, seed_index=0, budget=1, algorithm_id=cal.ALGORITHMS[0])
        self.assertNotIn("W", p)
        q=cal.public_payload(c, manifest_digest="a"*64, phase=cal.TIER_PHASE, seed_role=cal.TIER_ROLE, seed_index=0, budget=1, algorithm_id=cal.ALGORITHMS[0], W=2)
        self.assertEqual({k:v for k,v in q.items() if k!="W" and k!="phase" and k!="seed_role"}, {k:v for k,v in p.items() if k!="phase" and k!="seed_role"})
    def test_source_manifest_config_tampering_and_cli_smoke(self):
        plan,recs=self.plan_records(); bad=copy.deepcopy(recs[0]); bad["candidate_manifest_sha256"]="0"*64
        with self.assertRaises(cal.CalibrationV2Error): cal.validate_results(plan,[bad]+recs[1:])
        bad=copy.deepcopy(recs[0]); bad["candidate_config_digest"]="0"*64
        # validate_result_record requires binding presence; plan validation catches module/config mismatches via binding.
        with self.assertRaises(cal.CalibrationV2Error): cal.validate_results(plan,[bad]+recs[1:])
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(cal.main(["smoke","--output-dir",d]),0)
            self.assertTrue((Path(d)/"results.jsonl").exists())
            # deterministic double smoke generation/validation
            d2=Path(d)/"again"; self.assertEqual(cal.main(["smoke","--output-dir",str(d2)]),0)
            self.assertEqual((Path(d)/"thresholds.json").read_text(), (d2/"thresholds.json").read_text())
    def test_strict_json_duplicate_key_and_nonfinite(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"x.json"; p.write_text('{"a":1,"a":2}', encoding="ascii")
            with self.assertRaises(cal.CalibrationV2Error): cal.read_json(p)
            with self.assertRaises(ValueError): cal.canonical_bytes({"x":float("nan")})

if __name__ == "__main__":
    unittest.main()
