# Architecture

Semantic Traversal separates semantic interpretation, evidence execution, and natural-language synthesis into distinct authority domains.

## Turn lifecycle

```text
raw user message
  -> conversation and thread state
  -> semantic compiler packet
  -> runtime plan binding
  -> retrieval operators
  -> traversal manifest
  -> deterministic selection
  -> coverage audit
  -> synthesis context packet
  -> frontier response
  -> state delta and ledger record
```

The important distinction is not merely that several retrieval methods exist. It is that each stage has a different kind of authority.

## Authority boundaries

### Semantic compiler

The compiler interprets the user message and emits a structured request. It may propose:

- a semantic target;
- retrieval queries;
- operators;
- scopes;
- requiredness;
- graph depth;
- temporal modes and anchors;
- bounded limits;
- known limitations.

The compiler does not answer the user and does not decide whether its requests are executable or adequately covered.

### Runtime

The runtime is authoritative over:

- allowed operators;
- scope binding;
- limits and budgets;
- graph depth clamping;
- candidate admission;
- diversity and fusion;
- required-layer preservation;
- evidence selection;
- claim and negative-claim policy;
- coverage;
- artifact persistence.

Compiler requests are inputs to this policy, not overrides of it.

### Frontier synthesizer

The frontier model is authoritative only over the final natural-language composition. It receives conversation context and, when traversal succeeds, a bounded runtime-approved evidence packet.

It does not receive unrestricted corpus access and does not independently decide which retrieved material counts as evidence.

## Corpus substrate

The runtime projects a Markdown vault into several related representations:

- note records with stable UUID identity;
- semantic chunks;
- FTS5 lexical indexes;
- embedding vectors with persisted identity;
- canonical wikilink adjacency;
- typed temporal anchors;
- bounded inventory summaries.

These are complementary retrieval surfaces over one corpus, not separate knowledge bases.

## Retrieval operators

### Exact search

Exact search supports literal terms, field-aware context, exhaustive counting, and a narrow negative-claim contract. A zero-match result permits an absence claim only when execution was exhaustive under the configured scope and runtime policy.

### Lexical search

FTS5 supplies high-recall lexical candidates. Index validity and alignment are checked during staged ingest.

### Vector search

Vector retrieval supports multiple semantic queries, validates embedding identity, applies configured similarity thresholds, and uses per-query and per-note caps to prevent one query or note from dominating selection.

### Graph expansion

Graph retrieval traverses canonical note adjacency derived from wikilinks. Selected evidence retains seed, path, direction, and hop provenance.

### Temporal retrieval

Temporal retrieval uses normalized typed anchors rather than relying on filenames or prose order alone. The current modes include earliest, latest, before, after, between, and ordered.

## Candidate fusion and selection

Operator results are merged without treating every signal as commensurable in one opaque score.

The current selection policy uses deterministic ordinal evidence and breadth-before-depth behavior, while preserving required operator contributions inside configured packet bounds.

Selection remains a runtime policy surface because it controls what the final model is allowed to treat as evidence.

## Coverage

Coverage is an execution and provenance contract.

It asks whether:

- requested required operators executed successfully;
- those operators produced contractually adequate contribution;
- selected evidence represents required surfaces;
- negative claims are authorized;
- the retrieval plan was executable;
- the evidence packet is non-empty when the claim requires evidence.

Coverage approval does not prove that a selected passage logically entails every sentence the synthesizer might write. It establishes that the system executed the requested evidence process and passed the configured admission boundary.

## Persistence and audit

A turn produces a set of inspectable artifacts rather than only a final answer. The thread ledger links turn records by hash so later inspection can establish sequence and detect accidental mutation.

This persistence is intended for debugging, evaluation, and epistemic accountability—not blockchain theatre.

## Configuration as contract

Runtime policy lives in `semantic_traversal.runtime.yaml`. Centralizing these settings prevents model prompts, CLI flags, and hidden literals from becoming competing authority surfaces.

The configuration includes paths, model identities, retrieval controls, scope aliases, limits, temporal policy, graph policy, artifact names, and prompts.

## Current architectural frontier

The retrieval and evidence kernel is operational. The next major architectural seam is a control plane above the compiler.

That control plane will distinguish:

- direct conversation that does not require corpus evidence;
- traversal-backed responses;
- blocked or unsupported requests.

The control model may emit both response mode and, when required, a traversal plan. It will not itself produce the answer.

See [ROADMAP.md](ROADMAP.md).
