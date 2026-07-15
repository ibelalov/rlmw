# h-native-research-v2 external calibration harness

`rlmw_research_calibration_v2.py` is source-only orchestration for the external
`h-native-research-v2` calibration stage. It does **not** generate or commit the
192-case accepted candidate manifest, calibration JSONL, fitted thresholds,
tiers, logs, matrices, or final-evaluation data.

## Inputs and provenance

The `plan` command consumes only a fully validated `candidate_pool_manifest.json`
from `rlmw_research_corpus_v2.py`. Full validation requires:

- `generation_profile = accepted`;
- `calibration_ready = true`;
- exactly 192 records;
- the current candidate-tool configuration digest;
- the manifest's protected `candidate_manifest_digest`;
- a concrete source commit.

Every planned and validated run is bound to the candidate-manifest digest, public
H hash, calibration source commit, module/config digests, algorithm ID, seed
role/index, budget, phase, and solver stratum.

## Solver-facing boundary

The public payload is intentionally minimal: `case_id`, `H_rows`, public-H hash,
phase, allowed seed role/index, budget/config identity, and `W` only for tier
validation after threshold freeze. Evaluator-only fields such as family, split,
lineage, planted witnesses, exact distance, calibration incumbents, evaluator
provenance, and solver-assisted evidence are rejected by leakage tests.

## Frozen solver-disabled set

The v2 calibration plan enumerates only these solver-disabled algorithms:

- `uniform_kernel_sampling_v1`
- `fixed_weight_subset_sampling_v1`
- `lee_brickell_isd_v1`
- `stern_dumer_collision_isd_v1`

Budgets are `2^12`, `2^14`, `2^16`, and `2^18`. Threshold fitting uses only
`threshold_fit_seed[0..7]` at budget `2^18` and has no `W`. Tier validation uses
only `tier_validation_seed[0..7]` at all four budgets. `W` is not part of any
threshold-fit public payload and therefore cannot influence fit trajectories.

## Solver-assisted reference

`cp_sat_threshold_reference_v1` is planned in the separate
`solver_assisted_reference` stratum. The frozen profiles are 60 seconds / `1e7`
deterministic time and 600 seconds / `1e8` deterministic time, both with
`num_search_workers = 1`, and exactly these public seed pairs:
`threshold_fit_seed[0]`, `threshold_fit_seed[1]`, `tier_validation_seed[0]`, and
`tier_validation_seed[1]`. CP-SAT evidence is never pooled with solver-disabled
equal-budget summaries. `INFEASIBLE` remains a statement about the stated
threshold model only, not a minimum-distance claim.

## Commands

Generated outputs should be directed outside the repository, for example under
`/tmp` or controlled external artifact storage.

```bash
python rlmw_research_calibration_v2.py plan candidate_pool_manifest.json --output /tmp/rlmw-cal-v2/plan.json
python rlmw_research_calibration_v2.py run-shard /tmp/rlmw-cal-v2/plan.json --shard-index 0 --shard-count 16 --output /tmp/rlmw-cal-v2/shard-000.jsonl
python rlmw_research_calibration_v2.py validate-results /tmp/rlmw-cal-v2/plan.json /tmp/rlmw-cal-v2/results.jsonl --summary
python rlmw_research_calibration_v2.py fit-thresholds /tmp/rlmw-cal-v2/plan.json /tmp/rlmw-cal-v2/results.jsonl --output /tmp/rlmw-cal-v2/thresholds.json
python rlmw_research_calibration_v2.py validate-tiers /tmp/rlmw-cal-v2/plan.json /tmp/rlmw-cal-v2/thresholds.json /tmp/rlmw-cal-v2/results.jsonl --output /tmp/rlmw-cal-v2/tiers.json
python rlmw_research_calibration_v2.py summary /tmp/rlmw-cal-v2/tiers.json
python rlmw_research_calibration_v2.py smoke --output-dir /tmp/rlmw-cal-v2-smoke
```

`run-shard` refuses to overwrite an existing shard output. `validate-results`
detects missing, duplicate, extra, cross-phase, wrong-budget, and wrong-seed
records against the pre-enumerated plan.

## Two-pass policy

Threshold fitting uses solver-disabled records only, with an availability
denominator that includes missing and resource-limit records. A case requires at
least 50% availability and verified incumbents from at least two algorithms; `W`
is the nearest-rank 40th percentile of verified incumbent weights. Tier
validation uses independent tier-validation seeds, exact hit-rate and
resource-limit denominators, nearest-rank Q1/Q3 and IQR, lower medians for even
samples, and the algorithm-agreement rule. Bounded failure never becomes a lower
bound.
