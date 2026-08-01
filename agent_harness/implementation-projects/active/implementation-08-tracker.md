# Implementation 08 — Seam 0 Tracker

## Status

- State: accepted
- Project status: Seam 0: operator-accepted
- Current work: corrected Seam-0 bundle accepted as a planner-evaluation baseline.
- Next action: preserve Seam-0 boundary; do not begin Seam 1 in this correction.

## Work log

| Date | Change | Evidence | Next |
| --- | --- | --- | --- |
| 2026-07-29 | Created branch from exact supplied SHA | `git rev-parse HEAD` = `ad7c03fec4efcf86b773a2b377ccfada4eb24a67` | Preflight and records |
| 2026-07-29 | Ran preflight | 192 Python tests passed; compileall, diff check, plugin build, project strict TS passed | FTS reproduction |
| 2026-07-29 | Inspected persisted inventory read-only | No local database or latest manifest available | Record limitation honestly |
| 2026-07-29 | Reproduced and repaired FTS5 serialization defect | Starting-tree `I'm` produced raw `i'm` and structured lexical failure; focused regression suite now passes | Complete machine gates |
| 2026-07-29 | Ran generic baseline | 29 terminal JSONL records; controlled unavailable/timeout/malformed cases; qwen3:8b reachable | Review evidence and close out |
| 2026-07-30 | Corrected harness and authority records | Production-shaped state reload, observed/expected separation, atomic per-case checkpoints, focus sequencing | Replace baseline and verify |
| 2026-07-30 | Re-ran corrected qwen3:8b baseline | 29 unique terminal records; parsed 26, timed_out 1, unavailable 1, invalid_json 1 | Complete machine gates |
| 2026-07-30 | Completed correction verification | 206 local Python tests; 14 focused tests; compileall, diff check, plugin build, strict TypeScript passed | Final status transition |
| 2026-08-01 | Corrected private-UAT measurement instrument | Replaced schema v1 with incompatible per-turn schema v2; added raw compiler-contract observation, runtime-owned negative authorization metrics, all-turn aggregation, and allowlisted report v2 | Preserve the original v1 run locally; rerun the unchanged private baseline and inspect results before Seam 1 |
| 2026-08-01 | Corrected private-UAT repair observation semantics | Schema-v2 private rerun reached the unchanged production one-shot repair path; evaluator had assumed one compiler call per turn. Attempts are now explicit, required operators use subset semantics, and surface comparisons are non-authoritative diagnostics | Preserve failed private contents locally; rerun with a fresh post-observation-fix suite ID |
| 2026-08-01 | Corrected private-UAT repair timeout semantics | A repair request reached the configured 120-second timeout and production returned no raw repair response. The evaluator now records unavailable contract status, separates final response attempt from retained plan source, atomically records blocked turns, and continues to independent cases | Preserve original ignored runs; rerun with a fresh post-repair-timeout suite ID |
| 2026-08-01 | Canonical semantic chunk alignment correction | The private baseline exposed admitted semantic frontmatter being present in FTS but absent from vector input and selected synthesis evidence; the runtime now uses one admitted chunk surface and the evaluator normalizes unrestricted exact coverage scope | Complete post-fix reingest and rerun with a fresh post-alignment suite ID |

## Work status

| Work | Status | Verification |
| --- | --- | --- |
| Seam-0 records | operator-accepted | Corrected Seam-0 bundle |
| FTS reproduction | passed | Independent starting-tree fixture |
| Serializer repair | passed | 8 focused lexical/runtime tests |
| Evaluation matrix | passed | 29 persisted scenario-turn records |
| Resumable baseline harness | passed | 6 harness tests; atomic per-case checkpoints and truncation recovery |
| Machine closeout | passed | Required local gates passed; bounded commits pushed; clean worktree confirmed |

## Blockers

| Blocker | Boundary | Resolution |
| --- | --- | --- |
| Persisted inventory unavailable locally | Read-only environment state | Record unavailable; do not reingest |

All records agree: `Seam 0: operator-accepted`. The baseline is accepted as a planner-evaluation baseline, not as evidence of acceptable qwen3:8b quality or successful retrieval/synthesis. Seam 1 has not begun.

This preparation pass does not begin Seam 1. The private local baseline tooling is prepared, but real private-corpus execution remains operator-pending. Seam 1 must wait until the operator has pulled the branch, populated the ignored fixture, run the local baseline, inspected the raw private results, and explicitly accepted or recorded the result.

The initial schema-v1 private run and the partial schema-v2 private contents
remain historical evidence outside the repository and operator-preserved.
Neither is migrated, overwritten, quoted, or committed here. The corrected
evaluator records the production initial-plus-one-repair topology, preserves
initial versus final compiler quality, treats required operators as subset
requirements, and treats subject/referent strings as non-authoritative surface
signals. The corrected private baseline remains operator-rerun-pending under a
fresh suite ID. Production behaviour remains unchanged. Inventory
inspection/redesign and model replacement/bakeoff remain separate future work;
Seam 1 has not begun.

