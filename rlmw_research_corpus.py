"""Deterministic public-H research corpus manifest for RLMW.

This module intentionally contains only corpus construction, manifest validation, and
solver/evaluator payload helpers. It makes no benchmark-performance claims.
"""
from __future__ import annotations

import argparse, hashlib, itertools, json
from dataclasses import dataclass
from typing import Any

VERSION = "h-native-research-v1"
MAX_SMOKE_CASES = 9
SPLITS = ("train", "validation", "test")
SOLVER_KEYS = ("protocol_version", "manifest_sha256", "case_id", "H_rows", "H_sha256", "W", "budget_run")
EVALUATOR_ONLY_KEYS = {"family", "provenance", "construction", "split", "group_id", "base_group_id", "rank", "k", "label", "rowspace_sha256", "raw_H_sha256"}


def fail(msg: str) -> None:
    raise ValueError(f"{VERSION} validation error: {msg}")


def require(cond: bool, msg: str) -> None:
    if not cond:
        fail(msg)


def canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def sha(obj: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(obj)).hexdigest()


def rows_to_mat(rows: list[str]) -> list[list[int]]:
    require(rows and all(isinstance(r, str) for r in rows), "H_rows must be nonempty strings")
    n = len(rows[0]); require(n > 0, "H row width must be positive")
    out=[]
    for r in rows:
        require(len(r) == n and set(r) <= {"0", "1"}, "H_rows must be rectangular binary strings")
        out.append([1 if c == "1" else 0 for c in r])
    return out


def mat_to_rows(M: list[list[int]]) -> list[str]:
    return ["".join(str(int(x) & 1) for x in row) for row in M]


def gf2_rank(M: list[list[int]]) -> int:
    A=[row[:] for row in M]; m=len(A); n=len(A[0]) if m else 0; r=0
    for c in range(n):
        piv=next((i for i in range(r,m) if A[i][c]), None)
        if piv is None: continue
        A[r],A[piv]=A[piv],A[r]
        for i in range(m):
            if i != r and A[i][c]:
                A[i]=[x^y for x,y in zip(A[i],A[r])]
        r += 1
        if r == m: break
    return r


def rref_rows(rows: list[str]) -> list[str]:
    A=rows_to_mat(rows); m=len(A); n=len(A[0]); r=0
    for c in range(n):
        piv=next((i for i in range(r,m) if A[i][c]), None)
        if piv is None: continue
        A[r],A[piv]=A[piv],A[r]
        for i in range(m):
            if i != r and A[i][c]: A[i]=[x^y for x,y in zip(A[i],A[r])]
        r += 1
        if r == m: break
    return [s for s in mat_to_rows(A) if "1" in s]


def xorshift64(seed: int):
    x = seed & ((1<<64)-1) or 0x9E3779B97F4A7C15
    while True:
        x ^= (x << 13) & ((1<<64)-1); x ^= x >> 7; x ^= (x << 17) & ((1<<64)-1)
        yield x & ((1<<64)-1)


def hamming_H(m: int) -> list[str]:
    return ["".join("1" if (col >> bit) & 1 else "0" for col in range(1, 1<<m)) for bit in range(m)]


def extended_hamming_H(m: int) -> list[str]:
    base = [r + "0" for r in hamming_H(m)]
    return base + ["1" * (1 << m)]


def rm1_parity_H(m: int) -> list[str]:
    # RM(1,m) has generator rows [1; coordinate bits]. The evaluations of all
    # square-free monomials of degree >= 2 form a deterministic dual basis for
    # this first-order Reed--Muller control representation.
    n = 1 << m
    rows = []
    for degree in range(2, m + 1):
        for bits in itertools.combinations(range(m), degree):
            row = []
            for j in range(n):
                val = 1
                for bit in bits:
                    val &= (j >> bit) & 1
                row.append(val)
            rows.append(row)
    require(len(rows) == n - (m + 1), "unexpected RM(1,m) dual row count")
    require(gf2_rank(rows) == len(rows), "RM(1,m) dual rows are not independent")
    return mat_to_rows(rows)


