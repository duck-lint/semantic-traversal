# Implementation 07 Plan

## Intent

Complete the retrieval architecture described in the attached “Semantic Traversal Retrieval Completion” program while preserving the current runtime authority boundary and making every behavioural delta explicit.

## Admissibility Report

- Invariant constraints: YAML owns operational policy; raw user input remains authoritative; prompts and packet contracts are frozen except in the dedicated compiler/schema seam; no migration or persistent index-version system.
- Task constraints: work seam-by-seam, record hashes and evidence, use the current checkout as source of truth, and stop when a required boundary cannot be verified.
- Constraint conflicts: the attached program is broader than the currently exposed local agent tooling; no sub-agent delegation is claimed unless a delegation tool actually runs.
- Allowed transformation types: narrow production fixes, additive diagnostics/config fields, tests, probes, implementation records, and documentation grounded in verified behaviour.
- Affected surfaces: ingestion/index integrity, scope binding, exact/lexical/vector/graph retrieval, fusion, temporal metadata, persisted inventory, and final UAT artifacts as each seam is admitted.
- Non-affected surfaces: frontier synthesis prompt and UI behaviour unless a later explicit seam proves incompatibility.
- Admissibility checks: baseline hashes; current test/compile/build evidence; prohibited-file diff checks; seam-specific probes; full-suite verification after each merge-equivalent step.
- Stop conditions: a required field is silently ignored, a prompt changes outside Seam 8, a hidden operational default is introduced, or a failed rebuild can damage the prior active index.

## Planned Work

0. Baseline and drift guards.
1A. Ingest/index integrity.
1B. Plugin/tooling hygiene, only if independently reproducible.
2A. Scope algebra and hard/preferred scope.
2B. Exact-search status and coverage contract.
3. Runtime authority and requiredness.
4A. Graph executor.
4B. SQLite FTS5 executor.
4C. Multi-query vector executor and diversity controls.
5. Fusion, selection, and bounded packets; record ADR before algorithm changes.
6A. Temporal substrate.
7. Persisted resource inventory.
8. Explicit compiler/schema seam, with minimal prompt diff only if required.
9. Scaling, cleanup, documentation, and final UAT.

## Non-Goals

- No new sub-agent claims, migration framework, persistent index generations, ANN backend, UI redesign, broad prompt rewrite, or opportunistic dependency upgrade.
- No completion claim for a seam based only on fixtures, mocks, serialization, or file presence.

## Acceptance Criteria

The attached program’s 30 final acceptance criteria are the target, but each criterion must be supported by current-repo evidence and recorded in the tracker. Deferred or unverified criteria remain open.

## Current Repo Runtime State

The repository has archived implementation bundles through implementation-06 and no active bundle other than this one. Current runtime already contains lexical, vector, graph, exact, resolver, inventory, and selection code, but the attached requirements identify likely contract gaps including per-query vector execution, FTS5, scope algebra, exact statuses, graph direction/provenance, persisted inventory, and atomic index integrity. Baseline details are recorded in implementation-07-baseline.md.

## Assumptions And Unknowns

- The attached program is authoritative for desired behaviour, while existing tests and production consumers establish current contract details.
- The configured external vault may not be available in this environment; fixture-root tests are therefore required before any live ingest claim.
- No multi-agent delegation capability is exposed in the current tool set; this bundle is coordinator-executed unless that changes.

## Affected and Non-Affected Surfaces

See implementation-07-authority-map.md. The map is updated before each production seam and records allowed/prohibited files, tests, and downstream impact.

## Completion Rule

- Do not mark behavior complete on fixture, mock, dry-run, serialization, type, field, file, path, route, crate, config, or nominal-caller evidence alone.
- Do not close this bundle while any required acceptance criterion is merely aspirational.

## Approval Gates

- [ ] Schema
- [ ] API
- [ ] Auth
- [ ] Storage
- [ ] Deployment
- [ ] Destructive operation
- [ ] Broad architecture
- [x] Project-intent authority is supplied by the attached implementation program

## Closeout Note

- When this bundle completes, move it from `active/` to `archive/` only after final UAT and tracker evidence support closeout.
