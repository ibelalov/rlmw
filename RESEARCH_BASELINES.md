# Reproducible classical baselines for `h-native-research-v1`

## Scope

`rlmw_research_baselines.py` is the first correctness-first evaluation layer for the frozen public-`H` research corpus. It establishes deterministic run plans, exact GF(2) checks, canonical result records, independent readback validation, and conservative certificate semantics. It does not establish a performance ranking, neural superiority or inferiority, or a new minimum-distance result.

The layer is bound to:

- corpus protocol: `h-native-research-v1`;
- corpus digest: `b9ce7369cf3d2f1476390b8f1e823bf33d10268b1b0112cf55197ce4fff18559`;
- baseline protocol: `h-native-research-baselines-v1`;
- result schema: `rlmw-research-baseline-result-v1`.

The frozen research manifest, its thresholds and labels, and the separate `h-native-ci-v1` protocol are not changed.

## Public-input boundary

Each algorithm receives only the allowlisted solver payload produced by `rlmw_research_corpus.solver_payload`:

- corpus protocol and digest;
- opaque case ID;
- public binary parity-check rows `H_rows` and their hash;
- public threshold `W`;
- selected profile, seed and repetition metadata.

Family, split, construction, provenance, rank, kernel dimension, row-space identity, and certified or unknown-distance labels are evaluator-only. The runner attaches theorem-backed gaps only after the algorithm returns. This prevents a certified distance or construction label from influencing search.

Coordinates are never reordered in stored witnesses. Character position `j` in every public row string is coordinate `j` of the candidate bit string.

## Deterministic randomness

All solver-disabled baselines use repository-controlled `sha256-ctr-v1`. The 256-bit key is derived canonically from the baseline protocol, PRNG version, baseline ID, case ID, repetition ID and declared seed. Bounded draws use rejection, and subset draws sample without replacement within one subset. No Python `hash()`, `random`, NumPy RNG, process state or wall clock enters a solver-disabled trajectory.

Sampling remains pseudorandom and is not evidence of a distributional theorem. Repeated candidates still consume the declared external evaluation budget and are reported as duplicates.

## Baselines

### `uniform_kernel_sampling_v1`

The module row-reduces public `H` over GF(2), scans coordinates from left to right, and constructs a deterministic full-column-rank basis

\[
A\in\mathbb F_2^{n\times k},\qquad HA=0,
\]

whose restriction to the ordered free coordinates is the identity. The versioned PRNG supplies `k` pseudorandom bits at a time; the algorithm rejects only the all-zero coefficient vector and forms `c=Au` exactly. Under a uniform bit stream this is uniform on the nonzero coefficient vectors, while an actual run remains a deterministic pseudorandom trajectory. Every accepted `u` produces a nonzero kernel word because `A` has full column rank. Each accepted word is independently checked against the original `H`, weighed, and charged as one candidate and one objective evaluation. Sampling is with replacement and exhausts the candidate budget; rejected zero-coefficient draws do not consume it.

### `fixed_weight_subset_sampling_v1`

Requested positive support weights follow a frozen round-robin schedule. At each evaluation the algorithm samples one uniform coordinate subset of that weight, XORs the corresponding public-`H` columns, and checks whether the syndrome is zero. Every subset consumes one candidate and one objective evaluation, including non-codewords and repeats. A zero-syndrome subset is reconstructed as a full coordinate-preserving bit string and independently reverified before it can update the incumbent. Failure to observe a dependency proves nothing outside the sampled subsets.

### `lee_brickell_isd_v1`

Let `r=rank(H)` and `k=n-r`. Each information-set attempt samples `r` parity coordinates `J`; the complement `I` is the information set. Singular `H_J` attempts consume the information-set budget and no candidate budget. For an invertible `H_J`, exact GF(2) elimination gives

\[
P=H_J^{-1}H_I.
\]

For every nonzero information pattern `u` with `1 <= wt(u) <= p`, in increasing weight and lexicographic support order, the algorithm constructs

\[
c_I=u,\qquad c_J=Pu,
\]

scatters it back to the original coordinates, and independently verifies `c != 0` and `Hc=0`. Enumeration continues until the candidate or information-set budget is exhausted; a threshold hit does not stop the run. The zero-syndrome specialization rejects `p=0`, because an invertible `H_J` would then produce only the forbidden zero word. This random-information-set plus at-most-`p` enumeration is the Lee–Brickell method specialized to low-weight codeword search, not a Stern collision algorithm.

Primary sources:

- P. J. Lee and E. F. Brickell, “An Observation on the Security of McEliece’s Public-Key Cryptosystem,” *EUROCRYPT ’88*, LNCS 330, pp. 275–280, [doi:10.1007/3-540-45961-8_25](https://doi.org/10.1007/3-540-45961-8_25).
- A. Canteaut and F. Chabaud, “A New Algorithm for Finding Minimum-Weight Words in a Linear Code: Application to McEliece’s Cryptosystem and to Narrow-Sense BCH Codes of Length 511,” *IEEE Transactions on Information Theory* 44(1), pp. 367–378, 1998, [doi:10.1109/18.651067](https://doi.org/10.1109/18.651067).

### `cp_sat_threshold_reference_v1`

This optional OR-Tools reference is in the separate `solver_assisted_reference` stratum. For Boolean coordinates `c_j`, every parity row is encoded exactly as

\[
\sum_{j:H_{ij}=1}c_j=2q_i,
\]

together with `1 <= sum(c_j) <= W`. It uses one worker, a pinned seed, one solver call, and explicit wall and deterministic-time limits.

- raw `OPTIMAL` or `FEASIBLE` becomes `FEASIBLE` only after independent witness verification;
- completed `INFEASIBLE` proves that no nonzero codeword has weight at most this public `W`;
- all limit/no-conclusion results become `UNKNOWN` and prove neither existence nor nonexistence;
- `INFEASIBLE` is a threshold certificate, not automatically a minimum-distance certificate.

CP-SAT exposes no comparable internal candidate count. Its external candidate count is one for a returned verified witness and zero otherwise. These rows must not be aggregated with solver-disabled equal-candidate-budget results. OR-Tools is not required unless this baseline is explicitly selected.

## Frozen profiles

The research manifest defines case subsets but no algorithm budgets. Baseline run specifications therefore live in the baseline module and have their own canonical profile digest.

| Profile | Cases | Pinned `(repetition, seed)` | Uniform budget | Subset budget / weights | Lee–Brickell budget | CP-SAT reference |
|---|---:|---|---:|---|---|---|
| `smoke` | 9 smoke-tagged cases | `(0,101)` | 512 candidates | 512 / `1,2,3,4` | 512 candidates, 32 information-set attempts, `p=2` | 1 call, 1 s wall, 0.5 deterministic time |
| `full` | all 23 cases | `(0,101)`, `(1,202)`, `(2,303)`, `(3,404)` | 100,000 candidates | 100,000 / `1..12` | 100,000 candidates, 4,096 information-set attempts, `p=2` | 1 call, 60 s wall, 30 deterministic time |

The frozen profile SHA-256 values are `5629b265cdd325b776f41786243c34f354ac577f5a318fa336c2aaf61f026e9a` for `smoke` and `1195f7ed0f5a9a21c83dad41ddd23967a869e75dae2a26c7b7bb32128f48c7d2` for `full`. Changing a run pair, budget, weight schedule or solver limit requires a baseline-protocol version bump; the module fails closed if either v1 profile digest changes.

The three solver-disabled baselines are the default CLI selection. CP-SAT must be selected explicitly.

## CLI

List a run plan without executing it:

```bash
python rlmw_research_baselines.py list --profile smoke
```

Selectors are repeatable and may restrict the frozen plan:

```bash
python rlmw_research_baselines.py list \
  --profile full \
  --baseline lee_brickell_isd_v1 \
  --case-id hnrv1-c0009 \
  --repetition-id 1 \
  --seed 202
```

An unpinned seed/repetition pair, a case outside the profile, or an unknown baseline is rejected.

Run into a user-selected output directory:

```bash
python rlmw_research_baselines.py run \
  --profile smoke \
  --output-dir /tmp/rlmw-baseline-smoke
```

The command writes canonical `rlmw_research_baseline_results.jsonl` atomically and refuses to overwrite it unless `--overwrite` is given. Generated JSONL is ignored by Git and must not be committed.

Validate stored results without rerunning any search:

```bash
python rlmw_research_baselines.py validate \
  /tmp/rlmw-baseline-smoke/rlmw_research_baseline_results.jsonl \
  --print-summary
```

Print a stratum-separated summary:

```bash
python rlmw_research_baselines.py summary \
  /tmp/rlmw-baseline-smoke/rlmw_research_baseline_results.jsonl
```

Run built-in contracts in normal or optimized Python:

```bash
python rlmw_research_baselines.py self-test --print-summary
python -O rlmw_research_baselines.py self-test --print-summary
```

## Result and readback contracts

Every canonical record binds the corpus and profile, public case identity and `H` hash, baseline implementation, declared and derived seed, complete algorithm configuration and its SHA-256, explicit budgets, all external counters, best candidate and hash, independently recomputed weight and threshold status, theorem-backed gap when applicable, solver status, source hashes, Python/platform information, runtime and practical memory measurements.

The reproducible-core SHA-256 excludes only these explicitly observational fields:

- total runtime;
- solver-reported wall time;
- Python-traced peak memory;
- process maximum RSS;
- environment strings.

Source hashes, algorithm outcomes, statuses, candidates and counters remain inside the reproducible core. Identical solver-disabled inputs must produce byte-identical canonical cores.

Readback validation does not trust stored weights or flags. It reloads the frozen manifest and independently checks schema and canonical JSONL encoding, unique run identity, manifest/case/`H`/`W` binding, profile and seed binding, configuration and source hashes, genuine integers and finite timings, exact budgets, solver-stratum consistency, candidate hash, nonzero status, `Hc=0`, weight, threshold status, theorem gap, CP-SAT semantics, and the reproducible-core digest.

## Certificate scope

- `THRESHOLD_WITNESS_FOUND`: a stored nonzero word was verified to satisfy `Hc=0` and `wt(c) <= W`; it is an upper bound on distance.
- `BEST_VERIFIED_ABOVE_THRESHOLD`: a verified incumbent was found, but it misses `W`.
- `BUDGET_EXHAUSTED_NO_THRESHOLD_WITNESS`: bounded solver-disabled search found no hit; this is not exclusion evidence.
- `CERTIFIED_NO_THRESHOLD_WITNESS`: only a completed exact CP-SAT infeasibility result for this threshold.
- `INCONCLUSIVE`: solver limit or other no-conclusion status.

For theorem-certified controls, `known_distance_gap = best_weight - certified_distance` is evaluator-side comparison. For sparse and dense unknown-distance cases it is always null. Baseline records deliberately keep `optimality_claim=false`; a heuristic trajectory does not create a new global certificate.