def sparse_ldpc_H(n: int, r: int, col_w: int, seed: int) -> list[str]:
    gen=xorshift64(seed); rows=[[0]*n for _ in range(r)]
    for j in range(n):
        chosen=set()
        while len(chosen) < col_w:
            chosen.add(next(gen) % r)
        for i in chosen: rows[i][j]=1
    # deterministic row perturbation to avoid zero/repeated weak rows
    for i in range(r): rows[i][(next(gen)+i) % n] ^= 1
    return mat_to_rows(rows)


def dense_random_H(n: int, r: int, seed: int) -> list[str]:
    require(r <= n, "dense full-rank ensemble requires rows <= columns")
    gen=xorshift64(seed)
    rows=[]
    for i in range(r):
        row=[0]*n
        row[i]=1  # deterministic pivot guarantees full row rank.
        word=0
        for b in range(r, n):
            if (b-r) % 64 == 0:
                word = next(gen)
            row[b] = (word >> ((b-r) % 64)) & 1
        rows.append(row)
    require(gf2_rank(rows) == r, "constructed dense matrix is not full rank")
    return mat_to_rows(rows)

def first_kernel_upper_bound(rows: list[str], max_cols: int = 14) -> int | None:
    H=rows_to_mat(rows); n=len(H[0]); limit=min(n, max_cols)
    # bounded deterministic search only for a verified upper bound, not exact distance.
    col_words=[]
    for j in range(n):
        w=0
        for i,row in enumerate(H):
            if row[j]: w ^= 1<<i
        col_words.append(w)
    seen={0:0}
    for mask in range(1, 1<<limit):
        lsb=mask & -mask; j=lsb.bit_length()-1; prev=mask^lsb
        acc=seen[prev]^col_words[j]; seen[mask]=acc
        if acc == 0: return mask.bit_count()
    return None


def case(cid: int, split: str, family: str, rows: list[str], W: int, construction: dict, label: dict, subset: list[str]) -> dict:
    rank=gf2_rank(rows_to_mat(rows)); n=len(rows[0]); k=n-rank
    prefix=f"hnrv1-{split[0]}-{family.replace('_','-')}"
    return {"case_id": f"hnrv1-c{cid:04d}", "group_id": f"{prefix}-g{cid:04d}", "base_group_id": f"{prefix}-b{cid:04d}",
            "split": split, "family": family, "n": n, "rank": rank, "k": k, "H_rows": rows,
            "raw_H_sha256": sha({"H_rows": rows}), "rowspace_sha256": sha({"rref_rows": rref_rows(rows)}),
            "threshold": {"W": W, "relation": "at_most"}, "label": label, "provenance": construction.get("provenance"),
            "construction": construction, "subset": subset}


