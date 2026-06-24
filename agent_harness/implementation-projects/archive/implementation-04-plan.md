# Implementation 04 Plan

## Intent

Harden `implementation-03` so the retrieval seam is harder to accidentally misrepresent and easier for a human to inspect during acceptance testing.

This bundle is a review/cleanup and truthfulness-hardening pass, not a new architecture slice.

## In Scope

- verify ledger hash equality against persisted artifact contents
- add explicit `no_query_terms` coverage behavior
- resolve any implementation-03 active/archive doc-state mismatch truthfully
- improve CLI/probe observability around persisted per-turn artifact paths
- keep existing retrieval behavior stable
- prepare the repo for human user-acceptance testing

## Out of Scope

- vector search
- embeddings
- graph traversal
- graph ontology
- synthetic node promotion
- character/persona lenses
- branching
- semantic coverage math
- broad storage redesign
- broad CLI redesign
- changing the project thesis
- implementation-05 work

## Admissibility Report

- Invariant constraints:
  - preserve the retrieval runtime shape introduced by implementation-03
  - do not widen into vector, graph, or embedding behavior
  - keep the turn runtime deterministic and inspectable
- Task constraints:
  - add explicit `no_query_terms` status handling
  - persist any small artifact needed to verify ledger hashes truthfully
  - ensure CLI and probes expose enough artifact paths for human inspection
  - keep docs truthful about archive vs active bundle state
- Constraint conflicts:
  - any missing persisted artifact for hash verification is a truthfulness gap, not a license to hand-wave the hash check
- Allowed transformation types:
  - runtime, storage, CLI, probes, tests, and archive docs
- Affected surfaces:
  - `semantic_traversal/runtime.py`
  - `semantic_traversal/storage.py`
  - `semantic_traversal/cli.py`
  - `semantic_traversal/probes.py`
  - `tests/test_ingest_runtime.py`
  - `agent_harness/implementation-projects/archive/implementation-03-plan.md`
  - `agent_harness/implementation-projects/archive/implementation-03-tracker.md`
  - `agent_harness/implementation-projects/archive/semantic-traversal-implementation-03-summary.md`
- Non-affected surfaces:
  - vectors, embeddings, graph work, synthetic-node promotion
  - broad product/runtime redesign
- Admissibility checks:
  - ledger hashes can be compared to persisted JSON artifact contents
  - `no_query_terms` is distinct from `no_index`, `no_matches`, and `minimal_pass`
  - CLI output contains inspectable paths for the turn artifacts
  - the repo is left ready for human “try to break it” testing
- Stop conditions:
  - stop if implementing verification would require a broad redesign
  - stop if the runtime cannot truthfully distinguish the requested statuses
  - stop if archive state cannot be reconciled without fabricating evidence

## Observed Evidence

- `implementation-03` is already archived complete in `agent_harness/implementation-projects/archive/`
- the active directory still needed a truthfulness pass to ensure the bundle state matched the archive state
- `semantic_traversal/runtime.py` already persists the retrieval and synthesis artifacts needed for inspection
- `semantic_traversal/cli.py` already exposes the key turn artifact paths
- `tests/test_ingest_runtime.py` already covers the retrieval seam and can be extended for hash equality and status clarity

## Planned Work

1. Truthful status handling
   - Add explicit `no_query_terms` behavior and keep `not_attempted` reserved for future intentional skips.
   - Make the retrieval and coverage artifacts reflect that status clearly.
2. Hash integrity
   - Persist any small artifact needed for verifying `state_delta_hash`.
   - Add tests that compare ledger hashes to the hashes of the persisted artifact contents.
3. Inspectability
   - Add or improve CLI/probe observability for artifact paths.
   - Add a probe that verifies the CLI-reported artifact paths exist after a stub turn.
4. Doc-state cleanup
   - Resolve any active/archive mismatch for implementation-03 truthfully.
   - Leave the repo in a state suitable for human UAT.

## Acceptance Criteria

- `implementation-04` plan and tracker exist
- implementation-03 doc/archive state is truthful
- `semantic_context_packet_hash`, `semantic_traversal_manifest_hash`, `retrieval_packet_hash`, `coverage_report_hash`, `synthesis_context_packet_hash`, `next_thread_state_hash`, and `state_delta_hash` are verified against persisted artifact contents where applicable
- `no_query_terms` is explicit and non-crashing
- `minimal_pass`, `no_index`, `no_query_terms`, and `no_matches` are distinct and observable
- CLI/probe output exposes enough artifact paths for a human to inspect the turn
- existing tests pass
- new tests/probes pass
- an archive summary records what changed, what was validated, and what remains out of scope

## Validation Commands

- `python -m unittest discover -s tests -v`
- `python -m semantic_traversal.probes probe_lexical_retrieval_fixture_hit --repo-root . --data-root %TEMP%\semantic-traversal-probes-hit`
- `python -m semantic_traversal.probes probe_lexical_retrieval_no_index --repo-root . --data-root %TEMP%\semantic-traversal-probes-noindex`
- `python -m semantic_traversal.probes probe_lexical_retrieval_no_match --repo-root . --data-root %TEMP%\semantic-traversal-probes-nomatch`
- `python -m semantic_traversal.probes probe_lexical_retrieval_no_query_terms --repo-root . --data-root %TEMP%\semantic-traversal-probes-noquery`
- `python -m semantic_traversal.probes probe_ledger_hash_artifact_integrity --repo-root . --data-root %TEMP%\semantic-traversal-probes-integrity`
- `python -m semantic_traversal.probes probe_turn_cli_artifact_paths --repo-root . --data-root %TEMP%\semantic-traversal-probes-cli`
- `python -m semantic_traversal.probes probe_same_thread_continuation_turn --llm-mode stub --data-root %TEMP%\semantic-traversal-thread-continuity`

## Stop Conditions

- if any requested hash equality cannot be verified truthfully
- if `no_query_terms` cannot be made distinct without ambiguity
- if CLI/probe path observability would require a broad redesign
- if the work starts drifting into UAT-only changes without the hardening checks

## Handoff Note

- The next human step after this bundle is user acceptance testing, or “try to break it”

