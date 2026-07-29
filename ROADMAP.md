# Roadmap

Semantic Traversal is an operational runtime and an active research project. This roadmap describes the current direction without promising dates or treating experimental seams as settled product commitments.

## Current baseline

The present stable kernel includes:

- staged ingest and index activation;
- exact, lexical, vector, graph, and temporal retrieval;
- deterministic bounded selection;
- runtime-owned limits, scope, claim policy, and coverage;
- persisted inventory and turn artifacts;
- frontier synthesis over approved evidence;
- an Obsidian desktop interface;
- a substantial automated test suite and real-corpus UAT history.

The next major work is **Implementation 08: control plane and response-mode routing**.

## Implementation 08 — control plane

The goal is to stop treating every user message as a retrieval request.

A new control layer will decide whether a turn should be answered from conversation context alone or should execute a corpus traversal plan. The control model will classify and plan; the existing frontier synthesizer will still write the response.

The high-level seams are:

### 1. Contract and branch baseline

Freeze the accepted runtime, prompt, packet, and configuration contracts before introducing the new control path.

### 2. Response-mode schema

Define canonical response modes and their diagnostics. Initial modes are expected to distinguish direct conversation, traversal-backed synthesis, and blocked or unsupported execution.

### 3. Unified control-model call

Use one control-model call to emit response mode and, when traversal is required, the retrieval plan. Avoid a redundant router call followed by a second planner call.

### 4. Direct-answer path

For direct mode, bypass corpus retrieval but retain thread state, artifacts, and the existing frontier synthesizer. The control model does not answer the user.

### 5. Traversal path integration

Bind the control-model plan into the existing runtime without weakening YAML authority, operator contracts, evidence selection, or coverage.

### 6. Uncertainty and conservative routing

Establish explicit behavior for ambiguous turns. Routing uncertainty should not silently manufacture corpus authority or bypass required evidence.

### 7. Continuation and context policy

Handle referential continuation, topic changes, and conversation context so direct and traversal modes do not contaminate one another.

### 8. Planner-model bakeoff

Compare candidate control/planner models under the same canonical schema and runtime:

- current local `qwen3:8b`;
- a larger local model such as `qwen3:14b`;
- a frontier API model at low reasoning effort.

The purpose is to measure planning quality, latency, and cost. The runtime contract—not the selected model—is the product boundary.

### 9. Diagnostics, UAT, and closeout

Add mode-specific artifacts, regression tests, live UAT, deterministic fixtures, migration notes, and an explicit implementation closeout.

The seam labels may tighten during implementation, but the authority split is fixed: control models classify and request; runtime policy binds and executes; the frontier model synthesizes.

## Work after Implementation 08

### Runtime contracts and correctness

- continue hardening canonicalization and schema validation;
- keep prompt and packet contracts versioned and regression-tested;
- improve migration diagnostics for stale indexes and artifacts;
- expand failure-mode and recovery documentation;
- maintain deterministic evidence-boundary tests.

### Retrieval research and evaluation

- build reusable evaluation sets beyond one successful corpus question;
- compare planner models under identical runtime conditions;
- study fusion and breadth/depth behavior across query classes;
- measure temporal genealogy, relation traversal, and exact absence claims;
- develop falsifiable quality criteria rather than relying on answer vibes;
- evaluate cost, latency, and local/frontier model tradeoffs.

### Corpus and ingest tooling

- improve resource-inventory usefulness and semantic descriptions;
- refine chunking diagnostics and visual inspection;
- add safer corpus validation and schema guidance;
- improve metadata and relation authoring workflows;
- document corpus-design assumptions that materially affect retrieval.

### User interface

- improve the Obsidian inspection surface;
- make response mode, traversal, coverage, and evidence easier to understand;
- expose useful artifacts without dumping raw JSON on ordinary users;
- improve error and blocked-turn presentation;
- verify and repair plugin TypeScript/build hygiene;
- consider packaging only after the runtime contract is stable enough to support it.

### Distribution and operations

- verify macOS and Linux behavior;
- replace machine-specific example paths with a cleaner onboarding flow;
- add repeatable installation and release checks;
- decide whether the plugin should enter the Obsidian community-plugin process;
- publish versioned releases only when migration expectations are clear.

## Non-goals for now

The project is not currently pursuing:

- autonomous web research;
- multi-user hosting;
- a general-purpose agent framework;
- an unrestricted self-editing memory system;
- a plugin marketplace release before build and migration contracts are ready;
- broad pull-request-driven community development.

Issues, criticism, failure cases, and evaluation results are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).
