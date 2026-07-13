# h-native-research-v2 construction and calibration protocol design

This document is the normative, implementation-ready design for `h-native-research-v2`. It specifies policy only: no v2 generator, manifest, thresholds, case records, baseline output, notebook cells, neural/RL experiments, or generated matrices are introduced by this document.

The protocol directly addresses the known `h-native-research-v1` defects: sequential xorshift state created deterministic weight-3 circuits; dense `H=[I_r|P]` exposed identity-column structure; sparse cases called regular were later perturbed and therefore not regular; thresholds were weakly calibrated; lineage identifiers were often unique per case; a 23-case pilot was too small for broad claims; coordinate-permuted equivalent codes were not detected by row-space hashes; and preliminary bounds plus first-14-column searches were inadequate.

## 1. Scope and claim boundaries

### 1.1 Immutable v1 status

`h-native-research-v1` remains byte-frozen and quarantined for contract/audit regression only. Its manifest, semantic digest, all cases, labels, thresholds, audit conclusions, baseline protocols, seeds, budgets, result schemas, and quarantine conclusions must not be changed by v2 work. V1 must not be used for solver-superiority, neural-quality, RL-quality, or benchmark-difficulty claims.

### 1.2 Separate v2 identity

This document defines protocol ID `h-native-research-v2-design-pr58`. A later generator PR must implement candidate protocol ID `h-native-research-v2-candidate-v1` unless this design is changed in a reviewed documentation PR before implementation. The generator, manifest, digest, result schemas, and external artifact bindings must use distinct v2 identifiers so v1 and v2 artifacts cannot be confused.

### 1.3 Completion before learning claims

The v2 construction, structural validation, calibration, threshold assignment, acceptance decisions, manifest, and digest must be completed, reviewed, and frozen before any neural/RL evaluation uses v2. Calibration establishes benchmark usability and tier assignment; it does not establish neural superiority, RL superiority, classical solver inferiority, or global optimality.

### 1.4 Result semantics

The following concepts are distinct and must remain separately named in every manifest, calibration record, and later evaluator record:

- `verified_witness`: an exact nonzero codeword `c` satisfying public `H c = 0` over `F_2` and any stated threshold predicate such as `wt(c) <= W`.
- `certified_exclusion`: an exact proof that no nonzero codeword satisfies a stated predicate under explicitly named finite assumptions; bounded failure to find a witness is not an exclusion.
- `exact_distance`: a certified minimum nonzero codeword weight from a theorem or complete exact enumeration/proof.
- `heuristic_upper_bound`: the best exactly verified nonzero incumbent found by a bounded heuristic or incomplete solver.
- `threshold_hit`: an exactly verified witness with `wt(c) <= W`; this proves threshold feasibility only, not optimality.

## 2. Deterministic random-access randomness

### 2.1 Typed byte encoding

The v2 generator must use a repository-controlled SHA-256 counter-based primitive, not Python `hash(...)`, xorshift, NumPy global RNG streams, `random.Random` streams, or any mutable shared PRNG stream across rows, columns, sockets, attempts, or cases.

All inputs to the primitive use the following exact typed encoding. Unsupported or ambiguous values are protocol defects and must be rejected before hashing.

| type | encoded form |
| --- | --- |
| string | tag `S` (`0x53`) || `u32_be(byte_len)` || UTF-8 bytes; strings must be Unicode-normalized NFC and must not contain NUL |
| nonnegative integer | tag `I` (`0x49`) || `u32_be(byte_len)` || minimal unsigned big-endian bytes; zero has `byte_len=1` and byte `0x00`; booleans, negatives, floats, and decimal strings standing in for integers are rejected |
| bytes | tag `B` (`0x42`) || `u32_be(byte_len)` || raw bytes |
| tuple | tag `T` (`0x54`) || `u32_be(item_count)` || encoded items in order |
| list | tag `L` (`0x4c`) || `u32_be(item_count)` || encoded items in order; lists are allowed only where this document explicitly names them |
| binary row | tag `R` (`0x52`) || `u32_be(bit_len)` || packed bits in canonical coordinate order, most-significant bit first in each byte with zero padding in the final byte |
| binary matrix | tag `M` (`0x4d`) || `u32_be(row_count)` || `u32_be(col_count)` || encoded binary rows in public row order; every row must have `col_count` bits |
| logical coordinate | tuple `("coord", row_index, column_index)` for matrix entries, or `("coord1", column_index)` for one-dimensional coordinate draws |
| socket identity | tuple `("socket", variable_index, variable_socket_index, check_index, check_socket_index)` |
| edge identity | tuple `("edge", variable_index, variable_socket_index, check_index, check_socket_index)` |
| draw purpose | string from a frozen enum, for example `dense_entry`, `sparse_edge_priority`, `planted_witness_coordinate`, `planted_orthogonal_row_free_bit`, `row_operation_entry`, `coordinate_permutation_key`, `calibration_seed`, or `evaluation_seed`; hash domains use explicit purpose strings such as `public_h_hash`, `row_space_hash`, `lineage_group_id`, and `split_key` |
| construction attempt | nonnegative integer field named `construction_attempt`; it indexes rejected candidate retries |
| expansion counter | nonnegative integer field named `expansion_counter`; it expands bytes within one logical draw and is not a retry counter |

