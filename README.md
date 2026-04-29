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

## Correctness contract

- All GF(2) algebra is exact NumPy `uint8`/binary logic.
- Neural models may rank or propose.
- Cross-entropy and Q-learning are heuristic.
- A returned threshold solution is valid only after exact verification:
  - `c != 0`
  - `c == A u`
  - `wt(c) <= W`
- `valid_solution` means a verified threshold hit, not a certified optimum.
- `best_found` is heuristic best-so-far only.
- No optimality may be claimed without a separate exact exclusion certificate.

## Running tests

The final notebook cell is:

```text
Run all notebook self-tests
```

That cell calls `run_all_self_tests()`.

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

## Colab workflow

- Open the notebook from GitHub.
- Use **Runtime → Disconnect and delete runtime** for fresh tests.
- Run setup cells, then either run all cells or the final self-test cell.
- OR-Tools may be bootstrapped in interactive Colab.
- Torch is used only for neural modules; GPU is optional and not required for smoke tests.
- Persistent data/runs/checkpoints belong in Google Drive, not GitHub.

## Next experiment milestone

The next post-documentation milestone is a **small benchmark/evaluation harness**, not more architecture.

The milestone should measure:

- best weight found
- whether `wt(c) <= W` was found
- runtime
- number of directions
- number of descent steps
- number of failed minima archived
- solver calls and solver statuses
- cross-entropy entropy
- action trace from the hybrid wrapper

Early benchmarks should use tiny planted instances only, for example:

- `N in {24, 32, 48}`
- `r in {10, 12, 16}`
- `W in {3, 4, 5}`

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
