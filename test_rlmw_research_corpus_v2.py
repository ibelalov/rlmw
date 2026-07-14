import copy
import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path

import rlmw_research_corpus_v2 as v2

class V2CandidateToolingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = v2.generate_records(smoke=True)
        cls.manifest = v2.build_manifest(cls.records)

    def test_fixed_vectors_are_literal(self):
        self.assertEqual(v2.verify_test_vectors(), v2.EXPECTED_TEST_VECTOR_DIGEST)
        self.assertEqual(v2.EXPECTED_TEST_VECTORS["enc_int_0"], "490000000100")
        self.assertEqual(v2.computed_test_vectors(), v2.EXPECTED_TEST_VECTORS)
        self.assertIn(v2.RANDOM_DOMAIN.encode().hex(), v2.EXPECTED_TEST_VECTORS["dense_context_encoding"])
        self.assertNotEqual(v2.EXPECTED_TEST_VECTORS["dense_R0"], v2.EXPECTED_TEST_VECTORS["dense_R1"])

    def test_encoding_rejects_ambiguous_inputs(self):
        for bad in (True, False, -1, 1.0, float("nan"), {"x": 1}):
            with self.assertRaises(v2.V2Error):
                v2.encode(bad)
        with self.assertRaises(v2.V2Error): v2.encode("e\u0301")
        with self.assertRaises(v2.V2Error): v2.BinaryMatrix.from_rows([[True]])
        with self.assertRaises(v2.V2Error): v2.BinaryMatrix.from_rows([[1], [1, 0]])
        with self.assertRaises(v2.V2Error): v2.BinaryMatrix.from_rows([["1"]])

    def test_hash_literals_and_domains(self):
        H = v2.BinaryMatrix.from_rows([[1,0,1],[0,1,1]])
        self.assertEqual(v2.public_h_sha256(H), v2.EXPECTED_TEST_VECTORS["public_h_sha256_fixture"])
        self.assertEqual(v2.row_space_sha256(H), v2.EXPECTED_TEST_VECTORS["row_space_sha256_fixture"])
        self.assertEqual(v2.config_digest(), v2.EXPECTED_TEST_VECTORS["configuration_digest"])

    def test_sparse_feasibility_true_progressive_generator(self):
        report = v2.sparse_feasibility_report()
        self.assertEqual(set(report), set(v2.SPARSE_STRATA) | set(v2.PLANTED_SPARSE_STRATA))
        for stratum, attempt in report.items():
            self.assertLess(attempt, v2.MAX_SPARSE_ATTEMPTS)

    def test_planted_sparse_witness_is_independently_verified(self):
        H, support, attempt, _ = v2.generate_planted_sparse("planted-sparse-n120-r60-w10", 0, 0)
        self.assertLess(attempt, v2.MAX_SPARSE_ATTEMPTS)
        v2.validate_planted_witness(H, support, 10)
        v2.validate_matrix(H, "planted-sparse-n120-r60-w10", 60)
        corrupt = support[:-1] + [next(i for i in range(H.ncols) if i not in support)]
        with self.assertRaises(v2.V2Error): v2.validate_planted_witness(H, corrupt, 10)

    def test_small_circuit_audit_weights_and_resource_limit(self):
        for weight in range(1, 7):
            rows = [[0] * weight for _ in range(max(1, weight - 1))]
            if weight > 1:
                for j in range(weight - 1): rows[j][j] = 1; rows[j][-1] = 1
            H = v2.BinaryMatrix.from_rows(rows)
            audit = v2.small_circuit_audit(H, 6)
            self.assertEqual(audit["status"], "FOUND_WITNESS")
            self.assertEqual(audit["weight"], weight)
        large = v2.generate_dense("dense-n96-r48-p50", 0, 0)[0]
        self.assertEqual(v2.small_circuit_audit(large, 6, resource_limit_entries=10)["status"], "RESOURCE_LIMIT")

    def test_manifest_validation_and_determinism(self):
        a = v2.canonical_json(self.manifest)
        b = v2.canonical_json(v2.build_manifest(v2.generate_records(smoke=True)))
        self.assertEqual(a, b)
        v2.validate_manifest(self.manifest)
        self.assertFalse(self.manifest["is_frozen_v2_manifest"])

    def _tampered(self, fn):
        bad = copy.deepcopy(self.manifest); fn(bad)
        bad["candidate_manifest_digest"] = v2._digest(("candidate_manifest_digest", {k: vv for k, vv in bad.items() if k != "candidate_manifest_digest"}))
        return bad

    def test_manifest_rejects_tampering(self):
        cases = [
            lambda m: m["records"][0].__setitem__("case_id", "bad"),
            lambda m: m["records"][0].__setitem__("family_id", "bad_family"),
            lambda m: m["records"][0].__setitem__("lineage_group_id", "bad_lineage"),
            lambda m: m["records"][0].__setitem__("construction_attempt", m["records"][0]["construction_attempt"] + 1),
            lambda m: m["records"][0].__setitem__("configuration_digest", "00"*32),
            lambda m: m["records"][0]["validation"].__setitem__("rank", 999),
            lambda m: m["records"][0].__setitem__("split", "test"),
        ]
        for fn in cases:
            with self.assertRaises(v2.V2Error): v2.validate_manifest(self._tampered(fn))
        bad = copy.deepcopy(self.manifest)
        bad["records"][0]["H_rows"][0] = "x" + bad["records"][0]["H_rows"][0][1:]
        bad["candidate_manifest_digest"] = v2._digest(("candidate_manifest_digest", {k: vv for k, vv in bad.items() if k != "candidate_manifest_digest"}))
        with self.assertRaises(v2.V2Error): v2.validate_manifest(bad)
        bad = copy.deepcopy(self.manifest)
        bad["candidate_manifest_digest"] = "00"*32
        with self.assertRaises(v2.V2Error): v2.validate_manifest(bad)
        with self.assertRaises(ValueError): v2.canonical_json({"x": math.nan})

    def test_row_space_duplicate_rejected(self):
        bad = copy.deepcopy(self.manifest)
        bad["records"][1]["H_rows"] = bad["records"][0]["H_rows"]
        H = v2.BinaryMatrix.from_row_strings(bad["records"][1]["H_rows"])
        bad["records"][1]["public_h_sha256"] = v2.public_h_sha256(H)
        bad["records"][1]["row_space_sha256"] = v2.row_space_sha256(H)
        bad["records"][1]["protected_record_sha256"] = v2._digest(("protected_record", v2.protected_without_digest(bad["records"][1])))
        bad["candidate_manifest_digest"] = v2._digest(("candidate_manifest_digest", {k: vv for k, vv in bad.items() if k != "candidate_manifest_digest"}))
        with self.assertRaises(v2.V2Error): v2.validate_manifest(bad)

    def test_cli_smoke_generation_validation_summary(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            self.assertEqual(v2.main(["generate-candidate-pool", "--smoke", "--output-dir", d1]), 0)
            self.assertEqual(v2.main(["generate-candidate-pool", "--smoke", "--output-dir", d2]), 0)
            p1 = Path(d1) / "candidate_pool_manifest.json"; p2 = Path(d2) / "candidate_pool_manifest.json"
            self.assertEqual(p1.read_bytes(), p2.read_bytes())
            self.assertEqual(v2.main(["validate-candidate-pool", str(p1)]), 0)
            self.assertEqual(v2.main(["summary", str(p1)]), 0)

    def test_commitments_and_public_seed_vectors(self):
        self.assertEqual(v2.calibration_seed("threshold_fit_seed", 0), v2.EXPECTED_TEST_VECTORS["threshold_fit_seed_0"])
        self.assertEqual(v2.calibration_seed("tier_validation_seed", 0), v2.EXPECTED_TEST_VECTORS["tier_validation_seed_0"])
        commit = v2.final_eval_commitment(0, v2.DUMMY_FINAL_EVAL_SEEDS[0])
        self.assertEqual(commit, v2.EXPECTED_TEST_VECTORS["dummy_final_commit_0"])
        self.assertTrue(v2.verify_final_eval_commitment(0, v2.DUMMY_FINAL_EVAL_SEEDS[0].hex(), commit))

if __name__ == "__main__":
    unittest.main()
