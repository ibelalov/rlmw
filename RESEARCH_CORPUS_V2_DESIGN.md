# h-native-research-v2 construction and calibration protocol design

This document is the normative, implementation-ready design for `h-native-research-v2`.  It specifies policy only: no v2 generator, manifest, thresholds, case records, baseline output, notebook cells, neural/RL experiments, or generated matrices are introduced by this document.

The protocol directly addresses the known `h-native-research-v1` defects: sequential xorshift state created deterministic weight-3 circuits; dense `H=[I_r|P]` exposed identity-column structure; sparse cases called regular were later perturbed and therefore not regular; thresholds were weakly calibrated; lineage identifiers were often unique per case; a 23-case pilot was too small for broad claims; coordinate-permuted equivalent codes were not detected by row-space hashes; and preliminary bounds plus first-14-column searches were inadequate.

## 1. Scope and claim boundaries

### 1.1 Immutable v1 status

`h-native-research-v1` remains byte-frozen and quarantined for contract/audit regression only.  Its manifest, semantic digest, all cases, labels, thresholds, audit conclusions, baseline protocols, seeds, budgets, result schemas, and quarantine conclusions must not be changed by v2 work.  V1 must not be used for solver-superiority, neural-quality, RL-quality, or benchmark-difficulty claims.

### 1.2 Separate v2 identity

`h-native-research-v2` must be a separate protocol, generator, manifest, and digest.  A later implementation must use distinct protocol identifiers, generator identifiers, manifest filenames, digest fields, and result-binding fields so v1 and v2 artifacts cannot be confused.

### 1.3 Completion before learning claims

The v2 construction, structural validation, calibration, threshold assignment, acceptance decisions, manifest, and digest must be completed, reviewed, and frozen before any neural/RL evaluation uses v2.  Calibration establishes benchmark usability and tier assignment; it does not establish neural superiority, RL superiority, classical solver inferiority, or global optimality.

### 1.4 Result semantics

The following concepts are distinct and must remain separately named in every manifest, calibration record, and later evaluator record:

- `verified_witness`: an exact nonzero codeword `c` satisfying public `H c = 0` over `F_2` and any stated threshold predicate such as `wt(c) <= W`.
- `certified_exclusion`: an exact proof that no nonzero codeword satisfies a stated predicate under explicitly named finite assumptions; bounded failure to find a witness is not an exclusion.
- `exact_distance`: a certified minimum nonzero codeword weight from a theorem or complete exact enumeration/proof.
- `heuristic_upper_bound`: the best exactly verified nonzero incumbent found by a bounded heuristic or incomplete solver.
- `threshold_hit`: an exactly verified witness with `wt(c) <= W`; this proves threshold feasibility only, not optimality.

## 2. Deterministic random-access randomness

### 2.1 Primitive

The v2 generator must use a repository-controlled SHA-256 counter-based primitive, not Python `hash(...)`, xorshift, NumPy global RNG streams, `random.Random` streams, or any mutable shared PRNG stream across rows, columns, sockets, attempts, or cases.

Define `R(fields..., counter)` as:

1. Serialize a fixed ASCII domain tag `rlmw-h-native-research-v2-random-v1`.
2. Serialize each field as `u32_be(byte_length) || utf8_bytes(value)` for strings, `u32_be(byte_length) || two's-complement-free big-endian bytes` for nonnegative integers, and `u32_be(row_count) || rows` for binary matrices where each row is itself length-delimited.
3. Fields must include, in this order: protocol version, generator ID/version, family, parameter stratum, base seed, construction attempt, logical coordinate or socket identity, draw purpose, and counter.
4. Return `SHA256(serialized_fields)`; longer draws concatenate counter values `0,1,2,...` under the same field tuple.

The later implementation PR must freeze test vectors for representative string, integer, matrix, entry, edge, rejection-attempt, and byte-expansion calls.

### 2.2 Domain separation and rejection discipline

