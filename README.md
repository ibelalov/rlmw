# rlmw

`rlmw` studies low-weight codeword search in the binary column span of a matrix.

## Problem statement

Given a binary matrix
\[
A \in \mathbb{F}_2^{N\times r},
\]
find a nonzero vector
\[
c \in \operatorname{im}(A)
\]
with
\[
\mathrm{wt}(c) \le W.
\]

During training, the threshold `W` is derived from a planted vector
\[
c_\star = A u_\star,
\]
but `c_star` is **not** assumed to be minimum-weight.

## Notebook scaffold

Current notebook sections are:

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
- It separates solver-disabled diagnostic rows from solver-assisted reference rows.
- It reports action-attempt/success/no-op/fallback diagnostics.
- It still does not establish trained-neural search-quality gains.
- All returned candidates must be exactly verified.
- No optimality certification is implemented.

## Result semantics

- `valid_solution`: candidate with `c != 0`, `c = A u`, and `wt(c) <= W`, exactly verified; it is not an optimality certificate.
- `best_found`: exactly verified nonzero heuristic incumbent above the threshold, not certified optimal.
- `no_solution_found`: no verified threshold hit found within the configured budget.
- `certified_optimum`: **not implemented**; may only be claimed after a separate exact lower-weight exclusion proof.

The local-minimum solver and controller operate on the nonzero codeword domain: a direction `d` is a valid descending move from `c` only when `c XOR d` is nonzero and lowers weight. Solver use is controlled explicitly by `HybridSolverConfig.use_local_solver`; solver-disabled diagnostics must not depend on whether OR-Tools happens to be installed. Neural model flags similarly require supplied models and are not silently ignored. Benchmark metadata separates exact candidate verification from heuristic search and keeps `optimality_certified = false`.

## Running tests

The final notebook cell is:

```text
Run all notebook self-tests
```

Headless validation command:

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

- Do not use `--allow-errors`.
- Do not commit `/tmp/rlmw_nb/rlmw_executed.ipynb`.

## H-native interface and tiny exact oracle

Section 24 introduces the public parity-check representation `HCodeProblem` for codes `C = {c in F_2^n : Hc = 0}`. It derives the existing solver-compatible `A` convention as an `n`-by-`k` full-column-rank kernel basis satisfying `H A = 0`, where `k = n - rank(H)`. Rows of `A` retain the original coordinate order of columns of `H`; redundant parity-check rows and invertible row operations on `H` do not change the represented code.

The certified exact oracle is deliberately scoped to tiny instances. It enumerates all `2^k - 1` nonzero coefficient vectors in Gray-code order, updates `c = A u` incrementally, and reports `CERTIFIED_OPTIMUM` only after exhaustive coverage gives equal lower and upper bounds. If `k` exceeds the configured cap, it returns `RESOURCE_LIMIT` without an exact-distance claim. A direct coordinate-space `Hc = 0` enumerator is included only for very small differential tests.

Threshold feasibility remains separate from minimum distance: `VERIFIED_THRESHOLD_WITNESS` proves only that some nonzero codeword has weight at most `W`; `CERTIFIED_NO_THRESHOLD_WITNESS` requires complete exact coverage or another explicit exact proof. CP-SAT feasibility statuses are therefore treated as threshold evidence only and are never labelled as minimum-distance certificates.

## Next milestone

The next technical milestone is controlled neural diagnostic analysis after review of the H-native parity-check interface and certified tiny exact oracle.

## Executable artifact

The single executable artifact is:

- `rlmw.ipynb`

Core logic should remain in this notebook unless explicitly requested otherwise.

## Storage policy

Google Drive and `PROJECT_ROOT` are used only for runtime artifact persistence (for example: data, runs, checkpoints, exports, cache). They are not source identity. Evidence manifests record an explicit source root/notebook identity separately from artifact storage: CI uses the checked-out GitHub workspace and records the real git HEAD plus the SHA-256 of that checkout's `rlmw.ipynb`; Colab records the configured GitHub source URL/ref and marks checkout-only fields unavailable when no checkout is accessible. Ordinary Run all does not create an evidence ZIP; export is opt-in and writes to `EXPORT_DIR` only when explicitly requested.

## Development workflow

- One PR at a time.
- Codex edits the active PR.
- Colab tests the merged notebook.
