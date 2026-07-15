"""Deterministic Stern/Dumer-style collision ISD baseline for h-native-research-v2.

This module is standalone solver-disabled classical infrastructure.  It does
not freeze a v2 corpus manifest, thresholds, calibration results, final seeds,
CP-SAT evidence, neural/RL comparisons, or v1 baseline contracts.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import rlmw_research_corpus_v2 as corpus_v2

PROTOCOL_VERSION = "h-native-research-isd-v2-baselines-v1"
RESULT_SCHEMA_VERSION = "rlmw-research-isd-v2-result-v1"
ALGORITHM_ID = "stern_dumer_collision_isd_v1"
IMPLEMENTATION_VERSION = "1.0.0"
PRNG_VERSION = "sha256-ctr-v1"
SOLVER_STRATUM = "solver_disabled"
THREAD_COUNT = 1
RESULT_FILENAME = "rlmw_research_isd_v2_results.jsonl"
CANDIDATE_PROTOCOL_VERSION = corpus_v2.PROTOCOL_ID
CANDIDATE_GENERATOR_ID = corpus_v2.GENERATOR_ID
BUDGET_LADDER = (1 << 12, 1 << 14, 1 << 16, 1 << 18)
SEED_ROLES = ("threshold_fit_seed", "tier_validation_seed")
PHASES = ("threshold_fit", "tier_validation")
PHASE_SEED_ROLE = {"threshold_fit": "threshold_fit_seed", "tier_validation": "tier_validation_seed"}
ALLOWED_TERMINATION_REASONS = {"candidate_budget_exhausted", "information_set_limit_exhausted", "resource_limit", "trivial_code_no_nonzero_word", "search_exhausted_no_more_work"}
OBSERVATIONAL_FIELDS = {"runtime_s", "environment"}

PROFILE_SPECS: dict[str, dict[str, Any]] = {
    "smoke": {
        "budgets": [8, 16],
        "algorithm_config": {
            "num_threads": 1,
            "left_weight": 1,
            "right_weight": 1,
            "projection_bits": 2,
            "information_set_limit": 8,
            "max_left_list_entries": 64,
            "max_right_list_entries": 64,
            "max_collision_pairs": 128,
            "max_projection_operations": 512,
            "exhaust_candidate_budget": True,
        },
        "runs": [{"phase": "threshold_fit", "seed_role": "threshold_fit_seed", "seed_index": 0}],
    },
    "calibration": {
        "budgets": list(BUDGET_LADDER),
        "algorithm_config": {
            "num_threads": 1,
            "left_weight": 2,
            "right_weight": 2,
            "projection_bits": "min(8,rank)",
            "information_set_limit": 4096,
            "max_left_list_entries": 200000,
            "max_right_list_entries": 200000,
            "max_collision_pairs": 4000000,
            "max_projection_operations": 20000000,
            "exhaust_candidate_budget": True,
        },
        "runs": [
            {"phase": phase, "seed_role": role, "seed_index": i}
            for phase, role in (("threshold_fit", "threshold_fit_seed"), ("tier_validation", "tier_validation_seed"))
            for i in range(8)
        ],
    },
}

FIXTURE_CASES = {
    "isdv2-fixture-hamming7": {"case_id": "isdv2-fixture-hamming7", "H_rows": ["1010101", "0110011", "0001111"], "W": 3},
    "isdv2-fixture-even6": {"case_id": "isdv2-fixture-even6", "H_rows": ["111111"], "W": 2},
}

class ISDValidationError(ValueError):
    """Raised for v2 ISD protocol, schema, or validation errors."""

def fail(message: str) -> None:
    raise ISDValidationError(f"{PROTOCOL_VERSION} validation error: {message}")

def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)

def genuine_int(value: Any, name: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    require(isinstance(value, int) and not isinstance(value, bool), f"{name} must be a genuine integer")
    if minimum is not None:
        require(value >= minimum, f"{name} must be >= {minimum}")
    if maximum is not None:
        require(value <= maximum, f"{name} must be <= {maximum}")
    return int(value)

def finite_nonnegative_real(value: Any, name: str) -> float:
    require(isinstance(value, (int, float)) and not isinstance(value, bool), f"{name} must be a finite nonnegative real")
    number = float(value)
    require(math.isfinite(number) and number >= 0.0, f"{name} must be a finite nonnegative real")
    return number

def expected_seed_role(phase: str) -> str:
    require(phase in PHASES, "unknown phase")
    return PHASE_SEED_ROLE[phase]

def require_phase_seed_pair(phase: str, seed_role: str) -> None:
    require(seed_role == expected_seed_role(phase), "phase and seed_role are not the frozen allowed pair")

def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError) as exc:
        fail(f"value is not canonical JSON: {exc}")

def sha256_object(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()

def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)

def parse_h_rows(row_strings: Sequence[str]) -> tuple[list[int], int]:
    require(isinstance(row_strings, list) and row_strings, "H_rows must be a nonempty list")
    n = len(row_strings[0])
    require(n > 0, "H rows must be nonempty")
    rows: list[int] = []
    for row in row_strings:
        require(isinstance(row, str) and len(row) == n and set(row) <= {"0", "1"}, "H_rows must be equal-length binary strings")
        val = 0
        for j, ch in enumerate(row):
            if ch == "1":
                val |= 1 << j
        rows.append(val)
    return rows, n

def word_to_bits(word: int, n: int) -> str:
    return "".join("1" if (word >> j) & 1 else "0" for j in range(n))

def bits_to_word(bits: str) -> int:
    require(isinstance(bits, str) and set(bits) <= {"0", "1"}, "bits must be binary string")
    out = 0
    for j, ch in enumerate(bits):
        if ch == "1":
            out |= 1 << j
    return out

def public_h_sha256(row_strings: Sequence[str]) -> str:
    matrix = corpus_v2.BinaryMatrix.from_row_strings(list(row_strings))
    return corpus_v2.typed_digest(("public_h_hash", corpus_v2.PROTOCOL_ID, len(row_strings), len(row_strings[0]) if row_strings else 0, matrix))

def rref_bit_rows(rows: Sequence[int], n: int) -> tuple[list[int], list[int]]:
    vals = list(rows)
    rank = 0
    pivots: list[int] = []
    for col in range(n):
        pivot = next((i for i in range(rank, len(vals)) if (vals[i] >> col) & 1), None)
        if pivot is None:
            continue
        vals[rank], vals[pivot] = vals[pivot], vals[rank]
        for i in range(len(vals)):
            if i != rank and ((vals[i] >> col) & 1):
                vals[i] ^= vals[rank]
        pivots.append(col)
        rank += 1
    return vals[:rank], pivots

def gf2_rank(rows: Sequence[int], n: int) -> int:
    return len(rref_bit_rows(rows, n)[0])

def invert_square_bit_matrix(rows: Sequence[int], dim: int) -> list[int] | None:
    left = list(rows)
    right = [1 << i for i in range(dim)]
    rank = 0
    for col in range(dim):
        pivot = next((i for i in range(rank, dim) if (left[i] >> col) & 1), None)
        if pivot is None:
            return None
        left[rank], left[pivot] = left[pivot], left[rank]
        right[rank], right[pivot] = right[pivot], right[rank]
        for i in range(dim):
            if i != rank and ((left[i] >> col) & 1):
                left[i] ^= left[rank]
                right[i] ^= right[rank]
        rank += 1
    return right

def verify_nonzero_kernel_word(h_rows: Sequence[int], n: int, word: int) -> int:
    require(isinstance(word, int) and not isinstance(word, bool) and 0 <= word < (1 << n), "candidate word out of range")
    require(word != 0, "zero word is forbidden")
    for row in h_rows:
        require(((row & word).bit_count() & 1) == 0, "candidate is not in the original public kernel")
    return word.bit_count()

def syndrome_is_zero(h_rows: Sequence[int], word: int) -> bool:
    return all(((row & word).bit_count() & 1) == 0 for row in h_rows)

class Sha256CounterRng:
    def __init__(self, key: bytes):
        require(isinstance(key, bytes) and len(key) == 32, "rng key must be 32 bytes")
        self.key = key
        self.counter = 0
        self.pool = b""
        self.randbits_calls = 0
        self.randbelow_calls = 0
        self.sha256_blocks_generated = 0
    def _block(self) -> bytes:
        block = hashlib.sha256(self.key + self.counter.to_bytes(16, "big")).digest()
        self.counter += 1
        self.sha256_blocks_generated += 1
        return block
    def randbits(self, bits: int) -> int:
        genuine_int(bits, "bits", minimum=0)
        self.randbits_calls += 1
        nbytes = (bits + 7) // 8
        while len(self.pool) < nbytes:
            self.pool += self._block()
        raw, self.pool = self.pool[:nbytes], self.pool[nbytes:]
        value = int.from_bytes(raw, "big")
        extra = nbytes * 8 - bits
        return value >> extra if extra else value
    def randbelow(self, upper: int) -> int:
        genuine_int(upper, "upper", minimum=1)
        self.randbelow_calls += 1
        bits = (upper - 1).bit_length()
        while True:
            value = self.randbits(bits)
            if value < upper:
                return value
    def sample_subset(self, n: int, k: int) -> list[int]:
        genuine_int(n, "n", minimum=0); genuine_int(k, "k", minimum=0, maximum=n)
        arr = list(range(n))
        for i in range(k):
            j = i + self.randbelow(n - i)
            arr[i], arr[j] = arr[j], arr[i]
        return sorted(arr[:k])

def calibration_seed_bytes(role: str, index: int) -> bytes:
    require(role in SEED_ROLES, "unknown or forbidden seed role")
    genuine_int(index, "seed_index", minimum=0, maximum=7)
    return bytes.fromhex(corpus_v2.calibration_seed(role, index))

def derive_rng_key(*, case_id: str, public_h_hash: str, phase: str, seed_role: str, seed_index: int, budget: int, config_digest: str) -> bytes:
    require(phase in PHASES, "unknown phase")
    require_phase_seed_pair(phase, seed_role)
    seed = calibration_seed_bytes(seed_role, seed_index)
    material = {
        "protocol_version": PROTOCOL_VERSION,
        "algorithm_id": ALGORITHM_ID,
        "prng_version": PRNG_VERSION,
        "case_id": case_id,
        "public_h_sha256": public_h_hash,
        "phase": phase,
        "seed_role": seed_role,
        "seed_index": seed_index,
        "seed_hex": seed.hex(),
        "budget": genuine_int(budget, "budget", minimum=0),
        "algorithm_config_sha256": config_digest,
    }
    return hashlib.sha256(canonical_json_bytes(material)).digest()

def resolved_config(config: dict[str, Any], *, rank: int) -> dict[str, Any]:
    allowed = {"num_threads","left_weight","right_weight","projection_bits","information_set_limit","max_left_list_entries","max_right_list_entries","max_collision_pairs","max_projection_operations","exhaust_candidate_budget"}
    require(set(config) == allowed, "algorithm_config has missing or unknown fields")
    out = dict(config)
    require(out["num_threads"] == THREAD_COUNT, "num_threads must be 1")
    for name in ("left_weight","right_weight","information_set_limit","max_left_list_entries","max_right_list_entries","max_collision_pairs","max_projection_operations"):
        genuine_int(out[name], name, minimum=0)
    if out["projection_bits"] == "min(8,rank)":
        out["projection_bits"] = min(8, rank)
    else:
        genuine_int(out["projection_bits"], "projection_bits", minimum=0)
    require(isinstance(out["exhaust_candidate_budget"], bool), "exhaust_candidate_budget must be boolean")
    return out

def algorithm_config(profile: str = "smoke", overrides: dict[str, Any] | None = None, *, rank: int = 0) -> dict[str, Any]:
    require(profile in PROFILE_SPECS, "unknown profile")
    cfg = dict(PROFILE_SPECS[profile]["algorithm_config"])
    if overrides:
        cfg.update(overrides)
    return resolved_config(cfg, rank=rank)

def config_digest(config: dict[str, Any]) -> str:
    return sha256_object({"algorithm_id": ALGORITHM_ID, "implementation_version": IMPLEMENTATION_VERSION, "config": config})

@dataclass
class ISDOutcome:
    best_candidate: int | None = None
    best_weight: int | None = None
    information_set_attempts: int = 0
    singular_information_sets: int = 0
    information_sets_accepted: int = 0
    list_entries_left: int = 0
    list_entries_right: int = 0
    projection_operations: int = 0
    bucket_probes: int = 0
    collision_pairs: int = 0
    skipped_collision_pairs: int = 0
    reconstructed_candidates: int = 0
    candidate_evaluations: int = 0
    objective_evaluations: int = 0
    exact_verifications: int = 0
    valid_codewords_seen: int = 0
    threshold_witnesses_seen: int = 0
    duplicate_candidates: int = 0
    resource_limit_events: int = 0
    iterations: int = 0
    termination_reason: str = "not_started"
    diagnostics: dict[str, Any] = field(default_factory=dict)

def _systematic(independent_rows: Sequence[int], original_rows: Sequence[int], n: int, parity_coordinates: Sequence[int]) -> tuple[list[int], list[int], list[int]] | None:
    rank = len(independent_rows)
    square = []
    for row in independent_rows:
        v = 0
        for local, coord in enumerate(parity_coordinates):
            if (row >> coord) & 1:
                v |= 1 << local
        square.append(v)
    inv = invert_square_bit_matrix(square, rank)
    if inv is None:
        return None
    pset = set(parity_coordinates)
    info = [j for j in range(n) if j not in pset]
    basis = []
    for coord in info:
        syndrome = 0
        for i, row in enumerate(independent_rows):
            if (row >> coord) & 1:
                syndrome |= 1 << i
        parity_solution = 0
        for pidx, inv_row in enumerate(inv):
            if (inv_row & syndrome).bit_count() & 1:
                parity_solution |= 1 << parity_coordinates[pidx]
        word = (1 << coord) | parity_solution
        verify_nonzero_kernel_word(original_rows, n, word)
        basis.append(word)
    return basis, info, list(parity_coordinates)

def _projection(word: int, parity_coordinates: Sequence[int], ell: int) -> int:
    val = 0
    for i, coord in enumerate(parity_coordinates[:ell]):
        if (word >> coord) & 1:
            val |= 1 << i
    return val

def validate_public_input(public_input: dict[str, Any]) -> tuple[list[int], int, int]:
    required = {"case_id","H_rows","public_h_sha256","phase","seed_role","seed_index","budget","candidate_protocol_version","candidate_generator_config_sha256","candidate_manifest_sha256"}
    if isinstance(public_input, dict) and public_input.get("phase") == "tier_validation":
        required = set(required) | {"W"}
    require(isinstance(public_input, dict), "public input must be an object")
    require(set(public_input) == required, "public input has missing or unknown fields")
    require(isinstance(public_input["case_id"], str) and public_input["case_id"], "case_id must be nonempty string")
    require(public_input["candidate_protocol_version"] == CANDIDATE_PROTOCOL_VERSION, "wrong candidate protocol version")
    require(is_sha256(public_input["candidate_generator_config_sha256"]), "bad candidate generator digest")
    require(is_sha256(public_input["candidate_manifest_sha256"]), "bad candidate manifest digest")
    require(public_input["phase"] in PHASES, "invalid phase")
    require_phase_seed_pair(public_input["phase"], public_input["seed_role"])
    genuine_int(public_input["seed_index"], "seed_index", minimum=0, maximum=7)
    genuine_int(public_input["budget"], "budget", minimum=0)
    rows, n = parse_h_rows(public_input["H_rows"])
    require(public_input["public_h_sha256"] == public_h_sha256(public_input["H_rows"]), "public H hash mismatch")
    if public_input["phase"] == "tier_validation":
        genuine_int(public_input["W"], "W", minimum=0)
    return rows, n, len(rref_bit_rows(rows, n)[0])

def run_stern_dumer(public_input: dict[str, Any], config: dict[str, Any], rng: Sha256CounterRng) -> ISDOutcome:
    rows, n, rank = validate_public_input(public_input)
    phase = public_input["phase"]
    W = public_input.get("W")
    independent, _ = rref_bit_rows(rows, n)
    k = n - rank
    cfg = resolved_config(config, rank=rank)
    budget = genuine_int(public_input["budget"], "budget", minimum=0)
    outcome = ISDOutcome()
    if k == 0:
        outcome.termination_reason = "trivial_code_no_nonzero_word"
    if budget == 0:
        outcome.termination_reason = "candidate_budget_exhausted"
    seen: set[int] = set()
    left_w = cfg["left_weight"]; right_w = cfg["right_weight"]; ell = min(cfg["projection_bits"], rank)
    if left_w + right_w == 0 or left_w > (k + 1)//2 or right_w > k//2:
        outcome.resource_limit_events += 1
        outcome.termination_reason = "resource_limit"
    while not outcome.termination_reason == "resource_limit" and outcome.candidate_evaluations < budget and outcome.information_set_attempts < cfg["information_set_limit"] and k > 0:
        outcome.information_set_attempts += 1; outcome.iterations += 1
        parity = rng.sample_subset(n, rank)
        sysdata = _systematic(independent, rows, n, parity)
        if sysdata is None:
            outcome.singular_information_sets += 1
            continue
        outcome.information_sets_accepted += 1
        basis, info, parity_coords = sysdata
        split = (k + 1) // 2
        left_idx = list(range(split)); right_idx = list(range(split, k))
        if math.comb(len(left_idx), left_w) > cfg["max_left_list_entries"] or math.comb(len(right_idx), right_w) > cfg["max_right_list_entries"]:
            outcome.resource_limit_events += 1; outcome.termination_reason = "resource_limit"; break
        buckets: dict[int, list[tuple[tuple[int, ...], int]]] = {}
        for supp in itertools.combinations(left_idx, left_w):
            part = 0
            for idx in supp: part ^= basis[idx]
            if outcome.projection_operations >= cfg["max_projection_operations"]:
                outcome.resource_limit_events += 1; outcome.termination_reason = "resource_limit"; break
            key = _projection(part, parity_coords, ell)
            outcome.projection_operations += 1; outcome.list_entries_left += 1
            buckets.setdefault(key, []).append((supp, part))
        if outcome.termination_reason == "resource_limit": break
        for rsupp in itertools.combinations(right_idx, right_w):
            rpart = 0
            for idx in rsupp: rpart ^= basis[idx]
            if outcome.projection_operations >= cfg["max_projection_operations"]:
                outcome.resource_limit_events += 1; outcome.termination_reason = "resource_limit"; break
            key = _projection(rpart, parity_coords, ell)
            outcome.projection_operations += 1; outcome.list_entries_right += 1; outcome.bucket_probes += 1
            for _lsupp, lpart in buckets.get(key, []):
                if outcome.collision_pairs >= cfg["max_collision_pairs"]:
                    outcome.skipped_collision_pairs += 1; outcome.resource_limit_events += 1; outcome.termination_reason = "resource_limit"; break
                outcome.collision_pairs += 1
                candidate = lpart ^ rpart
                # Full parity part has already been computed by XORing full systematic basis words;
                # projected equality is only a bucket filter and never a validity claim.
                weight = verify_nonzero_kernel_word(rows, n, candidate)
                outcome.reconstructed_candidates += 1; outcome.candidate_evaluations += 1
                outcome.objective_evaluations += 1; outcome.exact_verifications += 1; outcome.valid_codewords_seen += 1
                if candidate in seen: outcome.duplicate_candidates += 1
                seen.add(candidate)
                if W is not None and weight <= W: outcome.threshold_witnesses_seen += 1
                if outcome.best_weight is None or weight < outcome.best_weight or (weight == outcome.best_weight and candidate < (outcome.best_candidate or 0)):
                    outcome.best_candidate = candidate; outcome.best_weight = weight
                if outcome.candidate_evaluations >= budget: break
            if outcome.termination_reason == "resource_limit" or outcome.candidate_evaluations >= budget: break
        if outcome.termination_reason == "resource_limit": break
    if outcome.termination_reason == "not_started":
        if outcome.candidate_evaluations >= budget:
            outcome.termination_reason = "candidate_budget_exhausted"
        elif outcome.information_set_attempts >= cfg["information_set_limit"]:
            outcome.termination_reason = "information_set_limit_exhausted"
        else:
            outcome.termination_reason = "search_exhausted_no_more_work"
    outcome.diagnostics = {"rank": rank, "kernel_dimension": k, "projection_bits_effective": ell, "prng_randbits_calls": rng.randbits_calls, "prng_randbelow_calls": rng.randbelow_calls, "prng_sha256_blocks": rng.sha256_blocks_generated, "collision_algorithm": "stern_dumer_projected_bucket_match", "max_projection_operations": cfg["max_projection_operations"], "max_collision_pairs": cfg["max_collision_pairs"], "collision_pairs_semantics": "processed_pairs; skipped_collision_pairs counts first unprocessed pair when cap is reached"}
    return outcome

def source_info() -> dict[str, Any]:
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        commit = None
    return {"source_commit": commit, "isd_module_sha256": sha256_file(Path(__file__)), "python_version": sys.version.split()[0]}

def reproducible_core(record: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in record.items() if k not in OBSERVATIONAL_FIELDS and k != "reproducible_core_sha256"}

def compute_reproducible_core_sha256(record: dict[str, Any]) -> str:
    return sha256_object(reproducible_core(record))

def assemble_record(public_input: dict[str, Any], config: dict[str, Any], outcome: ISDOutcome, *, runtime_s: float = 0.0) -> dict[str, Any]:
    rows, n = parse_h_rows(public_input["H_rows"])
    best_bits = word_to_bits(outcome.best_candidate, n) if outcome.best_candidate is not None else None
    record = {
        "result_schema_version": RESULT_SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "candidate_protocol_version": public_input["candidate_protocol_version"],
        "candidate_generator_config_sha256": public_input["candidate_generator_config_sha256"],
        "candidate_manifest_sha256": public_input["candidate_manifest_sha256"],
        "algorithm_id": ALGORITHM_ID,
        "implementation_version": IMPLEMENTATION_VERSION,
        "solver_stratum": SOLVER_STRATUM,
        "num_threads": THREAD_COUNT,
        "prng_version": PRNG_VERSION,
        "case_id": public_input["case_id"],
        "public_h_sha256": public_input["public_h_sha256"],
        "H_rows": public_input["H_rows"],
        "n": n,
        "rank": len(rref_bit_rows(rows, n)[0]),
        "phase": public_input["phase"],
        "seed_role": public_input["seed_role"],
        "seed_index": public_input["seed_index"],
        "budget": public_input["budget"],
        "W": public_input.get("W"),
        "algorithm_config": config,
        "algorithm_config_sha256": config_digest(config),
        "termination_reason": outcome.termination_reason,
        "information_set_attempts": outcome.information_set_attempts,
        "singular_information_sets": outcome.singular_information_sets,
        "information_sets_accepted": outcome.information_sets_accepted,
        "list_entries_left": outcome.list_entries_left,
        "list_entries_right": outcome.list_entries_right,
        "projection_operations": outcome.projection_operations,
        "bucket_probes": outcome.bucket_probes,
        "collision_pairs": outcome.collision_pairs,
        "skipped_collision_pairs": outcome.skipped_collision_pairs,
        "reconstructed_candidates": outcome.reconstructed_candidates,
        "candidate_evaluations": outcome.candidate_evaluations,
        "objective_evaluations": outcome.objective_evaluations,
        "exact_verifications": outcome.exact_verifications,
        "valid_codewords_seen": outcome.valid_codewords_seen,
        "threshold_witnesses_seen": outcome.threshold_witnesses_seen,
        "duplicate_candidates": outcome.duplicate_candidates,
        "resource_limit_events": outcome.resource_limit_events,
        "best_candidate_bits": best_bits,
        "best_candidate_sha256": sha256_object({"candidate_bits": best_bits}) if best_bits is not None else None,
        "best_weight": outcome.best_weight,
        "witness_verified": best_bits is not None,
        "threshold_hit": bool(public_input.get("W") is not None and outcome.best_weight is not None and outcome.best_weight <= public_input["W"]),
        "diagnostics": outcome.diagnostics,
        "source": source_info(),
        "runtime_s": runtime_s,
        "environment": {"platform": sys.platform},
    }
    record["reproducible_core_sha256"] = compute_reproducible_core_sha256(record)
    return record

def make_public_input(case: dict[str, Any], *, phase: str, seed_role: str, seed_index: int, budget: int, W: int | None = None) -> dict[str, Any]:
    require(set(case) <= {"case_id","H_rows","W"}, "fixture/public case contains forbidden keys")
    genuine_int(seed_index, "seed_index", minimum=0, maximum=7); genuine_int(budget, "budget", minimum=0)
    require(seed_role in SEED_ROLES, "unknown seed role"); require(phase in PHASES, "unknown phase"); require_phase_seed_pair(phase, seed_role)
    pi = {"case_id": case["case_id"], "H_rows": case["H_rows"], "public_h_sha256": public_h_sha256(case["H_rows"]), "phase": phase, "seed_role": seed_role, "seed_index": seed_index, "budget": budget, "candidate_protocol_version": CANDIDATE_PROTOCOL_VERSION, "candidate_generator_config_sha256": corpus_v2.config_digest(), "candidate_manifest_sha256": "0"*64}
    if phase == "tier_validation":
        pi["W"] = genuine_int(W if W is not None else case.get("W"), "W", minimum=0)
    else:
        require(W is None, "threshold_fit mode must not receive W")
    return pi

def run_record(public_input: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    rows, n, rank = validate_public_input(public_input)
    cfg = resolved_config(config, rank=rank)
    cd = config_digest(cfg)
    key = derive_rng_key(case_id=public_input["case_id"], public_h_hash=public_input["public_h_sha256"], phase=public_input["phase"], seed_role=public_input["seed_role"], seed_index=public_input["seed_index"], budget=public_input["budget"], config_digest=cd)
    start = time.perf_counter(); outcome = run_stern_dumer(public_input, cfg, Sha256CounterRng(key))
    return assemble_record(public_input, cfg, outcome, runtime_s=time.perf_counter()-start)

REQUIRED_RECORD_KEYS = None

def validate_result_record(record: dict[str, Any], *, check_current_source: bool = False) -> None:
    require(isinstance(record, dict), "record must be object")
    forbidden = {"solver_status", "solver_status_raw", "cp_sat_status", "exact_distance", "certified_lower_bound", "optimality_certified"}
    require(not (set(record) & forbidden), "solver-assisted or certificate fields are forbidden")
    required = {"result_schema_version","protocol_version","candidate_protocol_version","candidate_generator_config_sha256","candidate_manifest_sha256","algorithm_id","implementation_version","solver_stratum","num_threads","prng_version","case_id","public_h_sha256","H_rows","n","rank","phase","seed_role","seed_index","budget","W","algorithm_config","algorithm_config_sha256","termination_reason","information_set_attempts","singular_information_sets","information_sets_accepted","list_entries_left","list_entries_right","projection_operations","bucket_probes","collision_pairs","skipped_collision_pairs","reconstructed_candidates","candidate_evaluations","objective_evaluations","exact_verifications","valid_codewords_seen","threshold_witnesses_seen","duplicate_candidates","resource_limit_events","best_candidate_bits","best_candidate_sha256","best_weight","witness_verified","threshold_hit","diagnostics","source","runtime_s","environment","reproducible_core_sha256"}
    require(set(record) == required, "record has missing or unknown fields")
    require(record["result_schema_version"] == RESULT_SCHEMA_VERSION, "wrong result schema")
    require(record["protocol_version"] == PROTOCOL_VERSION and record["algorithm_id"] == ALGORITHM_ID, "wrong protocol/algorithm")
    require(record["candidate_protocol_version"] == CANDIDATE_PROTOCOL_VERSION, "wrong candidate protocol")
    require(record["implementation_version"] == IMPLEMENTATION_VERSION, "wrong implementation version")
    require(record["solver_stratum"] == SOLVER_STRATUM and record["num_threads"] == 1, "not solver-disabled single-thread")
    require(record["prng_version"] == PRNG_VERSION, "wrong PRNG")
    require(isinstance(record["case_id"], str) and record["case_id"], "case_id must be nonempty string")
    require(is_sha256(record["candidate_generator_config_sha256"]) and is_sha256(record["candidate_manifest_sha256"]), "bad candidate digests")
    rows, n = parse_h_rows(record["H_rows"]); require(record["n"] == n, "n mismatch")
    rank = len(rref_bit_rows(rows, n)[0]); require(record["rank"] == rank, "rank mismatch")
    require(record["public_h_sha256"] == public_h_sha256(record["H_rows"]), "public H hash mismatch")
    require(record["phase"] in PHASES and record["seed_role"] in SEED_ROLES, "bad phase/role")
    require_phase_seed_pair(record["phase"], record["seed_role"])
    genuine_int(record["seed_index"], "seed_index", minimum=0, maximum=7); genuine_int(record["budget"], "budget", minimum=0)
    if record["phase"] == "threshold_fit": require(record["W"] is None and record["threshold_witnesses_seen"] == 0 and record["threshold_hit"] is False, "threshold_fit must not use W")
    else: genuine_int(record["W"], "W", minimum=0)
    cfg = resolved_config(record["algorithm_config"], rank=rank); require(record["algorithm_config_sha256"] == config_digest(cfg), "config digest mismatch")
    require(record["termination_reason"] in ALLOWED_TERMINATION_REASONS, "unknown termination reason")
    finite_nonnegative_real(record["runtime_s"], "runtime_s")
    require(isinstance(record["environment"], dict) and set(record["environment"]) == {"platform"} and isinstance(record["environment"]["platform"], str), "bad environment schema")
    source = record["source"]
    require(isinstance(source, dict) and set(source) == {"source_commit", "isd_module_sha256", "python_version"}, "bad source schema")
    require((source["source_commit"] is None) or (isinstance(source["source_commit"], str) and len(source["source_commit"]) in {40,64} and all(c in "0123456789abcdef" for c in source["source_commit"])), "bad source commit")
    require(is_sha256(source["isd_module_sha256"]) and isinstance(source["python_version"], str), "bad source hash/version")
    counters = ["information_set_attempts","singular_information_sets","information_sets_accepted","list_entries_left","list_entries_right","projection_operations","bucket_probes","collision_pairs","skipped_collision_pairs","reconstructed_candidates","candidate_evaluations","objective_evaluations","exact_verifications","valid_codewords_seen","threshold_witnesses_seen","duplicate_candidates","resource_limit_events"]
    for c in counters: genuine_int(record[c], c, minimum=0)
    require(record["singular_information_sets"] + record["information_sets_accepted"] <= record["information_set_attempts"], "information-set counters invalid")
    require(record["information_set_attempts"] <= cfg["information_set_limit"], "information-set limit exceeded")
    require(record["candidate_evaluations"] == record["objective_evaluations"] == record["exact_verifications"] == record["reconstructed_candidates"] == record["valid_codewords_seen"], "candidate verification counters diverge")
    require(record["candidate_evaluations"] <= record["budget"], "candidate budget exceeded")
    require(record["duplicate_candidates"] <= record["candidate_evaluations"], "duplicate count invalid")
    require(record["threshold_witnesses_seen"] <= record["candidate_evaluations"], "threshold witness count invalid")
    require(record["projection_operations"] <= cfg["max_projection_operations"], "projection cap exceeded")
    require(record["collision_pairs"] <= cfg["max_collision_pairs"], "collision cap exceeded")
    require(record["list_entries_left"] <= cfg["max_left_list_entries"] * max(1, record["information_sets_accepted"]), "left list cap exceeded")
    require(record["list_entries_right"] <= cfg["max_right_list_entries"] * max(1, record["information_sets_accepted"]), "right list cap exceeded")
    diag = record["diagnostics"]
    expected_diag = {"rank","kernel_dimension","projection_bits_effective","prng_randbits_calls","prng_randbelow_calls","prng_sha256_blocks","collision_algorithm","max_projection_operations","max_collision_pairs","collision_pairs_semantics"}
    require(isinstance(diag, dict) and set(diag) == expected_diag, "bad diagnostics schema")
    require(diag["rank"] == rank and diag["kernel_dimension"] == n-rank and diag["projection_bits_effective"] == min(cfg["projection_bits"], rank), "diagnostic dimension mismatch")
    require(diag["collision_algorithm"] == "stern_dumer_projected_bucket_match", "wrong diagnostic algorithm")
    require(diag["max_projection_operations"] == cfg["max_projection_operations"] and diag["max_collision_pairs"] == cfg["max_collision_pairs"], "diagnostic cap mismatch")
    for d in ("prng_randbits_calls","prng_randbelow_calls","prng_sha256_blocks"): genuine_int(diag[d], d, minimum=0)
    if record["termination_reason"] == "candidate_budget_exhausted":
        require(record["candidate_evaluations"] == record["budget"], "candidate-budget termination requires exact budget exhaustion")
        require(record["resource_limit_events"] == 0, "resource-limit record cannot be relabelled as candidate-budget exhausted")
    if record["termination_reason"] == "resource_limit":
        require(record["resource_limit_events"] > 0, "resource_limit termination requires resource event")
    else:
        require(record["resource_limit_events"] == 0, "resource event requires resource_limit termination")
    if record["termination_reason"] == "information_set_limit_exhausted":
        require(record["information_set_attempts"] == cfg["information_set_limit"] and record["candidate_evaluations"] < record["budget"], "bad information-set-limit termination")
    if record["termination_reason"] == "trivial_code_no_nonzero_word":
        require(diag["kernel_dimension"] == 0 and record["candidate_evaluations"] == 0, "bad trivial-code termination")
    if record["best_candidate_bits"] is None:
        require(record["best_candidate_sha256"] is None and record["best_weight"] is None and record["witness_verified"] is False and record["threshold_hit"] is False, "empty incumbent fields inconsistent")
    else:
        bits = record["best_candidate_bits"]; require(len(bits) == n, "candidate length mismatch")
        require(record["best_candidate_sha256"] == sha256_object({"candidate_bits": bits}), "candidate hash mismatch")
        word = bits_to_word(bits); wt = verify_nonzero_kernel_word(rows, n, word)
        require(record["best_weight"] == wt and record["witness_verified"] is True, "witness verification mismatch")
        require(record["threshold_hit"] == (record["W"] is not None and wt <= record["W"]), "threshold status mismatch")
        if record["threshold_hit"]:
            require(record["threshold_witnesses_seen"] >= 1, "threshold hit requires threshold witness count")
    require(record["reproducible_core_sha256"] == compute_reproducible_core_sha256(record), "reproducible core digest mismatch")
    if check_current_source:
        require(record["source"]["isd_module_sha256"] == sha256_file(Path(__file__)), "module hash mismatch")

class RejectDuplicateKeys(dict):
    def __init__(self, pairs: Iterable[tuple[str, Any]]):
        super().__init__()
        for k, v in pairs:
            if k in self: fail(f"duplicate JSON object key {k!r}")
            self[k] = v

def read_validate_jsonl(path: Path, *, check_current_source: bool = False) -> list[dict[str, Any]]:
    records = []
    seen = set()
    for line_no, raw in enumerate(path.read_bytes().splitlines(), 1):
        require(raw, f"blank line {line_no}")
        try:
            obj = json.loads(raw.decode("ascii"), object_pairs_hook=RejectDuplicateKeys)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            fail(f"invalid JSON at line {line_no}: {exc}")
        require(canonical_json_bytes(obj) == raw, f"noncanonical JSON at line {line_no}")
        validate_result_record(obj, check_current_source=check_current_source)
        ident = (obj["case_id"], obj["phase"], obj["seed_role"], obj["seed_index"], obj["budget"], obj["algorithm_config_sha256"])
        require(ident not in seen, f"duplicate run identity at line {line_no}")
        seen.add(ident); records.append(obj)
    return records

def write_jsonl(records: Sequence[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(canonical_json_bytes(r)+b"\n" for r in records))

def brute_force_min_weight(row_strings: Sequence[str]) -> int | None:
    rows, n = parse_h_rows(row_strings)
    best = None
    for word in range(1, 1 << n):
        if syndrome_is_zero(rows, word):
            wt = word.bit_count(); best = wt if best is None else min(best, wt)
    return best

def calibration_preflight() -> list[dict[str, Any]]:
    """Cheap structural check that calibration caps can permit budget completion."""
    representatives = [(96, 48), (128, 64), (160, 80), (192, 96), (240, 120)]
    rows = []
    raw = PROFILE_SPECS["calibration"]["algorithm_config"]
    for n, rank in representatives:
        k = n - rank
        cfg = algorithm_config("calibration", rank=rank)
        left_n = (k + 1) // 2
        right_n = k // 2
        left_entries = math.comb(left_n, cfg["left_weight"])
        right_entries = math.comb(right_n, cfg["right_weight"])
        projections_per_set = left_entries + right_entries
        max_sets_by_projection = cfg["max_projection_operations"] // max(1, projections_per_set)
        accepted_sets = min(cfg["information_set_limit"], max_sets_by_projection)
        # Projection buckets are filters; cap feasibility is structural, so use the maximum
        # possible processed collision pairs across accepted sets before projection filtering.
        max_candidate_capacity = min(cfg["max_collision_pairs"], accepted_sets * left_entries * right_entries)
        for budget in BUDGET_LADDER:
            require(max_candidate_capacity >= budget, f"calibration caps cannot permit budget {budget} for n={n}, r={rank}")
        rows.append({"n": n, "rank": rank, "projection_bits": cfg["projection_bits"], "left_entries": left_entries, "right_entries": right_entries, "accepted_sets_by_projection_cap": accepted_sets, "max_candidate_capacity": max_candidate_capacity})
    return rows

def self_test() -> dict[str, Any]:
    case = FIXTURE_CASES["isdv2-fixture-hamming7"]
    pi = make_public_input(case, phase="tier_validation", seed_role="tier_validation_seed", seed_index=0, budget=16)
    cfg = algorithm_config("smoke", rank=len(rref_bit_rows(parse_h_rows(case["H_rows"])[0], 7)[0]))
    first = run_record(pi, cfg); second = run_record(pi, cfg)
    validate_result_record(first); validate_result_record(second)
    require(canonical_json_bytes(reproducible_core(first)) == canonical_json_bytes(reproducible_core(second)), "self-test replay mismatch")
    require(first["list_entries_left"] > 0 and first["bucket_probes"] > 0 and first["collision_pairs"] > 0, "collision plumbing did not execute")
    preflight = calibration_preflight()
    return {"records": 2, "best_weight": first["best_weight"], "collision_pairs": first["collision_pairs"], "preflight_cases": len(preflight)}

def cmd_list(args: argparse.Namespace) -> int:
    data = PROFILE_SPECS[args.profile]
    print(json.dumps(data, sort_keys=True, indent=2 if not args.json else None))
    return 0

def cmd_run_fixture(args: argparse.Namespace) -> int:
    records = []
    for case in FIXTURE_CASES.values():
        rows, n = parse_h_rows(case["H_rows"])
        rank = len(rref_bit_rows(rows, n)[0])
        cfg = algorithm_config(args.profile, rank=rank)
        for budget in PROFILE_SPECS[args.profile]["budgets"][:1]:
            pi = make_public_input(case, phase="tier_validation", seed_role="tier_validation_seed", seed_index=0, budget=budget)
            record = run_record(pi, cfg)
            record["runtime_s"] = 0.0
            records.append(record)
    out = Path(args.output_dir) / RESULT_FILENAME
    write_jsonl(records, out); print(out)
    return 0

def cmd_validate(args: argparse.Namespace) -> int:
    records = read_validate_jsonl(Path(args.path), check_current_source=args.check_current_source)
    if args.print_summary:
        print(json.dumps({"records": len(records), "threshold_hits": sum(1 for r in records if r["threshold_hit"]), "candidate_evaluations": sum(r["candidate_evaluations"] for r in records)}, sort_keys=True))
    return 0

def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("self-test"); s.add_argument("--print-summary", action="store_true")
    l = sub.add_parser("list"); l.add_argument("--profile", default="smoke", choices=PROFILE_SPECS); l.add_argument("--json", action="store_true")
    r = sub.add_parser("run-fixture"); r.add_argument("--profile", default="smoke", choices=PROFILE_SPECS); r.add_argument("--output-dir", required=True)
    v = sub.add_parser("validate"); v.add_argument("path"); v.add_argument("--print-summary", action="store_true"); v.add_argument("--check-current-source", action="store_true")
    args = p.parse_args(argv)
    if args.cmd == "self-test":
        summary = self_test();
        if args.print_summary: print(json.dumps(summary, sort_keys=True))
        return 0
    if args.cmd == "list": return cmd_list(args)
    if args.cmd == "run-fixture": return cmd_run_fixture(args)
    if args.cmd == "validate": return cmd_validate(args)
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
