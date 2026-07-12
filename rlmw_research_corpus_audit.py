"""Small-circuit audit for the frozen h-native-research-v1 corpus.

Standalone stdlib checker.  It performs an exact meet-in-the-middle search for
zero-syndrome supports through an audited cap of six in the public H coordinate
order.  This is correctness/replay infrastructure, not a general minimum-
distance algorithm and not a benchmark-performance claim.
"""
from __future__ import annotations

import argparse, hashlib, itertools, json, math, os, sys, tempfile
from typing import Any

import rlmw_research_corpus as corpus

AUDIT_PROTOCOL_VERSION = "h-native-research-v1-small-circuit-audit-v1"
AUDIT_ALGORITHM = "ordered_split_subset_xor_cap6_v1"
AUDIT_CAP = 6
UNKNOWN_CASE_IDS = tuple(f"hnrv1-c{i:04d}" for i in range(12,24))
EXPECTED_FINDINGS = {
    "hnrv1-c0012": ("CERTIFIED_EXACT_DISTANCE", 6, [4,14,34,62,78,80]),
    "hnrv1-c0013": ("CERTIFIED_EXACT_DISTANCE", 5, [26,37,82,97,105]),
    "hnrv1-c0020": ("CERTIFIED_EXACT_DISTANCE", 3, [72,79,136]),
    "hnrv1-c0022": ("CERTIFIED_EXACT_DISTANCE", 3, [72,79,136]),
    "hnrv1-c0023": ("CERTIFIED_EXACT_DISTANCE", 3, [80,87,144]),
}
LOWER_BOUND_CASES = tuple(f"hnrv1-c{i:04d}" for i in (14,15,16,17,18,19,21))


def fail(msg: str) -> None: raise ValueError(f"{AUDIT_PROTOCOL_VERSION}: {msg}")
def req(c: bool, msg: str) -> None:
    if not c: fail(msg)
def _strict_int(x: Any, name: str, lo: int|None=None, hi: int|None=None) -> int:
    req(isinstance(x,int) and not isinstance(x,bool), f"{name} must be an integer")
    if lo is not None: req(x >= lo, f"{name} below minimum")
    if hi is not None: req(x <= hi, f"{name} above maximum")
    return x
def canonical_json_bytes(o: Any) -> bytes:
    return json.dumps(o, sort_keys=True, separators=(",",":"), ensure_ascii=True, allow_nan=False).encode()
def sha(o: Any) -> str: return hashlib.sha256(canonical_json_bytes(o)).hexdigest()
def file_sha(path: str) -> str:
    h=hashlib.sha256();
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1<<20), b''): h.update(b)
    return h.hexdigest()
def load_manifest(path="h_native_research_v1_manifest.json") -> dict:
    with open(path, encoding="utf-8") as f: m=json.load(f)
    req(m["protocol_version"] == corpus.VERSION, "wrong corpus protocol")
    req(m["manifest_sha256"] == corpus.FROZEN_MANIFEST_SHA256, "wrong corpus digest")
    return m
def case_map(m: dict) -> dict[str,dict]:
    out={}
    for c in m["cases"]:
        cid=c["case_id"]; req(cid not in out, f"duplicate case {cid}"); out[cid]=c
    return out
def parse_h_rows(rows: list[str]) -> tuple[list[int],int,int]:
    mat=corpus.rows_to_mat(rows); r=len(mat); n=len(mat[0])
    cols=[]
    for j in range(n):
        w=0
        for i,row in enumerate(mat):
            if row[j]: w ^= 1<<i
        cols.append(w)
    return cols,r,n
def syndrome(cols: list[int], supp: tuple[int,...]|list[int]) -> int:
    x=0
    for j in supp: x ^= cols[j]
    return x
def verify_support(rows: list[str], support: list[int]) -> None:
    cols,r,n=parse_h_rows(rows)
    req(support and support == sorted(support), "support must be nonempty sorted")
    req(len(set(support)) == len(support), "support has duplicates")
    req(all(isinstance(j,int) and not isinstance(j,bool) and 0<=j<n for j in support), "support index out of range")
    req(syndrome(cols, support) == 0, "support is not a zero syndrome")

