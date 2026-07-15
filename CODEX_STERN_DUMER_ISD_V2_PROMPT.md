# Codex-from-GitHub prompt: implement pinned Stern/Dumer collision ISD v2 baseline

Repository: `https://github.com/ibelalov/rlmw`

Base branch: `main`

Do **not** merge the PR. Open one focused PR for chat audit after CI passes.

## Verified starting assumptions

I inspected current `main` at commit `3202e38103cb079040c0d2a260693f52b53b0314` and confirmed the following repository state before writing this prompt:

- `RESEARCH_CORPUS_V2_DESIGN.md` is the normative v2 construction/calibration design and explicitly says it introduces policy only, not a generated v2 manifest, thresholds, baseline output, notebook cells, neural/RL experiments, or generated matrices.
- V2 calibration requires the inherited solver-disabled baselines, one solver-assisted CP-SAT reference, and **at least one preregistered stronger solver-disabled ISD family**, with solver-disabled and solver-assisted evidence kept separate.
- The v2 solver-disabled budget ladder is `[2^12, 2^14, 2^16, 2^18]`; every incumbent and threshold hit must be exactly verified against the original public `H`; calibration records must bind source commit, protocol/generator/config digests, candidate-manifest digest, public-H hash, algorithm ID, seed role/index, budget, and solver stratum.
- `rlmw_research_baselines.py` is bound to the frozen v1 protocol (`h-native-research-baselines-v1`, result schema `rlmw-research-baseline-result-v1`) and includes `lee_brickell_isd_v1` as a solver-disabled baseline plus a separate `cp_sat_threshold_reference_v1` solver-assisted reference.
- The existing Lee--Brickell implementation samples parity coordinates, computes a systematic basis when the parity block is invertible, enumerates nonzero low-weight information patterns, restores original coordinates, and verifies every candidate against the original `H`. It is explicitly **not** a Stern collision algorithm.
- Existing tests cover deterministic replay, candidate verification/tamper rejection, canonical JSONL and duplicate-key rejection, budget boundaries, singular information-set behavior, row-operation invariance, and python `-O` validation for v1 baselines.
- CI currently runs v1 corpus/audit checks, v2 candidate-tooling smoke checks, v1 baseline contracts under normal and optimized Python, notebook execution, and `git diff --check`.
- README and AGENTS state the next milestone: implement a stronger solver-disabled ISD baseline before external v2 candidate generation/calibration; no generated/frozen v2 manifest, calibrated thresholds, calibration results, final-evaluation data, replicated experiments, or neural/RL results exist yet.

## Goal

Implement and test one pinned stronger solver-disabled collision-based ISD baseline for the v2 research workflow. The preferred algorithm is a deterministic Stern/Dumer-style collision ISD. This must be a genuinely collision-based meet-in-the-middle/list algorithm, **not** another spelling of the existing Lee--Brickell enumeration.

Create a separate implementation/spec/test layer, for example:

- `rlmw_research_isd_v2.py`
- `test_rlmw_research_isd_v2.py`
- `RESEARCH_ISD_V2.md`

Do not modify `rlmw.ipynb`. Do not alter the frozen v1 baseline protocol, v1 manifest/profile digests, v1 schemas, or v1 result semantics. You may reuse small GF(2), canonical JSON, SHA-256 counter RNG, and validation ideas from `rlmw_research_baselines.py`, but v2 ISD must have distinct protocol, algorithm, configuration, and result-schema identifiers.

## Required identities

Freeze explicit identifiers in code and docs. Suggested names are acceptable if used consistently:

- Protocol: `h-native-research-isd-v2-baselines-v1`
- Result schema: `rlmw-research-isd-v2-result-v1`
- Algorithm: `stern_dumer_collision_isd_v1`
- PRNG: `sha256-ctr-v1`
- Solver stratum: `solver_disabled`
- Implementation version: `1.0.0`
- Thread count: frozen to `1`

