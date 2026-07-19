# Implementation 07 Tracker

## Status

- State: active
- Current work: Seam 3 runtime authority and non-exact required-layer semantics.
- Next action: implement and verify structured execution status, runtime-owned policy binding, and adequate selected contribution for required lexical, vector, and graph layers.

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

## Work Status

| Work | Owner Agent | Status | Verification | Notes |
| --- | --- | --- | --- | --- |
| Seam 0: baseline and drift guards | Coordinator | reviewing | compileall, 80-test baseline, hashes, diff check | Guard scripts and golden artifacts still need to be formalized |
| Seam 1A: ingest/index integrity | Coordinator | proposed | pending | Depends on Seam 0 |
| Seam 1B: plugin/tooling hygiene | Coordinator | proposed | pending | Independent, only if reproducible |
| Seam 2A: scope model | Coordinator | reviewing | Hard/preferred fixture tests, deterministic repeated run, disposable corpus controlled-scope UAT; full suite 96 pass | Runtime contract is implemented; compiler emission of a journal preference remains open |
| Seam 2B: exact-search contract | Coordinator | complete | 14 focused retrieval-contract tests; full suite 104 pass; disposable corpus UAT | Required literal terms, count units, `return_total_count`, bounded context, exact evidence preservation, and conservative absence permission are implemented |
| Seam 3: runtime authority/requiredness | Coordinator | complete | 18 focused retrieval-contract tests; required lexical/vector/graph integration fixtures; full suite 112 pass; compileall, diff check, and controlled disposable-corpus UAT pass | Structured non-exact statuses, YAML-owned policy binding, unsupported-operator blocking, and selected source contribution are implemented; broad non-exact requiredness remains outside this seam |
| Seam 4A: graph executor | Coordinator | reviewing | Depth, fairness, provenance fixtures; corpus UAT graph pool 24 with multiple traversed notes; full suite 96 pass | Inbound/both corpus UAT pending |
| Seam 4B: lexical FTS5 executor | Coordinator | reviewing | Five FTS5 mode test; ingest/runtime lexical tests; full suite 86 pass | Full-corpus freshness and packet-size UAT pending |
| Seam 4C: vector executor | Coordinator | reviewing | Multi-query provenance test; full suite 86 pass | Threshold/diversity caps and identity checks still open; UAT required |
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

- Seam 3 is complete for this slice. The next planned seam is Seam 4A: graph executor corpus and direction coverage. Do not archive implementation-07 yet; compiler/schema emission of preferred scope, inbound/both graph corpus UAT, vector thresholds/diversity, general fusion/selection, temporal substrate, persisted inventory, final synthesis UAT, and plugin TypeScript readiness remain open.