Every logical random value must be independently addressed by all relevant context: protocol version, generator ID/version, family, parameter stratum, base seed, construction batch, construction attempt, candidate index, and logical coordinate or socket identity.  Rejection attempts must be explicitly keyed by attempt number.  A later accepted candidate must never depend on how many random bytes were conditionally consumed by earlier rejected candidates.  No row, column, socket, edge, or case may obtain randomness by advancing a shared mutable stream.

### 2.3 Meaning of coordinate-independent deterministic randomness

In this protocol, coordinate-independent deterministic randomness means random-access, domain-separated generation that avoids algebraic relations caused by adjacent stream positions or state transitions.  It does **not** mean permutation invariance of the resulting code, matrix distribution, hash, or acceptance rule unless a later implementation explicitly implements and proves that property.  The v2 generator must not claim complete coordinate-equivalence detection or permutation-invariant randomness merely because it uses coordinate-keyed hashes.

## 3. Dense full-rank construction

### 3.1 Family name and strata

Dense unknown-distance cases use family `dense_full_rank_hash_v1`.  Public matrices are generated directly as dense parity-check matrices; `H=[I_r|P]` is prohibited as the public construction.

Recommended dense parameter strata are:

| stratum | `n` | target rank `r` | target rate `(n-r)/n` | entry probability | accepted density interval |
| --- | ---: | ---: | ---: | ---: | ---: |
| `dense-n96-r48-p50` | 96 | 48 | 0.500 | 0.500 | [0.46, 0.54] |
| `dense-n128-r64-p50` | 128 | 64 | 0.500 | 0.500 | [0.47, 0.53] |
| `dense-n160-r80-p50` | 160 | 80 | 0.500 | 0.500 | [0.475, 0.525] |
| `dense-n192-r96-p50` | 192 | 96 | 0.500 | 0.500 | [0.48, 0.52] |

### 3.2 Candidate generation

For candidate attempt `a`, entry `(i,j)` of an `r x n` matrix is `1` iff the first 64 bits of `R(..., family=dense_full_rank_hash_v1, attempt=a, coordinate=(i,j), purpose=dense_entry, counter=0)` are below `floor(p * 2^64)`.  Rows and coordinates are generated in canonical order `i=0..r-1`, `j=0..n-1`, but each entry is independently keyed.

### 3.3 Acceptance requirements

Accept the first deterministically indexed candidate satisfying all requirements below:

- exact GF(2) row rank equals `r`;
- empirical density lies in the stratum interval;
- every column is nonzero;
- no two columns are identical;
- raw canonical H hash has not appeared before;
- canonical row-space hash has not appeared before;
- no tracked generator-produced coordinate-equivalence duplicate is known;
- exact small-circuit audit through the preregistered cap in Section 8 passes;
- all provenance fields needed for deterministic regeneration are recorded.

A row operation or coordinate permutation must not be added merely to hide a defective construction.  The underlying generated matrix must satisfy these requirements directly.  Canonical public row order is the generated row order; canonical coordinate order is the generated coordinate order.  Row-space hashes are validation metadata only and do not replace the public matrix.

### 3.4 Dense provenance fields

Each accepted dense candidate must record at least: protocol version, generator ID/version, family ID, parameter stratum ID, base seed, construction batch ID, attempt number, `n`, `r`, target density, accepted density interval, observed density numerator/denominator, raw-H SHA-256, row-space SHA-256, small-circuit cap, validator version, and source commit.

## 4. Sparse construction

### 4.1 Family names

Sparse unknown-distance cases use honestly named families:

- `sparse_simple_biregular_hash_v1` when exact divisibility permits a simple bipartite `(d_v,d_c)`-regular graph with `n d_v = r d_c`.
- `sparse_simple_irregular_hash_v1` only when an explicitly declared degree distribution is used; such cases must not be called regular.

No perturbation may violate the declared degree contract.  If a matrix needs perturbation to pass rank, duplicate-column, girth, or audit checks, that candidate is rejected instead of modified.

### 4.2 Recommended sparse strata

Sparse matrices are public parity-check matrices of size `r x n`; row-reduced matrices are not substituted for public `H`.