Add a canonical algorithm/config digest helper. The digest must be deterministic canonical JSON and must reject duplicate keys, NaN/Infinity, booleans/floats where integers are required, negative counts where nonnegative counts are required, and unknown config fields.

## Public input boundary

The runner must operate only on solver-facing public data:

- opaque `case_id`;
- public binary parity-check rows `H_rows` in original coordinate order;
- public `public_h_sha256` or equivalent public-H hash from a candidate manifest record;
- optional public threshold `W` for tier-validation mode;
- seed role/index and declared seed bytes or public deterministic seed material;
- budget and frozen algorithm config.

Do **not** expose split, family, lineage, planted witness, exact distance, calibration incumbent, threshold-fitting decisions, evaluator provenance, final-evaluation seeds, or solver-assisted evidence to the algorithm.

Support two modes without changing the search trajectory improperly:

1. `threshold_fit`: no `W` exists. Search records a verified incumbent upper bound if found.
2. `tier_validation`: public frozen `W` is supplied. Search follows the same deterministic trajectory and additionally records threshold-hit evidence. Do not early-stop on a threshold hit unless the frozen config explicitly says to do so; default must exhaust the declared budget/resource envelope.

## Stern/Dumer-style algorithm contract

Document and implement the exact algebra in `RESEARCH_ISD_V2.md` and keep code comments aligned.

For a public binary parity-check matrix `H` of rank `r` and length `n`:

1. Parse `H_rows` exactly as bit rows in original coordinate order; compute independent rows by exact GF(2) row reduction for rank only. Never row-reorder or coordinate-reorder the public output witness.
2. Each information-set attempt deterministically samples a parity set `J` of size `r` using repository-controlled SHA-256 randomness. The information set is `I = [0..n-1] \ J` in original coordinate order.
3. If `H_J` is singular, count an information-set attempt and singular retry, consume no candidate budget, and continue.
4. If `H_J` is invertible, transform to systematic form without changing public coordinate semantics: compute `P = H_J^{-1} H_I`, so any information vector `u` reconstructs a codeword with `c_I = u` and `c_J = P u`.
5. Dumer/Stern split: split the `k = n-r` information coordinates into deterministic halves `I_left` and `I_right` by their local systematic order. Let frozen parameters include:
   - `information_weight_total` or `(left_weight, right_weight)`;
   - `projection_bits` `ell`;
   - optional `residual_weight_cap` or explicit residual handling mode;
   - list limits/resource caps;
   - candidate budget.
6. Projection: choose `ell` projected syndrome rows/coordinates deterministically from the systematic parity side for each accepted information set. Define a projected value for each partial information pattern as the low-dimensional projection of `P_left a` or `P_right b` (depending on side). Count every projection/hash operation.
7. List construction: enumerate left partial patterns of exactly `left_weight` and right partial patterns of exactly `right_weight` in lexicographic local-support order. For each left pattern, compute its projected value and insert into a canonical bucket. For each right pattern, compute its projected value and probe the matching bucket(s). Count list entries, projection/hash operations, bucket probes, and collision pairs deterministically.
8. Collision matching: for each matching projected value, reconstruct the full information vector `u = a XOR b` over disjoint halves. Compute `c_J = P u`, scatter `(c_I, c_J)` back to original coordinates, and independently verify `c != 0` and original `Hc=0`. Count reconstructed candidates and exact verifications.
9. Residual handling: if residual rows outside the projection are not forced to zero by the collision, do **not** silently accept approximate candidates. Either:
   - exactly compute the full parity part `c_J = P u` and verify `Hc=0`; or
   - implement a documented residual extension/list filter that is still exact before accepting an incumbent.
