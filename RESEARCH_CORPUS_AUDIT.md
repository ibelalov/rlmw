# Small-circuit audit for `h-native-research-v1`

`rlmw_research_corpus_audit.py` is a standalone stdlib audit for the frozen public-H research corpus. It never changes `rlmw_research_corpus.py`, never enters `solver_payload`, and is classical correctness infrastructure rather than a novelty, solver-superiority, neural-quality, RL-quality, or benchmark-difficulty claim.

## Certificate scope

The audit independently replays the public parity-check rows in their original zero-based coordinate order. It searches all supports of weights 1 through a requested cap, with the supported audited cap limited to 6.

Statuses are intentionally strict:

- `CERTIFIED_EXACT_DISTANCE`: a nonzero zero-syndrome witness was verified at the first found weight after every smaller weight was exhaustively excluded.
- `CERTIFIED_LOWER_BOUND`: all weights through the requested cap completed without a witness; for cap 6 this proves only `d >= 7`.
- `RESOURCE_LIMIT`: work was interrupted or capped before a weight completed; the interrupted weight is not excluded.

A JSONL record validator checks schema bindings, hashes, counters, and witnesses. It does not validate absence by inspection. Use `validate --replay-exclusion` to deterministically replay exclusions and re-establish lower bounds or exact optimality.

## Ordered-split search

For each candidate weight `w`, the algorithm sets `a=floor(w/2)` and `b=w-a`, converts each H column to a Python integer syndrome, enumerates all `b`-subsets `R` by syndrome, and keeps a deterministic representative that maximizes `min(R)` with lexicographically largest tuple as a tie-break. It then enumerates all `a`-subsets `L` and accepts only if `syndrome(L)=syndrome(R)` and `min(R)>max(L)`. The strict inequality makes the halves disjoint and ordered.

Completeness follows from the canonical split of every sorted support: the first `a` coordinates form `L`, the last `b` coordinates form `R`, and necessarily `min(R)>max(L)`. If any same-syndrome right half can complete a given `L`, retaining the right half with maximum `min(R)` preserves an ordered completion whenever one exists.

For completed weights, time is proportional to `sum_w (C(n,floor(w/2)) + C(n,ceil(w/2)))`, including the cost of XORing syndrome words. Peak map size is bounded by `min(C(n,ceil(w/2)), 2^rank(H))`. The method is combinatorial as the cap grows and is not a polynomial-time general minimum-distance algorithm.

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

The first left shift cannot affect bit 7 from lower negative positions, so this gives the documented linear relation between the next block's bit 0 and the previous block's bits 0 and 7. Since `dense_random_H` writes a 64-bit block across columns `r..r+63` and the next block at `r+64..`, whenever the second block exists:

```text
H[:,r] XOR H[:,r+7] XOR H[:,r+64] = 0.
```

This construction artifact explains the weight-3 circuits in `hnrv1-c0020`, `hnrv1-c0022`, and `hnrv1-c0023`; tests check the relation across suitable seeds and dimensions, not only those supports.

## Quarantine semantics

The original thresholds such as `W=19` or `W=23` are not evidence of difficulty. Several dense cases contain structural circuits of weight 3, and other cases only have small-circuit lower bounds through 6. Therefore `h-native-research-v1` is suitable as a contract/audit regression corpus but must be quarantined from comparative performance, solver-superiority, neural-quality, RL-quality, or benchmark-difficulty claims.

The next milestone should be a separately reviewed `h-native-research-v2` design using coordinate-independent deterministic randomness and preregistered calibration.