| stratum | family | `n` | `r` | `d_v` | `d_c` | minimum girth | notes |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| `sparse-reg-n120-r60-dv3-dc6` | biregular | 120 | 60 | 3 | 6 | 6 | rejects all 4-cycles |
| `sparse-reg-n160-r80-dv3-dc6` | biregular | 160 | 80 | 3 | 6 | 6 | rejects all 4-cycles |
| `sparse-reg-n192-r96-dv3-dc6` | biregular | 192 | 96 | 3 | 6 | 6 | rejects all 4-cycles |
| `sparse-reg-n240-r120-dv3-dc6` | biregular | 240 | 120 | 3 | 6 | 6 | rejects all 4-cycles |
| `sparse-irreg-n180-r90-left-2x3x4` | irregular | 180 | 90 | 60 columns degree 2, 90 degree 3, 30 degree 4 | induced row degrees differ by at most 1 | 6 | optional if regular retries are too costly |

### 4.3 Edge generation

For biregular cases, create `d_v` variable sockets for each coordinate and `d_c` check sockets for each row.  Because `n d_v = r d_c`, both socket sets have equal size.  For attempt `a`, assign every possible variable-socket/check-socket pair an independently keyed 256-bit priority using `R(..., attempt=a, socket_identity=(var_socket, check_socket), purpose=sparse_edge_priority)`.  Process pairs in lexicographic order of `(priority, var_socket, check_socket)`, greedily accepting an edge if both sockets are unused and the edge would not create a parallel edge.  Continue until all sockets are matched or the attempt fails.  The public matrix entry `H[row,column]` is the parity of accepted edges; parallel edges are rejected before parity cancellation can occur.

For irregular cases, the same priority rule applies after constructing declared variable sockets and row socket quotas from the stratum.  Row quotas must be deterministic and differ by at most one unless the stratum declares exact row degrees.

### 4.4 Sparse acceptance requirements

A sparse candidate is accepted only if:

- all variable and check degrees exactly match the declared regular contract, or the exact declared irregular distribution;
- no parallel edge exists;
- no column is zero;
- no columns repeat;
- the Tanner graph has no 4-cycles, giving girth at least 6; if a future stratum relaxes this, its minimum-girth requirement must be stated in the stratum ID;
- exact GF(2) row rank equals the target rank for the stratum unless the stratum explicitly permits a rank range; public `H` remains sparse and is not row-reduced;
- raw-H and row-space hashes are new;
- tracked generator-produced coordinate-equivalence duplicates are absent;
- exact small-circuit audit through the preregistered cap passes.

Each stratum has `max_attempts = 20000`.  Exhausting attempts without an accepted candidate is a structured generation failure for that stratum and must be reviewed; it must not silently relax degree, girth, rank, or duplicate requirements.

## 5. Control and artificial-case strata

The v2 corpus must keep these strata separate in manifests, calibration summaries, and claims:

1. `exact_control`: theorem-backed or exhaustively certified exact-distance controls.
2. `dense_unknown`: dense full-rank unknown-distance research cases.
3. `sparse_unknown`: sparse unknown-distance research cases.
4. `planted_evaluator_only` (optional): artificial cases with evaluator-only planted witnesses.

If planted cases are retained, planted witnesses must never enter solver payloads, neural/RL training inputs, or public threshold-calibration inputs except as explicitly evaluator-only metadata.  Their stratum remains separate from natural dense/sparse claims.  Planting and hiding must be mathematically specified: for example, select a nonzero witness `c`, generate rows orthogonal to `c` by the same random-access primitive under a planted-family domain, require target rank, then optionally apply an explicitly recorded invertible row operation and generator-tracked coordinate permutation.  Such transformations are allowed only because the stratum is labelled artificial and evaluator-only.  A planted witness is an upper bound, not an exact-distance label.  Test-set performance claims must not be based solely on planted cases.

## 6. Quantitative corpus plan

### 6.1 Recommended accepted corpus

