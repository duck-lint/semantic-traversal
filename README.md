# semantic-traversal

`semantic-traversal` is a local conversational runtime over persisted thread state and ingested note chunks. Each user turn preserves the raw message, performs semantic extraction, activates retrieval surfaces, builds a traversal manifest, evaluates coverage, optionally synthesizes through the LLM boundary, updates thread state, and appends a hash-chained ledger record.

The current runtime keeps three practical layers visible:

- additive semantic extraction packets
- SQLite-backed lexical, vector, graph, and primary-corpus activation surfaces
- inspectable per-turn artifacts and ledger hashes

## What A Turn Does

For each user message, the runtime:

1. Loads prior `thread_state` and `conversation_thread` artifacts if they exist.
2. Preserves the raw user input unchanged.
3. Runs isolated semantic extraction from the raw message.
4. Runs contextual semantic extraction using the raw message, prior thread state, and the isolated extraction.
5. Builds additive retrieval preparation from raw lexical terms plus semantic extraction hints.
6. Activates lexical, primary-corpus, vector, graph, and optional synthetic-node surfaces from the ingestion database when available and configured.
7. Builds a semantic traversal manifest from activated candidate regions.
8. Assembles a retrieval packet only from traversal-selected chunk IDs.
9. Evaluates coverage as `approved` or `blocked`.
10. Builds a synthesis context packet that keeps the raw user input authoritative.
11. Calls the LLM backend only when the runtime outcome is `completed`.
12. Saves the next thread state, state delta, turn artifacts, and hash-chained ledger record.

Synthetic node promotion is still deferred.

## Config And Secrets

Checked-in runtime authority lives in [`semantic_traversal.runtime.yaml`](semantic_traversal.runtime.yaml).

Use that YAML for:

- runtime paths
- surface requirements
- provider/model/base URLs
- coverage limits and thresholds

Use `.env.local` only for secrets such as `OPENAI_API_KEY`.

The YAML must not contain API keys or credentials.

Install the declared dependencies before running vector-enabled runtime paths:

```powershell
pip install -r requirements.txt
```

`requirements.txt` declares the runtime/test dependencies used by the checked-in config and embedding stack, including `PyYAML` and `sentence-transformers`.

Normal turn execution does not expose semantic extractor mode selection on the CLI. Disabled and stub semantic extractors remain test-only or probe-only.

## Artifact Layout

Default runtime artifacts live under the repo-local configured data root:

- thread data root: `.semantic-traversal-data`
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

Coverage uses `decision=approved` or `decision=blocked`. Blocked turns may still persist diagnostic observations, but those observations do not approve synthesis.

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

Run a normal CLI turn:

```powershell
python -m semantic_traversal --message "Please retrieve the candy snack food before bed note." --llm-mode stub --repo-root .
```

Without a configured real semantic extractor, that command emits blocked diagnostic artifacts and exits non-zero.

## Ingest The Notes

Build or refresh the local ingestion database:

```powershell
python -m semantic_traversal ingest --repo-root .
```

That command reads the configured corpus roots, stores notes/chunks plus graph/vector surfaces in SQLite, and writes a JSON manifest for inspection.

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
- `semantic_traversal_manifest.json` for activation surfaces, candidate regions, and selected chunk IDs
- `retrieval_packet.json` for traversal-selected chunks and retrieval provenance
- `coverage_report.json` for binary approval-vs-blocked gating plus blocking reasons
- `synthesis_context_packet.json` for the exact context that would reach the final LLM when the runtime is approved
- `state_delta.json` for the persisted state transition
- `thread_ledger.jsonl` for the hash chain across turns

## Human UAT Focus

The current focus is tightening the existing activation/traversal slice and expanding the remaining deferred surfaces, especially synthetic node promotion and broader coverage heuristics.

Good break attempts:

- confirm the raw user message is unchanged across extraction and synthesis artifacts
- confirm the normal CLI blocks when no real semantic extractor is configured
- run the stub and disabled diagnostic probes and confirm they stay blocked while still persisting extraction and traversal artifacts
- inspect `candidate_term_sources` and verify raw lexical terms are not dropped when extraction is sparse
- confirm `approved_retrieval_packet` appears only when coverage is approved
- run two turns on the same thread and confirm the contextual extraction request includes prior thread state
- compare ledger hashes to the persisted artifact contents on disk

## Notes

- Live final-answer mode still requires `OPENAI_API_KEY`.
- Semantic extraction currently uses the configured Ollama backend.
- Vector activation uses the configured Sentence Transformers backend by default.
- Missing embeddings block the runtime rather than falling back to a softer completion mode.
- Completed implementation bundles live under `agent_harness/implementation-projects/archive/`.
