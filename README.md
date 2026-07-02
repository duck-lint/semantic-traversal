# semantic-traversal

`semantic-traversal` is a local conversational runtime over persisted thread state and ingested note chunks. Each user turn preserves the raw message, compiles a canonical `semantic_compiler_packet`, activates retrieval surfaces, builds a traversal manifest, evaluates coverage, optionally synthesizes through the frontier LLM boundary, updates thread state, and appends a hash-chained ledger record.

The current runtime keeps three practical layers visible:

- canonical semantic compiler packets
- SQLite-backed lexical, vector, graph, and primary-corpus activation surfaces
- inspectable per-turn artifacts and ledger hashes

## Runtime Shape

For each user message, the runtime:

1. Loads prior `thread_state` and `conversation_thread` artifacts if they exist.
2. Preserves the raw user input unchanged.
3. Sends the raw input plus compact prior thread state to the configured local semantic compiler.
4. Canonicalizes the compiler response into `semantic_compiler_packet`.
5. Activates lexical, vector, graph, and primary-corpus surfaces from the ingestion database when available.
6. Builds `semantic_traversal_manifest` from activated candidate regions.
7. Assembles `retrieval_packet` only from traversal-selected chunk IDs.
8. Evaluates coverage as `approved` or `blocked`.
9. Builds `synthesis_context_packet` with raw input, prior thread state, compiler packet, traversal manifest, coverage report, and approved retrieval only when runtime gating permits it.
10. Calls the frontier LLM backend only when the runtime outcome is `completed`.
11. Saves the next thread state, state delta, turn artifacts, and hash-chained ledger record.

## Config And Secrets

Checked-in runtime authority lives in [`semantic_traversal.runtime.yaml`](semantic_traversal.runtime.yaml).

Use that YAML for runtime paths, provider/model/base URLs, traversal knobs, storage names, and index table names.

Use `.env.local` only for secrets such as `OPENAI_API_KEY`. The YAML must not contain API keys or credentials.

Install dependencies before running vector-enabled runtime paths:

```powershell
pip install -r requirements.txt
```

## Artifact Layout

Default runtime artifacts live under the repo-local configured data root:

- thread data root: `.semantic-traversal-data`
- probe data root: `$env:TEMP\semantic-traversal-probes`

Per turn, the runtime writes:

- `semantic_compiler_packet.json`
- `semantic_traversal_manifest.json`
- `retrieval_packet.json`
- `coverage_report.json`
- `synthesis_context_packet.json`
- `state_delta.json`

The ledger records hashes for those persisted turn artifacts, the conversation thread, and the next thread state.

## Runtime Decisions

Normal runtime execution is binary:

- `completed`
- `blocked`

Coverage uses `decision=approved` or `decision=blocked`. Blocked turns may still persist diagnostic observations, but those observations do not approve synthesis.

Semantic compiler statuses are:

- `parsed`
- `unavailable`
- `invalid_json`
- `fallback`

Only `parsed` compiler output may pass normal runtime coverage. Fallback packets are diagnostic scaffolding for artifact inspection; they do not authorize synthesis.

## Quick Start

Run the test suite:

```powershell
python -m unittest discover -s tests -v
```

Build or refresh the local ingestion database:

```powershell
python -m semantic_traversal ingest --repo-root .
```

Run a normal CLI turn:

```powershell
python -m semantic_traversal --message "Please retrieve the candy snack food before bed note." --repo-root .
```

A real turn requires a configured semantic compiler and, for completed synthesis, a configured frontier LLM key. Missing compiler or LLM configuration blocks the runtime rather than substituting a local pretend answer.

## Probe Commands

```powershell
python -m semantic_traversal.probes new-thread --data-root $env:TEMP\semantic-traversal-probes-new
python -m semantic_traversal.probes continue-thread --data-root $env:TEMP\semantic-traversal-probes-continuation
python -m semantic_traversal.probes fixture-lexical-hit --data-root $env:TEMP\semantic-traversal-probes-fixture
```

## How To Inspect A Turn

Useful files after a turn:

- `semantic_compiler_packet.json` for the canonical compiler packet
- `semantic_traversal_manifest.json` for activation surfaces, graph traversal notes, candidate counts, and selected chunk IDs
- `retrieval_packet.json` for traversal-selected chunks and retrieval provenance
- `coverage_report.json` for binary approval-vs-blocked gating plus blocking reasons
- `synthesis_context_packet.json` for the exact context that would reach the final LLM when the runtime is completed
- `state_delta.json` for the persisted state transition
- `thread_ledger.jsonl` for the hash chain across turns

## Human UAT Focus

Good break attempts:

- confirm the raw user message is unchanged across compiler and synthesis artifacts
- confirm the normal CLI blocks when no real semantic compiler is configured
- confirm diagnostic fallback packets cannot produce `completed`
- inspect traversal notes and verify raw lexical terms are not dropped when compiler extraction is sparse
- confirm `approved_retrieval_packet` appears only when runtime gating permits synthesis
- run two turns on the same thread and confirm the compiler request receives prior thread state
- compare ledger hashes to the persisted artifact contents on disk

## Notes

- Live final-answer mode requires `OPENAI_API_KEY`.
- Semantic compiler calls use the configured local Ollama backend.
- Vector activation uses the configured Sentence Transformers backend by default.
- Missing embeddings block the runtime rather than falling back to a softer completion mode.