def build_manifest() -> dict:
    specs=[]; cid=1
    for split, ms in zip(SPLITS, [(6,7),(8,),(9,)]):
        for m in ms:
            rows=hamming_H(m); specs.append(case(cid, split, "hamming", rows, 3, {"method":"all_nonzero_binary_columns", "m":m, "provenance":"Hamming 1950"}, {"kind":"certified_distance", "distance":3, "certificate_method":"standard_hamming_code_theorem"}, ["smoke" if m<=7 else "full", "full"])); cid+=1
            rows=extended_hamming_H(m); specs.append(case(cid, split, "extended_hamming", rows, 4, {"method":"hamming_plus_overall_parity", "m":m, "provenance":"Hamming 1950; extended parity-check construction"}, {"kind":"certified_distance", "distance":4, "certificate_method":"standard_extended_hamming_code_theorem"}, ["full"])); cid+=1
    for split,m in zip(SPLITS,[5,6,7]):
        rows=rm1_parity_H(m); specs.append(case(cid, split, "reed_muller_rm1_control", rows, 1<<(m-1), {"method":"orthogonal_complement_of_RM(1,m)_generator", "m":m, "provenance":"Muller 1954; Reed 1954"}, {"kind":"certified_distance", "distance":1<<(m-1), "certificate_method":"standard_RM(1,m)_distance_theorem"}, ["smoke" if m==5 else "full", "full"])); cid+=1
    ens=[("train",96,48,3,1101),("validation",128,64,3,2202),("test",144,72,4,3303),("train",112,56,4,4404),("validation",144,72,4,5505),("test",160,80,4,6606)]
    for split,n,r,cw,seed in ens:
        rows=sparse_ldpc_H(n,r,cw,seed); ub=first_kernel_upper_bound(rows)
        label={"kind":"unknown_distance_threshold_challenge", "known_lower_bound":1, "verified_upper_bound":ub, "upper_bound_witness_exposed":False, "certificate_method":None}
        specs.append(case(cid,split,"sparse_ldpc_style",rows,max(3, (ub or 20)-1), {"method":"deterministic_regular_sparse_parity_check", "n":n,"rows":r,"column_weight":cw,"seed":seed,"provenance":"Gallager 1962 style sparse parity-check ensemble"}, label, ["smoke" if n<=128 else "full","full"])); cid+=1
    ens=[("train",96,48,7707),("validation",128,64,8808),("test",144,72,9909),("train",112,56,1010),("validation",144,72,2020),("test",160,80,3030)]
    for split,n,r,seed in ens:
        rows=dense_random_H(n,r,seed); ub=first_kernel_upper_bound(rows)
        label={"kind":"unknown_distance_threshold_challenge", "known_lower_bound":1, "verified_upper_bound":ub, "upper_bound_witness_exposed":False, "certificate_method":None}
        specs.append(case(cid,split,"dense_random_full_rank",rows,max(4, (ub or 24)-1), {"method":"deterministic_dense_full_rank_binary_matrix", "n":n,"rows":r,"seed":seed,"provenance":"MacWilliams-Sloane random linear code control ensemble"}, label, ["smoke" if n<=128 else "full","full"])); cid+=1
    body={"protocol_version":VERSION,"manifest_id":VERSION,"description":"Research-scale public-H binary-code corpus specification; no benchmark conclusions.","cases":specs,
          "budget_profiles":{"smoke":{"max_cases":MAX_SMOKE_CASES,"purpose":"CI/Colab bounded manifest and loader check only"},"full":{"max_cases":len(specs),"purpose":"research corpus enumeration; not run in CI"}},
          "references":["R. W. Hamming, Error detecting and error correcting codes, Bell System Technical Journal, 1950.","D. E. Muller, Application of Boolean algebra to switching circuit design and to error detection, IRE Transactions, 1954.","I. S. Reed, A class of multiple-error-correcting codes and the decoding scheme, IRE Transactions, 1954.","R. G. Gallager, Low-density parity-check codes, IRE Transactions, 1962.","F. J. MacWilliams and N. J. A. Sloane, The Theory of Error-Correcting Codes, 1977."]}
    body["manifest_sha256"] = sha({k:v for k,v in body.items() if k != "manifest_sha256"})
    return body


def solver_payload(manifest: dict, case_id: str, budget_run: dict | None = None) -> dict:
    c=next((x for x in manifest["cases"] if x["case_id"]==case_id), None); require(c is not None, "unknown case_id")
    return {"protocol_version": manifest["protocol_version"], "manifest_sha256": manifest["manifest_sha256"], "case_id": c["case_id"], "H_rows": c["H_rows"], "H_sha256": c["raw_H_sha256"], "W": c["threshold"]["W"], "budget_run": budget_run or {"profile":"smoke","seed":0}}


