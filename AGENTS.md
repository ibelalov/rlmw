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

Notebook sections 00–22 are currently scaffolded as:

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

## Mathematical invariants

For binary matrix A over F_2:

- All directions must satisfy d ∈ colspan(A).
- All current states c must satisfy c ∈ colspan(A).
- A returned solution must satisfy c ≠ 0 and wt(c) ≤ W.
- Directional weight change is:
  Δ_d(c) = wt(d) - 2 |supp(c) ∩ supp(d)|.

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
- Solver-disabled diagnostic rows and solver-assisted reference rows are separated.
- Action attempts/successes/no-ops/fallback diagnostics are reported.
- Neural smoke diagnostics verify plumbing/action execution but do not establish trained-neural search superiority.
- No certified optimum mode exists.

## Result semantics

- `valid_solution`: a candidate that has been exactly verified to satisfy `c != 0`, `c ∈ colspan(A)` (equivalently `c == A u` for some `u` over F_2), and `wt(c) <= W`.
- `best_found`: heuristic best-so-far candidate/weight observed during search; not a certificate of optimality.
- `no_solution_found`: no verified threshold hit (`wt(c) <= W`) was found within the configured budget.
- `certified_optimum`: **not currently implemented**; may only be claimed after a separate exact lower-weight exclusion proof.

## Next milestone order

1. Post-Section-22 notebook/docs hygiene. *(this PR)*
2. Controlled neural diagnostic analysis.
3. Small deterministic table review over Section 22 cases.
4. Optional solver-certification/exclusion phase design.
5. Larger performance work only after measurement justifies it.

## Review guidelines

- Check that every claimed solution is verified exactly.
- Check that Q-learning only chooses strategy-level operators, not raw algebraic validity.
- Check that local-minimum constraints are encoded as support-intersection inequalities.
- Check that no secret values are committed.
- Check that notebook cells remain runnable in order from a fresh Colab runtime.
- Do not use `--allow-errors` in notebook execution checks.
- Do not commit `/tmp` outputs.
- Notebook outputs should generally be cleared before committing.
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
