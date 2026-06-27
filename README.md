# semantic-traversal

`semantic-traversal` is a local conversational runtime over persisted thread state and ingested note chunks. Each user turn preserves the raw message, persists semantic-extraction and retrieval diagnostics, updates thread state, and appends a hash-chained ledger record. The current checked-in runtime fails closed before synthesis because the thesis-valid semantic activation and traversal chain is not yet implemented.

The current runtime keeps three practical layers visible:

- additive semantic extraction packets
- lexical SQLite retrieval
- inspectable per-turn artifacts and ledger hashes

## What A Turn Does

For each user message, the runtime:

1. Loads prior `thread_state` and `conversation_thread` artifacts if they exist.
2. Preserves the raw user input unchanged.
3. Runs isolated semantic extraction from the raw message.
4. Runs contextual semantic extraction using the raw message, prior thread state, and the isolated extraction.
5. Builds additive retrieval preparation from raw lexical terms plus semantic extraction hints.
6. Records lexical SQLite observations from the ingestion database when available.
7. Builds a blocked-or-approved synthesis context packet that keeps the raw user input authoritative.
8. Calls the LLM backend only when the runtime outcome is `completed`.
9. Saves the next thread state, state delta, turn artifacts, and hash-chained ledger record.

The runtime still does not use embeddings, vector search, graph traversal, or synthetic node promotion.

## Semantic Extraction

Normal turn execution does not expose semantic extractor mode selection on the CLI. The normal runtime resolves a real extractor from configuration and blocks if the extractor is unavailable, invalid, disabled, or fixture-backed.

Environment variables:

- `SEMANTIC_EXTRACTOR_MODE=ollama|auto`
- `SEMANTIC_EXTRACTOR_MODEL=<model name>`
- `SEMANTIC_EXTRACTOR_BASE_URL=http://localhost:11434`

Stub and disabled semantic extractors are reserved for tests and diagnostic probes only.

## Artifact Layout

Default runtime artifacts live under the local temp directory:

- thread data root: `$env:TEMP\semantic-traversal`
- probe data root: `$env:TEMP\semantic-traversal-probes`

Per turn, the runtime writes:

- `isolated_semantic_extraction_packet.json`
- `isolated_semantic_extraction_raw.json`
- `contextual_semantic_extraction_packet.json`
- `contextual_semantic_extraction_raw.json`
- `semantic_context_packet.json`
- `semantic_traversal_manifest.json`
- `retrieval_packet.json`
- `coverage_report.json`
- `synthesis_context_packet.json`
- `state_delta.json`

The ledger records hashes for all of those persisted turn artifacts, plus the next thread state.

## Runtime Decisions

Normal runtime execution is binary:

- `completed`
- `blocked`

Coverage uses `decision=approved` or `decision=blocked`. Diagnostic lexical observations may still record index-missing, no-query-term, no-match, or matched-chunk component results, but those observations do not approve synthesis.

Semantic extraction still uses explicit per-pass statuses:

- `parsed`
- `stub`
- `disabled`
- `unavailable`
- `invalid_json`

## Quick Start

Run the test suite:

```powershell
python -m unittest discover -s tests -v
```

Run the test suite:

```powershell
python -m unittest discover -s tests -v
```

Run a normal CLI turn:

```powershell
python -m semantic_traversal --message "Please retrieve the candy snack food before bed note." --llm-mode stub
```

Without a configured real semantic extractor, that command emits blocked diagnostic artifacts and exits non-zero.

## Ingest The Notes

Build or refresh the local ingestion database:

```powershell
python -m semantic_traversal ingest --repo-root . --data-root $env:TEMP\semantic-traversal
```

That command reads the repository corpus and fixture notes, stores them in SQLite, and writes a JSON manifest for inspection.

## Probe Commands

```powershell
python -m semantic_traversal.probes probe_lexical_retrieval_fixture_hit --repo-root . --data-root $env:TEMP\semantic-traversal-probes-hit
python -m semantic_traversal.probes probe_lexical_retrieval_no_index --repo-root . --data-root $env:TEMP\semantic-traversal-probes-noindex
python -m semantic_traversal.probes probe_lexical_retrieval_no_match --repo-root . --data-root $env:TEMP\semantic-traversal-probes-nomatch
python -m semantic_traversal.probes probe_lexical_retrieval_no_query_terms --repo-root . --data-root $env:TEMP\semantic-traversal-probes-noquery
python -m semantic_traversal.probes probe_ledger_hash_artifact_integrity --repo-root . --data-root $env:TEMP\semantic-traversal-probes-integrity
python -m semantic_traversal.probes probe_turn_cli_artifact_paths --repo-root . --data-root $env:TEMP\semantic-traversal-probes-cli
python -m semantic_traversal.probes probe_same_thread_continuation_turn --llm-mode stub --data-root $env:TEMP\semantic-traversal-thread-continuity
python -m semantic_traversal.probes probe_semantic_extraction_stub_packets --repo-root . --data-root $env:TEMP\semantic-traversal-probes-extract-stub
python -m semantic_traversal.probes probe_blocked_runtime_with_disabled_extraction --repo-root . --data-root $env:TEMP\semantic-traversal-probes-extract-disabled
python -m semantic_traversal.probes probe_semantic_extraction_contextual_thread_state --repo-root . --data-root $env:TEMP\semantic-traversal-probes-extract-context
python -m semantic_traversal.probes probe_semantic_extraction_hash_integrity --repo-root . --data-root $env:TEMP\semantic-traversal-probes-extract-integrity
python -m semantic_traversal.probes probe_blocked_runtime_with_stub_extraction --repo-root . --data-root $env:TEMP\semantic-traversal-probes-full-stub
```

## How To Inspect A Turn

Useful files after a turn:

- `isolated_semantic_extraction_packet.json` for the isolated extraction request, status, metadata, and parsed payload
- `contextual_semantic_extraction_packet.json` for the contextual extraction request, including prior thread state
- `semantic_context_packet.json` for additive retrieval preparation and semantic extraction status
- `semantic_traversal_manifest.json` for diagnostic activation surfaces and selected chunk IDs
- `retrieval_packet.json` for raw lexical terms, extraction hint terms, candidate term sources, and returned chunks
- `coverage_report.json` for binary approval-vs-blocked gating plus blocking reasons
- `synthesis_context_packet.json` for the exact context that would reach the final LLM when the runtime is approved
- `state_delta.json` for the persisted state transition
- `thread_ledger.jsonl` for the hash chain across turns

## Human UAT Focus

The current next end goal is implementing the missing thesis-valid activation and traversal chain so blocked diagnostic turns can become completed runtime turns.

Good break attempts:

- confirm the raw user message is unchanged across extraction and synthesis artifacts
- confirm the normal CLI blocks when no real semantic extractor is configured
- run the stub and disabled diagnostic probes and confirm they stay blocked while still persisting extraction and retrieval artifacts
- inspect `candidate_term_sources` and verify raw lexical terms are not dropped when extraction is sparse
- run two turns on the same thread and confirm the contextual extraction request includes prior thread state
- compare ledger hashes to the persisted artifact contents on disk

## Notes

- Live final-answer mode still requires `OPENAI_API_KEY`.
- Ollama is optional and is not required for tests or acceptance.
- Completed implementation bundles live under `agent_harness/implementation-projects/archive/`.
