# Stern/Dumer collision ISD v2 baseline

## Scope and identifiers

`rlmw_research_isd_v2.py` defines a standalone, solver-disabled stronger ISD baseline for the staged `h-native-research-v2` workflow. It does not freeze a v2 manifest, generate the 192-case corpus, assign thresholds or tiers, reveal final-evaluation seeds, run CP-SAT, or change any v1 quarantine contract.

Frozen identifiers:

| field | value |
| --- | --- |
| protocol | `h-native-research-isd-v2-baselines-v1` |
| result schema | `rlmw-research-isd-v2-result-v1` |
| algorithm | `stern_dumer_collision_isd_v1` |
| implementation version | `1.0.0` |
| PRNG | `sha256-ctr-v1` |
| solver stratum | `solver_disabled` |
| thread count | `1` |
| candidate protocol binding | `h-native-research-v2-candidate-v1` |

## Public-input boundary

The algorithm accepts only an opaque case ID, public binary `H_rows` in original coordinate order, the public-H hash, phase, seed role/index, budget, algorithm configuration, candidate protocol/config digest, candidate-manifest digest, and optional public `W` in `tier_validation` mode. It rejects family, split, lineage, planted witnesses, exact distance, calibration incumbents, evaluator provenance, solver-assisted results, final-evaluation seeds, and unknown fields.

`threshold_fit` has no `W` and records verified incumbents only. `tier_validation` requires public `W` and adds threshold-hit accounting. The supplied `W` is not included in candidate reconstruction and does not alter the deterministic candidate trajectory.

## Frozen parameters

| profile | budgets | left weight | right weight | projection bits | information-set limit | left/right list caps | collision cap | projection cap |
| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |
| `smoke` | `8, 16` | 1 | 1 | 2 | 8 | 64 / 64 | 128 | 512 |
| `calibration` | `2^12, 2^14, 2^16, 2^18` | 2 | 2 | `min(8, rank)` | 4096 | 200000 / 200000 | 4000000 | 20000000 |

All runs use `num_threads = 1` and `exhaust_candidate_budget = true`. Parameters are functions only of the public profile/config and public rank; they are never tuned from family, split, planted, evaluator-only, or calibration-outcome metadata.

## Seed discipline

Only exact phase/seed pairs `threshold_fit`/`threshold_fit_seed[index]` and `tier_validation`/`tier_validation_seed[index]` for `index in 0..7` are accepted; cross-pairings are rejected. Seed bytes are obtained from the frozen `rlmw_research_corpus_v2.calibration_seed(role, index)` derivation. Unknown roles, final-evaluation roles, indices outside `0..7`, booleans, floats, strings, or arbitrary replacement seed bytes are rejected. The RNG key binds protocol, algorithm, PRNG version, case ID, public-H hash, phase, role/index, budget, and algorithm-config digest.

## Algorithm

For public parity-check matrix `H` of length `n` and rank `r`, the implementation row-reduces `H` over GF(2) only to obtain independent equations. Each information-set attempt samples a deterministic parity set `J` of size `r`; its complement `I` is kept in original coordinate order. If `H_J` is singular, the attempt and singular counter are incremented and no candidate budget is consumed.

For invertible `H_J`, the code computes the systematic map `P = H_J^{-1} H_I`. Each local information coordinate has a full original-coordinate basis word with `c_I = e_i` and `c_J = P e_i`; each basis word is independently checked against the original public rows.

The deterministic Dumer/Stern split divides the local information coordinates into left and right halves. The left list enumerates all supports of frozen `left_weight`; the right list enumerates all supports of frozen `right_weight`. For every partial word, the algorithm computes the projection of the full parity part onto the first `ell` deterministic parity coordinates. Left entries are stored in canonical buckets by projected value; right entries probe matching buckets. Each bucket match is a collision pair.

For every collision pair, the implementation reconstructs the full information vector by XORing disjoint left/right supports, computes the full parity part by XORing full systematic basis words, scatters the result to original coordinates, and verifies `c != 0` and original `Hc = 0`. Projected equality is only a bucket filter and never a validity claim.

## Counters and termination

Records include deterministic counters for information-set attempts, singular/accepted sets, left/right list entries, projection operations, bucket probes, processed collision pairs, skipped collision pairs, reconstructed candidates, candidate/objective evaluations, exact verifications, valid codewords, threshold witnesses, duplicates, resource-limit events, and PRNG calls/blocks in diagnostics. Projection operations and processed collision pairs are checked before incrementing, so `max_projection_operations` and `max_collision_pairs` are never exceeded. If a collision cap is already reached, the first unprocessed matching pair increments `skipped_collision_pairs`, raises a resource event, and terminates as `resource_limit`; it is not counted as a processed collision or reconstructed candidate.

Candidate evaluations, objective evaluations, exact verifications, reconstructed candidates, and valid codewords are equal because every reconstructed candidate is fully verified. Duplicates consume budget and exact verification. `candidate_budget_exhausted` requires exact budget exhaustion with no resource event; `resource_limit` requires a resource event; `information_set_limit_exhausted` requires the configured information-set limit with remaining candidate budget. Bounded exhaustion, missing collisions, singular sets, list caps, operation caps, and resource limits are non-certifying termination states and never imply infeasibility, a lower bound, or optimality.

## Records and CLI

Canonical JSONL records bind source commit/module hash, protocol/schema, candidate protocol/config/manifest digests, case ID, public-H hash, algorithm/config digest, phase, seed role/index, budget, termination, counters, verified incumbent bits/hash/weight, optional `W`, threshold status, and a reproducible-core digest. Validation rejects duplicate JSON keys, noncanonical JSONL, unknown/missing fields, non-finite values, bool/float integer substitutions, non-literal boolean substitutions, phase/seed cross-pairings, public-input leakage, invalid source/environment/diagnostics schemas, missing production source commits unless an explicit fixture-only option is used, termination relabelling, counter/resource-cap tampering, witness tampering, digest tampering, solver-assisted fields, and certificate claims. The CLI catches expected validation, JSON, and file errors at the boundary and emits concise nonzero failures without tracebacks. A cheap deterministic calibration preflight covers representative v2 dimensions and every frozen budget without running an expensive calibration search.

Commands:

```bash
python rlmw_research_isd_v2.py self-test --print-summary
python rlmw_research_isd_v2.py list --profile smoke --json
python rlmw_research_isd_v2.py run-fixture --profile smoke --output-dir /tmp/rlmw_isd_v2_smoke
python rlmw_research_isd_v2.py validate /tmp/rlmw_isd_v2_smoke/rlmw_research_isd_v2_results.jsonl --print-summary
python -O rlmw_research_isd_v2.py self-test --print-summary
python -O -m unittest -v test_rlmw_research_isd_v2.py
```
