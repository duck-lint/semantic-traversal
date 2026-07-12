# semantic-traversal

`semantic-traversal` is a local semantic context compiler for conversations over an Obsidian-style Markdown vault.

The runtime owns the path from a user utterance to usable context:

```text
user utterance
  -> semantic compiler
  -> retrieval-plan binding
  -> lexical / vector / graph activation
  -> traversal manifest
  -> provenance-aware retrieval
  -> coverage audit
  -> frontier-model synthesis
```

<img width="2560" height="1439" alt="image" src="https://github.com/user-attachments/assets/0f073f53-1d34-4f33-85b3-3855ea43961f" />


The frontier model writes the final response. It does not choose what to retrieve, traverse the corpus, or decide whether evidence is valid.

This repository is local-first and deliberately inspectable. A turn leaves behind the compiler packet, traversal decisions, retrieved evidence, coverage decision, state transition, and hash-chained ledger record.

## Current shape

There are three practical parts:

- **Python runtime** — ingestion, thread state, semantic compilation, retrieval, traversal, coverage gating, synthesis, and artifact persistence.
- **SQLite latent space** — ingested note chunks, embeddings, graph nodes, and wikilink edges.
- **Obsidian plugin** — a desktop-only chat and inspection surface over the external Python runtime.

The runtime is not an autonomous RAG agent. Retrieval and evidence validity stay on the runtime side of the boundary.

## Runtime contract

For each turn, the runtime:

1. Preserves the raw user message.
2. Loads the prior thread state and ledger parent.
3. Sends the message and compact thread context to the configured semantic compiler.
4. Canonicalizes the compiler response into `semantic_compiler_packet`.
5. Binds soft scope requests to observed corpus metadata.
6. Activates available lexical, vector, graph, and primary-corpus surfaces.
7. Builds a `semantic_traversal_manifest` from those activation results.
8. Materializes `retrieval_packet` only from traversal-selected chunks.
9. Audits retrieval and provenance against the packet's coverage policy.
10. Calls the frontier model only when coverage approves synthesis.
11. Persists the next thread state, state delta, artifacts, and ledger record.

Normal runtime outcomes are intentionally binary:

- `completed` — the runtime contract was satisfied and synthesis was allowed.
- `blocked` — a required boundary was unavailable, invalid, under-covered, or failed.

Blocked turns still preserve diagnostics and an assistant-facing explanation. They do not call the frontier model as a fallback.

## Corpus model

The ingest path treats the vault as Markdown notes and chunks their substantive text into the local SQLite store.

- Notes are corpus nodes.
- Wikilinks are graph edges.
- Frontmatter is metadata used for filtering and scope binding; it is not a graph-node layer.
- Each note requires the configured UUID field, `uuid` by default.
- The ingestion manifest records inserted, updated, unchanged, and deleted content.

The checked-in runtime config is the authority for paths, models, retrieval limits, storage names, and prompts. Change the vault path before using the example config in a different environment.

## Requirements

- Python 3.11 or newer
- Ollama for the local semantic compiler
- An Ollama model matching `semantic_compiler.model` in the YAML
- Sentence Transformers for local embeddings
- An OpenAI API key for completed frontier synthesis
- Obsidian desktop only, if using the plugin

Install the Python dependencies:

```powershell
python -m pip install -r requirements.txt
python -m pip install openai
```

Start Ollama and install the configured compiler model. The exact model is configuration-controlled; the checked-in example currently uses `qwen3:8b`:

```powershell
ollama serve
ollama pull qwen3:8b
```

Put secrets in `.env.local`, not in `semantic_traversal.runtime.yaml`:

```text
OPENAI_API_KEY=your-key-here
```

The config loader rejects API keys and other credential-shaped values in YAML.

## Configuration

The runtime reads [`semantic_traversal.runtime.yaml`](semantic_traversal.runtime.yaml) by default. Use `--config` to supply another YAML file.

The important settings are:

- `paths.vault_root` — the Markdown vault to ingest.
- `paths.data_root` — where SQLite, manifests, thread state, and turn artifacts live. Relative paths resolve under `vault_root`.
- `semantic_compiler` — local Ollama provider, model, URL, and timeout.
- `embeddings` — local Sentence Transformers provider and model.
- `llm` — frontier model, reasoning effort, output limit, and prompt-cache breakpoint controls. Cache enablement, key rotation, and retention belong in YAML so the runtime has one authoritative configuration surface.
- `retrieval`, `graph_traversal`, and `storage` — activation limits and artifact names.
- `prompts` — compiler and synthesis instructions.

The repository's example config points at one local vault path. Treat that as a template and update `paths.vault_root` for your machine.

## Quick start

Run the test suite:

```powershell
python -m unittest discover -s tests -v
```

Ingest the configured vault:

```powershell
python -m semantic_traversal ingest --repo-root .
```

Run a turn from the CLI:

```powershell
python -m semantic_traversal `
  --message "Retrieve the note about candy snack food before bed." `
  --repo-root .
```

Continue an existing thread by passing its returned ID:

```powershell
python -m semantic_traversal `
  --message "Compare that with the sleep discussion." `
  --thread-id THREAD_ID `
  --repo-root .
```

The CLI prints a JSON summary containing the thread ID, runtime outcome, coverage decision, model metadata, and artifact paths. A blocked turn exits non-zero; inspect its persisted artifacts before changing configuration or retrying.

## Obsidian plugin

The plugin is a desktop-only V1 interface over the external Python runtime. It does not replace the YAML configuration and does not ingest the vault automatically.

Build it from `obsidian-plugin`:

```powershell
cd obsidian-plugin
npm install
npm run build
```

Copy `main.js`, `manifest.json`, and `styles.css` into:

```text
<vault>/.obsidian/plugins/semantic-traversal/
```

Configure these plugin settings:

- **Python executable** — normally `python`.
- **Runtime root** — the directory containing the `semantic_traversal` package.
- **Runtime config path** — the YAML file used by the Python runtime.
- **Thread artifact root** — the configured data root containing `threads/`.

The plugin renders persisted conversation messages as Agent responses, exposes thread continuity, provides an ingest command, and shows runtime/coverage diagnostics for individual turns.

## Artifact layout

Under the configured data root, the runtime stores ingestion data and thread artifacts:

```text
<data-root>/
├── ingestion/
│   ├── latent_space.sqlite3
│   └── manifests/latest.json
└── threads/
    └── <thread-id>/
        ├── conversation_thread.json
        ├── thread_state.json
        ├── thread_ledger.jsonl
        └── turns/
            └── turn-000001/
                ├── semantic_compiler_packet.json
                ├── semantic_compiler_diagnostic.json
                ├── semantic_traversal_manifest.json
                ├── retrieval_packet.json
                ├── coverage_report.json
                ├── synthesis_context_packet.json
                ├── state_delta.json
                └── ...
```

The most useful turn artifacts are:

| Artifact | Meaning |
| --- | --- |
| `semantic_compiler_packet.json` | Canonical semantic target, retrieval plan, coverage policy, and limitations. |
| `semantic_compiler_diagnostic.json` | Backend status, diagnostics, metadata, and capped raw-response preview. |
| `semantic_traversal_manifest.json` | Activation surfaces, candidate regions, graph expansion, and selection provenance. |
| `retrieval_packet.json` | Concrete selected chunks and their source provenance. |
| `coverage_report.json` | `approved` or `blocked`, with blocking and diagnostic gaps. |
| `synthesis_context_packet.json` | The exact bounded context sent to the frontier model when synthesis is allowed. |
| `state_delta.json` | The persisted thread transition for this turn. |
| `thread_ledger.jsonl` | Hash-chained audit records across turns. |

`synthesis_context_packet.json` is especially useful for checking what the frontier model actually received. A blocked turn must not contain approved retrieval for synthesis.

## Compiler and coverage statuses

Semantic compiler statuses include:

- `parsed` — valid compiler output was canonicalized.
- `unavailable` — the configured compiler could not be reached or was not configured.
- `invalid_json` — the backend returned a response that could not be accepted as JSON.
- `fallback` — diagnostic scaffolding was produced; it does not authorize normal synthesis.

Only valid parsed compiler output can pass normal runtime coverage. A lexical hit by itself is not proof that the semantic target was covered.

## Diagnostic probes

The probe runner exercises isolated behavior with fixture or probe backends. Probe success is not equivalent to a successful live runtime turn.

Examples:

```powershell
python -m semantic_traversal.probes new-thread `
  --data-root $env:TEMP\semantic-traversal-probes-new

python -m semantic_traversal.probes continue-thread `
  --data-root $env:TEMP\semantic-traversal-probes-continuation

python -m semantic_traversal.probes fixture-lexical-hit `
  --data-root $env:TEMP\semantic-traversal-probes-fixture
```

These are useful for artifact persistence, thread continuity, lexical retrieval, and blocked-runtime behavior. They do not prove that Ollama, Sentence Transformers, OpenAI, or the Obsidian plugin are working on a particular machine.

## Project boundaries

The project is intentionally conservative about authority:

- The semantic compiler plans; it does not answer the user.
- The runtime activates, traverses, retrieves, and audits evidence.
- The frontier model synthesizes from runtime-approved context.
- Coverage is a provenance/alignment gate, not a claim of perfect semantic entailment.
- Missing required runtime surfaces block completion rather than becoming a softer success mode.

The current implementation is still under active development around canonical semantic compilation and human UAT. The artifacts are designed to make weak evidence, missing surfaces, and blocked turns visible instead of hiding them behind a plausible answer.

## License and project status

No public license or release contract is declared yet. Treat this repository as an active local project rather than a packaged distribution.
