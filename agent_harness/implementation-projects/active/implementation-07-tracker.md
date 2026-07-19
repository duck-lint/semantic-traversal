# Implementation 07 Tracker

## Status

- State: active
- Current work: Seam 4C vector identity, thresholds, and diversity.
- Next action: verify index/query embedding compatibility, add YAML-owned similarity and diversity controls, and demonstrate independent multi-query contribution at corpus scale.

## Completed Repair Status

- Multi-surface provenance retention: complete; fixture-tested and corpus-demonstrated.
- Runtime-owned graph-depth binding and diagnostics: complete; fixture-tested and corpus-demonstrated.
- Fair round-robin graph candidate materialization: complete; fixture-tested and corpus-demonstrated after the new no-hard-scope corpus run.
- Selected `selection_source` serialization: complete; focused packet regression added separately.

## Work Log

| Date | Agent Role | Change | Evidence | Next |
| --- | --- | --- | --- | --- |
| 2026-07-18 | Coordinator | Created implementation-07 governance records for the attached retrieval completion program | Existing archived bundles end at implementation-06; current checkout inspected | Capture hashes and baseline test/build evidence |
| 2026-07-18 | Coordinator | Captured baseline hashes and verification | Python compileall passed; baseline Python suite passed with 80 tests; plugin build passed; `npx tsc --noEmit` failed on missing `electron` declaration (`TS2307`) | Preserve prompt/YAML hashes and inspect first runtime gaps |
| 2026-07-18 | Coordinator | Added exact status enum behaviour and multi-query vector execution/provenance | Focused retrieval contract tests pass; full suite passes with 83 tests; prompts and YAML unchanged | Continue with a separate scope/requiredness seam |
| 2026-07-18 | Coordinator | Added parsed scope algebra, required exact blocking, YAML-owned FTS5 modes/index, and configurable graph direction/provenance | Full suite passes with 86 tests; compileall and diff check pass; prompts unchanged; YAML hash changed for lexical mode and graph direction | Request corpus-level UAT |
| 2026-07-18 | Coordinator | Repaired selected multi-surface provenance, runtime-owned compiler graph depth, and fair graph candidate materialization; removed duplicate `graph_traversal.hop_limit` YAML authority | Changed `semantic_traversal/runtime.py`, `semantic_traversal/retrieval_resolver.py`, `semantic_traversal/config.py`, `semantic_traversal.runtime.yaml`, `tests/test_ingest_runtime.py`, and `tests/test_runtime_config.py`; 93-test suite, compileall, and diff check pass; prompt hashes unchanged | Keep Seam 5 fusion/selection open; corpus UAT not run against active index |
| 2026-07-18 | Coordinator | Added explicit hard-versus-preferred scope binding and deterministic preferred-scope ordering; added selected-packet `selection_source` serialization | Runtime/YAML scope policy distinguishes exact hard scope from semantic preferred scope; 96-test suite and disposable corpus UAT pass; prompt text unchanged | Actual compiler genealogy run emitted concept-like scope requests but no journal preference; keep compiler/schema seam open |
| 2026-07-18 | Coordinator | Completed Seam 2B exact contract and retained `selection_source` in selected packet serialization | Required literal-term coverage, independent total counts, bounded YAML context evidence, Unicode-safe per-term diagnostics, exact evidence preservation, and runtime-owned absence permission; 104-test suite, compileall, diff check, and disposable corpus UAT pass; prompt/YAML hashes preserved | Advance to Seam 3 only; keep compiler preferred-scope emission and general fusion open |
| 2026-07-18 | Coordinator | Completed Seam 3 runtime authority and non-exact required-layer contract | Preserved unknown operators and invalid limits for diagnosis; bound selection/claim policy, budgets, and limits from YAML; added lexical/vector/graph execution manifests; added required-layer contribution checks and deterministic source reservation; 112-test suite, compileall, diff check, prompt/YAML hash retention, and controlled disposable-corpus UAT pass | Advance to Seam 4A only; keep compiler/schema emission, vector thresholds/diversity, fusion, temporal, inventory, and final UAT open |
| 2026-07-18 | Coordinator | Completed Seam 4A graph executor direction and representative-corpus gate | Controlled complete `Journal -> Concept -> Reading` topology demonstrated outbound, inbound, and `both`; added canonical adjacency ordering, bounded structured hop provenance, submitted/matched/selected/unique note diagnostics, and packet propagation; 118-test suite, graph/config focused tests, compileall, diff check, and repeated representative UAT pass; prompt/YAML hashes preserved | Advance to Seam 4B only; keep FTS freshness, vector thresholds/diversity, fusion, temporal, inventory, compiler/schema, final synthesis, and plugin readiness open |
| 2026-07-19 | Coordinator | Completed Seam 4B FTS5 freshness, failure atomicity, and packet-scale evidence | Added candidate-database activation, lexical projection validation, preserved latest-success artifacts, staged failure diagnostics, and explicit FTS index/raw/scoped/returned counts; lifecycle, corruption, failure-stage, lexical-mode, and deterministic-order tests pass; full configured-vault disposable ingest produced 1,078 notes and 14,576 chunks with a valid 14,576-row FTS projection; operator manually verified CI for commit `7cad93683773c20e5bc0ff299c4842e95fa78ada` (no registered check artifact was used) | Advance to Seam 4C vector identity/threshold/diversity; keep fusion, temporal, inventory, compiler/schema, final synthesis, and plugin readiness open |
| 2026-07-19 | Coordinator | Completed Seam 4C vector identity, thresholds, and diversity | Added canonical embedding identity persistence/validation and changed-content invalidation; YAML-owned minimum similarity `0.5`, per-query cap `50`, per-note cap `4`, and global vector cap `200`; independent multi-query execution, deterministic query ordering, round-robin allocation, per-query score provenance, malformed-row diagnostics, and runtime compatibility diagnostics; focused vector/ingest/config tests pass; full suite 133 pass; complete configured-vault disposable ingest produced 1,078 notes, 14,577 chunks, and 14,577 compatible vectors; repeated corpus probes were deterministic | Advance to Seam 5 fusion/selection; keep temporal, inventory, compiler/schema, final synthesis, and plugin readiness open |

