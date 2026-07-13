import copy, hashlib, json, tempfile, unittest
from pathlib import Path

import rlmw_research_corpus_v2 as v2

class V2CandidateToolingTests(unittest.TestCase):
    def test_encoding_vectors_and_rejections(self):
        tv=v2.test_vectors()
        self.assertEqual(tv["encodings"]["integer_zero"], "490000000100")
        self.assertIn("dense_entry_R0", tv["random_access"])
        for bad in (True, -1, 1.2):
            with self.assertRaises(v2.V2Error): v2.encode(bad)
        with self.assertRaises(v2.V2Error): v2.encode("bad\x00")

    def test_sparse_feasibility_all_strata(self):
        for s in v2.SPARSE_STRATA:
            H,a=v2.generate_sparse(s, max_attempts=20000)
            self.assertLess(a, 20000)
            v2.validate_matrix(H, s, v2.SPARSE_STRATA[s][1], True)
        for s in v2.PLANTED_SPARSE_STRATA:
            H,w,a=v2.generate_planted_sparse(s, max_attempts=20000)
            self.assertLess(a, 20000)
            self.assertEqual(len(w), v2.PLANTED_SPARSE_STRATA[s][2])
            v2.validate_matrix(H, s, v2.PLANTED_SPARSE_STRATA[s][1], True)

    def _small_manifest(self):
        recs=[v2.build_record("dense-n96-r48-p50",0,0), v2.build_record("dense-n96-r48-p50",0,1), v2.build_record("sparse-reg-n120-r60-dv3-dc6",0,0), v2.build_record("sparse-reg-n120-r60-dv3-dc6",0,1)]
        return v2.manifest(recs)

    def test_manifest_determinism_and_validation(self):
        a=self._small_manifest(); b=self._small_manifest()
        self.assertEqual(v2.canonical_json(a), v2.canonical_json(b))
        v2.validate_manifest(a)
        self.assertFalse(a["is_frozen_v2_manifest"])

    def test_tampering_and_regeneration_mismatch(self):
        man=self._small_manifest()
        bad=copy.deepcopy(man); bad["records"][0]["H_rows"][0] = ("1" if bad["records"][0]["H_rows"][0][0]=="0" else "0") + bad["records"][0]["H_rows"][0][1:]
        bad["candidate_manifest_digest"]=hashlib.sha256(v2.canonical_json({k:v for k,v in bad.items() if k!="candidate_manifest_digest"})).hexdigest()
        with self.assertRaises(v2.V2Error): v2.validate_manifest(bad)
        bad=copy.deepcopy(man); bad["records"][0]["construction_batch_id"]=99
        bad["candidate_manifest_digest"]=hashlib.sha256(v2.canonical_json({k:v for k,v in bad.items() if k!="candidate_manifest_digest"})).hexdigest()
        with self.assertRaises(v2.V2Error): v2.validate_manifest(bad)

    def test_altered_provenance_hash_lineage_split_failures(self):
        man=self._small_manifest()
        bad=copy.deepcopy(man); bad["records"][0]["public_h_hash"]="00"*32
        bad["candidate_manifest_digest"]=hashlib.sha256(v2.canonical_json({k:v for k,v in bad.items() if k!="candidate_manifest_digest"})).hexdigest()
        with self.assertRaises(v2.V2Error): v2.validate_manifest(bad, regenerate=False)
        bad=copy.deepcopy(man); bad["records"][0]["split"]="test"
        bad["candidate_manifest_digest"]=hashlib.sha256(v2.canonical_json({k:v for k,v in bad.items() if k!="candidate_manifest_digest"})).hexdigest()
        with self.assertRaises(v2.V2Error): v2.validate_manifest(bad, regenerate=False)

    def test_four_cycle_incomplete_socket_and_witness_corruption(self):
        H=v2.BinaryMatrix.from_rows([[1,1,0,0],[1,1,0,0],[0,0,1,1]])
        with self.assertRaises(v2.V2Error): v2.validate_matrix(H, "sparse-reg-n120-r60-dv3-dc6", sparse=True)
        H,w=v2.generate_planted_dense("planted-dense-n96-r48-w10")
        with self.assertRaises(v2.V2Error): v2.validate_planted_witness(H, w[:-1]+[next(i for i in range(H.ncols) if i not in w)])
        H,_,_=v2.generate_planted_sparse("planted-sparse-n120-r60-w10")
        rows=H.as_lists(); rows[0][next(i for i,b in enumerate(rows[0]) if b)] = 0
        with self.assertRaises(v2.V2Error): v2.validate_matrix(v2.BinaryMatrix.from_rows(rows), "planted-sparse-n120-r60-w10", sparse=True)

    def test_cli_generate_validate_summary_and_commitments(self):
        seed=v2.calibration_seed("threshold_fit_seed",0)
        self.assertEqual(len(seed),32)
        c=v2.final_eval_commitment(0, v2.DUMMY_FINAL_EVAL_SEEDS[0])
        self.assertTrue(v2.verify_final_eval_commitment(0, v2.DUMMY_FINAL_EVAL_SEEDS[0].hex(), c))
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(v2.main(["generate-candidate-pool","--output-dir",d,"--dense","1","--sparse","0"]),0)
            p=str(Path(d)/"candidate_pool_manifest.json")
            self.assertEqual(v2.main(["validate-candidate-pool",p]),0)
            self.assertEqual(v2.main(["summary",p]),0)

    def test_self_test_under_asserts_enabled(self):
        self.assertEqual(v2.self_test()["status"], "PASS")

if __name__ == "__main__":
    unittest.main()
