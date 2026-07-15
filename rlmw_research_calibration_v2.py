"""External calibration harness for h-native-research-v2.

Source-only orchestration: plans, validates, fits thresholds, validates tiers, and
runs deterministic smoke fixtures.  It never writes a frozen 192-case manifest,
threshold file, tier file, log, matrix dump, or final-evaluation data into the
repository.
"""
from __future__ import annotations

import argparse, hashlib, json, math, subprocess, sys, time
from pathlib import Path
from typing import Any, Iterable, Sequence

import rlmw_research_corpus_v2 as corpus_v2
import rlmw_research_isd_v2 as isd_v2

PROTOCOL_ID = "h-native-research-v2-calibration-v1"
PLAN_SCHEMA = "rlmw-research-v2-calibration-plan-v1"
RESULT_SCHEMA = "rlmw-research-v2-calibration-result-v1"
THRESHOLD_SCHEMA = "rlmw-research-v2-thresholds-v1"
TIER_SCHEMA = "rlmw-research-v2-tiers-v1"
SOLVER_DISABLED = "solver_disabled"
SOLVER_ASSISTED = "solver_assisted_reference"
ALGORITHMS = ("uniform_kernel_sampling_v1","fixed_weight_subset_sampling_v1","lee_brickell_isd_v1",isd_v2.ALGORITHM_ID)
CP_SAT_ALGORITHM = "cp_sat_threshold_reference_v1"
BUDGETS = (1<<12, 1<<14, 1<<16, 1<<18)
FIT_ROLE = "threshold_fit_seed"
TIER_ROLE = "tier_validation_seed"
FIT_PHASE = "threshold_fit"
TIER_PHASE = "tier_validation"
CP_SAT_SEEDS = ((FIT_PHASE,FIT_ROLE,0),(FIT_PHASE,FIT_ROLE,1),(TIER_PHASE,TIER_ROLE,0),(TIER_PHASE,TIER_ROLE,1))
CP_SAT_PROFILES = ({"wall_seconds":60,"deterministic_time_limit":10_000_000,"num_search_workers":1},{"wall_seconds":600,"deterministic_time_limit":100_000_000,"num_search_workers":1})
FORBIDDEN_PAYLOAD = {"family_id","split","lineage_group_id","planted_witness","planted_witness_support","exact_distance","calibration_incumbent","evaluator_only_provenance","solver_assisted_evidence","validation","structural_status","certified_distance"}

class CalibrationV2Error(ValueError): pass

def fail(msg: str) -> None: raise CalibrationV2Error(f"{PROTOCOL_ID}: {msg}")
def req(ok: bool, msg: str) -> None:
    if not ok: fail(msg)
def gint(x: Any, name: str, lo: int|None=None, hi: int|None=None) -> int:
    req(isinstance(x,int) and not isinstance(x,bool), f"{name} must be a genuine integer")
    if lo is not None: req(x>=lo, f"{name} below minimum")
    if hi is not None: req(x<=hi, f"{name} above maximum")
    return x
