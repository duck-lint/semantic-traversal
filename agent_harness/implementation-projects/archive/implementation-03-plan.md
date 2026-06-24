# Implementation 03 Plan

## Intent

Add the minimal deterministic lexical retrieval bridge from the existing ingestion SQLite database into `run_thread_turn`, so each turn can emit real retrieval-aware perturbation artifacts without widening into embeddings, vectors, graphs, or synthetic-node promotion.

Delivery posture for this bundle is conditional:

- report `live-wired` only if retrieval artifacts are persisted, the synthesis context includes them, and the turn ledger records real hashes for the new perturbation artifacts
- report `scaffold-only` if retrieval is absent, the SQLite index is missing, or the runtime only serializes empty placeholders

## Admissibility Report

- Invariant constraints:
  - keep the runtime bounded to boring deterministic lexical retrieval only
  - do not add vector search, embeddings, graph expansion, coverage loops, or synthetic-node promotion
  - preserve existing thread continuity, hash chaining, and CLI behavior
- Task constraints:
  - wire retrieval into `run_thread_turn`
  - persist inspectable perturbation artifacts under the thread directory
  - keep the path deterministic and parameterized over the existing SQLite ingestion database
  - do not require the OpenAI SDK for stub-mode operation
- Constraint conflicts:
  - none identified from the current repo state; the ingestion database and chunk tables already exist
- Allowed transformation types:
  - update runtime code, storage helpers, CLI payloads, tests, and probes as needed for the lexical retrieval slice
  - add a focused archive summary when the slice completes
- Affected surfaces:
  - `semantic_traversal/runtime.py`
  - `semantic_traversal/ingest.py`
  - `semantic_traversal/storage.py` if persistence helpers need a small extension
  - `semantic_traversal/cli.py`
  - `semantic_traversal/probes.py`
  - `tests/test_ingest_runtime.py`
  - any new focused retrieval tests
- Non-affected surfaces:
  - vector retrieval
  - graph traversal
  - embeddings
  - synthetic-node promotion
  - project-spec and open-decision files
- Admissibility checks:
  - the runtime must surface `semantic_context_packet`, `semantic_traversal_manifest`, `retrieval_packet`, and `coverage_report`
  - the synthesis context packet must include the retrieval artifacts when present
  - ledger hashes for the new artifacts must be non-null when retrieval is attempted
  - no-index and no-match cases must be explicit and non-crashing
  - same-thread continuation must still preserve parent perturbation hash behavior
- Stop conditions:
  - stop if the implementation tries to introduce non-deterministic search, embeddings, or graph logic
  - stop if the SQLite ingestion database cannot be reused without a broader storage redesign

## Observed Evidence

- `semantic_traversal/ingest.py` already materializes a SQLite database with a `chunks` table that contains chunk text and metadata suitable for lexical lookups
- `semantic_traversal/runtime.py` already builds the synthesis context and ledger spine for a turn
- `semantic_traversal/cli.py` already preserves existing turn CLI behavior
- `tests/test_ingest_runtime.py` already covers ingestion, chunking, and reingest behavior
- `semantic_traversal/probes.py` already provides a probe entry point pattern that can be extended for the retrieval slice

## Planned Work

1. Retrieval seam
   - Load the existing ingestion SQLite database from the repo-local data root when available.
   - Tokenize user input into simple lexical terms, drop tiny/common stop terms, and rank chunks by deterministic term hit count plus stable ordering.
   - Return a small bounded retrieval packet with chunk metadata and concrete text.
2. Perturbation artifacts
   - Create deterministic `semantic_context_packet`, `semantic_traversal_manifest`, `retrieval_packet`, and `coverage_report` artifacts for each turn.
   - Persist the artifacts under the turn directory alongside the existing thread files.
   - Hash the artifacts and include those hashes in the ledger record.
3. Synthesis context and ledger
   - Feed the retrieval artifacts into the synthesis context packet when retrieval is present and approved.
   - Keep the parent perturbation hash chain intact and continue recording prior thread-state and next-thread-state hashes.
4. Tests and probes
   - Add deterministic tests for known fixture queries, no-index behavior, no-match behavior, retrieval persistence, and same-thread continuation.
   - Keep existing tests passing.

## Non-Goals

- vector search or embeddings
- graph traversal or graph expansion
- synthetic-node promotion
- coverage math beyond a minimal status report
- broad CLI redesign
- durable storage migration outside the current repo-local pattern

## Delivery Posture And Acceptance Criteria

Named user-facing acceptance probes for this bundle:

1. `probe_lexical_retrieval_fixture_hit`
   - Run a turn using a query that should match at least one known ingested chunk.
   - Expected observable result: the retrieval packet contains at least one expected chunk and the synthesis context includes the retrieval artifacts.
