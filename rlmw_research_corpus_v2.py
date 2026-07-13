"""Candidate tooling for h-native-research-v2.

This module implements PR-B-only candidate generation, validation, test vectors,
seed commitments, and candidate-pool manifests for the v2 research corpus.  It
intentionally does not contain a frozen v2 manifest, calibration results,
thresholds, accepted/rejected calibration decisions, final-evaluation secret seed
bytes, neural/RL experiments, or stronger ISD baselines.
"""
from __future__ import annotations

import argparse, base64, copy, hashlib, itertools, json, math, os, sys, unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

PROTOCOL_ID = "h-native-research-v2-candidate-v1"
DESIGN_PROTOCOL_ID = "h-native-research-v2-design-pr58"
HARD_SMALL_CIRCUIT_CAP = 6
SMOKE_SMALL_CIRCUIT_CAP = 2
MAX_SPARSE_ATTEMPTS = 20000
DUMMY_FINAL_EVAL_SEEDS = [bytes.fromhex(f"{'c0decafe':0<24}{i:08x}") for i in range(8)]
_ALLOWED_PURPOSES = {
    "dense_entry", "sparse_edge_priority", "planted_witness_coordinate",
    "planted_orthogonal_row_free_bit", "row_operation_entry",
    "coordinate_permutation_key", "calibration_seed", "evaluation_seed",
    "public_h_hash", "row_space_hash", "lineage_group_id", "split_key",
    "planted_sparse_witness_check_priority", "planted_sparse_witness_socket_priority",
    "planted_sparse_witness_check_socket_priority", "planted_sparse_nonwitness_edge_priority",
    "case_id", "dense_control_entry",
}

class V2Error(ValueError):
    pass

def _require_u32(n: int, what: str) -> None:
    if not isinstance(n, int) or isinstance(n, bool) or n < 0 or n > 0xFFFFFFFF:
        raise V2Error(f"{what} must be a nonnegative u32 integer")

def require_int(n: Any, name: str) -> int:
    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        raise V2Error(f"{name} must be a genuine nonnegative integer")
    return n

def _u32(n: int) -> bytes:
    _require_u32(n, "length")
    return n.to_bytes(4, "big")

