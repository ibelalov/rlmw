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
FIXTURE_BUDGETS = (8, 16, 24, 32)
PRODUCTION_PROFILE_ID = "calibration_v2"
FIXTURE_PROFILE_ID = "calibration_fixture_smoke_v2"
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
    prov = rec.get("evaluator_only_provenance", {}) if isinstance(rec.get("evaluator_only_provenance"), dict) else {}
    # Real generated controls carry their certificate only in protected,
    # evaluator-only provenance.  Replay it from public H; never trust a
    # synthetic validation field or an asserted distance.
    if rec.get("parameter_stratum_id") in corpus_v2.CONTROL_STRATA:
        cert = prov.get("certificate")
        require(isinstance(cert, dict), "control lacks evaluator certificate")
        H = corpus_v2.BinaryMatrix.from_row_strings(rec["H_rows"])
        replayed = corpus_v2.replay_control_certificate(H, rec["parameter_stratum_id"])
        require(cert == replayed, "control certificate replay mismatch")
        distance = cert.get("exact_distance")
        genuine_int(distance, "control exact distance", minimum=1)
        require(cert.get("status") in ("CERTIFIED_EXACT_DISTANCE", "CERTIFIED_THEOREM_DISTANCE"), "control certificate lacks exact status")
        if cert.get("status") == "CERTIFIED_EXACT_DISTANCE":
            require(cert.get("finite_lower_bound") == distance and cert.get("finite_upper_bound") == distance, "inconsistent enumerated control certificate bounds")
        meta["exact_distance"] = distance; meta["certified_lower_bound"] = distance
    elif isinstance(val.get("known_distance"), dict):
        # Fixtures retain their deliberately synthetic contract.
        meta["exact_distance"] = val["known_distance"].get("distance")
    supp = prov.get("planted_witness_support")
    if isinstance(supp, list): meta["planted_upper_bound"] = len(supp)
    sc = val.get("small_circuit", {}) if isinstance(val.get("small_circuit"), dict) else {}
    if isinstance(sc.get("cap"), int) and sc.get("status") == "PASS" and meta["exact_distance"] is None: meta["certified_lower_bound"] = sc["cap"] + 1
    return meta

def algorithm_config(algorithm_id: str, *, budget: int, n: int, rank: int) -> dict[str, Any]:
    genuine_int(budget, "budget", minimum=0); k = n - rank
    effective_budget = budget
    if algorithm_id == UNIFORM:
        cfg = {"candidate_budget": budget, "sampling_with_replacement": True, "exhaust_candidate_budget": True, "prng_version": v1.PRNG_VERSION}
        v1.validate_algorithm_config(UNIFORM, cfg, n=n, k=k); return cfg
    if algorithm_id == FIXED:
        cfg = {"candidate_budget": budget, "weights": list(range(1, min(12, n) + 1)), "weight_schedule": "round_robin", "sampling_with_replacement_across_iterations": True, "exhaust_candidate_budget": True, "prng_version": v1.PRNG_VERSION}
        v1.validate_algorithm_config(FIXED, cfg, n=n, k=k); return cfg
    if algorithm_id == LEE:
        cfg = {"candidate_budget": budget, "information_set_budget": 4096, "max_information_weight": 1 if k <= 1 else 2, "pattern_mode": "enumerate_nonzero_weight_at_most_p_lexicographic", "information_set_sampling": "uniform_r_subset_with_replacement", "exhaust_candidate_budget": True, "prng_version": v1.PRNG_VERSION}
        v1.validate_algorithm_config(LEE, cfg, n=n, k=k); return cfg
    if algorithm_id == STERN:
        return isd_v2.algorithm_config("calibration", {"max_collision_pairs": budget}, rank=rank)
    if algorithm_id == CP_SAT:
        return copy.deepcopy(CP_SAT_PROFILES[0])
    fail(f"unknown algorithm {algorithm_id}")

def config_digest(config: Mapping[str, Any]) -> str: return digest(config)
def seed_hex(role: str, index: int) -> str: return corpus_v2.calibration_seed(role, index)
def rng_for(run: Mapping[str, Any], cfg_digest: str) -> v1.Sha256CounterRng:
    return v1.Sha256CounterRng(hashlib.sha256(canonical_bytes([PROTOCOL_ID, run["case_id"], run["public_h_sha256"], run["algorithm_id"], run["phase"], run["seed_role"], run["seed_index"], run["budget"], cfg_digest])).digest())

def manifest_profile(manifest: Mapping[str, Any]) -> str:
    return FIXTURE_PROFILE_ID if manifest.get("manifest_kind") == "calibration_fixture_manifest" else PRODUCTION_PROFILE_ID