## Work Status

| Work | Owner Agent | Status | Verification | Notes |
| --- | --- | --- | --- | --- |
| Seam 0: baseline and drift guards | Coordinator | reviewing | compileall, 80-test baseline, hashes, diff check | Guard scripts and golden artifacts still need to be formalized |
| Seam 1A: ingest/index integrity | Coordinator | proposed | pending | Depends on Seam 0 |
| Seam 1B: plugin/tooling hygiene | Coordinator | proposed | pending | Independent, only if reproducible |
| Seam 2A: scope model | Coordinator | reviewing | Hard/preferred fixture tests, deterministic repeated run, disposable corpus controlled-scope UAT; full suite 96 pass | Runtime contract is implemented; compiler emission of a journal preference remains open |
| Seam 2B: exact-search contract | Coordinator | complete | 14 focused retrieval-contract tests; full suite 104 pass; disposable corpus UAT | Required literal terms, count units, `return_total_count`, bounded context, exact evidence preservation, and conservative absence permission are implemented |
| Seam 3: runtime authority/requiredness | Coordinator | complete | 18 focused retrieval-contract tests; required lexical/vector/graph integration fixtures; full suite 112 pass; compileall, diff check, and controlled disposable-corpus UAT pass | Structured non-exact statuses, YAML-owned policy binding, unsupported-operator blocking, and selected source contribution are implemented; broad non-exact requiredness remains outside this seam |
| Seam 4A: graph executor | Coordinator | complete | Directed complete-representative topology; outbound/inbound/`both`, depth 2, cycle/self-link, reciprocal, insertion-order, scope, edge/node controls, fairness, required contribution, and provenance tests; full suite 118 pass; compileall/diff check pass | Graph direction and bounded path provenance are verified. A full configured-corpus direction run remains optional evidence, not a blocker for the complete representative gate. |
| Seam 4B: lexical FTS5 executor | Coordinator | complete | 5 FTS5 mode coverage; freshness/update/rename/delete/unchanged lifecycle tests; corruption validator; initial/refresh/schema/validation/activation failure preservation tests; full suite 126 pass; compileall and diff check pass; disposable full configured-vault ingest and deterministic packet-scale lexical UAT | Candidate activation is atomic at the database-file boundary; FTS projection alignment and bounded lexical attrition are manifest-visible. |
| Seam 4C: vector executor | Coordinator | complete | Identity reuse/invalidation, malformed-index diagnostics, threshold/per-query/per-note caps, deterministic multi-query round-robin, score provenance, complete configured-corpus ingest, repeated corpus probes; full suite 133 pass; compileall and diff check pass | Canonical vector identity and bounded diversity are manifest-visible. General fusion/selection remains open. |
| Seam 5: fusion/selection | Coordinator | proposed | pending | Depends on 4A/4B/4C |
| Seam 6A: temporal substrate | Coordinator | proposed | pending | Depends on 1A |
| Seam 7: persisted inventory | Coordinator | proposed | pending | Depends on retrieval executors and 6A |
| Seam 8: compiler/schema contract | Coordinator | proposed | pending | Depends on runtime support |
| Seam 9: cleanup/docs/UAT | Coordinator | proposed | pending | Depends on all behavioural seams |

