# ADR: Seam 5 Fusion, Deterministic Selection, and Bounded Packets

- Status: superseded; historical analysis only; full hybrid unapproved
- Date: 2026-07-19
- Scope: the bounded ordinal-fusion experiment is accepted; the broader hybrid remains design-only
- Approval: superseded by `implementation-07-seam-5-accepted-ADR.md`; no approval exists for the full hybrid

## Context

Seams 2B through 4C established executor-local contracts for exact, lexical, vector, and graph retrieval. They now expose bounded candidates, structured statuses, hard/preferred scope evidence, multi-surface provenance, exact-term evidence, vector-query provenance, graph path provenance, and deterministic executor ordering. Seam 5 must decide how those executor outputs become one bounded selected packet without pretending that their raw scores share a scale.

The principal counterexample is the supplied corpus run for:

> Where did my current idea of semantic geometry actually come from, including precursors that I did not name that way at the time?

The compiler requested preferred `journal` and `personal_reflection` scope, semantic queries `idea_origin_semantic_geometry` and `unclaimed_foundations_semantic_geometry`, lexical queries `semantic geometry origin`, `precursor concepts`, and `unnamed idea foundations`, graph depth `1`, and packet maximum `24`. The executors reported 2,498 raw/scoped lexical matches and 50 returned candidates; 14,577 compatible vectors with threshold `0.5` and 8 returned candidates, with both semantic queries represented; and 24 graph candidates across 4 candidate notes from one matched seed and six expanded notes.

The final packet nevertheless contained 24 chunks from only 4 notes: 13 from `Gardenfors, Peter - The Geometry of Meaning`, 8 from `Semantic Geometry`, 2 near-duplicates from one 2025-12-31 journal entry, and 1 from `Semantic Traversal`. It retained lexical support on 19 chunks, vector support on 6, and graph support on 4. Only 2 preferred-scope journal chunks survived, and all selected graph-backed chunks came from the seed note. The approximately 47,895-byte retrieval packet and 93,029-byte synthesis context were bounded by chunk count and surrounding packet construction, not by explicit byte ceilings.

The synthesis answer treated Gärdenfors as the explicit geometric-semantic vocabulary source and constructed a causal lineage through Kant, cybernetics, cognitive linguistics, and Gärdenfors. It did not recover the previously demonstrated autobiographical chronology. This is a fusion/selection composition failure, not evidence that the vector executor failed: the vector surface executed both queries and returned candidates under its stated threshold and caps.

## Authority boundary

Runtime/YAML remains authoritative for operational limits, source admission, required evidence, preferred-scope semantics, budgets, redundancy limits, packet ceilings, and final selection. The compiler may propose operators, queries, scope intent, requiredness, and bounded limits only through supported plan fields. It cannot define cross-surface weights, override YAML budgets, authorize absence, or decide which evidence survives.

Executor raw scores remain executor-local evidence. Exact match counts/evidence, FTS5 BM25-derived ranks, cosine similarity, and graph constants are not directly comparable. The fusion stage may use per-surface rank positions and explicit runtime controls, while retaining every raw score and its surface.

Temporal ordering and chronology remain Seam 6A. Compiler preferred-scope emission remains Seam 8. Seam 5 may compose already-bound preferred-scope evidence but must not infer journal chronology or genealogy intent.

## Current algorithm audit

### Merge and provenance

`_merge_candidates` absorbs exact, lexical, vector, and graph lists into a dictionary keyed by `chunk_id`. The first representation initializes the merged record. Later representations add `source_layers`, merge exact evidence, graph provenance, graph hops, and vector query scores. Duplicate source entries are normalized to canonical order. A representation replaces the retained representation when its raw `score` is higher, or when scores tie and its configured `source_priority` is higher. That representation supplies `selection_source`, retained score, and the winning selection payload. `source_layers` records all independently contributing surfaces and is separate from `selection_source`.

### Current ranking tuple

Merged candidates are sorted by:

1. `_retrieval_demoted` (`False` before `True`);
2. preferred-scope boolean (`True` before `False`);
3. descending `source_priority[selection_source]`;
4. descending raw candidate `score`;
5. ascending `chunk_id`.

Current YAML values are `exact_bonus: 4.0`, `lexical_bonus: 2.0`, `vector_bonus: 1.0`, `graph_bonus: 1.5`, `demotion_penalty: 10.0`, and source priorities exact `4`, lexical `3`, vector `2`, graph `1`. These bonuses are mixed into executor scores before the cross-surface tuple, so the present ordering directly compares unlike score scales. The priority values are a deterministic tie/ordering influence, not a calibrated evidence model.

