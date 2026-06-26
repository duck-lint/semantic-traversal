# semantic-traversal

`semantic-traversal` is a local conversational runtime over persisted thread state and ingested note chunks. Each user turn preserves the raw message, runs bounded additive semantic extraction, prepares lexical retrieval from SQLite, calls the final LLM, updates thread state, and appends a hash-chained ledger record.

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
6. Retrieves matching chunks from the ingestion SQLite database when available.
7. Builds a synthesis context packet that keeps the raw user input authoritative.
8. Calls the LLM backend.
9. Saves the next thread state, state delta, turn artifacts, and hash-chained ledger record.

The runtime still does not use embeddings, vector search, graph traversal, or synthetic node promotion.

## Semantic Extraction Modes

Turn execution supports these semantic extractor modes:

- `disabled`: persist explicit disabled extraction artifacts and continue through the lexical path
- `stub`: deterministic local extractor for tests and probes
- `ollama`: optional local Ollama JSON extractor
- `auto`: use Ollama only when a semantic extractor model is configured, otherwise behave like `disabled`

Environment variables and matching CLI overrides:

- `SEMANTIC_EXTRACTOR_MODE=disabled|stub|ollama|auto`
- `SEMANTIC_EXTRACTOR_MODEL=<model name>`
- `SEMANTIC_EXTRACTOR_BASE_URL=http://localhost:11434`

Semantic extraction is additive only. It does not rewrite or replace the raw user input, and it does not become the retrieval gatekeeper.

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

## Status Meanings

Retrieval coverage still uses clear lexical statuses:

- `minimal_pass`: retrieval found matching chunks
- `no_index`: the ingestion SQLite database is not present
- `no_query_terms`: neither raw lexical extraction nor additive extraction hints produced candidate retrieval terms
- `no_matches`: candidate terms existed but nothing matched
- `not_attempted`: reserved for future intentional skips

Semantic extraction uses explicit statuses per pass:

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

Run a stub turn with stub semantic extraction:

```powershell
python -m semantic_traversal --message "Hello from stub mode." --llm-mode stub --semantic-extractor-mode stub
```

Run a stub turn with lexical retrieval and additive extraction:

```powershell
python -m semantic_traversal --message "Please retrieve the candy snack food before bed note." --llm-mode stub --semantic-extractor-mode stub
```

Run a disabled-extraction turn that still uses lexical fallback:

```powershell
python -m semantic_traversal --message "Please retrieve the candy snack food before bed note." --llm-mode stub --semantic-extractor-mode disabled
```

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
python -m semantic_traversal.probes probe_semantic_extraction_disabled_fallback --repo-root . --data-root $env:TEMP\semantic-traversal-probes-extract-disabled
python -m semantic_traversal.probes probe_semantic_extraction_contextual_thread_state --repo-root . --data-root $env:TEMP\semantic-traversal-probes-extract-context
python -m semantic_traversal.probes probe_semantic_extraction_hash_integrity --repo-root . --data-root $env:TEMP\semantic-traversal-probes-extract-integrity
```

## How To Inspect A Turn

Useful files after a turn:

- `isolated_semantic_extraction_packet.json` for the isolated extraction request, status, metadata, and parsed payload
- `contextual_semantic_extraction_packet.json` for the contextual extraction request, including prior thread state
- `semantic_context_packet.json` for additive retrieval preparation and semantic extraction status
- `semantic_traversal_manifest.json` for retrieval mode and selected chunk IDs
- `retrieval_packet.json` for raw lexical terms, extraction hint terms, candidate term sources, and returned chunks
- `coverage_report.json` for retrieval and semantic extraction status
- `synthesis_context_packet.json` for the exact context sent to the final LLM
- `state_delta.json` for the persisted state transition
- `thread_ledger.jsonl` for the hash chain across turns

## Human UAT Focus

The current next end goal is human UAT over additive semantic extraction and retrieval interaction.

Good break attempts:

- confirm the raw user message is unchanged across extraction and synthesis artifacts
- run with `--semantic-extractor-mode disabled` and confirm lexical retrieval still works
- inspect `candidate_term_sources` and verify raw lexical terms are not dropped when extraction is sparse
- run two turns on the same thread and confirm the contextual extraction request includes prior thread state
- compare ledger hashes to the persisted artifact contents on disk

## Notes

- Live final-answer mode still requires `OPENAI_API_KEY`.
- Ollama is optional and is not required for tests or acceptance.
- Completed implementation bundles live under `agent_harness/implementation-projects/archive/`.
