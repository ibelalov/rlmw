# AGENTS.md

## Project goal

This repo contains a single Colab notebook, `rlmw.ipynb`, for developing a hybrid symbolic/neural/Q-learning system for finding low-weight vectors in the binary column span of a matrix.

## Development rules

- Keep the main executable artifact as `rlmw.ipynb`.
- Do not split core logic into many `.py` files unless explicitly requested.
- Prefer clear notebook sections with markdown headings.
- Add small self-tests after important functions.
- Avoid storing large outputs, datasets, checkpoints, or run logs in the notebook.
- Do not commit secrets, API keys, GitHub tokens, or Google Drive paths containing private information.
- Keep algebraic checks exact. Neural and Q-learning components may propose actions, but returned codewords must be verified exactly.
- One PR at a time.
- Long-running cells must be guarded by SMOKE mode.

## Current scaffold status

Notebook sections 00–24 are currently scaffolded as:

- **00.** Setup, environment detection, paths, Colab dependency bootstrap
- **01.** Binary linear algebra over F_2
- **02.** Planted span-instance generator
- **03.** Direction bank
- **04.** Exact discrete-gradient descent
- **05.** Local-minimum intersection solver
- **06.** Failed-local-minima archive and attack utilities
- **07.** Neural direction ranker
- **08.** Neural coefficient generator
- **09.** Cross-entropy sampler
- **10.** Strategy-level Q-controller skeleton
- **11.** Anytime hybrid solver wrapper
- **12.** Small benchmark/evaluation harness
- **13.** Baseline comparison and ablation table
- **14.** Lightweight performance profiling harness
- **15.** Packed-bit GF(2) prototype helpers
- **16.** Packed batch-delta comparison harness
- **17.** Fast packed-popcount delta prototype
- **18.** Delta backend scaling study
- **19.** Supervised neural-guidance data generator
- **20.** Tiny offline-trained neural ablation
- **21.** Harder neural-diagnostic benchmark cases
- **22.** Calibrated neural diagnostic evaluation
- **23.** Optional Colab evidence exporter
- **24.** H-native binary-code interface and exact small-instance oracle
- **25.** Frozen H-native benchmark protocol v1

## Mathematical invariants

For binary matrix A over F_2:

- All directions must satisfy d ∈ colspan(A).
- All current states c must satisfy c ∈ colspan(A).
- A returned solution must satisfy c ≠ 0 and wt(c) ≤ W.
- Directional weight change is:
  Δ_d(c) = wt(d) - 2 |supp(c) ∩ supp(d)|.
- A valid descending move additionally requires c XOR d ≠ 0; the forbidden zero-codeword move must not be used as an improvement.

## Current empirical conclusions

- The exact symbolic core passes notebook self-tests.
- Solver-assisted tiny planted cases can find verified threshold solutions.
- Gradient-only or symbolic baselines may remain `best_found` on tiny or harder diagnostic cases.
- `DirectionBank.deltas` / vectorized `uint8` remains the default delta backend.
- Packed-bit helpers are exact but prototype-only.
- Packed-fast delta remains slower than vectorized `uint8` on smoke-scale tests.
- Neural ranker/generator label generation and tiny supervised training work.
- Neural macro-actions now execute in diagnostic ablations.
- Section 21 harder neural diagnostics are implemented.
- Section 22 calibrated neural diagnostic evaluation is implemented.
- Section 24 H-native parity-check interface derives an exact `A` basis with `H A = 0` over `F_2`, preserving the original coordinate order of `H`.
- Section 24 exact tiny oracle exhaustively enumerates all nonzero kernel coefficients in Gray-code order under a conservative dimension cap; it is for small CI/Colab checks, not cryptographic or large coding-theory scale claims.
- Section 24 distinguishes `CERTIFIED_OPTIMUM`, `CERTIFIED_TRIVIAL_CODE`, `VERIFIED_THRESHOLD_WITNESS`, `CERTIFIED_NO_THRESHOLD_WITNESS`, `RESOURCE_LIMIT`, and `INCONCLUSIVE`; a threshold witness proves only `d <= W`, not optimality.
- Solver-disabled diagnostic rows and solver-assisted reference rows are separated.
- Action attempts/successes/no-ops/fallback diagnostics are reported.
- Neural smoke diagnostics verify plumbing/action execution but do not establish trained-neural search superiority.
- Certified optimum mode exists only inside the capped exhaustive Section 24 oracle; the hybrid solver, CP-SAT threshold checks, and neural diagnostics remain non-optimality-certifying.
- Section 25 defines public-H benchmark protocol `h-native-protocol-v1` / `h-native-ci-v1` with manifest digest `89f1cdcdab541db4bab72726b33b5ff7bf980742d6b26166a6b6c52a0800a561`; it uses opaque case IDs, canonical H row strings, raw-H/RREF digests, group/hash-isolated splits, smoke/full run specs, solver strata, no-leakage solver payloads, and result schemas without making benchmark-performance claims.

## Result semantics

- `valid_solution`: a candidate that has been exactly verified to satisfy `c != 0`, `c ∈ colspan(A)` (equivalently `c == A u` for some `u` over F_2), and `wt(c) <= W`; this is not an optimality certificate.
- `best_found`: exactly verified nonzero heuristic best-so-far candidate/weight observed during search; not a certificate of optimality.
- `no_solution_found`: no verified threshold hit (`wt(c) <= W`) was found within the configured budget.
- `certified_optimum`: implemented only by the capped exhaustive Section 24 oracle after complete enumeration with matching finite bounds and a verified nonzero witness; heuristic and CP-SAT threshold results must not claim it.
- Solver use is explicit via `HybridSolverConfig.use_local_solver`; model-use flags require corresponding supplied models.