### Current budgets and selection

Runtime replaces compiler selection policy with YAML values: `max_chunks: 24`, `preserve_required_layers: true`, and budgets exact `12`, lexical `6`, vector `6`, graph `4`. The selector first reserves required exact terms and required non-exact layers, then performs a source pass up to each budget, counting membership in `source_layers`. A multi-surface chunk therefore contributes to every supporting source during those counts. After that pass, a final loop walks the already ranked merged list and fills any remaining packet slots without checking source budgets. Thus current budgets are neither hard maxima nor pure minima: they act as reservation targets during an intermediate pass and then become advisory. The characterization test `test_characterization_current_global_fill_can_exceed_source_budget` freezes this behavior.

Unused source budgets are not explicitly redistributed; unrestricted global fill consumes the remaining packet capacity. There is no overflow diagnostic. Selection accounting uses `source_layers`, not `selection_source`, so one multi-surface chunk can consume several source counters during the budget pass. Required evidence can be reserved within those counts, but a required conflict is diagnosed only after the bounded selection attempt.

### Candidate loss and concentration

Preferred scope is annotated after each executor has applied its own candidate limit. The lexical executor therefore cannot preserve a preferred candidate that falls below its ordinary return cap, even though raw/scoped counts retain the fact that it existed. The characterization test `test_characterization_current_lexical_limit_precedes_preferred_scope_annotation` freezes this boundary.

Vector execution already gives each semantic query an independent bounded opportunity and a per-note cap. Graph materialization is round-robin across traversed notes, but final selection does not impose a note cap. A seed note can therefore consume the final packet. The selector fills to `max_chunks` whenever enough candidates exist, even when the marginal evidence is repetitive. Exact content hashes prevent exact duplicates, but near-duplicate text can occupy multiple slots. No final semantic or deterministic lexical redundancy rule exists.

### Packet bounds

`max_chunks` is a hard selected-chunk ceiling. Retrieval-packet and synthesis-context byte sizes are not independent YAML-owned ceilings. Canonical paragraph text is not truncated. The synthesis context currently includes the compiler packet, traversal manifest, approved retrieval packet, coverage, and runtime fields; the durable manifest is detailed, so its size can grow with diagnostics. Current byte values are observations, not contracts.

### Potential dead or duplicate scoring authority

If rank-based fusion is approved, `exact_bonus`, `lexical_bonus`, `vector_bonus`, and `graph_bonus` must stop influencing cross-surface ordering; they could remain executor-local diagnostics only or be retired in a later compatibility change. `source_priority` would become a deterministic tie-breaker or be retired, not a primary evidence weight. `selection_policy.budgets` is currently ambiguous and must receive one meaning. `retrieval.max_chunks` and `planner_defaults.selection_policy.max_chunks` are already runtime-bound together; the implementation must preserve one effective ceiling rather than add another hidden default. Executor caps remain upstream admission controls and must not be silently treated as final fusion quotas.

## Invariants

- Hard scope remains an admissibility filter; preferred scope remains non-exclusionary.
- Raw executor scores, exact evidence, vector query scores, graph hop provenance, `selection_source`, and `source_layers` remain inspectable.
- One selected chunk may satisfy multiple evidence surfaces without losing provenance.
- Required exact terms and required layers are reserved first; inability to fit required evidence within hard bounds blocks with structured diagnostics rather than expanding bounds.
- No exact no-match result is created or strengthened by fusion; absence permission remains the exact/runtime contract.
- Final selection is deterministic under reversed query order, insertion order, and stable candidate identity.
- The packet maximum is a ceiling, not a requirement to fill repetitive slots.
- No temporal ordering, compiler prompt/schema change, corpus-specific rule, model-based pairwise reranker, or hidden numeric operational default is introduced.

## Alternatives considered

### A. Current source-priority ordering plus corrected caps

This is the smallest change: enforce budgets and add a final note cap. It would reduce lexical/book and seed-note concentration if caps are correctly defined, but it would still compare unlike raw scores, make preferred-scope preservation difficult after executor truncation, and provide weak multi-query coverage. It is useful as a fallback or migration safety mode, not sufficient as the primary design.

### B. Normalized weighted score fusion

Per-surface normalization could combine exact, BM25, cosine, and graph scores. It is unstable across query distributions, corpus sizes, FTS modes, vector thresholds, and graph topologies. Exact match is discrete evidence, BM25 is corpus/query dependent, cosine is bounded but model-dependent, and graph constants are policy values rather than relevance scores. Normalization would create calibration and drift obligations that this seam cannot prove. Rejected as the primary method.