Length prefixes are four-byte unsigned big-endian integers. Implementations must reject lengths exceeding `2^32-1`, noncanonical integer encodings with leading zero bytes, duplicate map-like field names, and any type not listed above.

### 2.2 Random-access primitive and field order

Define `R(context, expansion_counter)` as `SHA256(encoded_context || encoded_expansion_counter)`, where `encoded_expansion_counter` is the typed integer encoding of the byte-expansion counter. To obtain more than 32 bytes for one logical draw, concatenate `R(context, 0) || R(context, 1) || ...` and truncate to the requested byte length.

The `context` is the typed tuple below in exactly this order:

1. literal domain string `rlmw-h-native-research-v2-random-v1`;
2. protocol version string, initially `h-native-research-v2-candidate-v1`;
3. generator ID/version string;
4. family ID string;
5. parameter stratum ID string;
6. construction batch ID string;
7. case slot / candidate index integer within the batch;
8. base seed bytes;
9. construction attempt integer;
10. logical coordinate, socket identity, edge identity, or another purpose-specific tuple;
11. draw purpose string.

Sections 2.1 and 2.2 are a single contract: the construction attempt is field 9 in the context, while the expansion counter is outside the context and is used only to expand bytes for that same logical draw. No implementation may swap these roles or omit construction batch ID, case slot, construction attempt, logical identity, or draw purpose.

### 2.3 Domain separation and rejection discipline

Every logical random value must be independently addressed by all relevant context fields above. Rejection attempts must be explicitly keyed by construction attempt number. A later accepted candidate must never depend on how many random bytes were conditionally consumed by earlier rejected candidates. No row, column, socket, edge, case, calibration seed, or evaluation seed may obtain randomness by advancing a shared mutable stream.

The later implementation PR (PR B) must freeze representative test vectors for strings, integers, tuples, lists, matrices, dense entries, sparse edge priorities, planted witness coordinates, rejection attempts, and byte expansion.

### 2.4 Meaning of coordinate-independent deterministic randomness

In this protocol, coordinate-independent deterministic randomness means random-access, domain-separated generation that avoids algebraic relations caused by adjacent stream positions or state transitions. It does **not** mean permutation invariance of the resulting code, matrix distribution, hash, or acceptance rule unless a later implementation explicitly implements and proves that property. The v2 generator must not claim complete coordinate-equivalence detection or permutation-invariant randomness merely because it uses coordinate-keyed hashes.

## 3. Dense full-rank construction

### 3.1 Family name and strata

Dense unknown-distance cases use family `dense_full_rank_hash_v1`. Public matrices are generated directly as dense parity-check matrices; `H=[I_r|P]` is prohibited as the public construction.

| stratum | `n` | target rank `r` | target rate `(n-r)/n` | entry probability | accepted density interval |
| --- | ---: | ---: | ---: | ---: | ---: |
| `dense-n96-r48-p50` | 96 | 48 | 0.500 | 0.500 | [0.46, 0.54] |
| `dense-n128-r64-p50` | 128 | 64 | 0.500 | 0.500 | [0.47, 0.53] |
| `dense-n160-r80-p50` | 160 | 80 | 0.500 | 0.500 | [0.475, 0.525] |
| `dense-n192-r96-p50` | 192 | 96 | 0.500 | 0.500 | [0.48, 0.52] |

### 3.2 Candidate generation

For candidate attempt `a`, entry `(i,j)` of an `r x n` matrix is `1` iff the first 64 bits of `R(context, 0)` are below `floor(p * 2^64)`, where context field 9 is `a`, field 10 is `("coord", i, j)`, and field 11 is `dense_entry`. Rows and coordinates are emitted in canonical order `i=0..r-1`, `j=0..n-1`, but each entry is independently keyed.

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

A row operation or coordinate permutation must not be added merely to hide a defective construction. The underlying generated matrix must satisfy these requirements directly. Canonical public row order is the generated row order; canonical coordinate order is the generated coordinate order. Row-space hashes are validation metadata only and do not replace the public matrix.

## 4. Sparse construction

### 4.1 Family names and strata

Sparse unknown-distance cases use honestly named families:

- `sparse_simple_biregular_hash_v1` when exact divisibility permits a simple bipartite `(d_v,d_c)`-regular graph with `n d_v = r d_c`.
- `sparse_simple_irregular_hash_v1` only when an explicitly declared degree distribution is used; such cases must not be called regular.

