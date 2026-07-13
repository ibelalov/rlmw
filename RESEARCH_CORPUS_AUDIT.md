# Small-circuit audit for `h-native-research-v1`

`rlmw_research_corpus_audit.py` is standalone stdlib correctness infrastructure for the frozen public-H research corpus. It never changes the frozen manifest, `rlmw_research_corpus.py`, baseline schemas, or solver payloads. Audit JSONL is generated output and is not benchmark evidence.

## Versioned schema

This PR increments audit records to `h-native-research-v1-small-circuit-audit-v2` because the per-weight `completed` flag is replaced by explicit outcomes and exact counters. The corpus protocol and ordered-split algorithm identity remain unchanged.

Each canonical JSONL record binds the audit protocol, corpus protocol, frozen manifest digest, audited case ID, original raw-H digest, audit algorithm, requested cap, full audit configuration, configuration digest, preflight estimate, status-specific fields, and `record_sha256`. The record hash is computed from the canonical serialization of every other field. Validation rejects missing keys, unexpected keys, booleans/floats where integers are required, non-finite JSON, duplicate object keys, altered protected fields even after recomputing `record_sha256`, and any unknown or duplicate case.

## Per-weight outcomes and counters

For each weight, v2 stores `weight`, split sizes `a` and `b`, expected and examined right-subset counts, expected and examined left-subset counts, syndrome-map size, and one outcome:

- `EXHAUSTED_NO_WITNESS`: every right and left subset for that weight was evaluated and no witness was found.
- `WITNESS_FOUND`: all right subsets and the recorded prefix of left subsets were evaluated until a verified witness was found.
- `INTERRUPTED`: a subset-visit resource limit stopped before the next subset was evaluated; the rejected subset is not counted.

`CERTIFIED_EXACT_DISTANCE` requires every smaller weight to be `EXHAUSTED_NO_WITNESS`, a terminal `WITNESS_FOUND`, matching `witness_weight`, `certified_bound`, support length, and terminal weight, plus an independently verified nonzero zero-syndrome support; `last_excluded_weight` is therefore `witness_weight - 1`. `CERTIFIED_LOWER_BOUND` requires every weight through the requested cap to be exhausted, sets `last_excluded_weight = requested_cap`, and reports `requested_cap + 1`. `RESOURCE_LIMIT` requires a non-null `subset_visit_limit`, requires the sum of examined right and left subsets to equal that limit, identifies the interrupted weight, and bases `last_excluded_weight` and the lower bound only on earlier consecutive exhausted weights.

## Canonical JSONL and validation levels

`validate` and `summary` share one canonical JSONL reader. It rejects blank lines, leading/trailing whitespace, reordered/noncanonical keys, duplicate keys, NaN/Infinity, malformed UTF-8/JSON, duplicate cases, missing cases, and unexpected cases; it requires exactly the twelve audited unknown-distance v1 cases and a final newline. `summary` validates all records before printing anything. Lightweight validation checks schema, bindings, accounting consistency, and witness validity, but it does not independently replay absence claims. `validate --replay-exclusion` reruns the exact search and compares certificate-relevant fields.

## Ordered-split search

For each weight `w`, the algorithm uses `a=floor(w/2)` and `b=w-a`. It maps all `b`-subsets `R` by syndrome while retaining the representative that maximizes `min(R)` with lexicographically largest tuple as tie-breaker. It then enumerates `a`-subsets `L` and accepts only if `syndrome(L)=syndrome(R)` and `min(R)>max(L)`, proving the halves are ordered and disjoint. If a same-syndrome right half can complete a given left half, retaining maximum `min(R)` preserves a valid ordered completion.

Preflight reports actual `rank(H)` for the syndrome-space map bound `min(C(n,b), 2^rank(H))`, including redundant or zero parity-check rows.

## CLI

```bash
python rlmw_research_corpus_audit.py list
python rlmw_research_corpus_audit.py audit --max-weight 6 --output-dir /tmp/audit
python rlmw_research_corpus_audit.py validate /tmp/audit/rlmw_research_corpus_audit.jsonl
python rlmw_research_corpus_audit.py validate /tmp/audit/rlmw_research_corpus_audit.jsonl --replay-exclusion
python rlmw_research_corpus_audit.py summary /tmp/audit/rlmw_research_corpus_audit.jsonl
python rlmw_research_corpus_audit.py self-test
```

Malformed CLI use is handled by `argparse`; validation failures return nonzero status with concise diagnostics.

## Independently reproduced v1 findings

Through weight 6, the audit reproduces:

| Case | Certificate | Witness |
|---|---:|---|
| `hnrv1-c0012` | exact `d=6` | `[4,14,34,62,78,80]` |
| `hnrv1-c0013` | exact `d=5` | `[26,37,82,97,105]` |
| `hnrv1-c0020` | exact `d=3` | `[72,79,136]` |
| `hnrv1-c0022` | exact `d=3` | `[72,79,136]` |
| `hnrv1-c0023` | exact `d=3` | `[80,87,144]` |
| `hnrv1-c0014` | lower bound | `d>=7` |
| `hnrv1-c0015` | lower bound | `d>=7` |
| `hnrv1-c0016` | lower bound | `d>=7` |
| `hnrv1-c0017` | lower bound | `d>=7` |
| `hnrv1-c0018` | lower bound | `d>=7` |
| `hnrv1-c0019` | lower bound | `d>=7` |
| `hnrv1-c0021` | lower bound | `d>=7` |


## Dense xorshift artifact

`dense_random_H` constructs `H=[I_r|P]` from consecutive outputs of `xorshift64`. For the generator step

```text
x ^= x << 13; x ^= x >> 7; x ^= x << 17
```

the low output bit after the final left shift is unchanged by that final shift. The first left shift also cannot affect bit 0, so after the middle right shift:

```text
bit_0(next) = bit_0(current_after_left13) XOR bit_7(current_after_left13).
```

The first left shift cannot affect bit 7 from lower negative positions, so this gives the documented linear relation between the next block's bit 0 and the previous block's bits 0 and 7. Since `dense_random_H` writes a 64-bit block across columns `r..r+63` and the next block at `r+64..`, whenever all three referenced columns exist:

```text
H[:,r] XOR H[:,r+7] XOR H[:,r+64] = 0.
```

This construction artifact explains the weight-3 circuits in `hnrv1-c0020`, `hnrv1-c0022`, and `hnrv1-c0023`. Tests now require applicable dimensions for the relation and return an explicit not-applicable result rather than a vacuous success when the referenced columns do not exist.

## Quarantine and roadmap

The v1 corpus remains quarantined for contract/audit regression only. This hardening PR strengthens certificate contracts; after review and merge, the next separate milestone is the design of `h-native-research-v2` with coordinate-independent deterministic randomness and preregistered construction/difficulty calibration. Replicated performance and neural/RL comparisons remain deferred until v2 is reviewed and calibrated.