2. `probe_lexical_retrieval_no_index`
   - Run a turn without the ingestion SQLite database present.
   - Expected observable result: the turn completes, the coverage report is explicit about `no_index`, and the runtime does not crash.
3. `probe_same_thread_parent_hash_continuity`
   - Run two turns on the same thread.
   - Expected observable result: the second ledger record points to the first perturbation hash as its parent and all retrieval artifact hashes are present when retrieval was attempted.

Failure conditions that block completion:

- retrieval artifacts are missing or empty when the database is available
- the synthesis context omits retrieval artifacts after retrieval succeeds
- ledger hashes remain null for retrieval-related artifacts when retrieval is attempted
- no-index or no-match behavior crashes or becomes ambiguous
- thread continuity or parent hash chaining regresses

## Current Repo Runtime State

- `implementation-02` is being archived as a partial closeout with seams 1 through 4 complete.
- The runtime already has a turn spine, local storage helpers, and ingestion SQLite materialization to build on.
- No blocker has been identified that would prevent the lexical retrieval slice from starting.

## Assumptions And Unknowns

Assumptions:

- The ingestion SQLite database will remain the source of truth for lexical retrieval.
- A small deterministic stop-word list is sufficient for the first slice.

Unknowns:

- The exact turn-directory path shape used by the repo for new perturbation artifacts.
- Whether a small storage helper is needed to persist the new artifact files cleanly.

## Affected And Non-Affected Surfaces

Planning surfaces changed in this bundle:

- `agent_harness/implementation-projects/active/implementation-03-plan.md`
- `agent_harness/implementation-projects/active/implementation-03-tracker.md`

Implementation surfaces that must move together for this bundle to be truthful:

- turn runtime artifact assembly
- lexical retrieval from the ingestion SQLite database
- per-turn artifact persistence
- ledger hash updates
- focused retrieval tests and probes

Surfaces that must not move under this bundle:

- vectors, embeddings, graphs, synthetic-node promotion
- broader architecture or storage redesign

## Verification Contract Summary

- Structural verification:
  - confirm retrieval artifacts are persisted and inspectable
  - confirm ledger hashes are populated for the new retrieval-related artifacts
  - confirm the synthesis context includes retrieval artifacts when they exist
- Behavior verification:
  - run the repository's existing test suite
  - run new retrieval-focused probes
  - confirm same-thread continuation still preserves parent perturbation hash behavior
- Downgrade rule:
  - if the ingestion database is absent, the runtime should still complete the turn with an explicit `no_index` or similar coverage status
- Review obligation:
  - reviewer must reject any claim of retrieval integration if the runtime only exposes placeholders, filenames, or schema stubs

## Completion Rule

- Do not mark behavior complete on shape-only evidence without a real turn runtime path and visible persisted artifacts.

## Approval Gates

- [ ] Schema
- [ ] API
- [ ] Auth
- [x] Storage
- [ ] Deployment
- [ ] Destructive operation
- [ ] Broad architecture
- [ ] Project-intent authority not covered by spec or current authorization

Gate notes:

- The ingestion SQLite database already exists, so the storage slice is an extension of the current local artifact model rather than a redesign.
- Raise a new gate only if the implementation tries to widen into vector search, graph work, or durable external storage.

## Handoff Packet For Next Agent

## Role

- From: planner
- To: implementer
- Requested action: wire minimal lexical retrieval into `run_thread_turn`, persist the new turn artifacts, and prove the retrieval-aware turn path with deterministic tests

## Project And Task

`implementation-03` is the first bridge from ingested latent-space content into threaded synthesis. The task is to keep the retrieval path boring, deterministic, and inspectable: load chunks from the existing SQLite ingestion database, build minimal semantic context and traversal manifest artifacts, persist a retrieval packet and coverage report, include approved retrieval material in synthesis context, and keep the ledger hash-chain truthful.

## Admissibility Report

- Invariant constraints:
  - lexical retrieval only
  - no vector search, graph expansion, embeddings, or synthetic-node promotion
  - preserve continuity and hash chaining
- Task constraints:
  - keep the implementation small
  - make no-index and no-match explicit
  - persist inspectable artifacts under the turn directory
- Constraint conflicts:
  - none identified from the current repo state
- Allowed transformation types:
  - runtime, storage, CLI payload, tests, and probes
- Affected surfaces:
  - turn runtime artifact assembly
  - lexical retrieval from the ingestion SQLite database
  - per-turn artifact persistence
  - ledger hash updates
  - focused retrieval tests and probes
- Non-affected surfaces:
  - vectors, embeddings, graphs, synthetic-node promotion
  - broader architecture or storage redesign
- Stop conditions:
  - report a blocker if the lexical retrieval path would require a broader design change than this bundle authorizes

## Expected Change

Implement the minimal retrieval bridge, persist the artifacts, and verify that the runtime remains deterministic and continuity-preserving with and without an ingestion database.