The recommended frozen v2 corpus contains 192 accepted cases, substantially larger than v1 but still compact enough for manifest review and later replicated evaluation.

| stratum class | subfamilies / strata | accepted cases per stratum | total cases | train | validation | test | smoke cases |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| theorem/exact controls | Hamming/extended Hamming/RM and capped random controls at 8 small sizes | 2 | 16 | 6 | 5 | 5 | 8 |
| dense unknown | 4 dense strata in Section 3 | 18 | 72 | 40 | 16 | 16 | 8 |
| sparse unknown | 4 regular sparse strata in Section 4 | 18 | 72 | 40 | 16 | 16 | 8 |
| optional planted evaluator-only | 4 planted strata matched to dense/sparse sizes | 8 | 32 | 16 | 8 | 8 | 4 |
| **total** | 20 parameter strata | — | **192** | **102** | **45** | **45** | **28** |

If optional planted cases are omitted, the corpus has 160 cases; this is a different protocol choice and requires a distinct candidate protocol version before calibration begins.

### 6.2 Expected manifest size

The manifest stores canonical binary row strings, public `W` after acceptance, opaque case IDs, run specs, digests, and non-secret validation metadata.  Expected compact JSON size is approximately 1.5--3.5 MiB for 192 cases, assuming dense row strings dominate (`sum r*n` bits plus JSON overhead).  This is acceptable for GitHub once frozen; generated candidate pools and calibration JSONL remain external.

### 6.3 Cost estimates used to choose size

Structural exact small-circuit replay is capped at column-subset weight 6 for unknown cases unless PR B changes the cap with review.  Worst-case subset counts are `C(240,1)+...+C(240,6) ≈ 2.0e11`, so the implementation must use ordered meet-in-the-middle/XOR replay rather than naive enumeration and must record resource limits.  Smoke validation covers 28 cases with rank, regeneration, hash, degree/density, no-duplicate, and small-circuit spot replay suitable for ordinary CI.

Calibration hardware is separately provisioned.  Recommended calibration uses three solver-disabled baselines, one optional stronger ISD family if available before freeze, and one solver-assisted CP-SAT reference, with budgets in Section 9.  Expected calibration run records before threshold freeze:

- Solver-disabled: `192 cases * 3 algorithms * 4 budgets * 8 calibration seeds = 18432` records.
- Optional stronger ISD: `192 * 1 * 3 budgets * 6 seeds = 3456` records.
- Solver-assisted reference: `192 * 2 time limits * 3 seeds = 1152` records.
- Total planned calibration records: 19584 without stronger ISD, 23040 with stronger ISD.

Candidate evaluations are budget-dependent.  With solver-disabled budget ladder `[2^12, 2^14, 2^16, 2^18]`, maximum planned solver-disabled candidate evaluations are `192 * 3 * 8 * (4096+16384+65536+262144) = 1,603,256,832`, before early stops.  Later replicated evaluation uses disjoint evaluation seeds and may repeat only accepted cases and selected budgets, expected at 25--50% of calibration volume.

## 7. Lineage and split isolation

### 7.1 Identifiers

V2 must define these identifiers separately:

- `case_id`: opaque stable manifest identifier, not meaningful as a seed or lineage.
- `family_id`: construction family such as `dense_full_rank_hash_v1`.
- `parameter_stratum_id`: explicit size/rate/density/degree stratum.
- `construction_batch_id`: planned batch, base seed, and retry envelope for generation.
- `lineage_group_id`: meaningful group for split isolation; it must not include a unique case ID merely to make isolation tests pass.
- `public_h_sha256`: SHA-256 of canonical public H row strings.
- `row_space_sha256`: SHA-256 of canonical GF(2) row-space representation.

### 7.2 Split isolation rule

All matrices derived from the same latent base matrix, seed lineage, planted witness, transformation orbit, coordinate permutation orbit produced by the generator, or rejection batch must remain in one split.  Splits are assigned at `lineage_group_id` granularity, not per case.

### 7.3 Duplicate and equivalence handling

