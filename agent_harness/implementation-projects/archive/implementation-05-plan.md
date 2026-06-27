# Implementation 05 Plan

## Intent

Add an additive semantic extraction boundary ahead of retrieval and traversal preparation without mutating, narrowing, or replacing the authoritative raw user message.

## Admissibility Report

- Invariant constraints:
  - preserve the raw user input unchanged across extraction, semantic context, synthesis context, and state updates
  - keep semantic extraction additive and non-authoritative
  - preserve implementation-04 hash-truthfulness guarantees for persisted turn artifacts
  - keep the existing lexical SQLite path available as diagnostic instrumentation
- Task constraints:
  - add a semantic extractor backend interface with `stub`, `disabled`, and optional `ollama` backends
  - run isolated and contextual extraction passes using bounded packets
  - persist extraction packets and explicit raw-response artifacts
  - include extraction artifacts in semantic context, synthesis context, retrieval preparation, coverage reporting, and the ledger hash surface
  - add deterministic tests and probes proving additive, inspectable, non-destructive behavior
- Constraint conflicts:
  - any design that rewrites the user message or makes extraction authoritative violates the project thesis and must stop
  - any design that replaces lexical retrieval with deterministic weighting or semantic-only gating violates the request and must stop
- Allowed transformation types:
  - runtime, extractor backend, CLI, probes, tests, README, and implementation-project docs
- Affected surfaces:
  - `semantic_traversal/runtime.py`
  - `semantic_traversal/semantic_extraction.py`
  - `semantic_traversal/cli.py`
  - `semantic_traversal/llm.py`
  - `semantic_traversal/probes.py`
  - `tests/test_ingest_runtime.py`
  - `README.md`
  - `agent_harness/implementation-projects/archive/implementation-05-plan.md`
  - `agent_harness/implementation-projects/archive/implementation-05-tracker.md`
- Non-affected surfaces:
  - embeddings
  - vector search
  - graph traversal
  - synthetic node promotion
  - broad storage redesign
  - final-answer synthesis architecture beyond adding extraction context
- Admissibility checks:
  - raw user input appears unchanged in isolated extraction, contextual extraction, semantic context, and synthesis context artifacts
  - extraction failures remain explicit and non-crashing unless strict behavior is introduced later
  - retrieval preparation remains additive and never drops raw lexical terms because extraction omitted them
  - ledger hashes can still be verified directly against persisted artifact contents
- Stop conditions:
  - stop if implementation would require replacing raw user input with rewritten text
  - stop if extraction must become authoritative to complete the bundle
  - stop if the work requires embeddings, vector search, graph traversal, or broad storage redesign
  - stop if implementation-04 hash truthfulness cannot be preserved

## Planned Work

1. Open the bundle and inspect the post-revert runtime state.
2. Add the semantic extraction backend module and bounded response shapes.
3. Integrate isolated and contextual extraction into `run_thread_turn`.
4. Persist extraction packet and raw-response artifacts and extend the ledger hash surface.
5. Feed extraction hints additively into retrieval preparation while preserving the raw lexical channel.
6. Expose extraction mode, statuses, and artifact paths in the CLI.
7. Add deterministic tests and probes for additive extraction, blocked disabled-extraction diagnostics, contextual thread-state input, and hash integrity.
8. Run the requested validation commands and archive only if acceptance criteria pass.

## Non-Goals

- deterministic role-weight query parsing
- topic-specific semantic examples
- embeddings or vector search
- graph traversal or synthetic node promotion
- broad CLI redesign
- requiring Ollama for tests or acceptance

## Acceptance Criteria

- `implementation-05` plan and tracker exist
- the reverted role-weighting approach is not restored
- raw user input is preserved unmodified
- isolated and contextual semantic extraction artifacts exist
- extraction statuses are explicit and inspectable
- extraction artifacts appear in semantic context and synthesis context
- extraction hints are used additively and do not remove raw lexical terms
- lexical diagnostic observations still persist when extraction is disabled or unavailable
- contextual extraction receives prior thread state
- new extraction artifact hashes are recorded in the ledger and verified against persisted contents
- existing implementation-04 tests and probes still pass
- new semantic-extraction tests and probes pass
- archive summary records changes, validations, and remaining limitations

## Current Repo Runtime State

- `implementation-04` was archived complete before this bundle opened
- the runtime now preserves raw input, performs two additive semantic extraction passes, and keeps lexical SQLite retrieval as diagnostic instrumentation
- the reverted `fe146c7` role-weighted lexical ranking bundle remains absent from the final working tree

## Assumptions And Unknowns

- `disabled` remains the safe default when no semantic extractor mode or Ollama model is configured
- the local Ollama endpoint may be absent, and that should surface as `unavailable` rather than a crashing turn
- extraction hints improve retrieval preparation, but the bundle does not yet add embeddings, vector search, graph traversal, or synthetic-node promotion

## Affected and Non-Affected Surfaces

- affected:
  - turn runtime artifact construction
  - retrieval preparation metadata
  - CLI and probe observability
  - ledger hash verification surfaces
- non-affected:
  - ingestion schema
  - final synthesis model selection
  - archive policy outside this bundle

## Completion Rule

- Do not mark behavior complete on fixture, mock, dry-run, serialization, type, field, file, path, route, crate, config, or nominal-caller evidence alone.

## Approval Gates

- [ ] Schema
- [ ] API
- [ ] Auth
- [ ] Storage
- [ ] Deployment
- [ ] Destructive operation
- [x] Broad architecture
- [ ] Project-intent authority not covered by spec or current authorization

## Closeout Note

- This bundle completed and archived with the next end goal set to human UAT over additive semantic extraction and retrieval interaction.
