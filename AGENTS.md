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

Notebook sections 00–11 are currently scaffolded as:

- **00.** Setup, environment, paths, dependency bootstrap
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

## Mathematical invariants

For binary matrix A over F_2:

- All directions must satisfy d ∈ colspan(A).
- All current states c must satisfy c ∈ colspan(A).
- A returned solution must satisfy c ≠ 0 and wt(c) ≤ W.
- Directional weight change is:
  Δ_d(c) = wt(d) - 2 |supp(c) ∩ supp(d)|.

## Result semantics

- `valid_solution`: a candidate that has been exactly verified to satisfy `c != 0`, `c ∈ colspan(A)` (equivalently `c == A u` for some `u` over F_2), and `wt(c) <= W`.
- `best_found`: heuristic best-so-far candidate/weight observed during search; not a certificate of optimality.
- `no_solution_found`: no verified threshold hit (`wt(c) <= W`) was found within the configured budget.
- `certified_optimum`: **not currently implemented**; may only be claimed after a separate exact lower-weight exclusion proof.

## Next milestone order

1. Documentation and experiment-plan cleanup. *(this PR)*
2. Small benchmark/evaluation harness.
3. Baseline comparison and ablation table.
4. Performance cleanup for bit-packed GF(2) operations.
5. Only then consider longer training experiments.

## Review guidelines

- Check that every claimed solution is verified exactly.
- Check that Q-learning only chooses strategy-level operators, not raw algebraic validity.
- Check that local-minimum constraints are encoded as support-intersection inequalities.
- Check that no secret values are committed.
- Check that notebook cells remain runnable in order from a fresh Colab runtime.
- Do not use `--allow-errors` in notebook execution checks.
- Do not commit `/tmp` outputs.

## Review guidance for future benchmark PRs

- Benchmarks must be small in SMOKE mode.
- No long training in default notebook execution.
- Benchmark results must be labeled heuristic unless certified.
- Returned candidates must be exactly verified.
- Do not commit generated benchmark artifacts, plots, or checkpoints unless explicitly requested.

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
