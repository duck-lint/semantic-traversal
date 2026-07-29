# Semantic Traversal

**A local-first semantic runtime for reasoning over a structured Markdown corpus.**

Semantic Traversal is both a usable developer tool and an active research project.

It begins from a simple architectural claim: a language model should not be responsible for deciding what it knows, searching an entire corpus, validating its own evidence, and then writing the answer in one opaque step.

Instead, Semantic Traversal separates those responsibilities.

```text
user message
  -> semantic compilation
  -> runtime-owned retrieval plan
  -> lexical / vector / graph / temporal execution
  -> deterministic selection and coverage audit
  -> bounded evidence packet
  -> frontier-model synthesis
```

The frontier model writes the response. The runtime decides what evidence is admissible.

That separation turns retrieval from a hidden prompt-building trick into an inspectable computational process.

> **Project status:** the current runtime is operational and used against a real Obsidian vault, but the architecture is still evolving. Implementation 08 will add a control plane that distinguishes direct conversation from corpus traversal and improves plan generation. See [ROADMAP.md](ROADMAP.md).

<p align="center">
  <img width="1100" alt="Semantic Traversal Obsidian interface" src="https://github.com/user-attachments/assets/0f073f53-1d34-4f33-85b3-3855ea43961f" />
</p>

## What problem it addresses

Most retrieval-augmented generation systems collapse several distinct questions:

1. What is the user asking?
2. Does the answer require corpus evidence?
3. Which retrieval operations should run?
4. Which results are relevant enough to admit?
5. Is the available evidence sufficient for the requested claim?
6. What context should the answering model actually receive?

When these questions are handled inside one model call, failures are difficult to locate. A plausible answer can conceal weak retrieval, missing evidence, invalid scope, stale embeddings, or a plan that was never executed as requested.

Semantic Traversal makes those boundaries explicit.

For every turn, the runtime preserves the semantic plan, operator outcomes, selected evidence, provenance, coverage decision, synthesis context, state transition, and hash-chained ledger record. A failed boundary blocks the turn rather than silently degrading into an ungrounded answer.

## The conceptual model

Semantic Traversal does not treat a note vault as a bag of text waiting to be embedded.

It treats the corpus as a structured semantic substrate:

- **notes** are stable corpus entities;
- **chunks** are bounded textual projections of those entities;
- **wikilinks** are explicit graph relations;
- **frontmatter** is typed metadata used for filtering and scope;
- **temporal anchors** represent dates and ordered relations;
- **embeddings and lexical indexes** are retrieval surfaces, not sources of truth;
- **runtime artifacts** record how a response was produced.

The system retrieves across several surfaces, reconciles them under runtime-owned contracts, and exposes the final evidence boundary to the synthesis model.

This is closer to a semantic compiler and execution runtime than a conventional chatbot with “memory.”

## Why the architecture is different

### Models request; the runtime governs

The semantic compiler can request operators, scopes, limits, depth, and requiredness. It cannot override configured runtime policy.

YAML owns:

- allowed operators and scope aliases;
- retrieval limits and graph depth bounds;
- selection and diversity policy;
- required-layer preservation;
- negative-claim permission;
- model and prompt configuration;
- storage and artifact paths.

Accepted-but-unsupported requests produce diagnostics. Required requests that cannot be executed block completion.

### Evidence is narrower than retrieval

Candidate results, corpus inventory, compiler queries, aliases, counts, and diagnostics may help the runtime decide what to do, but they are not automatically answer evidence.

The frontier model receives:

- a bounded traversal summary; and
- the runtime-approved retrieval packet.

Unselected candidates and descriptive inventory data do not cross the evidence boundary.

### Failure is represented, not hidden

Normal turn outcomes are intentionally binary:

- `completed` — the runtime contract was satisfied and synthesis was allowed;
- `blocked` — a required surface was missing, invalid, failed, or inadequately represented.

Blocked turns still persist diagnostics and an assistant-facing explanation. They do not call the frontier model as a fallback.

### Retrieval remains auditable

Each selected chunk retains source-layer and traversal provenance. Exact, lexical, vector, graph, and temporal execution all expose structured diagnostics rather than collapsing into one relevance score.

For a denser description of the authority model and packet boundaries, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Current capabilities

The current runtime includes:

- semantic chunking for Markdown notes;
- required UUID-backed note identity;
- local Sentence Transformers embeddings;
- SQLite persistence and FTS5 lexical search;
- exact phrase search with exhaustive absence contracts;
- multi-query vector retrieval with identity validation and diversity controls;
- graph expansion over canonical wikilink adjacency;
- typed temporal retrieval with earliest, latest, before, after, between, and ordered modes;
- deterministic breadth-oriented fusion and bounded selection;
- persisted corpus inventory for planner context;
- required-layer adequacy checks and coverage gating;
- thread continuation, state deltas, and a hash-chained turn ledger;
- an Obsidian desktop interface over the external Python runtime.

