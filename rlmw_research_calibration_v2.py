"""Operational external calibration harness for ``h-native-research-v2``.

The module is source-only orchestration.  It consumes a validated accepted
candidate-pool manifest, emits deterministic phase plans, executes v2 adapters
for classical baselines, validates/merges JSONL shard evidence, fits thresholds,
and validates tiers.  It never commits generated manifests, calibration JSONL,
thresholds, tiers, logs, matrices, or final-evaluation seed material.
"""
from __future__ import annotations

import argparse, copy, hashlib, importlib.metadata, json, math, platform, subprocess, sys, time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import rlmw_research_baselines as v1
import rlmw_research_corpus_v2 as corpus_v2
import rlmw_research_isd_v2 as isd_v2

PROTOCOL_ID = "h-native-research-v2-calibration-v2"
FIT_PLAN_SCHEMA = "rlmw-research-v2-threshold-fit-plan-v2"
TIER_PLAN_SCHEMA = "rlmw-research-v2-tier-reference-plan-v2"
RESULT_SCHEMA = "rlmw-research-v2-calibration-result-v2"
THRESHOLD_SCHEMA = "rlmw-research-v2-threshold-artifact-v2"
TIER_SCHEMA = "rlmw-research-v2-tier-artifact-v2"
SOLVER_DISABLED = "solver_disabled"
SOLVER_ASSISTED = "solver_assisted_reference"
UNIFORM = v1.UNIFORM_KERNEL_SAMPLING
FIXED = v1.FIXED_WEIGHT_SUBSET_SAMPLING
LEE = v1.LEE_BRICKELL_ISD
STERN = isd_v2.ALGORITHM_ID
CP_SAT = v1.CP_SAT_THRESHOLD_REFERENCE
SOLVER_DISABLED_ALGORITHMS = (UNIFORM, FIXED, LEE, STERN)
BUDGETS = (1 << 12, 1 << 14, 1 << 16, 1 << 18)
FIT_PHASE, TIER_PHASE, REFERENCE_PHASE = "threshold_fit", "tier_validation", "solver_assisted_reference"
FIT_ROLE, TIER_ROLE = "threshold_fit_seed", "tier_validation_seed"
CP_SAT_SEED_PAIRS = ((FIT_PHASE, FIT_ROLE, 0), (FIT_PHASE, FIT_ROLE, 1), (TIER_PHASE, TIER_ROLE, 0), (TIER_PHASE, TIER_ROLE, 1))
CP_SAT_PROFILES = (
    {"profile_id": "cp_sat_60s_dt1e7_v2", "solver_call_budget": 1, "max_time_seconds": 60.0, "max_deterministic_time": 10_000_000.0, "num_search_workers": 1},
    {"profile_id": "cp_sat_600s_dt1e8_v2", "solver_call_budget": 1, "max_time_seconds": 600.0, "max_deterministic_time": 100_000_000.0, "num_search_workers": 1},
)
FORBIDDEN_PAYLOAD_KEYS = {"family_id", "split", "lineage_group_id", "evaluator_only_provenance", "validation", "structural_status", "exact_distance", "planted_witness", "planted_witness_support", "calibration_incumbent", "solver_assisted_evidence"}
RESOURCE_TERMINATIONS = {"resource_limit", "dependency_unavailable", "timeout", "error"}

class CalibrationV2Error(ValueError): pass
def fail(message: str) -> None: raise CalibrationV2Error(f"{PROTOCOL_ID}: {message}")
def require(condition: bool, message: str) -> None:
    if not condition: fail(message)