def common_plan_fields(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {"protocol_id": PROTOCOL_ID, "manifest_kind": manifest.get("manifest_kind", "candidate_pool_manifest"), "candidate_manifest_sha256": manifest_digest(manifest), "candidate_generator_config_sha256": manifest.get("configuration_digest", corpus_v2.config_digest()), "calibration_source_commit": current_commit(), "module_digests": module_digests(), "dependency_versions": dependency_versions()}

def plan_budgets(profile_id: str) -> tuple[int, int, int, int]:
    require(profile_id in (PRODUCTION_PROFILE_ID, FIXTURE_PROFILE_ID), "unknown calibration profile")
    return FIXTURE_BUDGETS if profile_id == FIXTURE_PROFILE_ID else BUDGETS

def build_threshold_fit_plan(manifest: Mapping[str, Any], *, profile_id: str = PRODUCTION_PROFILE_ID) -> dict[str, Any]:
    require(profile_id == manifest_profile(manifest), "manifest/profile binding mismatch")
    budgets = plan_budgets(profile_id)
    common = common_plan_fields(manifest); runs = []
    for rec in manifest["records"]:
        case = case_public(rec); validate_public_case(case); rows, n = v1.parse_h_rows(case["H_rows"]); rank = v1.gf2_rank_bit_rows(rows, n)
        for alg in SOLVER_DISABLED_ALGORITHMS:
            for budget in budgets:
                cfg = algorithm_config(alg, budget=budget, n=n, rank=rank); cd = config_digest(cfg)
                for seed_index in range(8):
                    runs.append({"run_schema": "fit-run-v2", **case, "solver_stratum": SOLVER_DISABLED, "algorithm_id": alg, "algorithm_config": cfg, "algorithm_config_sha256": cd, "phase": FIT_PHASE, "seed_role": FIT_ROLE, "seed_index": seed_index, "seed_hex": seed_hex(FIT_ROLE, seed_index), "budget": budget})
    plan = {"schema": FIT_PLAN_SCHEMA, **common, "profile_id": profile_id, "budgets": list(budgets), "runs": runs}; plan["plan_sha256"] = digest({k:v for k,v in plan.items() if k != "plan_sha256"}); return plan

def threshold_map(thresholds: Mapping[str, Any]) -> dict[str, Any]: return {t["case_id"]: t for t in thresholds["thresholds"]}
def build_tier_reference_plan(manifest: Mapping[str, Any], thresholds: Mapping[str, Any], *, profile_id: str = PRODUCTION_PROFILE_ID, fit_plan: Mapping[str, Any] | None = None, fit_records: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    require(profile_id == manifest_profile(manifest), "manifest/profile binding mismatch")
    require(fit_plan is not None and fit_records is not None, "tier planning requires complete threshold replay evidence")
    budgets = plan_budgets(profile_id)
    validate_threshold_artifact(thresholds, manifest=manifest, plan=fit_plan, records=fit_records); require(thresholds["candidate_manifest_sha256"] == manifest_digest(manifest), "threshold/manifest digest mismatch"); tm = threshold_map(thresholds); common = common_plan_fields(manifest); runs = []
    for rec in manifest["records"]:
        case = case_public(rec); rows, n = v1.parse_h_rows(case["H_rows"]); rank = v1.gf2_rank_bit_rows(rows, n); W = tm[case["case_id"]]["W"]
        # A missing threshold has no tier-validation objective, hence no
        # solver-facing run.  Its complete artifact row is emitted by replay.
        if W is not None:
            for alg in SOLVER_DISABLED_ALGORITHMS:
                for budget in budgets:
                    cfg = algorithm_config(alg, budget=budget, n=n, rank=rank); cd = config_digest(cfg)
                    for seed_index in range(8): runs.append({"run_schema": "tier-run-v2", **case, "solver_stratum": SOLVER_DISABLED, "algorithm_id": alg, "algorithm_config": cfg, "algorithm_config_sha256": cd, "phase": TIER_PHASE, "seed_role": TIER_ROLE, "seed_index": seed_index, "seed_hex": seed_hex(TIER_ROLE, seed_index), "budget": budget, "W": W})
            for profile in CP_SAT_PROFILES:
                cfg = copy.deepcopy(profile); cd = config_digest(cfg)
                for phase, role, seed_index in CP_SAT_SEED_PAIRS: runs.append({"run_schema": "reference-run-v2", **case, "solver_stratum": SOLVER_ASSISTED, "algorithm_id": CP_SAT, "algorithm_config": cfg, "algorithm_config_sha256": cd, "phase": REFERENCE_PHASE, "seed_role": role, "seed_index": seed_index, "seed_hex": seed_hex(role, seed_index), "budget": 0, "W": W, "reference_source_phase": phase})
    plan = {"schema": TIER_PLAN_SCHEMA, **common, "profile_id": profile_id, "budgets": list(budgets), "thresholds_sha256": thresholds["thresholds_sha256"], "runs": runs}; plan["plan_sha256"] = digest({k:v for k,v in plan.items() if k != "plan_sha256"}); return plan

def validate_plan(plan: Mapping[str, Any], *, expected_schema: str | None = None) -> None:
    require(plan.get("schema") in (FIT_PLAN_SCHEMA, TIER_PLAN_SCHEMA), "unknown plan schema")
    if expected_schema: require(plan.get("schema") == expected_schema, "wrong plan phase")
    common_keys = {"schema", "protocol_id", "manifest_kind", "candidate_manifest_sha256", "candidate_generator_config_sha256", "calibration_source_commit", "module_digests", "dependency_versions", "profile_id", "budgets", "runs", "plan_sha256"}
    expected_keys = common_keys if plan["schema"] == FIT_PLAN_SCHEMA else common_keys | {"thresholds_sha256"}
    require(set(plan) == expected_keys, "plan has missing or unknown fields")
    require(plan.get("protocol_id") == PROTOCOL_ID, "wrong protocol")
    for key in ("manifest_kind", "candidate_manifest_sha256", "candidate_generator_config_sha256", "calibration_source_commit", "module_digests", "dependency_versions", "profile_id", "budgets", "runs", "plan_sha256"):
        require(key in plan, f"missing plan field {key}")
    require(is_sha256(plan["candidate_manifest_sha256"]) and is_sha256(plan["candidate_generator_config_sha256"]), "bad plan digests")
    require(isinstance(plan["calibration_source_commit"], str) and len(plan["calibration_source_commit"]) == 40 and all(ch in "0123456789abcdef" for ch in plan["calibration_source_commit"]), "bad source commit")
    require(isinstance(plan["module_digests"], dict) and set(plan["module_digests"]) == {"calibration_module_sha256", "candidate_config_digest", "candidate_module_sha256", "v1_baselines_module_sha256", "isd_v2_module_sha256"} and all(is_sha256(value) for value in plan["module_digests"].values()), "bad module provenance")
    require(isinstance(plan["dependency_versions"], dict) and set(plan["dependency_versions"]) == {"python", "platform", "ortools", "numpy", "scipy"} and all(value is None or isinstance(value, str) for value in plan["dependency_versions"].values()), "bad dependency provenance")
    if plan["schema"] == TIER_PLAN_SCHEMA: require(is_sha256(plan["thresholds_sha256"]), "bad tier-plan threshold digest")
    require(plan["profile_id"] in (PRODUCTION_PROFILE_ID, FIXTURE_PROFILE_ID) and tuple(plan["budgets"]) == plan_budgets(plan["profile_id"]), "plan profile/budgets mismatch")
    require(plan["manifest_kind"] == ("calibration_fixture_manifest" if plan["profile_id"] == FIXTURE_PROFILE_ID else "candidate_pool_manifest"), "plan manifest-kind/profile mismatch")
    require(plan["plan_sha256"] == digest({k:v for k,v in plan.items() if k != "plan_sha256"}), "plan digest mismatch")
    seen = set()
    for run in plan["runs"]:
        validate_run(run, plan_schema=plan["schema"], profile_id=plan["profile_id"], budgets=tuple(plan["budgets"])); ident = run_identity(run); require(ident not in seen, "duplicate plan run"); seen.add(ident)

def validate_plan_against_manifest(plan: Mapping[str, Any], manifest: Mapping[str, Any]) -> None:
    validate_plan(plan)
    require(plan["profile_id"] == manifest_profile(manifest) and plan["manifest_kind"] == manifest.get("manifest_kind", "candidate_pool_manifest"), "plan/manifest profile mismatch")
    require(plan["candidate_manifest_sha256"] == manifest_digest(manifest), "plan/manifest digest mismatch")
    require(plan["candidate_generator_config_sha256"] == manifest.get("configuration_digest", corpus_v2.config_digest()), "plan/manifest config mismatch")
    by_case = {rec["case_id"]: case_public(rec) for rec in manifest["records"]}
    for run in plan["runs"]:
        require(run["case_id"] in by_case, "planned case missing from manifest")
        expected = by_case[run["case_id"]]
        require(run["H_rows"] == expected["H_rows"] and run["public_h_sha256"] == expected["public_h_sha256"], "planned H does not match manifest")


def validate_run(run: Mapping[str, Any], *, plan_schema: str, profile_id: str = PRODUCTION_PROFILE_ID, budgets: Sequence[int] = BUDGETS) -> None:
    required = {"run_schema", "case_id", "H_rows", "public_h_sha256", "solver_stratum", "algorithm_id", "algorithm_config", "algorithm_config_sha256", "phase", "seed_role", "seed_index", "seed_hex", "budget"}
    if plan_schema == TIER_PLAN_SCHEMA: required |= {"W"}
    allowed = set(required)
    if plan_schema == TIER_PLAN_SCHEMA and run.get("solver_stratum") == SOLVER_ASSISTED:
        allowed.add("reference_source_phase")
    require(set(run) == allowed, "run has missing or unknown fields")
    require(not (set(run) & FORBIDDEN_PAYLOAD_KEYS), "run leaked evaluator-only fields")
    validate_public_case({"case_id": run["case_id"], "H_rows": run["H_rows"], "public_h_sha256": run["public_h_sha256"]})
    genuine_int(run["seed_index"], "seed_index", minimum=0, maximum=7); genuine_int(run["budget"], "budget", minimum=0)
    if plan_schema == FIT_PLAN_SCHEMA:
        require(run["phase"] == FIT_PHASE and run["seed_role"] == FIT_ROLE and run["budget"] in budgets, "bad fit phase/role/budget")
        require("W" not in run, "threshold-fit run must not contain W")
        require(run["solver_stratum"] == SOLVER_DISABLED and run["algorithm_id"] in SOLVER_DISABLED_ALGORITHMS, "fit plan must be solver-disabled only")
    else:
        if run["solver_stratum"] == SOLVER_DISABLED:
            require(run["phase"] == TIER_PHASE and run["seed_role"] == TIER_ROLE and run["budget"] in budgets and run["algorithm_id"] in SOLVER_DISABLED_ALGORITHMS, "bad tier run")
        else:
            require(run["solver_stratum"] == SOLVER_ASSISTED and run["algorithm_id"] == CP_SAT and run["phase"] == REFERENCE_PHASE, "bad reference run")
            require((run.get("reference_source_phase"), run["seed_role"], run["seed_index"]) in CP_SAT_SEED_PAIRS, "bad CP-SAT seed pair")
        if run["W"] is not None: genuine_int(run["W"], "W", minimum=0)
    require(run["algorithm_config_sha256"] == config_digest(run["algorithm_config"]), "config digest mismatch")
    rows, n = v1.parse_h_rows(run["H_rows"]); rank = v1.gf2_rank_bit_rows(rows, n)
    if run["algorithm_id"] in SOLVER_DISABLED_ALGORITHMS:
        expected_cfg = algorithm_config(run["algorithm_id"], budget=run["budget"], n=n, rank=rank)
    elif run["algorithm_id"] == CP_SAT:
        require(any(canonical_bytes(run["algorithm_config"]) == canonical_bytes(profile) for profile in CP_SAT_PROFILES), "CP-SAT config is not frozen")
        expected_cfg = run["algorithm_config"]
    else:
        expected_cfg = run["algorithm_config"]
    require(canonical_bytes(run["algorithm_config"]) == canonical_bytes(expected_cfg), "algorithm config is not frozen for run")
    require(run["seed_hex"] == seed_hex(run["seed_role"], run["seed_index"]), "seed hex mismatch")

def run_identity(run: Mapping[str, Any]) -> tuple[Any, ...]: return (run["case_id"], run["solver_stratum"], run["algorithm_id"], run["phase"], run["seed_role"], run["seed_index"], run["budget"], run["algorithm_config_sha256"], run.get("reference_source_phase"))

def run_uniform_v2(public_input: Mapping[str, Any], config: Mapping[str, Any], rng: v1.Sha256CounterRng) -> v1.BaselineOutcome:
    h_rows, n = v1.parse_h_rows(public_input["H_rows"]); basis, _, _ = v1.deterministic_kernel_basis(h_rows, n)
    v1.validate_algorithm_config(UNIFORM, dict(config), n=n, k=len(basis)); budget = config["candidate_budget"]
    outcome = v1.BaselineOutcome(solver_status=None, solver_status_raw=None)
    if not basis:
        outcome.termination_reason = "trivial_code_no_nonzero_word"; outcome.diagnostics = {"kernel_dimension": 0, "zero_coefficient_draws": 0, "duplicate_candidates": 0}; return outcome
    seen: set[int] = set(); zero_draws = 0; W = public_input.get("W")
    while outcome.candidate_evaluations < budget:
        coefficients = rng.randbits(len(basis))
        if coefficients == 0: zero_draws += 1; continue
        candidate = v1.kernel_word(basis, coefficients); weight = v1.verify_nonzero_kernel_word(h_rows, n, candidate)
        outcome.candidate_evaluations += 1; outcome.objective_evaluations += 1; outcome.exact_verifications += 1; outcome.valid_codewords_seen += 1; outcome.iterations += 1
        if W is not None and weight <= W: outcome.threshold_witnesses_seen += 1
        v1._update_incumbent(outcome, candidate); seen.add(candidate)
    outcome.termination_reason = "candidate_budget_exhausted"; outcome.diagnostics = {"kernel_dimension": len(basis), "zero_coefficient_draws": zero_draws, "duplicate_candidates": outcome.candidate_evaluations-len(seen), "sampling_scope": "v2_uniform_nonzero_kernel_coefficients_with_replacement"}; return outcome

def run_fixed_v2(public_input: Mapping[str, Any], config: Mapping[str, Any], rng: v1.Sha256CounterRng) -> v1.BaselineOutcome:
    h_rows, n = v1.parse_h_rows(public_input["H_rows"]); v1.validate_algorithm_config(FIXED, dict(config), n=n); columns = v1._column_syndromes(h_rows, n)
    outcome = v1.BaselineOutcome(solver_status=None, solver_status_raw=None); seen: set[int] = set(); W = public_input.get("W")
    for evaluation in range(config["candidate_budget"]):
        requested_weight = config["weights"][evaluation % len(config["weights"])]
        support = rng.sample_subset(n, requested_weight); candidate = sum(1 << coordinate for coordinate in support); syndrome = 0
        for coordinate in support: syndrome ^= columns[coordinate]
        outcome.candidate_evaluations += 1; outcome.objective_evaluations += 1; outcome.exact_verifications += 1; outcome.iterations += 1
        if syndrome == 0:
            weight = v1.verify_nonzero_kernel_word(h_rows, n, candidate); outcome.valid_codewords_seen += 1
            if W is not None and weight <= W: outcome.threshold_witnesses_seen += 1
            v1._update_incumbent(outcome, candidate)
        seen.add(candidate)
    outcome.termination_reason = "candidate_budget_exhausted"; outcome.diagnostics = {"duplicate_subsets": outcome.candidate_evaluations-len(seen), "sampling_scope": "v2_coordinate_subsets_with_replacement"}; return outcome

def run_lee_v2(public_input: Mapping[str, Any], config: Mapping[str, Any], rng: v1.Sha256CounterRng) -> v1.BaselineOutcome:
    original_rows, n = v1.parse_h_rows(public_input["H_rows"]); independent_rows, _ = v1.rref_bit_rows(original_rows, n); rank = len(independent_rows); k = n-rank
    v1.validate_algorithm_config(LEE, dict(config), n=n, k=k); outcome = v1.BaselineOutcome(solver_status=None, solver_status_raw=None); W = public_input.get("W")
    if k == 0: outcome.termination_reason = "trivial_code_no_nonzero_word"; outcome.diagnostics = {"rank": rank, "kernel_dimension": 0}; return outcome
    seen: set[int] = set()
    for _attempt in range(config["information_set_budget"]):
        if outcome.candidate_evaluations >= config["candidate_budget"]: break
        outcome.information_set_attempts += 1; outcome.iterations += 1; parity_coordinates = rng.sample_subset(n, rank)
        systematic = v1._systematic_codeword_basis(independent_rows, original_rows, n, parity_coordinates)
        if systematic is None: outcome.singular_information_sets += 1; continue
        basis, _info = systematic; outcome.information_sets_accepted += 1; stop = False
        for information_weight in range(1, config["max_information_weight"]+1):
            import itertools
            for local_support in itertools.combinations(range(k), information_weight):
                if outcome.candidate_evaluations >= config["candidate_budget"]: stop = True; break
                candidate = 0
                for basis_index in local_support: candidate ^= basis[basis_index]
                weight = v1.verify_nonzero_kernel_word(original_rows, n, candidate)
                outcome.candidate_evaluations += 1; outcome.objective_evaluations += 1; outcome.exact_verifications += 1; outcome.valid_codewords_seen += 1
                if W is not None and weight <= W: outcome.threshold_witnesses_seen += 1
                v1._update_incumbent(outcome, candidate); seen.add(candidate)
            if stop: break
        if stop: break
    outcome.termination_reason = "candidate_budget_exhausted" if outcome.candidate_evaluations >= config["candidate_budget"] else "information_set_budget_exhausted"
    outcome.diagnostics = {"rank": rank, "kernel_dimension": k, "duplicate_candidates": outcome.candidate_evaluations-len(seen), "pattern_scope": "v2_lee_weight_at_most_p"}; return outcome


def execute_run(run: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    validate_run(run, plan_schema=plan["schema"], profile_id=plan.get("profile_id", PRODUCTION_PROFILE_ID), budgets=tuple(plan.get("budgets", BUDGETS))); rows, n = v1.parse_h_rows(run["H_rows"]); rank = v1.gf2_rank_bit_rows(rows, n); cfg = copy.deepcopy(run["algorithm_config"]); start = time.perf_counter(); error = None
    public = {"case_id": run["case_id"], "H_rows": run["H_rows"], "public_h_sha256": run["public_h_sha256"]}
    public["W"] = run.get("W")
    if run["algorithm_id"] == UNIFORM:
        outcome = run_uniform_v2(public, cfg, rng_for(run, run["algorithm_config_sha256"]))
    elif run["algorithm_id"] == FIXED:
        outcome = run_fixed_v2(public, cfg, rng_for(run, run["algorithm_config_sha256"]))
    elif run["algorithm_id"] == LEE:
        outcome = run_lee_v2(public, cfg, rng_for(run, run["algorithm_config_sha256"]))
    elif run["algorithm_id"] == STERN:
        isd_public = {"case_id": run["case_id"], "H_rows": run["H_rows"], "public_h_sha256": run["public_h_sha256"], "phase": FIT_PHASE if plan["schema"] == FIT_PLAN_SCHEMA else TIER_PHASE, "seed_role": run["seed_role"], "seed_index": run["seed_index"], "budget": run["budget"], "candidate_protocol_version": corpus_v2.PROTOCOL_ID, "candidate_generator_config_sha256": plan["candidate_generator_config_sha256"], "candidate_manifest_sha256": plan["candidate_manifest_sha256"]}
        if run.get("W") is not None: isd_public["W"] = run["W"]
        rec = isd_v2.run_record(isd_public, cfg)
        return normalize_isd_record(rec, run, plan, time.perf_counter() - start)
    elif run["algorithm_id"] == CP_SAT:
        try:
            outcome = v1.run_cp_sat_threshold_reference(public, {k:v for k,v in cfg.items() if k != "profile_id"}, int(run["seed_hex"][:16], 16))
        except v1.OptionalDependencyUnavailable as exc:
            return normalize_dependency_unavailable(run, plan, rank=rank, runtime=time.perf_counter() - start, error=str(exc))
    else: fail("unknown algorithm")
    return normalize_v1_outcome(outcome, run, plan, rank=rank, runtime=time.perf_counter() - start, error=error)

def candidate_fields(best_word: int | None, rows: Sequence[int], n: int) -> dict[str, Any]:
    if best_word is None: return {"best_candidate_bits": None, "best_candidate_sha256": None, "best_weight": None, "witness_verified": False}
    wt = v1.verify_nonzero_kernel_word(rows, n, best_word); bits = v1.word_to_bits(best_word, n)
    return {"best_candidate_bits": bits, "best_candidate_sha256": v1.candidate_sha256(bits), "best_weight": wt, "witness_verified": True}

def base_record(run: Mapping[str, Any], plan: Mapping[str, Any], *, rank: int, runtime: float) -> dict[str, Any]:
    record = {"result_schema": RESULT_SCHEMA, "protocol_id": PROTOCOL_ID, "plan_schema": plan["schema"], "profile_id": plan["profile_id"], "manifest_kind": plan["manifest_kind"], "plan_sha256": plan["plan_sha256"], "candidate_manifest_sha256": plan["candidate_manifest_sha256"], "candidate_generator_config_sha256": plan["candidate_generator_config_sha256"], "calibration_source_commit": plan["calibration_source_commit"], "module_digests": plan["module_digests"], "dependency_versions": dependency_versions(), "case_id": run["case_id"], "H_rows": run["H_rows"], "public_h_sha256": run["public_h_sha256"], "n": len(run["H_rows"][0]), "rank": rank, "solver_stratum": run["solver_stratum"], "algorithm_id": run["algorithm_id"], "algorithm_config": run["algorithm_config"], "algorithm_config_sha256": run["algorithm_config_sha256"], "phase": run["phase"], "seed_role": run["seed_role"], "seed_index": run["seed_index"], "seed_hex": run["seed_hex"], "budget": run["budget"], "W": run.get("W"), "runtime_s": runtime, "reference_source_phase": run.get("reference_source_phase"), "record_sha256": ""}
    return record

def normalize_dependency_unavailable(run: Mapping[str, Any], plan: Mapping[str, Any], *, rank: int, runtime: float, error: str) -> dict[str, Any]:
    rows, n = v1.parse_h_rows(run["H_rows"]); rec = base_record(run, plan, rank=rank, runtime=runtime)
    rec.update({"termination_reason": "dependency_unavailable", "completed_budget": False, "resource_limit": True, "error": error, "candidate_evaluations": 0, "objective_evaluations": 0, "exact_verifications": 0, "valid_codewords_seen": 0, "threshold_witnesses_seen": 0, "information_set_attempts": 0, "information_sets_accepted": 0, "singular_information_sets": 0, "solver_calls": 0, "solver_status": "DEPENDENCY_UNAVAILABLE", "solver_status_raw": None, "threshold_infeasibility_certified": False, "diagnostics": {}, **candidate_fields(None, rows, n)})
    rec["threshold_hit"] = False; rec["record_sha256"] = digest({k:v for k,v in rec.items() if k != "record_sha256"}); return rec


def normalize_v1_outcome(outcome: Any, run: Mapping[str, Any], plan: Mapping[str, Any], *, rank: int, runtime: float, error: str | None) -> dict[str, Any]:
    rows, n = v1.parse_h_rows(run["H_rows"]); rec = base_record(run, plan, rank=rank, runtime=runtime)
    if outcome is None:
        rec.update({"termination_reason": "error", "completed_budget": False, "resource_limit": True, "error": error, "candidate_evaluations": 0, "objective_evaluations": 0, "exact_verifications": 0, "valid_codewords_seen": 0, "threshold_witnesses_seen": 0, "information_set_attempts": 0, "information_sets_accepted": 0, "singular_information_sets": 0, "solver_calls": 0, "solver_status": None, "solver_status_raw": None, "threshold_infeasibility_certified": False, "diagnostics": {}, **candidate_fields(None, rows, n)})
    else:
        cf = candidate_fields(outcome.best_candidate, rows, n); completed = (run["solver_stratum"] == SOLVER_DISABLED and outcome.candidate_evaluations == run["budget"] and outcome.termination_reason == "candidate_budget_exhausted")
        rec.update({"termination_reason": outcome.termination_reason, "completed_budget": completed, "resource_limit": (run["solver_stratum"] == SOLVER_DISABLED and not completed) or outcome.termination_reason in RESOURCE_TERMINATIONS, "error": None, "candidate_evaluations": outcome.candidate_evaluations, "objective_evaluations": outcome.objective_evaluations, "exact_verifications": outcome.exact_verifications, "valid_codewords_seen": outcome.valid_codewords_seen, "threshold_witnesses_seen": 0 if run["phase"] == FIT_PHASE else outcome.threshold_witnesses_seen, "information_set_attempts": outcome.information_set_attempts, "information_sets_accepted": outcome.information_sets_accepted, "singular_information_sets": outcome.singular_information_sets, "solver_calls": outcome.solver_calls, "solver_status": outcome.solver_status, "solver_status_raw": outcome.solver_status_raw, "threshold_infeasibility_certified": bool(outcome.threshold_infeasibility_certified), "diagnostics": outcome.diagnostics, **cf})
    rec["threshold_hit"] = bool(rec["W"] is not None and rec["best_weight"] is not None and rec["best_weight"] <= rec["W"])
    if run["phase"] == FIT_PHASE: rec["W"] = None; rec["threshold_hit"] = False; rec["threshold_witnesses_seen"] = 0
    rec["record_sha256"] = digest({k:v for k,v in rec.items() if k != "record_sha256"}); return rec

def normalize_isd_record(src: Mapping[str, Any], run: Mapping[str, Any], plan: Mapping[str, Any], runtime: float) -> dict[str, Any]:
    rows, n = v1.parse_h_rows(run["H_rows"]); rec = base_record(run, plan, rank=src["rank"], runtime=runtime)
    rec.update({"termination_reason": src["termination_reason"], "completed_budget": src["candidate_evaluations"] == run["budget"] and src["termination_reason"] == "candidate_budget_exhausted", "resource_limit": not (src["candidate_evaluations"] == run["budget"] and src["termination_reason"] == "candidate_budget_exhausted"), "error": None, "candidate_evaluations": src["candidate_evaluations"], "objective_evaluations": src["objective_evaluations"], "exact_verifications": src["exact_verifications"], "valid_codewords_seen": src["valid_codewords_seen"], "threshold_witnesses_seen": 0 if run["phase"] == FIT_PHASE else src["threshold_witnesses_seen"], "information_set_attempts": src["information_set_attempts"], "information_sets_accepted": src["information_sets_accepted"], "singular_information_sets": src["singular_information_sets"], "solver_calls": 0, "solver_status": None, "solver_status_raw": None, "threshold_infeasibility_certified": False, "diagnostics": src["diagnostics"], "best_candidate_bits": src["best_candidate_bits"], "best_candidate_sha256": src["best_candidate_sha256"], "best_weight": src["best_weight"], "witness_verified": src["witness_verified"]})
    rec["threshold_hit"] = bool(rec["W"] is not None and rec["best_weight"] is not None and rec["best_weight"] <= rec["W"])
    if run["phase"] == FIT_PHASE: rec["W"] = None; rec["threshold_hit"] = False
    rec["record_sha256"] = digest({k:v for k,v in rec.items() if k != "record_sha256"}); return rec

def validate_result_record(record: Mapping[str, Any], *, plan: Mapping[str, Any] | None = None) -> None:
    required = {"result_schema", "protocol_id", "plan_schema", "profile_id", "manifest_kind", "plan_sha256", "candidate_manifest_sha256", "candidate_generator_config_sha256", "calibration_source_commit", "module_digests", "dependency_versions", "case_id", "H_rows", "public_h_sha256", "n", "rank", "solver_stratum", "algorithm_id", "algorithm_config", "algorithm_config_sha256", "phase", "seed_role", "seed_index", "seed_hex", "budget", "W", "runtime_s", "termination_reason", "completed_budget", "resource_limit", "error", "candidate_evaluations", "objective_evaluations", "exact_verifications", "valid_codewords_seen", "threshold_witnesses_seen", "information_set_attempts", "information_sets_accepted", "singular_information_sets", "solver_calls", "solver_status", "solver_status_raw", "threshold_infeasibility_certified", "diagnostics", "best_candidate_bits", "best_candidate_sha256", "best_weight", "witness_verified", "threshold_hit", "reference_source_phase", "record_sha256"}
    require(set(record) == required, "result has missing or unknown fields")
    require(record["result_schema"] == RESULT_SCHEMA and record["protocol_id"] == PROTOCOL_ID and record["profile_id"] in (PRODUCTION_PROFILE_ID, FIXTURE_PROFILE_ID), "bad result schema/protocol")
    require(record["manifest_kind"] == ("calibration_fixture_manifest" if record["profile_id"] == FIXTURE_PROFILE_ID else "candidate_pool_manifest"), "result manifest-kind/profile mismatch")
    require(isinstance(record["runtime_s"], (int, float)) and not isinstance(record["runtime_s"], bool) and math.isfinite(record["runtime_s"]) and record["runtime_s"] >= 0, "runtime_s must be finite and nonnegative")
    require(record["record_sha256"] == digest({k:v for k,v in record.items() if k != "record_sha256"}), "record digest mismatch")
    validate_public_case({"case_id": record["case_id"], "H_rows": record["H_rows"], "public_h_sha256": record["public_h_sha256"]})
    rows, n = v1.parse_h_rows(record["H_rows"]); require(record["n"] == n and record["rank"] == v1.gf2_rank_bit_rows(rows, n), "dimension/rank mismatch")
    for key in ("candidate_manifest_sha256", "candidate_generator_config_sha256", "plan_sha256", "algorithm_config_sha256"): require(is_sha256(record[key]), f"bad digest {key}")
    require(record["algorithm_config_sha256"] == config_digest(record["algorithm_config"]), "algorithm config digest mismatch")
    for key in ("n", "rank", "seed_index", "budget", "candidate_evaluations", "objective_evaluations", "exact_verifications", "valid_codewords_seen", "threshold_witnesses_seen", "information_set_attempts", "information_sets_accepted", "singular_information_sets", "solver_calls"): genuine_int(record[key], key, minimum=0)
    require(all(isinstance(record[key], bool) for key in ("completed_budget", "resource_limit", "witness_verified", "threshold_hit", "threshold_infeasibility_certified")), "boolean fields must be literal")
    if record["phase"] == FIT_PHASE: require(record["W"] is None and record["threshold_hit"] is False and record["threshold_witnesses_seen"] == 0, "fit result must not use W")
    else:
        if record["W"] is not None: genuine_int(record["W"], "W", minimum=0)
    if record["best_candidate_bits"] is None:
        require(record["best_candidate_sha256"] is None and record["best_weight"] is None and record["witness_verified"] is False, "empty incumbent mismatch")
    else:
        require(record["best_candidate_sha256"] == v1.candidate_sha256(record["best_candidate_bits"]), "candidate hash mismatch")
        wt = v1.verify_nonzero_kernel_word(rows, n, v1.bits_to_word(record["best_candidate_bits"])); require(record["best_weight"] == wt and record["witness_verified"] is True, "incumbent verification mismatch")
    require(record["threshold_hit"] == (record["W"] is not None and record["best_weight"] is not None and record["best_weight"] <= record["W"]), "threshold-hit mismatch")
    if record["solver_stratum"] == SOLVER_DISABLED: require(record["algorithm_id"] in SOLVER_DISABLED_ALGORITHMS and record["solver_calls"] == 0 and record["solver_status"] is None and record["solver_status_raw"] is None and record["threshold_infeasibility_certified"] is False, "solver-disabled separation violated")
    else: require(record["algorithm_id"] == CP_SAT and record["solver_stratum"] == SOLVER_ASSISTED, "solver-assisted separation violated")
    if record["solver_stratum"] == SOLVER_DISABLED:
        require(record["candidate_evaluations"] <= record["budget"], "candidate budget exceeded")
        require(record["candidate_evaluations"] == record["objective_evaluations"] == record["exact_verifications"], "candidate/objective/verification counters mismatch")
        require(record["valid_codewords_seen"] <= record["candidate_evaluations"], "valid count exceeds candidates")
        require(record["threshold_witnesses_seen"] <= record["valid_codewords_seen"], "threshold count exceeds valid count")
        require(record["information_sets_accepted"] + record["singular_information_sets"] == record["information_set_attempts"], "information-set accounting mismatch")
        require(record["completed_budget"] == (record["candidate_evaluations"] == record["budget"] and record["termination_reason"] == "candidate_budget_exhausted"), "completed_budget semantics mismatch")
        require(record["resource_limit"] == (not record["completed_budget"]), "solver-disabled resource-limit semantics mismatch")
    else:
        # This is deliberately the contract of run_cp_sat_threshold_reference(),
        # not a generic interpretation of CP-SAT statuses. That adapter makes
        # one Solve call, exposes at most its returned witness, and reports
        # UNKNOWN without turning it into an infeasibility or resource claim.
        require(record["phase"] == REFERENCE_PHASE and record["W"] is not None, "CP-SAT reference requires a threshold")
        require(record["completed_budget"] is False, "CP-SAT does not complete a candidate budget")
        require(record["information_set_attempts"] == record["information_sets_accepted"] == record["singular_information_sets"] == 0, "CP-SAT information-set counters must be zero")
        if record["termination_reason"] == "dependency_unavailable":
            require(record["solver_calls"] == 0 and record["solver_status"] == "DEPENDENCY_UNAVAILABLE" and record["solver_status_raw"] is None, "CP-SAT dependency status mismatch")
            require(record["resource_limit"] is True and isinstance(record["error"], str) and record["error"], "CP-SAT dependency error mismatch")
            require(record["candidate_evaluations"] == record["objective_evaluations"] == record["exact_verifications"] == record["valid_codewords_seen"] == record["threshold_witnesses_seen"] == 0, "CP-SAT dependency counters must be zero")
            require(record["best_candidate_bits"] is None and record["threshold_hit"] is False and record["threshold_infeasibility_certified"] is False, "CP-SAT dependency outcome mismatch")
        else:
            require(record["solver_calls"] == 1 and record["error"] is None and isinstance(record["solver_status_raw"], str) and record["solver_status_raw"], "CP-SAT call/status provenance mismatch")
            if record["termination_reason"] == "solver_feasible":
                require(record["solver_status"] == "FEASIBLE" and record["solver_status_raw"] in {"OPTIMAL", "FEASIBLE"}, "CP-SAT feasible status mismatch")
                require(record["resource_limit"] is False and record["threshold_infeasibility_certified"] is False and record["threshold_hit"] is True, "CP-SAT feasible flags mismatch")
                require(record["candidate_evaluations"] == record["objective_evaluations"] == record["exact_verifications"] == record["valid_codewords_seen"] == record["threshold_witnesses_seen"] == 1, "CP-SAT feasible counters mismatch")
                require(record["best_candidate_bits"] is not None, "CP-SAT feasible witness missing")
            elif record["termination_reason"] == "solver_infeasible":
                require(record["solver_status"] == "INFEASIBLE" and record["solver_status_raw"] == "INFEASIBLE", "CP-SAT infeasible status mismatch")
                require(record["resource_limit"] is False and record["threshold_infeasibility_certified"] is True and record["threshold_hit"] is False, "CP-SAT infeasible flags mismatch")
                require(record["candidate_evaluations"] == record["objective_evaluations"] == record["exact_verifications"] == record["valid_codewords_seen"] == record["threshold_witnesses_seen"] == 0 and record["best_candidate_bits"] is None, "CP-SAT infeasible outcome mismatch")
            elif record["termination_reason"] == "solver_unknown_or_limit":
                require(record["solver_status"] == "UNKNOWN" and record["solver_status_raw"] == "UNKNOWN", "CP-SAT unknown status mismatch")
                require(record["resource_limit"] is False and record["threshold_infeasibility_certified"] is False and record["threshold_hit"] is False, "CP-SAT unknown flags mismatch")
                require(record["candidate_evaluations"] == record["objective_evaluations"] == record["exact_verifications"] == record["valid_codewords_seen"] == record["threshold_witnesses_seen"] == 0 and record["best_candidate_bits"] is None, "CP-SAT unknown outcome mismatch")
            else:
                fail("unknown CP-SAT termination reason")
    if record["completed_budget"]: require(record["resource_limit"] is False, "completed resource-limit mismatch")
    if plan is not None:
        require(record["plan_sha256"] == plan["plan_sha256"], "record bound to wrong plan")
        for bind in ("profile_id", "manifest_kind", "candidate_manifest_sha256", "candidate_generator_config_sha256", "calibration_source_commit", "module_digests", "dependency_versions") :
            require(record[bind] == plan[bind], f"record binding mismatch {bind}")
        exp = {run_identity(run): run for run in plan["runs"]}; ident = result_identity(record); require(ident in exp, "record identity not in plan")
        planned = exp[ident]
        for field in ("case_id", "H_rows", "public_h_sha256", "W", "seed_role", "seed_index", "seed_hex", "phase", "budget", "solver_stratum", "algorithm_id", "algorithm_config", "algorithm_config_sha256", "reference_source_phase"):
            require(record[field] == planned.get(field), f"record planned-field mismatch {field}")

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
    validate_plan_against_manifest(plan, manifest); validate_plan(plan, expected_schema=FIT_PLAN_SCHEMA); validate_results(plan, records)
    by_case: dict[str, list[Mapping[str, Any]]] = {}
    for rec in records: by_case.setdefault(rec["case_id"], []).append(rec)
    thresholds = []
    for crec in manifest["records"]:
        cid = crec["case_id"]; meta = evaluator_metadata(crec); rs = by_case.get(cid, [])
        max_rs = [r for r in rs if r["budget"] == plan["budgets"][-1]]
        if meta["exact_distance"] is not None:
            W = genuine_int(meta["exact_distance"], "exact distance", minimum=0); source = "exact_control_replay"; artificial = False; decision = "threshold_frozen"
        elif meta["is_planted"] and meta["planted_upper_bound"] is not None:
            W = genuine_int(meta["planted_upper_bound"], "planted upper bound", minimum=1); source = "evaluator_planted_upper_bound"; artificial = True; decision = "threshold_frozen_artificial"
        else:
            vals = [r["best_weight"] for r in max_rs if completed_incumbent(r)]; algs = {r["algorithm_id"] for r in max_rs if completed_incumbent(r)}; denom = len(SOLVER_DISABLED_ALGORITHMS)*8
            if len(vals)/denom >= 0.5 and len(algs) >= 2:
                W = nearest_rank(vals, 0.40); source = "solver_disabled_nearest_rank_40pct"; decision = "threshold_frozen"; artificial = False
            else:
                W = None; source = "insufficient_availability"; decision = "calibration_incomplete"; artificial = False
        thresholds.append({"case_id": cid, "public_h_sha256": crec["public_h_sha256"], "n": crec.get("n"), "W": W, "threshold_source": source, "decision": decision, "planted_threshold_artificial": artificial, "certified_lower_bound": meta["certified_lower_bound"], "exact_distance": meta["exact_distance"], "fit_denominator": len(SOLVER_DISABLED_ALGORITHMS)*8, "fit_available": sum(1 for r in max_rs if completed_incumbent(r)), "fit_contributing_algorithms": sorted({r["algorithm_id"] for r in max_rs if completed_incumbent(r)})})
    artifact = {"schema": THRESHOLD_SCHEMA, "protocol_id": PROTOCOL_ID, "manifest_kind": plan["manifest_kind"], "profile_id": plan["profile_id"], "budgets": plan["budgets"], "fit_plan_sha256": plan["plan_sha256"], "candidate_manifest_sha256": manifest_digest(manifest), "candidate_generator_config_sha256": plan["candidate_generator_config_sha256"], "calibration_source_commit": plan["calibration_source_commit"], "module_digests": plan["module_digests"], "dependency_versions": plan["dependency_versions"], "fit_results_sha256": digest(list(records)), "thresholds": thresholds}; artifact["thresholds_sha256"] = digest({k:v for k,v in artifact.items() if k != "thresholds_sha256"}); return artifact

def validate_threshold_artifact(artifact: Mapping[str, Any], *, manifest: Mapping[str, Any] | None = None, plan: Mapping[str, Any] | None = None, records: Sequence[Mapping[str, Any]] | None = None) -> None:
    required = {"schema", "protocol_id", "manifest_kind", "profile_id", "budgets", "fit_plan_sha256", "candidate_manifest_sha256", "candidate_generator_config_sha256", "calibration_source_commit", "module_digests", "dependency_versions", "fit_results_sha256", "thresholds", "thresholds_sha256"}
    require(set(artifact) == required, "bad threshold top-level schema")
    require(artifact["schema"] == THRESHOLD_SCHEMA and artifact["protocol_id"] == PROTOCOL_ID, "bad threshold artifact")
    require(artifact["profile_id"] in (PRODUCTION_PROFILE_ID, FIXTURE_PROFILE_ID) and tuple(artifact["budgets"]) == plan_budgets(artifact["profile_id"]), "threshold profile/budgets mismatch")
    require(artifact["manifest_kind"] == ("calibration_fixture_manifest" if artifact["profile_id"] == FIXTURE_PROFILE_ID else "candidate_pool_manifest"), "threshold manifest-kind/profile mismatch")
    require(artifact["thresholds_sha256"] == digest({k:v for k,v in artifact.items() if k != "thresholds_sha256"}), "threshold artifact digest mismatch")
    seen = set()
    for row in artifact["thresholds"]:
        require(set(row) == {"case_id", "public_h_sha256", "n", "W", "threshold_source", "decision", "planted_threshold_artificial", "certified_lower_bound", "exact_distance", "fit_denominator", "fit_available", "fit_contributing_algorithms"}, "bad threshold row schema")
        require(row["case_id"] not in seen, "duplicate threshold row"); seen.add(row["case_id"])
        if row["W"] is not None: genuine_int(row["W"], "W", minimum=0)
        for name in ("fit_denominator", "fit_available", "certified_lower_bound"): genuine_int(row[name], name, minimum=0)
        require(isinstance(row["planted_threshold_artificial"], bool) and isinstance(row["fit_contributing_algorithms"], list), "bad threshold field types")
    if manifest is not None:
        require(artifact["candidate_manifest_sha256"] == manifest_digest(manifest), "threshold manifest digest mismatch")
        require(artifact["profile_id"] == manifest_profile(manifest), "threshold/manifest profile mismatch")
        require(seen == {r["case_id"] for r in manifest["records"]}, "threshold artifact cases do not match manifest")
    require((plan is None) == (records is None), "threshold replay needs both fit plan and results")
    if plan is not None and records is not None:
        validate_plan_against_manifest(plan, manifest if manifest is not None else fail("threshold replay requires manifest"))
        require(plan["schema"] == FIT_PLAN_SCHEMA and artifact["fit_plan_sha256"] == plan["plan_sha256"], "threshold fit-plan binding mismatch")
        require(artifact["profile_id"] == plan["profile_id"] and artifact["budgets"] == plan["budgets"], "threshold plan profile/budget mismatch")
        require(artifact["fit_results_sha256"] == digest(list(records)), "threshold result evidence mismatch")
        authoritative = fit_thresholds(manifest, plan, records)
        require(canonical_bytes(artifact) == canonical_bytes(authoritative), "threshold artifact disagrees with authoritative replay")

def validate_tiers(manifest: Mapping[str, Any], plan: Mapping[str, Any], thresholds: Mapping[str, Any], records: Sequence[Mapping[str, Any],], *, fit_plan: Mapping[str, Any] | None = None, fit_records: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    require(fit_plan is not None and fit_records is not None, "tier validation requires threshold replay evidence")
    validate_plan_against_manifest(plan, manifest); validate_plan(plan, expected_schema=TIER_PLAN_SCHEMA); validate_threshold_artifact(thresholds, manifest=manifest, plan=fit_plan, records=fit_records); require(plan["thresholds_sha256"] == thresholds["thresholds_sha256"], "tier plan/threshold digest mismatch"); validate_results(plan, records)
    tm = threshold_map(thresholds); by_case: dict[str, list[Mapping[str, Any]]] = {}
    for rec in records:
        if rec["solver_stratum"] == SOLVER_DISABLED: by_case.setdefault(rec["case_id"], []).append(rec)
    tiers = []
    for crec in manifest["records"]:
        cid = crec["case_id"]; trow = tm[cid]; W = trow["W"]; rs = by_case.get(cid, []); n = int(trow["n"] or len(crec["H_rows"][0])); lower = int(trow["certified_lower_bound"] or 1)
        if W is None:
            # Non-applicable statistics are explicit so all tier rows replay under one schema.
            tiers.append({"case_id": cid, "tier": "calibration_incomplete", "decision": "incomplete", "reason": "missing_threshold", "W": None, "hit_rates": {}, "resource_limit_frequencies": {}, "iqr": None, "algorithm_medians": {}, "algorithm_agreement_gap2": False, "best_solver_disabled_upper_bound": None, "certified_lower_bound": lower, "hard_gap_ok": False}); continue
        rates = {}; rlf = {}
        for budget in plan["budgets"]:
            br = [r for r in rs if r["budget"] == budget]; denom = len(SOLVER_DISABLED_ALGORITHMS)*8
            rates[str(budget)] = sum(1 for r in br if r["threshold_hit"])/denom
            rlf[str(budget)] = (denom - len(br) + sum(1 for r in br if r["resource_limit"] or not r["completed_budget"])) / denom
        maxrs = [r for r in rs if r["budget"] == plan["budgets"][-1] and completed_incumbent(r)]; weights = [r["best_weight"] for r in maxrs]
        iqr = None if len(weights) < 8 else nearest_rank(weights, .75) - nearest_rank(weights, .25)
        medians = {alg: lower_median([r["best_weight"] for r in maxrs if r["algorithm_id"] == alg]) for alg in SOLVER_DISABLED_ALGORITHMS if len([r for r in maxrs if r["algorithm_id"] == alg]) >= 2}
        agree = any(abs(a-b) <= 2 for i, a in enumerate(medians.values()) for b in list(medians.values())[i+1:])
        best = min(weights) if weights else None; hard_gap_ok = best is not None and best - lower <= max(12, math.ceil(0.20*n))
        hi, lo = rates[str(plan["budgets"][-1])], rates[str(plan["budgets"][0])]
        if trow["exact_distance"] is not None: tier, decision, reason = "control_exact", "accepted", "exact_control"
        elif iqr is None: tier, decision, reason = "calibration_incomplete", "incomplete", "insufficient_incumbent_iqr"
        elif not agree: tier, decision, reason = "calibration_incomplete", "incomplete", "algorithm_disagreement"
        elif .70 <= hi < .90 and lo < .70: tier, decision, reason = "easy_calibrated", "accepted", "easy_rule"
        elif .35 <= hi < .70 and lo < .50 and iqr >= 1 and rlf[str(plan["budgets"][-1])] < .25: tier, decision, reason = "medium_calibrated", "accepted", "medium_rule"
        elif .10 <= hi < .35 and best is not None and rlf[str(plan["budgets"][-1])] < .50 and hard_gap_ok: tier, decision, reason = "hard_calibrated", "accepted", "hard_rule"
        else: tier, decision, reason = "calibration_incomplete", "incomplete", "tier_rule_not_satisfied"
        if trow["planted_threshold_artificial"]:
            tier, decision, reason = "planted_artificial", "accepted", "artificial_planted_threshold"
        tiers.append({"case_id": cid, "tier": tier, "decision": decision, "reason": reason, "W": W, "hit_rates": rates, "resource_limit_frequencies": rlf, "iqr": iqr, "algorithm_medians": medians, "algorithm_agreement_gap2": agree, "best_solver_disabled_upper_bound": best, "certified_lower_bound": lower, "hard_gap_ok": hard_gap_ok})
    artifact = {"schema": TIER_SCHEMA, "protocol_id": PROTOCOL_ID, "manifest_kind": plan["manifest_kind"], "profile_id": plan["profile_id"], "budgets": plan["budgets"], "tier_plan_sha256": plan["plan_sha256"], "thresholds_sha256": thresholds["thresholds_sha256"], "candidate_manifest_sha256": plan["candidate_manifest_sha256"], "tier_results_sha256": digest(list(records)), "tiers": tiers}; artifact["tiers_sha256"] = digest({k:v for k,v in artifact.items() if k != "tiers_sha256"}); return artifact

def validate_tier_artifact(artifact: Mapping[str, Any], *, manifest: Mapping[str, Any] | None = None, plan: Mapping[str, Any] | None = None, thresholds: Mapping[str, Any] | None = None, records: Sequence[Mapping[str, Any]] | None = None, fit_plan: Mapping[str, Any] | None = None, fit_records: Sequence[Mapping[str, Any]] | None = None) -> None:
    required = {"schema", "protocol_id", "manifest_kind", "profile_id", "budgets", "tier_plan_sha256", "thresholds_sha256", "candidate_manifest_sha256", "tier_results_sha256", "tiers", "tiers_sha256"}
    require(set(artifact) == required and artifact["schema"] == TIER_SCHEMA and artifact["protocol_id"] == PROTOCOL_ID, "bad tier artifact schema")
    require(artifact["tiers_sha256"] == digest({k:v for k,v in artifact.items() if k != "tiers_sha256"}), "tier artifact digest mismatch")
    require(artifact["profile_id"] in (PRODUCTION_PROFILE_ID, FIXTURE_PROFILE_ID) and tuple(artifact["budgets"]) == plan_budgets(artifact["profile_id"]), "tier profile/budgets mismatch")
    row_keys = {"case_id", "tier", "decision", "reason", "W", "hit_rates", "resource_limit_frequencies", "iqr", "algorithm_medians", "algorithm_agreement_gap2", "best_solver_disabled_upper_bound", "certified_lower_bound", "hard_gap_ok"}
    seen=set()
    for row in artifact["tiers"]:
        require(set(row) == row_keys, "bad tier row schema")
        require(row["case_id"] not in seen, "duplicate tier row"); seen.add(row["case_id"])
    supplied = (manifest, plan, thresholds, records, fit_plan, fit_records)
    require(all(x is None for x in supplied) or all(x is not None for x in supplied), "tier replay needs manifest, plans, thresholds, and results")
    if manifest is not None:
        require(artifact["profile_id"] == manifest_profile(manifest) and artifact["candidate_manifest_sha256"] == manifest_digest(manifest), "tier/manifest binding mismatch")
        require(seen == {r["case_id"] for r in manifest["records"]}, "tier cases do not match manifest")
        validate_threshold_artifact(thresholds, manifest=manifest, plan=fit_plan, records=fit_records)
        validate_plan_against_manifest(plan, manifest); require(plan["schema"] == TIER_PLAN_SCHEMA, "tier plan schema mismatch")
        require(plan["thresholds_sha256"] == thresholds["thresholds_sha256"], "tier plan/threshold binding mismatch")
        require(artifact["tier_plan_sha256"] == plan["plan_sha256"] and artifact["thresholds_sha256"] == thresholds["thresholds_sha256"], "tier evidence binding mismatch")
        require(artifact["tier_results_sha256"] == digest(list(records)), "tier result evidence mismatch")
        authoritative = validate_tiers(manifest, plan, thresholds, records, fit_plan=fit_plan, fit_records=fit_records)
        require(canonical_bytes(artifact) == canonical_bytes(authoritative), "tier artifact disagrees with authoritative replay")


def assigned_runs(plan: Mapping[str, Any], shard_index: int, shard_count: int) -> list[Mapping[str, Any]]:
    genuine_int(shard_index, "shard_index", minimum=0); genuine_int(shard_count, "shard_count", minimum=1); require(shard_index < shard_count, "shard_index must be < shard_count")
    return [run for idx, run in enumerate(plan["runs"]) if idx % shard_count == shard_index]
def merge_shards(plan: Mapping[str, Any], paths: Sequence[Path]) -> list[dict[str, Any]]:
    records_by_identity: dict[tuple[Any, ...], dict[str, Any]] = {}
    for path in paths:
        for record in read_jsonl(path):
            ident = result_identity(record)
            require(ident not in records_by_identity, "duplicate or overlapping shard record")
            records_by_identity[ident] = record
    validate_results(plan, list(records_by_identity.values()))
    ordered = []
    for run in plan["runs"]:
        ident = run_identity(run); require(ident in records_by_identity, "missing shard record")
        ordered.append(records_by_identity[ident])
    return ordered

def make_fixture_manifest() -> dict[str, Any]:
    records = [
        {"case_id": "calv2-fixture-even4", "H_rows": ["1111"], "public_h_sha256": isd_v2.public_h_sha256(["1111"]), "n": 4, "family_id": "exact-control", "validation": {"known_distance": {"distance": 2}, "small_circuit": {"status": "PASS", "cap": 1}}},
        {"case_id": "calv2-fixture-planted5", "H_rows": ["10101", "01110"], "public_h_sha256": isd_v2.public_h_sha256(["10101", "01110"]), "n": 5, "family_id": "planted-fixture", "validation": {"small_circuit": {"status": "PASS", "cap": 1}}, "evaluator_only_provenance": {"planted_witness_support": [0, 1, 2]}},
    ]
    man = {"manifest_kind": "calibration_fixture_manifest", "candidate_manifest_digest": "f"*64, "configuration_digest": corpus_v2.config_digest(), "records": records}
    return man

def smoke(output_dir: Path) -> dict[str, Any]:
    # Fixture evidence is deliberately byte-reproducible; production records
    # retain the measured runtime supplied by execute_run/base_record.
    def fixture_record(run: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
        record = execute_run(run, plan)
        record["runtime_s"] = 0.0
        record["record_sha256"] = digest({k: v for k, v in record.items() if k != "record_sha256"})
        return record
    output_dir.mkdir(parents=True, exist_ok=True); manifest = make_fixture_manifest(); write_json(output_dir/"fixture_manifest.json", manifest)
    fit_plan = build_threshold_fit_plan(manifest, profile_id=FIXTURE_PROFILE_ID); write_json(output_dir/"threshold_fit_plan.json", fit_plan)
    fit_runs = assigned_runs(fit_plan, 0, 1); fit_records = [fixture_record(run, fit_plan) for run in fit_runs]; write_jsonl(output_dir/"threshold_fit_results.jsonl", fit_records)
    thresholds = fit_thresholds(manifest, fit_plan, fit_records); write_json(output_dir/"thresholds.json", thresholds)
    tier_plan = build_tier_reference_plan(manifest, thresholds, profile_id=FIXTURE_PROFILE_ID, fit_plan=fit_plan, fit_records=fit_records); write_json(output_dir/"tier_reference_plan.json", tier_plan)
    tier_records = [fixture_record(run, tier_plan) for run in tier_plan["runs"] if not (run["algorithm_id"] == CP_SAT and dependency_versions()["ortools"] is None)]
    write_jsonl(output_dir/"tier_reference_results.jsonl", tier_records)
    # For smoke without OR-Tools, validate the partial operational shard and skip final tier artifact.
    if len(tier_records) == len(tier_plan["runs"]): tiers = validate_tiers(manifest, tier_plan, thresholds, tier_records, fit_plan=fit_plan, fit_records=fit_records); write_json(output_dir/"tiers.json", tiers)
    return {"fit_records": len(fit_records), "tier_records": len(tier_records), "tier_plan_runs": len(tier_plan["runs"])}

def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(); sub = p.add_subparsers(dest="cmd", required=True)
    a=sub.add_parser("threshold-fit-plan"); a.add_argument("manifest"); a.add_argument("--output", required=True); a.add_argument("--allow-fixture", action="store_true")
    a=sub.add_parser("fit-thresholds"); a.add_argument("manifest"); a.add_argument("plan"); a.add_argument("results"); a.add_argument("--output", required=True); a.add_argument("--allow-fixture", action="store_true")
    a=sub.add_parser("tier-reference-plan"); a.add_argument("manifest"); a.add_argument("thresholds"); a.add_argument("--fit-plan", required=True); a.add_argument("--fit-results", required=True); a.add_argument("--output", required=True); a.add_argument("--allow-fixture", action="store_true")
    a=sub.add_parser("run-shard"); a.add_argument("manifest"); a.add_argument("plan"); a.add_argument("--thresholds"); a.add_argument("--fit-plan"); a.add_argument("--fit-results"); a.add_argument("--shard-index", type=int, required=True); a.add_argument("--shard-count", type=int, required=True); a.add_argument("--output", required=True); a.add_argument("--allow-fixture", action="store_true")
    a=sub.add_parser("validate-results"); a.add_argument("plan"); a.add_argument("results", nargs="+"); a.add_argument("--allow-partial", action="store_true"); a.add_argument("--summary", action="store_true")
    a=sub.add_parser("validate-tiers"); a.add_argument("manifest"); a.add_argument("plan"); a.add_argument("thresholds"); a.add_argument("results"); a.add_argument("--fit-plan", required=True); a.add_argument("--fit-results", required=True); a.add_argument("--output", required=True); a.add_argument("--allow-fixture", action="store_true")
    a=sub.add_parser("merge-shards"); a.add_argument("plan"); a.add_argument("shards", nargs="+"); a.add_argument("--output", required=True)
    a=sub.add_parser("summary"); a.add_argument("tiers"); a.add_argument("--manifest", required=True); a.add_argument("--tier-plan", required=True); a.add_argument("--thresholds", required=True); a.add_argument("--tier-results", required=True); a.add_argument("--fit-plan", required=True); a.add_argument("--fit-results", required=True); a.add_argument("--allow-fixture", action="store_true")
    a=sub.add_parser("smoke"); a.add_argument("--output-dir", required=True)
    args = p.parse_args(argv)
    if args.cmd == "threshold-fit-plan": write_json(Path(args.output), build_threshold_fit_plan(load_candidate_manifest(Path(args.manifest), allow_fixture=args.allow_fixture), profile_id=FIXTURE_PROFILE_ID if args.allow_fixture else PRODUCTION_PROFILE_ID)); return 0
    if args.cmd == "fit-thresholds": write_json(Path(args.output), fit_thresholds(load_candidate_manifest(Path(args.manifest), allow_fixture=args.allow_fixture), read_json(Path(args.plan)), read_jsonl(Path(args.results)))); return 0
    if args.cmd == "tier-reference-plan": write_json(Path(args.output), build_tier_reference_plan(load_candidate_manifest(Path(args.manifest), allow_fixture=args.allow_fixture), read_json(Path(args.thresholds)), profile_id=FIXTURE_PROFILE_ID if args.allow_fixture else PRODUCTION_PROFILE_ID, fit_plan=read_json(Path(args.fit_plan)), fit_records=read_jsonl(Path(args.fit_results)))); return 0
    if args.cmd == "run-shard":
        manifest = load_candidate_manifest(Path(args.manifest), allow_fixture=args.allow_fixture); plan = read_json(Path(args.plan)); validate_plan_against_manifest(plan, manifest);
        if plan["schema"] == TIER_PLAN_SCHEMA: require(args.thresholds is not None, "tier/reference shards require threshold artifact"); threshold_artifact = read_json(Path(args.thresholds)); require(args.fit_plan is not None and args.fit_results is not None, "tier/reference shards require complete fit replay evidence"); validate_threshold_artifact(threshold_artifact, manifest=manifest, plan=read_json(Path(args.fit_plan)), records=read_jsonl(Path(args.fit_results))); require(plan.get("thresholds_sha256") == threshold_artifact["thresholds_sha256"], "plan/threshold artifact mismatch")
        runs = assigned_runs(plan, args.shard_index, args.shard_count); require(runs, "assigned shard is empty"); write_jsonl(Path(args.output), [execute_run(run, plan) for run in runs]); return 0
    if args.cmd == "validate-results":
        plan = read_json(Path(args.plan)); records=[]
        for path in args.results: records.extend(read_jsonl(Path(path)))
        summary = validate_results(plan, records, allow_partial=args.allow_partial)
        if args.summary: print(json.dumps(summary, sort_keys=True))
        return 0
    if args.cmd == "validate-tiers": write_json(Path(args.output), validate_tiers(load_candidate_manifest(Path(args.manifest), allow_fixture=args.allow_fixture), read_json(Path(args.plan)), read_json(Path(args.thresholds)), read_jsonl(Path(args.results)), fit_plan=read_json(Path(args.fit_plan)), fit_records=read_jsonl(Path(args.fit_results)))); return 0
    if args.cmd == "merge-shards": write_jsonl(Path(args.output), merge_shards(read_json(Path(args.plan)), [Path(p) for p in args.shards])); return 0
    if args.cmd == "summary":
        manifest = load_candidate_manifest(Path(args.manifest), allow_fixture=args.allow_fixture); artifact = read_json(Path(args.tiers)); validate_tier_artifact(artifact, manifest=manifest, plan=read_json(Path(args.tier_plan)), thresholds=read_json(Path(args.thresholds)), records=read_jsonl(Path(args.tier_results)), fit_plan=read_json(Path(args.fit_plan)), fit_records=read_jsonl(Path(args.fit_results))); tiers = artifact["tiers"]; print(json.dumps({tier: sum(1 for row in tiers if row["tier"] == tier) for tier in sorted({row["tier"] for row in tiers})}, sort_keys=True)); return 0
    if args.cmd == "smoke": print(json.dumps(smoke(Path(args.output_dir)), sort_keys=True)); return 0
    return 2

if __name__ == "__main__":
    try: raise SystemExit(main())
    except (CalibrationV2Error, v1.BaselineValidationError, isd_v2.ISDValidationError, corpus_v2.V2Error, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr); raise SystemExit(1)