No perturbation may violate the declared degree contract. If a matrix needs perturbation to pass rank, duplicate-column, girth, or audit checks, that candidate is rejected instead of modified.

| stratum | family | `n` | `r` | `d_v` | `d_c` | minimum girth | accepted cases |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `sparse-reg-n120-r60-dv3-dc6` | biregular | 120 | 60 | 3 | 6 | 6 | 18 |
| `sparse-reg-n160-r80-dv3-dc6` | biregular | 160 | 80 | 3 | 6 | 6 | 18 |
| `sparse-reg-n192-r96-dv3-dc6` | biregular | 192 | 96 | 3 | 6 | 6 | 18 |
| `sparse-reg-n240-r120-dv3-dc6` | biregular | 240 | 120 | 3 | 6 | 6 | 18 |

`h-native-research-v2-candidate-v1` does not include irregular sparse cases. A later irregular extension must use a distinct protocol ID and complete strata definitions before generation.

### 4.2 Edge generation

For biregular cases, create `d_v` variable sockets for each coordinate and `d_c` check sockets for each row. Because `n d_v = r d_c`, both socket sets have equal size. For attempt `a`, assign every possible variable-socket/check-socket pair an independently keyed 256-bit priority using purpose `sparse_edge_priority` and socket identity `("socket", variable_index, variable_socket_index, check_index, check_socket_index)`. Process pairs in lexicographic order of `(priority, variable_index, variable_socket_index, check_index, check_socket_index)`, greedily accepting an edge if both sockets are unused and the edge would not create a parallel edge. Continue until all sockets are matched or the attempt fails. The public matrix entry `H[row,column]` is the parity of accepted edges; parallel edges are rejected before parity cancellation can occur.

### 4.3 Sparse acceptance requirements

A sparse candidate is accepted only if:

- all variable and check degrees exactly match the declared regular contract;
- no parallel edge exists;
- no column is zero;
- no columns repeat;
- the Tanner graph has no 4-cycles, giving girth at least 6;
- exact GF(2) row rank equals the target rank for the stratum; public `H` remains sparse and is not row-reduced;
- raw-H and row-space hashes are new;
- tracked generator-produced coordinate-equivalence duplicates are absent;
- exact small-circuit audit through the preregistered cap passes.

Each stratum has `max_attempts = 20000`. Exhausting attempts without an accepted candidate is a structured generation failure for that stratum and must be reviewed; it must not silently relax degree, girth, rank, or duplicate requirements.

## 5. Control and planted strata

The normative `h-native-research-v2-candidate-v1` corpus contains 192 cases, including planted evaluator-only cases. If planted cases are removed, that is a different protocol (`h-native-research-v2-no-planted-candidate-v1`) requiring a complete design update before PR B.

### 5.1 Exact/control strata

The 16 exact controls are fully enumerated below. Controls are split-isolated from unknown and planted cases and must replay their theorem/exact-distance certificate before acceptance.

| stratum | construction | `n` | target rank `r` | cases | exact-distance source |
| --- | --- | ---: | ---: | ---: | --- |
| `ctrl-hamming-m4` | binary Hamming parity check | 15 | 4 | 2 | theorem `d=3` |
| `ctrl-hamming-m5` | binary Hamming parity check | 31 | 5 | 2 | theorem `d=3` |
| `ctrl-ext-hamming-m4` | extended Hamming parity check | 16 | 5 | 2 | theorem `d=4` |
| `ctrl-ext-hamming-m5` | extended Hamming parity check | 32 | 6 | 2 | theorem `d=4` |
| `ctrl-rm1-m5` | parity check for RM(1,5) | 32 | 26 | 2 | exhaustive/theorem `d=16` |
| `ctrl-rm1-m6` | parity check for RM(1,6) | 64 | 57 | 2 | exhaustive/theorem `d=32` |
| `ctrl-random-k8-n24` | capped exact random-access dense control | 24 | 16 | 2 | exhaustive kernel replay, `k=8` |
| `ctrl-random-k10-n32` | capped exact random-access dense control | 32 | 22 | 2 | exhaustive kernel replay, `k=10` |

For controls with two cases per stratum, `case_slot` values `0` and `1` generate either the deterministic canonical theorem matrix (case 0) or a generator-tracked coordinate permutation plus invertible row operation (case 1). Both variants share one `lineage_group_id` and must remain in one split.

### 5.2 Planted evaluator-only strata

The 32 planted cases are artificial controls for leakage and plumbing diagnostics, not natural dense/sparse evidence. Planted witnesses never enter solver payloads, neural/RL training inputs, or threshold-fitting records unless a later experimental protocol explicitly makes a planted-training field public before training.