def representative_better(new: tuple[int,...], old: tuple[int,...]|None) -> bool:
    """Deterministic R representative: maximize min(R), then lexicographically largest."""
    return old is None or (new[0], new) > (old[0], old)

def preflight(n:int, r:int, max_weight:int) -> dict:
    max_weight=_strict_int(max_weight,"max_weight",0,AUDIT_CAP)
    weights=[]
    for w in range(1,max_weight+1):
        a=w//2; b=w-a
        weights.append({"weight":w,"left_subsets":math.comb(n,a),"right_subsets":math.comb(n,b),"peak_map_bound":min(math.comb(n,b), 1<<r)})
    return {"n":n,"rank_rows":r,"max_weight":max_weight,"weights":weights}

def ordered_split_search(rows:list[str], max_weight:int=6, interrupt_after_subsets:int|None=None) -> dict:
    max_weight=_strict_int(max_weight,"max_weight",0,AUDIT_CAP)
    if interrupt_after_subsets is not None: interrupt_after_subsets=_strict_int(interrupt_after_subsets,"interrupt_after_subsets",0,None)
    cols,r,n=parse_h_rows(rows); per=[]; processed=0; last=0
    for w in range(1,max_weight+1):
        a=w//2; b=w-a; mp={}; rc=lc=0; complete=False
        for R in itertools.combinations(range(n), b):
            rc+=1; processed+=1
            if interrupt_after_subsets is not None and processed > interrupt_after_subsets:
                per.append({"weight":w,"a":a,"b":b,"right_subsets":rc,"left_subsets":0,"map_size":len(mp),"completed":False})
                return {"status":"RESOURCE_LIMIT","last_completed_weight":last,"certified_bound":last+1 if last else 1,"per_weight":per,"failure":"interrupted"}
            s=syndrome(cols,R); old=mp.get(s)
            if representative_better(R, old): mp[s]=R
        for L in itertools.combinations(range(n), a):
            lc+=1; processed+=1
            if interrupt_after_subsets is not None and processed > interrupt_after_subsets:
                per.append({"weight":w,"a":a,"b":b,"right_subsets":rc,"left_subsets":lc,"map_size":len(mp),"completed":False})
                return {"status":"RESOURCE_LIMIT","last_completed_weight":last,"certified_bound":last+1 if last else 1,"per_weight":per,"failure":"interrupted"}
            R=mp.get(syndrome(cols,L))
            if R is not None and (a==0 or min(R) > max(L)):
                supp=sorted((*L,*R)); verify_support(rows,supp)
                per.append({"weight":w,"a":a,"b":b,"right_subsets":rc,"left_subsets":lc,"map_size":len(mp),"completed":True})
                return {"status":"CERTIFIED_EXACT_DISTANCE","last_completed_weight":w,"certified_bound":w,"witness_support":supp,"witness_weight":w,"witness_sha256":sha({"support":supp}),"per_weight":per}
        complete=True; last=w
        per.append({"weight":w,"a":a,"b":b,"right_subsets":rc,"left_subsets":lc,"map_size":len(mp),"completed":complete})
    return {"status":"CERTIFIED_LOWER_BOUND","last_completed_weight":max_weight,"certified_bound":max_weight+1,"per_weight":per}

def audit_case(c:dict, max_weight:int=6, interrupt_after_subsets:int|None=None) -> dict:
    rows=c["H_rows"]; h=corpus.sha({"H_rows":rows}); cfg={"algorithm":AUDIT_ALGORITHM,"max_weight":max_weight}
    res=ordered_split_search(rows,max_weight,interrupt_after_subsets)
    rec={"audit_protocol_version":AUDIT_PROTOCOL_VERSION,"corpus_protocol_version":corpus.VERSION,"manifest_sha256":corpus.FROZEN_MANIFEST_SHA256,"case_id":c["case_id"],"raw_H_sha256":h,"audit_algorithm":AUDIT_ALGORITHM,"audit_config_sha256":sha(cfg),"requested_cap":max_weight, **res}
    rec["record_sha256"] = sha({k:v for k,v in rec.items() if k != "record_sha256"})
    return rec

