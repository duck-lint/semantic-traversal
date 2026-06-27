# semantic-traversal implementation-06 summary

## Goal and Final Status

- Project prefix: `semantic-traversal`
- Bundle: `implementation-06`
- Goal: harden additive semantic extraction for human UAT without widening the architecture
- Final status: `complete`, archived on `2026-06-26`

## Changed Surfaces

- `semantic_traversal/semantic_extraction.py`
- `semantic_traversal/runtime.py`
- `semantic_traversal/probes.py`
- `tests/test_ingest_runtime.py`
- `README.md`
- `.github/workflows/tests.yml`
- `agent_harness/implementation-projects/archive/implementation-06-plan.md`
- `agent_harness/implementation-projects/archive/implementation-06-tracker.md`

## What Changed

- Added explicit `raw_user_input_validation` diagnostics to extraction packets so missing or mismatched model-supplied raw input is visible even when the authoritative raw input is preserved.
- Kept raw-input preservation intact while stopping silent epistemic repair.
- Pruned extraction hint harvesting to the approved additive field list:
  - isolated `candidate_targets`
  - isolated `candidate_relations`
  - isolated `terms_or_phrases_not_to_discard`
  - contextual `activation_hints.lexical_terms`
  - contextual `activation_hints.phrases`
  - contextual `activation_hints.entity_hints`
  - contextual `activation_hints.relation_hints`
- Added more specific source labels in `candidate_term_sources`.
- Added `.github/workflows/tests.yml` for push and pull request unittest coverage.
- Added a stub-backed diagnostic probe for artifact persistence and local blocked-runtime verification.

## Verification Evidence

- `python -m unittest discover -s tests -v` -> pass (`Ran 28 tests`, `OK`)
- `python -m semantic_traversal.probes probe_lexical_retrieval_fixture_hit --repo-root . --data-root $env:TEMP\semantic-traversal-probes-hit` -> pass
- `python -m semantic_traversal.probes probe_lexical_retrieval_no_index --repo-root . --data-root $env:TEMP\semantic-traversal-probes-noindex` -> pass
- `python -m semantic_traversal.probes probe_lexical_retrieval_no_match --repo-root . --data-root $env:TEMP\semantic-traversal-probes-nomatch` -> pass
- `python -m semantic_traversal.probes probe_lexical_retrieval_no_query_terms --repo-root . --data-root $env:TEMP\semantic-traversal-probes-noquery` -> pass
- `python -m semantic_traversal.probes probe_ledger_hash_artifact_integrity --repo-root . --data-root $env:TEMP\semantic-traversal-probes-integrity` -> pass
- `python -m semantic_traversal.probes probe_turn_cli_artifact_paths --repo-root . --data-root $env:TEMP\semantic-traversal-probes-cli` -> pass
- `python -m semantic_traversal.probes probe_same_thread_continuation_turn --llm-mode stub --data-root $env:TEMP\semantic-traversal-thread-continuity` -> pass
- `python -m semantic_traversal.probes probe_semantic_extraction_stub_packets --repo-root . --data-root $env:TEMP\semantic-traversal-probes-extract-stub` -> pass
- `python -m semantic_traversal.probes probe_blocked_runtime_with_disabled_extraction --repo-root . --data-root $env:TEMP\semantic-traversal-probes-extract-disabled` -> pass
- `python -m semantic_traversal.probes probe_semantic_extraction_contextual_thread_state --repo-root . --data-root $env:TEMP\semantic-traversal-probes-extract-context` -> pass
- `python -m semantic_traversal.probes probe_semantic_extraction_hash_integrity --repo-root . --data-root $env:TEMP\semantic-traversal-probes-extract-integrity` -> pass
- `python -m semantic_traversal.probes probe_blocked_runtime_with_stub_extraction --repo-root . --data-root $env:TEMP\semantic-traversal-probes-full-stub` -> pass (`llm_mode: not_called`, extraction statuses `stub`, `coverage_decision: blocked`)

## User-Facing Acceptance Result

- Acceptance result: pass
- Raw-input mismatch or omission is no longer silently repaired without an explicit packet-visible diagnostic.
- Retrieval hint harvesting is smaller, cleaner, and limited to approved additive hint fields.
- A boring CI workflow now exists for unittests.
- The repo has dedicated blocked-runtime diagnostic probes for stub and disabled semantic extraction.

## Known Limitations

- Ollama remains optional and was not required for acceptance.
- Retrieval is still lexical SQLite retrieval; this bundle did not widen into embeddings, vector search, graph traversal, or semantic coverage math.
- CI currently runs the unittest suite only and does not yet run the probe matrix.

## Next End Goal

- Human UAT over additive semantic extraction