## What it is not

Semantic Traversal is not:

- an autonomous agent that searches until it feels satisfied;
- a model-managed memory bank;
- a hosted service;
- a polished consumer application;
- a claim that retrieval coverage proves semantic entailment;
- a replacement for careful corpus design.

The quality of the substrate still matters. Stable note identity, useful prose structure, metadata discipline, and meaningful relations all affect what can be retrieved and interpreted.

## Requirements and platform status

- Python 3.11 or newer
- Ollama and a configured local compiler model
- Sentence Transformers
- an OpenAI API key for frontier synthesis
- Obsidian desktop, when using the plugin

The project is **tested on Windows**. The Python runtime is designed to be portable, but other operating systems have not yet been verified as supported environments.

## Installation

Install the Python dependencies:

```powershell
python -m pip install -r requirements.txt
python -m pip install openai
```

Start Ollama and pull the compiler model configured in `semantic_traversal.runtime.yaml`. The checked-in example currently uses `qwen3:8b`:

```powershell
ollama serve
ollama pull qwen3:8b
```

Store secrets in `.env.local`, not in YAML:

```text
OPENAI_API_KEY=your-key-here
```

The configuration loader rejects API keys and other credential-shaped values in the runtime config.

## Configuration

The runtime reads [`semantic_traversal.runtime.yaml`](semantic_traversal.runtime.yaml) by default. Use `--config` to supply a different file.

Before running the project, update:

- `paths.vault_root` — the Markdown vault to ingest;
- `paths.data_root` — the SQLite, manifest, thread, and artifact location.

Other major sections configure the semantic compiler, embeddings, frontier model, retrieval operators, graph traversal, temporal retrieval, storage, and prompts.

The checked-in file is an example operating configuration, not a universal default.

## Quick start

Run the test suite:

```powershell
python -m unittest discover -s tests -v
```

Ingest the configured vault:

```powershell
python -m semantic_traversal ingest --repo-root .
```

Run a turn:

```powershell
python -m semantic_traversal `
  --message "Where did this idea develop from?" `
  --repo-root .
```

Continue the returned thread:

```powershell
python -m semantic_traversal `
  --message "Compare that with the earlier journal entries." `
  --thread-id THREAD_ID `
  --repo-root .
```

The CLI prints a JSON summary with the thread ID, runtime outcome, coverage decision, model metadata, and artifact paths. Blocked turns exit non-zero so their persisted diagnostics can be inspected.

## Obsidian plugin

The plugin is a desktop interface over the external Python runtime. It does not replace YAML configuration and does not ingest the vault automatically.

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

Configure the Python executable, runtime root, YAML path, and thread artifact root in the plugin settings.

The current interface supports conversation threads, runtime execution, ingestion, and inspection of turn-level coverage and diagnostics. It is functional but still an early interface rather than a packaged community plugin.

## Artifacts and inspection

Under the configured data root:

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
                └── state_delta.json
```

The most useful inspection points are:

| Artifact | Meaning |
| --- | --- |
| `semantic_compiler_packet.json` | Canonical semantic target and requested retrieval plan |
| `semantic_traversal_manifest.json` | Operator execution, candidates, traversal, selection, and provenance |
| `retrieval_packet.json` | Concrete selected evidence |
| `coverage_report.json` | Approval or blocking decision and gaps |
| `synthesis_context_packet.json` | Exact bounded context sent to the frontier model |
| `state_delta.json` | Persisted thread transition |
| `thread_ledger.jsonl` | Hash-chained audit records across turns |

## Operational notes

Every ingested note must contain the configured UUID field, `uuid` by default. Missing, malformed, or duplicate UUIDs fail ingest rather than being generated or skipped.

Reingest after corpus content, metadata, wikilinks, embedding identity, or temporal projection inputs change. A plugin restart or prompt-only change does not require reingest.

Ingest is staged. A failed staged build should preserve the previous active database. Inspect `ingestion/manifests/latest.json` before attempting recovery.

## Roadmap and project status

The present system has a stable retrieval and evidence kernel, but it is not “finished.”

The next major implementation introduces a control plane that decides whether a turn should be answered directly from conversation context or routed through corpus traversal. It will also separate response-mode policy from retrieval execution and compare planner models without changing the underlying runtime contract.

Longer-term work includes compiler research, retrieval evaluation, corpus tooling, UI refinement, packaging, and cross-platform verification.

See [ROADMAP.md](ROADMAP.md) for the current public plan.

## Feedback and contributions

Issues, bug reports, design criticism, and reproducible evaluation results are welcome.

The project is not currently soliciting pull requests or shared maintenance. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening an issue.

Security-sensitive reports should not be filed publicly. See [SECURITY.md](SECURITY.md).

## Author and license

Created by **Madison (`duck-lint`)**.

Licensed under the [Apache License 2.0](LICENSE).
