# Implementation 07 Tracker

## Status

- State: active
- Current work: Seam 6A temporal retrieval contract (reopened/proposed).
- Next action: define typed temporal anchors and retrieval operations before production implementation; do not advance to Seam 7.

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
| 2026-07-19 | Coordinator | Opened Seam 5 architecture-decision phase | Audited current merge/rank/selection behavior; recorded supplied 24-chunk/4-note genealogy counterexample; added four current-behavior characterization tests; proposed bounded-hybrid ADR with rank-based fusion, required reservations, preferred reservoir, hard note/source/byte bounds, and deterministic redundancy controls; pre-change baseline 133, post-characterization suite 137 pass | Await explicit approval of `implementation-07-seam-5-fusion-ADR.md`; do not modify production ranking |
| 2026-07-19 | Coordinator | Accepted and closed the bounded Seam 5 ordinal-fusion experiment on the target branch | Fast-forwarded `codex/big-refactor-07.18.26` from `b5883fb` to `3dd1579`; same-pool replay reduced selected-note concentration from 4 notes/max 13 chunks to 11 notes/max 4 chunks; full suite 142 pass; prompt guards and diff checks pass | Use the compact accepted ADR; historical full hybrid analysis is superseded and unapproved |
| 2026-07-19 | Coordinator | Recovered the incomplete Seam 6A continuation | Reverted pushed commit `0b039ad` with normal revert `d6c770a`; it was temporal annotation/order, not temporal retrieval; prototype remains historical in Git only | Reopen Seam 6A as proposed; define typed anchors and retrieval operations; do not start Seam 7 |

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
| Seam 5: fusion/selection | Coordinator | complete for bounded experiment | Accepted ordinal surface fusion, required reservations, explicit budget retirement, and breadth-before-depth selection; merged to target as `3dd1579`; same-pool replay, representative probes, full suite 142, compileall, and diff checks pass | The broader weighted-RRF hybrid is superseded historical analysis and unapproved; preferred reservoir, hard note/source/byte bounds, redundancy suppression, query/graph quotas, and marginal thresholds are not routine planned Seam 5 work |
| Seam 6A: temporal substrate | Coordinator | reopened/proposed | `0b039ad` reverted; no temporal retrieval contract accepted | Define typed temporal anchors and retrieval operations; production implementation is not started |
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

- Seam 5 is complete for the accepted bounded ordinal-fusion contract in `implementation-07-seam-5-accepted-ADR.md`. The original full weighted-RRF/hybrid ADR is superseded, historical, and unapproved; it is not routine planned Seam 5 work. Seam 6A is reopened/proposed after reverting `0b039ad`, and Seam 7 has not begun.
- Seam 4B production changes were coordinator-executed. Deferred delegated-agent tools were available but were not discovered before implementation; no delegation was used and none is claimed.

Seam 5 architecture decision (2026-07-19): `agent_harness/implementation-projects/active/implementation-07-seam-5-accepted-ADR.md` is the accepted compact contract: unweighted ordinal per-surface fusion, required-evidence reservations, retirement of ordinary layer budgets, breadth-before-depth by `note_id`, global configured `max_chunks` ceiling, and deterministic diagnostics. `implementation-07-seam-5-fusion-ADR.md` is superseded historical analysis and unapproved; preferred reservoirs, sparse stopping, hard note/source/byte bounds, redundancy suppression, query/graph quotas, and weighted RRF are not routine planned Seam 5 work.

Seam 5 normalization and closeout (2026-07-19): the accepted experiment branch was verified at `3dd1579de29a5b960489a3b4f1bb6eba05f40f73`, descended directly from `b5883fbce243d1cc61c28fe06a19f4d5eda7181f`, and fast-forwarded into `codex/big-refactor-07.18.26` with no conflict. Seam 5 is closed for that bounded experiment only. The full-hybrid recommendation in the original ADR is superseded historical analysis and unapproved; no preferred reservoir, hard note/source/byte caps, fuzzy redundancy rule, byte ceiling, weighted RRF, query/graph quota, temporal ordering, compiler/schema change, or final synthesis claim is closed by this normalization.

Final design-slice verification: pre-change full suite `133` passed; characterization-focused suite `4` passed; post-change full suite `137` passed; `compileall` and `git diff --check` passed. Prompt and YAML hashes remained unchanged.

## Seam 5 bounded ordinal-fusion experiment (2026-07-19)