### C. Weighted reciprocal-rank fusion

Per-surface rank positions avoid raw-scale comparison and naturally tolerate missing surfaces. A candidate supported by multiple surfaces receives multiple rank contributions; exact evidence can receive a configured rank weight without pretending its score equals cosine or BM25. Ties must use stable `chunk_id` order, and each query or graph-note stream needs an explicit rank stream. Preferred scope can add a bounded admission/reservoir signal or deterministic tie-break, not an unbounded bonus. This is the recommended core because it is explainable and stable under the current executor contracts.

### D. Deterministic source interleaving

Round-robin source selection guarantees coverage and is easy to audit, but it can elevate weak candidates and does not distinguish high-quality from low-quality candidates within a source. It handles unused quotas and duplicates only with additional rules. It is appropriate for bounded reservoir admission and required reservations, not as the complete ranking method.

### E. Greedy marginal utility or MMR-style selection

This could enforce note diversity and redundancy reduction, but it requires deterministic pairwise similarity, explicit tokenization/normalization thresholds, and more computation. It risks introducing a second relevance model and hidden thresholds. A narrow deterministic text-overlap filter may be added after rank fusion; full MMR is rejected for this seam.

### F. Bounded hybrid — recommended for approval

Use the smallest composition that addresses the observed failures:

1. preserve required exact-term and required-layer evidence;
2. admit a bounded preferred-scope reservoir before ordinary executor truncation can erase it;
3. build per-surface rank streams, including one stream per semantic query and bounded graph-note streams where available;
4. fuse with weighted reciprocal rank and deterministic tie-breaks;
5. apply hard final note/primary-source and exact-content controls;
6. apply an explicit normalized-text redundancy rule;
7. stop when the next candidate violates a hard bound or has no permitted marginal contribution;
8. serialize detailed rejection diagnostics in the durable manifest while preserving the existing selected packet fields.

This is recommended because each stage maps to a demonstrated failure and remains runtime/YAML-auditable. It is not approved for implementation until the open questions below are answered.

## Proposed contract for approval

### Budget semantics

`selection_policy.budgets` should mean hard primary-source allocation caps for ordinary candidates, not vague minima. Accounting uses `selection_source` so a multi-surface candidate is charged once to the representation that won selection; `source_layers` remains the coverage/provenance surface. Required reservations are a separate first stage: a candidate can satisfy several required layers, and each satisfied required layer is recorded, but the global `max_chunks` and exact-term bounds remain hard.

Unused source capacity is not redistributed by increasing another source's hard cap. It is available to the fused global pool only when the candidate's primary source remains within its cap; if no eligible candidate remains, selection stops early. Overflow is rejected with `source_budget_rejection` diagnostics. This avoids the current budget-pass-then-unrestricted-fill ambiguity and makes the packet sparse when evidence is sparse or repetitive.

The global maximum constrains reservations and ordinary allocations together. A required candidate that cannot fit because of global packet bounds, required source caps, or a hard byte ceiling blocks. No configured limit is silently expanded. Exact no-match evidence consumes no selected-chunk slot and remains a manifest fact rather than a candidate.

### Preferred-scope admission

Use a bounded two-stream admission contract per executor: ordinary candidates retain their configured return bound, while a separate preferred-scope reservoir admits up to an explicit `preferred_reservoir_per_source` candidates from the same hard-scoped result set. The reservoir is selected deterministically by the executor's local rank and chunk ID, and its count is reported separately. This is not a corpus-specific journal rule and does not alter raw/scoped counts. Fusion receives the ordinary pool plus the bounded reservoir, deduplicates by chunk ID, and makes the final decision. The reservoir is an explicit bounded addition, not a silent increase to all executor limits.

Preferred scope is a ranking/admission opportunity only. A preferred candidate may lose to stronger non-preferred evidence, but it cannot be absent solely because the ordinary executor cap filled first. Hard scope still filters before both streams.

### Diversity and coverage

The proposed minimum controls are:

| Control | Proposed default | Stage | Hardness and diagnostics |
| --- | ---: | --- | --- |
| `selection_policy.max_chunks` | existing `24` | final selection | hard global ceiling; `packet_maximum` |
| `selection_policy.max_chunks_per_note` | `3` | final selection | hard; `note_cap_rejection` |
| `selection_policy.max_chunks_per_primary_source` | existing source budgets | final selection | hard source caps; `source_budget_rejection` |
| `selection_policy.preferred_reservoir_per_source` | `4` | pre-fusion admission | hard reservoir; `preferred_reservoir_admitted`/`exhausted` |
| `selection_policy.max_normalized_text_jaccard` | `0.90` | post-rank candidate admission | hard redundancy rejection; `redundancy_rejection` |
| `selection_policy.min_marginal_contribution` | disabled initially | final selection | no hidden score threshold; introduce only with evidence |

No explicit graph-note minimum is recommended initially. Graph-note round-robin already exists upstream; fusion should expose graph-note coverage and use rank streams, with an optional bounded graph-note reservation considered only if corpus evidence shows repeated collapse after the hybrid is implemented. Semantic queries receive independent rank streams and therefore a bounded opportunity, not a mandatory quota.

### Redundancy

Keep exact `chunk_hash` deduplication as the first rule. Add deterministic normalized-text fingerprinting only if the runtime defines Unicode normalization, case-folding, whitespace collapse, and punctuation handling in YAML/documented code. For the proposed `0.90` token-set Jaccard threshold, compare only within the bounded candidate pool and use stable chunk order. Do not use embedding similarity or model-based pairwise reranking. False positives may suppress differently framed passages sharing formulaic language; false negatives may retain paraphrases. The manifest must record the compared representative and rejection reason. The threshold is proposed, not active.

### Packet bytes

Add explicit future YAML ceilings, proposed as retrieval packet `65,536` bytes and synthesis context `131,072` bytes, subject to representative-set calibration. Count UTF-8 serialized JSON deterministically using the existing serializer options. Never truncate canonical paragraph text or exact context. Candidates that would exceed a hard ceiling are skipped with `packet_byte_rejection`; required evidence that cannot fit blocks. The durable traversal manifest may be larger and should retain full diagnostics. The synthesis context should receive a bounded diagnostic projection rather than an unbounded duplicate of the full manifest, while preserving the existing approved packet fields and prompt text.

### Provenance and diagnostics

Each candidate's durable fusion record should expose per-surface rank, raw score, query identity, graph-note/path identity where present, fused contribution, selected/rejected stage, preferred-scope match, required reservation state, primary-source charge, note/source counts, duplicate merge, redundancy comparison, byte rejection, and any relaxation. Selected packet provenance remains additive and unchanged in this ADR phase: `selection_source`, canonical `source_layers`, exact evidence, vector query scores, and graph provenance remain distinct.

## Acceptance scenarios

The implementation must characterize and UAT at least:

1. required exact evidence plus optional surfaces;
2. broad lexical versus narrow vector evidence;
3. two distinct vector queries;
4. multi-note graph candidates with seed-note concentration;
5. preferred journal scope with relevant non-journal evidence;
6. preferred candidates below ordinary executor caps;
7. a large lexical book result;
8. duplicate and near-duplicate chunks;
9. one chunk supported by all four surfaces;
10. required evidence conflicting with note or byte caps;
11. sparse retrieval that should not fill repetitive slots;
12. reversed insertion/query order;
13. no exact layer and no negative-claim authorization;
14. the supplied semantic-geometry genealogy counterexample.

The representative evaluation set must also include a literal exact query, broad conceptual query, narrow factual vault query, graph relation query, preferred-scope query, multi-query semantic case, and unrelated control. Evaluation records packet composition, selected/rejected reasons, note/source/query coverage, and determinism; prose quality alone is not an acceptance criterion.

## Required tests and UAT matrix

Characterization tests added in this ADR slice are explicitly current-behavior tests:

- global fill can exceed a configured source budget;
- final selection can fill a packet with one note;
- unlike executor raw scores participate in one ordering;
- lexical limit is applied before preferred-scope annotation can preserve a lower-ranked candidate.

Before implementation, add tests for rank-stream construction, multi-surface rank fusion, required reservations, preferred reservoirs, hard note/source/byte caps, normalized-text rejection, sparse early stop, multi-query coverage, graph-note coverage, provenance/diagnostic completeness, insertion-order determinism, and all acceptance scenarios above. Keep current required exact/layer tests green.

Corpus UAT must repeat the supplied genealogy query and record candidate and selected composition, preferred-scope admission, query coverage, graph-note coverage, near-duplicate rejections, packet bytes, deterministic repeated runs, and whether chronology is still unavailable. It must also run the other representative queries; no single genealogy result closes Seam 5.

## Migration, compatibility, and rollback boundary

