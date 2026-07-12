"""Deterministic public-H research corpus manifest for RLMW.

This module intentionally contains only corpus construction, manifest validation, and
solver/evaluator payload helpers. It makes no benchmark-performance claims.
"""
from __future__ import annotations

import argparse, hashlib, itertools, json
from dataclasses import dataclass
from typing import Any

VERSION = "h-native-research-v1"
FROZEN_MANIFEST_SHA256 = "b9ce7369cf3d2f1476390b8f1e823bf33d10268b1b0112cf55197ce4fff18559"
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


def is_sha256_hex(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


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


def rm1_generator_rows(m: int) -> list[str]:
    """Generator for RM(1,m): constant row followed by coordinate evaluations."""
    require(isinstance(m, int) and not isinstance(m, bool) and m >= 1, "m must be a positive integer")
    n = 1 << m
    rows = [[1] * n]
    for bit in range(m):
        rows.append([(j >> bit) & 1 for j in range(n)])
    return mat_to_rows(rows)


def rm1_parity_H(m: int) -> list[str]:
    """True parity-check basis for RM(1,m), the dual RM(m-2,m).

    Rows are evaluation vectors of all square-free monomials of degrees 0..m-2.
    This deterministic row basis has rank 2^m-(m+1) and is orthogonal to the
    constant and coordinate-evaluation generator rows of RM(1,m).
    """
    require(isinstance(m, int) and not isinstance(m, bool) and m >= 2, "m must be an integer >= 2")
    n = 1 << m
    rows = []
    for degree in range(0, m - 1):
        for bits in itertools.combinations(range(m), degree):
            row = []
            for j in range(n):
                val = 1
                for bit in bits:
                    val &= (j >> bit) & 1
                row.append(val)
            rows.append(row)
    require(len(rows) == n - (m + 1), "unexpected RM(1,m) parity row count")
    require(gf2_rank(rows) == len(rows), "RM(1,m) parity rows are not independent")
    return mat_to_rows(rows)


def gf2_matmul(A: list[list[int]], B: list[list[int]]) -> list[list[int]]:
    require(not A or (B and len(A[0]) == len(B)), "incompatible matrix shapes")
    Bt = list(zip(*B)) if B else []
    return [[sum(x & y for x, y in zip(row, col)) & 1 for col in Bt] for row in A]


def rm1_control_check(m: int) -> dict:
    """Independent exact checks for the RM(1,m) parity-check construction."""
    G = rows_to_mat(rm1_generator_rows(m))
    H = rows_to_mat(rm1_parity_H(m))
    n = 1 << m
    product = gf2_matmul(H, [list(col) for col in zip(*G)])
    require(all(v == 0 for row in product for v in row), "RM parity check is not orthogonal to RM(1,m) generator")
    require(gf2_rank(H) == n - (m + 1), "RM parity-check rank mismatch")
    columns = list(zip(*H))
    require(all(any(col) for col in columns), "RM parity check contains a zero column")
    min_w = n + 1
    for mask in range(1, 1 << (m + 1)):
        word = [0] * n
        for i, grow in enumerate(G):
            if (mask >> i) & 1:
                word = [a ^ b for a, b in zip(word, grow)]
        wt = sum(word)
        if wt < min_w:
            min_w = wt
    expected = 1 << (m - 1)
    require(min_w == expected, f"RM(1,{m}) minimum distance mismatch: {min_w} != {expected}")
    return {"m": m, "n": n, "rank_H": gf2_rank(H), "messages_checked": (1 << (m + 1)) - 1, "minimum_distance": min_w}


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


def case(cid: int, split: str, family: str, rows: list[str], W: int, construction: dict, label: dict, subset: list[str], lineage_id: str | None = None) -> dict:
    rank=gf2_rank(rows_to_mat(rows)); n=len(rows[0]); k=n-rank
    prefix=f"hnrv1-{split[0]}-{family.replace('_','-')}"
    return {"case_id": f"hnrv1-c{cid:04d}", "group_id": f"{prefix}-g{cid:04d}", "base_group_id": f"{prefix}-b{cid:04d}",
            "split": split, "family": family, "n": n, "rank": rank, "k": k, "H_rows": rows,
            "raw_H_sha256": sha({"H_rows": rows}), "rowspace_sha256": sha({"rref_rows": rref_rows(rows)}),
            "threshold": {"W": W, "relation": "at_most"}, "label": label, "provenance": construction.get("provenance"),
            "construction": construction, "subset": subset, "construction_lineage_id": lineage_id or f"{family}:{construction.get('method')}:cid{cid:04d}"}


def subset_tags(*, smoke: bool) -> list[str]:
    return ["smoke", "full"] if smoke else ["full"]


def build_manifest() -> dict:
    specs=[]; cid=1
    for split, ms in zip(SPLITS, [(6,7),(8,),(9,)]):
        for m in ms:
            rows=hamming_H(m); specs.append(case(cid, split, "hamming", rows, 3, {"method":"all_nonzero_binary_columns", "m":m, "provenance":"Hamming 1950"}, {"kind":"certified_distance", "distance":3, "certificate_method":"standard_hamming_code_theorem"}, subset_tags(smoke=m<=7))); cid+=1
            rows=extended_hamming_H(m); specs.append(case(cid, split, "extended_hamming", rows, 4, {"method":"hamming_plus_overall_parity", "m":m, "provenance":"Hamming 1950; extended parity-check construction"}, {"kind":"certified_distance", "distance":4, "certificate_method":"standard_extended_hamming_code_theorem"}, ["full"])); cid+=1
    for split,m in zip(SPLITS,[5,6,7]):
        rows=rm1_parity_H(m); specs.append(case(cid, split, "reed_muller_rm1_control", rows, 1<<(m-1), {"method":"orthogonal_complement_of_RM(1,m)_generator", "m":m, "provenance":"Muller 1954; Reed 1954"}, {"kind":"certified_distance", "distance":1<<(m-1), "certificate_method":"standard_RM(1,m)_distance_theorem"}, subset_tags(smoke=m==5))); cid+=1
    ens=[("train",96,48,3,1101),("validation",128,64,3,2202),("test",144,72,4,3303),("train",112,56,4,4404),("validation",144,72,4,5505),("test",160,80,4,6606)]
    for split,n,r,cw,seed in ens:
        rows=sparse_ldpc_H(n,r,cw,seed); ub=first_kernel_upper_bound(rows)
        label={"kind":"unknown_distance_threshold_challenge", "known_lower_bound":1, "verified_upper_bound":ub, "upper_bound_witness_exposed":False, "certificate_method":None}
        specs.append(case(cid,split,"sparse_ldpc_style",rows,max(3, (ub or 20)-1), {"method":"deterministic_regular_sparse_parity_check", "n":n,"rows":r,"column_weight":cw,"seed":seed,"provenance":"Gallager 1962 style sparse parity-check ensemble"}, label, subset_tags(smoke=n<=128))); cid+=1
    ens=[("train",96,48,7707),("validation",128,64,8808),("test",144,72,9909),("train",112,56,1010),("validation",144,72,2020),("test",160,80,3030)]
    for split,n,r,seed in ens:
        rows=dense_random_H(n,r,seed); ub=first_kernel_upper_bound(rows)
        label={"kind":"unknown_distance_threshold_challenge", "known_lower_bound":1, "verified_upper_bound":ub, "upper_bound_witness_exposed":False, "certificate_method":None}
        specs.append(case(cid,split,"dense_random_full_rank",rows,max(4, (ub or 24)-1), {"method":"deterministic_dense_full_rank_binary_matrix", "n":n,"rows":r,"seed":seed,"provenance":"MacWilliams-Sloane random linear code control ensemble"}, label, subset_tags(smoke=n<=128))); cid+=1
    body={"protocol_version":VERSION,"manifest_id":VERSION,"description":"Research-scale public-H binary-code corpus specification; no benchmark conclusions.","cases":specs,
          "budget_profiles":{"smoke":{"max_cases":MAX_SMOKE_CASES,"purpose":"CI/Colab bounded manifest and loader check only"},"full":{"max_cases":len(specs),"purpose":"research corpus enumeration; not run in CI"}},
          "references":["R. W. Hamming, Error detecting and error correcting codes, Bell System Technical Journal, 1950.","D. E. Muller, Application of Boolean algebra to switching circuit design and to error detection, IRE Transactions, 1954.","I. S. Reed, A class of multiple-error-correcting codes and the decoding scheme, IRE Transactions, 1954.","R. G. Gallager, Low-density parity-check codes, IRE Transactions, 1962.","F. J. MacWilliams and N. J. A. Sloane, The Theory of Error-Correcting Codes, 1977."]}
    body["manifest_sha256"] = sha({k:v for k,v in body.items() if k != "manifest_sha256"})
    return body


def solver_payload(manifest: dict, case_id: str, budget_run: dict | None = None) -> dict:
    c=next((x for x in manifest["cases"] if x["case_id"]==case_id), None); require(c is not None, "unknown case_id")
    return {"protocol_version": manifest["protocol_version"], "manifest_sha256": manifest["manifest_sha256"], "case_id": c["case_id"], "H_rows": c["H_rows"], "H_sha256": c["raw_H_sha256"], "W": c["threshold"]["W"], "budget_run": budget_run or {"profile":"smoke","seed":0}}


def _validate_exact_case(c: dict) -> None:
    """Run matrix-derived and family-certificate checks for one selected case."""
    rows=c["H_rows"]; M=rows_to_mat(rows); n=len(rows[0]); rank=gf2_rank(M)
    require(c["n"]==n and c["rank"]==rank and c["k"]==n-rank and n > 12, "dimension/rank invalid or not beyond tiny oracle")
    require(c["raw_H_sha256"] == sha({"H_rows": rows}), "raw H hash mismatch")
    require(c["rowspace_sha256"] == sha({"rref_rows": rref_rows(rows)}), "rowspace hash mismatch")
    lab=c["label"]
    if lab["kind"] == "certified_distance" and c["family"] == "reed_muller_rm1_control":
        chk = rm1_control_check(c["construction"]["m"])
        require(chk["minimum_distance"] == lab["distance"], "RM independent distance check mismatch")


def _validate_manifest_core(manifest: dict, *, smoke_only: bool, expected_manifest_sha256: str) -> dict:
    """Validate one expected manifest, with exact matrix work limited by ``smoke_only``."""
    require(isinstance(smoke_only, bool), "smoke_only must be boolean")
    require(is_sha256_hex(expected_manifest_sha256), "invalid expected manifest digest")
    require(isinstance(manifest, dict), "manifest must be an object")
    require(manifest.get("protocol_version") == VERSION, "wrong protocol_version")
    got=manifest.get("manifest_sha256"); require(is_sha256_hex(got), "missing manifest digest")
    require(got == sha({k:v for k,v in manifest.items() if k != "manifest_sha256"}), "manifest digest mismatch")
    require(got == expected_manifest_sha256, "unexpected frozen manifest digest")
    cases=manifest.get("cases"); require(isinstance(cases,list) and cases, "cases must be a nonempty list")
    selected_cases=0
    exact_matrix_checked_case_ids=[]
    seen_ids=set(); seen_rowspace={s:set() for s in SPLITS}; lineages={s:set() for s in SPLITS}; groups={s:set() for s in SPLITS}; counts={s:0 for s in SPLITS}; smoke=0
    for c in cases:
        require(isinstance(c,dict), "case must be an object")
        case_id=c.get("case_id"); require(isinstance(case_id,str) and case_id and case_id not in seen_ids, "duplicate or invalid case_id"); seen_ids.add(case_id)
        split=c.get("split"); require(split in SPLITS, "bad split"); counts[split]+=1
        n=c.get("n"); rank=c.get("rank"); k=c.get("k")
        require(isinstance(n,int) and not isinstance(n,bool) and n > 12, "invalid declared length")
        require(isinstance(rank,int) and not isinstance(rank,bool) and 0 <= rank <= n, "invalid declared rank")
        require(isinstance(k,int) and not isinstance(k,bool) and k == n-rank, "invalid declared dimension")
        raw_hash=c.get("raw_H_sha256"); rowspace_hash=c.get("rowspace_sha256")
        require(is_sha256_hex(raw_hash), "invalid declared raw H hash")
        require(is_sha256_hex(rowspace_hash), "invalid declared rowspace hash")
        group_id=c.get("group_id"); base_group_id=c.get("base_group_id")
        require(isinstance(group_id,str) and group_id and isinstance(base_group_id,str) and base_group_id, "missing group identifier")
        require(group_id not in groups[split] and base_group_id not in groups[split], "duplicate group in split")
        lineage=c.get("construction_lineage_id"); require(isinstance(lineage,str) and lineage, "missing construction lineage")
        require(lineage not in lineages[split], "duplicate construction lineage in split")
        lineages[split].add(lineage)
        groups[split].update([group_id, base_group_id]); seen_rowspace[split].add(rowspace_hash)
        subset=c.get("subset")
        require(isinstance(subset, list) and subset == list(dict.fromkeys(subset)) and all(x in {"smoke", "full"} for x in subset) and "full" in subset and subset in (["full"], ["smoke", "full"]), "subset must be unique ordered smoke/full tags and include full")
        threshold=c.get("threshold"); require(isinstance(threshold,dict) and threshold.get("relation") == "at_most", "bad threshold relation")
        W=threshold.get("W"); require(isinstance(W,int) and not isinstance(W,bool) and 0 < W <= n, "bad threshold")
        lab=c.get("label"); require(isinstance(lab,dict) and lab.get("kind") in {"certified_distance","unknown_distance_threshold_challenge"}, "bad label kind")
        if lab["kind"] == "certified_distance":
            distance=lab.get("distance"); require(isinstance(distance,int) and not isinstance(distance,bool) and distance > 0, "bad certified label")
        else:
            lower=lab.get("known_lower_bound")
            require(lab.get("distance") is None and isinstance(lower,int) and not isinstance(lower,bool) and lower >= 1 and lab.get("upper_bound_witness_exposed") is False, "unknown label leaks or implies exact distance")
        p=solver_payload(manifest,case_id); require(tuple(p.keys()) == SOLVER_KEYS and not (set(p) & EVALUATOR_ONLY_KEYS), "solver payload leakage")
        is_smoke = "smoke" in subset
        if is_smoke: smoke += 1
        if (not smoke_only) or is_smoke:
            _validate_exact_case(c)
            exact_matrix_checked_case_ids.append(case_id)
            selected_cases += 1
    for a,b in itertools.combinations(SPLITS,2):
        require(not (seen_rowspace[a] & seen_rowspace[b]), f"rowspace leakage across {a}/{b}")
        require(not (groups[a] & groups[b]), f"group leakage across {a}/{b}")
        require(not (lineages[a] & lineages[b]), f"construction lineage leakage across {a}/{b}")
    require(all(counts[s] > 0 for s in SPLITS), "empty split")
    require(0 < smoke <= MAX_SMOKE_CASES, "smoke subset must be bounded")
    if not smoke_only:
        require(build_manifest()["manifest_sha256"] == got, "deterministic regeneration mismatch")
    return {"selected_cases":selected_cases, "exact_matrix_checked_case_ids":exact_matrix_checked_case_ids, "total_cases":len(cases), "smoke_cases":smoke, "splits":counts, "digest":got, "smoke_only":smoke_only}


def validate_manifest(manifest: dict, *, smoke_only: bool=False) -> dict:
    """Validate the frozen v1 manifest; smoke mode limits matrix-derived work to smoke cases."""
    return _validate_manifest_core(manifest, smoke_only=smoke_only, expected_manifest_sha256=FROZEN_MANIFEST_SHA256)



def run_regression_tests() -> dict:
    manifest = build_manifest()
    require(manifest["manifest_sha256"] == FROZEN_MANIFEST_SHA256, "generator no longer matches frozen manifest digest")
    smoke_summary = validate_manifest(json.loads(json.dumps(manifest)), smoke_only=True)
    full_summary = validate_manifest(json.loads(json.dumps(manifest)), smoke_only=False)
    smoke_ids = [c["case_id"] for c in manifest["cases"] if "smoke" in c["subset"]]
    full_ids = [c["case_id"] for c in manifest["cases"]]
    require(smoke_summary["selected_cases"] == MAX_SMOKE_CASES, "smoke validation selected wrong case count")
    require(smoke_summary["exact_matrix_checked_case_ids"] == smoke_ids, "smoke validation checked a non-smoke matrix or missed a smoke matrix")
    require(full_summary["selected_cases"] == full_summary["total_cases"], "full validation did not select all cases")
    require(full_summary["exact_matrix_checked_case_ids"] == full_ids, "full validation did not check every matrix")
    rm_checks = [rm1_control_check(m) for m in (5, 6, 7)]

    def expect_failure(label: str, fn, message_fragment: str | None = None) -> None:
        try:
            fn()
        except ValueError as exc:
            if message_fragment is not None:
                require(message_fragment in str(exc), f"negative regression failed for the wrong reason: {label}: {exc}")
            return
        fail(f"negative regression unexpectedly passed: {label}")

    def resign(obj: dict) -> str:
        obj["manifest_sha256"] = sha({k:v for k,v in obj.items() if k != "manifest_sha256"})
        return obj["manifest_sha256"]

    bad = json.loads(json.dumps(manifest)); bad["cases"][0]["subset"] = ["full", "full"]; bad["manifest_sha256"] = sha({k:v for k,v in bad.items() if k != "manifest_sha256"})
    expect_failure("duplicate subset values", lambda: _validate_manifest_core(bad, smoke_only=True, expected_manifest_sha256=bad["manifest_sha256"]), "subset must be unique")
    bad = json.loads(json.dumps(manifest)); bad["cases"][0]["subset"] = ["smoke"]; bad["manifest_sha256"] = sha({k:v for k,v in bad.items() if k != "manifest_sha256"})
    expect_failure("subset missing full", lambda: _validate_manifest_core(bad, smoke_only=True, expected_manifest_sha256=bad["manifest_sha256"]), "subset must be unique")
    bad = json.loads(json.dumps(manifest)); bad["cases"][0]["subset"] = ["full", "smoke"]; bad["manifest_sha256"] = sha({k:v for k,v in bad.items() if k != "manifest_sha256"})
    expect_failure("misordered subset", lambda: _validate_manifest_core(bad, smoke_only=True, expected_manifest_sha256=bad["manifest_sha256"]), "subset must be unique")
    bad = json.loads(json.dumps(manifest)); bad["cases"][4]["construction_lineage_id"] = bad["cases"][0]["construction_lineage_id"]; resign(bad)
    expect_failure("non-smoke cross-split construction lineage reuse", lambda: _validate_manifest_core(bad, smoke_only=True, expected_manifest_sha256=bad["manifest_sha256"]), "construction lineage leakage across")
    bad = json.loads(json.dumps(manifest)); bad["cases"][4]["rowspace_sha256"] = bad["cases"][0]["rowspace_sha256"]; resign(bad)
    expect_failure("non-smoke declared rowspace reuse", lambda: _validate_manifest_core(bad, smoke_only=True, expected_manifest_sha256=bad["manifest_sha256"]), "rowspace leakage across")

    # A private, explicitly rebound smoke check skips matrix-derived work for a
    # non-smoke case.  The public validator still rejects the modified full
    # manifest via FROZEN_MANIFEST_SHA256, and full validation detects the bad H.
    bad = json.loads(json.dumps(manifest))
    row = bad["cases"][1]["H_rows"][0]
    bad["cases"][1]["H_rows"][0] = ("1" if row[0] == "0" else "0") + row[1:]
    resign(bad)
    rebound_smoke = _validate_manifest_core(bad, smoke_only=True, expected_manifest_sha256=bad["manifest_sha256"])
    require(rebound_smoke["exact_matrix_checked_case_ids"] == smoke_ids, "rebound smoke validation checked a non-smoke matrix")
    expect_failure("public frozen digest binding", lambda: validate_manifest(bad, smoke_only=True), "unexpected frozen manifest digest")
    expect_failure("full validation checks non-smoke H", lambda: _validate_manifest_core(bad, smoke_only=False, expected_manifest_sha256=bad["manifest_sha256"]), "raw H hash mismatch")

    bad = json.loads(json.dumps(manifest)); bad["cases"][0]["manifest_sha256"] = "not a case field"
    expect_failure("manifest digest mismatch", lambda: validate_manifest(bad, smoke_only=True), "manifest digest mismatch")
    return {"smoke": smoke_summary, "full": full_summary, "rm1_checks": rm_checks}

def main(argv=None) -> int:
    ap=argparse.ArgumentParser(); ap.add_argument("--write", help="write canonical manifest JSON"); ap.add_argument("--validate", help="validate manifest JSON; use 'generated' for regenerated manifest"); ap.add_argument("--smoke", action="store_true"); ap.add_argument("--print-summary", action="store_true"); ap.add_argument("--self-test", action="store_true")
    ns=ap.parse_args(argv)
    if ns.self_test:
        summary = run_regression_tests()
        if ns.print_summary: print(json.dumps(summary, sort_keys=True))
        return 0
    manifest = None
    if ns.write:
        manifest = build_manifest()
        open(ns.write,"w",encoding="utf-8").write(json.dumps(manifest,indent=2,sort_keys=True)+"\n")
    if ns.validate:
        if ns.validate == "generated":
            if manifest is None: manifest = build_manifest()
        else:
            manifest = json.load(open(ns.validate,encoding="utf-8"))
        summary=validate_manifest(manifest, smoke_only=ns.smoke)
        if ns.print_summary: print(json.dumps(summary, sort_keys=True))
    elif ns.print_summary:
        if manifest is None: manifest = build_manifest()
        print(json.dumps(validate_manifest(manifest), sort_keys=True))
    return 0

if __name__ == "__main__": raise SystemExit(main())