def validate_manifest(manifest: dict, *, smoke_only: bool=False) -> dict:
    require(manifest.get("protocol_version") == VERSION, "wrong protocol_version")
    got=manifest.get("manifest_sha256"); require(isinstance(got,str) and len(got)==64, "missing manifest digest")
    require(got == sha({k:v for k,v in manifest.items() if k != "manifest_sha256"}), "manifest digest mismatch")
    seen_ids=set(); seen_rowspace={s:set() for s in SPLITS}; groups={s:set() for s in SPLITS}; counts={s:0 for s in SPLITS}; smoke=0
    for c in manifest["cases"]:
        require(c["case_id"] not in seen_ids, "duplicate case_id"); seen_ids.add(c["case_id"])
        require(c["split"] in SPLITS, "bad split"); counts[c["split"]]+=1
        rows=c["H_rows"]; M=rows_to_mat(rows); n=len(rows[0]); rank=gf2_rank(M)
        require(c["n"]==n and c["rank"]==rank and c["k"]==n-rank and c["n"] > 12, "dimension/rank invalid or not beyond tiny oracle")
        require(c["raw_H_sha256"] == sha({"H_rows": rows}), "raw H hash mismatch")
        rh=sha({"rref_rows": rref_rows(rows)}); require(c["rowspace_sha256"] == rh, "rowspace hash mismatch")
        require(c["group_id"] not in groups[c["split"]] and c["base_group_id"] not in groups[c["split"]], "duplicate group in split")
        groups[c["split"]].update([c["group_id"], c["base_group_id"]]); seen_rowspace[c["split"]].add(rh)
        W=c["threshold"]["W"]; require(isinstance(W,int) and not isinstance(W,bool) and 0 < W <= n, "bad threshold")
        lab=c["label"]; require(lab["kind"] in {"certified_distance","unknown_distance_threshold_challenge"}, "bad label kind")
        if lab["kind"] == "certified_distance": require(isinstance(lab.get("distance"),int) and lab["distance"] > 0 and lab.get("certificate_method"), "bad certified label")
        else: require(lab.get("distance") is None and lab.get("known_lower_bound",0) >= 1 and lab.get("upper_bound_witness_exposed") is False, "unknown label leaks or implies exact distance")
        p=solver_payload(manifest,c["case_id"]); require(tuple(p.keys()) == SOLVER_KEYS and not (set(p) & EVALUATOR_ONLY_KEYS), "solver payload leakage")
        if "smoke" in c["subset"]: smoke += 1
    for a,b in itertools.combinations(SPLITS,2):
        require(not (seen_rowspace[a] & seen_rowspace[b]), f"rowspace leakage across {a}/{b}")
        require(not (groups[a] & groups[b]), f"group leakage across {a}/{b}")
    require(all(counts[s] > 0 for s in SPLITS), "empty split")
    require(0 < smoke <= MAX_SMOKE_CASES, "smoke subset must be bounded")
    require(build_manifest()["manifest_sha256"] == got, "deterministic regeneration mismatch")
    return {"cases":len(manifest["cases"]), "smoke_cases":smoke, "splits":counts, "digest":got}


def main(argv=None) -> int:
    ap=argparse.ArgumentParser(); ap.add_argument("--write", help="write canonical manifest JSON"); ap.add_argument("--validate", help="validate manifest JSON; use 'generated' for regenerated manifest"); ap.add_argument("--smoke", action="store_true"); ap.add_argument("--print-summary", action="store_true")
    ns=ap.parse_args(argv); manifest=build_manifest()
    if ns.write: open(ns.write,"w",encoding="utf-8").write(json.dumps(manifest,indent=2,sort_keys=True)+"\n")
    if ns.validate:
        manifest = manifest if ns.validate == "generated" else json.load(open(ns.validate,encoding="utf-8"))
        summary=validate_manifest(manifest, smoke_only=ns.smoke)
        if ns.print_summary: print(json.dumps(summary, sort_keys=True))
    elif ns.print_summary: print(json.dumps(validate_manifest(manifest), sort_keys=True))
    return 0

if __name__ == "__main__": raise SystemExit(main())