The experiment was created from `b5883fbce243d1cc61c28fe06a19f4d5eda7181f` on branch `codex/seam5-ordinal-note-breadth`; the baseline branch was not modified. The recorded genealogy artifacts were available at `C:\Users\madis\Desktop\kháos\.semantic-traversal\threads\thread-872f7213b0c1\turns\turn-000001`. Their hashes were compiler packet `ee789c9a5b6e5a42d7b8279ada0c7dabe6e7d630ec36cc21eded0e654364df03`, retrieval packet `7eb7292fccd60c2b7d44f205587144a0602d06910263deff4671ffecfd2d0856`, traversal manifest `b0a5b8b7e0d8ac9ed21557551790659928f3637dcd6624a567d27a92ad51c782`, and synthesis context `ade4f5dff22f37428f02f48abe9631bde91f94cf88f0d850b5851110f4bf56f`. The candidate pool was not persisted by the original run; temporary instrumentation reconstructed it from the same compiler packet, configured database, and executor backends. Capture hash: `797a5d8bfd507c0e15ca03dc3736061c1773da6552d57f9593f3bda437f387b`.

The original selection reproduced 24 chunks from 4 notes, with maximum note concentration 13; title counts were 13 Gärdenfors, 8 Semantic Geometry, 2 December 31 2025, and 1 Semantic Traversal. Support counts were lexical 19, vector 6, graph 4; only 2 preferred-scope chunks survived. Same-pool experimental selection retained 24 chunks from 11 notes, maximum concentration 4, with support counts lexical 13, vector 3, graph 12. Both semantic-query streams remained represented and graph support came from four distinct notes rather than only the seed note. Repeated experimental replay produced identical selected IDs, candidate pool, and fusion diagnostics; selected-ID hash `d8248dc0a14c20aabc8a51353656e4597763165c7ce02fbc9dfdc8428fcd1ef`, fusion hash `09722de61abd7a797d228e524ce891fbcdc656c237e77ccf9c18b1d2e472e500`.

The production change is intentionally narrower than the proposed ADR: fusion uses one-based per-surface ranks and `sum(1/rank)` with deterministic preferred-scope, best-rank, rank-sum, canonical-surface, and chunk-ID tie-breaks; raw cross-surface scores and source-priority values are retained for audit but no longer decide cross-surface fusion. Required exact/layer reservations remain first. Ordinary layer budgets are retired explicitly by runtime resolver diagnostics rather than silently treated as caps. Remaining candidates are selected breadth-before-depth by deterministic note order, without a new hard note cap. Existing provenance, exact/content-hash deduplication, and global `max_chunks` remain intact.

Representative probes covered genealogy, exact literal, broad conceptual, narrow factual, graph relation, preferred-scope, multi-query, and unrelated control cases. All completed deterministically; packet sizes ranged from 31,404 to 66,856 bytes in direct executor probes. The preferred-scope probe used an empty preferred request in its controlled plan, so it is not evidence for preferred-scope admission; the existing preferred-scope truncation limitation remains open. The original persisted genealogy packet was approximately 47,895 bytes and synthesis context approximately 93,029 bytes; no byte ceiling was introduced. Near-duplicate journal chunks remain because fuzzy redundancy suppression is deferred.

Verification for this branch: focused retrieval/config/fusion tests `142` passed; the complete local Python suite, compileall, and diff check are recorded with final hashes below. Prompt hashes remained compiler `9ca92643b2550410743d494fc82e3ae67c718c8f7867d72e43ddb49925e28f4b` and frontier `fdac281e48e765af09578be02e53ad65a443d49914fac53d017ade5391a18776`. The YAML hash changed only because the retired compiler-budget compatibility surface was made explicit; no prompt text changed.

Final local hash guard: rendered compiler fixture `21e89d824fa2bf13515c49170d375cee1abb8c9d71f80f5c61215e63073b34f4`, frontier instructions `fdac281e48e765af09578be02e53ad65a443d49914fac53d017ade5391a18776`, and YAML `070f4a528d845e2a8d31a7c174062a7444caca66b2325e682079f5f8432c8bd6`. The previously recorded genealogy compiler prompt hash `9ca92643b2550410743d494fc82e3ae67c718c8f7867d72e43ddb49925e28f4b` remains unchanged in the source UAT artifact.

Operator-supplied visual GitHub Actions evidence: run title `complete seam 4c vector identity and diversity`, tests/run identifier `tests #94`, commit `ac4516f`, branch `codex/big-refactor-07.18.26`, passed, approximately 1m49s. No independently available run/job ID is claimed.

Seam 4C record (2026-07-19): vector rows now carry canonical provider/model/dimension/normalization/encoding identity and an identity hash; changed or incompatible rows are re-embedded, and malformed or mixed rows are diagnosed. YAML owns `min_similarity: 0.5`, `per_query_max_candidates: 50`, `max_chunks_per_note: 4`, and `max_candidates: 200`. Each accepted query executes independently, with canonical query ordering, per-query score provenance, and deterministic round-robin allocation. Corpus calibration at `.2/.35/.5/.65` was recorded before selecting `.5` as a bounded tradeoff; this is not corpus-specific ranking logic.