| stratum | base family | `n` | `r` | planted weight `w_p` | cases | split allocation |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| `planted-dense-n96-r48-w10` | dense orthogonal rows | 96 | 48 | 10 | 8 | 4 train / 2 validation / 2 test |
| `planted-dense-n128-r64-w12` | dense orthogonal rows | 128 | 64 | 12 | 8 | 4 train / 2 validation / 2 test |
| `planted-sparse-n120-r60-w10` | sparse orthogonal rows | 120 | 60 | 10 | 8 | 4 train / 2 validation / 2 test |
| `planted-sparse-n160-r80-w12` | sparse orthogonal rows | 160 | 80 | 12 | 8 | 4 train / 2 validation / 2 test |

Deterministic planted construction for each case slot and attempt:

1. Select witness support by ranking all coordinates by 256-bit purpose `planted_witness_coordinate`; take the first `w_p` coordinates and set those witness bits to 1.
2. Generate candidate rows independently. For dense planted rows, draw all but the largest witness coordinate with purpose `planted_orthogonal_row_free_bit`; set the largest witness coordinate to the parity needed for row dot witness = 0.
3. For sparse planted rows, every row has weight exactly 6; planted sparse cases do not claim regular column degrees, and their Tanner graph must still pass the no-4-cycle check used for sparse unknown cases. For row `i`, rank all coordinates by 256-bit purpose `planted_sparse_row_coordinate_priority` with logical identity `("planted_sparse_row", i, coordinate)`. Let `S0` be the first six coordinates. If `|S0 ∩ supp(c)|` is even, use `S0`. If it is odd, make exactly one deterministic exchange that toggles this parity while preserving row weight 6: first rank `remove_selected = S0 ∩ supp(c)` by purpose `planted_sparse_exchange_remove_selected` and `add_nonwitness = {0..n-1} \ (S0 ∪ supp(c))` by purpose `planted_sparse_exchange_add_nonwitness`; if both are nonempty, replace the first `remove_selected` coordinate with the first `add_nonwitness` coordinate. Otherwise rank `remove_nonwitness = S0 \ supp(c)` by purpose `planted_sparse_exchange_remove_nonwitness` and `add_witness = supp(c) \ S0` by purpose `planted_sparse_exchange_add_witness`; if both are nonempty, replace the first `remove_nonwitness` coordinate with the first `add_witness` coordinate. Both exchange types change `|row ∩ supp(c)|` by exactly one, enforce row dot witness = 0, and keep row weight exactly 6. All priority ties are broken lexicographically by coordinate. If neither exchange is possible, if the resulting row weight is not 6, if the final witness intersection is not even, if a zero/repeated column appears after all rows are built, if the Tanner graph has a 4-cycle, or if rank/audit checks fail, reject the construction attempt.
4. Public row order is generated order. Rank handling is by rejection only: accept only if exact GF(2) row rank equals `r`. Do not row-reduce the public matrix.
5. Apply an invertible row operation only if generated by purpose `row_operation_entry` and recorded as evaluator-only provenance; this operation is not used to hide a defective base matrix because the pre-transform matrix must already satisfy rank, no-zero/repeated-column, and audit checks.
6. Apply a coordinate permutation generated by sorting 256-bit purpose `coordinate_permutation_key`; record the permutation orbit in evaluator-only provenance and lineage. The public H is after this permutation, and the planted witness is permuted accordingly for evaluator verification only.
7. Validate exact `H c = 0`, `wt(c)=w_p`, no zero/repeated columns, rank, duplicate hashes, lineage isolation, and small-circuit audit. The planted witness is an upper bound only, never an exact-distance label.

All matrices derived from the same planted witness before coordinate permutation, row operation, or retry batch share a lineage group and split.

## 6. Quantitative corpus plan

### 6.1 Normative accepted corpus

The frozen v2 corpus contains 192 accepted cases.

| stratum class | subfamilies / strata | accepted cases per stratum | total cases | train | validation | test | smoke cases |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| theorem/exact controls | 8 control strata in Section 5.1 | 2 | 16 | 8 | 4 | 4 | 8 |
| dense unknown | 4 dense strata in Section 3 | 18 | 72 | 40 | 16 | 16 | 8 |
| sparse unknown | 4 regular sparse strata in Section 4 | 18 | 72 | 40 | 16 | 16 | 8 |
| planted evaluator-only | 4 planted strata in Section 5.2 | 8 | 32 | 16 | 8 | 8 | 4 |
| **total** | 20 parameter strata | — | **192** | **104** | **44** | **44** | **28** |

### 6.2 Expected manifest size

The manifest stores canonical binary row strings, public `W` after acceptance, opaque case IDs, run specs, digests, and non-secret validation metadata. Expected compact JSON size is approximately 1.5--3.5 MiB for 192 cases, assuming dense row strings dominate (`sum r*n` bits plus JSON overhead). This is acceptable for GitHub once frozen; generated candidate pools and calibration JSONL remain external.

