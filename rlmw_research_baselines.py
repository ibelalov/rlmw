"""Deterministic classical baselines for the frozen RLMW research corpus.

The algorithms in this module receive only the public solver payload of
``h-native-research-v1``.  Evaluator-only labels are applied after a run has
finished.  Solver-disabled candidate counts and solver-assisted CP-SAT rows are
kept in separate strata and must never be pooled as an equal-budget comparison.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
import time
import tracemalloc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

try:
    import resource
except ImportError:  # pragma: no cover - resource is available on CI/Colab Linux.
    resource = None  # type: ignore[assignment]

import rlmw_research_corpus as research_corpus


BASELINE_PROTOCOL_VERSION = "h-native-research-baselines-v1"
RESULT_SCHEMA_VERSION = "rlmw-research-baseline-result-v1"
EXPECTED_CORPUS_PROTOCOL_VERSION = "h-native-research-v1"
EXPECTED_MANIFEST_SHA256 = "b9ce7369cf3d2f1476390b8f1e823bf33d10268b1b0112cf55197ce4fff18559"
PRNG_VERSION = "sha256-ctr-v1"
RESULT_FILENAME = "rlmw_research_baseline_results.jsonl"

UNIFORM_KERNEL_SAMPLING = "uniform_kernel_sampling_v1"
FIXED_WEIGHT_SUBSET_SAMPLING = "fixed_weight_subset_sampling_v1"
LEE_BRICKELL_ISD = "lee_brickell_isd_v1"
CP_SAT_THRESHOLD_REFERENCE = "cp_sat_threshold_reference_v1"

BASELINE_IDS = (
    UNIFORM_KERNEL_SAMPLING,
    FIXED_WEIGHT_SUBSET_SAMPLING,
    LEE_BRICKELL_ISD,
    CP_SAT_THRESHOLD_REFERENCE,
)
DEFAULT_BASELINE_IDS = BASELINE_IDS[:3]
BASELINE_IMPLEMENTATION_VERSIONS = {baseline_id: "1.0.0" for baseline_id in BASELINE_IDS}
SOLVER_STRATA = {
    UNIFORM_KERNEL_SAMPLING: "solver_disabled",
    FIXED_WEIGHT_SUBSET_SAMPLING: "solver_disabled",
    LEE_BRICKELL_ISD: "solver_disabled",
    CP_SAT_THRESHOLD_REFERENCE: "solver_assisted_reference",
}

PROFILE_SPECS: dict[str, dict[str, Any]] = {
    "smoke": {
        "case_subset": "smoke",
        "runs": [{"repetition_id": 0, "seed": 101}],
        "algorithms": {
            UNIFORM_KERNEL_SAMPLING: {
                "candidate_budget": 512,
                "sampling_with_replacement": True,
                "exhaust_candidate_budget": True,
                "prng_version": PRNG_VERSION,
            },
            FIXED_WEIGHT_SUBSET_SAMPLING: {
                "candidate_budget": 512,
                "weights": [1, 2, 3, 4],
                "weight_schedule": "round_robin",
                "sampling_with_replacement_across_iterations": True,
                "exhaust_candidate_budget": True,
                "prng_version": PRNG_VERSION,
            },
            LEE_BRICKELL_ISD: {
                "candidate_budget": 512,
                "information_set_budget": 32,
                "max_information_weight": 2,
                "pattern_mode": "enumerate_nonzero_weight_at_most_p_lexicographic",
                "information_set_sampling": "uniform_r_subset_with_replacement",
                "exhaust_candidate_budget": True,
                "prng_version": PRNG_VERSION,
            },
            CP_SAT_THRESHOLD_REFERENCE: {
                "solver_call_budget": 1,
                "max_time_seconds": 1.0,
                "max_deterministic_time": 0.5,
                "num_search_workers": 1,
            },
        },
    },
    "full": {
        "case_subset": "full",
        "runs": [
            {"repetition_id": 0, "seed": 101},
            {"repetition_id": 1, "seed": 202},
            {"repetition_id": 2, "seed": 303},
            {"repetition_id": 3, "seed": 404},
        ],
        "algorithms": {
            UNIFORM_KERNEL_SAMPLING: {
                "candidate_budget": 100_000,
                "sampling_with_replacement": True,
                "exhaust_candidate_budget": True,
                "prng_version": PRNG_VERSION,
            },
            FIXED_WEIGHT_SUBSET_SAMPLING: {
                "candidate_budget": 100_000,
                "weights": list(range(1, 13)),
                "weight_schedule": "round_robin",
                "sampling_with_replacement_across_iterations": True,
                "exhaust_candidate_budget": True,
                "prng_version": PRNG_VERSION,
            },
            LEE_BRICKELL_ISD: {
                "candidate_budget": 100_000,
                "information_set_budget": 4_096,
                "max_information_weight": 2,
                "pattern_mode": "enumerate_nonzero_weight_at_most_p_lexicographic",
                "information_set_sampling": "uniform_r_subset_with_replacement",
                "exhaust_candidate_budget": True,
                "prng_version": PRNG_VERSION,
            },
            CP_SAT_THRESHOLD_REFERENCE: {
                "solver_call_budget": 1,
                "max_time_seconds": 60.0,
                "max_deterministic_time": 30.0,
                "num_search_workers": 1,
            },
        },
    },
}

EXPECTED_PROFILE_SHA256 = {
    "smoke": "5629b265cdd325b776f41786243c34f354ac577f5a318fa336c2aaf61f026e9a",
    "full": "1195f7ed0f5a9a21c83dad41ddd23967a869e75dae2a26c7b7bb32128f48c7d2",
}

OBSERVATIONAL_FIELDS = {
    "total_runtime_s",
    "solver_time_s",
    "peak_traced_memory_bytes",
    "process_max_rss_bytes",
    "environment",
}


class BaselineValidationError(ValueError):
    """Raised for a violated baseline, result, or CLI contract."""


class OptionalDependencyUnavailable(RuntimeError):
    """Raised when an explicitly selected optional reference cannot run."""


def fail(message: str) -> None:
    raise BaselineValidationError(f"{BASELINE_PROTOCOL_VERSION} validation error: {message}")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def genuine_int(value: Any, name: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    require(isinstance(value, int) and not isinstance(value, bool), f"{name} must be a genuine integer")
    if minimum is not None:
        require(value >= minimum, f"{name} must be >= {minimum}")
    if maximum is not None:
        require(value <= maximum, f"{name} must be <= {maximum}")
    return value


def finite_nonnegative_real(value: Any, name: str, *, positive: bool = False) -> float:
    require(isinstance(value, (int, float)) and not isinstance(value, bool), f"{name} must be a real number")
    number = float(value)
    require(math.isfinite(number), f"{name} must be finite")
    require(number > 0.0 if positive else number >= 0.0, f"{name} must be {'positive' if positive else 'nonnegative'}")
    return number


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        fail(f"value is not canonical-JSON serializable: {exc}")


def sha256_object(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def candidate_sha256(bits: str) -> str:
    return sha256_object({"candidate_bits": bits})


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def is_git_oid(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) in {40, 64}
        and all(c in "0123456789abcdef" for c in value)
    )


def profile_sha256(profile_id: str) -> str:
    require(profile_id in PROFILE_SPECS, f"unknown profile_id {profile_id!r}")
    digest = sha256_object(PROFILE_SPECS[profile_id])
    require(
        digest == EXPECTED_PROFILE_SHA256[profile_id],
        f"profile {profile_id!r} changed without a baseline-protocol version bump",
    )
    return digest


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            fail(f"duplicate JSON object key {key!r}")
        out[key] = value
    return out


def strict_json_loads(text: str) -> Any:
    def reject_constant(token: str) -> None:
        fail(f"non-finite JSON constant {token!r}")

    try:
        return json.loads(text, object_pairs_hook=_strict_object, parse_constant=reject_constant)
    except BaselineValidationError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        fail(f"invalid JSON: {exc}")


def load_manifest(path: str | os.PathLike[str], *, smoke_only: bool = False) -> dict[str, Any]:
    manifest_path = Path(path)
    require(manifest_path.is_file(), f"manifest file does not exist: {manifest_path}")
    manifest = strict_json_loads(manifest_path.read_text(encoding="utf-8"))
    require(isinstance(manifest, dict), "manifest root must be an object")
    require(manifest.get("protocol_version") == EXPECTED_CORPUS_PROTOCOL_VERSION, "wrong corpus protocol version")
    require(manifest.get("manifest_sha256") == EXPECTED_MANIFEST_SHA256, "research manifest is not the frozen expected digest")
    research_corpus.validate_manifest(manifest, smoke_only=smoke_only)
    return manifest


def _case_by_id(manifest: dict[str, Any], case_id: str) -> dict[str, Any]:
    case = next((item for item in manifest["cases"] if item.get("case_id") == case_id), None)
    require(case is not None, f"unknown case_id {case_id!r}")
    return case


def select_cases(
    manifest: dict[str, Any],
    profile_id: str,
    case_ids: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    require(profile_id in PROFILE_SPECS, f"unknown profile_id {profile_id!r}")
    subset = PROFILE_SPECS[profile_id]["case_subset"]
    eligible = [case for case in manifest["cases"] if subset in case["subset"]]
    if case_ids is None:
        return eligible
    requested = list(case_ids)
    require(len(requested) == len(set(requested)), "case selectors must be unique")
    eligible_ids = {case["case_id"] for case in eligible}
    for case_id in requested:
        require(case_id in eligible_ids, f"case_id {case_id!r} is not in profile {profile_id!r}")
    requested_set = set(requested)
    return [case for case in eligible if case["case_id"] in requested_set]


def select_baselines(baseline_ids: Sequence[str] | None) -> list[str]:
    selected = list(DEFAULT_BASELINE_IDS if baseline_ids is None else baseline_ids)
    require(selected, "at least one baseline must be selected")
    require(len(selected) == len(set(selected)), "baseline selectors must be unique")
    for baseline_id in selected:
        require(baseline_id in BASELINE_IDS, f"unknown baseline_id {baseline_id!r}")
    selected_set = set(selected)
    return [baseline_id for baseline_id in BASELINE_IDS if baseline_id in selected_set]


def select_run_pairs(
    profile_id: str,
    repetition_ids: Sequence[int] | None = None,
    seeds: Sequence[int] | None = None,
) -> list[dict[str, int]]:
    require(profile_id in PROFILE_SPECS, f"unknown profile_id {profile_id!r}")
    pinned = copy.deepcopy(PROFILE_SPECS[profile_id]["runs"])
    repetition_filter = None
    if repetition_ids is not None:
        repetition_filter = {genuine_int(x, "repetition_id", minimum=0) for x in repetition_ids}
        require(len(repetition_filter) == len(repetition_ids), "repetition selectors must be unique")
        known = {run["repetition_id"] for run in pinned}
        require(repetition_filter <= known, "a repetition selector is not pinned in this profile")
    seed_filter = None
    if seeds is not None:
        seed_filter = {genuine_int(x, "seed", minimum=0, maximum=(1 << 64) - 1) for x in seeds}
        require(len(seed_filter) == len(seeds), "seed selectors must be unique")
        known = {run["seed"] for run in pinned}
        require(seed_filter <= known, "a seed selector is not pinned in this profile")
    selected = [
        run
        for run in pinned
        if (repetition_filter is None or run["repetition_id"] in repetition_filter)
        and (seed_filter is None or run["seed"] in seed_filter)
    ]
    require(selected, "seed/repetition selectors do not select a pinned run pair")
    return selected


def _config_keyset(baseline_id: str) -> set[str]:
    return set(PROFILE_SPECS["smoke"]["algorithms"][baseline_id])


def validate_algorithm_config(baseline_id: str, config: dict[str, Any], *, n: int | None = None, k: int | None = None) -> None:
    require(baseline_id in BASELINE_IDS, f"unknown baseline_id {baseline_id!r}")
    require(isinstance(config, dict), "algorithm_config must be an object")
    require(set(config) == _config_keyset(baseline_id), f"algorithm_config keys are invalid for {baseline_id}")
    if baseline_id == UNIFORM_KERNEL_SAMPLING:
        genuine_int(config["candidate_budget"], "candidate_budget", minimum=0)
        require(config["sampling_with_replacement"] is True, "uniform sampling must be with replacement")
        require(config["exhaust_candidate_budget"] is True, "uniform sampling must exhaust its candidate budget")
        require(config["prng_version"] == PRNG_VERSION, "wrong PRNG version")
    elif baseline_id == FIXED_WEIGHT_SUBSET_SAMPLING:
        genuine_int(config["candidate_budget"], "candidate_budget", minimum=0)
        weights = config["weights"]
        require(isinstance(weights, list) and weights, "weights must be a nonempty list")
        require(weights == sorted(set(weights)), "weights must be unique and increasing")
        for weight in weights:
            genuine_int(weight, "subset weight", minimum=1)
            if n is not None:
                require(weight <= n, "subset weight exceeds code length")
        require(config["weight_schedule"] == "round_robin", "unsupported fixed-weight schedule")
        require(config["sampling_with_replacement_across_iterations"] is True, "subsets must be sampled with replacement across iterations")
        require(config["exhaust_candidate_budget"] is True, "fixed-weight sampling must exhaust its candidate budget")
        require(config["prng_version"] == PRNG_VERSION, "wrong PRNG version")
    elif baseline_id == LEE_BRICKELL_ISD:
        genuine_int(config["candidate_budget"], "candidate_budget", minimum=0)
        genuine_int(config["information_set_budget"], "information_set_budget", minimum=0)
        p = genuine_int(config["max_information_weight"], "max_information_weight", minimum=1)
        if k is not None and k > 0:
            require(p <= k, "max_information_weight exceeds kernel dimension")
        require(config["pattern_mode"] == "enumerate_nonzero_weight_at_most_p_lexicographic", "unsupported Lee-Brickell pattern mode")
        require(config["information_set_sampling"] == "uniform_r_subset_with_replacement", "unsupported information-set sampling mode")
        require(config["exhaust_candidate_budget"] is True, "Lee-Brickell must not stop at its first threshold hit")
        require(config["prng_version"] == PRNG_VERSION, "wrong PRNG version")
    else:
        require(genuine_int(config["solver_call_budget"], "solver_call_budget", minimum=1, maximum=1) == 1, "CP-SAT uses exactly one solver call")
        finite_nonnegative_real(config["max_time_seconds"], "max_time_seconds", positive=True)
        finite_nonnegative_real(config["max_deterministic_time"], "max_deterministic_time", positive=True)
        require(genuine_int(config["num_search_workers"], "num_search_workers", minimum=1, maximum=1) == 1, "CP-SAT must use one worker")


def algorithm_config(
    profile_id: str,
    baseline_id: str,
    *,
    overrides: dict[str, Any] | None = None,
    n: int | None = None,
    k: int | None = None,
) -> dict[str, Any]:
    require(profile_id in PROFILE_SPECS, f"unknown profile_id {profile_id!r}")
    require(baseline_id in BASELINE_IDS, f"unknown baseline_id {baseline_id!r}")
    config = copy.deepcopy(PROFILE_SPECS[profile_id]["algorithms"][baseline_id])
    if overrides:
        require(set(overrides) <= set(config), f"an override is invalid for {baseline_id}")
        config.update(copy.deepcopy(overrides))
    validate_algorithm_config(baseline_id, config, n=n, k=k)
    return config


def requested_budgets(baseline_id: str, config: dict[str, Any]) -> dict[str, Any]:
    if baseline_id in (UNIFORM_KERNEL_SAMPLING, FIXED_WEIGHT_SUBSET_SAMPLING):
        return {"candidate_evaluations": config["candidate_budget"]}
    if baseline_id == LEE_BRICKELL_ISD:
        return {
            "candidate_evaluations": config["candidate_budget"],
            "information_set_attempts": config["information_set_budget"],
        }
    return {
        "solver_calls": config["solver_call_budget"],
        "wall_time_seconds": float(config["max_time_seconds"]),
        "deterministic_time": float(config["max_deterministic_time"]),
    }


def make_public_run_input(
    manifest: dict[str, Any],
    case_id: str,
    *,
    profile_id: str,
    repetition_id: int,
    seed: int,
) -> dict[str, Any]:
    budget_run = {
        "profile_id": profile_id,
        "profile_sha256": profile_sha256(profile_id),
        "repetition_id": genuine_int(repetition_id, "repetition_id", minimum=0),
        "seed": genuine_int(seed, "seed", minimum=0, maximum=(1 << 64) - 1),
    }
    payload = research_corpus.solver_payload(manifest, case_id, budget_run=budget_run)
    require(tuple(payload) == research_corpus.SOLVER_KEYS, "public solver payload key order changed")
    require(not (set(payload) & research_corpus.EVALUATOR_ONLY_KEYS), "evaluator-only field leaked into public solver payload")
    return payload


def derive_rng_key(*, baseline_id: str, case_id: str, repetition_id: int, seed: int) -> bytes:
    material = {
        "baseline_protocol_version": BASELINE_PROTOCOL_VERSION,
        "prng_version": PRNG_VERSION,
        "baseline_id": baseline_id,
        "case_id": case_id,
        "repetition_id": genuine_int(repetition_id, "repetition_id", minimum=0),
        "declared_seed": genuine_int(seed, "seed", minimum=0, maximum=(1 << 64) - 1),
    }
    return hashlib.sha256(canonical_json_bytes(material)).digest()


class Sha256CounterRng:
    """Repository-controlled deterministic SHA-256 counter byte stream."""

    def __init__(self, key: bytes):
        require(isinstance(key, bytes) and len(key) == 32, "PRNG key must be 32 bytes")
        self._key = key
        self._counter = 0
        self._buffer = b""
        self.sha256_blocks_generated = 0
        self.randbits_calls = 0
        self.randbelow_calls = 0

    def _refill(self) -> None:
        require(self._counter < (1 << 128), "PRNG counter exhausted")
        self._buffer += hashlib.sha256(self._key + self._counter.to_bytes(16, "big")).digest()
        self._counter += 1
        self.sha256_blocks_generated += 1

    def bytes(self, count: int) -> bytes:
        genuine_int(count, "PRNG byte count", minimum=0)
        while len(self._buffer) < count:
            self._refill()
        out, self._buffer = self._buffer[:count], self._buffer[count:]
        return out

    def randbits(self, count: int) -> int:
        genuine_int(count, "PRNG bit count", minimum=0)
        if count == 0:
            return 0
        self.randbits_calls += 1
        byte_count = (count + 7) // 8
        value = int.from_bytes(self.bytes(byte_count), "big")
        return value & ((1 << count) - 1)

    def randbelow(self, bound: int) -> int:
        genuine_int(bound, "randbelow bound", minimum=1)
        self.randbelow_calls += 1
        bits = bound.bit_length()
        while True:
            value = self.randbits(bits)
            if value < bound:
                return value

    def sample_subset(self, population_size: int, sample_size: int) -> list[int]:
        n = genuine_int(population_size, "population_size", minimum=0)
        k = genuine_int(sample_size, "sample_size", minimum=0)
        require(k <= n, "sample_size exceeds population_size")
        values = list(range(n))
        for left in range(k):
            right = left + self.randbelow(n - left)
            values[left], values[right] = values[right], values[left]
        return sorted(values[:k])


def parse_h_rows(rows: Sequence[str]) -> tuple[list[int], int]:
    require(isinstance(rows, (list, tuple)) and rows, "H_rows must be a nonempty sequence")
    require(all(isinstance(row, str) for row in rows), "H rows must be strings")
    n = len(rows[0])
    require(n > 0, "H must have positive width")
    parsed: list[int] = []
    for row in rows:
        require(len(row) == n and set(row) <= {"0", "1"}, "H_rows must be rectangular binary strings")
        word = 0
        for coordinate, char in enumerate(row):
            if char == "1":
                word |= 1 << coordinate
        parsed.append(word)
    return parsed, n


def rref_bit_rows(rows: Sequence[int], n: int) -> tuple[list[int], list[int]]:
    genuine_int(n, "matrix width", minimum=0)
    matrix = list(rows)
    require(all(isinstance(row, int) and not isinstance(row, bool) and 0 <= row < (1 << n) for row in matrix), "invalid bit row")
    pivot_columns: list[int] = []
    pivot_row = 0
    for column in range(n):
        source = next((idx for idx in range(pivot_row, len(matrix)) if (matrix[idx] >> column) & 1), None)
        if source is None:
            continue
        matrix[pivot_row], matrix[source] = matrix[source], matrix[pivot_row]
        for idx in range(len(matrix)):
            if idx != pivot_row and ((matrix[idx] >> column) & 1):
                matrix[idx] ^= matrix[pivot_row]
        pivot_columns.append(column)
        pivot_row += 1
        if pivot_row == len(matrix):
            break
    return matrix[:pivot_row], pivot_columns


def gf2_rank_bit_rows(rows: Sequence[int], n: int) -> int:
    return len(rref_bit_rows(rows, n)[1])


def syndrome_is_zero(h_rows: Sequence[int], candidate: int) -> bool:
    return all(((row & candidate).bit_count() & 1) == 0 for row in h_rows)


def bits_to_word(bits: str) -> int:
    require(isinstance(bits, str) and bits and set(bits) <= {"0", "1"}, "candidate bits must be a nonempty binary string")
    word = 0
    for coordinate, char in enumerate(bits):
        if char == "1":
            word |= 1 << coordinate
    return word


def word_to_bits(word: int, n: int) -> str:
    genuine_int(word, "candidate word", minimum=0)
    genuine_int(n, "candidate length", minimum=1)
    require(word < (1 << n), "candidate word exceeds declared length")
    return "".join("1" if (word >> coordinate) & 1 else "0" for coordinate in range(n))


def verify_nonzero_kernel_word(h_rows: Sequence[int], n: int, candidate: int) -> int:
    genuine_int(candidate, "candidate", minimum=0)
    require(candidate < (1 << n), "candidate exceeds code length")
    require(candidate != 0, "candidate is the forbidden zero codeword")
    require(syndrome_is_zero(h_rows, candidate), "candidate does not satisfy Hc=0")
    weight = candidate.bit_count()
    require(0 < weight <= n, "candidate weight is invalid")
    return weight


def deterministic_kernel_basis(h_rows: Sequence[int], n: int) -> tuple[list[int], list[int], list[int]]:
    rref_rows, pivot_columns = rref_bit_rows(h_rows, n)
    pivot_set = set(pivot_columns)
    free_columns = [column for column in range(n) if column not in pivot_set]
    basis: list[int] = []
    for free_column in free_columns:
        vector = 1 << free_column
        for row, pivot_column in zip(rref_rows, pivot_columns):
            if (row >> free_column) & 1:
                vector |= 1 << pivot_column
        verify_nonzero_kernel_word(h_rows, n, vector)
        basis.append(vector)
    require(len(basis) == n - len(pivot_columns), "kernel basis dimension mismatch")
    for basis_index, free_column in enumerate(free_columns):
        restriction = sum(((vector >> free_column) & 1) << idx for idx, vector in enumerate(basis))
        require(restriction == (1 << basis_index), "kernel basis does not preserve free-coordinate identity")
    require(gf2_rank_bit_rows(basis, n) == len(basis), "kernel basis is not independent")
    return basis, pivot_columns, free_columns


def kernel_word(basis: Sequence[int], coefficients: int) -> int:
    genuine_int(coefficients, "kernel coefficients", minimum=0)
    require(coefficients < (1 << len(basis)), "kernel coefficients exceed basis dimension")
    candidate = 0
    remaining = coefficients
    while remaining:
        lsb = remaining & -remaining
        candidate ^= basis[lsb.bit_length() - 1]
        remaining ^= lsb
    return candidate


def invert_square_bit_matrix(rows: Sequence[int], size: int) -> list[int] | None:
    genuine_int(size, "matrix size", minimum=0)
    require(len(rows) == size, "square matrix row count mismatch")
    if size == 0:
        return []
    mask = (1 << size) - 1
    augmented = [(row & mask) | (1 << (size + idx)) for idx, row in enumerate(rows)]
    for column in range(size):
        source = next((idx for idx in range(column, size) if (augmented[idx] >> column) & 1), None)
        if source is None:
            return None
        augmented[column], augmented[source] = augmented[source], augmented[column]
        for idx in range(size):
            if idx != column and ((augmented[idx] >> column) & 1):
                augmented[idx] ^= augmented[column]
    require(all((row & mask) == (1 << idx) for idx, row in enumerate(augmented)), "matrix inversion failed")
    return [(row >> size) & mask for row in augmented]


def _column_syndromes(h_rows: Sequence[int], n: int) -> list[int]:
    columns: list[int] = []
    for coordinate in range(n):
        syndrome = 0
        for row_index, row in enumerate(h_rows):
            if (row >> coordinate) & 1:
                syndrome |= 1 << row_index
        columns.append(syndrome)
    return columns


@dataclass
class BaselineOutcome:
    best_candidate: int | None = None
    candidate_evaluations: int = 0
    objective_evaluations: int = 0
    exact_verifications: int = 0
    valid_codewords_seen: int = 0
    threshold_witnesses_seen: int = 0
    iterations: int = 0
    information_set_attempts: int = 0
    information_sets_accepted: int = 0
    singular_information_sets: int = 0
    solver_calls: int = 0
    solver_time_s: float = 0.0
    solver_status: str | None = None
    solver_status_raw: str | None = None
    threshold_infeasibility_certified: bool = False
    termination_reason: str = "not_started"
    diagnostics: dict[str, Any] = field(default_factory=dict)


def _update_incumbent(outcome: BaselineOutcome, candidate: int) -> None:
    if outcome.best_candidate is None:
        outcome.best_candidate = candidate
        return
    current_key = (outcome.best_candidate.bit_count(), outcome.best_candidate)
    proposed_key = (candidate.bit_count(), candidate)
    if proposed_key < current_key:
        outcome.best_candidate = candidate


def run_uniform_kernel_sampling(
    public_input: dict[str, Any], config: dict[str, Any], rng: Sha256CounterRng
) -> BaselineOutcome:
    h_rows, n = parse_h_rows(public_input["H_rows"])
    basis, _, _ = deterministic_kernel_basis(h_rows, n)
    validate_algorithm_config(UNIFORM_KERNEL_SAMPLING, config, n=n, k=len(basis))
    budget = config["candidate_budget"]
    outcome = BaselineOutcome(solver_status=None, solver_status_raw=None)
    if not basis:
        outcome.termination_reason = "trivial_code_no_nonzero_word"
        outcome.diagnostics = {
            "kernel_dimension": 0,
            "zero_coefficient_draws": 0,
            "duplicate_candidates": 0,
            "prng_randbits_calls": 0,
            "prng_randbelow_calls": 0,
            "prng_sha256_blocks": 0,
        }
        return outcome
    seen: set[int] = set()
    zero_draws = 0
    while outcome.candidate_evaluations < budget:
        coefficients = rng.randbits(len(basis))
        if coefficients == 0:
            zero_draws += 1
            continue
        candidate = kernel_word(basis, coefficients)
        weight = verify_nonzero_kernel_word(h_rows, n, candidate)
        outcome.candidate_evaluations += 1
        outcome.objective_evaluations += 1
        outcome.exact_verifications += 1
        outcome.valid_codewords_seen += 1
        outcome.iterations += 1
        if weight <= public_input["W"]:
            outcome.threshold_witnesses_seen += 1
        _update_incumbent(outcome, candidate)
        seen.add(candidate)
    outcome.termination_reason = "candidate_budget_exhausted"
    outcome.diagnostics = {
        "kernel_dimension": len(basis),
        "zero_coefficient_draws": zero_draws,
        "duplicate_candidates": outcome.candidate_evaluations - len(seen),
        "prng_randbits_calls": rng.randbits_calls,
        "prng_randbelow_calls": rng.randbelow_calls,
        "prng_sha256_blocks": rng.sha256_blocks_generated,
        "sampling_scope": "uniform_nonzero_kernel_coefficients_with_replacement",
    }
    return outcome


def run_fixed_weight_subset_sampling(
    public_input: dict[str, Any], config: dict[str, Any], rng: Sha256CounterRng
) -> BaselineOutcome:
    h_rows, n = parse_h_rows(public_input["H_rows"])
    validate_algorithm_config(FIXED_WEIGHT_SUBSET_SAMPLING, config, n=n)
    columns = _column_syndromes(h_rows, n)
    outcome = BaselineOutcome(solver_status=None, solver_status_raw=None)
    seen_supports: set[int] = set()
    weights = config["weights"]
    for evaluation in range(config["candidate_budget"]):
        requested_weight = weights[evaluation % len(weights)]
        support = rng.sample_subset(n, requested_weight)
        candidate = sum(1 << coordinate for coordinate in support)
        syndrome = 0
        for coordinate in support:
            syndrome ^= columns[coordinate]
        outcome.candidate_evaluations += 1
        outcome.objective_evaluations += 1
        outcome.exact_verifications += 1
        outcome.iterations += 1
        if syndrome == 0:
            weight = verify_nonzero_kernel_word(h_rows, n, candidate)
            require(weight == requested_weight, "fixed-weight candidate changed weight")
            outcome.valid_codewords_seen += 1
            if weight <= public_input["W"]:
                outcome.threshold_witnesses_seen += 1
            _update_incumbent(outcome, candidate)
        seen_supports.add(candidate)
    outcome.termination_reason = "candidate_budget_exhausted"
    outcome.diagnostics = {
        "duplicate_subsets": outcome.candidate_evaluations - len(seen_supports),
        "prng_randbits_calls": rng.randbits_calls,
        "prng_randbelow_calls": rng.randbelow_calls,
        "prng_sha256_blocks": rng.sha256_blocks_generated,
        "sampling_scope": "coordinate_subsets_with_replacement_across_iterations",
        "evaluated_weights": weights,
    }
    return outcome


def _systematic_codeword_basis(
    independent_rows: Sequence[int], original_rows: Sequence[int], n: int, parity_coordinates: Sequence[int]
) -> tuple[list[int], list[int]] | None:
    rank = len(independent_rows)
    require(len(parity_coordinates) == rank, "parity-coordinate count must equal rank")
    parity_rows = []
    for row in independent_rows:
        square_row = 0
        for local_column, coordinate in enumerate(parity_coordinates):
            if (row >> coordinate) & 1:
                square_row |= 1 << local_column
        parity_rows.append(square_row)
    inverse = invert_square_bit_matrix(parity_rows, rank)
    if inverse is None:
        return None
    parity_set = set(parity_coordinates)
    information_coordinates = [coordinate for coordinate in range(n) if coordinate not in parity_set]
    basis: list[int] = []
    for information_coordinate in information_coordinates:
        syndrome = 0
        for row_index, row in enumerate(independent_rows):
            if (row >> information_coordinate) & 1:
                syndrome |= 1 << row_index
        parity_solution = 0
        for parity_index, inverse_row in enumerate(inverse):
            if (inverse_row & syndrome).bit_count() & 1:
                parity_solution |= 1 << parity_coordinates[parity_index]
        codeword = (1 << information_coordinate) | parity_solution
        verify_nonzero_kernel_word(original_rows, n, codeword)
        basis.append(codeword)
    require(gf2_rank_bit_rows(basis, n) == len(information_coordinates), "systematic information basis is not independent")
    return basis, information_coordinates


def run_lee_brickell_isd(
    public_input: dict[str, Any], config: dict[str, Any], rng: Sha256CounterRng
) -> BaselineOutcome:
    original_rows, n = parse_h_rows(public_input["H_rows"])
    independent_rows, _ = rref_bit_rows(original_rows, n)
    rank = len(independent_rows)
    k = n - rank
    validate_algorithm_config(LEE_BRICKELL_ISD, config, n=n, k=k)
    outcome = BaselineOutcome(solver_status=None, solver_status_raw=None)
    if k == 0:
        outcome.termination_reason = "trivial_code_no_nonzero_word"
        outcome.diagnostics = {
            "rank": rank,
            "kernel_dimension": 0,
            "duplicate_candidates": 0,
            "prng_randbits_calls": 0,
            "prng_randbelow_calls": 0,
            "prng_sha256_blocks": 0,
        }
        return outcome
    seen_candidates: set[int] = set()
    candidate_budget = config["candidate_budget"]
    information_set_budget = config["information_set_budget"]
    p = config["max_information_weight"]
    for _attempt in range(information_set_budget):
        if outcome.candidate_evaluations >= candidate_budget:
            break
        outcome.information_set_attempts += 1
        outcome.iterations += 1
        parity_coordinates = rng.sample_subset(n, rank)
        systematic = _systematic_codeword_basis(
            independent_rows, original_rows, n, parity_coordinates
        )
        if systematic is None:
            outcome.singular_information_sets += 1
            continue
        basis, information_coordinates = systematic
        require(len(basis) == k and len(information_coordinates) == k, "systematic dimension mismatch")
        outcome.information_sets_accepted += 1
        stop = False
        for information_weight in range(1, p + 1):
            for local_support in itertools.combinations(range(k), information_weight):
                if outcome.candidate_evaluations >= candidate_budget:
                    stop = True
                    break
                candidate = 0
                for basis_index in local_support:
                    candidate ^= basis[basis_index]
                weight = verify_nonzero_kernel_word(original_rows, n, candidate)
                outcome.candidate_evaluations += 1
                outcome.objective_evaluations += 1
                outcome.exact_verifications += 1
                outcome.valid_codewords_seen += 1
                if weight <= public_input["W"]:
                    outcome.threshold_witnesses_seen += 1
                _update_incumbent(outcome, candidate)
                seen_candidates.add(candidate)
            if stop:
                break
        if stop:
            break
    outcome.termination_reason = (
        "candidate_budget_exhausted"
        if outcome.candidate_evaluations >= candidate_budget
        else "information_set_budget_exhausted"
    )
    outcome.diagnostics = {
        "rank": rank,
        "kernel_dimension": k,
        "max_information_weight": p,
        "duplicate_candidates": outcome.candidate_evaluations - len(seen_candidates),
        "prng_randbits_calls": rng.randbits_calls,
        "prng_randbelow_calls": rng.randbelow_calls,
        "prng_sha256_blocks": rng.sha256_blocks_generated,
        "pattern_scope": "all_nonzero_information_patterns_of_weight_at_most_p_per_accepted_set",
    }
    return outcome


def run_cp_sat_threshold_reference(
    public_input: dict[str, Any], config: dict[str, Any], declared_seed: int
) -> BaselineOutcome:
    h_rows, n = parse_h_rows(public_input["H_rows"])
    validate_algorithm_config(CP_SAT_THRESHOLD_REFERENCE, config, n=n)
    try:
        from ortools.sat.python import cp_model
    except ImportError as exc:
        raise OptionalDependencyUnavailable(
            "cp_sat_threshold_reference_v1 was explicitly selected, but OR-Tools is unavailable"
        ) from exc

    model = cp_model.CpModel()
    candidate_vars = [model.NewBoolVar(f"c_{coordinate}") for coordinate in range(n)]
    for row_index, row in enumerate(h_rows):
        coordinates = [coordinate for coordinate in range(n) if (row >> coordinate) & 1]
        parity_count = model.NewIntVar(0, len(coordinates) // 2, f"q_{row_index}")
        model.Add(sum(candidate_vars[coordinate] for coordinate in coordinates) == 2 * parity_count)
    total_weight = sum(candidate_vars)
    model.Add(total_weight >= 1)
    model.Add(total_weight <= genuine_int(public_input["W"], "public W", minimum=0, maximum=n))

    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = genuine_int(declared_seed, "declared_seed", minimum=0) % 2_147_483_647
    solver.parameters.max_time_in_seconds = float(config["max_time_seconds"])
    solver.parameters.max_deterministic_time = float(config["max_deterministic_time"])
    outcome = BaselineOutcome(solver_calls=1)
    status_code = solver.Solve(model)
    raw_name = solver.StatusName(status_code).upper()
    outcome.solver_time_s = finite_nonnegative_real(solver.WallTime(), "CP-SAT wall time")
    outcome.solver_status_raw = raw_name
    if status_code in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        outcome.solver_status = "FEASIBLE"
        candidate = sum((1 << coordinate) for coordinate, var in enumerate(candidate_vars) if solver.Value(var))
        weight = verify_nonzero_kernel_word(h_rows, n, candidate)
        require(weight <= public_input["W"], "CP-SAT returned a witness above W")
        outcome.best_candidate = candidate
        outcome.candidate_evaluations = 1
        outcome.objective_evaluations = 1
        outcome.exact_verifications = 1
        outcome.valid_codewords_seen = 1
        outcome.threshold_witnesses_seen = 1
        outcome.termination_reason = "solver_feasible"
    elif status_code == cp_model.INFEASIBLE:
        outcome.solver_status = "INFEASIBLE"
        outcome.threshold_infeasibility_certified = True
        outcome.termination_reason = "solver_infeasible"
    elif status_code == cp_model.MODEL_INVALID:
        fail("CP-SAT declared the internally constructed parity model invalid")
    else:
        outcome.solver_status = "UNKNOWN"
        outcome.termination_reason = "solver_unknown_or_limit"
    outcome.iterations = 1
    outcome.diagnostics = {
        "candidate_counting_scope": "external_returned_witness_checks_only",
        "solver_internal_candidates_comparable": False,
        "wall_time_limit_seconds": float(config["max_time_seconds"]),
        "deterministic_time_limit": float(config["max_deterministic_time"]),
        "num_search_workers": 1,
    }
    return outcome


RECORD_KEYS = {
    "result_schema_version",
    "baseline_protocol_version",
    "corpus_protocol_version",
    "manifest_id",
    "manifest_sha256",
    "case_id",
    "H_sha256",
    "n",
    "rank",
    "k",
    "public_W",
    "profile_id",
    "profile_sha256",
    "repetition_id",
    "declared_seed",
    "derived_seed_hex",
    "prng_version",
    "baseline_id",
    "baseline_implementation_version",
    "solver_stratum",
    "algorithm_config",
    "algorithm_config_sha256",
    "requested_budgets",
    "conservative_status",
    "termination_reason",
    "best_candidate_bits",
    "best_candidate_sha256",
    "best_weight",
    "witness_verified",
    "threshold_hit",
    "known_distance_gap",
    "optimality_claim",
    "certificate_scope",
    "candidate_evaluations",
    "objective_evaluations",
    "exact_verifications",
    "valid_codewords_seen",
    "threshold_witnesses_seen",
    "iterations",
    "information_set_attempts",
    "information_sets_accepted",
    "singular_information_sets",
    "solver_calls",
    "solver_status",
    "solver_status_raw",
    "threshold_infeasibility_certified",
    "diagnostics",
    "total_runtime_s",
    "solver_time_s",
    "peak_traced_memory_bytes",
    "process_max_rss_bytes",
    "environment",
    "source",
    "reproducible_core_sha256",
}

SOURCE_KEYS = {
    "git_commit_sha",
    "git_dirty",
    "baseline_module_sha256",
    "corpus_module_sha256",
    "manifest_file_sha256",
}
ENVIRONMENT_KEYS = {
    "python_version",
    "python_implementation",
    "platform",
    "ortools_version",
}


def _git_source(root: Path) -> tuple[str | None, bool | None]:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL
        ).strip()
        dirty_output = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        )
        if not is_git_oid(commit):
            return None, None
        return commit, bool(dirty_output.strip())
    except (OSError, subprocess.CalledProcessError):
        return None, None


def source_provenance(manifest_path: Path) -> dict[str, Any]:
    module_path = Path(__file__).resolve()
    corpus_path = Path(research_corpus.__file__).resolve()
    git_commit, git_dirty = _git_source(module_path.parent)
    return {
        "git_commit_sha": git_commit,
        "git_dirty": git_dirty,
        "baseline_module_sha256": sha256_file(module_path),
        "corpus_module_sha256": sha256_file(corpus_path),
        "manifest_file_sha256": sha256_file(manifest_path.resolve()),
    }


def environment_provenance() -> dict[str, Any]:
    try:
        import ortools

        ortools_version: str | None = str(ortools.__version__)
    except ImportError:
        ortools_version = None
    return {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "ortools_version": ortools_version,
    }


def process_max_rss_bytes() -> int | None:
    if resource is None:
        return None
    try:
        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except (AttributeError, OSError, ValueError):
        return None
    if value < 0:
        return None
    # Linux reports KiB; macOS reports bytes.
    return value if sys.platform == "darwin" else value * 1024


def reproducible_core(record: dict[str, Any]) -> dict[str, Any]:
    core = copy.deepcopy(record)
    for field_name in OBSERVATIONAL_FIELDS | {"reproducible_core_sha256"}:
        core.pop(field_name, None)
    return core


def compute_reproducible_core_sha256(record: dict[str, Any]) -> str:
    return sha256_object(reproducible_core(record))


def _expected_status_and_scope(
    *, baseline_id: str, outcome: BaselineOutcome, threshold_hit: bool, has_candidate: bool
) -> tuple[str, str]:
    if baseline_id == CP_SAT_THRESHOLD_REFERENCE:
        if outcome.solver_status == "FEASIBLE":
            return "THRESHOLD_WITNESS_FOUND", "verified_witness_upper_bound_only"
        if outcome.solver_status == "INFEASIBLE":
            return "CERTIFIED_NO_THRESHOLD_WITNESS", "exact_threshold_infeasibility_only"
        return "INCONCLUSIVE", "solver_unknown_no_existence_or_exclusion_claim"
    if threshold_hit:
        return "THRESHOLD_WITNESS_FOUND", "verified_witness_upper_bound_only"
    if has_candidate:
        return "BEST_VERIFIED_ABOVE_THRESHOLD", "verified_incumbent_upper_bound_only"
    return "BUDGET_EXHAUSTED_NO_THRESHOLD_WITNESS", "bounded_search_no_exclusion_claim"


def _validate_outcome_counters(outcome: BaselineOutcome) -> None:
    for field_name in (
        "candidate_evaluations",
        "objective_evaluations",
        "exact_verifications",
        "valid_codewords_seen",
        "threshold_witnesses_seen",
        "iterations",
        "information_set_attempts",
        "information_sets_accepted",
        "singular_information_sets",
        "solver_calls",
    ):
        genuine_int(getattr(outcome, field_name), field_name, minimum=0)
    finite_nonnegative_real(outcome.solver_time_s, "solver_time_s")
    require(outcome.objective_evaluations == outcome.candidate_evaluations, "objective/candidate evaluation counts differ")
    require(outcome.exact_verifications == outcome.candidate_evaluations, "every external candidate must be exactly checked")
    require(outcome.valid_codewords_seen <= outcome.candidate_evaluations, "valid-codeword count exceeds candidates")
    require(outcome.threshold_witnesses_seen <= outcome.valid_codewords_seen, "threshold-witness count exceeds valid codewords")
    require(outcome.information_sets_accepted + outcome.singular_information_sets == outcome.information_set_attempts, "information-set accounting mismatch")


def assemble_result_record(
    *,
    manifest: dict[str, Any],
    manifest_path: Path,
    case: dict[str, Any],
    public_input: dict[str, Any],
    profile_id: str,
    baseline_id: str,
    repetition_id: int,
    declared_seed: int,
    derived_seed_hex: str,
    config: dict[str, Any],
    outcome: BaselineOutcome,
    total_runtime_s: float,
    peak_traced_memory_bytes: int | None,
    process_rss_bytes: int | None,
) -> dict[str, Any]:
    _validate_outcome_counters(outcome)
    h_rows, n = parse_h_rows(public_input["H_rows"])
    require(n == case["n"], "public H length differs from evaluator case")
    candidate_bits: str | None = None
    candidate_hash: str | None = None
    best_weight: int | None = None
    witness_verified = False
    threshold_hit = False
    if outcome.best_candidate is not None:
        best_weight = verify_nonzero_kernel_word(h_rows, n, outcome.best_candidate)
        candidate_bits = word_to_bits(outcome.best_candidate, n)
        candidate_hash = candidate_sha256(candidate_bits)
        witness_verified = True
        threshold_hit = best_weight <= public_input["W"]
    require(threshold_hit == (outcome.threshold_witnesses_seen > 0), "threshold-hit/counter inconsistency")

    known_distance_gap: int | None = None
    label = case["label"]
    if label["kind"] == "certified_distance" and best_weight is not None:
        certified_distance = genuine_int(label["distance"], "certified distance", minimum=1)
        require(best_weight >= certified_distance, "candidate contradicts the certified distance")
        known_distance_gap = best_weight - certified_distance
    if outcome.solver_status == "INFEASIBLE" and label["kind"] == "certified_distance":
        require(label["distance"] > public_input["W"], "CP-SAT infeasibility contradicts the theorem-certified threshold")

    conservative_status, certificate_scope = _expected_status_and_scope(
        baseline_id=baseline_id,
        outcome=outcome,
        threshold_hit=threshold_hit,
        has_candidate=outcome.best_candidate is not None,
    )
    record: dict[str, Any] = {
        "result_schema_version": RESULT_SCHEMA_VERSION,
        "baseline_protocol_version": BASELINE_PROTOCOL_VERSION,
        "corpus_protocol_version": manifest["protocol_version"],
        "manifest_id": manifest["manifest_id"],
        "manifest_sha256": manifest["manifest_sha256"],
        "case_id": case["case_id"],
        "H_sha256": case["raw_H_sha256"],
        "n": case["n"],
        "rank": case["rank"],
        "k": case["k"],
        "public_W": public_input["W"],
        "profile_id": profile_id,
        "profile_sha256": profile_sha256(profile_id),
        "repetition_id": repetition_id,
        "declared_seed": declared_seed,
        "derived_seed_hex": derived_seed_hex,
        "prng_version": PRNG_VERSION,
        "baseline_id": baseline_id,
        "baseline_implementation_version": BASELINE_IMPLEMENTATION_VERSIONS[baseline_id],
        "solver_stratum": SOLVER_STRATA[baseline_id],
        "algorithm_config": copy.deepcopy(config),
        "algorithm_config_sha256": sha256_object(config),
        "requested_budgets": requested_budgets(baseline_id, config),
        "conservative_status": conservative_status,
        "termination_reason": outcome.termination_reason,
        "best_candidate_bits": candidate_bits,
        "best_candidate_sha256": candidate_hash,
        "best_weight": best_weight,
        "witness_verified": witness_verified,
        "threshold_hit": threshold_hit,
        "known_distance_gap": known_distance_gap,
        "optimality_claim": False,
        "certificate_scope": certificate_scope,
        "candidate_evaluations": outcome.candidate_evaluations,
        "objective_evaluations": outcome.objective_evaluations,
        "exact_verifications": outcome.exact_verifications,
        "valid_codewords_seen": outcome.valid_codewords_seen,
        "threshold_witnesses_seen": outcome.threshold_witnesses_seen,
        "iterations": outcome.iterations,
        "information_set_attempts": outcome.information_set_attempts,
        "information_sets_accepted": outcome.information_sets_accepted,
        "singular_information_sets": outcome.singular_information_sets,
        "solver_calls": outcome.solver_calls,
        "solver_status": outcome.solver_status,
        "solver_status_raw": outcome.solver_status_raw,
        "threshold_infeasibility_certified": outcome.threshold_infeasibility_certified,
        "diagnostics": copy.deepcopy(outcome.diagnostics),
        "total_runtime_s": float(total_runtime_s),
        "solver_time_s": float(outcome.solver_time_s),
        "peak_traced_memory_bytes": peak_traced_memory_bytes,
        "process_max_rss_bytes": process_rss_bytes,
        "environment": environment_provenance(),
        "source": source_provenance(manifest_path),
        "reproducible_core_sha256": "",
    }
    record["reproducible_core_sha256"] = compute_reproducible_core_sha256(record)
    return record


def run_baseline_record(
    *,
    manifest: dict[str, Any],
    manifest_path: Path,
    case: dict[str, Any],
    profile_id: str,
    baseline_id: str,
    repetition_id: int,
    seed: int,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    repetition_id = genuine_int(repetition_id, "repetition_id", minimum=0)
    seed = genuine_int(seed, "seed", minimum=0, maximum=(1 << 64) - 1)
    public_input = make_public_run_input(
        manifest,
        case["case_id"],
        profile_id=profile_id,
        repetition_id=repetition_id,
        seed=seed,
    )
    frozen_config = algorithm_config(profile_id, baseline_id, n=case["n"], k=case["k"])
    chosen_config = frozen_config if config is None else copy.deepcopy(config)
    validate_algorithm_config(baseline_id, chosen_config, n=case["n"], k=case["k"])
    require(
        canonical_json_bytes(chosen_config) == canonical_json_bytes(frozen_config),
        "stored runs must use the exact frozen profile configuration",
    )
    key = derive_rng_key(
        baseline_id=baseline_id,
        case_id=case["case_id"],
        repetition_id=repetition_id,
        seed=seed,
    )
    rng = Sha256CounterRng(key)

    was_tracing = tracemalloc.is_tracing()
    if not was_tracing:
        tracemalloc.start()
    before = time.perf_counter()
    try:
        if baseline_id == UNIFORM_KERNEL_SAMPLING:
            outcome = run_uniform_kernel_sampling(public_input, chosen_config, rng)
        elif baseline_id == FIXED_WEIGHT_SUBSET_SAMPLING:
            outcome = run_fixed_weight_subset_sampling(public_input, chosen_config, rng)
        elif baseline_id == LEE_BRICKELL_ISD:
            outcome = run_lee_brickell_isd(public_input, chosen_config, rng)
        elif baseline_id == CP_SAT_THRESHOLD_REFERENCE:
            outcome = run_cp_sat_threshold_reference(public_input, chosen_config, seed)
        else:
            fail(f"unknown baseline_id {baseline_id!r}")
        total_runtime = time.perf_counter() - before
        _, peak_memory = tracemalloc.get_traced_memory()
    finally:
        if not was_tracing and tracemalloc.is_tracing():
            tracemalloc.stop()
    return assemble_result_record(
        manifest=manifest,
        manifest_path=manifest_path,
        case=case,
        public_input=public_input,
        profile_id=profile_id,
        baseline_id=baseline_id,
        repetition_id=repetition_id,
        declared_seed=seed,
        derived_seed_hex=key.hex(),
        config=chosen_config,
        outcome=outcome,
        total_runtime_s=total_runtime,
        peak_traced_memory_bytes=peak_memory,
        process_rss_bytes=process_max_rss_bytes(),
    )


def _expected_record_status(record: dict[str, Any]) -> tuple[str, str]:
    outcome = BaselineOutcome(
        best_candidate=None if record["best_candidate_bits"] is None else 1,
        solver_status=record["solver_status"],
    )
    return _expected_status_and_scope(
        baseline_id=record["baseline_id"],
        outcome=outcome,
        threshold_hit=record["threshold_hit"],
        has_candidate=record["best_candidate_bits"] is not None,
    )


def validate_result_record(
    record: dict[str, Any],
    manifest: dict[str, Any],
    *,
    manifest_path: Path | None = None,
    check_current_source: bool = False,
) -> None:
    require(isinstance(record, dict), "result record must be an object")
    require(set(record) == RECORD_KEYS, "result record has missing or unexpected keys")
    require(record["result_schema_version"] == RESULT_SCHEMA_VERSION, "wrong result schema version")
    require(record["baseline_protocol_version"] == BASELINE_PROTOCOL_VERSION, "wrong baseline protocol version")
    require(record["corpus_protocol_version"] == manifest["protocol_version"], "wrong corpus protocol binding")
    require(record["manifest_id"] == manifest["manifest_id"], "wrong manifest ID binding")
    require(record["manifest_sha256"] == EXPECTED_MANIFEST_SHA256 == manifest["manifest_sha256"], "wrong manifest digest binding")
    case = _case_by_id(manifest, record["case_id"])
    for field_name in ("n", "rank", "k"):
        genuine_int(record[field_name], field_name, minimum=0)
        require(record[field_name] == case[field_name], f"{field_name} differs from manifest")
    require(record["n"] > 0 and record["rank"] + record["k"] == record["n"], "invalid recorded dimensions")
    require(record["H_sha256"] == case["raw_H_sha256"], "H hash differs from manifest")
    genuine_int(record["public_W"], "public_W", minimum=0, maximum=record["n"])
    require(record["public_W"] == case["threshold"]["W"], "public W differs from manifest")

    profile_id = record["profile_id"]
    require(profile_id in PROFILE_SPECS, "unknown profile binding")
    require(record["profile_sha256"] == profile_sha256(profile_id), "profile digest mismatch")
    subset = PROFILE_SPECS[profile_id]["case_subset"]
    require(subset in case["subset"], "case is outside the recorded profile")
    repetition_id = genuine_int(record["repetition_id"], "repetition_id", minimum=0)
    seed = genuine_int(record["declared_seed"], "declared_seed", minimum=0, maximum=(1 << 64) - 1)
    require(
        {"repetition_id": repetition_id, "seed": seed} in PROFILE_SPECS[profile_id]["runs"],
        "seed/repetition pair is not pinned in the profile",
    )
    require(record["prng_version"] == PRNG_VERSION, "wrong PRNG version")
    baseline_id = record["baseline_id"]
    require(baseline_id in BASELINE_IDS, "unknown baseline ID")
    require(record["baseline_implementation_version"] == BASELINE_IMPLEMENTATION_VERSIONS[baseline_id], "wrong baseline implementation version")
    require(record["solver_stratum"] == SOLVER_STRATA[baseline_id], "wrong solver stratum")
    derived = derive_rng_key(
        baseline_id=baseline_id,
        case_id=case["case_id"],
        repetition_id=repetition_id,
        seed=seed,
    ).hex()
    require(record["derived_seed_hex"] == derived, "derived seed mismatch")
    validate_algorithm_config(baseline_id, record["algorithm_config"], n=record["n"], k=record["k"])
    require(
        canonical_json_bytes(record["algorithm_config"])
        == canonical_json_bytes(algorithm_config(profile_id, baseline_id, n=record["n"], k=record["k"])),
        "algorithm config differs from the frozen profile",
    )
    require(record["algorithm_config_sha256"] == sha256_object(record["algorithm_config"]), "algorithm-config digest mismatch")
    require(record["requested_budgets"] == requested_budgets(baseline_id, record["algorithm_config"]), "requested budgets do not match config")

    count_fields = (
        "candidate_evaluations",
        "objective_evaluations",
        "exact_verifications",
        "valid_codewords_seen",
        "threshold_witnesses_seen",
        "iterations",
        "information_set_attempts",
        "information_sets_accepted",
        "singular_information_sets",
        "solver_calls",
    )
    for field_name in count_fields:
        genuine_int(record[field_name], field_name, minimum=0)
    require(record["objective_evaluations"] == record["candidate_evaluations"], "objective/candidate counts differ")
    require(record["exact_verifications"] == record["candidate_evaluations"], "not every external candidate was checked")
    require(record["valid_codewords_seen"] <= record["candidate_evaluations"], "valid-codeword count exceeds candidates")
    require(record["threshold_witnesses_seen"] <= record["valid_codewords_seen"], "threshold witnesses exceed codewords")
    require(record["information_sets_accepted"] + record["singular_information_sets"] == record["information_set_attempts"], "information-set counts do not balance")
    finite_nonnegative_real(record["total_runtime_s"], "total_runtime_s")
    finite_nonnegative_real(record["solver_time_s"], "solver_time_s")
    for field_name in ("peak_traced_memory_bytes", "process_max_rss_bytes"):
        if record[field_name] is not None:
            genuine_int(record[field_name], field_name, minimum=0)
    require(isinstance(record["diagnostics"], dict), "diagnostics must be an object")

    config = record["algorithm_config"]
    if baseline_id == UNIFORM_KERNEL_SAMPLING:
        require(record["information_set_attempts"] == record["information_sets_accepted"] == record["singular_information_sets"] == 0, "uniform record contains information-set activity")
        if record["k"] == 0:
            require(record["candidate_evaluations"] == 0, "trivial-code uniform run cannot evaluate a nonzero candidate")
            require(record["termination_reason"] == "trivial_code_no_nonzero_word", "wrong uniform trivial-code termination")
        else:
            require(record["candidate_evaluations"] == config["candidate_budget"], "candidate budget was not exhausted exactly")
            require(record["termination_reason"] == "candidate_budget_exhausted", "wrong uniform termination reason")
        require(record["iterations"] == record["candidate_evaluations"], "iteration count differs from candidate count")
    elif baseline_id == FIXED_WEIGHT_SUBSET_SAMPLING:
        require(record["information_set_attempts"] == record["information_sets_accepted"] == record["singular_information_sets"] == 0, "fixed-weight record contains information-set activity")
        require(record["candidate_evaluations"] == config["candidate_budget"], "candidate budget was not exhausted exactly")
        require(record["termination_reason"] == "candidate_budget_exhausted", "wrong fixed-weight termination reason")
        require(record["iterations"] == record["candidate_evaluations"], "iteration count differs from candidate count")
    elif baseline_id == LEE_BRICKELL_ISD:
        require(record["candidate_evaluations"] <= config["candidate_budget"], "Lee-Brickell exceeded candidate budget")
        require(record["information_set_attempts"] <= config["information_set_budget"], "Lee-Brickell exceeded information-set budget")
        require(record["iterations"] == record["information_set_attempts"], "Lee-Brickell iteration count must equal information-set attempts")
        if record["termination_reason"] == "candidate_budget_exhausted":
            require(record["candidate_evaluations"] == config["candidate_budget"], "candidate-budget termination is off by one")
            patterns_per_set = sum(
                math.comb(record["k"], information_weight)
                for information_weight in range(1, config["max_information_weight"] + 1)
            )
            expected_accepted = (
                0
                if config["candidate_budget"] == 0
                else math.ceil(config["candidate_budget"] / patterns_per_set)
            )
            require(record["information_sets_accepted"] == expected_accepted, "candidate-budget Lee-Brickell accepted-set accounting mismatch")
        elif record["termination_reason"] == "information_set_budget_exhausted":
            require(record["information_set_attempts"] == config["information_set_budget"], "information-set termination is off by one")
            patterns_per_set = sum(
                math.comb(record["k"], information_weight)
                for information_weight in range(1, config["max_information_weight"] + 1)
            )
            require(record["candidate_evaluations"] == record["information_sets_accepted"] * patterns_per_set, "information-budget Lee-Brickell pattern accounting mismatch")
        else:
            require(record["termination_reason"] == "trivial_code_no_nonzero_word", "unknown Lee-Brickell termination")
            require(record["k"] == 0 and record["candidate_evaluations"] == 0, "Lee-Brickell trivial termination requires k=0")

    h_rows, n = parse_h_rows(case["H_rows"])
    bits = record["best_candidate_bits"]
    if bits is None:
        require(record["best_candidate_sha256"] is None and record["best_weight"] is None, "null candidate tuple is inconsistent")
        require(record["witness_verified"] is False and record["threshold_hit"] is False, "null candidate cannot be verified or hit threshold")
        require(record["threshold_witnesses_seen"] == 0, "null incumbent conflicts with threshold-witness count")
        candidate_weight = None
    else:
        require(isinstance(bits, str) and len(bits) == n and set(bits) <= {"0", "1"}, "candidate bit string is malformed")
        require(record["best_candidate_sha256"] == candidate_sha256(bits), "candidate hash mismatch")
        candidate_word = bits_to_word(bits)
        candidate_weight = verify_nonzero_kernel_word(h_rows, n, candidate_word)
        genuine_int(record["best_weight"], "best_weight", minimum=1, maximum=n)
        require(record["best_weight"] == candidate_weight, "candidate weight mismatch")
        require(record["witness_verified"] is True, "stored candidate is not marked verified")
        require(record["threshold_hit"] is (candidate_weight <= record["public_W"]), "threshold-hit flag mismatch")
        require(record["threshold_hit"] is (record["threshold_witnesses_seen"] > 0), "threshold counter/flag mismatch")
    require((record["valid_codewords_seen"] > 0) is (bits is not None), "valid-codeword count/incumbent presence mismatch")

    if baseline_id in (UNIFORM_KERNEL_SAMPLING, LEE_BRICKELL_ISD):
        require(record["valid_codewords_seen"] == record["candidate_evaluations"], "kernel-derived baseline emitted a non-codeword")

    label = case["label"]
    if label["kind"] == "certified_distance" and candidate_weight is not None:
        expected_gap = candidate_weight - label["distance"]
        require(expected_gap >= 0, "candidate contradicts theorem-certified distance")
        genuine_int(record["known_distance_gap"], "known_distance_gap", minimum=0)
        require(record["known_distance_gap"] == expected_gap, "known-distance gap mismatch")
    else:
        require(record["known_distance_gap"] is None, "distance gap is forbidden without a theorem-certified candidate comparison")
    require(record["optimality_claim"] is False, "baseline record must not claim global optimality")

    expected_status, expected_scope = _expected_record_status(record)
    require(record["conservative_status"] == expected_status, "conservative status is inconsistent")
    require(record["certificate_scope"] == expected_scope, "certificate scope is inconsistent")
    if baseline_id != CP_SAT_THRESHOLD_REFERENCE:
        require(record["solver_calls"] == 0, "solver-disabled record contains solver calls")
        require(record["solver_time_s"] == 0.0, "solver-disabled record contains solver time")
        require(record["solver_status"] is None and record["solver_status_raw"] is None, "solver-disabled record contains solver status")
        require(record["threshold_infeasibility_certified"] is False, "solver-disabled record claims infeasibility")
    else:
        require(record["solver_calls"] == 1, "CP-SAT reference must make exactly one call")
        require(record["iterations"] == 1, "CP-SAT iteration count must equal its one solver call")
        require(record["information_set_attempts"] == 0, "CP-SAT record contains information-set attempts")
        require(record["solver_status"] in {"FEASIBLE", "INFEASIBLE", "UNKNOWN"}, "unrecognized normalized CP-SAT status")
        require(isinstance(record["solver_status_raw"], str) and record["solver_status_raw"], "missing raw CP-SAT status")
        if record["solver_status"] == "FEASIBLE":
            require(record["solver_status_raw"] in {"OPTIMAL", "FEASIBLE"}, "raw CP-SAT feasible status is inconsistent")
            require(bits is not None and record["threshold_hit"], "FEASIBLE requires a verified threshold witness")
            require(record["candidate_evaluations"] == record["valid_codewords_seen"] == 1, "FEASIBLE external-witness accounting must equal one")
            require(record["threshold_infeasibility_certified"] is False, "FEASIBLE cannot certify infeasibility")
            require(record["termination_reason"] == "solver_feasible", "wrong CP-SAT FEASIBLE termination")
        elif record["solver_status"] == "INFEASIBLE":
            require(record["solver_status_raw"] == "INFEASIBLE", "raw CP-SAT infeasible status is inconsistent")
            require(bits is None, "INFEASIBLE cannot carry a witness")
            require(record["candidate_evaluations"] == 0, "INFEASIBLE cannot contain an external candidate")
            require(record["threshold_infeasibility_certified"] is True, "INFEASIBLE must carry threshold-only certificate scope")
            require(record["termination_reason"] == "solver_infeasible", "wrong CP-SAT INFEASIBLE termination")
            if label["kind"] == "certified_distance":
                require(label["distance"] > record["public_W"], "INFEASIBLE contradicts theorem-certified threshold")
        else:
            require(record["solver_status_raw"] == "UNKNOWN", "raw CP-SAT unknown status is inconsistent")
            require(bits is None, "UNKNOWN cannot carry a solver witness")
            require(record["candidate_evaluations"] == 0, "UNKNOWN cannot contain an external candidate")
            require(record["threshold_infeasibility_certified"] is False, "UNKNOWN cannot certify infeasibility")
            require(record["termination_reason"] == "solver_unknown_or_limit", "wrong CP-SAT UNKNOWN termination")

    require(isinstance(record["environment"], dict) and set(record["environment"]) == ENVIRONMENT_KEYS, "environment schema mismatch")
    for name in ("python_version", "python_implementation", "platform"):
        require(isinstance(record["environment"][name], str) and record["environment"][name], f"invalid environment {name}")
    require(record["environment"]["ortools_version"] is None or isinstance(record["environment"]["ortools_version"], str), "invalid OR-Tools version")
    require(isinstance(record["source"], dict) and set(record["source"]) == SOURCE_KEYS, "source schema mismatch")
    for name in ("baseline_module_sha256", "corpus_module_sha256", "manifest_file_sha256"):
        require(is_sha256(record["source"][name]), f"invalid source hash {name}")
    require(record["source"]["git_commit_sha"] is None or is_git_oid(record["source"]["git_commit_sha"]), "invalid git commit SHA")
    require(record["source"]["git_dirty"] is None or isinstance(record["source"]["git_dirty"], bool), "invalid git dirty flag")
    if check_current_source:
        require(manifest_path is not None, "manifest_path is required for source validation")
        expected_source = source_provenance(manifest_path)
        for name in SOURCE_KEYS:
            require(record["source"][name] == expected_source[name], f"current source hash mismatch for {name}")
    require(is_sha256(record["reproducible_core_sha256"]), "invalid reproducible-core digest")
    require(record["reproducible_core_sha256"] == compute_reproducible_core_sha256(record), "reproducible-core digest mismatch")


def _run_identity(record: dict[str, Any]) -> tuple[Any, ...]:
    return (
        record["manifest_sha256"],
        record["profile_id"],
        record["case_id"],
        record["baseline_id"],
        record["repetition_id"],
        record["declared_seed"],
        record["algorithm_config_sha256"],
    )


def read_validate_jsonl(
    results_path: str | os.PathLike[str],
    manifest: dict[str, Any],
    *,
    manifest_path: Path | None = None,
    check_current_source: bool = False,
) -> list[dict[str, Any]]:
    path = Path(results_path)
    require(path.is_file(), f"results file does not exist: {path}")
    data = path.read_bytes()
    require(data, "results JSONL is empty")
    require(data.endswith(b"\n"), "results JSONL must end with a newline")
    require(b"\r" not in data, "results JSONL must use canonical LF line endings")
    records: list[dict[str, Any]] = []
    seen_identities: set[tuple[Any, ...]] = set()
    for line_number, raw_line in enumerate(data.splitlines(), start=1):
        require(raw_line, f"blank JSONL line at {line_number}")
        try:
            line = raw_line.decode("ascii")
        except UnicodeDecodeError:
            fail(f"JSONL line {line_number} is not ASCII canonical JSON")
        record = strict_json_loads(line)
        require(canonical_json_bytes(record) == raw_line, f"JSONL line {line_number} is not canonical")
        validate_result_record(
            record,
            manifest,
            manifest_path=manifest_path,
            check_current_source=check_current_source,
        )
        identity = _run_identity(record)
        require(identity not in seen_identities, f"duplicate run identity at JSONL line {line_number}")
        seen_identities.add(identity)
        records.append(record)
    return records


def write_canonical_jsonl(
    records: Sequence[dict[str, Any]], output_dir: str | os.PathLike[str], *, overwrite: bool = False
) -> Path:
    require(records, "refusing to write an empty result set")
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / RESULT_FILENAME
    if path.exists() and not overwrite:
        fail(f"result file already exists: {path}; pass --overwrite to replace it")
    payload = b"".join(canonical_json_bytes(record) + b"\n" for record in records)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{RESULT_FILENAME}.", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return path


def summarize_records(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    require(records, "cannot summarize an empty result set")
    groups: dict[str, dict[str, Any]] = {}
    for record in records:
        group_key = f"{record['solver_stratum']}::{record['baseline_id']}"
        group = groups.setdefault(
            group_key,
            {
                "solver_stratum": record["solver_stratum"],
                "baseline_id": record["baseline_id"],
                "records": 0,
                "threshold_hits": 0,
                "candidate_evaluations": 0,
                "objective_evaluations": 0,
                "information_set_attempts": 0,
                "solver_calls": 0,
                "runtime_s": 0.0,
                "solver_time_s": 0.0,
                "max_peak_traced_memory_bytes": None,
                "max_process_rss_bytes": None,
                "solver_status_counts": {},
                "case_metrics": {},
            },
        )
        group["records"] += 1
        group["threshold_hits"] += int(record["threshold_hit"])
        group["candidate_evaluations"] += record["candidate_evaluations"]
        group["objective_evaluations"] += record["objective_evaluations"]
        group["information_set_attempts"] += record["information_set_attempts"]
        group["solver_calls"] += record["solver_calls"]
        group["runtime_s"] += record["total_runtime_s"]
        group["solver_time_s"] += record["solver_time_s"]
        if record["peak_traced_memory_bytes"] is not None:
            current_peak = group["max_peak_traced_memory_bytes"]
            group["max_peak_traced_memory_bytes"] = (
                record["peak_traced_memory_bytes"]
                if current_peak is None
                else max(current_peak, record["peak_traced_memory_bytes"])
            )
        if record["process_max_rss_bytes"] is not None:
            current_rss = group["max_process_rss_bytes"]
            group["max_process_rss_bytes"] = (
                record["process_max_rss_bytes"]
                if current_rss is None
                else max(current_rss, record["process_max_rss_bytes"])
            )
        status = record["solver_status"] or "NOT_INVOKED"
        group["solver_status_counts"][status] = group["solver_status_counts"].get(status, 0) + 1
        case_metric = group["case_metrics"].setdefault(
            record["case_id"],
            {
                "case_id": record["case_id"],
                "runs": 0,
                "threshold_hits": 0,
                "best_verified_weight": None,
                "best_known_distance_gap": None,
            },
        )
        case_metric["runs"] += 1
        case_metric["threshold_hits"] += int(record["threshold_hit"])
        if record["best_weight"] is not None:
            current_weight = case_metric["best_verified_weight"]
            case_metric["best_verified_weight"] = (
                record["best_weight"]
                if current_weight is None
                else min(current_weight, record["best_weight"])
            )
        if record["known_distance_gap"] is not None:
            current_gap = case_metric["best_known_distance_gap"]
            case_metric["best_known_distance_gap"] = (
                record["known_distance_gap"]
                if current_gap is None
                else min(current_gap, record["known_distance_gap"])
            )
    for group in groups.values():
        group["threshold_hit_rate"] = group["threshold_hits"] / group["records"]
        group["runtime_s"] = round(group["runtime_s"], 6)
        group["solver_time_s"] = round(group["solver_time_s"], 6)
        group["case_metrics"] = [group["case_metrics"][case_id] for case_id in sorted(group["case_metrics"])]
    return {
        "result_schema_version": RESULT_SCHEMA_VERSION,
        "records": len(records),
        "groups": [groups[key] for key in sorted(groups)],
    }


def print_compact_summary(summary: dict[str, Any]) -> None:
    print(f"validated_records={summary['records']}")
    for group in summary["groups"]:
        print(
            f"{group['solver_stratum']} {group['baseline_id']}: "
            f"runs={group['records']} threshold_hits={group['threshold_hits']} "
            f"candidates={group['candidate_evaluations']} information_sets={group['information_set_attempts']} "
            f"solver_calls={group['solver_calls']} runtime_s={group['runtime_s']} "
            f"solver_time_s={group['solver_time_s']} "
            f"max_peak_memory={group['max_peak_traced_memory_bytes']} "
            f"max_rss={group['max_process_rss_bytes']} "
            f"solver_statuses={canonical_json_bytes(group['solver_status_counts']).decode('ascii')}"
        )
        for case_metric in group["case_metrics"]:
            print(
                f"  {case_metric['case_id']}: runs={case_metric['runs']} "
                f"threshold_hits={case_metric['threshold_hits']} "
                f"best_weight={case_metric['best_verified_weight']} "
                f"best_known_gap={case_metric['best_known_distance_gap']}"
            )


def build_run_plan(
    manifest: dict[str, Any],
    *,
    profile_id: str,
    baseline_ids: Sequence[str] | None = None,
    case_ids: Sequence[str] | None = None,
    repetition_ids: Sequence[int] | None = None,
    seeds: Sequence[int] | None = None,
) -> list[dict[str, Any]]:
    cases = select_cases(manifest, profile_id, case_ids)
    baselines = select_baselines(baseline_ids)
    runs = select_run_pairs(profile_id, repetition_ids, seeds)
    plan: list[dict[str, Any]] = []
    for case in cases:
        for baseline_id in baselines:
            config = algorithm_config(profile_id, baseline_id, n=case["n"], k=case["k"])
            for run in runs:
                plan.append(
                    {
                        "case_id": case["case_id"],
                        "family": case["family"],
                        "n": case["n"],
                        "rank": case["rank"],
                        "k": case["k"],
                        "W": case["threshold"]["W"],
                        "baseline_id": baseline_id,
                        "solver_stratum": SOLVER_STRATA[baseline_id],
                        "repetition_id": run["repetition_id"],
                        "seed": run["seed"],
                        "algorithm_config": config,
                        "algorithm_config_sha256": sha256_object(config),
                    }
                )
    return plan


def _expect_validation_failure(label: str, function: Any) -> None:
    try:
        function()
    except ValueError:
        return
    fail(f"negative self-test unexpectedly passed: {label}")


def run_self_tests(manifest_path: Path | None = None) -> dict[str, Any]:
    path = manifest_path or Path(__file__).resolve().with_name("h_native_research_v1_manifest.json")
    manifest = load_manifest(path)

    tiny_rows, tiny_n = parse_h_rows(["1011", "0110"])
    basis, pivots, free = deterministic_kernel_basis(tiny_rows, tiny_n)
    require(len(basis) == tiny_n - len(pivots) == len(free), "tiny nullspace dimension mismatch")
    enumerated = {kernel_word(basis, mask) for mask in range(1 << len(basis))}
    direct = {word for word in range(1 << tiny_n) if syndrome_is_zero(tiny_rows, word)}
    require(enumerated == direct, "tiny nullspace basis does not enumerate the exact kernel")
    row_operated = [tiny_rows[0] ^ tiny_rows[1], tiny_rows[1], 0]
    require(deterministic_kernel_basis(row_operated, tiny_n)[0] == basis, "kernel basis changed under row operations")

    key = bytes.fromhex("00" * 32)
    rng_a = Sha256CounterRng(key)
    rng_b = Sha256CounterRng(key)
    known_prng_hex = rng_a.bytes(40).hex()
    require(known_prng_hex == rng_b.bytes(40).hex(), "PRNG replay mismatch")
    require(
        known_prng_hex == "17b0761f87b081d5cf10757ccc89f12be355c70e2e29df288b65b30710dcbcd186f77354f38f799c",
        "PRNG known-answer vector changed",
    )

    hamming = _case_by_id(manifest, "hnrv1-c0001")
    h_rows, n = parse_h_rows(hamming["H_rows"])
    require(verify_nonzero_kernel_word(h_rows, n, (1 << 0) | (1 << 1) | (1 << 2)) == 3, "Hamming control failed")
    extended = _case_by_id(manifest, "hnrv1-c0002")
    h_rows, n = parse_h_rows(extended["H_rows"])
    extended_word = (1 << 0) | (1 << 1) | (1 << 2) | (1 << (n - 1))
    require(verify_nonzero_kernel_word(h_rows, n, extended_word) == 4, "extended-Hamming control failed")
    rm = _case_by_id(manifest, "hnrv1-c0009")
    h_rows, n = parse_h_rows(rm["H_rows"])
    rm_word = bits_to_word(research_corpus.rm1_generator_rows(5)[1])
    require(verify_nonzero_kernel_word(h_rows, n, rm_word) == 16, "RM(1,5) control failed")

    configs = {
        baseline_id: algorithm_config("smoke", baseline_id, n=hamming["n"], k=hamming["k"])
        for baseline_id in DEFAULT_BASELINE_IDS
    }
    replay_hashes: dict[str, str] = {}
    for baseline_id, config in configs.items():
        first = run_baseline_record(
            manifest=manifest,
            manifest_path=path,
            case=hamming,
            profile_id="smoke",
            baseline_id=baseline_id,
            repetition_id=0,
            seed=101,
            config=config,
        )
        second = run_baseline_record(
            manifest=manifest,
            manifest_path=path,
            case=hamming,
            profile_id="smoke",
            baseline_id=baseline_id,
            repetition_id=0,
            seed=101,
            config=config,
        )
        validate_result_record(first, manifest, manifest_path=path, check_current_source=True)
        validate_result_record(second, manifest, manifest_path=path, check_current_source=True)
        require(canonical_json_bytes(reproducible_core(first)) == canonical_json_bytes(reproducible_core(second)), f"deterministic replay failed for {baseline_id}")
        replay_hashes[baseline_id] = first["reproducible_core_sha256"]
        tampered = copy.deepcopy(first)
        tampered["candidate_evaluations"] += 1
        _expect_validation_failure(
            f"counter tamper for {baseline_id}",
            lambda record=tampered: validate_result_record(record, manifest, manifest_path=path),
        )

    _expect_validation_failure("boolean budget", lambda: genuine_int(True, "budget", minimum=0))
    _expect_validation_failure("float budget", lambda: genuine_int(1.0, "budget", minimum=0))
    _expect_validation_failure("negative budget", lambda: genuine_int(-1, "budget", minimum=0))
    _expect_validation_failure("NaN timing", lambda: finite_nonnegative_real(float("nan"), "timing"))
    _expect_validation_failure("infinite timing", lambda: finite_nonnegative_real(float("inf"), "timing"))

    return {
        "baseline_protocol_version": BASELINE_PROTOCOL_VERSION,
        "manifest_sha256": manifest["manifest_sha256"],
        "kernel_basis_size": len(basis),
        "prng_known_answer_sha256": hashlib.sha256(bytes.fromhex(known_prng_hex)).hexdigest(),
        "replay_hashes": replay_hashes,
        "controls": {"hamming": 3, "extended_hamming": 4, "rm1_5": 16},
    }


def _add_selection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest", default=str(Path(__file__).resolve().with_name("h_native_research_v1_manifest.json")))
    parser.add_argument("--profile", choices=tuple(PROFILE_SPECS), required=True)
    parser.add_argument("--baseline", dest="baseline_ids", action="append", choices=BASELINE_IDS)
    parser.add_argument("--case-id", dest="case_ids", action="append")
    parser.add_argument("--repetition-id", dest="repetition_ids", action="append", type=int)
    parser.add_argument("--seed", dest="seeds", action="append", type=int)


def _print_plan(plan: Sequence[dict[str, Any]]) -> None:
    print("case_id\tfamily\tn\trank\tk\tW\tbaseline_id\tstratum\trepetition\tseed\tbudgets")
    for item in plan:
        budgets = requested_budgets(item["baseline_id"], item["algorithm_config"])
        print(
            "\t".join(
                str(value)
                for value in (
                    item["case_id"],
                    item["family"],
                    item["n"],
                    item["rank"],
                    item["k"],
                    item["W"],
                    item["baseline_id"],
                    item["solver_stratum"],
                    item["repetition_id"],
                    item["seed"],
                    canonical_json_bytes(budgets).decode("ascii"),
                )
            )
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="list selected runs without executing algorithms")
    _add_selection_arguments(list_parser)
    list_parser.add_argument("--json", action="store_true", help="print the canonical JSON run plan")

    run_parser = subparsers.add_parser("run", help="execute a frozen smoke/full profile")
    _add_selection_arguments(run_parser)
    run_parser.add_argument("--output-dir", required=True)
    run_parser.add_argument("--overwrite", action="store_true")
    run_parser.add_argument("--json-summary", action="store_true")

    validate_parser = subparsers.add_parser("validate", help="validate stored JSONL without rerunning algorithms")
    validate_parser.add_argument("results")
    validate_parser.add_argument("--manifest", default=str(Path(__file__).resolve().with_name("h_native_research_v1_manifest.json")))
    validate_parser.add_argument("--print-summary", action="store_true")
    validate_parser.add_argument("--json-summary", action="store_true")
    validate_parser.add_argument("--skip-current-source-check", action="store_true")

    summary_parser = subparsers.add_parser("summary", help="validate and summarize stored JSONL")
    summary_parser.add_argument("results")
    summary_parser.add_argument("--manifest", default=str(Path(__file__).resolve().with_name("h_native_research_v1_manifest.json")))
    summary_parser.add_argument("--json", action="store_true")
    summary_parser.add_argument("--skip-current-source-check", action="store_true")

    self_test_parser = subparsers.add_parser("self-test", help="run deterministic built-in contract tests")
    self_test_parser.add_argument("--manifest", default=str(Path(__file__).resolve().with_name("h_native_research_v1_manifest.json")))
    self_test_parser.add_argument("--print-summary", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.command == "self-test":
        result = run_self_tests(Path(arguments.manifest))
        if arguments.print_summary:
            print(canonical_json_bytes(result).decode("ascii"))
        return 0

    manifest_path = Path(arguments.manifest)
    manifest = load_manifest(
        manifest_path,
        smoke_only=arguments.command in {"list", "run"} and arguments.profile == "smoke",
    )
    if arguments.command in {"validate", "summary"}:
        records = read_validate_jsonl(
            arguments.results,
            manifest,
            manifest_path=manifest_path,
            check_current_source=not arguments.skip_current_source_check,
        )
        summary = summarize_records(records)
        if arguments.command == "validate":
            print(f"validated {len(records)} canonical result records")
            if arguments.print_summary:
                print_compact_summary(summary)
            if arguments.json_summary:
                print(canonical_json_bytes(summary).decode("ascii"))
        elif arguments.json:
            print(canonical_json_bytes(summary).decode("ascii"))
        else:
            print_compact_summary(summary)
        return 0

    plan = build_run_plan(
        manifest,
        profile_id=arguments.profile,
        baseline_ids=arguments.baseline_ids,
        case_ids=arguments.case_ids,
        repetition_ids=arguments.repetition_ids,
        seeds=arguments.seeds,
    )
    if arguments.command == "list":
        if arguments.json:
            print(canonical_json_bytes(plan).decode("ascii"))
        else:
            _print_plan(plan)
        return 0

    records: list[dict[str, Any]] = []
    for item in plan:
        case = _case_by_id(manifest, item["case_id"])
        record = run_baseline_record(
            manifest=manifest,
            manifest_path=manifest_path,
            case=case,
            profile_id=arguments.profile,
            baseline_id=item["baseline_id"],
            repetition_id=item["repetition_id"],
            seed=item["seed"],
            config=item["algorithm_config"],
        )
        validate_result_record(record, manifest, manifest_path=manifest_path, check_current_source=True)
        records.append(record)
    results_path = write_canonical_jsonl(records, arguments.output_dir, overwrite=arguments.overwrite)
    validated = read_validate_jsonl(
        results_path,
        manifest,
        manifest_path=manifest_path,
        check_current_source=True,
    )
    summary = summarize_records(validated)
    print(results_path)
    if arguments.json_summary:
        print(canonical_json_bytes(summary).decode("ascii"))
    else:
        print_compact_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
