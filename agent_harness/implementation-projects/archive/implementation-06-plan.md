# Implementation 06 Plan

## Intent

Harden the additive semantic extraction bundle before human UAT by tightening diagnostics, pruning retrieval-hint harvesting, adding basic CI, and making full-route stub execution explicit and consistent.

## Admissibility Report

- Invariant constraints:
  - preserve raw user input as the authoritative value everywhere
  - keep semantic extraction additive and non-authoritative
  - preserve lexical retrieval diagnostics and implementation-05 hash truthfulness
  - keep the patch small and local to the existing route
- Task constraints:
  - record explicit diagnostics when extractor payloads omit or mismatch `raw_user_input`
  - prune extraction hint harvesting to a small approved field list
  - add a basic GitHub Actions unittest workflow
  - make full-route stub execution explicit in probes and docs
- Constraint conflicts:
  - silently repairing mismatched extractor input without packet-visible diagnostics is no longer acceptable
  - recursively harvesting arbitrary model fields would overbuild the extraction seam
- Allowed transformation types:
  - semantic extraction helpers, runtime retrieval preparation, probes, tests, README, CI workflow, and implementation docs
- Affected surfaces:
  - `semantic_traversal/semantic_extraction.py`
  - `semantic_traversal/runtime.py`
  - `semantic_traversal/probes.py`
  - `tests/test_ingest_runtime.py`
  - `README.md`
  - `.github/workflows/tests.yml`
  - `agent_harness/implementation-projects/archive/implementation-06-plan.md`
  - `agent_harness/implementation-projects/archive/implementation-06-tracker.md`
- Non-affected surfaces:
  - embeddings
  - vector search
  - graph traversal
  - synthetic node promotion
  - broad retrieval redesign
  - broad storage or CLI redesign
- Admissibility checks:
  - raw-input repairs are visible in persisted extraction packets
  - noisy model fields do not become retrieval terms
  - the stub-backed diagnostic probe exercises blocked artifact persistence without non-local calls
  - CI requires no secrets and no live model backends
- Stop conditions:
  - stop if diagnostics would require breaking raw-input preservation
  - stop if pruning hint harvesting breaks raw lexical diagnostic observations
  - stop if full-route stub coverage requires a broad runtime redesign

## Planned Work

1. Open `implementation-06` as a small hardening bundle.
2. Add explicit `raw_user_input` repair diagnostics to extraction packets.
3. Prune extraction hint harvesting to the approved additive hint fields.
4. Add full-route stub probe coverage and tighten probe backend consistency.
5. Add a minimal GitHub Actions unittest workflow.
6. Run the requested tests and probes and archive only if all pass.

## Non-Goals

- deterministic role-weight query parsing
- embeddings, vector search, or graph traversal
- semantic coverage math
- broad runtime or prompt architecture changes
- requiring Ollama or OpenAI for validation

## Acceptance Criteria

- raw input mismatch and missing diagnostics are explicit in extraction packets
- no raw input mismatch is silently repaired without a visible record
- extraction hint harvesting uses only the small approved field list
- noisy fields do not become retrieval terms
- basic GitHub Actions CI exists and runs unittests
- full-route stub execution is documented and probed
- existing tests and probes still pass
- new tests and the new full-route stub probe pass
- no reverted role-weighting parser is reintroduced

## Current Repo Runtime State

- implementation-05 was archived complete before this hardening bundle
- the runtime now records explicit raw-input repair diagnostics in extraction packets
- retrieval hint harvesting is pruned to the approved additive field list
- a basic GitHub Actions unittest workflow exists under `.github/workflows/tests.yml`
- the repo has an explicit full-route stub probe for local end-to-end route exercise

## Assumptions And Unknowns

- the current unittest suite still requires no extra dependency installation in CI
- live Ollama behavior remains optional and outside this bundle’s acceptance surface

## Affected and Non-Affected Surfaces

- affected:
  - extraction packet truthfulness
  - retrieval preparation cleanliness
  - probe and README operator clarity
- non-affected:
  - ingestion schema
  - live model behavior outside existing optional seams

## Completion Rule

- Do not mark behavior complete on fixture, mock, dry-run, serialization, type, field, file, path, route, crate, config, or nominal-caller evidence alone.

## Approval Gates

- [ ] Schema
- [ ] API
- [ ] Auth
- [ ] Storage
- [ ] Deployment
- [ ] Destructive operation
- [ ] Broad architecture
- [ ] Project-intent authority not covered by spec or current authorization

## Closeout Note

- This bundle completed and archived with the repo ready for human UAT over additive semantic extraction.
