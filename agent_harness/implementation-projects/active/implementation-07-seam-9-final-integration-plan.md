# Implementation 07 Seam 9 Final Integration Plan

Status: active closeout checklist; no new behavioral architecture is approved.

## Accepted contracts to freeze

- exact-search status, count, context, required-term, and absence semantics;
- FTS5 modes, freshness validation, and atomic staged ingest;
- vector identity, threshold `0.5`, per-query cap `50`, per-note cap `4`, and
  deterministic multi-query allocation;
- graph direction, depth, fairness, bounded provenance, and scope behavior;
- hard/preferred scope, required-layer contribution, and runtime/YAML authority;
- unweighted ordinal fusion, required reservations, breadth-before-depth by
  `note_id`, and the configured global `max_chunks` ceiling;
- typed temporal anchors, governing-anchor selection, relation ordering, and
  fifth-surface provenance;
- persisted inventory snapshot validation and same-turn reuse;
- compiler subject preservation, explicit-empty semantics, retired policy
  diagnostics, and compiler/runtime authority separation;
- centralized compiler/frontier prompts and existing packet boundaries.

## Machine-verifiable gates

- reconcile tracker and ADR/UAT status without erasing historical evidence;
- audit YAML/runtime consumers, duplicate authority, retired fields, and
  production TODO/FIXME/dead-code signals;
- verify compiler/schema/prompt markers and all current hashes;
- inspect the active database read-only and record projection/inventory health;
- run the complete Python suite, `compileall`, diff checks, and repository
  checks already defined by the project;
- run plugin dependency/build/type/lint commands from its declared package
  scripts without changing dependency policy unless a concrete blocker is
  reproducible and narrowly repairable;
- record the final UAT matrix and packet/manifest evidence with deterministic
  comparisons where artifacts are available;
- complete privacy/security and migration-documentation audits.

## Final UAT matrix

Record exact positive/negative cases, broad conceptual retrieval, the accepted
genealogy thread, explicit temporal boundaries, outbound/inbound/both graph
direction, two-query vector retrieval, preferred scope, a narrow factual
query, referential continuation/reset, and a low-signal control. Separate
compiler, executor, packet, synthesis, controlled, normal-live, and operator
evidence. Do not require stochastic prose identity.

## Plugin readiness gates

Inspect the declared package manager, lockfile, build, typecheck, lint/tests,
manifest, runtime connection, generated-output convention, and the historical
`electron` declaration failure. Record plugin/runtime machine-boundary checks.
Interactive Obsidian UI checks remain operator-only unless actually exercised.

## Operator-only gates

If interactive UI cannot be exercised, leave a bounded checklist covering plugin
reload, normal query rendering, persisted artifacts, clean runtime error/recovery,
and no unnecessary reingest after restart. Do not claim visual completion.

## Cleanup boundaries

Only documentation, tests/guards, and demonstrably dead or duplicate authority
may change. Do not implement preferred reservoirs, sparse stopping, new caps,
weighted RRF, new temporal/inventory semantics, compiler topic heuristics,
prompt changes, broad dependency upgrades, or unrelated refactoring.

## Archive conditions

Archive only after all machine gates, final UAT artifacts, plugin build/type
status, operator-only status, reconciled tracker rows, known limitations, and
privacy/security checks are recorded. If the UI gate remains open, mark the
bundle machine-complete/operator-UAT-pending and keep active records.

## Rollback boundary

Use normal git reverts for any closeout repair. Do not reset or rewrite history.
Production retrieval/index data is not migrated by this closeout. Any future
behavioral change requires a separately approved seam and its own rollback.