Known raw-H duplicates and row-space duplicates are rejected.  The generator must also track and reject coordinate permutations that it itself produces, including planted hiding permutations, canonical coordinate reorderings, and any optional family-defined coordinate shuffles.  V2 does not claim a complete general code-equivalence oracle.  Residual equivalence risk from unrelated matrices that define permutation-equivalent codes remains explicit and appears in the risk register.

## 8. Structural pre-acceptance checks

Before calibration, every candidate must pass preregistered checks:

- deterministic regeneration from provenance;
- dimensions and genuine integer fields, rejecting booleans/floats where integers are required;
- binary entries only;
- expected exact GF(2) rank;
- nontrivial kernel for research cases;
- no zero columns and no repeated columns;
- no duplicate raw-H hash, row-space hash, or prohibited lineage overlap;
- family-specific density or degree contracts;
- family-specific girth/cycle contracts;
- exact `H c = 0` verification for every evaluator witness;
- exact small-circuit audit through fixed cap 6 for unknown cases and exact replay cap appropriate to controls;
- known-distance replay for controls;
- absence of the v1 xorshift relation and other explicitly tested generator artifacts, including adjacent-coordinate stream relation probes and the known deterministic weight-3 circuit templates found in v1.

Candidate-specific structural failures caused by random draw outcomes, such as wrong rank, duplicate columns, bad density, 4-cycles, small circuits, or duplicate hashes, reject the candidate and cause the next deterministically indexed retry.  Serialization ambiguity, non-integer fields, invalid stratum definitions, impossible degree equations, hash test-vector mismatch, hidden mutable RNG use, failure to regenerate, malformed provenance, or contradiction between public H and metadata abort generation as protocol defects.

## 9. Preregistered classical calibration

Calibration occurs before neural/RL use and remains outside GitHub until reviewed.  Solver-disabled and solver-assisted strata are never pooled.

### 9.1 Baseline algorithms

Calibration inherits existing baseline-layer IDs where applicable:

- `uniform_kernel_sampling_v1` (solver-disabled);
- `fixed_weight_subset_sampling_v1` (solver-disabled);
- `lee_brickell_isd_v1` (solver-disabled);
- `cp_sat_threshold_reference_v1` (solver-assisted threshold feasibility reference).

Before freezing v2, the project should either add and preregister at least one stronger solver-disabled ISD variant, such as Stern/Dumer-style ISD, or explicitly record that v2 calibration used only the inherited baselines and that stronger-ISD coverage remains a limitation.  This choice is frozen before threshold assignment.

### 9.2 Seeds, repetitions, and budgets

Calibration-only seed set: eight 128-bit hex seeds labelled `calib-00` through `calib-07`, derived from the random-access primitive under purpose `calibration_seed` and frozen in PR B or PR C before execution.  Evaluation-only seed set: eight disjoint seeds labelled `eval-00` through `eval-07`, frozen at the same time but never used for W selection.

Solver-disabled candidate-evaluation budget ladder is `[2^12, 2^14, 2^16, 2^18]` for all applicable algorithms unless a stronger ISD algorithm has an algorithm-specific deterministic operation budget declared before calibration.  CP-SAT reference limits are two strata: 60 seconds / deterministic-time limit 1e7 and 600 seconds / deterministic-time limit 1e8 per case/seed, subject to the solver's deterministic-time support being recorded.

Every incumbent and threshold hit must be exactly verified against the original public `H`.  Calibration JSONL, logs, intermediate candidates, and summaries are generated outputs stored outside GitHub.  Canonical validation must bind each record to source commit, protocol version, generator/config digest, manifest candidate digest, public H hash, algorithm ID, seed, budget, and solver stratum.

Calibration failure, timeout, unknown solver status, or bounded exhaustion never becomes a certified lower bound.

## 10. Threshold policy

Threshold `W` is assigned without neural/RL results and without evaluation-seed leakage.

### 10.1 Controls

