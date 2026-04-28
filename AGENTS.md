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

## Mathematical invariants

For binary matrix A over F_2:

- All directions must satisfy d ∈ colspan(A).
- All current states c must satisfy c ∈ colspan(A).
- A returned solution must satisfy c ≠ 0 and wt(c) ≤ W.
- Directional weight change is:
  Δ_d(c) = wt(d) - 2 |supp(c) ∩ supp(d)|.

## Review guidelines

- Check that every claimed solution is verified exactly.
- Check that Q-learning only chooses strategy-level operators, not raw algebraic validity.
- Check that local-minimum constraints are encoded as support-intersection inequalities.
- Check that no secret values are committed.
- Check that notebook cells remain runnable in order from a fresh Colab runtime.

## Notebook validation command

```bash
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
