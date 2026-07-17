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

Every plan, result, threshold artifact, and tier artifact is bound to manifest kind, profile, frozen budget tuple, candidate-manifest/config digests, public H hash, calibration source commit, module/dependency digests, algorithm configuration, seed role/index, phase, and solver stratum. Production and fixture profiles are mutually exclusive: fixture evidence requires explicit `--allow-fixture` and cannot validate as production evidence.

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

Budgets are `2^12`, `2^14`, `2^16`, and `2^18`. Threshold-fit plans enumerate
all four budgets for complete, auditable run accounting, but threshold fitting
uses only completed maximum-budget (`2^18`) records from
`threshold_fit_seed[0..7]`; fit payloads have no `W`. Tier validation uses
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

CP-SAT normalization follows the producing adapter exactly. An available call
records one solver call: `FEASIBLE` (raw `FEASIBLE` or `OPTIMAL`) has one
exactly verified threshold witness; `INFEASIBLE` (raw `INFEASIBLE`) has no
witness and sets only the stated-model threshold-infeasibility flag; and
`UNKNOWN` (raw `UNKNOWN`) has neither witness nor certificate. These outcomes
do not complete a solver-disabled candidate budget and are not included in its
resource-limit rates. If OR-Tools is unavailable, the normalized result has
zero calls, `DEPENDENCY_UNAVAILABLE`, a nonempty error, and a resource limit;
all available CP-SAT outcomes have `error = null`.

## Commands

Generated outputs should be directed outside the repository, for example under
`/tmp` or controlled external artifact storage.

Production plans use the frozen budget ladder verbatim. Smoke/fixture runs use the explicit `calibration_fixture_smoke_v2` profile with declared budgets `[8, 16, 24, 32]`; these fixture plans are identifiable by profile ID and are not production calibration evidence. Threshold-fit adapters accept `W = null` rather than receiving a hidden threshold.


```bash
python rlmw_research_calibration_v2.py threshold-fit-plan candidate_pool_manifest.json --output /tmp/rlmw-cal-v2/fit-plan.json
python rlmw_research_calibration_v2.py run-shard candidate_pool_manifest.json /tmp/rlmw-cal-v2/fit-plan.json --shard-index 0 --shard-count 16 --output /tmp/rlmw-cal-v2/fit-shard-000.jsonl
python rlmw_research_calibration_v2.py validate-results /tmp/rlmw-cal-v2/fit-plan.json /tmp/rlmw-cal-v2/fit-results.jsonl --summary
python rlmw_research_calibration_v2.py fit-thresholds candidate_pool_manifest.json /tmp/rlmw-cal-v2/fit-plan.json /tmp/rlmw-cal-v2/fit-results.jsonl --output /tmp/rlmw-cal-v2/thresholds.json
python rlmw_research_calibration_v2.py tier-reference-plan candidate_pool_manifest.json /tmp/rlmw-cal-v2/thresholds.json --fit-plan /tmp/rlmw-cal-v2/fit-plan.json --fit-results /tmp/rlmw-cal-v2/fit-results.jsonl --output /tmp/rlmw-cal-v2/tier-plan.json
python rlmw_research_calibration_v2.py run-shard candidate_pool_manifest.json /tmp/rlmw-cal-v2/tier-plan.json --thresholds /tmp/rlmw-cal-v2/thresholds.json --fit-plan /tmp/rlmw-cal-v2/fit-plan.json --fit-results /tmp/rlmw-cal-v2/fit-results.jsonl --shard-index 0 --shard-count 16 --output /tmp/rlmw-cal-v2/tier-shard-000.jsonl
python rlmw_research_calibration_v2.py validate-tiers candidate_pool_manifest.json /tmp/rlmw-cal-v2/tier-plan.json /tmp/rlmw-cal-v2/thresholds.json /tmp/rlmw-cal-v2/tier-results.jsonl --fit-plan /tmp/rlmw-cal-v2/fit-plan.json --fit-results /tmp/rlmw-cal-v2/fit-results.jsonl --output /tmp/rlmw-cal-v2/tiers.json
python rlmw_research_calibration_v2.py summary /tmp/rlmw-cal-v2/tiers.json --manifest candidate_pool_manifest.json --tier-plan /tmp/rlmw-cal-v2/tier-plan.json --thresholds /tmp/rlmw-cal-v2/thresholds.json --tier-results /tmp/rlmw-cal-v2/tier-results.jsonl --fit-plan /tmp/rlmw-cal-v2/fit-plan.json --fit-results /tmp/rlmw-cal-v2/fit-results.jsonl
python rlmw_research_calibration_v2.py smoke --output-dir /tmp/rlmw-cal-v2-smoke
```

Threshold-fit and tier-reference plans have separate exact top-level schemas: only tier-reference plans carry a required SHA-256 `thresholds_sha256` binding. Plans reject missing and unknown fields. When fitting cannot establish an unknown-case threshold, tier planning emits no solver-facing tier runs for that case; authoritative replay still emits a full, schema-identical `calibration_incomplete` tier row with `W`, IQR, and upper bound set to `null`, rate/median maps empty, and boolean agreement/gap fields `false`.

Generated Hamming, extended-Hamming, Reed--Muller, and exact-random controls
carry certificates only in evaluator-only provenance. Calibration replays that
certificate from the public `H`, rejects malformed or inconsistent replay, and
sets both `W` and `certified_lower_bound` to the certified exact distance.

For a complete 192-case production manifest, threshold fitting has
`192 * 4 algorithms * 4 budgets * 8 seeds = 24,576` records; independent tier
validation has another 24,576, and the two CP-SAT profiles have 1,536 reference
records, for the normative total of **50,688** records.

The first stage fits thresholds from solver-disabled evidence only. `tier-reference-plan`, `validate-tiers`, and `summary` perform authoritative replay: they require the complete fit plan/results and complete tier plan/results, then recompute every decision-bearing threshold and tier field. A self-recomputed artifact digest is only an integrity checksum and is never sufficient evidence. The second stage plans tier-validation and CP-SAT reference runs only from a validated threshold artifact. `run-shard` executes the assigned real v2 adapters and refuses to overwrite an existing shard output. `validate-results`
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


Plan validation authoritatively reconstructs the exact ordered case × algorithm × budget × seed run list from the bound manifest, profile, and (for tier/reference plans) replayed threshold artifact. A self-consistent rehashed plan with deleted, substituted, extra, cross-case, or reordered runs is rejected.
