# H-native research corpus v1

`h-native-research-v1` is a deterministic public parity-check (`H`) corpus for the next RLMW milestone. It is a corpus specification and validation artifact only: it does **not** contain classical benchmark conclusions, neural-performance claims, packed/GPU optimization claims, or minimum-distance claims for incomplete searches.

## Composition

The generated manifest contains 23 public-H cases split by construction into train/validation/test groups. The current digest is:

```text
61fa35025f2002a6df4cc505044f3048c5160d18a0d521f20713d48960985965
```

Families included:

- Hamming controls from all nonzero binary columns (`m = 6, 7, 8, 9`).
- Extended Hamming controls with one overall parity coordinate.
- First-order Reed--Muller controls represented by a deterministic parity-check basis for RM(1, m).
- Sparse LDPC-style parity-check ensembles generated from pinned `(n, rows, column_weight, seed)` parameters.
- Dense random full-rank parity-check ensembles generated from pinned `(n, rows, seed)` parameters with deterministic pivots.

The corpus has separate smoke and full subsets. Smoke validation is bounded and intended for CI/Colab contract checks only; full corpus execution is deliberately not part of CI.

## Labels and payload separation

Each case records canonical public `H_rows`, raw-H hash, row-space hash, family, size, rank, dimension, construction parameters, provenance, split, group IDs, and label semantics.

Solver-facing payloads expose only protocol version, manifest digest, opaque case ID, public `H_rows`, public `H` hash, threshold `W`, and budget-run metadata. Evaluator-only family/provenance/split/group/label fields are intentionally excluded, and no planted witnesses are exposed.

Exact distances are recorded only for standard families with theorem-backed certificates. Sparse and dense ensembles are labeled as unknown-distance threshold challenges with known lower bounds and optional verified upper-bound weights; incomplete bounded searches never become exact-distance labels.

## Split isolation

Train/validation/test isolation is enforced by construction and validation. Row-space hashes and group/base-group IDs must not cross splits. Equivalent matrices produced by row operations or repeated base objects are rejected by row-space hash checks.

## References

- R. W. Hamming, “Error detecting and error correcting codes,” *Bell System Technical Journal*, 1950.
- D. E. Muller, “Application of Boolean algebra to switching circuit design and to error detection,” *IRE Transactions*, 1954.
- I. S. Reed, “A class of multiple-error-correcting codes and the decoding scheme,” *IRE Transactions*, 1954.
- R. G. Gallager, “Low-density parity-check codes,” *IRE Transactions*, 1962.
- F. J. MacWilliams and N. J. A. Sloane, *The Theory of Error-Correcting Codes*, 1977.

## Validation commands

```bash
python rlmw_research_corpus.py --validate generated --smoke --print-summary
python -O rlmw_research_corpus.py --validate h_native_research_v1_manifest.json --smoke --print-summary
python -m json.tool h_native_research_v1_manifest.json >/tmp/h_native_research_v1_manifest.pretty.json
```
