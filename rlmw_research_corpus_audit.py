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

AUDIT_PROTOCOL_VERSION = "h-native-research-v1-small-circuit-audit-v2"
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

def gf2_rank_rows(rows:list[str]) -> int:
    vals=[]
    for row in rows:
        vals.append(int(row,2) if row else 0)
    rank=0
    vals=[v for v in vals if v]
    while vals:
        pivot=max(vals)
        bit=pivot.bit_length()-1
        rank += 1
        vals=[v ^ pivot if ((v>>bit)&1) else v for v in vals if (v ^ pivot if ((v>>bit)&1) else v)]
    return rank

def preflight(n:int, r:int, max_weight:int, rank:int|None=None) -> dict:
    max_weight=_strict_int(max_weight,"max_weight",0,AUDIT_CAP)
    rank = r if rank is None else _strict_int(rank,"rank",0,r)
    weights=[]
    for w in range(1,max_weight+1):
        a=w//2; b=w-a
        weights.append({"weight":w,"expected_left_subsets":math.comb(n,a),"expected_right_subsets":math.comb(n,b),"peak_map_bound":min(math.comb(n,b), 1<<rank)})
    return {"n":n,"row_count":r,"rank_H":rank,"max_weight":max_weight,"weights":weights}

def _weight_record(w:int,a:int,b:int,n:int,rc:int,lc:int,map_size:int,outcome:str)->dict:
    return {"weight":w,"a":a,"b":b,
            "expected_right_subsets":math.comb(n,b),"examined_right_subsets":rc,
            "expected_left_subsets":math.comb(n,a),"examined_left_subsets":lc,
            "syndrome_map_size":map_size,"outcome":outcome}

def ordered_split_search(rows:list[str], max_weight:int=6, interrupt_after_subsets:int|None=None) -> dict:
    max_weight=_strict_int(max_weight,"max_weight",0,AUDIT_CAP)
    if interrupt_after_subsets is not None: interrupt_after_subsets=_strict_int(interrupt_after_subsets,"interrupt_after_subsets",0,None)
    cols,r,n=parse_h_rows(rows); per=[]; processed=0; last=0
    for w in range(1,max_weight+1):
        a=w//2; b=w-a; mp={}; rc=lc=0
        for R in itertools.combinations(range(n), b):
            if interrupt_after_subsets is not None and processed >= interrupt_after_subsets:
                per.append(_weight_record(w,a,b,n,rc,lc,len(mp),"INTERRUPTED"))
                return {"status":"RESOURCE_LIMIT","last_completed_weight":last,"interrupted_weight":w,"certified_bound":last+1,"per_weight":per,"failure":"interrupted"}
            s=syndrome(cols,R); old=mp.get(s)
            if representative_better(R, old): mp[s]=R
            rc+=1; processed+=1
        for L in itertools.combinations(range(n), a):
            if interrupt_after_subsets is not None and processed >= interrupt_after_subsets:
                per.append(_weight_record(w,a,b,n,rc,lc,len(mp),"INTERRUPTED"))
                return {"status":"RESOURCE_LIMIT","last_completed_weight":last,"interrupted_weight":w,"certified_bound":last+1,"per_weight":per,"failure":"interrupted"}
            R=mp.get(syndrome(cols,L)); lc+=1; processed+=1
            if R is not None and (a==0 or min(R) > max(L)):
                supp=sorted((*L,*R)); verify_support(rows,supp)
                per.append(_weight_record(w,a,b,n,rc,lc,len(mp),"WITNESS_FOUND"))
                return {"status":"CERTIFIED_EXACT_DISTANCE","last_completed_weight":w,"certified_bound":w,"witness_support":supp,"witness_weight":w,"witness_sha256":sha({"support":supp}),"per_weight":per}
        last=w
        per.append(_weight_record(w,a,b,n,rc,lc,len(mp),"EXHAUSTED_NO_WITNESS"))
    return {"status":"CERTIFIED_LOWER_BOUND","last_completed_weight":max_weight,"certified_bound":max_weight+1,"per_weight":per}

def audit_case(c:dict, max_weight:int=6, interrupt_after_subsets:int|None=None) -> dict:
    rows=c["H_rows"]; h=corpus.sha({"H_rows":rows}); cfg={"algorithm":AUDIT_ALGORITHM,"max_weight":max_weight,"subset_visit_limit":interrupt_after_subsets}
    res=ordered_split_search(rows,max_weight,interrupt_after_subsets)
    rec={"audit_protocol_version":AUDIT_PROTOCOL_VERSION,"corpus_protocol_version":corpus.VERSION,"manifest_sha256":corpus.FROZEN_MANIFEST_SHA256,"case_id":c["case_id"],"raw_H_sha256":h,"audit_algorithm":AUDIT_ALGORITHM,"audit_config_sha256":sha(cfg),"requested_cap":max_weight,"audit_config":cfg,"preflight":preflight(len(rows[0]),len(rows),max_weight,gf2_rank_rows(rows)), **res}
    rec["record_sha256"] = sha({k:v for k,v in rec.items() if k != "record_sha256"})
    return rec