The private schema-v2 baseline reached the one-shot repair path. The repair
request reached the configured timeout, production returned no raw repair
response, and the evaluator's prior requirement for a hash on every final
attempt was invalid. This correction changes only measurement semantics:
failed repairs are recorded as deterministic failed or incomplete turns,
without changing the timeout, repair behaviour, prompts, models, retrieval,
inventory, synthesis, corpus, or fixture schema. The original private runs
remain ignored and operator-preserved. The corrected baseline remains
operator-rerun-pending; Seam 1 has not begun.

The completed pre-fix private baseline also exposed a semantic chunk alignment
defect: configured semantic frontmatter was stored and indexed in FTS but was
absent from vector input and selected synthesis evidence, so metadata-only
changes did not necessarily invalidate vectors. The correction uses one
generated admitted semantic chunk surface across applicable retrieval and
synthesis boundaries while preserving each operator's distinct semantics. The
inventory remains a bounded generated projection, not a second ontology. The
exact-absence evaluator now recognizes an unrestricted runtime filter as the
fixture's `complete_eligible_corpus` requirement without conflating coverage
with negative-claim authorization. The completed pre-fix run remains
operator-preserved; post-fix reingest and a fresh private baseline are
pending. Seam 1 has not begun.

The post-alignment private baseline completed structurally and showed canonical
metadata propagation working, but all six compiler turns emitted invalid
contracts and used deterministic fallback. Repository-safe inspection found
that the compiler-facing inventory payload duplicated full facet surfaces,
enumerated high-cardinality values, and exposed output-schema-like capability
mapping. The correction preserves the one full persisted inventory and adds
one deterministic in-memory compiler projection: every admitted field name is
retained, only generic low-cardinality values are enumerated, path and source
observations are bounded, and the serialized projection is capped by YAML.
The full inventory remains authoritative for persistence, validation, resolver
binding, traversal, manifests, and diagnostics. No reingest is required; the
post-alignment run remains operator-preserved and the corrected private rerun
remains pending. Seam 1 has not begun.

## Resolved-referent propagation correction

PR #15 successfully excised scope aliases and established the context-first
temporal kernel. The post-PR-15 private rerun showed compiler referents
present upstream, but resolver binding omitted them. Temporal execution then
used an anonymous query-level fallback; the improved answer was supported by
other evidence and did not validate per-subject temporal execution.

This correction preserves ordered, canonically deduplicated referents from
compiler plan through binding and temporal execution. It distinguishes genuine
no-referent query context from propagation failure, counts named-subject
coverage only from real referents, and preserves same-chunk multi-subject
provenance during temporal deduplication. Lexical multi-query flattening
remains deferred. Repair and fallback remain unchanged. No reingest is
required; the fresh private rerun remains operator-pending; Seam 1 has not
begun.
# Implementation 08 — pre-Seam-1 kernel correction

## Canonical retrieval-surface completeness

PR #16 preserved resolved referents through temporal execution. The completed
post-PR-16 private rerun confirmed context-first subjects reached the executor,
then exposed a mismatch between inventory claims and actual operator reach.
Admitted frontmatter was already present in canonical chunks, FTS, embeddings,
and selected evidence, but exact retrieval omitted it. Graph ingest extracted
body wikilinks only, and traversal was outbound-only.

This correction adds one generated closed-world retrieval-surface manifest. It
describes capability rather than meaning, aligns exact and lexical retrieval
with the canonical substrate, extracts admitted-frontmatter wikilinks, and
supports outbound, inbound, and both-direction traversal with canonical
hydration and merged authored-link provenance. Vector coverage was verified
without changing its model or policy. Temporal execution remains independent
before fusion; no graph-to-temporal chaining or field-specific ontology was
added. Inventory schema is 3, projection version is 2, and manifest version is
1. Complete post-merge reingest is required before a fresh private rerun.
Model bakeoff remains pending; the fresh private suite is operator-pending and
Seam 1 has not begun.

The post-PR-14 private rerun completed five cases and six turns: all six compiler contracts were valid, with no repair or fallback. Exact-absence coverage and canonical metadata propagation remained healthy. A valid multi-subject temporal plan nevertheless admitted globally earliest unrelated evidence; compiler-emitted substrate terms were rejected by the positive alias gate.

This correction excises the scope-alias subsystem and establishes temporal relevance independently per existing subject before temporal ordering. Missing required subject evidence blocks a complete comparison. No replacement positive ontology, filter language, attachment protocol, or speculative database-safety layer was added. Future UI attachments and the model bakeoff remain separate work. Seam 1 has not begun.