## Next milestone order

1. Build a genuinely research-scale H-native corpus using standard code families and independent larger ensembles.
2. Add strong classical baselines only after that corpus is reviewed.
3. Revisit controlled neural-quality claims only after research-scale corpus and baselines exist.

## Review guidelines

- Check that every claimed solution is verified exactly.
- Check that Q-learning only chooses strategy-level operators, not raw algebraic validity.
- Check that local-minimum constraints are encoded as support-intersection inequalities.
- Check that no secret values are committed.
- Check that notebook cells remain runnable in order from a fresh Colab runtime.
- Do not use `--allow-errors` in notebook execution checks.
- Do not commit `/tmp` outputs.
- Notebook outputs should generally be cleared before committing.
- PROJECT_ROOT and Google Drive `rlmw` directories are artifact storage only; source identity must come from an explicit source root/URL/ref, not from artifact storage.
- Do not commit executed notebooks from `/tmp`.
- Do not confuse solver-assisted reference rows with neural gains.
- Do not claim trained-neural superiority from smoke diagnostics.

## Review guidance for future neural benchmark PRs

- Keep benchmarks small in SMOKE mode.
- Ensure no Python `hash(...)` is used for deterministic experiment seeds.
- Ensure solver-disabled diagnostics really mask solver action.
- Ensure trained vs untrained variants are separated.
- Ensure neural variants actually attempt relevant neural actions.
- Do not count fallback actions as neural successes.
- Do not make model-quality claims from smoke tests.
- Ensure every returned candidate is exactly verified.
- Do not confuse solver-assisted results with neural gains.
- Do not commit generated benchmark artifacts, plots, datasets, or checkpoints unless explicitly requested.

## Frozen H-native benchmark protocol v1

Section 25 defines protocol version `h-native-protocol-v1` / `h-native-ci-v1`. Cases must expose algorithm-facing data as public `H`, public `W`, opaque case ID, selected budget/run spec, and allowed algorithm configuration only. Evaluator-only labels, group/split/provenance, certified distances, and planted metadata must not enter solver payloads. Research-scale unknown-distance cases are deferred to the next milestone and must not be inferred from this CI manifest. Solver-disabled and solver-assisted strata must remain separate, and registry binding must reject fake IDs or altered H/W.

## H-native interface and tiny exact oracle

Section 24 introduces the public parity-check representation `HCodeProblem` for codes `C = {c in F_2^n : Hc = 0}`.  It derives the existing solver-compatible `A` convention as an `n`-by-`k` full-column-rank kernel basis satisfying `H A = 0`, where `k = n - rank(H)`.  Rows of `A` retain the original coordinate order of columns of `H`; redundant parity-check rows and invertible row operations on `H` do not change the represented code.

The certified exact oracle is deliberately scoped to tiny instances.  It enumerates all `2^k - 1` nonzero coefficient vectors in Gray-code order, updates `c = A u` incrementally, and reports `CERTIFIED_OPTIMUM` only after exhaustive coverage gives equal lower and upper bounds.  If `k` exceeds the effective cap, it returns `RESOURCE_LIMIT` without an exact-distance claim. Requested caps must be genuine nonnegative integers (booleans and floats are rejected), and the effective cap is bounded by the hard replay limit of 12. A direct coordinate-space `Hc = 0` enumerator is included only for very small differential tests.

Exact enumeration accounting is intentionally explicit: Gray/kernel replay counts the `2^k - 1` nonzero coefficient vectors, while direct H-space replay counts all `2^n - 1` nonzero coordinate candidates and separately validates `2^k - 1` valid nonzero codewords satisfying `Hc = 0`. Certified optimum claims are independently replayed on these capped tiny instances before threshold status code trusts them. `PublicHSpanAdapter` is a hybrid-stack bridge only: it requires a concrete `W >= 1` and a nonzero kernel dimension; exact H-native oracle routines still handle trivial codes and `W = 0`. Independent replay and primary exact enumeration share a hard cap of 12 (`k <= 12` for Gray/kernel replay and `n <= 12` for direct-H replay). Oversized requested caps are recorded with both requested/effective values in metadata, but enumeration returns structured `RESOURCE_LIMIT` instead of traversing beyond 12; oversized manual certified objects are rejected before exponential work begins.

Threshold feasibility remains separate from minimum distance: `VERIFIED_THRESHOLD_WITNESS` proves only that some nonzero codeword has weight at most `W`; `CERTIFIED_NO_THRESHOLD_WITNESS` requires complete exact coverage or another explicit exact proof.  CP-SAT feasibility statuses are therefore treated as threshold evidence only and are never labelled as minimum-distance certificates.

## Notebook validation command

```bash
mkdir -p /tmp/rlmw_nb

RLMW_HEADLESS=1 \
RLMW_SMOKE=1 \
RLMW_PROJECT_ROOT=/tmp/rlmw \
jupyter nbconvert \
  --to notebook \
  --execute rlmw.ipynb \
  --output rlmw_executed.ipynb \
  --output-dir /tmp/rlmw_nb \
  --ExecutePreprocessor.kernel_name=python3 \
  --ExecutePreprocessor.timeout=900
```