The disposable configured corpus contained 1,078 notes and 14,577 chunks. Ingest produced 14,577 compatible vectors: sentence-transformers `all-MiniLM-L6-v2`, dimension 384, normalized, encoding `chunk_embedding_text_v1`; all missing, orphan, malformed, dimension, identity, zero-norm, and mixed counts were zero. Final threshold-0.5 probes returned `semantic geometry` 8 candidates/2 notes, `autobiographical chronology journal development` 14/11, `conceptual precursor influence` 4/1, an unrelated control 0/0, and a two-query combination 22/13 with both queries represented; max chunks per note was 4. Reversed query order produced identical diagnostics and candidate ordering. Focused vector/ingest/config tests passed (33); full local suite passed (133); compileall and diff check passed.

Operator-supplied visual evidence for the preceding gate: GitHub Actions run title `complete seam 4b fts freshness and atomic ingest`, `tests #93`, commit `e4087f1`, branch `codex/big-refactor-07.18.26`, passed in approximately 1m46s. No run/job ID was available here, so this is recorded as reported operator evidence, not an independently inspected GitHub check.

Final hash evidence: semantic compiler `9ca92643b2550410743d494fc82e3ae67c718c8f7867d72e43ddb49925e28f4b`; frontier synthesis `fdac281e48e765af09578be02e53ad65a443d49914fac53d017ade5391a18776`; YAML `2178698b9086aed79747618ca792eddc2e885732d8a952750850e45a7f029743` (before Seam 4C `b0b2aa1f0ef45d1cadc52156dd79ab3e9e7379d3e626f541bbb5768065abfba4`).

## Recovery and governance correction (2026-07-19)

Commit `0b039ad0d0bfe7adc974ad1ed1fee81c853697d8` was reverted normally as
`d6c770a`. It implemented temporal annotation and sorting of already selected
evidence, not typed temporal anchors, temporal retrieval operations, recovery
outside the ordinary fusion pool, or a chronology-capable UAT. The commit is
preserved as historical prototype evidence; no temporal production code from
it remains. Seam 6A is reopened/proposed and Seam 7 has not begun.

The accepted Seam 5 ADR is
`implementation-07-seam-5-accepted-ADR.md`. The original full weighted-RRF /
hybrid ADR is retained at `implementation-07-seam-5-fusion-ADR.md`, marked
superseded and unapproved, and is historical analysis rather than routine
planned Seam 5 work.

The source-priority audit found no remaining runtime consumer for
`retrieval_scoring.source_priority`; ordinal fusion uses per-surface ranks and
canonical tie-breaks. The YAML/config authority was removed. Executor-local
exact, lexical, vector, graph, and demotion bonuses remain active for their
local candidate ordering and diagnostics.

Deferred live-UAT findings: preferred evidence can still be removed by
executor-local truncation before fusion sees it; no reservoir is introduced,
and this is reassessed after genuine temporal retrieval. The selector can fill
the configured `selection_policy.max_chunks` when enough admissible candidates
remain; no sparse stopping is introduced, and the configured value is not
inherently 24. Reassess only if later UAT demonstrates noise, reasoning
degradation, or material context cost.

Operator live-UAT aggregates: the genealogy run produced the strongest
conceptual-development reconstruction observed so far, but did not establish
historical chronology or causal influence; this supports fusion functioning
while showing temporal retrieval is a distinct epistemic requirement. The
direct sister/family-relation run preserved sister/full/half-sister distinctions
and did not infer beyond indexed evidence; its broad lexical max-fill is
recorded as an observation, not a demonstrated reasoning failure. No private
family-note text is committed.

Recovery verification: focused fusion/config and exact/runtime regressions
passed (`36` tests); complete local Python suite passed (`142` tests),
compileall passed, and `git diff --check` passed. Prompt hashes are unchanged:
compiler `21e89d824fa2bf13515c49170d375cee1abb8c9d71f80f5c61215e63073b34f4`
and frontier `fdac281e48e765af09578be02e53ad65a443d49914fac53d017ade5391a18776`.
The YAML hash was `070f4a528d845e2a8d31a7c174062a7444caca66b2325e682079f5f8432c8bd6`
after the temporal revert and `09d7d5ea139d66b98ce56e17a469b70fbdbbbd3c4bd4258ea5f9217eb6c15052`
after retiring `source_priority`. No temporal production surface remains.
