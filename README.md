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