### 6.3 Cost estimates used to choose size

Structural exact small-circuit replay is capped at column-subset weight 6 for unknown cases unless PR B changes the cap with review. The exact worst-case subset count for `n=240` is `sum(C(240,i), i=1..6) = 255,719,544,468`, so the implementation must use ordered meet-in-the-middle/XOR replay rather than naive enumeration and must record resource limits. Smoke validation covers 28 cases with rank, regeneration, hash, degree/density, no-duplicate, and small-circuit spot replay suitable for ordinary CI.

Calibration hardware is separately provisioned. Required calibration uses three inherited solver-disabled baselines, at least one stronger solver-disabled ISD family, and one solver-assisted CP-SAT reference, with budgets in Section 9. Expected pre-freeze calibration run records:

- Threshold fitting, solver-disabled inherited: `192 cases * 3 algorithms * 4 budgets * 8 threshold seeds = 18432` records.
- Threshold fitting, stronger ISD: `192 * 1 * 4 budgets * 8 threshold seeds = 6144` records.
- Independent tier validation, solver-disabled inherited: `192 * 3 * 4 budgets * 8 tier seeds = 18432` records.
- Independent tier validation, stronger ISD: `192 * 1 * 4 budgets * 8 tier seeds = 6144` records.
- Solver-assisted reference: `192 * 2 time limits * 4 seeds = 1536` records.
- Total planned calibration records: `50688`.

With solver-disabled budget ladder `[2^12, 2^14, 2^16, 2^18]`, maximum threshold-fitting inherited-baseline candidate evaluations are `192 * 3 * 8 * (2^12 + 2^14 + 2^16 + 2^18) = 1,604,321,280`, before early stops. Including stronger ISD and independent tier-validation seeds doubles the inherited workload and adds `2 * 192 * 1 * 8 * (2^12 + 2^14 + 2^16 + 2^18) = 1,069,547,520` stronger-ISD budget units, for `4,278,190,080` total solver-disabled budget units before early stops. Later replicated evaluation uses final evaluation seeds only and is not part of threshold/tier selection.

## 7. Lineage and split isolation

### 7.1 Identifiers

V2 must define these identifiers separately:

- `case_id`: opaque stable manifest identifier, not meaningful as a seed or lineage.
- `family_id`: construction family such as `dense_full_rank_hash_v1`.
- `parameter_stratum_id`: explicit size/rate/density/degree stratum.
- `construction_batch_id`: planned batch, base seed, and retry envelope for generation.
- `case_slot`: deterministic slot inside a stratum and batch; used in RNG context field 7.
- `lineage_group_id`: meaningful group for split isolation; it must not include `case_id`, raw-H hash, or a unique per-case nonce merely to make isolation tests pass.
- `public_h_sha256`: SHA-256 over `encode(("public_h_hash", protocol_id, r, n, H_matrix))`, where `H_matrix` uses the Section 2 binary-matrix encoding in public row and coordinate order.
- `row_space_sha256`: SHA-256 over `encode(("row_space_hash", protocol_id, rref_rank, n, rref_matrix))`, where `rref_matrix` is the canonical GF(2) reduced row echelon form: pivot columns strictly increasing, each pivot column has a single 1, pivot rows sorted by pivot column, all-zero rows removed, and rows encoded with the Section 2 binary-matrix encoding.

### 7.2 Lineage-group construction

Every accepted case belongs to a complete lineage group of size 2. For dense and sparse unknown cases, PR B creates construction batches with two case slots, `case_slot=0` and `case_slot=1`, from the same `construction_batch_id` and base seed family. The two slots are not train/validation/test intended; split assignment occurs only at group level after both slots pass structural validation. Their `lineage_group_id` is `SHA256(encode(("lineage_group_id", protocol_id, family_id, parameter_stratum_id, construction_batch_id)))`, where `encode` is the Section 2 typed tuple/string encoding.

For exact controls, the canonical theorem matrix and its declared coordinate-equivalent variant share one lineage group. For planted cases, all variants derived from the same unpermuted planted witness and base orthogonal-row candidate share one lineage group. Dense, sparse, planted, and paired control group sizes are all exactly 2; exact target counts are reached by deterministic acceptance of complete groups only. Generation must structurally fail if any frozen class or split target is not divisible by its group size.

### 7.3 Deterministic split assignment

Split assignment is performed before calibration and never changed based on calibration, thresholds, neural/RL results, or convenience. For each complete lineage group, compute `split_key = SHA256(encode(("split_key", protocol_id, lineage_group_id)))` using the Section 2 typed tuple/string encoding and sort groups lexicographically by `split_key` within each stratum class. Assign whole groups greedily to meet the exact class-level targets in Section 6.1: exact controls 8/4/4, dense 40/16/16, sparse 40/16/16, and planted 16/8/8. Because every group has size 2, validators must first check that every class split target is divisible by 2. If exact targets cannot be met with accepted complete groups, generation continues with the next deterministic batch; if the frozen allocation cannot be satisfied, generation fails structurally. Post-hoc movement across splits is prohibited.