def validate_record(rec:dict, m:dict, replay_exclusion:bool=False) -> None:
    req(rec.get("record_sha256") == sha({k:v for k,v in rec.items() if k != "record_sha256"}), "noncanonical/tampered record")
    cm=case_map(m); cid=rec.get("case_id"); req(cid in cm, "missing/unknown case")
    c=cm[cid]; req(rec["raw_H_sha256"] == c["raw_H_sha256"], "H hash mismatch")
    cap=_strict_int(rec["requested_cap"],"requested_cap",0,AUDIT_CAP)
    req(rec["audit_config_sha256"] == sha({"algorithm":AUDIT_ALGORITHM,"max_weight":cap}), "config hash mismatch")
    st=rec["status"]; req(st in {"CERTIFIED_EXACT_DISTANCE","CERTIFIED_LOWER_BOUND","RESOURCE_LIMIT"}, "bad status")
    if "witness_support" in rec: verify_support(c["H_rows"], rec["witness_support"])
    if replay_exclusion:
        rr=audit_case(c, cap)
        for k in ("status","last_completed_weight","certified_bound","witness_support","witness_weight","witness_sha256","per_weight"):
            req(rec.get(k)==rr.get(k), f"replay mismatch {k}")

def dense_xorshift_relation_holds(n:int,r:int,seed:int)->bool:
    rows=corpus.dense_random_H(n,r,seed); cols,_,_=parse_h_rows(rows)
    if n <= r+64 or r+7 >= n: return True
    return cols[r] ^ cols[r+7] ^ cols[r+64] == 0

def self_test() -> None:
    for n,r in [(80,8),(96,48),(144,72)]:
        for seed in [1,7707,9909,3030]: req(dense_xorshift_relation_holds(n,r,seed), "xorshift relation failed")
    m=load_manifest(); cm=case_map(m)
    for cid,(st,w,supp) in EXPECTED_FINDINGS.items():
        rec=audit_case(cm[cid],6); req(rec["status"]==st and rec.get("witness_weight")==w and rec.get("witness_support")==supp, f"finding mismatch {cid}")
    for cid in LOWER_BOUND_CASES:
        rec=audit_case(cm[cid],6); req(rec["status"]=="CERTIFIED_LOWER_BOUND" and rec["certified_bound"]==7, f"lower-bound mismatch {cid}")

def write_audit(max_weight:int, outdir:str) -> str:
    m=load_manifest(); cm=case_map(m); os.makedirs(outdir, exist_ok=True); path=os.path.join(outdir,"rlmw_research_corpus_audit.jsonl")
    with open(path,"w",encoding="utf-8") as f:
        for cid in UNKNOWN_CASE_IDS:
            f.write(json.dumps(audit_case(cm[cid],max_weight), sort_keys=True, separators=(",",":"))+"\n")
    return path

def main(argv=None)->int:
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    a=sub.add_parser("audit"); a.add_argument("--max-weight",type=int,required=True); a.add_argument("--output-dir",required=True)
    v=sub.add_parser("validate"); v.add_argument("path"); v.add_argument("--replay-exclusion",action="store_true")
    s=sub.add_parser("summary"); s.add_argument("path", nargs="?")
    sub.add_parser("self-test")
    ns=p.parse_args(argv)
    if ns.cmd=="list": print("\n".join(UNKNOWN_CASE_IDS)); return 0
    if ns.cmd=="self-test": self_test(); print("self-test passed"); return 0
    if ns.cmd=="audit": print(write_audit(ns.max_weight, ns.output_dir)); return 0
    if ns.cmd in {"validate","summary"}:
        m=load_manifest(); counts={}
        with open(ns.path,encoding="utf-8") as f: recs=[json.loads(l) for l in f if l.strip()]
        req(len(recs)==len(UNKNOWN_CASE_IDS), "wrong record count")
        req(sorted(r["case_id"] for r in recs)==list(UNKNOWN_CASE_IDS), "missing/duplicate cases")
        for r in recs:
            if ns.cmd=="validate": validate_record(r,m,ns.replay_exclusion)
            counts[r["status"]]=counts.get(r["status"],0)+1
        print(json.dumps({"records":len(recs),"statuses":counts}, sort_keys=True)); return 0
    return 2
if __name__ == "__main__": sys.exit(main())
