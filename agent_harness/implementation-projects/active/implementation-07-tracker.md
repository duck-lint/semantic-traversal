# Implementation 07 Tracker

## Status

- State: active
- Current work: Seam 2A hard/preferred scope contract and corpus UAT evidence
- Next action: operator UAT against a complete disposable corpus or explicitly bounded representative corpus

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

## Work Status

| Work | Owner Agent | Status | Verification | Notes |
| --- | --- | --- | --- | --- |
| Seam 0: baseline and drift guards | Coordinator | reviewing | compileall, 80-test baseline, hashes, diff check | Guard scripts and golden artifacts still need to be formalized |
| Seam 1A: ingest/index integrity | Coordinator | proposed | pending | Depends on Seam 0 |
| Seam 1B: plugin/tooling hygiene | Coordinator | proposed | pending | Independent, only if reproducible |
| Seam 2A: scope model | Coordinator | reviewing | Hard/preferred fixture tests, deterministic repeated run, disposable corpus controlled-scope UAT; full suite 96 pass | Runtime contract is implemented; compiler emission of a journal preference remains open |
| Seam 2B: exact-search contract | Coordinator | active | 3 focused tests; full suite 83 pass | Status and match-mode slice landed; count/context/requiredness still open |
| Seam 3: runtime authority/requiredness | Coordinator | reviewing | Required exact failure blocks; full suite 86 pass | Non-exact required-layer diagnostics need UAT |
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
| Corpus-level UAT is required | evidence boundary | Operator/user | Run disposable complete-corpus checks in implementation-07-UAT-request.md | Do not archive until artifacts or bounded follow-up seams are recorded |
| Compiler did not emit journal preference for genealogy query | compiler/schema boundary | Coordinator | Actual compiler emitted `idea_origin` and `precursor_concepts`; runtime did not infer journal scope; controlled supported-scope UAT separately demonstrated the runtime contract. | Keep Seam 8 open; do not hardcode query-specific scope inference |

## Closeout Note

- When this bundle completes, move it from `active/` to `archive/`.