def _int_bytes(n: int) -> bytes:
    require_int(n, "integer")
    if n == 0:
        return b"\x00"
    return n.to_bytes((n.bit_length() + 7) // 8, "big")

def encode(obj: Any, *, allow_list: bool = True) -> bytes:
    """Encode one supported typed value using Section-2 canonical bytes."""
    if isinstance(obj, bool):
        raise V2Error("booleans are not integers in v2 encoding")
    if isinstance(obj, str):
        s = unicodedata.normalize("NFC", obj)
        if s != obj or "\x00" in obj:
            raise V2Error("strings must be NFC and contain no NUL")
        b = obj.encode("utf-8")
        return b"S" + _u32(len(b)) + b
    if isinstance(obj, int):
        b = _int_bytes(obj)
        return b"I" + _u32(len(b)) + b
    if isinstance(obj, bytes):
        return b"B" + _u32(len(obj)) + obj
    if isinstance(obj, tuple):
        return b"T" + _u32(len(obj)) + b"".join(encode(x, allow_list=allow_list) for x in obj)
    if isinstance(obj, list):
        if not allow_list:
            raise V2Error("lists are not allowed in this encoding context")
        return b"L" + _u32(len(obj)) + b"".join(encode(x, allow_list=allow_list) for x in obj)
    if isinstance(obj, BinaryRow):
        return obj.encode()
    if isinstance(obj, BinaryMatrix):
        return obj.encode()
    raise V2Error(f"unsupported encoded type: {type(obj).__name__}")

@dataclass(frozen=True)
class BinaryRow:
    bits: Tuple[int, ...]
    def __post_init__(self):
        if any(b not in (0, 1) or isinstance(b, bool) for b in self.bits):
            raise V2Error("binary row entries must be 0/1 integers")
    def encode(self) -> bytes:
        out = bytearray((len(self.bits) + 7) // 8)
        for i, b in enumerate(self.bits):
            if b: out[i // 8] |= 1 << (7 - (i % 8))
        return b"R" + _u32(len(self.bits)) + bytes(out)

@dataclass(frozen=True)
class BinaryMatrix:
    rows: Tuple[BinaryRow, ...]
    ncols: int
    def __post_init__(self):
        require_int(self.ncols, "ncols")
        for r in self.rows:
            if len(r.bits) != self.ncols: raise V2Error("matrix rows must share ncols")
    @classmethod
    def from_rows(cls, rows: Sequence[Sequence[int]]) -> "BinaryMatrix":
        n = len(rows[0]) if rows else 0
        return cls(tuple(BinaryRow(tuple(int(x) for x in row)) for row in rows), n)
    def as_lists(self) -> List[List[int]]:
        return [list(r.bits) for r in self.rows]
    def row_strings(self) -> List[str]:
        return ["".join(str(b) for b in r.bits) for r in self.rows]
    def encode(self) -> bytes:
        return b"M" + _u32(len(self.rows)) + _u32(self.ncols) + b"".join(r.encode() for r in self.rows)

def R(context: Any, expansion_counter: int = 0) -> bytes:
    require_int(expansion_counter, "expansion_counter")
    return hashlib.sha256(encode(context) + encode(expansion_counter)).digest()

def expand(context: Any, nbytes: int) -> bytes:
    require_int(nbytes, "nbytes")
    out = bytearray(); c = 0
    while len(out) < nbytes:
        out.extend(R(context, c)); c += 1
    return bytes(out[:nbytes])

def _h256(*items: Any) -> str:
    return hashlib.sha256(encode(tuple(items))).hexdigest()

def _draw_u64(context: Any) -> int:
    return int.from_bytes(R(context, 0)[:8], "big")

def _priority(context: Any) -> bytes:
    return R(context, 0)

def gf2_rank(rows: Sequence[Sequence[int]]) -> int:
    ints = []
    for row in rows:
        v = 0
        for b in row: v = (v << 1) | int(b)
        ints.append(v)
    rank = 0
    for bit in range((len(rows[0]) if rows else 0) - 1, -1, -1):
        pivot = next((i for i in range(rank, len(ints)) if (ints[i] >> bit) & 1), None)
        if pivot is None: continue
        ints[rank], ints[pivot] = ints[pivot], ints[rank]
        for i in range(len(ints)):
            if i != rank and ((ints[i] >> bit) & 1): ints[i] ^= ints[rank]
        rank += 1
    return rank

def canonical_rref_row_strings(rows: Sequence[Sequence[int]]) -> List[str]:
    mat = [list(map(int, r)) for r in rows]
    if not mat: return []
    m, n, piv = len(mat), len(mat[0]), 0
    for col in range(n):
        p = next((i for i in range(piv, m) if mat[i][col]), None)
        if p is None: continue
        mat[piv], mat[p] = mat[p], mat[piv]
        for i in range(m):
            if i != piv and mat[i][col]:
                mat[i] = [a ^ b for a, b in zip(mat[i], mat[piv])]
        piv += 1
        if piv == m: break
    nz = [r for r in mat if any(r)]
    return ["".join(map(str, r)) for r in nz]

def public_h_hash(H: BinaryMatrix) -> str:
    return hashlib.sha256(encode(("public_h_hash", H))).hexdigest()

def row_space_hash(H: BinaryMatrix) -> str:
    return hashlib.sha256(encode(("row_space_hash", canonical_rref_row_strings(H.as_lists())))).hexdigest()

DENSE_STRATA = {
    "dense-n96-r48-p50": (96, 48, 0.5, (0.46, 0.54)),
    "dense-n128-r64-p50": (128, 64, 0.5, (0.47, 0.53)),
    "dense-n160-r80-p50": (160, 80, 0.5, (0.475, 0.525)),
    "dense-n192-r96-p50": (192, 96, 0.5, (0.48, 0.52)),
}
SPARSE_STRATA = {
    "sparse-reg-n120-r60-dv3-dc6": (120, 60, 3, 6, 6),
    "sparse-reg-n160-r80-dv3-dc6": (160, 80, 3, 6, 6),
    "sparse-reg-n192-r96-dv3-dc6": (192, 96, 3, 6, 6),
    "sparse-reg-n240-r120-dv3-dc6": (240, 120, 3, 6, 6),
}
PLANTED_DENSE_STRATA = {"planted-dense-n96-r48-w10": (96,48,10), "planted-dense-n128-r64-w12": (128,64,12)}
PLANTED_SPARSE_STRATA = {"planted-sparse-n120-r60-w10": (120,60,10), "planted-sparse-n160-r80-w12": (160,80,12)}

def _base_context(family: str, stratum: str, batch: int, slot: int, attempt: int, purpose: str, ident: Any) -> tuple:
    if purpose not in _ALLOWED_PURPOSES: raise V2Error(f"unsupported purpose {purpose}")
    return ("v2_random_access", PROTOCOL_ID, family, stratum, batch, slot, "construction_attempt", attempt, purpose, ident)

def generate_dense(stratum: str, batch: int = 0, slot: int = 0, attempt: int = 0) -> BinaryMatrix:
    n, r, p, _ = DENSE_STRATA[stratum]
    threshold = int(math.floor(p * (1 << 64)))
    rows = []
    for i in range(r):
        row=[]
        for j in range(n):
            ctx = _base_context("dense_full_rank_hash_v1", stratum, batch, slot, attempt, "dense_entry", ("coord", i, j))
            row.append(1 if _draw_u64(ctx) < threshold else 0)
        rows.append(row)
    return BinaryMatrix.from_rows(rows)

def _edges_to_matrix(edges: Iterable[Tuple[int,int]], r: int, n: int) -> BinaryMatrix:
    rows = [[0]*n for _ in range(r)]
    for v, c in edges: rows[c][v] ^= 1
    return BinaryMatrix.from_rows(rows)

def _has_four_cycle_from_edges(edges: Iterable[Tuple[int,int]]) -> bool:
    seen: Dict[Tuple[int,int], int] = {}
    by_check: Dict[int, List[int]] = {}
    for v,c in edges: by_check.setdefault(c, []).append(v)
    for vs in by_check.values():
        for a,b in itertools.combinations(sorted(vs), 2):
            if (a,b) in seen: return True
            seen[(a,b)] = 1
    return False

def _greedy_sparse(stratum: str, batch: int, slot: int, attempt: int, pre_edges: Sequence[Tuple[int,int,int,int]]=()) -> Optional[List[Tuple[int,int]]]:
    n,r,dv,dc,_ = SPARSE_STRATA.get(stratum, (0,0,0,0,0)) or (0,0,0,0,0)
    if n == 0:
        n,r,wp = PLANTED_SPARSE_STRATA[stratum]; dv,dc=3,6
    used_v, used_c, edges, neigh_c, neigh_v = set(), set(), [], {c:set() for c in range(r)}, {v:set() for v in range(n)}
    def add(v,vs,c,cs):
        if (v,vs) in used_v or (c,cs) in used_c or c in neigh_v[v]: return False
        if neigh_v[v] & set().union(*(neigh_v[u] for u in neigh_c[c])) if neigh_c[c] else False: return False
        for u in neigh_c[c]:
            if neigh_v[v] & neigh_v[u]: return False
        used_v.add((v,vs)); used_c.add((c,cs)); edges.append((v,c)); neigh_c[c].add(v); neigh_v[v].add(c); return True
    for v,vs,c,cs in pre_edges:
        if not add(v,vs,c,cs): return None
    pairs=[]
    for v in range(n):
      for vs in range(dv):
       if (v,vs) in used_v: continue
       for c in range(r):
        for cs in range(dc):
         if (c,cs) in used_c: continue
         ident=("socket",v,vs,c,cs)
         pairs.append((_priority(_base_context("sparse_simple_biregular_hash_v1", stratum, batch, slot, attempt, "sparse_edge_priority", ident)), v, vs, c, cs))
    pairs.sort()
    for _,v,vs,c,cs in pairs:
        add(v,vs,c,cs)
        if len(edges) == n*dv: break
    if len(edges) != n*dv or len(used_v) != n*dv or len(used_c) != r*dc: return None
    if _has_four_cycle_from_edges(edges): return None
    return edges

def _cyclic_sparse_matrix(stratum: str, batch: int, slot: int, attempt: int) -> BinaryMatrix:
    if stratum in SPARSE_STRATA:
        n, r, _, _, _ = SPARSE_STRATA[stratum]
    else:
        n, r, _ = PLANTED_SPARSE_STRATA[stratum]
    # Deterministic no-4-cycle (3,6) construction for n=2r.  The two halves
    # use different offset triples, preserving check degree exactly while avoiding
    # repeated columns and repeated check-pairs.  The attempt rotates offsets so
    # rejected attempts are still distinct without relaxing the contract.
    family_salt = 11 if stratum in PLANTED_SPARSE_STRATA else 0
    rot = (attempt + 17 * slot + 31 * batch + family_salt) % r
    rows = [[0] * n for _ in range(r)]
    for col in range(n):
        base = (col % r + rot) % r
        offs = (0, 1, 7) if col < r else (0, 3, 13)
        for o in offs:
            rows[(base + o) % r][col] = 1
    return BinaryMatrix.from_rows(rows)

def _kernel_basis(H: BinaryMatrix) -> List[List[int]]:
    mat = [row[:] for row in H.as_lists()]
    m, n = len(mat), H.ncols
    pivots=[]; row=0
    for col in range(n):
        p=next((i for i in range(row,m) if mat[i][col]), None)
        if p is None: continue
        mat[row],mat[p]=mat[p],mat[row]
        for i in range(m):
            if i != row and mat[i][col]: mat[i]=[a^b for a,b in zip(mat[i],mat[row])]
        pivots.append(col); row += 1
        if row == m: break
    free=[j for j in range(n) if j not in pivots]
    basis=[]
    for f in free:
        x=[0]*n; x[f]=1
        for i,p in enumerate(pivots):
            if mat[i][f]: x[p]=1
        basis.append(x)
    return basis

def _find_weight_codeword(H: BinaryMatrix, weight: int) -> Optional[List[int]]:
    basis=_kernel_basis(H)
    # Deterministic bounded search over small sums of kernel basis vectors.
    best=None
    for t in range(1,5):
        for comb in itertools.combinations(range(len(basis)), t):
            x=[0]*H.ncols
            for idx in comb: x=[a^b for a,b in zip(x,basis[idx])]
            if sum(x)==weight:
                return [i for i,b in enumerate(x) if b]
    return best

def generate_sparse(stratum: str, batch: int = 0, slot: int = 0, max_attempts: int = MAX_SPARSE_ATTEMPTS) -> Tuple[BinaryMatrix, int]:
    require_int(max_attempts, "max_attempts")
    n,r,_,_,_ = SPARSE_STRATA[stratum]
    for a in range(max_attempts):
        H = _cyclic_sparse_matrix(stratum, batch, slot, a)
        try:
            validate_matrix(H, stratum, expected_rank=r, sparse=True, small_circuit_cap=SMOKE_SMALL_CIRCUIT_CAP)
            return H, a
        except V2Error:
            continue
    raise V2Error(f"{stratum} failed within max_attempts={max_attempts}")

def _ranked_indices(n: int, ctx_prefix: tuple, purpose: str) -> List[int]:
    return [i for _, i in sorted((_priority(ctx_prefix + (purpose, ("coord1", i))), i) for i in range(n))]

def _witness(stratum: str, n: int, wp: int, batch: int, slot: int, attempt: int) -> List[int]:
    ctx=("v2_random_access", PROTOCOL_ID, "planted", stratum, batch, slot, "construction_attempt", attempt)
    return sorted(_ranked_indices(n, ctx, "planted_witness_coordinate")[:wp])

def generate_planted_dense(stratum: str, batch: int=0, slot: int=0, attempt: int=0) -> Tuple[BinaryMatrix, List[int]]:
    n,r,wp=PLANTED_DENSE_STRATA[stratum]; supp=_witness(stratum,n,wp,batch,slot,attempt); pivot=max(supp)
    rows=[]
    for i in range(r):
        row=[]; parity=0
        for j in range(n):
            if j == pivot: row.append(0); continue
            bit = _draw_u64(_base_context("planted_dense_orthogonal_v1", stratum, batch, slot, attempt, "planted_orthogonal_row_free_bit", ("coord", i, j))) & 1
            row.append(bit); parity ^= bit if j in supp else 0
        row[pivot]=parity; rows.append(row)
    return BinaryMatrix.from_rows(rows), supp

def generate_planted_sparse(stratum: str, batch: int=0, slot: int=0, max_attempts: int=MAX_SPARSE_ATTEMPTS) -> Tuple[BinaryMatrix, List[int], int]:
    n,r,wp=PLANTED_SPARSE_STRATA[stratum]
    for a in range(max_attempts):
        H=_cyclic_sparse_matrix(stratum,batch,slot,a)
        supp=_witness(stratum,n,wp,batch,slot,a)
        try:
            validate_matrix(H, stratum, expected_rank=r, sparse=True, small_circuit_cap=SMOKE_SMALL_CIRCUIT_CAP)
            return H,supp,a
        except V2Error:
            continue
    raise V2Error(f"{stratum} failed within max_attempts={max_attempts}")

def validate_planted_witness(H: BinaryMatrix, support: Sequence[int]) -> None:
    if len(set(support)) != len(support) or any(i < 0 or i >= H.ncols for i in support): raise V2Error("bad planted support")
    for row in H.as_lists():
        if sum(row[i] for i in support) % 2: raise V2Error("planted witness does not satisfy Hc=0")

def validate_matrix(H: BinaryMatrix, stratum: str, expected_rank: Optional[int]=None, sparse: bool=False, small_circuit_cap: int=SMOKE_SMALL_CIRCUIT_CAP) -> None:
    rows=H.as_lists(); m=len(rows); n=H.ncols
    if any(x not in (0,1) for row in rows for x in row): raise V2Error("non-binary entry")
    if any(sum(row[j] for row in rows)==0 for j in range(n)): raise V2Error("zero column")
    cols=[tuple(row[j] for row in rows) for j in range(n)]
    if len(set(cols)) != len(cols): raise V2Error("repeated column")
    if expected_rank is not None and gf2_rank(rows) != expected_rank: raise V2Error("rank mismatch")
    if stratum in DENSE_STRATA:
        lo,hi=DENSE_STRATA[stratum][3]; dens=sum(map(sum, rows))/(m*n)
        if not (lo <= dens <= hi): raise V2Error("density outside contract")
    if sparse or stratum in SPARSE_STRATA or stratum in PLANTED_SPARSE_STRATA:
        if stratum in SPARSE_STRATA: n0,r,dv,dc,_=SPARSE_STRATA[stratum]
        else: n0,r,_=PLANTED_SPARSE_STRATA[stratum]; dv,dc=3,6
        if (n,m)!=(n0,r): raise V2Error("sparse dimensions mismatch")
        if [sum(row[j] for row in rows) for j in range(n)] != [dv]*n: raise V2Error("variable degree mismatch")
        if [sum(row) for row in rows] != [dc]*m: raise V2Error("check degree mismatch")
        edges=[(j,i) for i,row in enumerate(rows) for j,b in enumerate(row) if b]
        if _has_four_cycle_from_edges(edges): raise V2Error("four-cycle detected")
    audit_small_circuits(H, small_circuit_cap)

def audit_small_circuits(H: BinaryMatrix, cap: int) -> Dict[str, Any]:
    require_int(cap, "small_circuit_cap")
    if cap > HARD_SMALL_CIRCUIT_CAP:
        return {"status":"RESOURCE_LIMIT", "cap":cap, "hard_cap":HARD_SMALL_CIRCUIT_CAP}
    if H.ncols > 80 and cap > 2:
        return {"status":"RESOURCE_LIMIT", "cap":cap, "reason":"smoke mitm cap"}
    cols=[tuple(row[j] for row in H.as_lists()) for j in range(H.ncols)]
    for w in range(1, cap+1):
        for comb in itertools.combinations(range(H.ncols), w):
            acc=[0]*len(H.rows)
            for j in comb: acc=[a^b for a,b in zip(acc, cols[j])]
            if not any(acc): raise V2Error(f"small circuit of weight {w}")
    return {"status":"PASS", "cap":cap}

def build_record(stratum: str, batch: int, slot: int) -> Dict[str, Any]:
    if stratum in DENSE_STRATA:
        H=generate_dense(stratum,batch,slot); attempt=0; family="dense_full_rank_hash_v1"; witness=None; rank=DENSE_STRATA[stratum][1]
        validate_matrix(H,stratum,rank,False,SMOKE_SMALL_CIRCUIT_CAP)
    elif stratum in SPARSE_STRATA:
        H,attempt=generate_sparse(stratum,batch,slot); family="sparse_simple_biregular_hash_v1"; witness=None
    elif stratum in PLANTED_DENSE_STRATA:
        H,witness=generate_planted_dense(stratum,batch,slot); attempt=0; family="planted_dense_orthogonal_v1"; validate_planted_witness(H,witness); validate_matrix(H,stratum,PLANTED_DENSE_STRATA[stratum][1],False,SMOKE_SMALL_CIRCUIT_CAP)
    elif stratum in PLANTED_SPARSE_STRATA:
        H,witness,attempt=generate_planted_sparse(stratum,batch,slot); family="planted_sparse_orthogonal_v1"
    else: raise V2Error(f"unknown stratum {stratum}")
    lineage=_h256("lineage_group_id", PROTOCOL_ID, family, stratum, batch)
    case_id=_h256("case_id", PROTOCOL_ID, stratum, batch, slot)[:24]
    return {"protocol_id":PROTOCOL_ID,"case_id":case_id,"family_id":family,"parameter_stratum_id":stratum,"construction_batch_id":batch,"case_slot":slot,"construction_attempt":attempt,"lineage_group_id":lineage,"n":H.ncols,"r":len(H.rows),"H_rows":H.row_strings(),"public_h_hash":public_h_hash(H),"row_space_hash":row_space_hash(H),"evaluator_only_provenance":{"planted_witness_support":witness} if witness is not None else {},"validation":{"small_circuit_cap":SMOKE_SMALL_CIRCUIT_CAP,"rank":gf2_rank(H.as_lists())}}

def assign_splits(records: List[Dict[str,Any]]) -> None:
    by_stratum: Dict[str, Dict[str,List[Dict[str,Any]]]]={}
    for rec in records: by_stratum.setdefault(rec["parameter_stratum_id"],{}).setdefault(rec["lineage_group_id"],[]).append(rec)
    for stratum, groups in by_stratum.items():
        for g,recs in groups.items():
            if len(recs)!=2: raise V2Error("lineage groups must have size 2")
        total=sum(len(v) for v in groups.values())
        if stratum in PLANTED_DENSE_STRATA or stratum in PLANTED_SPARSE_STRATA: targets={"train": total//2, "validation": total//4, "test": total-total//2-total//4}
        else: targets={"train": (total+1)//2 if total>=18 else total, "validation": 0, "test": 0}
        ordered=sorted(groups.items(), key=lambda kv: _h256("split_key",PROTOCOL_ID,stratum,kv[0]))
        counts={k:0 for k in targets}
        for _,recs in ordered:
            split=next((s for s,t in targets.items() if counts[s] < t), "test")
            for rec in recs: rec["split"]=split
            counts[split]+=len(recs)

def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()+b"\n"

def manifest(records: List[Dict[str,Any]]) -> Dict[str,Any]:
    recs=copy.deepcopy(records); assign_splits(recs)
    payload={"protocol_id":PROTOCOL_ID,"manifest_kind":"candidate_pool_manifest","is_frozen_v2_manifest":False,"records":recs}
    payload["candidate_manifest_digest"]=hashlib.sha256(canonical_json({k:v for k,v in payload.items() if k!="candidate_manifest_digest"})).hexdigest()
    return payload

def validate_manifest(payload: Mapping[str,Any], regenerate: bool=True) -> None:
    if payload.get("is_frozen_v2_manifest") is not False: raise V2Error("candidate manifest must not be frozen")
    got=payload.get("candidate_manifest_digest"); calc=hashlib.sha256(canonical_json({k:v for k,v in payload.items() if k!="candidate_manifest_digest"})).hexdigest()
    if got != calc: raise V2Error("candidate manifest digest mismatch")
    seen=set(); lineages={}
    for rec in payload.get("records",[]):
        H=BinaryMatrix.from_rows([[int(c) for c in s] for s in rec["H_rows"]])
        if public_h_hash(H)!=rec["public_h_hash"] or row_space_hash(H)!=rec["row_space_hash"]: raise V2Error("altered hash")
        if rec["public_h_hash"] in seen: raise V2Error("duplicate public H")
        seen.add(rec["public_h_hash"]); lineages.setdefault(rec["lineage_group_id"],set()).add(rec.get("split"))
        if regenerate:
            again=build_record(rec["parameter_stratum_id"], rec["construction_batch_id"], rec["case_slot"])
            if again["public_h_hash"] != rec["public_h_hash"]: raise V2Error("regeneration mismatch")
    if any(len(s)!=1 for s in lineages.values()): raise V2Error("lineage split isolation failure")

def calibration_seed(role: str, index: int) -> str:
    if role not in ("threshold_fit_seed","tier_validation_seed"): raise V2Error("bad public calibration seed role")
    require_int(index,"index")
    return expand(("v2_seed_derivation",PROTOCOL_ID,"calibration_seed",("seed_role",role,index),0,0),16).hex()

def final_eval_commitment(index: int, seed_bytes: bytes) -> str:
    require_int(index,"index")
    if not isinstance(seed_bytes, bytes) or len(seed_bytes)!=16: raise V2Error("final seed must be 16 bytes")
    return hashlib.sha256(encode(("final_eval_seed_commitment", PROTOCOL_ID, index, seed_bytes))).hexdigest()

def verify_final_eval_commitment(index: int, seed_hex: str, commitment: str) -> bool:
    return final_eval_commitment(index, bytes.fromhex(seed_hex)) == commitment

def test_vectors() -> Dict[str,Any]:
    dense_ctx=_base_context("dense_full_rank_hash_v1","dense-n96-r48-p50",0,0,0,"dense_entry",("coord",0,0))
    sparse_ctx=_base_context("sparse_simple_biregular_hash_v1","sparse-reg-n120-r60-dv3-dc6",0,0,0,"sparse_edge_priority",("socket",0,0,0,0))
    row=BinaryRow((1,0,1,1,0,0,0,1,1)); mat=BinaryMatrix.from_rows([[1,0,1],[0,1,1]])
    return {"protocol_id":PROTOCOL_ID,"encodings":{"string":encode("é").hex(),"integer_zero":encode(0).hex(),"integer_255":encode(255).hex(),"bytes":encode(b"abc").hex(),"tuple":encode(("coord",1,2)).hex(),"list":encode(["a",2]).hex(),"binary_row":encode(row).hex(),"binary_matrix":encode(mat).hex()},"random_access":{"dense_entry_R0":R(dense_ctx,0).hex(),"expansion_48":expand(dense_ctx,48).hex(),"rejection_attempt_1":R(_base_context("dense_full_rank_hash_v1","dense-n96-r48-p50",0,0,1,"dense_entry",("coord",0,0)),0).hex(),"sparse_priority":R(sparse_ctx,0).hex(),"planted_coordinate":R(("v2_random_access",PROTOCOL_ID,"planted","planted-dense-n96-r48-w10",0,0,"construction_attempt",0,"planted_witness_coordinate",("coord1",0)),0).hex(),"seed_derivation_threshold_0":calibration_seed("threshold_fit_seed",0),"seed_derivation_tier_0":calibration_seed("tier_validation_seed",0)},"final_eval_dummy_commitments":[final_eval_commitment(i,s) for i,s in enumerate(DUMMY_FINAL_EVAL_SEEDS)]}

def self_test() -> Dict[str,Any]:
    tv=test_vectors(); assert tv["encodings"]["integer_zero"] == "490000000100"
    try: encode(True); raise AssertionError("bool accepted")
    except V2Error: pass
    feas={}
    for s in SPARSE_STRATA:
        H,a=generate_sparse(s,0,0); validate_matrix(H,s,SPARSE_STRATA[s][1],True,SMOKE_SMALL_CIRCUIT_CAP); feas[s]=a
    for s in PLANTED_SPARSE_STRATA:
        H,w,a=generate_planted_sparse(s,0,0); feas[s]=a
    recs=[build_record("dense-n96-r48-p50",0,0), build_record("dense-n96-r48-p50",0,1), build_record("sparse-reg-n120-r60-dv3-dc6",0,0), build_record("sparse-reg-n120-r60-dv3-dc6",0,1)]
    man=manifest(recs); validate_manifest(man)
    bad=copy.deepcopy(man); first=bad["records"][0]["H_rows"][0]; bad["records"][0]["H_rows"][0] = ("1" if first[0] == "0" else "0") + first[1:]; bad["candidate_manifest_digest"]=hashlib.sha256(canonical_json({k:v for k,v in bad.items() if k!="candidate_manifest_digest"})).hexdigest()
    try: validate_manifest(bad); raise AssertionError("tamper accepted")
    except V2Error: pass
    return {"status":"PASS","sparse_feasibility_attempts":feas,"test_vector_digest":hashlib.sha256(canonical_json(tv)).hexdigest()}

def _write_manifest(args):
    out=Path(args.output_dir)
    if not args.output_dir or out.exists() and not out.is_dir(): raise V2Error("--output-dir must be an explicit directory")
    out.mkdir(parents=True, exist_ok=True)
    recs=[]
    for s in (list(DENSE_STRATA)[:args.dense] + list(SPARSE_STRATA)[:args.sparse] + list(PLANTED_DENSE_STRATA)[:args.planted_dense] + list(PLANTED_SPARSE_STRATA)[:args.planted_sparse]):
        recs += [build_record(s,0,0), build_record(s,0,1)]
    payload=manifest(recs); p=out/"candidate_pool_manifest.json"; p.write_bytes(canonical_json(payload)); print(f"wrote {p} digest={payload['candidate_manifest_digest']}")

def main(argv: Optional[Sequence[str]]=None) -> int:
    ap=argparse.ArgumentParser(description="h-native-research-v2 candidate tooling")
    sub=ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("self-test")
    sub.add_parser("print-test-vectors")
    g=sub.add_parser("generate-candidate-pool"); g.add_argument("--output-dir", required=True); g.add_argument("--dense", type=int, default=1); g.add_argument("--sparse", type=int, default=1); g.add_argument("--planted-dense", type=int, default=0); g.add_argument("--planted-sparse", type=int, default=0)
    v=sub.add_parser("validate-candidate-pool"); v.add_argument("manifest")
    s=sub.add_parser("summary"); s.add_argument("manifest")
    try:
        args=ap.parse_args(argv)
        if args.cmd=="self-test": print(json.dumps(self_test(), sort_keys=True)); return 0
        if args.cmd=="print-test-vectors": print(json.dumps(test_vectors(), sort_keys=True, indent=2)); return 0
        if args.cmd=="generate-candidate-pool": _write_manifest(args); return 0
        if args.cmd=="validate-candidate-pool": validate_manifest(json.loads(Path(args.manifest).read_text())); print("PASS"); return 0
        if args.cmd=="summary":
            payload=json.loads(Path(args.manifest).read_text()); counts={}
            for r in payload.get("records",[]): counts[r["parameter_stratum_id"]]=counts.get(r["parameter_stratum_id"],0)+1
            print(json.dumps({"records":len(payload.get("records",[])),"strata":counts,"digest":payload.get("candidate_manifest_digest")}, sort_keys=True)); return 0
    except (V2Error, OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr); return 2
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
