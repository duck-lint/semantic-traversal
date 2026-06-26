# semantic-traversal

`semantic-traversal` is a local conversational runtime. A user message becomes one bounded turn, the runtime reads prior thread state, optionally looks up relevant ingested notes from SQLite, calls the LLM, then saves the updated thread state and a hash-chained ledger record.

This repo now has three practical layers:

- turn runtime
- ingestion database
- human-readable artifacts for inspection

## What A Turn Does

For each user message, the runtime:

1. Loads the prior thread state if it exists.
2. Extracts simple lexical query terms from the user message.
3. Looks in the ingestion SQLite database for matching note chunks when an index is available.
4. Builds a synthesis context packet that includes the prior thread state and any approved retrieval material.
5. Calls the LLM backend.
6. Saves the next thread state.
7. Appends a ledger record that is hash-chained to the previous turn.
8. Writes inspectable JSON artifacts for the turn.

The retrieval path is intentionally boring and deterministic. It does not use embeddings, vector search, graph traversal, or synthetic node promotion.

## Artifact Layout

Default runtime artifacts live under the local temp directory:

- thread data root: `$env:TEMP\semantic-traversal`
- probe data root: `$env:TEMP\semantic-traversal-probes`

Per thread, the runtime writes:

- `threads/<thread_id>/conversation_thread.json`
- `threads/<thread_id>/thread_state.json`
- `threads/<thread_id>/thread_ledger.jsonl`
- `threads/<thread_id>/turns/turn-000001/semantic_context_packet.json`
- `threads/<thread_id>/turns/turn-000001/semantic_traversal_manifest.json`
- `threads/<thread_id>/turns/turn-000001/retrieval_packet.json`
- `threads/<thread_id>/turns/turn-000001/coverage_report.json`
- `threads/<thread_id>/turns/turn-000001/synthesis_context_packet.json`
- `threads/<thread_id>/turns/turn-000001/state_delta.json`

## Status Meanings

The coverage report uses a few clear statuses:

- `minimal_pass`: the SQLite index exists, query terms exist, and matching chunks were found
- `no_index`: the ingestion database is not present
- `no_query_terms`: the user message produced no usable lexical terms after filtering
- `no_matches`: the database exists, query terms exist, but nothing matched
- `not_attempted`: reserved for future intentional skips

## Quick Start

Run the test suite:

```powershell
python -m unittest discover -s tests -v
```

Run a stub turn:

```powershell
python -m semantic_traversal --message "Hello from stub mode." --llm-mode stub
```

Run a stub turn with retrieval enabled against the local ingestion database:

```powershell
python -m semantic_traversal --message "Please retrieve the candy snack food before bed note." --llm-mode stub
```

Run the named probes:

```powershell
python -m semantic_traversal.probes probe_new_thread_minimal_turn --llm-mode stub
python -m semantic_traversal.probes probe_same_thread_continuation_turn --llm-mode stub
python -m semantic_traversal.probes probe_lexical_retrieval_fixture_hit --repo-root . --data-root $env:TEMP\semantic-traversal-probes-hit
python -m semantic_traversal.probes probe_lexical_retrieval_no_index --repo-root . --data-root $env:TEMP\semantic-traversal-probes-noindex
python -m semantic_traversal.probes probe_lexical_retrieval_no_match --repo-root . --data-root $env:TEMP\semantic-traversal-probes-nomatch
python -m semantic_traversal.probes probe_lexical_retrieval_no_query_terms --repo-root . --data-root $env:TEMP\semantic-traversal-probes-noquery
python -m semantic_traversal.probes probe_ledger_hash_artifact_integrity --repo-root . --data-root $env:TEMP\semantic-traversal-probes-integrity
python -m semantic_traversal.probes probe_turn_cli_artifact_paths --repo-root . --data-root $env:TEMP\semantic-traversal-probes-cli
```

## Ingest The Notes

Build or refresh the local ingestion database:

```powershell
python -m semantic_traversal ingest --repo-root . --data-root $env:TEMP\semantic-traversal
```

That command reads the repository corpus and fixture notes, stores them in SQLite, and writes a JSON manifest for inspection.

## How Retrieval Works

The retrieval step is lexical, not semantic:

- the user message is split into simple lowercase word tokens
- short words and common stop words are ignored
- the runtime searches the ingested chunk text and metadata
- matching chunks are ranked deterministically
- the top few matches are placed into the retrieval packet

If there is no SQLite database, the turn still completes and reports `no_index`.
If there are no usable query terms, the turn still completes and reports `no_query_terms`.
If there are terms but nothing matches, the turn still completes and reports `no_matches`.

## How To Inspect A Turn

After a turn, inspect the saved files from the JSON payload printed by the CLI.

Typical useful files are:

- `semantic_context_packet.json` for the extracted query terms and prior thread context
- `semantic_traversal_manifest.json` for the retrieval mode and chosen chunk IDs
- `retrieval_packet.json` for the concrete chunk content returned to the turn
- `coverage_report.json` for the retrieval status
- `synthesis_context_packet.json` for the exact context sent to the LLM
- `state_delta.json` for the state transition saved with the turn
- `thread_ledger.jsonl` for the hash chain across turns

Example inspection commands:

```powershell
Get-Content "$env:TEMP\semantic-traversal\threads\<thread_id>\turns\turn-000001\coverage_report.json"
Get-Content "$env:TEMP\semantic-traversal\threads\<thread_id>\turns\turn-000001\retrieval_packet.json"
Get-Content "$env:TEMP\semantic-traversal\threads\<thread_id>\thread_ledger.jsonl"
```

## Human Acceptance Testing

The repo is ready for “try to break it” testing if:

- the CLI shows where each turn wrote its artifacts
- the probe commands pass
- the retrieval statuses are easy to distinguish
- the ledger hashes match the files on disk

Good break attempts:

- use an empty message and confirm `no_query_terms`
- delete the ingestion database and confirm `no_index`
- ask for unrelated words and confirm `no_matches`
- run two turns on the same thread and confirm the parent hash chain still holds

## Notes

- Live mode still works only when `OPENAI_API_KEY` is available.
- Stub mode is enough for the retrieval hardening and UAT prep flows.
- The active implementation bundles live in `agent_harness/implementation-projects/archive/` once complete.