All matrices derived from the same latent base matrix, seed lineage, planted witness, transformation orbit, coordinate permutation orbit produced by the generator, or rejection batch must remain in one split. Declared coordinate-equivalent control or planted variants are intentionally permitted only within one lineage group and are not statistically independent cases for claim counting unless a later protocol explicitly defines an aggregation rule.

### 7.4 Duplicate and equivalence handling

Known raw-H duplicates and row-space duplicates across different lineage groups are rejected. Declared coordinate-equivalent control variants and declared planted variants are permitted within their own lineage group and recorded as correlated variants. A coordinate permutation applied internally to produce one public planted matrix is recorded in evaluator-only provenance; it must not cause that same case to reject itself. The generator must track generator-produced coordinate permutations and reject equivalent cases that appear across different lineage groups. V2 does not claim a complete general code-equivalence oracle. Residual equivalence risk from unrelated matrices that define permutation-equivalent codes remains explicit and appears in the risk register.

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

Candidate-specific structural failures caused by random draw outcomes, such as wrong rank, duplicate columns, bad density, 4-cycles, small circuits, or duplicate hashes, reject the candidate and cause the next deterministically indexed retry. Serialization ambiguity, non-integer fields, invalid stratum definitions, impossible degree equations, hash test-vector mismatch, hidden mutable RNG use, failure to regenerate, malformed provenance, or contradiction between public H and metadata abort generation as protocol defects.

## 9. Preregistered classical calibration

Calibration occurs before neural/RL use and remains outside GitHub until reviewed. Solver-disabled and solver-assisted strata are never pooled.

### 9.1 Required baseline algorithms

Calibration inherits existing baseline-layer IDs where applicable:

- `uniform_kernel_sampling_v1` (solver-disabled);
- `fixed_weight_subset_sampling_v1` (solver-disabled);
- `lee_brickell_isd_v1` (solver-disabled);
- `cp_sat_threshold_reference_v1` (solver-assisted threshold feasibility reference).

For any future neural/RL performance or superiority claim, v2 must also include at least one preregistered stronger solver-disabled ISD baseline, such as a pinned Stern/Dumer-style implementation with frozen version, parameters, deterministic operation accounting, and validation tests, before v2 is frozen. Merely recording that stronger-ISD coverage is absent is not sufficient for performance-comparison readiness. If the stronger solver-disabled baseline is absent, v2 may be frozen only as corpus infrastructure and must not proceed to neural/RL superiority claims. Solver-disabled and solver-assisted evidence remain separate in all summaries.

### 9.2 Frozen seed sets and deterministic settings

PR B must freeze either the literal seed sets below or a fully deterministic derivation that reproduces them, plus generator/config digest, baseline versions, baseline parameters, dependency versions or containers, thread counts, CP-SAT deterministic-time settings, and operation-count definitions before candidate generation or external calibration begins. PR C occurs only after calibration and must not retroactively choose seeds, budgets, algorithms, deterministic settings, threshold rules, or tier rules.

Use three disjoint seed roles:

- `threshold_fit_seed[0..7]`: used only to fit `W`.
- `tier_validation_seed[0..7]`: used only for acceptance and difficulty-tier validation.
- `final_eval_seed[0..7]`: reserved for later replicated evaluation and never used for W selection or tier acceptance.

The seed bytes are derived with the Section 2 primitive using purpose `calibration_seed` or `evaluation_seed`, case slot `0`, construction attempt `0`, and logical identity `("seed_role", role, index)`. The actual resulting 128-bit hex values must be frozen as PR B test vectors. The CP-SAT reference uses exactly four seed-role/index pairs: `threshold_fit_seed[0]`, `threshold_fit_seed[1]`, `tier_validation_seed[0]`, and `tier_validation_seed[1]`; these provide four deterministic runs per case for each CP-SAT time-limit stratum and are reported only as solver-assisted reference evidence.

### 9.3 Budgets and records

Solver-disabled candidate-evaluation budget ladder is `[2^12, 2^14, 2^16, 2^18]` for all applicable algorithms unless the stronger ISD baseline has an additional deterministic operation count declared before calibration. CP-SAT reference limits are two strata: 60 seconds / deterministic-time limit 1e7 and 600 seconds / deterministic-time limit 1e8 per case/seed, subject to the solver's deterministic-time support being recorded.

Every incumbent and threshold hit must be exactly verified against the original public `H`. Calibration JSONL, logs, intermediate candidates, and summaries are generated outputs stored outside GitHub. Canonical validation must bind each record to source commit, protocol version, generator/config digest, manifest candidate digest, public H hash, algorithm ID, seed role, seed index, budget, and solver stratum.