10. Candidate budget semantics: count one candidate evaluation per reconstructed candidate that reaches exact verification, including duplicates. Missing collisions or list exhaustion consumes operation counters and information-set attempts but not candidate evaluations unless a candidate is reconstructed and verified.
11. Duplicate handling: track canonical candidate words seen in a run; duplicates still consume candidate budget and exact verification, and increment `duplicate_candidates`.
12. Incumbent semantics: every incumbent must be nonzero, exactly satisfy original public `Hc=0`, preserve original coordinate order, and record weight/hash/bits. A threshold hit proves only `wt(c) <= W`, not optimality.
13. Termination semantics: bounded exhaustion, singular information sets, list caps, missing collisions, operation caps, or resource limits must never become lower-bound, infeasibility, or optimality claims.

The implementation may use a compact Dumer/Stern variant suitable for bounded CI smoke, but it must include true collision-list construction and projected-syndrome bucket matching.

## Frozen parameters

Define frozen per-profile/per-budget configs before calibration:

- Budget ladder: `2^12`, `2^14`, `2^16`, `2^18` candidate-evaluation budgets.
- Smoke profile: tiny deterministic cases and small list caps so CI finishes quickly.
- Full/calibration profile: budget ladder entries and fixed deterministic parameters. Do not tune per case from evaluator metadata.
- `num_threads = 1`.
- deterministic seed roles: at least `threshold_fit_seed` and `tier_validation_seed`; final-evaluation seed values must not be revealed or used.

Suggested default algorithm parameters to start with, subject to tests:

- `left_weight = 1`, `right_weight = 1` for smoke fixtures;
- a second small fixture with `left_weight = 2`, `right_weight = 1` or `2` to verify nontrivial buckets/collisions;
- `projection_bits` small in smoke (for example 2--8 depending on rank) and frozen in docs;
- candidate budget controls candidate verifications, while list/resource caps prevent accidental combinatorial explosions.

If a parameter is derived from `n`, `r`, or `k`, document the deterministic formula and include it in the config digest. Reject unsupported dimensions structurally with `RESOURCE_LIMIT`, not by hanging or silently falling back to Lee--Brickell.

## Operation accounting

Add deterministic counters at minimum:

- `information_set_attempts`
- `singular_information_sets`
- `information_sets_accepted`
- `list_entries_left`
- `list_entries_right`
- `projection_hash_operations`
- `bucket_probes`
- `collision_pairs`
- `reconstructed_candidates`
- `candidate_evaluations`
- `objective_evaluations`
- `exact_verifications`
- `valid_codewords_seen`
- `threshold_witnesses_seen`
- `duplicate_candidates`
- `resource_limit_events`
- PRNG counters: `randbits`, `randbelow`, `sha256_blocks` or equivalent

Enforce invariants in validation, for example:

- `candidate_evaluations == exact_verifications == reconstructed_candidates` unless a documented pre-verification rejection counter exists;
- `duplicate_candidates <= candidate_evaluations`;
- `collision_pairs >= reconstructed_candidates` if all pairs reconstruct, or document/count skipped pairs explicitly;
- solver stratum is always `solver_disabled`;
- no CP-SAT status fields appear in solver-disabled records.

## Result records and JSONL

Implement canonical result-record assembly and validation. Records must bind:

- source commit and module SHA-256;
- v2 ISD protocol/result schema;
- generator/config digest and candidate-manifest digest supplied by caller (for tests, use small fixture digests);
- case ID;
- public-H hash;
- algorithm ID and algorithm/config digest;
- seed role and seed index;
- budget;
- deterministic counters;
- termination reason/status;
- incumbent verification fields: bits, SHA-256, weight, `witness_verified`, `threshold_hit` when `W` is supplied;
- reproducible core SHA-256 excluding observational wall-clock/memory fields.

Implement duplicate-key rejection for JSON input and canonical JSONL roundtrip validation. Generated JSONL, logs, candidate pools, calibration outputs, and benchmark results must not be committed.

## Tests required for this PR

Add comprehensive bounded tests in `test_rlmw_research_isd_v2.py`:

1. Tiny differential comparison against brute-force kernel enumeration for several small public `H` matrices.
2. Exact candidate reconstruction and `Hc=0` verification against original public rows.
3. Information-set singularity and deterministic retry behavior.
4. Collision-list bucket/projection fixtures with expected bucket counts and collision pairs.
5. Residual-algebra fixture proving projected collision alone is insufficient and full exact verification is required.
6. Original-coordinate restoration after a nontrivial parity/information split.
7. Deterministic replay with byte-identical reproducible cores.
8. Budget zero, one, and off-by-one boundaries.
9. Operation-counter invariants and tamper rejection.
10. Duplicate-candidate accounting.
11. Resource-limit semantics for too-large lists/dimensions.
12. Row-operation invariance where applicable: equivalent row operations must preserve the codeword validity and deterministic validation semantics, while run trajectories may differ only if the public row bytes are intentionally bound into the seed/hash.
13. Separation of solver-disabled and solver-assisted schemas; CP-SAT fields/statuses must be rejected.
14. Rejection of booleans, floats, strings, and negative values where integers are required.
15. Canonical JSON and duplicate-key rejection.
16. Normal and `python -O` validation.
17. Bounded CLI smoke execution that does not require a generated/frozen v2 manifest.

Prefer small embedded fixture matrices rather than generated benchmark artifacts. Do not commit JSONL outputs created by tests.

## CLI expectations

Provide a small CLI analogous to existing baseline tooling, but scoped to v2 ISD fixtures or caller-supplied JSON:

- `python rlmw_research_isd_v2.py self-test --print-summary`
- `python rlmw_research_isd_v2.py list --profile smoke --json`
- `python rlmw_research_isd_v2.py run-fixture --profile smoke --output-dir /tmp/rlmw_isd_v2_smoke`
- `python rlmw_research_isd_v2.py validate /tmp/rlmw_isd_v2_smoke/<result>.jsonl --print-summary`
- Optimized validation: `python -O rlmw_research_isd_v2.py self-test --print-summary` and `python -O -m unittest -v test_rlmw_research_isd_v2.py`

If you add CI commands, keep them bounded and generated outputs under `/tmp`.

## Documentation requirements

`RESEARCH_ISD_V2.md` must include:

- scope and claim boundaries;
- public-input/no-leakage boundary;
- pinned IDs and frozen config/profile table;
- exact Stern/Dumer algebra and coordinate-restoration description;
- deterministic randomness and seed binding;
- operation-counter definitions;
- result-record schema and validation semantics;
- threshold-fit vs tier-validation mode semantics;
- resource-limit and bounded-exhaustion semantics;
- explicit statement that this is solver-disabled evidence and must remain separate from CP-SAT solver-assisted references;
- exact commands for normal and optimized self-tests.

Update `README.md` and/or `AGENTS.md` only if needed to note that the stronger ISD baseline layer now exists. Do not claim performance superiority, calibrated difficulty, frozen v2 thresholds, or neural/RL gains.

## Commands to run before committing

At minimum run:

```bash
python -m py_compile rlmw_research_isd_v2.py test_rlmw_research_isd_v2.py
python rlmw_research_isd_v2.py self-test --print-summary
python -m unittest -v test_rlmw_research_isd_v2.py
python -O rlmw_research_isd_v2.py self-test --print-summary
python -O -m unittest -v test_rlmw_research_isd_v2.py
git diff --check
```

If you touch CI, run any newly added commands locally where practical. Do **not** run long external calibration. Do **not** generate the 192-case v2 candidate pool. Do **not** assign `W`, tiers, or final-evaluation seeds.

## Scope exclusions

- Do not generate the full 192-case candidate pool.
- Do not run external calibration.
- Do not assign calibrated `W` values or difficulty tiers.
- Do not freeze the v2 manifest.
- Do not reveal or use final-evaluation seeds.
- Do not implement neural/RL comparisons.
- Do not modify `rlmw.ipynb`.
- Do not weaken or alter v1 quarantine contracts.
- Do not pool solver-disabled and solver-assisted evidence.