def genuine_int(value: Any, name: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    require(isinstance(value, int) and not isinstance(value, bool), f"{name} must be a genuine integer")
    if minimum is not None: require(value >= minimum, f"{name} below minimum")
    if maximum is not None: require(value <= maximum, f"{name} above maximum")
    return value
def is_sha256(value: Any) -> bool: return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)
def canonical_bytes(value: Any) -> bytes: return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
def digest(value: Any) -> str: return hashlib.sha256(canonical_bytes(value)).hexdigest()
def sha_file(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def current_commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
class RejectDuplicateKeys(dict):
    def __init__(self, pairs: Iterable[tuple[str, Any]]):
        super().__init__()
        for key, value in pairs:
            if key in self: fail(f"duplicate JSON key {key!r}")
            self[key] = value
def read_json(path: Path) -> Any:
    raw = path.read_bytes(); obj = json.loads(raw.decode("ascii"), object_pairs_hook=RejectDuplicateKeys)
    require(canonical_bytes(obj) == raw.rstrip(b"\n"), f"{path} is not canonical JSON")
    return obj
def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(canonical_bytes(obj) + b"\n")
def read_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line_no, raw in enumerate(path.read_bytes().splitlines(), 1):
        require(bool(raw), f"blank JSONL line {line_no}")
        obj = json.loads(raw.decode("ascii"), object_pairs_hook=RejectDuplicateKeys)
        require(canonical_bytes(obj) == raw, f"line {line_no} is not canonical JSON")
        out.append(obj)
    return out
def write_jsonl(path: Path, records: Sequence[dict[str, Any]]) -> None:
    require(records, "refusing to write empty shard output")
    require(not path.exists(), f"{path} already exists; refusing unsafe overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(canonical_bytes(record) + b"\n" for record in records))

def dependency_versions() -> dict[str, Any]:
    versions = {"python": sys.version.split()[0], "platform": platform.platform()}
    for package in ("ortools", "numpy", "scipy"):
        try: versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError: versions[package] = None
    return versions

def module_digests() -> dict[str, str]:
    return {
        "calibration_module_sha256": sha_file(Path(__file__)),
        "candidate_config_digest": corpus_v2.config_digest(),
        "candidate_module_sha256": sha_file(Path(corpus_v2.__file__)),
        "v1_baselines_module_sha256": sha_file(Path(v1.__file__)),
        "isd_v2_module_sha256": sha_file(Path(isd_v2.__file__)),
    }

def load_candidate_manifest(path: Path, *, full: bool = True, allow_fixture: bool = False) -> dict[str, Any]:
    payload = read_json(path)
    if allow_fixture and payload.get("manifest_kind") == "calibration_fixture_manifest":
        return payload
    corpus_v2.validate_manifest(payload, full=full)
    require(payload.get("generation_profile") == "accepted", "candidate manifest must be accepted")
    require(payload.get("calibration_ready") is True, "candidate manifest must be calibration_ready=true")
    require(len(payload.get("records", [])) == 192, "candidate manifest must contain exactly 192 records")
    require(payload.get("configuration_digest") == corpus_v2.config_digest(), "candidate config digest mismatch")
    require(isinstance(payload.get("source_commit"), str) and len(payload["source_commit"]) >= 40, "candidate manifest requires concrete source commit")
    return payload

def manifest_digest(manifest: Mapping[str, Any]) -> str: return manifest.get("candidate_manifest_digest") or digest({k: v for k, v in manifest.items() if k != "candidate_manifest_digest"})
def case_public(rec: Mapping[str, Any]) -> dict[str, Any]: return {"case_id": rec["case_id"], "H_rows": rec["H_rows"], "public_h_sha256": rec["public_h_sha256"]}
def validate_public_case(case: Mapping[str, Any]) -> None:
    require(set(case) == {"case_id", "H_rows", "public_h_sha256"}, "public case has forbidden or missing keys")
    require(case["public_h_sha256"] == isd_v2.public_h_sha256(case["H_rows"]), "public H hash mismatch")
def evaluator_metadata(rec: Mapping[str, Any]) -> dict[str, Any]:
    meta = {"family_id": rec.get("family_id"), "n": rec.get("n"), "certified_lower_bound": 1, "exact_distance": None, "planted_upper_bound": None, "is_planted": "planted" in str(rec.get("family_id", ""))}
    val = rec.get("validation", {}) if isinstance(rec.get("validation"), dict) else {}
    if isinstance(val.get("known_distance"), dict): meta["exact_distance"] = val["known_distance"].get("distance")
    prov = rec.get("evaluator_only_provenance", {}) if isinstance(rec.get("evaluator_only_provenance"), dict) else {}
    supp = prov.get("planted_witness_support")
    if isinstance(supp, list): meta["planted_upper_bound"] = len(supp)
    sc = val.get("small_circuit", {}) if isinstance(val.get("small_circuit"), dict) else {}
    if isinstance(sc.get("cap"), int) and sc.get("status") == "PASS": meta["certified_lower_bound"] = sc["cap"] + 1
    return meta

def algorithm_config(algorithm_id: str, *, budget: int, n: int, rank: int) -> dict[str, Any]:
    genuine_int(budget, "budget", minimum=0); k = n - rank
    effective_budget = min(budget, 32) if n <= 16 else budget
    if algorithm_id == UNIFORM:
        cfg = {"candidate_budget": effective_budget, "sampling_with_replacement": True, "exhaust_candidate_budget": True, "prng_version": v1.PRNG_VERSION}
        v1.validate_algorithm_config(UNIFORM, cfg, n=n, k=k); return cfg
    if algorithm_id == FIXED:
        cfg = {"candidate_budget": effective_budget, "weights": list(range(1, min(12, n) + 1)), "weight_schedule": "round_robin", "sampling_with_replacement_across_iterations": True, "exhaust_candidate_budget": True, "prng_version": v1.PRNG_VERSION}
        v1.validate_algorithm_config(FIXED, cfg, n=n, k=k); return cfg
    if algorithm_id == LEE:
        cfg = {"candidate_budget": effective_budget, "information_set_budget": (8 if n <= 16 else 4096), "max_information_weight": 1 if k <= 1 else 2, "pattern_mode": "enumerate_nonzero_weight_at_most_p_lexicographic", "information_set_sampling": "uniform_r_subset_with_replacement", "exhaust_candidate_budget": True, "prng_version": v1.PRNG_VERSION}
        v1.validate_algorithm_config(LEE, cfg, n=n, k=k); return cfg
    if algorithm_id == STERN:
        return isd_v2.algorithm_config("calibration", rank=rank) | {"candidate_budget_adapter": effective_budget}
    if algorithm_id == CP_SAT:
        return copy.deepcopy(CP_SAT_PROFILES[0])
    fail(f"unknown algorithm {algorithm_id}")

def config_digest(config: Mapping[str, Any]) -> str: return digest(config)
def seed_hex(role: str, index: int) -> str: return corpus_v2.calibration_seed(role, index)
def rng_for(run: Mapping[str, Any], cfg_digest: str) -> v1.Sha256CounterRng:
    return v1.Sha256CounterRng(hashlib.sha256(canonical_bytes([PROTOCOL_ID, run["case_id"], run["public_h_sha256"], run["algorithm_id"], run["phase"], run["seed_role"], run["seed_index"], run["budget"], cfg_digest])).digest())

def common_plan_fields(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {"protocol_id": PROTOCOL_ID, "candidate_manifest_sha256": manifest_digest(manifest), "candidate_generator_config_sha256": manifest.get("configuration_digest", corpus_v2.config_digest()), "calibration_source_commit": current_commit(), "module_digests": module_digests(), "dependency_versions": dependency_versions()}

def build_threshold_fit_plan(manifest: Mapping[str, Any]) -> dict[str, Any]:
    common = common_plan_fields(manifest); runs = []
    for rec in manifest["records"]:
        case = case_public(rec); validate_public_case(case); rows, n = v1.parse_h_rows(case["H_rows"]); rank = v1.gf2_rank_bit_rows(rows, n)
        for alg in SOLVER_DISABLED_ALGORITHMS:
            cfg = algorithm_config(alg, budget=BUDGETS[-1], n=n, rank=rank); cd = config_digest(cfg)
            for seed_index in range(8):
                runs.append({"run_schema": "fit-run-v2", **case, "solver_stratum": SOLVER_DISABLED, "algorithm_id": alg, "algorithm_config": cfg, "algorithm_config_sha256": cd, "phase": FIT_PHASE, "seed_role": FIT_ROLE, "seed_index": seed_index, "seed_hex": seed_hex(FIT_ROLE, seed_index), "budget": BUDGETS[-1]})
    plan = {"schema": FIT_PLAN_SCHEMA, **common, "runs": runs}; plan["plan_sha256"] = digest({k:v for k,v in plan.items() if k != "plan_sha256"}); return plan

def threshold_map(thresholds: Mapping[str, Any]) -> dict[str, Any]: return {t["case_id"]: t for t in thresholds["thresholds"]}
def build_tier_reference_plan(manifest: Mapping[str, Any], thresholds: Mapping[str, Any]) -> dict[str, Any]:
    validate_threshold_artifact(thresholds, manifest=manifest); tm = threshold_map(thresholds); common = common_plan_fields(manifest); runs = []
    for rec in manifest["records"]:
        case = case_public(rec); rows, n = v1.parse_h_rows(case["H_rows"]); rank = v1.gf2_rank_bit_rows(rows, n); W = tm[case["case_id"]]["W"]
        for alg in SOLVER_DISABLED_ALGORITHMS:
            for budget in BUDGETS:
                cfg = algorithm_config(alg, budget=budget, n=n, rank=rank); cd = config_digest(cfg)
                for seed_index in range(8): runs.append({"run_schema": "tier-run-v2", **case, "solver_stratum": SOLVER_DISABLED, "algorithm_id": alg, "algorithm_config": cfg, "algorithm_config_sha256": cd, "phase": TIER_PHASE, "seed_role": TIER_ROLE, "seed_index": seed_index, "seed_hex": seed_hex(TIER_ROLE, seed_index), "budget": budget, "W": W})
        if W is not None:
            for profile in CP_SAT_PROFILES:
                cfg = copy.deepcopy(profile); cd = config_digest(cfg)
                for phase, role, seed_index in CP_SAT_SEED_PAIRS: runs.append({"run_schema": "reference-run-v2", **case, "solver_stratum": SOLVER_ASSISTED, "algorithm_id": CP_SAT, "algorithm_config": cfg, "algorithm_config_sha256": cd, "phase": REFERENCE_PHASE, "seed_role": role, "seed_index": seed_index, "seed_hex": seed_hex(role, seed_index), "budget": 0, "W": W, "reference_source_phase": phase})
    plan = {"schema": TIER_PLAN_SCHEMA, **common, "thresholds_sha256": thresholds["thresholds_sha256"], "runs": runs}; plan["plan_sha256"] = digest({k:v for k,v in plan.items() if k != "plan_sha256"}); return plan

def validate_plan(plan: Mapping[str, Any], *, expected_schema: str | None = None) -> None:
    require(plan.get("schema") in (FIT_PLAN_SCHEMA, TIER_PLAN_SCHEMA), "unknown plan schema")
    if expected_schema: require(plan.get("schema") == expected_schema, "wrong plan phase")
    require(plan.get("protocol_id") == PROTOCOL_ID, "wrong protocol")
    for key in ("candidate_manifest_sha256", "candidate_generator_config_sha256", "calibration_source_commit", "module_digests", "runs", "plan_sha256"):
        require(key in plan, f"missing plan field {key}")
    require(is_sha256(plan["candidate_manifest_sha256"]) and is_sha256(plan["candidate_generator_config_sha256"]), "bad plan digests")
    require(isinstance(plan["calibration_source_commit"], str) and len(plan["calibration_source_commit"]) >= 40, "bad source commit")
    require(plan["plan_sha256"] == digest({k:v for k,v in plan.items() if k != "plan_sha256"}), "plan digest mismatch")
    seen = set()
    for run in plan["runs"]:
        validate_run(run, plan_schema=plan["schema"]); ident = run_identity(run); require(ident not in seen, "duplicate plan run"); seen.add(ident)

def validate_run(run: Mapping[str, Any], *, plan_schema: str) -> None:
    required = {"run_schema", "case_id", "H_rows", "public_h_sha256", "solver_stratum", "algorithm_id", "algorithm_config", "algorithm_config_sha256", "phase", "seed_role", "seed_index", "seed_hex", "budget"}
    if plan_schema == TIER_PLAN_SCHEMA: required |= {"W"}
    require(set(run) >= required, "run missing required fields")
    require(not (set(run) & FORBIDDEN_PAYLOAD_KEYS), "run leaked evaluator-only fields")
    validate_public_case({"case_id": run["case_id"], "H_rows": run["H_rows"], "public_h_sha256": run["public_h_sha256"]})
    genuine_int(run["seed_index"], "seed_index", minimum=0, maximum=7); genuine_int(run["budget"], "budget", minimum=0)
    if plan_schema == FIT_PLAN_SCHEMA:
        require(run["phase"] == FIT_PHASE and run["seed_role"] == FIT_ROLE and run["budget"] == BUDGETS[-1], "bad fit phase/role/budget")
        require("W" not in run, "threshold-fit run must not contain W")
        require(run["solver_stratum"] == SOLVER_DISABLED and run["algorithm_id"] in SOLVER_DISABLED_ALGORITHMS, "fit plan must be solver-disabled only")
    else:
        if run["solver_stratum"] == SOLVER_DISABLED:
            require(run["phase"] == TIER_PHASE and run["seed_role"] == TIER_ROLE and run["budget"] in BUDGETS and run["algorithm_id"] in SOLVER_DISABLED_ALGORITHMS, "bad tier run")
        else:
            require(run["solver_stratum"] == SOLVER_ASSISTED and run["algorithm_id"] == CP_SAT and run["phase"] == REFERENCE_PHASE, "bad reference run")
            require((run.get("reference_source_phase"), run["seed_role"], run["seed_index"]) in CP_SAT_SEED_PAIRS, "bad CP-SAT seed pair")
        if run["W"] is not None: genuine_int(run["W"], "W", minimum=0)
    require(run["algorithm_config_sha256"] == config_digest(run["algorithm_config"]), "config digest mismatch")

def run_identity(run: Mapping[str, Any]) -> tuple[Any, ...]: return (run["case_id"], run["solver_stratum"], run["algorithm_id"], run["phase"], run["seed_role"], run["seed_index"], run["budget"], run["algorithm_config_sha256"], run.get("reference_source_phase"))

def execute_run(run: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    validate_run(run, plan_schema=plan["schema"]); rows, n = v1.parse_h_rows(run["H_rows"]); rank = v1.gf2_rank_bit_rows(rows, n); cfg = copy.deepcopy(run["algorithm_config"]); start = time.perf_counter(); error = None
    public = {"case_id": run["case_id"], "H_rows": run["H_rows"], "public_h_sha256": run["public_h_sha256"]}
    if run.get("W") is not None: public["W"] = run["W"]
    else: public["W"] = n  # adapter-only threshold counter; omitted from fit record and does not affect trajectories.
    try:
        if run["algorithm_id"] == UNIFORM:
            outcome = v1.run_uniform_kernel_sampling(public, cfg, rng_for(run, run["algorithm_config_sha256"]))
        elif run["algorithm_id"] == FIXED:
            outcome = v1.run_fixed_weight_subset_sampling(public, cfg, rng_for(run, run["algorithm_config_sha256"]))
        elif run["algorithm_id"] == LEE:
            outcome = v1.run_lee_brickell_isd(public, cfg, rng_for(run, run["algorithm_config_sha256"]))
        elif run["algorithm_id"] == STERN:
            isd_public = {"case_id": run["case_id"], "H_rows": run["H_rows"], "public_h_sha256": run["public_h_sha256"], "phase": FIT_PHASE if plan["schema"] == FIT_PLAN_SCHEMA else TIER_PHASE, "seed_role": run["seed_role"], "seed_index": run["seed_index"], "budget": min(run["budget"], 32) if len(run["H_rows"][0]) <= 16 else run["budget"], "candidate_protocol_version": corpus_v2.PROTOCOL_ID, "candidate_generator_config_sha256": plan["candidate_generator_config_sha256"], "candidate_manifest_sha256": plan["candidate_manifest_sha256"]}
            if run.get("W") is not None: isd_public["W"] = run["W"]
            rec = isd_v2.run_record(isd_public, {k:v for k,v in cfg.items() if k != "candidate_budget_adapter"})
            return normalize_isd_record(rec, run, plan, time.perf_counter() - start)
        elif run["algorithm_id"] == CP_SAT:
            outcome = v1.run_cp_sat_threshold_reference(public, cfg, int(run["seed_hex"][:16], 16))
        else: fail("unknown algorithm")
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"; outcome = None
    return normalize_v1_outcome(outcome, run, plan, rank=rank, runtime=time.perf_counter() - start, error=error)

def candidate_fields(best_word: int | None, rows: Sequence[int], n: int) -> dict[str, Any]:
    if best_word is None: return {"best_candidate_bits": None, "best_candidate_sha256": None, "best_weight": None, "witness_verified": False}
    wt = v1.verify_nonzero_kernel_word(rows, n, best_word); bits = v1.word_to_bits(best_word, n)
    return {"best_candidate_bits": bits, "best_candidate_sha256": v1.candidate_sha256(bits), "best_weight": wt, "witness_verified": True}

def base_record(run: Mapping[str, Any], plan: Mapping[str, Any], *, rank: int, runtime: float) -> dict[str, Any]:
    record = {"result_schema": RESULT_SCHEMA, "protocol_id": PROTOCOL_ID, "plan_schema": plan["schema"], "plan_sha256": plan["plan_sha256"], "candidate_manifest_sha256": plan["candidate_manifest_sha256"], "candidate_generator_config_sha256": plan["candidate_generator_config_sha256"], "calibration_source_commit": plan["calibration_source_commit"], "module_digests": plan["module_digests"], "dependency_versions": dependency_versions(), "case_id": run["case_id"], "H_rows": run["H_rows"], "public_h_sha256": run["public_h_sha256"], "n": len(run["H_rows"][0]), "rank": rank, "solver_stratum": run["solver_stratum"], "algorithm_id": run["algorithm_id"], "algorithm_config": run["algorithm_config"], "algorithm_config_sha256": run["algorithm_config_sha256"], "phase": run["phase"], "seed_role": run["seed_role"], "seed_index": run["seed_index"], "seed_hex": run["seed_hex"], "budget": run["budget"], "W": run.get("W"), "runtime_s": 0.0, "reference_source_phase": run.get("reference_source_phase"), "record_sha256": ""}
    return record

def normalize_v1_outcome(outcome: Any, run: Mapping[str, Any], plan: Mapping[str, Any], *, rank: int, runtime: float, error: str | None) -> dict[str, Any]:
    rows, n = v1.parse_h_rows(run["H_rows"]); rec = base_record(run, plan, rank=rank, runtime=runtime)
    if outcome is None:
        rec.update({"termination_reason": "error", "completed_budget": False, "resource_limit": True, "error": error, "candidate_evaluations": 0, "objective_evaluations": 0, "exact_verifications": 0, "valid_codewords_seen": 0, "threshold_witnesses_seen": 0, "information_set_attempts": 0, "information_sets_accepted": 0, "singular_information_sets": 0, "solver_calls": 0, "solver_status": None, "solver_status_raw": None, "threshold_infeasibility_certified": False, "diagnostics": {}, **candidate_fields(None, rows, n)})
    else:
        cf = candidate_fields(outcome.best_candidate, rows, n); completed = outcome.termination_reason in {"candidate_budget_exhausted", "information_set_budget_exhausted", "trivial_code_no_nonzero_word"}
        rec.update({"termination_reason": outcome.termination_reason, "completed_budget": completed and run["solver_stratum"] == SOLVER_DISABLED, "resource_limit": outcome.termination_reason in RESOURCE_TERMINATIONS, "error": None, "candidate_evaluations": outcome.candidate_evaluations, "objective_evaluations": outcome.objective_evaluations, "exact_verifications": outcome.exact_verifications, "valid_codewords_seen": outcome.valid_codewords_seen, "threshold_witnesses_seen": 0 if run["phase"] == FIT_PHASE else outcome.threshold_witnesses_seen, "information_set_attempts": outcome.information_set_attempts, "information_sets_accepted": outcome.information_sets_accepted, "singular_information_sets": outcome.singular_information_sets, "solver_calls": outcome.solver_calls, "solver_status": outcome.solver_status, "solver_status_raw": outcome.solver_status_raw, "threshold_infeasibility_certified": bool(outcome.threshold_infeasibility_certified), "diagnostics": outcome.diagnostics, **cf})
    rec["threshold_hit"] = bool(rec["W"] is not None and rec["best_weight"] is not None and rec["best_weight"] <= rec["W"])
    if run["phase"] == FIT_PHASE: rec["W"] = None; rec["threshold_hit"] = False; rec["threshold_witnesses_seen"] = 0
    rec["record_sha256"] = digest({k:v for k,v in rec.items() if k != "record_sha256"}); return rec

def normalize_isd_record(src: Mapping[str, Any], run: Mapping[str, Any], plan: Mapping[str, Any], runtime: float) -> dict[str, Any]:
    rows, n = v1.parse_h_rows(run["H_rows"]); rec = base_record(run, plan, rank=src["rank"], runtime=runtime)
    rec.update({"termination_reason": src["termination_reason"], "completed_budget": src["termination_reason"] == "candidate_budget_exhausted", "resource_limit": src["termination_reason"] == "resource_limit", "error": None, "candidate_evaluations": src["candidate_evaluations"], "objective_evaluations": src["objective_evaluations"], "exact_verifications": src["exact_verifications"], "valid_codewords_seen": src["valid_codewords_seen"], "threshold_witnesses_seen": 0 if run["phase"] == FIT_PHASE else src["threshold_witnesses_seen"], "information_set_attempts": src["information_set_attempts"], "information_sets_accepted": src["information_sets_accepted"], "singular_information_sets": src["singular_information_sets"], "solver_calls": 0, "solver_status": None, "solver_status_raw": None, "threshold_infeasibility_certified": False, "diagnostics": src["diagnostics"], "best_candidate_bits": src["best_candidate_bits"], "best_candidate_sha256": src["best_candidate_sha256"], "best_weight": src["best_weight"], "witness_verified": src["witness_verified"]})
    rec["threshold_hit"] = bool(rec["W"] is not None and rec["best_weight"] is not None and rec["best_weight"] <= rec["W"])
    if run["phase"] == FIT_PHASE: rec["W"] = None; rec["threshold_hit"] = False
    rec["record_sha256"] = digest({k:v for k,v in rec.items() if k != "record_sha256"}); return rec

def validate_result_record(record: Mapping[str, Any], *, plan: Mapping[str, Any] | None = None) -> None:
    required = {"result_schema", "protocol_id", "plan_schema", "plan_sha256", "candidate_manifest_sha256", "candidate_generator_config_sha256", "calibration_source_commit", "module_digests", "dependency_versions", "case_id", "H_rows", "public_h_sha256", "n", "rank", "solver_stratum", "algorithm_id", "algorithm_config", "algorithm_config_sha256", "phase", "seed_role", "seed_index", "seed_hex", "budget", "W", "runtime_s", "termination_reason", "completed_budget", "resource_limit", "error", "candidate_evaluations", "objective_evaluations", "exact_verifications", "valid_codewords_seen", "threshold_witnesses_seen", "information_set_attempts", "information_sets_accepted", "singular_information_sets", "solver_calls", "solver_status", "solver_status_raw", "threshold_infeasibility_certified", "diagnostics", "best_candidate_bits", "best_candidate_sha256", "best_weight", "witness_verified", "threshold_hit", "reference_source_phase", "record_sha256"}
    require(set(record) == required, "result has missing or unknown fields")
    require(record["result_schema"] == RESULT_SCHEMA and record["protocol_id"] == PROTOCOL_ID, "bad result schema/protocol")
    require(record["record_sha256"] == digest({k:v for k,v in record.items() if k != "record_sha256"}), "record digest mismatch")
    validate_public_case({"case_id": record["case_id"], "H_rows": record["H_rows"], "public_h_sha256": record["public_h_sha256"]})
    rows, n = v1.parse_h_rows(record["H_rows"]); require(record["n"] == n and record["rank"] == v1.gf2_rank_bit_rows(rows, n), "dimension/rank mismatch")
    for key in ("candidate_manifest_sha256", "candidate_generator_config_sha256", "plan_sha256", "algorithm_config_sha256"): require(is_sha256(record[key]), f"bad digest {key}")
    require(record["algorithm_config_sha256"] == config_digest(record["algorithm_config"]), "algorithm config digest mismatch")
    for key in ("seed_index", "budget", "candidate_evaluations", "objective_evaluations", "exact_verifications", "valid_codewords_seen", "threshold_witnesses_seen", "information_set_attempts", "information_sets_accepted", "singular_information_sets", "solver_calls"): genuine_int(record[key], key, minimum=0)
    require(isinstance(record["completed_budget"], bool) and isinstance(record["resource_limit"], bool) and isinstance(record["witness_verified"], bool) and isinstance(record["threshold_hit"], bool), "boolean fields must be literal")
    if record["phase"] == FIT_PHASE: require(record["W"] is None and record["threshold_hit"] is False and record["threshold_witnesses_seen"] == 0, "fit result must not use W")
    else:
        if record["W"] is not None: genuine_int(record["W"], "W", minimum=0)
    if record["best_candidate_bits"] is None:
        require(record["best_candidate_sha256"] is None and record["best_weight"] is None and record["witness_verified"] is False, "empty incumbent mismatch")
    else:
        require(record["best_candidate_sha256"] == v1.candidate_sha256(record["best_candidate_bits"]), "candidate hash mismatch")
        wt = v1.verify_nonzero_kernel_word(rows, n, v1.bits_to_word(record["best_candidate_bits"])); require(record["best_weight"] == wt and record["witness_verified"] is True, "incumbent verification mismatch")
    require(record["threshold_hit"] == (record["W"] is not None and record["best_weight"] is not None and record["best_weight"] <= record["W"]), "threshold-hit mismatch")
    if record["solver_stratum"] == SOLVER_DISABLED: require(record["algorithm_id"] in SOLVER_DISABLED_ALGORITHMS and record["solver_calls"] == 0, "solver-disabled separation violated")
    else: require(record["algorithm_id"] == CP_SAT and record["solver_stratum"] == SOLVER_ASSISTED, "solver-assisted separation violated")
    if record["completed_budget"]: require(record["resource_limit"] is False, "completed resource-limit mismatch")
    if plan is not None:
        require(record["plan_sha256"] == plan["plan_sha256"], "record bound to wrong plan")
        for bind in ("candidate_manifest_sha256", "candidate_generator_config_sha256", "calibration_source_commit"):
            require(record[bind] == plan[bind], f"record binding mismatch {bind}")
        exp = {run_identity(run): run for run in plan["runs"]}; ident = result_identity(record); require(ident in exp, "record identity not in plan")

def result_identity(record: Mapping[str, Any]) -> tuple[Any, ...]: return (record["case_id"], record["solver_stratum"], record["algorithm_id"], record["phase"], record["seed_role"], record["seed_index"], record["budget"], record["algorithm_config_sha256"], record.get("reference_source_phase"))

def validate_results(plan: Mapping[str, Any], records: Sequence[Mapping[str, Any]], *, allow_partial: bool = False) -> dict[str, int]:
    validate_plan(plan); expected = {run_identity(run): run for run in plan["runs"]}; seen = {}
    for rec in records:
        validate_result_record(rec, plan=plan); ident = result_identity(rec); require(ident not in seen, "duplicate result identity"); seen[ident] = rec
    if not allow_partial:
        missing = set(expected) - set(seen); require(not missing, f"missing {len(missing)} planned results")
    extra = set(seen) - set(expected); require(not extra, f"extra {len(extra)} results")
    return {"expected": len(expected), "records": len(records), "missing": len(set(expected)-set(seen))}

def nearest_rank(vals: Sequence[int], q: float) -> int:
    require(vals, "empty percentile input"); ordered = sorted(vals); return ordered[max(1, math.ceil(q * len(ordered))) - 1]
def lower_median(vals: Sequence[int]) -> int:
    require(vals, "empty median input"); ordered = sorted(vals); return ordered[(len(ordered)-1)//2]
def completed_incumbent(record: Mapping[str, Any]) -> bool: return record.get("completed_budget") is True and record.get("resource_limit") is False and record.get("best_weight") is not None and record.get("witness_verified") is True

def fit_thresholds(manifest: Mapping[str, Any], plan: Mapping[str, Any], records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    validate_plan(plan, expected_schema=FIT_PLAN_SCHEMA); validate_results(plan, records)
    by_case: dict[str, list[Mapping[str, Any]]] = {}
    for rec in records: by_case.setdefault(rec["case_id"], []).append(rec)
    thresholds = []
    for crec in manifest["records"]:
        cid = crec["case_id"]; meta = evaluator_metadata(crec); rs = by_case.get(cid, [])
        if meta["exact_distance"] is not None:
            W = genuine_int(meta["exact_distance"], "exact distance", minimum=0); source = "exact_control_replay"; artificial = False; decision = "threshold_frozen"
        elif meta["is_planted"] and meta["planted_upper_bound"] is not None:
            W = genuine_int(meta["planted_upper_bound"], "planted upper bound", minimum=1); source = "evaluator_planted_upper_bound"; artificial = True; decision = "threshold_frozen_artificial"
        else:
            vals = [r["best_weight"] for r in rs if completed_incumbent(r)]; algs = {r["algorithm_id"] for r in rs if completed_incumbent(r)}; denom = len(SOLVER_DISABLED_ALGORITHMS)*8
            if len(vals)/denom >= 0.5 and len(algs) >= 2:
                W = nearest_rank(vals, 0.40); source = "solver_disabled_nearest_rank_40pct"; decision = "threshold_frozen"; artificial = False
            else:
                W = None; source = "insufficient_availability"; decision = "calibration_incomplete"; artificial = False
        thresholds.append({"case_id": cid, "public_h_sha256": crec["public_h_sha256"], "n": crec.get("n"), "W": W, "threshold_source": source, "decision": decision, "planted_threshold_artificial": artificial, "certified_lower_bound": meta["certified_lower_bound"], "exact_distance": meta["exact_distance"], "fit_denominator": len(SOLVER_DISABLED_ALGORITHMS)*8, "fit_available": sum(1 for r in rs if completed_incumbent(r)), "fit_contributing_algorithms": sorted({r["algorithm_id"] for r in rs if completed_incumbent(r)})})
    artifact = {"schema": THRESHOLD_SCHEMA, "protocol_id": PROTOCOL_ID, "fit_plan_sha256": plan["plan_sha256"], "candidate_manifest_sha256": manifest_digest(manifest), "thresholds": thresholds}; artifact["thresholds_sha256"] = digest({k:v for k,v in artifact.items() if k != "thresholds_sha256"}); return artifact

def validate_threshold_artifact(artifact: Mapping[str, Any], *, manifest: Mapping[str, Any] | None = None) -> None:
    require(artifact.get("schema") == THRESHOLD_SCHEMA and artifact.get("protocol_id") == PROTOCOL_ID, "bad threshold artifact")
    require(artifact.get("thresholds_sha256") == digest({k:v for k,v in artifact.items() if k != "thresholds_sha256"}), "threshold artifact digest mismatch")
    seen = set()
    for row in artifact["thresholds"]:
        require(set(row) == {"case_id", "public_h_sha256", "n", "W", "threshold_source", "decision", "planted_threshold_artificial", "certified_lower_bound", "exact_distance", "fit_denominator", "fit_available", "fit_contributing_algorithms"}, "bad threshold row schema")
        require(row["case_id"] not in seen, "duplicate threshold row"); seen.add(row["case_id"])
        if row["W"] is not None: genuine_int(row["W"], "W", minimum=0)
    if manifest is not None: require(seen == {r["case_id"] for r in manifest["records"]}, "threshold artifact cases do not match manifest")

def validate_tiers(manifest: Mapping[str, Any], plan: Mapping[str, Any], thresholds: Mapping[str, Any], records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    validate_plan(plan, expected_schema=TIER_PLAN_SCHEMA); validate_threshold_artifact(thresholds, manifest=manifest); validate_results(plan, records)
    tm = threshold_map(thresholds); by_case: dict[str, list[Mapping[str, Any]]] = {}
    for rec in records:
        if rec["solver_stratum"] == SOLVER_DISABLED: by_case.setdefault(rec["case_id"], []).append(rec)
    tiers = []
    for crec in manifest["records"]:
        cid = crec["case_id"]; trow = tm[cid]; W = trow["W"]; rs = by_case.get(cid, []); n = int(trow["n"] or len(crec["H_rows"][0])); lower = int(trow["certified_lower_bound"] or 1)
        if W is None: tiers.append({"case_id": cid, "tier": "calibration_incomplete", "decision": "incomplete", "reason": "missing_threshold"}); continue
        rates = {}; rlf = {}
        for budget in BUDGETS:
            br = [r for r in rs if r["budget"] == budget]; denom = len(SOLVER_DISABLED_ALGORITHMS)*8
            rates[str(budget)] = sum(1 for r in br if r["threshold_hit"])/denom
            rlf[str(budget)] = (denom - len(br) + sum(1 for r in br if r["resource_limit"] or not r["completed_budget"])) / denom
        maxrs = [r for r in rs if r["budget"] == BUDGETS[-1] and completed_incumbent(r)]; weights = [r["best_weight"] for r in maxrs]
        iqr = None if len(weights) < 8 else nearest_rank(weights, .75) - nearest_rank(weights, .25)
        medians = {alg: lower_median([r["best_weight"] for r in maxrs if r["algorithm_id"] == alg]) for alg in SOLVER_DISABLED_ALGORITHMS if len([r for r in maxrs if r["algorithm_id"] == alg]) >= 2}
        agree = any(abs(a-b) <= 2 for i, a in enumerate(medians.values()) for b in list(medians.values())[i+1:])
        best = min(weights) if weights else None; hard_gap_ok = best is not None and best - lower <= max(12, math.ceil(0.20*n))
        hi, lo = rates[str(BUDGETS[-1])], rates[str(BUDGETS[0])]
        if trow["exact_distance"] is not None: tier, decision, reason = "control_exact", "accepted", "exact_control"
        elif iqr is None: tier, decision, reason = "calibration_incomplete", "incomplete", "insufficient_incumbent_iqr"
        elif not agree: tier, decision, reason = "calibration_incomplete", "incomplete", "algorithm_disagreement"
        elif .70 <= hi < .90 and lo < .70: tier, decision, reason = "easy_calibrated", "accepted", "easy_rule"
        elif .35 <= hi < .70 and lo < .50 and iqr >= 1 and rlf[str(BUDGETS[-1])] < .25: tier, decision, reason = "medium_calibrated", "accepted", "medium_rule"
        elif .10 <= hi < .35 and best is not None and rlf[str(BUDGETS[-1])] < .50 and hard_gap_ok: tier, decision, reason = "hard_calibrated", "accepted", "hard_rule"
        else: tier, decision, reason = "calibration_incomplete", "incomplete", "tier_rule_not_satisfied"
        tiers.append({"case_id": cid, "tier": tier, "decision": decision, "reason": reason, "W": W, "hit_rates": rates, "resource_limit_frequencies": rlf, "iqr": iqr, "algorithm_medians": medians, "algorithm_agreement_gap2": agree, "best_solver_disabled_upper_bound": best, "certified_lower_bound": lower, "hard_gap_ok": hard_gap_ok})
    artifact = {"schema": TIER_SCHEMA, "protocol_id": PROTOCOL_ID, "tier_plan_sha256": plan["plan_sha256"], "thresholds_sha256": thresholds["thresholds_sha256"], "tiers": tiers}; artifact["tiers_sha256"] = digest({k:v for k,v in artifact.items() if k != "tiers_sha256"}); return artifact

def assigned_runs(plan: Mapping[str, Any], shard_index: int, shard_count: int) -> list[Mapping[str, Any]]:
    genuine_int(shard_index, "shard_index", minimum=0); genuine_int(shard_count, "shard_count", minimum=1); require(shard_index < shard_count, "shard_index must be < shard_count")
    return [run for idx, run in enumerate(plan["runs"]) if idx % shard_count == shard_index]
def merge_shards(plan: Mapping[str, Any], paths: Sequence[Path]) -> list[dict[str, Any]]:
    all_records: list[dict[str, Any]] = []
    for path in paths: all_records.extend(read_jsonl(path))
    validate_results(plan, all_records); return all_records

def make_fixture_manifest() -> dict[str, Any]:
    records = [
        {"case_id": "calv2-fixture-even4", "H_rows": ["1111"], "public_h_sha256": isd_v2.public_h_sha256(["1111"]), "n": 4, "family_id": "exact-control", "validation": {"known_distance": {"distance": 2}, "small_circuit": {"status": "PASS", "cap": 1}}},
        {"case_id": "calv2-fixture-planted5", "H_rows": ["10101", "01110"], "public_h_sha256": isd_v2.public_h_sha256(["10101", "01110"]), "n": 5, "family_id": "planted-fixture", "validation": {"small_circuit": {"status": "PASS", "cap": 1}}, "evaluator_only_provenance": {"planted_witness_support": [0, 1, 2]}},
    ]
    man = {"manifest_kind": "calibration_fixture_manifest", "candidate_manifest_digest": "f"*64, "configuration_digest": corpus_v2.config_digest(), "records": records}
    return man

def smoke(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True); manifest = make_fixture_manifest(); write_json(output_dir/"fixture_manifest.json", manifest)
    fit_plan = build_threshold_fit_plan(manifest); write_json(output_dir/"threshold_fit_plan.json", fit_plan)
    fit_runs = assigned_runs(fit_plan, 0, 1); fit_records = [execute_run(run, fit_plan) for run in fit_runs]; write_jsonl(output_dir/"threshold_fit_results.jsonl", fit_records)
    thresholds = fit_thresholds(manifest, fit_plan, fit_records); write_json(output_dir/"thresholds.json", thresholds)
    tier_plan = build_tier_reference_plan(manifest, thresholds); write_json(output_dir/"tier_reference_plan.json", tier_plan)
    tier_records = [execute_run(run, tier_plan) for run in tier_plan["runs"] if not (run["algorithm_id"] == CP_SAT and dependency_versions()["ortools"] is None)]
    write_jsonl(output_dir/"tier_reference_results.jsonl", tier_records)
    # For smoke without OR-Tools, validate the partial operational shard and skip final tier artifact.
    if len(tier_records) == len(tier_plan["runs"]): tiers = validate_tiers(manifest, tier_plan, thresholds, tier_records); write_json(output_dir/"tiers.json", tiers)
    return {"fit_records": len(fit_records), "tier_records": len(tier_records), "tier_plan_runs": len(tier_plan["runs"])}

def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(); sub = p.add_subparsers(dest="cmd", required=True)
    a=sub.add_parser("threshold-fit-plan"); a.add_argument("manifest"); a.add_argument("--output", required=True); a.add_argument("--allow-fixture", action="store_true")
    a=sub.add_parser("fit-thresholds"); a.add_argument("manifest"); a.add_argument("plan"); a.add_argument("results"); a.add_argument("--output", required=True); a.add_argument("--allow-fixture", action="store_true")
    a=sub.add_parser("tier-reference-plan"); a.add_argument("manifest"); a.add_argument("thresholds"); a.add_argument("--output", required=True); a.add_argument("--allow-fixture", action="store_true")
    a=sub.add_parser("run-shard"); a.add_argument("manifest"); a.add_argument("plan"); a.add_argument("--thresholds"); a.add_argument("--shard-index", type=int, required=True); a.add_argument("--shard-count", type=int, required=True); a.add_argument("--output", required=True); a.add_argument("--allow-fixture", action="store_true")
    a=sub.add_parser("validate-results"); a.add_argument("plan"); a.add_argument("results", nargs="+"); a.add_argument("--allow-partial", action="store_true"); a.add_argument("--summary", action="store_true")
    a=sub.add_parser("validate-tiers"); a.add_argument("manifest"); a.add_argument("plan"); a.add_argument("thresholds"); a.add_argument("results"); a.add_argument("--output", required=True); a.add_argument("--allow-fixture", action="store_true")
    a=sub.add_parser("merge-shards"); a.add_argument("plan"); a.add_argument("shards", nargs="+"); a.add_argument("--output", required=True)
    a=sub.add_parser("summary"); a.add_argument("tiers")
    a=sub.add_parser("smoke"); a.add_argument("--output-dir", required=True)
    args = p.parse_args(argv)
    if args.cmd == "threshold-fit-plan": write_json(Path(args.output), build_threshold_fit_plan(load_candidate_manifest(Path(args.manifest), allow_fixture=args.allow_fixture))); return 0
    if args.cmd == "fit-thresholds": write_json(Path(args.output), fit_thresholds(load_candidate_manifest(Path(args.manifest), allow_fixture=args.allow_fixture), read_json(Path(args.plan)), read_jsonl(Path(args.results)))); return 0
    if args.cmd == "tier-reference-plan": write_json(Path(args.output), build_tier_reference_plan(load_candidate_manifest(Path(args.manifest), allow_fixture=args.allow_fixture), read_json(Path(args.thresholds)))); return 0
    if args.cmd == "run-shard":
        load_candidate_manifest(Path(args.manifest), allow_fixture=args.allow_fixture); plan = read_json(Path(args.plan)); validate_plan(plan);
        if plan["schema"] == TIER_PLAN_SCHEMA: require(args.thresholds is not None, "tier/reference shards require threshold artifact"); validate_threshold_artifact(read_json(Path(args.thresholds)))
        runs = assigned_runs(plan, args.shard_index, args.shard_count); require(runs, "assigned shard is empty"); write_jsonl(Path(args.output), [execute_run(run, plan) for run in runs]); return 0
    if args.cmd == "validate-results":
        plan = read_json(Path(args.plan)); records=[]
        for path in args.results: records.extend(read_jsonl(Path(path)))
        summary = validate_results(plan, records, allow_partial=args.allow_partial)
        if args.summary: print(json.dumps(summary, sort_keys=True))
        return 0
    if args.cmd == "validate-tiers": write_json(Path(args.output), validate_tiers(load_candidate_manifest(Path(args.manifest), allow_fixture=args.allow_fixture), read_json(Path(args.plan)), read_json(Path(args.thresholds)), read_jsonl(Path(args.results)))); return 0
    if args.cmd == "merge-shards": write_jsonl(Path(args.output), merge_shards(read_json(Path(args.plan)), [Path(p) for p in args.shards])); return 0
    if args.cmd == "summary":
        tiers = read_json(Path(args.tiers))["tiers"]; print(json.dumps({tier: sum(1 for row in tiers if row["tier"] == tier) for tier in sorted({row["tier"] for row in tiers})}, sort_keys=True)); return 0
    if args.cmd == "smoke": print(json.dumps(smoke(Path(args.output_dir)), sort_keys=True)); return 0
    return 2

if __name__ == "__main__":
    try: raise SystemExit(main())
    except (CalibrationV2Error, v1.BaselineValidationError, isd_v2.ISDValidationError, corpus_v2.V2Error, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr); raise SystemExit(1)
