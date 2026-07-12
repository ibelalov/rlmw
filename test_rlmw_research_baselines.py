"""Independent stdlib regression tests for ``rlmw_research_baselines``.

These tests deliberately consume the module as a client.  They do not import
notebook state and do not use ``assert`` statements, so the same suite remains
effective under ``python -O``.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import rlmw_research_baselines as baselines
import rlmw_research_corpus as corpus

try:
    from ortools.sat.python import cp_model as _cp_model  # noqa: F401
except ImportError:
    ORTOOLS_AVAILABLE = False
else:
    ORTOOLS_AVAILABLE = True


ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / "h_native_research_v1_manifest.json"


class ResearchBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = baselines.load_manifest(MANIFEST_PATH)
        cls._record_cache: dict[tuple[str, str, str], dict] = {}

    def case(self, case_id: str) -> dict:
        return next(case for case in self.manifest["cases"] if case["case_id"] == case_id)

    @staticmethod
    def rng(baseline_id: str, *, case_id: str = "tiny", seed: int = 101) -> baselines.Sha256CounterRng:
        key = baselines.derive_rng_key(
            baseline_id=baseline_id,
            case_id=case_id,
            repetition_id=0,
            seed=seed,
        )
        return baselines.Sha256CounterRng(key)

    def config(
        self,
        baseline_id: str,
        case: dict,
        *,
        profile_id: str = "smoke",
    ) -> dict:
        return baselines.algorithm_config(
            profile_id,
            baseline_id,
            n=case["n"],
            k=case["k"],
        )

    def run_record(
        self,
        baseline_id: str = baselines.UNIFORM_KERNEL_SAMPLING,
        *,
        case_id: str = "hnrv1-c0001",
        profile_id: str = "smoke",
        fresh: bool = False,
    ) -> dict:
        cache_key = (baseline_id, case_id, profile_id)
        if not fresh and cache_key in self._record_cache:
            return copy.deepcopy(self._record_cache[cache_key])
        case = self.case(case_id)
        config = self.config(
            baseline_id,
            case,
            profile_id=profile_id,
        )
        record = baselines.run_baseline_record(
            manifest=self.manifest,
            manifest_path=MANIFEST_PATH,
            case=case,
            profile_id=profile_id,
            baseline_id=baseline_id,
            repetition_id=0,
            seed=101,
            config=config,
        )
        self._record_cache[cache_key] = copy.deepcopy(record)
        return record

    @staticmethod
    def rehash(record: dict) -> dict:
        record["reproducible_core_sha256"] = baselines.compute_reproducible_core_sha256(record)
        return record

    def assert_record_rejected(
        self,
        record: dict,
        *,
        check_current_source: bool = False,
    ) -> None:
        with self.assertRaises((baselines.BaselineValidationError, ValueError)):
            baselines.validate_result_record(
                record,
                self.manifest,
                manifest_path=MANIFEST_PATH,
                check_current_source=check_current_source,
            )

    @staticmethod
    def write_bytes(directory: Path, name: str, data: bytes) -> Path:
        path = directory / name
        path.write_bytes(data)
        return path

    def canonical_line(self, record: dict) -> bytes:
        return baselines.canonical_json_bytes(record) + b"\n"

    def known_control_record(self, case_id: str, candidate_word: int, *, profile_id: str) -> dict:
        case = self.case(case_id)
        config = self.config(
            baselines.UNIFORM_KERNEL_SAMPLING,
            case,
            profile_id=profile_id,
        )
        public_input = baselines.make_public_run_input(
            self.manifest,
            case_id,
            profile_id=profile_id,
            repetition_id=0,
            seed=101,
        )
        key = baselines.derive_rng_key(
            baseline_id=baselines.UNIFORM_KERNEL_SAMPLING,
            case_id=case_id,
            repetition_id=0,
            seed=101,
        )
        outcome = baselines.BaselineOutcome(
            best_candidate=candidate_word,
            candidate_evaluations=config["candidate_budget"],
            objective_evaluations=config["candidate_budget"],
            exact_verifications=config["candidate_budget"],
            valid_codewords_seen=config["candidate_budget"],
            threshold_witnesses_seen=1,
            iterations=config["candidate_budget"],
            termination_reason="candidate_budget_exhausted",
            diagnostics={"test_control": True},
        )
        return baselines.assemble_result_record(
            manifest=self.manifest,
            manifest_path=MANIFEST_PATH,
            case=case,
            public_input=public_input,
            profile_id=profile_id,
            baseline_id=baselines.UNIFORM_KERNEL_SAMPLING,
            repetition_id=0,
            declared_seed=101,
            derived_seed_hex=key.hex(),
            config=config,
            outcome=outcome,
            total_runtime_s=0.0,
            peak_traced_memory_bytes=0,
            process_rss_bytes=0,
        )

    def cp_sat_record(
        self,
        status: str,
        *,
        case_id: str,
        candidate_word: int | None = None,
        raw_status: str | None = None,
    ) -> dict:
        """Build a CP-SAT semantic record without importing or invoking OR-Tools."""
        case = self.case(case_id)
        profile_id = "smoke" if "smoke" in case["subset"] else "full"
        config = self.config(
            baselines.CP_SAT_THRESHOLD_REFERENCE,
            case,
            profile_id=profile_id,
        )
        public_input = baselines.make_public_run_input(
            self.manifest,
            case_id,
            profile_id=profile_id,
            repetition_id=0,
            seed=101,
        )
        key = baselines.derive_rng_key(
            baseline_id=baselines.CP_SAT_THRESHOLD_REFERENCE,
            case_id=case_id,
            repetition_id=0,
            seed=101,
        )
        status_data = {
            "FEASIBLE": {
                "raw": raw_status or "OPTIMAL",
                "termination": "solver_feasible",
                "infeasible": False,
            },
            "INFEASIBLE": {
                "raw": raw_status or "INFEASIBLE",
                "termination": "solver_infeasible",
                "infeasible": True,
            },
            "UNKNOWN": {
                "raw": raw_status or "UNKNOWN",
                "termination": "solver_unknown_or_limit",
                "infeasible": False,
            },
        }[status]
        has_candidate = candidate_word is not None
        outcome = baselines.BaselineOutcome(
            best_candidate=candidate_word,
            candidate_evaluations=int(has_candidate),
            objective_evaluations=int(has_candidate),
            exact_verifications=int(has_candidate),
            valid_codewords_seen=int(has_candidate),
            threshold_witnesses_seen=int(has_candidate),
            iterations=1,
            solver_calls=1,
            solver_time_s=0.01,
            solver_status=status,
            solver_status_raw=status_data["raw"],
            threshold_infeasibility_certified=status_data["infeasible"],
            termination_reason=status_data["termination"],
            diagnostics={"synthetic_solver_semantics_test": True},
        )
        return baselines.assemble_result_record(
            manifest=self.manifest,
            manifest_path=MANIFEST_PATH,
            case=case,
            public_input=public_input,
            profile_id=profile_id,
            baseline_id=baselines.CP_SAT_THRESHOLD_REFERENCE,
            repetition_id=0,
            declared_seed=101,
            derived_seed_hex=key.hex(),
            config=config,
            outcome=outcome,
            total_runtime_s=0.02,
            peak_traced_memory_bytes=0,
            process_rss_bytes=0,
        )

    def synthetic_lee_record(self) -> dict:
        case = self.case("hnrv1-c0001")
        config = self.config(baselines.LEE_BRICKELL_ISD, case, profile_id="smoke")
        public_input = baselines.make_public_run_input(
            self.manifest,
            case["case_id"],
            profile_id="smoke",
            repetition_id=0,
            seed=101,
        )
        key = baselines.derive_rng_key(
            baseline_id=baselines.LEE_BRICKELL_ISD,
            case_id=case["case_id"],
            repetition_id=0,
            seed=101,
        )
        budget = config["candidate_budget"]
        outcome = baselines.BaselineOutcome(
            best_candidate=(1 << 0) | (1 << 1) | (1 << 2),
            candidate_evaluations=budget,
            objective_evaluations=budget,
            exact_verifications=budget,
            valid_codewords_seen=budget,
            threshold_witnesses_seen=1,
            iterations=1,
            information_set_attempts=1,
            information_sets_accepted=1,
            singular_information_sets=0,
            termination_reason="candidate_budget_exhausted",
            diagnostics={"synthetic_accepted_set_accounting_test": True},
        )
        return baselines.assemble_result_record(
            manifest=self.manifest,
            manifest_path=MANIFEST_PATH,
            case=case,
            public_input=public_input,
            profile_id="smoke",
            baseline_id=baselines.LEE_BRICKELL_ISD,
            repetition_id=0,
            declared_seed=101,
            derived_seed_hex=key.hex(),
            config=config,
            outcome=outcome,
            total_runtime_s=0.0,
            peak_traced_memory_bytes=0,
            process_rss_bytes=0,
        )

    def test_nullspace_preserves_coordinates_and_exhausts_tiny_kernel(self) -> None:
        h_rows, n = baselines.parse_h_rows(["10110", "01101", "11011", "00000"])
        basis, pivots, free = baselines.deterministic_kernel_basis(h_rows, n)

        self.assertEqual(len(basis), n - len(pivots))
        self.assertEqual(len(basis), len(free))
        for basis_index, vector in enumerate(basis):
            for free_index, coordinate in enumerate(free):
                self.assertEqual((vector >> coordinate) & 1, int(basis_index == free_index))

        generated = {
            baselines.kernel_word(basis, coefficients)
            for coefficients in range(1 << len(basis))
        }
        direct = {
            word
            for word in range(1 << n)
            if baselines.syndrome_is_zero(h_rows, word)
        }
        self.assertEqual(generated, direct)

        for coordinate in range(n):
            bits = "".join("1" if index == coordinate else "0" for index in range(n))
            self.assertEqual(baselines.word_to_bits(baselines.bits_to_word(bits), n), bits)

    def test_nullspace_is_invariant_under_row_operations(self) -> None:
        h_rows, n = baselines.parse_h_rows(["10110", "01101"])
        original = baselines.deterministic_kernel_basis(h_rows, n)
        row_operated = [h_rows[0] ^ h_rows[1], h_rows[1], 0, h_rows[0]]
        operated = baselines.deterministic_kernel_basis(row_operated, n)
        self.assertEqual(operated, original)

    def test_prng_known_answer_and_replay(self) -> None:
        key = bytes(32)
        first = baselines.Sha256CounterRng(key)
        second = baselines.Sha256CounterRng(key)
        expected = (
            "17b0761f87b081d5cf10757ccc89f12be355c70e2e29df288b65b30710dcbcd1"
            "86f77354f38f799c"
        )
        self.assertEqual(first.bytes(40).hex(), expected)
        self.assertEqual(second.bytes(40).hex(), expected)

        first = self.rng(baselines.UNIFORM_KERNEL_SAMPLING)
        second = self.rng(baselines.UNIFORM_KERNEL_SAMPLING)
        self.assertEqual([first.randbelow(97) for _ in range(30)], [second.randbelow(97) for _ in range(30)])
        different = self.rng(baselines.FIXED_WEIGHT_SUBSET_SAMPLING)
        self.assertNotEqual(first.bytes(16), different.bytes(16))

    def test_solver_disabled_budget_zero_one_and_off_by_one_boundaries(self) -> None:
        public_input = {"H_rows": ["111"], "W": 2}
        for budget in (0, 1, 2):
            with self.subTest(baseline="uniform", budget=budget):
                config = baselines.algorithm_config(
                    "smoke",
                    baselines.UNIFORM_KERNEL_SAMPLING,
                    overrides={"candidate_budget": budget},
                    n=3,
                    k=2,
                )
                outcome = baselines.run_uniform_kernel_sampling(
                    public_input,
                    config,
                    self.rng(baselines.UNIFORM_KERNEL_SAMPLING),
                )
                self.assertEqual(outcome.candidate_evaluations, budget)
                self.assertEqual(outcome.objective_evaluations, budget)
                self.assertEqual(outcome.exact_verifications, budget)
                self.assertEqual(outcome.iterations, budget)

            with self.subTest(baseline="fixed", budget=budget):
                config = baselines.algorithm_config(
                    "smoke",
                    baselines.FIXED_WEIGHT_SUBSET_SAMPLING,
                    overrides={"candidate_budget": budget, "weights": [1, 2]},
                    n=3,
                    k=2,
                )
                outcome = baselines.run_fixed_weight_subset_sampling(
                    public_input,
                    config,
                    self.rng(baselines.FIXED_WEIGHT_SUBSET_SAMPLING),
                )
                self.assertEqual(outcome.candidate_evaluations, budget)
                self.assertEqual(outcome.objective_evaluations, budget)
                self.assertEqual(outcome.exact_verifications, budget)
                self.assertEqual(outcome.iterations, budget)

            with self.subTest(baseline="lee-brickell", budget=budget):
                config = baselines.algorithm_config(
                    "smoke",
                    baselines.LEE_BRICKELL_ISD,
                    overrides={
                        "candidate_budget": budget,
                        "information_set_budget": 1,
                        "max_information_weight": 1,
                    },
                    n=3,
                    k=2,
                )
                outcome = baselines.run_lee_brickell_isd(
                    public_input,
                    config,
                    self.rng(baselines.LEE_BRICKELL_ISD),
                )
                self.assertEqual(outcome.candidate_evaluations, budget)
                self.assertEqual(outcome.objective_evaluations, budget)
                self.assertEqual(outcome.exact_verifications, budget)
                self.assertLessEqual(outcome.information_set_attempts, 1)
                if budget:
                    self.assertEqual(outcome.information_set_attempts, 1)
                    self.assertEqual(outcome.information_sets_accepted, 1)

    def test_lee_brickell_information_set_budget_zero(self) -> None:
        public_input = {"H_rows": ["111"], "W": 2}
        config = baselines.algorithm_config(
            "smoke",
            baselines.LEE_BRICKELL_ISD,
            overrides={
                "candidate_budget": 1,
                "information_set_budget": 0,
                "max_information_weight": 1,
            },
            n=3,
            k=2,
        )
        outcome = baselines.run_lee_brickell_isd(
            public_input,
            config,
            self.rng(baselines.LEE_BRICKELL_ISD),
        )
        self.assertEqual(outcome.candidate_evaluations, 0)
        self.assertEqual(outcome.information_set_attempts, 0)
        self.assertEqual(outcome.termination_reason, "information_set_budget_exhausted")

    def test_lee_systematic_algebra_singularity_and_at_most_p_patterns(self) -> None:
        h_strings = ["1011", "0110"]
        h_rows, n = baselines.parse_h_rows(h_strings)
        independent_rows, _ = baselines.rref_bit_rows(h_rows, n)

        systematic = baselines._systematic_codeword_basis(
            independent_rows,
            h_rows,
            n,
            [0, 1],
        )
        self.assertIsNotNone(systematic)
        basis, information_coordinates = systematic
        self.assertEqual(information_coordinates, [2, 3])
        expected_basis = [
            (1 << 0) | (1 << 1) | (1 << 2),
            (1 << 0) | (1 << 3),
        ]
        self.assertEqual(basis, expected_basis)
        for vector in basis:
            self.assertGreater(baselines.verify_nonzero_kernel_word(h_rows, n, vector), 0)

        singular = baselines._systematic_codeword_basis(
            independent_rows,
            h_rows,
            n,
            [0, 3],
        )
        self.assertIsNone(singular)

        class FixedInformationSetRng:
            randbits_calls = 0
            randbelow_calls = 0
            sha256_blocks_generated = 0

            def sample_subset(self, population_size: int, sample_size: int) -> list[int]:
                if (population_size, sample_size) != (4, 2):
                    raise AssertionError("unexpected information-set request")
                return [0, 1]

        config = baselines.algorithm_config(
            "smoke",
            baselines.LEE_BRICKELL_ISD,
            overrides={
                "candidate_budget": 3,
                "information_set_budget": 1,
                "max_information_weight": 2,
            },
            n=4,
            k=2,
        )
        outcome = baselines.run_lee_brickell_isd(
            {"H_rows": h_strings, "W": 2},
            config,
            FixedInformationSetRng(),
        )
        self.assertEqual(outcome.candidate_evaluations, 3)
        self.assertEqual(outcome.valid_codewords_seen, 3)
        self.assertEqual(outcome.information_sets_accepted, 1)
        # With p=2 the implementation must enumerate the two weight-1 patterns
        # before the single weight-2 pattern.  Only the second weight-1 pattern
        # yields this weight-2 incumbent; exact-weight-2-only enumeration would
        # instead see just the weight-3 XOR of both basis vectors.
        self.assertEqual(outcome.best_candidate, expected_basis[1])
        self.assertEqual(outcome.best_candidate.bit_count(), 2)
        self.assertEqual(outcome.threshold_witnesses_seen, 1)
        self.assertEqual(
            outcome.diagnostics["pattern_scope"],
            "all_nonzero_information_patterns_of_weight_at_most_p_per_accepted_set",
        )

    @unittest.skipUnless(ORTOOLS_AVAILABLE, "OR-Tools is not installed")
    def test_actual_cp_sat_matches_bruteforce_on_tiny_even_parity_code(self) -> None:
        h_strings = ["11"]
        h_rows, n = baselines.parse_h_rows(h_strings)
        config = baselines.algorithm_config(
            "smoke",
            baselines.CP_SAT_THRESHOLD_REFERENCE,
            n=n,
            k=1,
        )

        def brute_force(W: int) -> list[int]:
            return [
                word
                for word in range(1, 1 << n)
                if word.bit_count() <= W and baselines.syndrome_is_zero(h_rows, word)
            ]

        for W in (2, 1):
            with self.subTest(W=W):
                witnesses = brute_force(W)
                outcome = baselines.run_cp_sat_threshold_reference(
                    {"H_rows": h_strings, "W": W},
                    config,
                    declared_seed=101,
                )
                expected_status = "FEASIBLE" if witnesses else "INFEASIBLE"
                self.assertEqual(outcome.solver_status, expected_status)
                self.assertEqual(outcome.solver_calls, 1)
                if witnesses:
                    self.assertEqual(witnesses, [0b11])
                    self.assertEqual(outcome.best_candidate, 0b11)
                    self.assertEqual(
                        baselines.verify_nonzero_kernel_word(h_rows, n, outcome.best_candidate),
                        2,
                    )
                    self.assertEqual(outcome.candidate_evaluations, 1)
                    self.assertFalse(outcome.threshold_infeasibility_certified)
                else:
                    self.assertIsNone(outcome.best_candidate)
                    self.assertEqual(outcome.candidate_evaluations, 0)
                    self.assertTrue(outcome.threshold_infeasibility_certified)

    def test_genuine_integer_validation_rejects_bool_float_and_negative(self) -> None:
        invalid_values = (True, False, 1.0, -1)
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(baselines.BaselineValidationError):
                    baselines.genuine_int(value, "budget", minimum=0)

        case = self.case("hnrv1-c0001")
        base = baselines.algorithm_config(
            "smoke",
            baselines.UNIFORM_KERNEL_SAMPLING,
            n=case["n"],
            k=case["k"],
        )
        for value in (True, 1.0, -1):
            bad = copy.deepcopy(base)
            bad["candidate_budget"] = value
            with self.subTest(config_budget=value):
                with self.assertRaises(baselines.BaselineValidationError):
                    baselines.validate_algorithm_config(
                        baselines.UNIFORM_KERNEL_SAMPLING,
                        bad,
                        n=case["n"],
                        k=case["k"],
                    )

    def test_normalized_records_replay_deterministically(self) -> None:
        for baseline_id in baselines.DEFAULT_BASELINE_IDS:
            with self.subTest(baseline_id=baseline_id):
                first = self.run_record(baseline_id, fresh=True)
                second = self.run_record(baseline_id, fresh=True)
                first_core = baselines.canonical_json_bytes(baselines.reproducible_core(first))
                second_core = baselines.canonical_json_bytes(baselines.reproducible_core(second))
                self.assertEqual(first_core, second_core)
                self.assertEqual(
                    first["reproducible_core_sha256"],
                    second["reproducible_core_sha256"],
                )

    def test_record_budget_off_by_one_is_rejected_for_each_solver_disabled_baseline(self) -> None:
        for baseline_id in baselines.DEFAULT_BASELINE_IDS:
            with self.subTest(baseline_id=baseline_id):
                record = self.run_record(baseline_id)
                over_budget = record["algorithm_config"]["candidate_budget"] + 1
                record["candidate_evaluations"] = over_budget
                record["objective_evaluations"] = over_budget
                record["exact_verifications"] = over_budget
                if baseline_id != baselines.LEE_BRICKELL_ISD:
                    record["iterations"] = over_budget
                self.rehash(record)
                self.assert_record_rejected(record)

    def test_canonical_jsonl_roundtrip_and_duplicate_identity_rejection(self) -> None:
        record = self.run_record()
        line = self.canonical_line(record)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            valid = self.write_bytes(directory, "valid.jsonl", line)
            loaded = baselines.read_validate_jsonl(
                valid,
                self.manifest,
                manifest_path=MANIFEST_PATH,
                check_current_source=True,
            )
            self.assertEqual(len(loaded), 1)

            duplicate = self.write_bytes(directory, "duplicate.jsonl", line + line)
            with self.assertRaises(baselines.BaselineValidationError):
                baselines.read_validate_jsonl(duplicate, self.manifest, manifest_path=MANIFEST_PATH)

    def test_malformed_and_noncanonical_jsonl_are_rejected(self) -> None:
        record = self.run_record()
        canonical = baselines.canonical_json_bytes(record)
        noncanonical = json.dumps(record, sort_keys=True, allow_nan=False).encode("ascii") + b"\n"
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            cases = {
                "malformed.jsonl": b"{\n",
                "duplicate-key.jsonl": b'{"a":1,"a":2}\n',
                "noncanonical.jsonl": noncanonical,
                "missing-newline.jsonl": canonical,
                "blank-line.jsonl": canonical + b"\n\n",
            }
            for name, payload in cases.items():
                with self.subTest(name=name):
                    path = self.write_bytes(directory, name, payload)
                    with self.assertRaises((baselines.BaselineValidationError, ValueError)):
                        baselines.read_validate_jsonl(path, self.manifest, manifest_path=MANIFEST_PATH)

    def test_candidate_hash_weight_and_membership_tampering_are_rejected(self) -> None:
        original = self.run_record()
        self.assertIsNotNone(original["best_candidate_bits"])

        bad_hash = copy.deepcopy(original)
        bad_hash["best_candidate_sha256"] = "0" * 64
        self.rehash(bad_hash)
        self.assert_record_rejected(bad_hash)

        bad_weight = copy.deepcopy(original)
        bad_weight["best_weight"] += 1
        self.rehash(bad_weight)
        self.assert_record_rejected(bad_weight)

        bad_candidate = copy.deepcopy(original)
        unit = "1" + "0" * (bad_candidate["n"] - 1)
        bad_candidate["best_candidate_bits"] = unit
        bad_candidate["best_candidate_sha256"] = baselines.candidate_sha256(unit)
        bad_candidate["best_weight"] = 1
        bad_candidate["witness_verified"] = True
        bad_candidate["threshold_hit"] = True
        bad_candidate["threshold_witnesses_seen"] = max(1, bad_candidate["threshold_witnesses_seen"])
        self.rehash(bad_candidate)
        self.assert_record_rejected(bad_candidate)

    def test_H_manifest_and_source_tampering_are_rejected(self) -> None:
        original = self.run_record()

        bad_h = copy.deepcopy(original)
        bad_h["H_sha256"] = "0" * 64
        self.rehash(bad_h)
        self.assert_record_rejected(bad_h)

        bad_manifest = copy.deepcopy(original)
        bad_manifest["manifest_sha256"] = "0" * 64
        self.rehash(bad_manifest)
        self.assert_record_rejected(bad_manifest)

        bad_source = copy.deepcopy(original)
        bad_source["source"]["baseline_module_sha256"] = "0" * 64
        self.rehash(bad_source)
        self.assert_record_rejected(bad_source, check_current_source=True)

        tampered_manifest = copy.deepcopy(self.manifest)
        tampered_manifest["cases"][0]["H_rows"][0] = (
            ("0" if tampered_manifest["cases"][0]["H_rows"][0][0] == "1" else "1")
            + tampered_manifest["cases"][0]["H_rows"][0][1:]
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps(tampered_manifest), encoding="utf-8")
            with self.assertRaises((baselines.BaselineValidationError, ValueError)):
                baselines.load_manifest(path)

    def test_solver_disabled_solver_fields_are_rejected(self) -> None:
        original = self.run_record()
        mutations = (
            {"solver_calls": 1},
            {"solver_time_s": 0.01},
            {"solver_status": "FEASIBLE", "solver_status_raw": "OPTIMAL"},
            {"threshold_infeasibility_certified": True},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                record = copy.deepcopy(original)
                record.update(mutation)
                self.rehash(record)
                self.assert_record_rejected(record)

    def test_dependency_independent_cp_sat_valid_status_semantics(self) -> None:
        hamming_word = (1 << 0) | (1 << 1) | (1 << 2)
        feasible = self.cp_sat_record(
            "FEASIBLE",
            case_id="hnrv1-c0001",
            candidate_word=hamming_word,
        )
        infeasible = self.cp_sat_record("INFEASIBLE", case_id="hnrv1-c0012")
        unknown = self.cp_sat_record("UNKNOWN", case_id="hnrv1-c0012")

        for name, record in (
            ("feasible", feasible),
            ("infeasible", infeasible),
            ("unknown", unknown),
        ):
            with self.subTest(name=name):
                baselines.validate_result_record(
                    record,
                    self.manifest,
                    manifest_path=MANIFEST_PATH,
                    check_current_source=True,
                )
        self.assertTrue(feasible["threshold_hit"])
        self.assertEqual(feasible["known_distance_gap"], 0)
        self.assertTrue(infeasible["threshold_infeasibility_certified"])
        self.assertIsNone(infeasible["known_distance_gap"])
        self.assertFalse(unknown["threshold_infeasibility_certified"])
        self.assertEqual(unknown["conservative_status"], "INCONCLUSIVE")

    def test_cp_sat_feasible_requires_a_valid_verified_witness(self) -> None:
        valid = self.cp_sat_record(
            "FEASIBLE",
            case_id="hnrv1-c0001",
            candidate_word=(1 << 0) | (1 << 1) | (1 << 2),
        )

        without_witness = copy.deepcopy(valid)
        without_witness.update(
            {
                "best_candidate_bits": None,
                "best_candidate_sha256": None,
                "best_weight": None,
                "witness_verified": False,
                "threshold_hit": False,
                "known_distance_gap": None,
                "candidate_evaluations": 0,
                "objective_evaluations": 0,
                "exact_verifications": 0,
                "valid_codewords_seen": 0,
                "threshold_witnesses_seen": 0,
            }
        )
        self.rehash(without_witness)
        self.assert_record_rejected(without_witness)

        invalid_witness = copy.deepcopy(valid)
        unit = "1" + "0" * (invalid_witness["n"] - 1)
        invalid_witness["best_candidate_bits"] = unit
        invalid_witness["best_candidate_sha256"] = baselines.candidate_sha256(unit)
        invalid_witness["best_weight"] = 1
        invalid_witness["known_distance_gap"] = 0
        self.rehash(invalid_witness)
        self.assert_record_rejected(invalid_witness)

    def test_cp_sat_infeasible_and_unknown_forgeries_are_rejected(self) -> None:
        infeasible = self.cp_sat_record("INFEASIBLE", case_id="hnrv1-c0012")
        h_rows, n = baselines.parse_h_rows(self.case("hnrv1-c0012")["H_rows"])
        basis, _, _ = baselines.deterministic_kernel_basis(h_rows, n)
        candidate = basis[0]
        weight = baselines.verify_nonzero_kernel_word(h_rows, n, candidate)
        bits = baselines.word_to_bits(candidate, n)

        with_witness = copy.deepcopy(infeasible)
        with_witness.update(
            {
                "best_candidate_bits": bits,
                "best_candidate_sha256": baselines.candidate_sha256(bits),
                "best_weight": weight,
                "witness_verified": True,
                "threshold_hit": weight <= with_witness["public_W"],
                "candidate_evaluations": 1,
                "objective_evaluations": 1,
                "exact_verifications": 1,
                "valid_codewords_seen": 1,
                "threshold_witnesses_seen": int(weight <= with_witness["public_W"]),
            }
        )
        self.rehash(with_witness)
        self.assert_record_rejected(with_witness)

        unknown = self.cp_sat_record("UNKNOWN", case_id="hnrv1-c0012")
        unknown["threshold_infeasibility_certified"] = True
        self.rehash(unknown)
        self.assert_record_rejected(unknown)

    def test_cp_sat_raw_status_and_termination_mismatches_are_rejected(self) -> None:
        valid_records = (
            self.cp_sat_record(
                "FEASIBLE",
                case_id="hnrv1-c0001",
                candidate_word=(1 << 0) | (1 << 1) | (1 << 2),
            ),
            self.cp_sat_record("INFEASIBLE", case_id="hnrv1-c0012"),
            self.cp_sat_record("UNKNOWN", case_id="hnrv1-c0012"),
        )
        mismatched_raw = ("INFEASIBLE", "UNKNOWN", "FEASIBLE")
        for record, raw in zip(valid_records, mismatched_raw):
            with self.subTest(status=record["solver_status"], mutation="raw"):
                tampered = copy.deepcopy(record)
                tampered["solver_status_raw"] = raw
                self.rehash(tampered)
                self.assert_record_rejected(tampered)
            with self.subTest(status=record["solver_status"], mutation="termination"):
                tampered = copy.deepcopy(record)
                tampered["termination_reason"] = "solver_wrong_termination"
                self.rehash(tampered)
                self.assert_record_rejected(tampered)

    def test_solver_disabled_and_solver_assisted_strata_cannot_be_mixed(self) -> None:
        cp_record = self.cp_sat_record("UNKNOWN", case_id="hnrv1-c0012")
        cp_record["solver_stratum"] = "solver_disabled"
        self.rehash(cp_record)
        self.assert_record_rejected(cp_record)

        solver_disabled = self.run_record()
        solver_disabled["solver_stratum"] = "solver_assisted_reference"
        self.rehash(solver_disabled)
        self.assert_record_rejected(solver_disabled)

    def test_float_best_weight_and_known_gap_are_rejected(self) -> None:
        record = self.known_control_record(
            "hnrv1-c0001",
            (1 << 0) | (1 << 1) | (1 << 2),
            profile_id="smoke",
        )
        float_weight = copy.deepcopy(record)
        float_weight["best_weight"] = 3.0
        self.rehash(float_weight)
        self.assert_record_rejected(float_weight)

        float_gap = copy.deepcopy(record)
        float_gap["known_distance_gap"] = 0.0
        self.rehash(float_gap)
        self.assert_record_rejected(float_gap)

    def test_fake_git_commit_and_dirty_flag_fail_current_source_check(self) -> None:
        original = self.run_record()
        commit = original["source"]["git_commit_sha"]
        oid_length = len(commit) if isinstance(commit, str) else 40
        fake_commit = copy.deepcopy(original)
        fake_commit["source"]["git_commit_sha"] = "0" * oid_length
        self.rehash(fake_commit)
        self.assert_record_rejected(fake_commit, check_current_source=True)

        fake_dirty = copy.deepcopy(original)
        current_dirty = fake_dirty["source"]["git_dirty"]
        fake_dirty["source"]["git_dirty"] = False if current_dirty is None else not current_dirty
        self.rehash(fake_dirty)
        self.assert_record_rejected(fake_dirty, check_current_source=True)

    def test_forged_lee_accepted_set_accounting_is_rejected(self) -> None:
        valid = self.synthetic_lee_record()
        baselines.validate_result_record(valid, self.manifest, manifest_path=MANIFEST_PATH)

        forged = copy.deepcopy(valid)
        forged["information_sets_accepted"] += 1
        forged["information_set_attempts"] += 1
        forged["iterations"] += 1
        self.rehash(forged)
        self.assert_record_rejected(forged)

    def test_hamming_extended_hamming_and_all_rm_controls(self) -> None:
        hamming = self.case("hnrv1-c0001")
        h_rows, n = baselines.parse_h_rows(hamming["H_rows"])
        hamming_word = (1 << 0) | (1 << 1) | (1 << 2)
        self.assertEqual(baselines.verify_nonzero_kernel_word(h_rows, n, hamming_word), 3)
        hamming_record = self.known_control_record("hnrv1-c0001", hamming_word, profile_id="smoke")
        self.assertEqual(hamming_record["known_distance_gap"], 0)
        baselines.validate_result_record(hamming_record, self.manifest, manifest_path=MANIFEST_PATH)

        extended = self.case("hnrv1-c0002")
        h_rows, n = baselines.parse_h_rows(extended["H_rows"])
        extended_word = hamming_word | (1 << (n - 1))
        self.assertEqual(baselines.verify_nonzero_kernel_word(h_rows, n, extended_word), 4)
        extended_record = self.known_control_record("hnrv1-c0002", extended_word, profile_id="full")
        self.assertEqual(extended_record["known_distance_gap"], 0)
        baselines.validate_result_record(extended_record, self.manifest, manifest_path=MANIFEST_PATH)

        for case_id, m, expected_distance in (
            ("hnrv1-c0009", 5, 16),
            ("hnrv1-c0010", 6, 32),
            ("hnrv1-c0011", 7, 64),
        ):
            with self.subTest(case_id=case_id):
                case = self.case(case_id)
                h_rows, n = baselines.parse_h_rows(case["H_rows"])
                rm_word = baselines.bits_to_word(corpus.rm1_generator_rows(m)[1])
                self.assertEqual(
                    baselines.verify_nonzero_kernel_word(h_rows, n, rm_word),
                    expected_distance,
                )
                profile_id = "smoke" if "smoke" in case["subset"] else "full"
                record = self.known_control_record(case_id, rm_word, profile_id=profile_id)
                self.assertEqual(record["known_distance_gap"], 0)
                baselines.validate_result_record(record, self.manifest, manifest_path=MANIFEST_PATH)

    def test_unknown_distance_record_never_has_a_known_distance_gap(self) -> None:
        record = self.run_record(
            baselines.UNIFORM_KERNEL_SAMPLING,
            case_id="hnrv1-c0012",
            profile_id="smoke",
        )
        self.assertIsNotNone(record["best_candidate_bits"])
        self.assertIsNone(record["known_distance_gap"])
        self.assertFalse(record["optimality_claim"])
        baselines.validate_result_record(record, self.manifest, manifest_path=MANIFEST_PATH)

        tampered = copy.deepcopy(record)
        tampered["known_distance_gap"] = 0
        self.rehash(tampered)
        self.assert_record_rejected(tampered)

    def test_validation_remains_active_in_python_optimized_mode(self) -> None:
        good = self.run_record()
        bad = copy.deepcopy(good)
        bad["solver_calls"] = 1
        self.rehash(bad)

        script = (
            "import pathlib,sys; "
            "import rlmw_research_baselines as b; "
            "m=b.load_manifest(sys.argv[2]); "
            "b.read_validate_jsonl(sys.argv[1],m,manifest_path=pathlib.Path(sys.argv[2]),"
            "check_current_source=True)"
        )
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            good_path = self.write_bytes(directory, "good.jsonl", self.canonical_line(good))
            bad_path = self.write_bytes(directory, "bad.jsonl", self.canonical_line(bad))
            valid = subprocess.run(
                [sys.executable, "-O", "-c", script, str(good_path), str(MANIFEST_PATH)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(valid.returncode, 0, msg=valid.stdout + valid.stderr)
            invalid = subprocess.run(
                [sys.executable, "-O", "-c", script, str(bad_path), str(MANIFEST_PATH)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(invalid.returncode, 0, msg="tampered record passed under python -O")


if __name__ == "__main__":
    unittest.main()