This ADR phase changes only records and characterization tests. The future implementation must preserve existing selected packet keys and source provenance, add diagnostics primarily to the durable manifest, and keep old executor-local raw-score fields for audit. A feature flag or copied YAML configuration should permit comparison of current and proposed selection on identical candidate manifests. Rollback means restoring the prior selector and YAML policy without rewriting stored chunks or vectors; no ingest/index migration is required.

## Rejected or deferred scope

- raw-score weighted fusion is deferred because score calibration is unproven;
- full MMR/model-based redundancy is deferred;
- mandatory quotas for every executor/query/graph note are rejected as too rigid;
- temporal chronology is deferred to Seam 6A;
- compiler prompt/schema changes and preferred-scope emission are deferred to Seam 8;
- final synthesis claims remain outside executor fusion proof;
- packet prompt text is frozen.

## Open questions requiring approval

1. Approve rank-based fusion as the core rather than corrected source-priority ordering.
2. Approve budgets as hard primary-source caps with separate required-evidence reservations.
3. Approve the bounded preferred reservoir and its per-source default of `4`.
4. Approve final note cap `3` and normalized token Jaccard `0.90`, or request a calibration-only phase first.
5. Approve proposed byte ceilings `65,536` and `131,072`, or require corpus calibration before binding values.
6. Approve whether source-priority and executor bonuses become diagnostics/tie-breakers only or are retired in a compatibility seam.
7. Approve a comparison/rollback flag for the first implementation run.

## Decision and implementation gate

Decision: proposed bounded hybrid as described above, with weighted reciprocal-rank fusion at its core. This is a recommendation, not an implementation authorization. Production ranking, selection, YAML controls, packet construction, and prompts must remain unchanged until the user explicitly approves the open questions or supplies a revised contract.

## Approved bounded-decision addendum (2026-07-19)

The user approved a narrower experiment on branch `codex/seam5-ordinal-note-breadth`, based on `b5883fb`, without approving the full hybrid above. The approved experiment is:

- unweighted ordinal reciprocal-rank fusion with no offset constant and no source weights;
- one stream each for exact, lexical, vector, and graph;
- `selection_source` chosen by best surface rank, then canonical surface order;
- required exact and required-layer reservations preserved first;
- ordinary `selection_policy.budgets` allocation retired explicitly;
- evidence-source breadth-before-depth grouped by `note_id`;
- existing exact chunk/content-hash deduplication and global `max_chunks` ceiling;
- durable fusion/rejection diagnostics sufficient to audit the experiment.

The following remain deferred and unapproved: preferred-scope reservoirs, fuzzy or Jaccard redundancy, packet-byte ceilings, hard note caps, hard retrieval-layer quotas, weighted RRF, query or graph quotas, marginal-contribution thresholds, temporal ordering, compiler/schema work, and the remainder of the proposed hybrid. Preferred scope remains only a tie-break in this experiment and may still be lost before fusion when an executor limit truncates it.

Success requires truthful same-candidate-pool replay of the recorded genealogy failure, more than four selected notes, lower than thirteen maximum note concentration, no reduction in preferred selected evidence, retained vector/graph provenance, deterministic output, required-evidence preservation, representative-query regressions remaining green, and unchanged prompts. Failure must be reported as the narrowest demonstrated gap; it must not trigger automatic implementation of the full hybrid.

Rollback boundary: revert the experiment branch to `b5883fb`; no ingest or index migration is required. The original full-hybrid decision above is superseded historical analysis and unapproved.

The experiment passed its bounded gate on the reconstructed live candidate pool. The baseline pool was exact 0, lexical 50, vector 8, graph 24, merged 77; the baseline selector produced 24 selected chunks from 4 notes with maximum concentration 13. The ordinal selector used the same 77 merged candidates and produced 24 selected chunks from 11 notes with maximum concentration 4. Repeated runs produced identical selected IDs and fusion diagnostics. Vector provenance retained both semantic-query streams, and graph support represented four notes rather than collapsing to the seed note. Preferred selected evidence did not decrease in the same-pool replay, but preferred-scope admission before executor truncation remains unresolved and is intentionally not claimed as fixed. The original near-duplicate journal chunks also remain because redundancy suppression was deferred.

The implementation changed runtime selector behavior and the explicit runtime/YAML budget-binding seam; therefore the earlier migration statement that this phase changes only records and characterization tests applies only to the preceding design-only phase, not to this approved experiment branch. This superseded ADR authorizes nothing; the accepted bounded experiment is defined by `implementation-07-seam-5-accepted-ADR.md`.