Calibration failure, timeout, unknown solver status, missing record, resource limit, or bounded exhaustion never becomes a certified lower bound.

## 10. Threshold policy without circular tiering

Threshold `W` is assigned without neural/RL results, without tier-validation records, and without final-evaluation seed leakage.

### 10.1 Controls

For exact controls, `W` may be derived from theorem-backed or exact replayed distance. If the intended task is threshold feasibility at the exact distance, set `W = d_exact`. If the intended task is an above-distance smoke task, record the offset explicitly. Controls must state whether `W` is exact-distance-derived.

### 10.2 Unknown-distance W fitting

For dense and sparse unknown-distance cases, `W` is fitted only from threshold-fit records at maximum budget `2^18` from solver-disabled algorithms, including the required stronger ISD baseline. Let `A_fit` be the frozen set of solver-disabled algorithms and `S_fit` the eight threshold-fit seeds. The denominator for fitting availability is `|A_fit| * |S_fit|`. A record enters the incumbent multiset `U_fit` only if it completed its declared budget and produced an exactly verified nonzero incumbent. Missing records, crashes, resource limits, and no-incumbent completions are excluded from `U_fit` but counted in the fitting availability denominator.

A case is eligible for threshold fitting only if at least 50% of the denominator produced verified incumbents and at least two algorithms, one of which may be the stronger ISD baseline, contributed verified incumbents. Sort `U_fit` ascending by weight. Set `W` to the nearest-rank 40th percentile: rank `max(1, ceil(0.40 * |U_fit|))`. Ties keep the tied integer weight. This percentile is intentionally not used to define tiers; independent tier-validation records determine hit rates.

Minimum certified lower-bound information for unknown cases is the small-circuit lower bound from the preregistered cap and any stronger exact lower bound available before threshold freeze. The lower bound must be recorded as `certified_lower_bound`, and `W` must never be described as minimum distance unless `W = exact_distance` is certified.

### 10.3 Tier-validation aggregation

Tier validation uses only `tier_validation_seed[0..7]` records and all solver-disabled algorithms in `A_fit`. For each budget `b`, the hit-rate denominator is `|A_fit| * 8`. The numerator is the number of records at budget `b` with an exactly verified threshold hit `wt(c) <= W`. Resource-limit frequency at budget `b` uses the same denominator and counts missing records, crashes, solver resource limits, and records that did not complete the declared budget. No-hit completed records count in the hit-rate denominator but not the resource-limit numerator.

Incumbent-weight distributions use exactly verified incumbents from completed maximum-budget tier-validation records. Missing/no-incumbent records are excluded from IQR calculation but counted in resource-limit frequency. IQR is `Q3 - Q1` using nearest-rank quartiles: `Q1` rank `ceil(0.25*m)` and `Q3` rank `ceil(0.75*m)` on sorted verified incumbent weights of size `m`. If `m < 8`, IQR is unavailable and the case is relabelled `calibration_incomplete` unless it is an exact control.

“Algorithms agree within weight gap 2” means at least two distinct solver-disabled algorithms each have at least two completed maximum-budget tier-validation records with verified incumbents, and the medians of their verified incumbent weights differ by at most 2. Medians use lower median for even sample counts.

### 10.4 Difficulty tiers and rejection rules

Difficulty tiers are assigned by independent tier-validation evidence:

| tier | required independent evidence |
| --- | --- |
| `control_exact` | exact distance or theorem replay; threshold semantics explicit |
| `easy_calibrated` | maximum-budget hit rate in [0.70, 0.90); smallest-budget hit rate < 0.70; IQR available; algorithm agreement within gap 2 |
| `medium_calibrated` | maximum-budget hit rate in [0.35, 0.70); smallest-budget hit rate < 0.50; IQR at least 1; resource-limit frequency < 0.25 |
| `hard_calibrated` | maximum-budget hit rate in [0.10, 0.35); verified upper bound exists; resource-limit frequency < 0.50; `best_solver_disabled_upper_bound - certified_lower_bound <= max(12, ceil(0.20*n))` |
| `calibration_incomplete` | structural checks pass but fitting or tier evidence is insufficient |

A case is rejected or assigned the deterministic label `calibration_incomplete` when it is structurally trivial, maximum-budget hit rate is below 0.10, all baselines saturate it at the smallest budget with hit rate >= 0.90, no usable verified upper bound is obtained, the preregistered generator-artifact checks fail, resource-limit frequency exceeds the tier limit, algorithms/repetitions disagree beyond tier limits, fitting availability is below 50%, or the hard-tier certified gap formula is not satisfied. Bound gaps, solver-disabled upper bounds, and solver-assisted upper bounds must remain separate fields.