## Blockers

| Blocker | Boundary | Owner Agent | Resolution |
| --- | --- | --- | --- |
| Deferred delegated-agent inventory was not inspected before implementation | execution model | Coordinator | Delegated-agent tools were available in deferred inventory but were not discovered before implementation; the completed work was therefore coordinator-executed. |
| Final corpus-wide UAT remains required | evidence boundary | Operator/user | Exact UAT is recorded; later inbound/both, vector, temporal, and final synthesis evidence remain open | Do not archive until the remaining bundle gates are recorded |
| Compiler did not emit journal preference for genealogy query | compiler/schema boundary | Coordinator | Actual compiler emitted `idea_origin` and `precursor_concepts`; runtime did not infer journal scope; controlled supported-scope UAT separately demonstrated the runtime contract. | Keep Seam 8 open; do not hardcode query-specific scope inference |

## Closeout Note

- Seam 4C is complete for this slice. The next planned seam is Seam 5: fusion/selection. Do not archive implementation-07 yet; temporal substrate, persisted inventory, compiler/schema emission of preferred scope, final synthesis UAT, and plugin TypeScript readiness remain open.
- Seam 4B production changes were coordinator-executed. Deferred delegated-agent tools were available but were not discovered before implementation; no delegation was used and none is claimed.

Seam 4C record (2026-07-19): vector rows now carry canonical provider/model/dimension/normalization/encoding identity and an identity hash; changed or incompatible rows are re-embedded, and malformed or mixed rows are diagnosed. YAML owns `min_similarity: 0.5`, `per_query_max_candidates: 50`, `max_chunks_per_note: 4`, and `max_candidates: 200`. Each accepted query executes independently, with canonical query ordering, per-query score provenance, and deterministic round-robin allocation. Corpus calibration at `.2/.35/.5/.65` was recorded before selecting `.5` as a bounded tradeoff; this is not corpus-specific ranking logic.

The disposable configured corpus contained 1,078 notes and 14,577 chunks. Ingest produced 14,577 compatible vectors: sentence-transformers `all-MiniLM-L6-v2`, dimension 384, normalized, encoding `chunk_embedding_text_v1`; all missing, orphan, malformed, dimension, identity, zero-norm, and mixed counts were zero. Final threshold-0.5 probes returned `semantic geometry` 8 candidates/2 notes, `autobiographical chronology journal development` 14/11, `conceptual precursor influence` 4/1, an unrelated control 0/0, and a two-query combination 22/13 with both queries represented; max chunks per note was 4. Reversed query order produced identical diagnostics and candidate ordering. Focused vector/ingest/config tests passed (33); full local suite passed (133); compileall and diff check passed.

Operator-supplied visual evidence for the preceding gate: GitHub Actions run title `complete seam 4b fts freshness and atomic ingest`, `tests #93`, commit `e4087f1`, branch `codex/big-refactor-07.18.26`, passed in approximately 1m46s. No run/job ID was available here, so this is recorded as reported operator evidence, not an independently inspected GitHub check.

Final hash evidence: semantic compiler `9ca92643b2550410743d494fc82e3ae67c718c8f7867d72e43ddb49925e28f4b`; frontier synthesis `fdac281e48e765af09578be02e53ad65a443d49914fac53d017ade5391a18776`; YAML `2178698b9086aed79747618ca792eddc2e885732d8a952750850e45a7f029743` (before Seam 4C `b0b2aa1f0ef45d1cadc52156dd79ab3e9e7379d3e626f541bbb5768065abfba4`).
