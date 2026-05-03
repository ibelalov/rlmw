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
- Section 21 tracks neural action attempts, successes, no-ops, fallbacks, and model-compatibility failures.
- Section 21 disables or masks solver action where needed so solver-assisted results are not confused with neural gains.
- Neural plumbing and action execution are verified; search-quality gains are not established by smoke-scale diagnostics.
- No optimality certification is implemented.

## Result semantics

- `valid_solution`: candidate with `c != 0`, `c = A u`, and `wt(c) <= W`, exactly verified.
- `best_found`: heuristic best-so-far candidate/weight, not certified.
- `no_solution_found`: no verified threshold hit found within the configured budget.
- `certified_optimum`: **not implemented**; may only be claimed after a separate exact lower-weight exclusion proof.

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

## Next milestone

The next technical milestone is:

**Calibrated neural diagnostic evaluation.**

This milestone should:

- run a small deterministic table over Section 21 harder diagnostic cases,
- compare symbolic baseline, untrained neural variants, and trained neural variants,
- report exact action-attempt/success/no-op/fallback diagnostics,
- optionally repeat over a few seeds, still smoke-scale,
- keep solver-disabled and solver-assisted results clearly separated,
- exactly verify every returned candidate,
- avoid model-quality claims unless backed by larger controlled experiments.

## Executable artifact

The single executable artifact is:

- `rlmw.ipynb`

Core logic should remain in this notebook unless explicitly requested otherwise.

## Storage policy

Google Drive is used for runtime persistence (for example: data, runs, checkpoints, exports, cache). These runtime artifacts should not be stored in GitHub.

## Development workflow

- One PR at a time.
- Codex edits the active PR.
- Colab tests the merged notebook.
