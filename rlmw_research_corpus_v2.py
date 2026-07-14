"""PR-B candidate tooling for ``h-native-research-v2``.

This file is source-only tooling.  It does not freeze a v2 manifest, generated
corpus, thresholds, calibration outputs, final-evaluation secret seed bytes,
neural/RL experiments, or a stronger ISD baseline.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import math
import os
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

PROTOCOL_ID = "h-native-research-v2-candidate-v1"
GENERATOR_ID = "rlmw-research-corpus-v2-tooling-v2"
RANDOM_DOMAIN = "rlmw-h-native-research-v2-random-v1"
BASE_SEED = bytes.fromhex("726c6d772d682d6e61746976652d76322d63616e6469646174652d7631")
HARD_SMALL_CIRCUIT_CAP = 6
MAX_SPARSE_ATTEMPTS = 20000
DUMMY_FINAL_EVAL_SEEDS = tuple(bytes.fromhex(f"d00df00d0000000000000000{i:08x}") for i in range(8))
AUDIT_NOT_RUN = "AUDIT_NOT_RUN"
AUDIT_RESOURCE_LIMIT = "AUDIT_RESOURCE_LIMIT"
REJECTED_SMALL_CIRCUIT = "REJECTED_SMALL_CIRCUIT"
STRUCTURALLY_ACCEPTED = "STRUCTURALLY_ACCEPTED"
PREAUDIT = "PREAUDIT"

def construction_batch_id(index: int) -> str:
    require_uint(index, "construction_batch_index")
    return f"rlmw-v2-candidate-batch-{index:06d}"

class V2Error(ValueError):
    """Protocol/validation error for v2 candidate tooling."""

class AuditResourceLimitError(V2Error):
    """Small-circuit audit resource limit; fail closed without retry."""


def require_uint(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise V2Error(f"{name} must be a genuine nonnegative integer")
    return value


def _u32(value: int, name: str = "length") -> bytes:
    require_uint(value, name)
    if value > 0xFFFFFFFF:
        raise V2Error(f"{name} exceeds u32")
    return value.to_bytes(4, "big")


def _int_payload(value: int) -> bytes:
    require_uint(value, "integer")
    if value == 0:
        return b"\x00"
    return value.to_bytes((value.bit_length() + 7) // 8, "big")

@dataclass(frozen=True)
class BinaryRow:
    bits: Tuple[int, ...]
    def __post_init__(self) -> None:
        if not isinstance(self.bits, tuple):
            raise V2Error("BinaryRow bits must be a tuple")
        for bit in self.bits:
            if not isinstance(bit, int) or isinstance(bit, bool) or bit not in (0, 1):
                raise V2Error("binary row entries must be literal integer 0/1")
    def encode(self) -> bytes:
        packed = bytearray((len(self.bits) + 7) // 8)
        for i, bit in enumerate(self.bits):
            if bit:
                packed[i // 8] |= 1 << (7 - (i % 8))
        return b"R" + _u32(len(self.bits), "bit_len") + bytes(packed)

@dataclass(frozen=True)
class BinaryMatrix:
    rows: Tuple[BinaryRow, ...]
    ncols: int
    def __post_init__(self) -> None:
        require_uint(self.ncols, "ncols")
        if not isinstance(self.rows, tuple):
            raise V2Error("matrix rows must be a tuple")
        for row in self.rows:
            if not isinstance(row, BinaryRow):
                raise V2Error("matrix rows must be BinaryRow objects")
            if len(row.bits) != self.ncols:
                raise V2Error("inconsistent matrix row lengths")
    @classmethod
    def from_rows(cls, rows: Sequence[Sequence[int]]) -> "BinaryMatrix":
        if not isinstance(rows, (list, tuple)):
            raise V2Error("matrix rows must be a sequence")
        ncols: Optional[int] = None
        out: List[BinaryRow] = []
        for raw in rows:
            if not isinstance(raw, (list, tuple)):
                raise V2Error("each matrix row must be a sequence")
            bits: List[int] = []
            for bit in raw:
                if not isinstance(bit, int) or isinstance(bit, bool) or bit not in (0, 1):
                    raise V2Error("matrix entries must be literal integer 0/1")
                bits.append(bit)
            if ncols is None:
                ncols = len(bits)
            elif len(bits) != ncols:
                raise V2Error("inconsistent matrix row lengths")
            out.append(BinaryRow(tuple(bits)))
        return cls(tuple(out), 0 if ncols is None else ncols)
    @classmethod
    def from_row_strings(cls, row_strings: Sequence[str]) -> "BinaryMatrix":
        rows: List[List[int]] = []
        for s in row_strings:
            if not isinstance(s, str) or any(ch not in "01" for ch in s):
                raise V2Error("row strings must contain only 0/1")
            rows.append([1 if ch == "1" else 0 for ch in s])
        return cls.from_rows(rows)
    def as_lists(self) -> List[List[int]]:
        return [list(row.bits) for row in self.rows]
    def row_strings(self) -> List[str]:
        return ["".join(str(bit) for bit in row.bits) for row in self.rows]
    def encode(self) -> bytes:
        return b"M" + _u32(len(self.rows), "row_count") + _u32(self.ncols, "col_count") + b"".join(row.encode() for row in self.rows)


def encode(value: Any, *, allow_list: bool = True) -> bytes:
    if isinstance(value, bool):
        raise V2Error("booleans are not valid v2 integers")
    if isinstance(value, str):
        normalized = unicodedata.normalize("NFC", value)
        if normalized != value or "\x00" in value:
            raise V2Error("strings must be NFC and contain no NUL")
        payload = value.encode("utf-8")
        return b"S" + _u32(len(payload)) + payload
    if isinstance(value, int):
        payload = _int_payload(value)
        return b"I" + _u32(len(payload)) + payload
    if isinstance(value, bytes):
        return b"B" + _u32(len(value)) + value
    if isinstance(value, tuple):
        return b"T" + _u32(len(value), "item_count") + b"".join(encode(v, allow_list=allow_list) for v in value)
    if isinstance(value, list):
        if not allow_list:
            raise V2Error("lists are disallowed in this encoding context")
        return b"L" + _u32(len(value), "item_count") + b"".join(encode(v, allow_list=allow_list) for v in value)
    if isinstance(value, BinaryRow):
        return value.encode()
    if isinstance(value, BinaryMatrix):
        return value.encode()
    raise V2Error(f"unsupported encoded type {type(value).__name__}")


def make_context(family_id: str, parameter_stratum_id: str, construction_batch_index: int, case_slot: int, base_seed: bytes, construction_attempt: int, logical_identity: Any, draw_purpose: str) -> Tuple[Any, ...]:
    batch_id = construction_batch_id(construction_batch_index)
    require_uint(case_slot, "case_slot")
    require_uint(construction_attempt, "construction_attempt")
    if not isinstance(base_seed, bytes):
        raise V2Error("base_seed must be bytes")
    return (RANDOM_DOMAIN, PROTOCOL_ID, GENERATOR_ID, family_id, parameter_stratum_id, batch_id, case_slot, base_seed, construction_attempt, logical_identity, draw_purpose)


def R(context: Any, expansion_counter: int = 0) -> bytes:
    require_uint(expansion_counter, "expansion_counter")
    return hashlib.sha256(encode(context) + encode(expansion_counter)).digest()


def expand(context: Any, nbytes: int) -> bytes:
    require_uint(nbytes, "nbytes")
    out = bytearray()
    counter = 0
    while len(out) < nbytes:
        out.extend(R(context, counter))
        counter += 1
    return bytes(out[:nbytes])


def typed_digest(value: Any) -> str:
    return hashlib.sha256(encode(value)).hexdigest()

def json_digest(domain: str, value: Any) -> str:
    return hashlib.sha256(domain.encode("utf-8") + b"\0" + canonical_json(value)).hexdigest()

# Backward-compatible local alias for tests that recompute manifest digests.
def _digest(value: Any) -> str:
    return typed_digest(value)

def _priority(context: Any) -> bytes:
    return R(context, 0)


def _u64(context: Any) -> int:
    return int.from_bytes(R(context, 0)[:8], "big")


def gf2_rank(rows: Sequence[Sequence[int]]) -> int:
    if not rows:
        return 0
    ncols = len(rows[0])
    vals: List[int] = []
    for row in rows:
        if len(row) != ncols:
            raise V2Error("rank input has inconsistent row lengths")
        val = 0
        for bit in row:
            if bit not in (0, 1) or isinstance(bit, bool):
                raise V2Error("rank input must be binary")
            val = (val << 1) | bit
        vals.append(val)
    rank = 0
    for bit in range(ncols - 1, -1, -1):
        pivot = next((i for i in range(rank, len(vals)) if (vals[i] >> bit) & 1), None)
        if pivot is None:
            continue
        vals[rank], vals[pivot] = vals[pivot], vals[rank]
        for i in range(len(vals)):
            if i != rank and ((vals[i] >> bit) & 1):
                vals[i] ^= vals[rank]
        rank += 1
    return rank


def rref_matrix(H: BinaryMatrix) -> BinaryMatrix:
    mat = H.as_lists()
    if not mat:
        return BinaryMatrix.from_rows([])
    m, n = len(mat), H.ncols
    prow = 0
    for col in range(n):
        pivot = next((i for i in range(prow, m) if mat[i][col]), None)
        if pivot is None:
            continue
        mat[prow], mat[pivot] = mat[pivot], mat[prow]
        for i in range(m):
            if i != prow and mat[i][col]:
                mat[i] = [a ^ b for a, b in zip(mat[i], mat[prow])]
        prow += 1
        if prow == m:
            break
    return BinaryMatrix.from_rows([row for row in mat if any(row)])


def public_h_sha256(H: BinaryMatrix) -> str:
    return typed_digest(("public_h_hash", PROTOCOL_ID, len(H.rows), H.ncols, H))


def row_space_sha256(H: BinaryMatrix) -> str:
    rref = rref_matrix(H)
    return typed_digest(("row_space_hash", PROTOCOL_ID, len(rref.rows), H.ncols, rref))

DENSE_STRATA = {
    "dense-n96-r48-p50": (96, 48, 0.5, (0.46, 0.54)),
    "dense-n128-r64-p50": (128, 64, 0.5, (0.47, 0.53)),
    "dense-n160-r80-p50": (160, 80, 0.5, (0.475, 0.525)),
    "dense-n192-r96-p50": (192, 96, 0.5, (0.48, 0.52)),
}
SPARSE_STRATA = {
    "sparse-reg-n120-r60-dv3-dc6": (120, 60, 3, 6),
    "sparse-reg-n160-r80-dv3-dc6": (160, 80, 3, 6),
    "sparse-reg-n192-r96-dv3-dc6": (192, 96, 3, 6),
    "sparse-reg-n240-r120-dv3-dc6": (240, 120, 3, 6),
}
PLANTED_DENSE_STRATA = {"planted-dense-n96-r48-w10": (96, 48, 10), "planted-dense-n128-r64-w12": (128, 64, 12)}
PLANTED_SPARSE_STRATA = {"planted-sparse-n120-r60-w10": (120, 60, 10), "planted-sparse-n160-r80-w12": (160, 80, 12)}
CONTROL_STRATA = {
    "ctrl-hamming-m4": (15, 4, 3), "ctrl-hamming-m5": (31, 5, 3),
    "ctrl-ext-hamming-m4": (16, 5, 4), "ctrl-ext-hamming-m5": (32, 6, 4),
    "ctrl-rm1-m5": (32, 26, 16), "ctrl-rm1-m6": (64, 57, 32),
    "ctrl-random-k8-n24": (24, 16, None), "ctrl-random-k10-n32": (32, 22, None),
}


def _matrix_from_int_columns(columns: Sequence[int], r: int) -> BinaryMatrix:
    rows = [[0] * len(columns) for _ in range(r)]
    for j, col in enumerate(columns):
        for i in range(r):
            rows[i][j] = (col >> (r - 1 - i)) & 1
    return BinaryMatrix.from_rows(rows)


def _apply_row_ops_and_permutation(H: BinaryMatrix, stratum: str, slot: int) -> Tuple[BinaryMatrix, Dict[str, Any]]:
    if slot == 0:
        return H, {"variant": "canonical"}
    n, r = H.ncols, len(H.rows)
    keys = []
    for j in range(n):
        ctx = make_context("control_transform_v1", stratum, 0, slot, BASE_SEED, 0, ("coord1", j), "coordinate_permutation_key")
        keys.append((_priority(ctx), j))
    perm = [j for _, j in sorted(keys)]
    rows = H.as_lists()
    rows = [[row[j] for j in perm] for row in rows]
    # Deterministic invertible row operation: add earlier rows into later rows.
    for i in range(r):
        for j in range(i):
            ctx = make_context("control_transform_v1", stratum, 0, slot, BASE_SEED, 0, ("coord", i, j), "row_operation_entry")
            if R(ctx, 0)[0] & 1:
                rows[i] = [a ^ b for a, b in zip(rows[i], rows[j])]
    return BinaryMatrix.from_rows(rows), {"variant": "coordinate_permuted_row_operated", "coordinate_permutation": perm}


def generate_control(stratum: str, case_slot: int = 0) -> Tuple[BinaryMatrix, Dict[str, Any]]:
    n, r, distance = CONTROL_STRATA[stratum]
    if stratum.startswith("ctrl-hamming"):
        m = r
        H = _matrix_from_int_columns(list(range(1, 2 ** m)), m)
    elif stratum.startswith("ctrl-ext-hamming"):
        m = r - 1
        base = _matrix_from_int_columns(list(range(1, 2 ** m)), m).as_lists()
        rows = [row + [0] for row in base]
        rows.append([1] * (2 ** m))
        H = BinaryMatrix.from_rows(rows)
    elif stratum.startswith("ctrl-rm1"):
        m = 5 if "m5" in stratum else 6
        points = [tuple((x >> b) & 1 for b in range(m)) for x in range(2 ** m)]
        code_basis = [[1] * (2 ** m)] + [[p[b] for p in points] for b in range(m)]
        # Parity check is the nullspace of RM(1,m) generator via RREF equations.
        H = _nullspace_parity_check(BinaryMatrix.from_rows(code_basis), 2 ** m)
    else:
        H = _dense_control(stratum)
    H, prov = _apply_row_ops_and_permutation(H, stratum, case_slot)
    cert = replay_control_certificate(H, stratum)
    prov["certificate"] = cert
    if cert["status"] not in ("CERTIFIED_EXACT_DISTANCE", "CERTIFIED_THEOREM_DISTANCE"):
        raise V2Error("control certificate replay failed")
    return H, prov


def _nullspace_parity_check(G: BinaryMatrix, n: int) -> BinaryMatrix:
    rref = rref_matrix(G).as_lists()
    pivots = []
    for row in rref:
        pivots.append(next(i for i, b in enumerate(row) if b))
    free = [j for j in range(n) if j not in pivots]
    checks = []
    for f in free:
        x = [0] * n
        x[f] = 1
        for row, p in zip(rref, pivots):
            if row[f]:
                x[p] = 1
        checks.append(x)
    return BinaryMatrix.from_rows(checks)


def _dense_control(stratum: str) -> BinaryMatrix:
    n, r, _ = CONTROL_STRATA[stratum]
    for attempt in range(500):
        rows = []
        for i in range(r):
            row = []
            for j in range(n):
                ctx = make_context("control_dense_hash_v1", stratum, 0, 0, BASE_SEED, attempt, ("coord", i, j), "dense_entry")
                row.append(_u64(ctx) & 1)
            rows.append(row)
        H = BinaryMatrix.from_rows(rows)
        try:
            validate_matrix(H, stratum, expected_rank=r, small_circuit_cap=0, require_audit_pass=False)
            return H
        except V2Error:
            continue
    raise V2Error(f"failed to generate dense control {stratum}")


def generate_dense(stratum: str, construction_batch_id: int, case_slot: int, max_attempts: int = 20000, audit_cap: int = 0, profile: str = "preaudit", audit_resource_limit_entries: int = 2_000_000) -> Tuple[BinaryMatrix, int, Dict[str, Any]]:
    n, r, p, _ = DENSE_STRATA[stratum]
    threshold = int(math.floor(p * (1 << 64)))
    for attempt in range(require_uint(max_attempts, "max_attempts")):
        try:
            rows = []
            for i in range(r):
                row = []
                for j in range(n):
                    ctx = make_context("dense_full_rank_hash_v1", stratum, construction_batch_id, case_slot, BASE_SEED, attempt, ("coord", i, j), "dense_entry")
                    row.append(1 if _u64(ctx) < threshold else 0)
                rows.append(row)
            H = BinaryMatrix.from_rows(rows)
            validate_matrix(H, stratum, expected_rank=r, small_circuit_cap=0, require_audit_pass=False)
        except V2Error:
            continue
        ok, audit = _audit_gate(H, audit_cap, profile, audit_resource_limit_entries)
        if ok:
            return H, attempt, audit
    raise V2Error(f"dense stratum {stratum} exhausted {max_attempts} attempts")


def _would_close_four_cycle(v: int, c: int, check_neighbors: Dict[int, set], var_neighbors: Dict[int, set]) -> bool:
    for u in check_neighbors[c]:
        if var_neighbors[v] & var_neighbors[u]:
            return True
    return False


def _insert_edge(v: int, vs: int, c: int, cs: int, used_v: set, used_c: set, check_neighbors: Dict[int, set], var_neighbors: Dict[int, set], edges: List[Tuple[int, int]]) -> bool:
    if (v, vs) in used_v or (c, cs) in used_c:
        return False
    if c in var_neighbors[v]:
        return False
    if _would_close_four_cycle(v, c, check_neighbors, var_neighbors):
        return False
    used_v.add((v, vs)); used_c.add((c, cs)); check_neighbors[c].add(v); var_neighbors[v].add(c); edges.append((v, c))
    return True


def _socket_priorities(family: str, stratum: str, batch: int, slot: int, attempt: int, variables: Iterable[int], checks: Iterable[int], purpose: str, allowed_vs: Optional[Dict[int, List[int]]] = None, allowed_cs: Optional[Dict[int, List[int]]] = None) -> List[Tuple[bytes, int, int, int, int]]:
    pairs = []
    for v in variables:
        v_sockets = allowed_vs[v] if allowed_vs is not None else [0, 1, 2]
        for vs in v_sockets:
            for c in checks:
                c_sockets = allowed_cs[c] if allowed_cs is not None else [0, 1, 2, 3, 4, 5]
                for cs in c_sockets:
                    ident = ("socket", v, vs, c, cs)
                    ctx = make_context(family, stratum, batch, slot, BASE_SEED, attempt, ident, purpose)
                    pairs.append((_priority(ctx), v, vs, c, cs))
    pairs.sort(key=lambda x: (x[0], x[1], x[2], x[3], x[4]))
    return pairs


def _progressive_socket_matrix(stratum: str, family: str, n: int, r: int, batch: int, slot: int, attempt: int, pre_edges: Sequence[Tuple[int, int, int, int]] = (), purpose: str = "sparse_edge_priority", variables: Optional[Iterable[int]] = None) -> Optional[BinaryMatrix]:
    used_v: set = set(); used_c: set = set(); edges: List[Tuple[int, int]] = []
    check_neighbors = {c: set() for c in range(r)}
    var_neighbors = {v: set() for v in range(n)}
    for v, vs, c, cs in pre_edges:
        if not _insert_edge(v, vs, c, cs, used_v, used_c, check_neighbors, var_neighbors, edges):
            return None
    remaining_vs = {v: [s for s in range(3) if (v, s) not in used_v] for v in range(n)}
    remaining_cs = {c: [s for s in range(6) if (c, s) not in used_c] for c in range(r)}
    var_iter = list(range(n) if variables is None else variables)
    pairs = _socket_priorities(family, stratum, batch, slot, attempt, var_iter, range(r), purpose, remaining_vs, remaining_cs)
    target_edges = n * 3
    for _, v, vs, c, cs in pairs:
        if _insert_edge(v, vs, c, cs, used_v, used_c, check_neighbors, var_neighbors, edges) and len(edges) == target_edges:
            break
    if len(used_v) != n * 3 or len(used_c) != r * 6 or len(edges) != target_edges:
        return None
    return _edges_to_matrix(edges, r, n)


def _edges_to_matrix(edges: Iterable[Tuple[int, int]], r: int, n: int) -> BinaryMatrix:
    rows = [[0] * n for _ in range(r)]
    for v, c in edges:
        rows[c][v] ^= 1
    return BinaryMatrix.from_rows(rows)


def generate_sparse(stratum: str, construction_batch_id: int, case_slot: int, max_attempts: int = MAX_SPARSE_ATTEMPTS, audit_cap: int = 0, profile: str = "preaudit", audit_resource_limit_entries: int = 2_000_000) -> Tuple[BinaryMatrix, int, Dict[str, Any]]:
    n, r, dv, dc = SPARSE_STRATA[stratum]
    if dv != 3 or dc != 6 or n * dv != r * dc:
        raise V2Error("unsupported sparse degree contract")
    for attempt in range(require_uint(max_attempts, "max_attempts")):
        try:
            H = _progressive_socket_matrix(stratum, "sparse_simple_biregular_hash_v1", n, r, construction_batch_id, case_slot, attempt)
            if H is None:
                continue
            validate_matrix(H, stratum, expected_rank=r, small_circuit_cap=0, require_audit_pass=False)
        except V2Error:
            continue
        ok, audit = _audit_gate(H, audit_cap, profile, audit_resource_limit_entries)
        if ok:
            return H, attempt, audit
    raise V2Error(f"{stratum} failed within max_attempts={max_attempts}")


def _ranked_indices(n: int, family: str, stratum: str, batch: int, slot: int, attempt: int, purpose: str, kind: str = "coord1") -> List[int]:
    scored = []
    for i in range(n):
        ctx = make_context(family, stratum, batch, slot, BASE_SEED, attempt, (kind, i), purpose)
        scored.append((_priority(ctx), i))
    return [i for _, i in sorted(scored)]


def _witness_support(stratum: str, n: int, weight: int, batch: int, slot: int, attempt: int) -> List[int]:
    return sorted(_ranked_indices(n, "planted_witness_v1", stratum, batch, slot, attempt, "planted_witness_coordinate")[:weight])


def generate_planted_dense(stratum: str, construction_batch_id: int, case_slot: int, max_attempts: int = 20000, audit_cap: int = 0, profile: str = "preaudit", audit_resource_limit_entries: int = 2_000_000) -> Tuple[BinaryMatrix, List[int], int, Dict[str, Any], Dict[str, Any]]:
    n, r, wp = PLANTED_DENSE_STRATA[stratum]
    for attempt in range(max_attempts):
        try:
            supp = _witness_support(stratum, n, wp, construction_batch_id, 0, attempt)
            pivot = max(supp)
            rows = []
            for i in range(r):
                row = []
                parity = 0
                for j in range(n):
                    if j == pivot:
                        row.append(0); continue
                    ctx = make_context("planted_dense_orthogonal_v1", stratum, construction_batch_id, 0, BASE_SEED, attempt, ("coord", i, j), "planted_orthogonal_row_free_bit")
                    bit = R(ctx, 0)[0] & 1
                    row.append(bit)
                    if j in supp:
                        parity ^= bit
                row[pivot] = parity
                rows.append(row)
            H = BinaryMatrix.from_rows(rows)
            validate_planted_witness(H, supp, wp)
            validate_matrix(H, stratum, expected_rank=r, small_circuit_cap=0, require_audit_pass=False)
        except V2Error:
            continue
        ok, audit = _audit_gate(H, audit_cap, profile, audit_resource_limit_entries)
        if not ok:
            continue
        try:
            base_digest = public_h_sha256(H)
            base_support = list(supp)
            H2, transform = _apply_planted_transform(H, stratum, construction_batch_id, case_slot, attempt)
            transform["base_candidate_digest"] = base_digest
            transform["base_witness_support"] = base_support
            supp2 = [transform["coordinate_permutation_inverse"][i] for i in supp] if transform.get("coordinate_permutation_inverse") else supp
            validate_planted_witness(H2, supp2, wp)
            transform["base_audit"] = audit
            transform["base_accepted_attempt"] = attempt
            return H2, sorted(supp2), attempt, transform, audit
        except V2Error:
            continue
    raise V2Error(f"planted dense {stratum} exhausted attempts")


def _apply_planted_transform(H: BinaryMatrix, stratum: str, batch: int, slot: int, attempt: int) -> Tuple[BinaryMatrix, Dict[str, Any]]:
    if slot == 0:
        return H, {"variant": "base", "coordinate_permutation_inverse": None}
    n, r = H.ncols, len(H.rows)
    keys = []
    for j in range(n):
        ctx = make_context("planted_transform_v1", stratum, batch, slot, BASE_SEED, attempt, ("coord1", j), "coordinate_permutation_key")
        keys.append((_priority(ctx), j))
    perm = [j for _, j in sorted(keys)]
    inv = [0] * n
    for new, old in enumerate(perm):
        inv[old] = new
    rows = [[row.bits[old] for old in perm] for row in H.rows]
    row_operations = []
    if stratum not in PLANTED_SPARSE_STRATA:
        for i in range(r):
            for j in range(i):
                ctx = make_context("planted_transform_v1", stratum, batch, slot, BASE_SEED, attempt, ("coord", i, j), "row_operation_entry")
                if R(ctx, 0)[0] & 1:
                    rows[i] = [a ^ b for a, b in zip(rows[i], rows[j])]
                    row_operations.append([i, j])
    variant = "coordinate_permuted" if stratum in PLANTED_SPARSE_STRATA else "coordinate_permuted_row_operated"
    return BinaryMatrix.from_rows(rows), {"variant": variant, "coordinate_permutation": perm, "coordinate_permutation_inverse": inv, "row_operations": row_operations}


def generate_planted_sparse(stratum: str, construction_batch_id: int, case_slot: int, max_attempts: int = MAX_SPARSE_ATTEMPTS, audit_cap: int = 0, profile: str = "preaudit", audit_resource_limit_entries: int = 2_000_000) -> Tuple[BinaryMatrix, List[int], int, Dict[str, Any], Dict[str, Any]]:
    n, r, wp = PLANTED_SPARSE_STRATA[stratum]
    for attempt in range(max_attempts):
        try:
            support = _witness_support(stratum, n, wp, construction_batch_id, 0, attempt)
            checks_needed = (3 * wp) // 2
            chosen_checks = _ranked_indices(r, "planted_sparse_orthogonal_v1", stratum, construction_batch_id, 0, attempt, "planted_sparse_witness_check_priority")[:checks_needed]
            witness_sockets = [(v, vs) for v in support for vs in range(3)]
            witness_sockets.sort(key=lambda x: (_priority(make_context("planted_sparse_orthogonal_v1", stratum, construction_batch_id, 0, BASE_SEED, attempt, ("socket", x[0], x[1]), "planted_sparse_witness_socket_priority")), x))
            check_sockets: List[Tuple[int, int]] = []
            for c in chosen_checks:
                sockets = list(range(6))
                sockets.sort(key=lambda cs: (_priority(make_context("planted_sparse_orthogonal_v1", stratum, construction_batch_id, 0, BASE_SEED, attempt, ("socket", c, cs), "planted_sparse_witness_check_socket_priority")), cs))
                check_sockets.extend([(c, sockets[0]), (c, sockets[1])])
            pre_edges = [(v, vs, c, cs) for (v, vs), (c, cs) in zip(witness_sockets, check_sockets)]
            nonwitness = [v for v in range(n) if v not in set(support)]
            H = _progressive_socket_matrix(stratum, "planted_sparse_orthogonal_v1", n, r, construction_batch_id, 0, attempt, pre_edges, "planted_sparse_nonwitness_edge_priority", variables=nonwitness)
            if H is None:
                continue
            validate_planted_witness(H, support, wp)
            validate_matrix(H, stratum, expected_rank=r, small_circuit_cap=0, require_audit_pass=False)
        except V2Error:
            continue
        ok, audit = _audit_gate(H, audit_cap, profile, audit_resource_limit_entries)
        if not ok:
            continue
        try:
            base_digest = public_h_sha256(H)
            base_support = list(support)
            H2, transform = _apply_planted_transform(H, stratum, construction_batch_id, case_slot, attempt)
            transform["base_candidate_digest"] = base_digest
            transform["base_witness_support"] = base_support
            support2 = [transform["coordinate_permutation_inverse"][i] for i in support] if transform.get("coordinate_permutation_inverse") else support
            validate_planted_witness(H2, support2, wp)
            transform["base_audit"] = audit
            transform["base_accepted_attempt"] = attempt
            return H2, sorted(support2), attempt, {**transform, "witness_check_set": chosen_checks}, audit
        except V2Error:
            continue
    raise V2Error(f"{stratum} failed within max_attempts={max_attempts}")


def validate_planted_witness(H: BinaryMatrix, support: Sequence[int], expected_weight: Optional[int] = None) -> None:
    if not isinstance(support, (list, tuple)):
        raise V2Error("planted support must be a list/tuple")
    if any(not isinstance(i, int) or isinstance(i, bool) for i in support):
        raise V2Error("planted support entries must be integers")
    if len(set(support)) != len(support) or any(i < 0 or i >= H.ncols for i in support):
        raise V2Error("bad planted support")
    if expected_weight is not None and len(support) != expected_weight:
        raise V2Error("planted witness weight mismatch")
    for row in H.as_lists():
        if sum(row[i] for i in support) % 2:
            raise V2Error("planted witness does not satisfy Hc=0")


def _column_tuples(H: BinaryMatrix) -> List[Tuple[int, ...]]:
    rows = H.as_lists()
    return [tuple(row[j] for row in rows) for j in range(H.ncols)]


def _has_four_cycle(H: BinaryMatrix) -> bool:
    seen: set = set()
    rows = H.as_lists()
    for row in rows:
        cols = [j for j, bit in enumerate(row) if bit]
        for a, b in itertools.combinations(cols, 2):
            if (a, b) in seen:
                return True
            seen.add((a, b))
    return False


def validate_matrix(H: BinaryMatrix, stratum: str, expected_rank: Optional[int] = None, small_circuit_cap: int = 0, require_audit_pass: bool = False, audit_resource_limit_entries: int = 2_000_000) -> Dict[str, Any]:
    rows = H.as_lists(); m = len(rows); n = H.ncols
    if any(bit not in (0, 1) or isinstance(bit, bool) for row in rows for bit in row):
        raise V2Error("non-binary entry")
    cols = _column_tuples(H)
    if any(not any(col) for col in cols):
        raise V2Error("zero column")
    if len(set(cols)) != len(cols):
        raise V2Error("repeated column")
    rank = gf2_rank(rows)
    if expected_rank is not None and rank != expected_rank:
        raise V2Error("rank mismatch")
    if stratum in DENSE_STRATA:
        n0, r0, _, (lo, hi) = DENSE_STRATA[stratum]
        if (n, m) != (n0, r0):
            raise V2Error("dense dimensions mismatch")
        density = sum(map(sum, rows)) / (m * n)
        if not (lo <= density <= hi):
            raise V2Error("density outside contract")
    if stratum in SPARSE_STRATA or stratum in PLANTED_SPARSE_STRATA:
        if stratum in SPARSE_STRATA:
            n0, r0, dv, dc = SPARSE_STRATA[stratum]
        else:
            n0, r0, _ = PLANTED_SPARSE_STRATA[stratum]; dv, dc = 3, 6
        if (n, m) != (n0, r0):
            raise V2Error("sparse dimensions mismatch")
        if [sum(col) for col in cols] != [dv] * n:
            raise V2Error("variable degree mismatch")
        if [sum(row) for row in rows] != [dc] * m:
            raise V2Error("check degree mismatch")
        if _has_four_cycle(H):
            raise V2Error("four-cycle detected")
    audit = small_circuit_audit(H, small_circuit_cap, resource_limit_entries=audit_resource_limit_entries) if small_circuit_cap else {"status": "NOT_RUN", "cap": 0}
    if require_audit_pass and audit["status"] != "PASS":
        raise V2Error("required small-circuit audit did not pass")
    return {"rank": rank, "small_circuit": audit}


def _xor_cols(cols: Sequence[Tuple[int, ...]], indices: Tuple[int, ...]) -> Tuple[int, ...]:
    if not indices:
        return tuple([0] * len(cols[0]))
    acc = [0] * len(cols[0])
    for j in indices:
        acc = [a ^ b for a, b in zip(acc, cols[j])]
    return tuple(acc)


def small_circuit_audit(H: BinaryMatrix, cap: int, resource_limit_entries: int = 2_000_000) -> Dict[str, Any]:
    require_uint(cap, "small_circuit_cap")
    if cap > HARD_SMALL_CIRCUIT_CAP:
        return {"status": "RESOURCE_LIMIT", "cap": cap, "reason": "cap exceeds hard limit"}
    if cap == 0:
        return {"status": "PASS", "cap": 0}
    n = H.ncols
    half = min(3, cap)
    entries = sum(math.comb(n, w) for w in range(0, half + 1))
    if entries > resource_limit_entries:
        return {"status": "RESOURCE_LIMIT", "cap": cap, "estimated_entries": entries}
    cols = _column_tuples(H)
    seen: Dict[Tuple[int, ...], Tuple[int, ...]] = {tuple([0] * len(H.rows)): tuple()}
    for w in range(1, half + 1):
        for comb in itertools.combinations(range(n), w):
            x = _xor_cols(cols, comb)
            if not any(x):
                return {"status": "FOUND_WITNESS", "cap": cap, "weight": w, "columns": list(comb)}
            prev = seen.get(x)
            if prev is not None and set(prev).isdisjoint(comb) and len(prev) + len(comb) <= cap:
                return {"status": "FOUND_WITNESS", "cap": cap, "weight": len(prev) + len(comb), "columns": sorted(prev + comb)}
            if prev is None or len(comb) < len(prev):
                seen[x] = comb
    return {"status": "PASS", "cap": cap, "estimated_entries": entries}


def kernel_basis(H: BinaryMatrix) -> List[List[int]]:
    mat = H.as_lists()
    m, n = len(mat), H.ncols
    prow = 0
    pivots: List[int] = []
    for col in range(n):
        pivot = next((i for i in range(prow, m) if mat[i][col]), None)
        if pivot is None:
            continue
        mat[prow], mat[pivot] = mat[pivot], mat[prow]
        for i in range(m):
            if i != prow and mat[i][col]:
                mat[i] = [a ^ b for a, b in zip(mat[i], mat[prow])]
        pivots.append(col)
        prow += 1
        if prow == m:
            break
    free = [j for j in range(n) if j not in pivots]
    basis: List[List[int]] = []
    for f in free:
        x = [0] * n
        x[f] = 1
        for row_i, pcol in enumerate(pivots):
            if mat[row_i][f]:
                x[pcol] = 1
        basis.append(x)
    return basis

def _syndrome_zero(H: BinaryMatrix, c: Sequence[int]) -> bool:
    return all(sum(bit & cj for bit, cj in zip(row.bits, c)) % 2 == 0 for row in H.rows)

def exact_kernel_distance_certificate(H: BinaryMatrix, max_k: int = 12) -> Dict[str, Any]:
    basis = kernel_basis(H)
    k = len(basis)
    if k > max_k:
        return {"status": "RESOURCE_LIMIT", "kernel_dimension": k, "max_k": max_k}
    best_weight = H.ncols + 1
    best: Optional[List[int]] = None
    prev_gray = 0
    c = [0] * H.ncols
    count = 0
    for t in range(1, 1 << k):
        gray = t ^ (t >> 1)
        flip = (gray ^ prev_gray).bit_length() - 1
        c = [a ^ b for a, b in zip(c, basis[flip])]
        prev_gray = gray
        count += 1
        if not _syndrome_zero(H, c):
            raise V2Error("kernel enumeration produced non-codeword")
        wt = sum(c)
        if wt and (wt < best_weight or (wt == best_weight and [i for i,b in enumerate(c) if b] < (best or []))):
            best_weight = wt
            best = [i for i, b in enumerate(c) if b]
    if best is None:
        raise V2Error("nontrivial random control has no witness")
    return {"status": "CERTIFIED_EXACT_DISTANCE", "kernel_dimension": k, "enumerated_nonzero_coefficients": count, "finite_lower_bound": best_weight, "finite_upper_bound": best_weight, "exact_distance": best_weight, "canonical_witness_support": best}

def replay_control_certificate(H: BinaryMatrix, stratum: str) -> Dict[str, Any]:
    expected = CONTROL_STRATA[stratum][2]
    if expected is None:
        cert = exact_kernel_distance_certificate(H, max_k=12)
        if cert["status"] != "CERTIFIED_EXACT_DISTANCE":
            raise V2Error("random control exact enumeration did not complete")
        return cert
    if stratum.startswith("ctrl-rm1"):
        return {"status": "CERTIFIED_THEOREM_DISTANCE", "theorem_id": stratum.replace("ctrl-", "theorem-"), "exact_distance": expected, "structural_rank": gf2_rank(H.as_lists())}
    audit = small_circuit_audit(H, max(0, expected - 1), resource_limit_entries=500_000)
    if audit["status"] != "PASS":
        return {"status": "CERTIFICATE_FAILED", "audit": audit}
    return {"status": "CERTIFIED_THEOREM_DISTANCE", "theorem_id": stratum.replace("ctrl-", "theorem-"), "exact_distance": expected, "structural_replay": audit}


def _family_for(stratum: str) -> str:
    if stratum in DENSE_STRATA: return "dense_full_rank_hash_v1"
    if stratum in SPARSE_STRATA: return "sparse_simple_biregular_hash_v1"
    if stratum in PLANTED_DENSE_STRATA: return "planted_dense_orthogonal_v1"
    if stratum in PLANTED_SPARSE_STRATA: return "planted_sparse_orthogonal_v1"
    if stratum in CONTROL_STRATA: return "exact_control_v1"
    raise V2Error(f"unknown stratum {stratum}")


def case_id(family: str, stratum: str, batch: int | str, slot: int) -> str:
    bid = construction_batch_id(batch) if isinstance(batch, int) and not isinstance(batch, bool) else batch
    if not isinstance(bid, str): raise V2Error("construction_batch_id must be string")
    return typed_digest(("case_id", PROTOCOL_ID, GENERATOR_ID, family, stratum, bid, slot))[:32]


def lineage_group_id(family: str, stratum: str, batch: int | str) -> str:
    bid = construction_batch_id(batch) if isinstance(batch, int) and not isinstance(batch, bool) else batch
    if not isinstance(bid, str): raise V2Error("construction_batch_id must be string")
    return typed_digest(("lineage_group_id", PROTOCOL_ID, family, stratum, bid))


def split_key(stratum: str, lineage: str) -> str:
    return typed_digest(("split_key", PROTOCOL_ID, stratum, lineage))


def config_digest() -> str:

    dense = tuple((k, str(v[0]), str(v[1]), str(v[2]), str(v[3][0]), str(v[3][1])) for k, v in sorted(DENSE_STRATA.items()))
    sparse = tuple((k,) + tuple(str(x) for x in v) for k, v in sorted(SPARSE_STRATA.items()))
    planted_d = tuple((k,) + tuple(str(x) for x in v) for k, v in sorted(PLANTED_DENSE_STRATA.items()))
    planted_s = tuple((k,) + tuple(str(x) for x in v) for k, v in sorted(PLANTED_SPARSE_STRATA.items()))
    controls = tuple((k,) + tuple("none" if x is None else str(x) for x in v) for k, v in sorted(CONTROL_STRATA.items()))
    return typed_digest(("configuration_digest", PROTOCOL_ID, GENERATOR_ID, BASE_SEED, dense, sparse, planted_d, planted_s, controls))


def structural_status_from_audit(audit: Mapping[str, Any], audit_cap: int, profile: str) -> Tuple[str, bool]:
    if audit_cap == 0 or audit.get("status") == "NOT_RUN":
        return AUDIT_NOT_RUN, False
    if audit.get("status") == "RESOURCE_LIMIT":
        return AUDIT_RESOURCE_LIMIT, False
    if audit.get("status") == "FOUND_WITNESS":
        return REJECTED_SMALL_CIRCUIT, False
    if audit.get("status") == "PASS" and audit_cap >= HARD_SMALL_CIRCUIT_CAP:
        return STRUCTURALLY_ACCEPTED, profile == "accepted"
    return PREAUDIT, False

def _is_audit_applicable(stratum: str) -> bool:
    return stratum in DENSE_STRATA or stratum in SPARSE_STRATA or stratum in PLANTED_DENSE_STRATA or stratum in PLANTED_SPARSE_STRATA

def _audit_gate(H: BinaryMatrix, audit_cap: int, profile: str, audit_resource_limit_entries: int) -> Tuple[bool, Dict[str, Any]]:
    if audit_cap == 0:
        return True, {"status": "NOT_RUN", "cap": 0}
    audit = small_circuit_audit(H, audit_cap, resource_limit_entries=audit_resource_limit_entries)
    if audit["status"] == "PASS":
        return True, audit
    if audit["status"] == "FOUND_WITNESS":
        return False, audit
    if audit["status"] == "RESOURCE_LIMIT":
        if profile == "accepted":
            raise AuditResourceLimitError("accepted generation hit small-circuit audit resource limit")
        return True, audit
    raise V2Error(f"unknown audit status {audit.get('status')}")

def build_record(stratum: str, batch: int, slot: int, audit_cap: int = 0, profile: str = "preaudit", audit_resource_limit_entries: int = 2_000_000) -> Dict[str, Any]:
    family = _family_for(stratum)
    witness = None; provenance: Dict[str, Any] = {}; attempt = 0; audit_override: Optional[Dict[str, Any]] = None
    if stratum in DENSE_STRATA:
        H, attempt, audit_override = generate_dense(stratum, batch, slot, audit_cap=audit_cap, profile=profile, audit_resource_limit_entries=audit_resource_limit_entries)
        expected_rank = DENSE_STRATA[stratum][1]
    elif stratum in SPARSE_STRATA:
        H, attempt, audit_override = generate_sparse(stratum, batch, slot, audit_cap=audit_cap, profile=profile, audit_resource_limit_entries=audit_resource_limit_entries)
        expected_rank = SPARSE_STRATA[stratum][1]
    elif stratum in PLANTED_DENSE_STRATA:
        H, witness, attempt, provenance, audit_override = generate_planted_dense(stratum, batch, slot, audit_cap=audit_cap, profile=profile, audit_resource_limit_entries=audit_resource_limit_entries)
        expected_rank = PLANTED_DENSE_STRATA[stratum][1]
    elif stratum in PLANTED_SPARSE_STRATA:
        H, witness, attempt, provenance, audit_override = generate_planted_sparse(stratum, batch, slot, audit_cap=audit_cap, profile=profile, audit_resource_limit_entries=audit_resource_limit_entries)
        expected_rank = PLANTED_SPARSE_STRATA[stratum][1]
    elif stratum in CONTROL_STRATA:
        H, provenance = generate_control(stratum, slot)
        expected_rank = CONTROL_STRATA[stratum][1]
    else:
        raise V2Error(f"unknown stratum {stratum}")
    if witness is not None:
        validate_planted_witness(H, witness)
    validation = validate_matrix(H, stratum, expected_rank, 0, require_audit_pass=False, audit_resource_limit_entries=audit_resource_limit_entries)
    if audit_override is not None:
        validation["small_circuit"] = audit_override
    elif audit_cap:
        validation["small_circuit"] = small_circuit_audit(H, audit_cap, resource_limit_entries=audit_resource_limit_entries)
    if _is_audit_applicable(stratum):
        structural_status, calibration_ready = structural_status_from_audit(validation["small_circuit"], audit_cap, profile)
        if profile == "accepted" and structural_status != STRUCTURALLY_ACCEPTED:
            raise V2Error(f"candidate is not structurally accepted: {structural_status}")
    else:
        structural_status, calibration_ready = STRUCTURALLY_ACCEPTED, profile == "accepted"
    lineage = lineage_group_id(family, stratum, batch)
    protected = {
        "protocol_id": PROTOCOL_ID, "generator_id": GENERATOR_ID, "source_commit": _source_commit(),
        "configuration_digest": config_digest(), "case_id": case_id(family, stratum, batch, slot),
        "family_id": family, "parameter_stratum_id": stratum, "construction_batch_id": construction_batch_id(batch),
        "construction_batch_index": batch, "case_slot": slot, "base_seed_sha256": hashlib.sha256(BASE_SEED).hexdigest(),
        "construction_attempt": attempt, "lineage_group_id": lineage, "n": H.ncols, "r": len(H.rows),
        "H_rows": H.row_strings(), "public_h_sha256": public_h_sha256(H), "row_space_sha256": row_space_sha256(H),
        "validation": validation, "structural_status": structural_status, "generation_profile": profile, "calibration_ready": calibration_ready, "audit_resource_limit_entries": audit_resource_limit_entries, "evaluator_only_provenance": provenance,
    }
    if witness is not None:
        protected["evaluator_only_provenance"]["planted_witness_support"] = list(witness)
    protected["protected_record_sha256"] = json_digest("protected_record", protected_without_digest(protected))
    return protected


def protected_without_digest(record: Mapping[str, Any]) -> Dict[str, Any]:
    return {k: copy.deepcopy(v) for k, v in record.items() if k not in ("protected_record_sha256", "split")}


def _source_commit() -> str:
    try:
        import subprocess
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "UNKNOWN"


def _targets_for_stratum(stratum: str, count: int) -> Dict[str, int]:
    if stratum in DENSE_STRATA or stratum in SPARSE_STRATA:
        if count != 18: raise V2Error(f"{stratum} requires 18 records")
        return {"train": 10, "validation": 4, "test": 4}
    if stratum in PLANTED_DENSE_STRATA or stratum in PLANTED_SPARSE_STRATA:
        if count != 8: raise V2Error(f"{stratum} requires 8 records")
        return {"train": 4, "validation": 2, "test": 2}
    raise V2Error("controls are assigned as complete strata")


def assign_splits(records: List[Dict[str, Any]], full: bool = False) -> None:
    by_stratum: Dict[str, List[Dict[str, Any]]] = {}
    for rec in records:
        by_stratum.setdefault(rec["parameter_stratum_id"], []).append(rec)
    control_order = sorted([s for s in by_stratum if s in CONTROL_STRATA], key=lambda s: split_key(s, by_stratum[s][0]["lineage_group_id"]))
    control_split = {}
    for idx, s in enumerate(control_order):
        control_split[s] = "train" if idx < 4 else "validation" if idx < 6 else "test"
    for stratum, recs in by_stratum.items():
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for rec in recs:
            groups.setdefault(rec["lineage_group_id"], []).append(rec)
        if any(len(g) != 2 for g in groups.values()):
            raise V2Error("every lineage group must contain exactly two records")
        if stratum in CONTROL_STRATA:
            if len(recs) != 2 and full: raise V2Error("control stratum must have 2 records")
            split = control_split.get(stratum, "train")
            for rec in recs: rec["split"] = split
            continue
        if full:
            targets = _targets_for_stratum(stratum, len(recs))
        else:
            targets = {"train": len(recs), "validation": 0, "test": 0}
        ordered = sorted(groups.items(), key=lambda kv: split_key(stratum, kv[0]))
        counts = {k: 0 for k in targets}
        for _, grecs in ordered:
            split = next((name for name, target in targets.items() if counts[name] < target), None)
            if split is None:
                raise V2Error("split target overflow")
            if counts[split] + 2 > targets[split]:
                raise V2Error("split target not divisible by lineage group size")
            for rec in grecs: rec["split"] = split
            counts[split] += 2
        if full and counts != targets:
            raise V2Error("split target mismatch")
    if full:
        totals = {"train": 0, "validation": 0, "test": 0}
        for rec in records: totals[rec["split"]] += 1
        if totals != {"train": 104, "validation": 44, "test": 44}:
            raise V2Error(f"overall split mismatch {totals}")


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"


def build_manifest(records: List[Dict[str, Any]], full: bool = False, profile: str = "preaudit") -> Dict[str, Any]:
    recs = copy.deepcopy(records)
    assign_splits(recs, full=full)
    calibration_ready = profile == "accepted" and full and all(r.get("calibration_ready") is True and r.get("structural_status") == STRUCTURALLY_ACCEPTED for r in recs)
    payload = {"protocol_id": PROTOCOL_ID, "generator_id": GENERATOR_ID, "manifest_kind": "candidate_pool_manifest", "generation_profile": profile, "calibration_ready": calibration_ready, "is_frozen_v2_manifest": False, "source_commit": _source_commit(), "configuration_digest": config_digest(), "records": recs}
    payload["candidate_manifest_digest"] = json_digest("candidate_manifest_digest", {k: v for k, v in payload.items() if k != "candidate_manifest_digest"})
    return payload


def validate_manifest(payload: Mapping[str, Any], full: bool = False) -> None:
    canonical_json(payload)  # rejects NaN/Infinity
    if payload.get("protocol_id") != PROTOCOL_ID or payload.get("generator_id") != GENERATOR_ID:
        raise V2Error("bad protocol/generator")
    if payload.get("manifest_kind") != "candidate_pool_manifest":
        raise V2Error("bad manifest kind")
    if payload.get("generation_profile") not in ("preaudit", "accepted"):
        raise V2Error("bad generation profile")
    if payload.get("configuration_digest") != config_digest():
        raise V2Error("top-level configuration digest mismatch")
    if payload.get("is_frozen_v2_manifest") is not False:
        raise V2Error("candidate pool must not be marked frozen")
    expected_digest = json_digest("candidate_manifest_digest", {k: v for k, v in payload.items() if k != "candidate_manifest_digest"})
    if payload.get("candidate_manifest_digest") != expected_digest:
        raise V2Error("candidate manifest digest mismatch")
    records = copy.deepcopy(payload.get("records"))
    if not isinstance(records, list):
        raise V2Error("records must be a list")
    allowed_top = {"protocol_id", "generator_id", "manifest_kind", "generation_profile", "calibration_ready", "is_frozen_v2_manifest", "source_commit", "configuration_digest", "records", "candidate_manifest_digest"}
    if set(payload.keys()) - allowed_top:
        raise V2Error("unknown manifest fields")
    allowed_record = {"protocol_id", "generator_id", "source_commit", "configuration_digest", "case_id", "family_id", "parameter_stratum_id", "construction_batch_id", "construction_batch_index", "case_slot", "base_seed_sha256", "construction_attempt", "lineage_group_id", "n", "r", "H_rows", "public_h_sha256", "row_space_sha256", "validation", "structural_status", "generation_profile", "calibration_ready", "audit_resource_limit_entries", "evaluator_only_provenance", "protected_record_sha256", "split"}
    for rec in records:
        if not isinstance(rec, dict):
            raise V2Error("record must be object")
        if set(rec.keys()) - allowed_record:
            raise V2Error("unknown record fields")
    assign_splits(records, full=full)
    raw_hashes: Dict[str, str] = {}; row_hashes: Dict[str, str] = {}; lineages: Dict[str, List[Dict[str, Any]]] = {}
    for expected, rec in zip(records, payload["records"]):
        if rec.get("split") != expected.get("split"):
            raise V2Error("deterministic split mismatch")
        H = BinaryMatrix.from_row_strings(rec["H_rows"])
        family = _family_for(rec["parameter_stratum_id"])
        if rec.get("generation_profile") != payload.get("generation_profile"):
            raise V2Error("record generation profile mismatch")
        if rec["family_id"] != family: raise V2Error("family mismatch")
        if rec["case_id"] != case_id(family, rec["parameter_stratum_id"], rec.get("construction_batch_id"), rec["case_slot"]): raise V2Error("case ID mismatch")
        if rec["lineage_group_id"] != lineage_group_id(family, rec["parameter_stratum_id"], rec["construction_batch_id"]): raise V2Error("lineage mismatch")
        if rec["configuration_digest"] != config_digest(): raise V2Error("configuration digest mismatch")
        if rec["public_h_sha256"] != public_h_sha256(H) or rec["row_space_sha256"] != row_space_sha256(H): raise V2Error("hash mismatch")
        if rec["public_h_sha256"] in raw_hashes and raw_hashes[rec["public_h_sha256"]] != rec["lineage_group_id"]: raise V2Error("raw-H duplicate across lineages")
        if rec["row_space_sha256"] in row_hashes and row_hashes[rec["row_space_sha256"]] != rec["lineage_group_id"]: raise V2Error("row-space duplicate across lineages")
        raw_hashes[rec["public_h_sha256"]] = rec["lineage_group_id"]; row_hashes[rec["row_space_sha256"]] = rec["lineage_group_id"]
        if "planted" in family:
            support = rec.get("evaluator_only_provenance", {}).get("planted_witness_support")
            validate_planted_witness(H, support)
        validation = validate_matrix(H, rec["parameter_stratum_id"], rec["r"], rec.get("validation", {}).get("small_circuit", {}).get("cap", 0), False, rec.get("audit_resource_limit_entries", 2_000_000))
        if validation != rec["validation"]:
            raise V2Error("validation metadata mismatch")
        regenerated = build_record(rec["parameter_stratum_id"], rec["construction_batch_index"], rec["case_slot"], rec["validation"]["small_circuit"]["cap"], rec.get("generation_profile", "preaudit"), rec.get("audit_resource_limit_entries", 2_000_000))
        regenerated["split"] = rec["split"]
        if protected_without_digest(regenerated) != protected_without_digest(rec):
            raise V2Error("regenerated protected record mismatch")
        if rec["protected_record_sha256"] != json_digest("protected_record", protected_without_digest(rec)):
            raise V2Error("protected record digest mismatch")
        lineages.setdefault(rec["lineage_group_id"], []).append(rec)
    if any(len(v) != 2 for v in lineages.values()):
        raise V2Error("lineage group size mismatch")
    derived_calibration_ready = payload.get("generation_profile") == "accepted" and full and all(r.get("calibration_ready") is True and r.get("structural_status") == STRUCTURALLY_ACCEPTED for r in payload["records"])
    if payload.get("calibration_ready") is not derived_calibration_ready:
        raise V2Error("manifest calibration_ready mismatch")
    if payload.get("generation_profile") == "accepted":
        for rec in payload["records"]:
            if _is_audit_applicable(rec["parameter_stratum_id"]) and (rec.get("structural_status") != STRUCTURALLY_ACCEPTED or rec.get("validation", {}).get("small_circuit", {}).get("status") != "PASS" or rec.get("validation", {}).get("small_circuit", {}).get("cap", 0) < HARD_SMALL_CIRCUIT_CAP or rec.get("calibration_ready") is not True):
                raise V2Error("accepted manifest contains incomplete audit record")



def calibration_seed(role: str, index: int) -> str:
    if role not in ("threshold_fit_seed", "tier_validation_seed"):
        raise V2Error("unsupported calibration seed role")
    require_uint(index, "seed index")
    ctx = make_context("calibration_seed_v1", "global", 0, 0, BASE_SEED, 0, ("seed_role", role, index), "calibration_seed")
    return expand(ctx, 16).hex()


def final_eval_commitment(index: int, seed_bytes: bytes) -> str:
    require_uint(index, "index")
    if not isinstance(seed_bytes, bytes) or len(seed_bytes) != 16:
        raise V2Error("final-evaluation seed must be 16 bytes")
    return typed_digest(("final_eval_seed_commitment", PROTOCOL_ID, index, seed_bytes))


def verify_final_eval_commitment(index: int, seed_hex: str, commitment: str) -> bool:
    return final_eval_commitment(index, bytes.fromhex(seed_hex)) == commitment

EXPECTED_TEST_VECTOR_DIGEST = "615807fe0a63a11441a4bb22e672ed235edbd8d9e1d85b84933676f1cce1a34f"
EXPECTED_TEST_VECTORS: Dict[str, Any] = {'configuration_digest': 'f33579c66db1b709eec666fb395fb9ecb889a482369d797ad6f19486a0111f65',
 'dense_R0': 'd7211073d74dacb57744f485718ef0708ae37d1f6ca22c824d1deed909850187',
 'dense_R1': '1a9b1fa4c613abb0c693ad2806477ac3dff8b91e8908289169bf59c48f790b12',
 'dense_context_encoding': '540000000b5300000023726c6d772d682d6e61746976652d72657365617263682d76322d72616e646f6d2d76315300000021682d6e61746976652d72657365617263682d76322d63616e6469646174652d76315300000022726c6d772d72657365617263682d636f727075732d76322d746f6f6c696e672d7632530000001764656e73655f66756c6c5f72616e6b5f686173685f7631530000001164656e73652d6e39362d7234382d703530530000001e726c6d772d76322d63616e6469646174652d62617463682d303030303030490000000100420000001d726c6d772d682d6e61746976652d76322d63616e6469646174652d763149000000010054000000035300000005636f6f7264490000000100490000000100530000000b64656e73655f656e747279',
 'dense_expand_48': 'd7211073d74dacb57744f485718ef0708ae37d1f6ca22c824d1deed9098501871a9b1fa4c613abb0c693ad2806477ac3',
 'dummy_final_commit_0': 'f626b61a1b53946d6f04d090ae8dae4b568e41a745b8287a29d7a9dfa414a54e',
 'enc_binary_matrix': '4d00000002000000035200000003a0520000000360',
 'enc_binary_row': '5200000009b180',
 'enc_bytes_abc': '4200000003616263',
 'enc_int_0': '490000000100',
 'enc_int_255': '4900000001ff',
 'enc_list': '4c00000002530000000161490000000102',
 'enc_string_e_acute': '5300000002c3a9',
 'enc_tuple_coord': '54000000035300000005636f6f7264490000000101490000000102',
 'public_h_sha256_fixture': '73807c6412c4f609e313b953e1827ec528af9cc6a87cb944a0b3ba9e8a2716ba',
 'rejection_attempt_1': '91220294f14a01800f0aaad300cca584ccc652ef3a57270afa8d041236823a7f',
 'row_space_sha256_fixture': 'ebe911625f0229265ee92939591d6339cdcfc32099d8fb00b08519e55a85aa70',
 'sparse_priority': 'e742dc4f56645f9da60e93fa2010b15cb737939b2fdc05124a0c46467986dab2',
 'threshold_fit_seed_0': '89371f5bb32d830de861247354897b67',
 'tier_validation_seed_0': '0b62e847455257f4601d4a037906431d'}

def computed_test_vectors() -> Dict[str, Any]:
    row = BinaryRow((1, 0, 1, 1, 0, 0, 0, 1, 1))
    mat = BinaryMatrix.from_rows([[1, 0, 1], [0, 1, 1]])
    dense_ctx = make_context("dense_full_rank_hash_v1", "dense-n96-r48-p50", 0, 0, BASE_SEED, 0, ("coord", 0, 0), "dense_entry")
    sparse_ctx = make_context("sparse_simple_biregular_hash_v1", "sparse-reg-n120-r60-dv3-dc6", 0, 0, BASE_SEED, 0, ("socket", 0, 0, 0, 0), "sparse_edge_priority")
    return {
        "enc_string_e_acute": encode("é").hex(), "enc_int_0": encode(0).hex(), "enc_int_255": encode(255).hex(),
        "enc_bytes_abc": encode(b"abc").hex(), "enc_tuple_coord": encode(("coord", 1, 2)).hex(), "enc_list": encode(["a", 2]).hex(),
        "enc_binary_row": encode(row).hex(), "enc_binary_matrix": encode(mat).hex(),
        "dense_context_encoding": encode(dense_ctx).hex(), "dense_R0": R(dense_ctx, 0).hex(), "dense_R1": R(dense_ctx, 1).hex(),
        "dense_expand_48": expand(dense_ctx, 48).hex(), "rejection_attempt_1": R(make_context("dense_full_rank_hash_v1", "dense-n96-r48-p50", 0, 0, BASE_SEED, 1, ("coord", 0, 0), "dense_entry"), 0).hex(),
        "sparse_priority": R(sparse_ctx, 0).hex(), "public_h_sha256_fixture": public_h_sha256(mat), "row_space_sha256_fixture": row_space_sha256(mat),
        "threshold_fit_seed_0": calibration_seed("threshold_fit_seed", 0), "tier_validation_seed_0": calibration_seed("tier_validation_seed", 0),
        "dummy_final_commit_0": final_eval_commitment(0, DUMMY_FINAL_EVAL_SEEDS[0]), "configuration_digest": config_digest(),
    }


def test_vectors() -> Dict[str, Any]:
    return copy.deepcopy(EXPECTED_TEST_VECTORS) if EXPECTED_TEST_VECTORS else computed_test_vectors()


def verify_test_vectors() -> str:
    got = computed_test_vectors()
    if EXPECTED_TEST_VECTORS and got != EXPECTED_TEST_VECTORS:
        raise V2Error("fixed v2 test vectors do not match implementation")
    digest = hashlib.sha256(canonical_json(got)).hexdigest()
    if EXPECTED_TEST_VECTOR_DIGEST != "TO_BE_FILLED" and digest != EXPECTED_TEST_VECTOR_DIGEST:
        raise V2Error("fixed v2 test-vector digest mismatch")
    return digest


def sparse_feasibility_report() -> Dict[str, int]:
    report = {}
    for stratum in SPARSE_STRATA:
        H, attempt, _ = generate_sparse(stratum, 0, 0)
        validate_matrix(H, stratum, SPARSE_STRATA[stratum][1], 0, False)
        report[stratum] = attempt
    for stratum in PLANTED_SPARSE_STRATA:
        H, support, attempt, _, _ = generate_planted_sparse(stratum, 0, 0)
        validate_planted_witness(H, support, PLANTED_SPARSE_STRATA[stratum][2])
        validate_matrix(H, stratum, PLANTED_SPARSE_STRATA[stratum][1], 0, False)
        report[stratum] = attempt
    return report


def generate_records(full: bool = False, smoke: bool = False, audit_cap: int = 0, profile: str = "preaudit", audit_resource_limit_entries: int = 2_000_000) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    if full:
        for stratum in CONTROL_STRATA:
            records += [build_record(stratum, 0, 0, audit_cap, profile, audit_resource_limit_entries), build_record(stratum, 0, 1, audit_cap, profile, audit_resource_limit_entries)]
        for stratum in DENSE_STRATA:
            for batch in range(9): records += [build_record(stratum, batch, 0, audit_cap, profile, audit_resource_limit_entries), build_record(stratum, batch, 1, audit_cap, profile, audit_resource_limit_entries)]
        for stratum in SPARSE_STRATA:
            for batch in range(9): records += [build_record(stratum, batch, 0, audit_cap, profile, audit_resource_limit_entries), build_record(stratum, batch, 1, audit_cap, profile, audit_resource_limit_entries)]
        for stratum in list(PLANTED_DENSE_STRATA) + list(PLANTED_SPARSE_STRATA):
            for batch in range(4): records += [build_record(stratum, batch, 0, audit_cap, profile, audit_resource_limit_entries), build_record(stratum, batch, 1, audit_cap, profile, audit_resource_limit_entries)]
        return records
    # Bounded CI smoke: one complete group from representative classes plus all controls.
    if smoke and profile == "accepted":
        strata = ["ctrl-hamming-m4", "ctrl-ext-hamming-m4"]
        for stratum in strata:
            records += [build_record(stratum, 0, 0, audit_cap, profile, audit_resource_limit_entries), build_record(stratum, 0, 1, audit_cap, profile, audit_resource_limit_entries)]
        stratum = "dense-n96-r48-p50"
        records += [build_record(stratum, 0, 0, audit_cap, profile, audit_resource_limit_entries), build_record(stratum, 0, 1, audit_cap, profile, audit_resource_limit_entries)]
        return records
    strata = list(CONTROL_STRATA) if not smoke else ["ctrl-hamming-m4", "ctrl-ext-hamming-m4"]
    for stratum in strata:
        records += [build_record(stratum, 0, 0, audit_cap, profile, audit_resource_limit_entries), build_record(stratum, 0, 1, audit_cap, profile, audit_resource_limit_entries)]
    for stratum in ["dense-n96-r48-p50", "sparse-reg-n120-r60-dv3-dc6", "planted-dense-n96-r48-w10", "planted-sparse-n120-r60-w10"]:
        records += [build_record(stratum, 0, 0, audit_cap, profile, audit_resource_limit_entries), build_record(stratum, 0, 1, audit_cap, profile, audit_resource_limit_entries)]
    return records


def self_test() -> Dict[str, Any]:
    vector_digest = verify_test_vectors()
    for bad in (True, -1, 1.25):
        try:
            encode(bad)
        except V2Error:
            pass
        else:
            raise V2Error("bad scalar accepted")
    for weight in range(1, 7):
        rows = [[0] * weight for _ in range(weight - 1 if weight > 1 else 1)]
        if weight == 1:
            rows = [[0]]
        else:
            for j in range(weight - 1): rows[j][j] = 1
            for j in range(weight - 1): rows[j][-1] = 1
        H = BinaryMatrix.from_rows(rows)
        audit = small_circuit_audit(H, 6)
        if audit["status"] != "FOUND_WITNESS" or audit["weight"] != weight:
            raise V2Error(f"audit fixture failed for weight {weight}: {audit}")
    report = sparse_feasibility_report()
    manifest = build_manifest(generate_records(smoke=True), full=False, profile="preaudit")
    validate_manifest(manifest, full=False)
    return {"status": "PASS", "test_vector_digest": vector_digest, "sparse_feasibility_attempts": report}


def _write_manifest(args: argparse.Namespace) -> None:
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    audit_cap = HARD_SMALL_CIRCUIT_CAP if args.profile == "accepted" and args.audit_cap is None else (0 if args.audit_cap is None else args.audit_cap)
    if args.profile == "accepted" and audit_cap < HARD_SMALL_CIRCUIT_CAP:
        raise V2Error("accepted generation requires audit cap at least 6")
    records = generate_records(full=args.full, smoke=args.smoke, audit_cap=audit_cap, profile=args.profile, audit_resource_limit_entries=args.audit_resource_limit_entries)
    payload = build_manifest(records, full=args.full, profile=args.profile)
    path = out / "candidate_pool_manifest.json"
    path.write_bytes(canonical_json(payload))
    print(f"wrote {path} digest={payload['candidate_manifest_digest']} records={len(payload['records'])}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="h-native-research-v2 candidate tooling")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("self-test")
    sub.add_parser("print-test-vectors")
    g = sub.add_parser("generate-candidate-pool"); g.add_argument("--output-dir", required=True); g.add_argument("--full", action="store_true"); g.add_argument("--smoke", action="store_true"); g.add_argument("--profile", choices=["preaudit", "accepted"], default="preaudit"); g.add_argument("--audit-cap", type=int, default=None); g.add_argument("--audit-resource-limit-entries", type=int, default=2_000_000)
    v = sub.add_parser("validate-candidate-pool"); v.add_argument("manifest"); v.add_argument("--full", action="store_true")
    s = sub.add_parser("summary"); s.add_argument("manifest")
    sub.add_parser("sparse-feasibility")
    try:
        args = parser.parse_args(argv)
        if args.cmd == "self-test": print(json.dumps(self_test(), sort_keys=True)); return 0
        if args.cmd == "print-test-vectors": print(json.dumps(computed_test_vectors(), sort_keys=True, indent=2)); return 0
        if args.cmd == "generate-candidate-pool": _write_manifest(args); return 0
        if args.cmd == "validate-candidate-pool": validate_manifest(json.loads(Path(args.manifest).read_text()), full=args.full); print("PASS"); return 0
        if args.cmd == "summary":
            payload = json.loads(Path(args.manifest).read_text()); counts: Dict[str, int] = {}; splits: Dict[str, int] = {}
            for rec in payload.get("records", []):
                counts[rec["parameter_stratum_id"]] = counts.get(rec["parameter_stratum_id"], 0) + 1
                splits[rec.get("split", "?")] = splits.get(rec.get("split", "?"), 0) + 1
            statuses={}
            for r in payload.get("records", []): statuses[r.get("structural_status", "?")] = statuses.get(r.get("structural_status", "?"), 0) + 1
            print(json.dumps({"records": len(payload.get("records", [])), "strata": counts, "splits": splits, "structural_statuses": statuses, "calibration_ready": payload.get("calibration_ready"), "digest": payload.get("candidate_manifest_digest")}, sort_keys=True)); return 0
        if args.cmd == "sparse-feasibility": print(json.dumps(sparse_feasibility_report(), sort_keys=True)); return 0
    except (V2Error, OSError, json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