def sha_file(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def canonical_bytes(x: Any) -> bytes:
    return json.dumps(x, sort_keys=True, separators=(",",":"), ensure_ascii=True, allow_nan=False).encode("ascii")
def digest(x: Any) -> str: return hashlib.sha256(canonical_bytes(x)).hexdigest()
def source_commit() -> str:
    return subprocess.check_output(["git","rev-parse","HEAD"], text=True).strip()
def module_digests() -> dict[str,str]:
    return {"calibration_module_sha256": sha_file(Path(__file__)), "candidate_config_digest": corpus_v2.config_digest(), "isd_module_sha256": sha_file(Path(isd_v2.__file__))}
class RejectDuplicateKeys(dict):
    def __init__(self, pairs: Iterable[tuple[str,Any]]):
        super().__init__()
        for k,v in pairs:
            if k in self: fail(f"duplicate JSON key {k}")
            self[k]=v
def read_json(path: Path) -> Any:
    raw = path.read_bytes()
    obj = json.loads(raw.decode("ascii"), object_pairs_hook=RejectDuplicateKeys)
    req(canonical_bytes(obj)==raw.rstrip(b"\n"), f"{path} is not canonical JSON")
    return obj
def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(canonical_bytes(obj)+b"\n")
def read_jsonl(path: Path) -> list[dict[str,Any]]:
    out=[]
    for i,line in enumerate(path.read_bytes().splitlines(),1):
        req(bool(line), f"blank JSONL line {i}"); obj=json.loads(line.decode("ascii"), object_pairs_hook=RejectDuplicateKeys)
        req(canonical_bytes(obj)==line, f"noncanonical JSONL line {i}"); out.append(obj)
    return out
def append_jsonl(path: Path, records: Sequence[dict[str,Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as f:
        for r in records: f.write(canonical_bytes(r)+b"\n")

def load_candidate_manifest(path: Path, *, full: bool=True) -> dict[str,Any]:
    payload = read_json(path)
    corpus_v2.validate_manifest(payload, full=full)
    req(payload["generation_profile"]=="accepted", "manifest must be generation_profile=accepted")
    req(payload["calibration_ready"] is True, "manifest must be calibration_ready=true")
    req(len(payload["records"])==192, "manifest must contain exactly 192 records")
    req(payload["configuration_digest"]==corpus_v2.config_digest(), "candidate configuration digest mismatch")
    req(isinstance(payload.get("source_commit"),str) and len(payload["source_commit"])>=40, "manifest needs concrete source commit")
    return payload

def public_payload(case: dict[str,Any], *, manifest_digest: str, phase: str, seed_role: str, seed_index: int, budget: int, algorithm_id: str, W: int|None=None) -> dict[str,Any]:
    req(not (set(case) & FORBIDDEN_PAYLOAD), "case object passed to public_payload contains evaluator-only fields")
    req(phase in (FIT_PHASE,TIER_PHASE), "bad phase"); gint(seed_index,"seed_index",0,7); gint(budget,"budget",0)
    req((phase,seed_role) in ((FIT_PHASE,FIT_ROLE),(TIER_PHASE,TIER_ROLE)), "bad phase/seed role")
    req(algorithm_id in ALGORITHMS+(CP_SAT_ALGORITHM,), "bad algorithm")
    p={"case_id":case["case_id"],"H_rows":case["H_rows"],"public_h_sha256":case["public_h_sha256"],"phase":phase,"seed_role":seed_role,"seed_index":seed_index,"budget":budget,"algorithm_id":algorithm_id,"candidate_protocol_version":corpus_v2.PROTOCOL_ID,"candidate_generator_config_sha256":corpus_v2.config_digest(),"candidate_manifest_sha256":manifest_digest}
    if phase==TIER_PHASE:
        p["W"] = gint(W,"W",0)
    else:
        req(W is None, "threshold-fit payload must not include W")
    req(not (set(p)&FORBIDDEN_PAYLOAD), "public payload leaked forbidden fields")
    return p

def manifest_case(rec: dict[str,Any]) -> dict[str,Any]:
    return {"case_id":rec["case_id"],"H_rows":rec["H_rows"],"public_h_sha256":rec["public_h_sha256"]}

def plan_from_manifest(manifest: dict[str,Any]) -> dict[str,Any]:
    md=manifest["candidate_manifest_digest"]; src=source_commit(); mods=module_digests(); runs=[]
    for rec in manifest["records"]:
        base={"case_id":rec["case_id"],"public_h_sha256":rec["public_h_sha256"],"candidate_manifest_sha256":md,"calibration_source_commit":src,**mods}
        for alg in ALGORITHMS:
            for seed in range(8): runs.append({**base,"solver_stratum":SOLVER_DISABLED,"algorithm_id":alg,"phase":FIT_PHASE,"seed_role":FIT_ROLE,"seed_index":seed,"budget":BUDGETS[-1]})
            for budget in BUDGETS:
                for seed in range(8): runs.append({**base,"solver_stratum":SOLVER_DISABLED,"algorithm_id":alg,"phase":TIER_PHASE,"seed_role":TIER_ROLE,"seed_index":seed,"budget":budget})
        for prof in CP_SAT_PROFILES:
            for ph,role,seed in CP_SAT_SEEDS: runs.append({**base,"solver_stratum":SOLVER_ASSISTED,"algorithm_id":CP_SAT_ALGORITHM,"phase":ph,"seed_role":role,"seed_index":seed,"budget":0,"cp_sat_profile":prof})
    plan={"schema":PLAN_SCHEMA,"protocol_id":PROTOCOL_ID,"candidate_manifest_sha256":md,"calibration_source_commit":src,"module_digests":mods,"budgets":list(BUDGETS),"solver_disabled_algorithms":list(ALGORITHMS),"solver_assisted_reference":{"algorithm_id":CP_SAT_ALGORITHM,"profiles":CP_SAT_PROFILES,"seed_pairs":CP_SAT_SEEDS},"runs":runs}
    plan["plan_sha256"]=digest({k:v for k,v in plan.items() if k!="plan_sha256"}); return plan

def identity(r: dict[str,Any]) -> tuple[Any,...]:
    return (r["case_id"],r["solver_stratum"],r["algorithm_id"],r["phase"],r["seed_role"],r["seed_index"],r["budget"],digest(r.get("cp_sat_profile",{})))

def verify_incumbent(record: dict[str,Any]) -> None:
    bits=record.get("best_candidate_bits")
    if bits is None: return
    rows,n=isd_v2.parse_h_rows(record["H_rows"]); word=isd_v2.bits_to_word(bits); wt=isd_v2.verify_nonzero_kernel_word(rows,n,word)
    req(record.get("best_weight")==wt, "incumbent weight/H verification failed")

def validate_result_record(r: dict[str,Any]) -> None:
    req(r.get("result_schema")==RESULT_SCHEMA, "bad result schema")
    req(r.get("solver_stratum") in (SOLVER_DISABLED,SOLVER_ASSISTED), "bad solver stratum")
    for k in ("candidate_manifest_sha256","public_h_sha256","calibration_source_commit","calibration_module_sha256","candidate_config_digest","algorithm_id","seed_role","seed_index","budget"):
        req(k in r, f"missing binding field {k}")
    gint(r["seed_index"],"seed_index",0,7); gint(r["budget"],"budget",0)
    req(r["phase"] in (FIT_PHASE,TIER_PHASE), "bad phase")
    req((r["phase"],r["seed_role"]) in ((FIT_PHASE,FIT_ROLE),(TIER_PHASE,TIER_ROLE)), "wrong seed role for phase")
    if r["phase"]==FIT_PHASE: req(r.get("W") is None and r.get("threshold_hit") is False, "threshold fit must have no W/hit")
    else: gint(r.get("W"),"W",0)
    req(r["algorithm_id"] in ALGORITHMS+(CP_SAT_ALGORITHM,), "unexpected algorithm")
    if r["algorithm_id"]==CP_SAT_ALGORITHM:
        req(r["solver_stratum"]==SOLVER_ASSISTED, "CP-SAT must be solver-assisted reference")
    else:
        req(r["solver_stratum"]==SOLVER_DISABLED, "classical baselines must be solver-disabled")
    verify_incumbent(r)

def validate_results(plan: dict[str,Any], records: Sequence[dict[str,Any]]) -> dict[str,Any]:
    exp={identity(r):r for r in plan["runs"]}; seen={}
    for r in records:
        validate_result_record(r); ident=identity(r)
        req(ident in exp, f"extra or cross-phase record {ident}"); req(ident not in seen, f"duplicate record {ident}")
        for k in ("candidate_manifest_sha256","public_h_sha256","calibration_source_commit","algorithm_id","phase","seed_role","seed_index","budget","solver_stratum","candidate_config_digest","calibration_module_sha256"):
            req(r[k]==exp[ident][k], f"record binding mismatch {k}")
        seen[ident]=r
    missing=set(exp)-set(seen); req(not missing, f"missing {len(missing)} expected records")
    return {"records":len(records),"threshold_fit":sum(1 for r in records if r["phase"]==FIT_PHASE),"tier_validation":sum(1 for r in records if r["phase"]==TIER_PHASE)}

def nearest_rank(vals: Sequence[int], q: float) -> int:
    req(vals, "empty percentile input"); s=sorted(vals); return s[max(1, math.ceil(q*len(s)))-1]
def lower_median(vals: Sequence[int]) -> int:
    req(vals, "empty median input"); s=sorted(vals); return s[(len(s)-1)//2]
def completed(r: dict[str,Any]) -> bool: return r.get("completed_budget") is True and r.get("resource_limit") is not True
def fit_thresholds(plan: dict[str,Any], records: Sequence[dict[str,Any]]) -> dict[str,Any]:
    out=[]; bycase={c["case_id"]:[] for c in plan["runs"]}
    for r in records: bycase.setdefault(r["case_id"],[]).append(r)
    for cid,rs in sorted(bycase.items()):
        fit=[r for r in rs if r.get("solver_stratum")==SOLVER_DISABLED and r["phase"]==FIT_PHASE and r["budget"]==BUDGETS[-1]]
        vals=[r["best_weight"] for r in fit if completed(r) and r.get("best_weight") is not None and r.get("witness_verified") is True]
        algs={r["algorithm_id"] for r in fit if completed(r) and r.get("best_weight") is not None and r.get("witness_verified") is True}
        denom=len(ALGORITHMS)*8; avail=len(vals)/denom
        W=nearest_rank(vals,.40) if avail>=.5 and len(algs)>=2 else None
        out.append({"case_id":cid,"candidate_manifest_sha256":plan["candidate_manifest_sha256"],"availability":avail,"available_records":len(vals),"denominator":denom,"contributing_algorithms":sorted(algs),"W":W,"threshold_source":"solver_disabled_nearest_rank_40pct" if W is not None else "calibration_incomplete","planted_threshold_artificial":False})
    obj={"schema":THRESHOLD_SCHEMA,"protocol_id":PROTOCOL_ID,"thresholds":out}; obj["thresholds_sha256"]=digest({k:v for k,v in obj.items() if k!="thresholds_sha256"}); return obj

def validate_tiers(plan: dict[str,Any], thresholds: dict[str,Any], records: Sequence[dict[str,Any]]) -> dict[str,Any]:
    W={t["case_id"]:t["W"] for t in thresholds["thresholds"]}; tiers=[]
    for cid in sorted(W):
        rs=[r for r in records if r["case_id"]==cid and r.get("solver_stratum")==SOLVER_DISABLED and r["phase"]==TIER_PHASE]
        if W[cid] is None: tiers.append({"case_id":cid,"tier":"calibration_incomplete","reason":"no fitted threshold"}); continue
        rates={}; rlf={}
        for b in BUDGETS:
            br=[r for r in rs if r["budget"]==b]; denom=len(ALGORITHMS)*8
            rates[str(b)]=sum(1 for r in br if r.get("threshold_hit") is True and r.get("best_weight",10**9)<=W[cid])/denom
            rlf[str(b)]=(denom-len(br)+sum(1 for r in br if not completed(r)))/denom
        maxrs=[r for r in rs if r["budget"]==BUDGETS[-1] and completed(r) and r.get("best_weight") is not None]
        weights=[r["best_weight"] for r in maxrs]; iqr=None
        if len(weights)>=8: iqr=nearest_rank(weights,.75)-nearest_rank(weights,.25)
        med_by_alg={a:lower_median([r["best_weight"] for r in maxrs if r["algorithm_id"]==a]) for a in ALGORITHMS if len([r for r in maxrs if r["algorithm_id"]==a])>=2}
        agree=any(abs(x-y)<=2 for i,x in enumerate(med_by_alg.values()) for y in list(med_by_alg.values())[i+1:])
        hi=rates[str(BUDGETS[-1])]; lo=rates[str(BUDGETS[0])]
        if iqr is None: tier="calibration_incomplete"
        elif .70 <= hi < .90 and lo < .70 and agree: tier="easy_calibrated"
        elif .35 <= hi < .70 and lo < .50 and iqr>=1 and rlf[str(BUDGETS[-1])]<.25: tier="medium_calibrated"
        elif .10 <= hi < .35 and weights and rlf[str(BUDGETS[-1])]<.50: tier="hard_calibrated"
        else: tier="calibration_incomplete"
        tiers.append({"case_id":cid,"W":W[cid],"tier":tier,"hit_rates":rates,"resource_limit_frequencies":rlf,"iqr":iqr,"algorithm_agreement_gap2":agree})
    obj={"schema":TIER_SCHEMA,"protocol_id":PROTOCOL_ID,"tiers":tiers}; obj["tiers_sha256"]=digest({k:v for k,v in obj.items() if k!="tiers_sha256"}); return obj

# deterministic toy runners for smoke; real external calibration may replace records with runner outputs.
def _toy_record(run: dict[str,Any], case: dict[str,Any], W: int|None=None) -> dict[str,Any]:
    key=digest([run["case_id"],run["algorithm_id"],run["phase"],run["seed_index"],run["budget"]]); rows,n=isd_v2.parse_h_rows(case["H_rows"])
    best=None
    for word in range(1, min(1<<n, 512)):
        if isd_v2.syndrome_is_zero(rows, word): best=word; break
    bits=isd_v2.word_to_bits(best,n) if best else None; wt=best.bit_count() if best else None
    rec={"result_schema":RESULT_SCHEMA,**run,"H_rows":case["H_rows"],"W":None if run["phase"]==FIT_PHASE else W,"completed_budget":True,"resource_limit":False,"best_candidate_bits":bits,"best_weight":wt,"witness_verified":bits is not None,"threshold_hit":False,"runtime_s":0.0,"calibration_result_sha256":""}
    if rec["W"] is not None and wt is not None: rec["threshold_hit"]=wt<=rec["W"]
    rec["calibration_result_sha256"]=digest({k:v for k,v in rec.items() if k!="calibration_result_sha256"}); return rec

def smoke(outdir: Path) -> dict[str,Any]:
    cases=[{"case_id":"cal-v2-smoke-even4","H_rows":["1111"],"public_h_sha256":isd_v2.public_h_sha256(["1111"])}]
    man={"candidate_manifest_digest":"0"*64,"records":cases}; plan={"schema":PLAN_SCHEMA,"protocol_id":PROTOCOL_ID,"candidate_manifest_sha256":"0"*64,"runs":[]}
    mods=module_digests(); src=source_commit()
    for c in cases:
        base={"case_id":c["case_id"],"public_h_sha256":c["public_h_sha256"],"candidate_manifest_sha256":"0"*64,"calibration_source_commit":src,**mods}
        for alg in ALGORITHMS:
            for seed in range(8): plan["runs"].append({**base,"solver_stratum":SOLVER_DISABLED,"algorithm_id":alg,"phase":FIT_PHASE,"seed_role":FIT_ROLE,"seed_index":seed,"budget":BUDGETS[-1]})
            for b in BUDGETS:
                for seed in range(8): plan["runs"].append({**base,"solver_stratum":SOLVER_DISABLED,"algorithm_id":alg,"phase":TIER_PHASE,"seed_role":TIER_ROLE,"seed_index":seed,"budget":b})
    recs=[_toy_record(r,cases[0],W=2) for r in plan["runs"]]
    validate_results(plan,recs); th=fit_thresholds(plan,recs); ti=validate_tiers(plan,th,recs)
    write_json(outdir/"plan.json",plan); append_jsonl(outdir/"results.jsonl",recs); write_json(outdir/"thresholds.json",th); write_json(outdir/"tiers.json",ti)
    return {"records":len(recs),"thresholds":len(th["thresholds"]),"tiers":len(ti["tiers"])}

def main(argv: Sequence[str]|None=None) -> int:
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="cmd", required=True)
    a=sub.add_parser("plan"); a.add_argument("manifest"); a.add_argument("--output",required=True)
    a=sub.add_parser("validate-results"); a.add_argument("plan"); a.add_argument("results"); a.add_argument("--summary",action="store_true")
    a=sub.add_parser("fit-thresholds"); a.add_argument("plan"); a.add_argument("results"); a.add_argument("--output",required=True)
    a=sub.add_parser("validate-tiers"); a.add_argument("plan"); a.add_argument("thresholds"); a.add_argument("results"); a.add_argument("--output",required=True)
    a=sub.add_parser("summary"); a.add_argument("tiers")
    a=sub.add_parser("smoke"); a.add_argument("--output-dir",required=True)
    a=sub.add_parser("run-shard"); a.add_argument("plan"); a.add_argument("--shard-index",type=int,required=True); a.add_argument("--shard-count",type=int,required=True); a.add_argument("--output",required=True)
    args=p.parse_args(argv)
    if args.cmd=="plan": write_json(Path(args.output), plan_from_manifest(load_candidate_manifest(Path(args.manifest)))); return 0
    if args.cmd=="validate-results": s=validate_results(read_json(Path(args.plan)), read_jsonl(Path(args.results))); print(json.dumps(s,sort_keys=True)) if args.summary else None; return 0
    if args.cmd=="fit-thresholds": write_json(Path(args.output), fit_thresholds(read_json(Path(args.plan)), read_jsonl(Path(args.results)))); return 0
    if args.cmd=="validate-tiers": write_json(Path(args.output), validate_tiers(read_json(Path(args.plan)), read_json(Path(args.thresholds)), read_jsonl(Path(args.results)))); return 0
    if args.cmd=="summary": tiers=read_json(Path(args.tiers))["tiers"]; print(json.dumps({t:sum(1 for x in tiers if x["tier"]==t) for t in sorted({x["tier"] for x in tiers})},sort_keys=True)); return 0
    if args.cmd=="smoke": print(json.dumps(smoke(Path(args.output_dir)),sort_keys=True)); return 0
    if args.cmd=="run-shard":
        plan=read_json(Path(args.plan)); gint(args.shard_index,"shard_index",0); gint(args.shard_count,"shard_count",1); out=Path(args.output); req(not out.exists(), "output exists; refusing to overwrite shard evidence")
        runs=[r for i,r in enumerate(plan["runs"]) if i%args.shard_count==args.shard_index]; append_jsonl(out, []) ; print(json.dumps({"planned_runs":len(runs),"output":str(out)},sort_keys=True)); return 0
    return 2
if __name__=="__main__":
    try: raise SystemExit(main())
    except (CalibrationV2Error, OSError, json.JSONDecodeError, corpus_v2.V2Error, isd_v2.ISDValidationError) as e:
        print(f"error: {e}", file=sys.stderr); raise SystemExit(1)