For exact controls, `W` may be derived from theorem-backed or exact replayed distance.  If the intended task is threshold feasibility at the exact distance, set `W = d_exact`.  If the intended task is an above-distance smoke task, record the offset explicitly.  Controls must state whether `W` is exact-distance-derived.

### 10.2 Unknown-distance cases

For dense and sparse unknown-distance cases, `W` must be derived only from preregistered classical calibration evidence using calibration seeds.  Let `U` be the multiset of exactly verified solver-disabled incumbent weights at the maximum calibration budget from accepted calibration records.  Solver-assisted upper bounds are reported separately and may influence rejection/tier labels but do not directly set solver-disabled-comparison thresholds unless the protocol version explicitly says so before calibration.

Recommended deterministic statistic: set `W` to the 25th percentile of `U` using nearest-rank `ceil(0.25*|U|)`, after sorting ascending, provided at least 12 verified solver-disabled incumbents exist and at least two solver-disabled algorithms contributed incumbents.  Ties keep the tied integer weight.  Missing results are excluded from `U` but counted in resource-limit frequency.  If requirements are not met, the case is rejected as insufficiently bounded or labelled calibration-incomplete outside the main benchmark.

Minimum certified lower-bound information for unknown cases is the small-circuit lower bound from the preregistered cap and any stronger exact lower bound available before threshold freeze.  The lower bound must be recorded as `certified_lower_bound`, and `W` must never be described as minimum distance unless `W = exact_distance` is certified.

Reject a case as too easy if all solver-disabled algorithms hit `W` at the smallest budget in at least 90% of calibration runs.  Reject as too hard if fewer than 10% of maximum-budget calibration runs hit `W` or no usable verified upper bound exists.  Reject as insufficiently bounded if the gap `best_verified_upper_bound - certified_lower_bound` exceeds the tier's maximum gap and no tier is designed for such uncertainty.  Once frozen, `W` must not change after examining neural/RL results.

### 10.3 Planted cases

For planted evaluator-only cases, the planted weight is an evaluator upper bound only.  Thresholds may use the planted upper bound only inside the planted stratum and must be labelled artificial.  Planted thresholds do not support natural dense/sparse difficulty claims.

## 11. Difficulty tiers and acceptance criteria

Difficulty tiers are assigned by replicated classical evidence rather than subjective labels.

| tier | required evidence | rejection / relabel rules |
| --- | --- | --- |
| `control_exact` | exact distance or theorem replay; threshold semantics explicit | reject if exact replay fails or public H differs from proof object |
| `easy_calibrated` | certified lower bound recorded; solver-disabled max-budget hit rate 60--90%; smallest-budget hit rate below 90%; at least two algorithms agree within weight gap 2 | reject as trivial if smallest-budget saturation occurs |
| `medium_calibrated` | hit rate 20--60%; incumbent interquartile range at least 1; best solver-disabled upper bound and solver-assisted upper bound reported separately; resource-limit frequency below 50% | reject if threshold unreachable at max budget or all evidence comes from one algorithm |
| `hard_calibrated` | hit rate 10--20%; verified upper bound exists; certified lower-bound gap explicitly accepted; solver-assisted reference separately reported | label experimental/hard, not standard, if resource limits exceed 50% |
| `calibration_incomplete` | structural checks pass but upper/lower evidence is insufficient | exclude from main accepted benchmark unless a future protocol explicitly includes incomplete cases |

A case is rejected or separately labelled when it is structurally trivial, all baselines saturate it at the smallest budget, no usable verified upper bound is obtained, its threshold is unreachable under the maximum calibration budget, construction artifacts dominate its solution, resource-limit frequency prevents interpretation, algorithms/repetitions disagree beyond tier limits, or evidence is too weak for the intended tier.  Bound gaps, solver-disabled upper bounds, and solver-assisted upper bounds must remain separate fields.

## 12. Leakage prevention

Data visibility must be separated as follows:

| actor / phase | visible data | prohibited data by default |
| --- | --- | --- |
| generator | protocol, stratum definitions, base seeds, attempts, structural validators | calibration outcomes, neural/RL outcomes |
| calibration evaluator | public H, structural metadata needed for validation, calibration seeds, algorithm budgets | evaluation seeds, neural/RL results, hidden planted witnesses except evaluator-only verification when required |
| solver-facing input | opaque case ID, public H, public W after freeze, allowed run configuration | split labels, family/lineage metadata, exact distances, planted witnesses, calibration incumbents, evaluator certificates |
| neural/RL training | only fields explicitly public in the later experimental protocol; train split cases only | validation/test labels, hidden certificates, calibration incumbents unless deliberately released as public training data in a later protocol |
| final evaluator | public solver submissions plus evaluator-only certificates and hidden metadata for scoring | mutation of W, post-hoc split reassignment |

Split labels, family/lineage metadata, exact distances, planted witnesses, calibration incumbents, solver-assisted results, and evaluator certificates are excluded from solver payloads unless a later experimental protocol explicitly treats a field as public and freezes that decision before training.

## 13. Versioning and staged workflow

The v2 workflow is staged:

1. **PR A:** this design specification.  No generator or manifest exists.
2. **PR B:** candidate v2 generator, validator, independent tests, RNG test vectors, and candidate-manifest tooling only.  Generated candidates remain uncommitted.
3. **External calibration phase:** candidate generation, structural audits, and calibration runs occur outside GitHub; outputs remain uncommitted but are bound to source commits and digests.
4. **PR C:** evidence-backed review of acceptance decisions plus frozen v2 manifest/protocol/digest if accepted.
5. **Later PR:** replicated classical evaluation on the frozen manifest using evaluation seeds.
6. **Only afterward:** controlled neural/RL comparison.

After PR B, RNG serialization, generator version, stratum definitions, validator checks, and calibration seed derivation are immutable for that candidate protocol.  After PR C, accepted public H matrices, W values, split assignments, digests, run specs, and public schemas are immutable.  Failed calibration leads to a new candidate protocol version, not silent mutation of cases, W, seeds, or acceptance rules.

## 14. Resource and storage policy

Generated candidates, audit JSONL, calibration runs, checkpoints, plots, logs, and large intermediate matrices are not committed.  GitHub stores source, specifications, frozen compact manifests, and tests only.  Runtime artifacts may be stored in Google Drive or other external storage, but they are not source identity.  Every external artifact must bind source commit, generator/config digest, manifest or candidate-manifest digest, public H hashes, seeds, protocol versions, algorithm IDs, and run budgets.

## 15. Risks and explicit limitations

| risk | mitigation | unresolved limitation |
| --- | --- | --- |
| residual code equivalence | reject raw-H, row-space, and generator-tracked coordinate-permutation duplicates; group known orbits in one split | no complete general code-equivalence oracle is claimed |
| calibration overfitting | freeze calibration seeds, thresholds, and acceptance rules before neural/RL use; reserve disjoint evaluation seeds | public calibration policy may still influence later method design |
| public benchmark adaptation | keep evaluator-only metadata hidden and stage replicated evaluation before neural/RL comparison | once manifest is public, algorithms can tune to public H/W distributions |
| sparse-construction rejection cost | bounded deterministic retries, girth/rank prechecks, optional honestly named irregular family | some strata may fail within cap and require a new protocol version |
| dense minimum-distance hardness | use small-circuit lower bounds, replicated classical upper bounds, and clear gap reporting | unknown cases generally lack exact distances |
| insufficient statistical power | use 160--192 cases with train/validation/test splits and replicated seeds | still not enough for all fine-grained neural/RL claims |
| computational cost | budget ladder, smoke subset, external calibration hardware, compact committed manifest only | full calibration may be expensive and time-consuming |
| planted-case artificiality | separate planted stratum and prohibit natural-case claims from planted-only evidence | planted performance may not transfer to natural dense/sparse cases |
| inability of bounded search to prove absence | never convert bounded exhaustion or solver `UNKNOWN` into lower bounds | many hard-case exclusions remain uncertified |