def _expect_keys(rec:dict, required:set[str], optional:set[str]=frozenset())->None:
    keys=set(rec)
    req(keys == required | optional, f"schema keys mismatch missing={sorted((required|optional)-keys)} unexpected={sorted(keys-(required|optional))}")

def _validate_per_weight(per:list, cap:int, status:str, n:int)->None:
    req(isinstance(per,list), "per_weight must be list")
    seen=[]
    for i,wr in enumerate(per,1):
        _expect_keys(wr,{"weight","a","b","expected_right_subsets","examined_right_subsets","expected_left_subsets","examined_left_subsets","syndrome_map_size","outcome"})
        w=_strict_int(wr["weight"],"weight",1,AUDIT_CAP); req(w==i,"per_weight must be consecutive")
        a=_strict_int(wr["a"],"a",0,w); b=_strict_int(wr["b"],"b",0,w); req(a==w//2 and b==w-a,"bad split")
        er=_strict_int(wr["expected_right_subsets"],"expected_right_subsets",0,None); el=_strict_int(wr["expected_left_subsets"],"expected_left_subsets",0,None)
        req(er==math.comb(n,b) and el==math.comb(n,a),"bad expected subset count")
        rr=_strict_int(wr["examined_right_subsets"],"examined_right_subsets",0,er); ll=_strict_int(wr["examined_left_subsets"],"examined_left_subsets",0,el)
        _strict_int(wr["syndrome_map_size"],"syndrome_map_size",0,er)
        req(wr["outcome"] in {"EXHAUSTED_NO_WITNESS","WITNESS_FOUND","INTERRUPTED"},"bad outcome")
        if wr["outcome"]=="EXHAUSTED_NO_WITNESS": req(rr==er and ll==el,"exhausted counters incomplete")
        if wr["outcome"]=="WITNESS_FOUND": req(rr==er and 1<=ll<=el,"witness counters invalid")
        if wr["outcome"]=="INTERRUPTED": req(rr<er or ll<el,"interrupted after full exhaustion")
        seen.append(wr["outcome"])
    if status=="CERTIFIED_EXACT_DISTANCE": req(seen and seen[-1]=="WITNESS_FOUND" and all(x=="EXHAUSTED_NO_WITNESS" for x in seen[:-1]),"bad exact outcomes")
    if status=="CERTIFIED_LOWER_BOUND": req(len(seen)==cap and all(x=="EXHAUSTED_NO_WITNESS" for x in seen),"bad lower-bound outcomes")
    if status=="RESOURCE_LIMIT": req(seen and seen[-1]=="INTERRUPTED" and all(x=="EXHAUSTED_NO_WITNESS" for x in seen[:-1]),"bad resource outcomes")

def validate_record(rec:dict, m:dict, replay_exclusion:bool=False) -> None:
    req(isinstance(rec,dict), "record must be object")
    req(rec.get("audit_protocol_version")==AUDIT_PROTOCOL_VERSION,"audit protocol mismatch")
    req(rec.get("corpus_protocol_version")==corpus.VERSION,"corpus protocol mismatch")
    req(rec.get("manifest_sha256")==corpus.FROZEN_MANIFEST_SHA256,"manifest digest mismatch")
    req(rec.get("audit_algorithm")==AUDIT_ALGORITHM,"audit algorithm mismatch")
    req(rec.get("record_sha256") == sha({k:v for k,v in rec.items() if k != "record_sha256"}), "noncanonical/tampered record")
    cm=case_map(m); cid=rec.get("case_id"); req(cid in UNKNOWN_CASE_IDS and cid in cm, "missing/unknown case")
    c=cm[cid]; req(rec.get("raw_H_sha256") == c["raw_H_sha256"], "H hash mismatch")
    cap=_strict_int(rec.get("requested_cap"),"requested_cap",0,AUDIT_CAP)
    cfg=rec.get("audit_config"); req(isinstance(cfg,dict), "config must be object")
    req(cfg.get("algorithm")==AUDIT_ALGORITHM and cfg.get("max_weight")==cap, "config mismatch")
    lim=cfg.get("subset_visit_limit")
    if lim is not None: _strict_int(lim,"subset_visit_limit",0,None)
    req(set(cfg)=={"algorithm","max_weight","subset_visit_limit"}, "config keys mismatch")
    req(rec.get("audit_config_sha256") == sha(cfg), "config hash mismatch")
    st=rec.get("status"); req(st in {"CERTIFIED_EXACT_DISTANCE","CERTIFIED_LOWER_BOUND","RESOURCE_LIMIT"}, "bad status")
    base={"audit_protocol_version","corpus_protocol_version","manifest_sha256","case_id","raw_H_sha256","audit_algorithm","audit_config_sha256","audit_config","preflight","requested_cap","status","last_completed_weight","certified_bound","per_weight","record_sha256"}
    if st=="CERTIFIED_EXACT_DISTANCE": _expect_keys(rec,base|{"witness_support","witness_weight","witness_sha256"})
    elif st=="CERTIFIED_LOWER_BOUND": _expect_keys(rec,base)
    else: _expect_keys(rec,base|{"interrupted_weight","failure"})
    rows=c["H_rows"]; n=len(rows[0])
    req(rec["preflight"]==preflight(n,len(rows),cap,gf2_rank_rows(rows)),"preflight mismatch")
    _validate_per_weight(rec["per_weight"],cap,st,n)
    last=_strict_int(rec["last_completed_weight"],"last_completed_weight",0,cap)
    cb=_strict_int(rec["certified_bound"],"certified_bound",1,AUDIT_CAP+1)
    if st=="CERTIFIED_EXACT_DISTANCE":
        supp=rec["witness_support"]; verify_support(rows, supp)
        ww=_strict_int(rec["witness_weight"],"witness_weight",1,AUDIT_CAP)
        req(ww==len(supp)==cb==rec["per_weight"][-1]["weight"]==last,"witness/bound mismatch")
        req(rec["witness_sha256"]==sha({"support":supp}),"witness hash mismatch")
    elif st=="CERTIFIED_LOWER_BOUND": req(last==cap and cb==cap+1,"lower bound mismatch")
    else:
        req(rec["failure"]=="interrupted","bad failure")
        iw=_strict_int(rec["interrupted_weight"],"interrupted_weight",1,cap); req(iw==len(rec["per_weight"]) and last==iw-1 and cb==last+1,"resource bound mismatch")
    if replay_exclusion:
        rr=audit_case(c, cap, cfg.get("subset_visit_limit"))
        for k in ("status","last_completed_weight","certified_bound","witness_support","witness_weight","witness_sha256","per_weight"):
            req(rec.get(k)==rr.get(k), f"replay mismatch {k}")

def _no_dupe_object_pairs(pairs):
    out={}
    for k,v in pairs:
        if k in out: fail(f"duplicate JSON key {k}")
        out[k]=v
    return out

def read_canonical_jsonl(path:str, replay_exclusion:bool=False)->list[dict]:
    m=load_manifest(); recs=[]; seen=set()
    with open(path,'rb') as f: data=f.read()
    req(data.endswith(b'\n'),"JSONL must end with newline")
    for lineno, raw in enumerate(data.splitlines(),1):
        req(raw, f"blank line {lineno}"); req(raw.strip()==raw, f"line {lineno} has surrounding whitespace")
        try: text=raw.decode('utf-8')
        except UnicodeDecodeError: fail(f"line {lineno} malformed UTF-8")
        try: rec=json.loads(text, object_pairs_hook=_no_dupe_object_pairs, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
        except Exception as e: fail(f"line {lineno} malformed JSON: {e}")
        req(isinstance(rec,dict), f"line {lineno} not object")
        req(raw==canonical_json_bytes(rec), f"line {lineno} is not canonical")
        validate_record(rec,m,replay_exclusion)
        cid=rec['case_id']; req(cid not in seen, f"duplicate case {cid}"); seen.add(cid); recs.append(rec)
    req(tuple(sorted(seen))==UNKNOWN_CASE_IDS,"missing or unexpected case IDs")
    return recs

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

def write_audit(max_weight:int, outdir:str, subset_visit_limit:int|None=None) -> str:
    m=load_manifest(); cm=case_map(m); os.makedirs(outdir, exist_ok=True); path=os.path.join(outdir,"rlmw_research_corpus_audit.jsonl")
    with open(path,"w",encoding="utf-8") as f:
        for cid in UNKNOWN_CASE_IDS:
            f.write(canonical_json_bytes(audit_case(cm[cid],max_weight,subset_visit_limit)).decode()+"\n")
    return path

def main(argv=None)->int:
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    a=sub.add_parser("audit"); a.add_argument("--max-weight",type=int,required=True); a.add_argument("--output-dir",required=True); a.add_argument("--subset-visit-limit",type=int)
    v=sub.add_parser("validate"); v.add_argument("path"); v.add_argument("--replay-exclusion",action="store_true")
    s=sub.add_parser("summary"); s.add_argument("path"); s.add_argument("--replay-exclusion",action="store_true")
    sub.add_parser("self-test")
    ns=p.parse_args(argv)
    if ns.cmd=="list": print("\n".join(UNKNOWN_CASE_IDS)); return 0
    if ns.cmd=="self-test": self_test(); print("self-test passed"); return 0
    if ns.cmd=="audit": print(write_audit(ns.max_weight, ns.output_dir, ns.subset_visit_limit)); return 0
    if ns.cmd in {"validate","summary"}:
        counts={}; recs=read_canonical_jsonl(ns.path, getattr(ns,"replay_exclusion",False))
        for r in recs: counts[r["status"]]=counts.get(r["status"],0)+1
        print(json.dumps({"records":len(recs),"statuses":counts}, sort_keys=True)); return 0
    return 2
if __name__ == "__main__":
    try: sys.exit(main())
    except ValueError as e:
        print(str(e), file=sys.stderr); sys.exit(1)