### 10.5 Planted cases

For planted evaluator-only cases, the planted weight is an evaluator upper bound only. Thresholds may use the planted upper bound only inside the planted stratum and must be labelled artificial. Planted thresholds do not support natural dense/sparse difficulty claims.

## 11. Leakage prevention

Data visibility must be separated as follows:

| actor / phase | visible data | prohibited data by default |
| --- | --- | --- |
| generator | protocol, stratum definitions, base seeds, attempts, structural validators | calibration outcomes, neural/RL outcomes |
| calibration evaluator | public H, structural metadata needed for validation, threshold-fit/tier-validation seeds, algorithm budgets | final evaluation seeds, neural/RL results, hidden planted witnesses except evaluator-only verification when required |
| solver-facing input | opaque case ID, public H, public W after freeze, allowed run configuration | split labels, family/lineage metadata, exact distances, planted witnesses, calibration incumbents, evaluator certificates |
| neural/RL training | only fields explicitly public in the later experimental protocol; train split cases only | validation/test labels, hidden certificates, calibration incumbents unless deliberately released as public training data in a later protocol |
| final evaluator | public solver submissions plus evaluator-only certificates and hidden metadata for scoring | mutation of W, post-hoc split reassignment |

Split labels, family/lineage metadata, exact distances, planted witnesses, calibration incumbents, solver-assisted results, and evaluator certificates are excluded from solver payloads unless a later experimental protocol explicitly treats a field as public and freezes that decision before training.

## 12. Versioning and staged workflow

The v2 workflow is staged:

1. **PR A:** this design specification. No generator or manifest exists.
2. **PR B:** candidate v2 generator, validator, independent tests, RNG test vectors, frozen seed derivation or literal seed sets, deterministic execution settings, baseline versions/parameters, and candidate-manifest tooling only. Generated candidates remain uncommitted.
3. **External calibration phase:** candidate generation, structural audits, and calibration runs occur outside GitHub using PR B-frozen settings; outputs remain uncommitted but are bound to source commits and digests.
4. **PR C:** evidence-backed review of acceptance decisions plus frozen v2 manifest/protocol/digest if accepted. PR C may accept or reject cases under frozen rules; it must not retroactively choose seeds, rules, budgets, or algorithms.
5. **Later PR:** replicated classical evaluation on the frozen manifest using final evaluation seeds.
6. **Only afterward:** controlled neural/RL comparison, and only if the stronger solver-disabled ISD baseline requirement is satisfied.

After PR B, RNG serialization, generator version, stratum definitions, validator checks, seed derivation, baseline versions, baseline parameters, and deterministic execution settings are immutable for that candidate protocol. After PR C, accepted public H matrices, W values, split assignments, digests, run specs, and public schemas are immutable. Failed calibration leads to a new candidate protocol version, not silent mutation of cases, W, seeds, or acceptance rules.

## 13. Resource and storage policy

Generated candidates, audit JSONL, calibration runs, checkpoints, plots, logs, and large intermediate matrices are not committed. GitHub stores source, specifications, frozen compact manifests, and tests only. Runtime artifacts may be stored in Google Drive or other external storage, but they are not source identity. Every external artifact must bind source commit, generator/config digest, manifest or candidate-manifest digest, public H hashes, seeds, protocol versions, algorithm IDs, and run budgets.

## 14. Risks and explicit limitations

| risk | mitigation | unresolved limitation |
| --- | --- | --- |
| residual code equivalence | reject raw-H, row-space, and generator-tracked coordinate-permutation duplicates; group known orbits in one split | no complete general code-equivalence oracle is claimed |
| calibration overfitting | separate threshold-fit, tier-validation, and final-evaluation seeds; freeze rules before generation | public calibration policy may still influence later method design |
| public benchmark adaptation | keep evaluator-only metadata hidden and stage replicated evaluation before neural/RL comparison | once manifest is public, algorithms can tune to public H/W distributions |
| sparse-construction rejection cost | bounded deterministic retries and girth/rank prechecks | some strata may fail within cap and require a new protocol version |
| dense minimum-distance hardness | use small-circuit lower bounds, replicated classical upper bounds, and clear gap reporting | unknown cases generally lack exact distances |
| insufficient statistical power | use 192 cases with 104/44/44 train/validation/test splits and replicated seeds | still not enough for all fine-grained neural/RL claims |
| computational cost | budget ladder, smoke subset, external calibration hardware, compact committed manifest only | full calibration may be expensive and time-consuming |
| planted-case artificiality | separate planted stratum and prohibit natural-case claims from planted-only evidence | planted performance may not transfer to natural dense/sparse cases |
| inability of bounded search to prove absence | never convert bounded exhaustion, missing records, or solver `UNKNOWN` into lower bounds | many hard-case exclusions remain uncertified |
