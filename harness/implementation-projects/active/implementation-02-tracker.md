# Implementation 02 Tracker

## Status

- State: proposed
- Current work: active `implementation-02` bundle wording now keeps seams 1 through 3 admissible with explicit `corpus_root` and `artifact_root` inputs, while future defaults and optional seams stay bundle-local
- Next action: implementer may start seam 1 using explicit `corpus_root` and `artifact_root` inputs only; optional graph or embedding follow-ons remain outside the current core seam

## Work Log

| Date | Agent Role | Change | Evidence | Next |
| --- | --- | --- | --- | --- |
| 2026-06-24 | planner | Created `implementation-02` active bundle for deterministic `primary_corpus` to `corpus_nodes` materialization, explicit chunking contract, core acceptance probes, and gated optional lexical or graph follow-ons | `harness/project-spec/project_spec_0.1.2.json`, `harness/README.md`, `harness/harness-runtime.md`, `harness/sub-agents.md`, `harness/archive-policy.md`, `harness/open-decisions.md`, archived `implementation-01` bundle and summary, `semantic_traversal/runtime.py`, `semantic_traversal/storage.py`, `semantic_traversal/cli.py`, `semantic_traversal/probes.py`, user-supplied PM admissibility report | reviewer checks plan truthfulness and gate posture before implementation starts |
| 2026-06-24 | implementer | Corrected active plan/tracker wording so seams 1 through 3 stay admissible with caller-supplied roots; downgraded future defaults, embeddings, and graph allowlist items to bundle-local unknowns or optional-seam gates | reviewer findings in current task context, `harness/open-decisions.md`, active `implementation-02` plan and tracker | orchestrator can hand seam 1 to implementation without changing `harness/open-decisions.md` |

## Work Status

| Work | Owner Agent | Status | Verification | Notes |
| --- | --- | --- | --- | --- |
| Create active `implementation-02` planning bundle | planner | complete | active plan and tracker exist under `harness/implementation-projects/active/` | bundle is scoped to deterministic ingestion only |
| Seam 1: deterministic note discovery and chunk contract | implementer | proposed | required acceptance probe: `probe_ingest_markdown_note_to_corpus_nodes` | caller must require explicit `corpus_root` and `artifact_root`; future live defaults are not required for this seam |
| Seam 2: core `corpus_nodes` materialization and ingest record | implementer | proposed | must prove deterministic ordered materialization without duplicate append behavior | exact filenames are not yet fixed, but artifact roles are fixed by the plan and do not depend on operator defaults |
| Seam 3: unchanged reingest and localized change verification | implementer | proposed | required acceptance probes: `probe_reingest_unchanged_corpus_preserves_chunk_ids` and `probe_changed_section_only_changes_affected_chunks` | localized-change probe must use a body-text edit under stable heading topology; optional graph and embeddings do not block this seam |
| Optional lexical follow-on after core seam pass | implementer | proposed | optional lexical probe required if this seam is attempted | must remain blocked until seams 1 through 3 pass |
| Optional initial graph projection after explicit allowlist decision | implementer | blocked | optional graph probe required if this seam is attempted | blocked on frontmatter allowlist authority; must stay Obsidian-native only |

## Bundle-Local Unknowns And Optional-Seam Gates

| Item | Applies To | Owner Agent | Current Posture |
| --- | --- | --- | --- |
| Future default corpus source for live operator use | later operator-facing seam only | orchestrator plus user | seams 1 through 3 proceed on explicit caller-supplied `corpus_root`; choose a default only if later work needs one |
| Future default latent-space artifact location | later operator-facing seam only | orchestrator plus user | seams 1 through 3 proceed on explicit caller-supplied `artifact_root`; choose a default only if later work needs one |
| Any later live vector embedding, backend, or spend boundary | future bundle only | orchestrator plus user | out of scope for `implementation-02`; do not treat as a blocker for the core ingestion seams |
| Frontmatter allowlist for optional graph projection | optional graph seam only | orchestrator plus user | answer the allowlist question before graph projection beyond note, chunk, wiki-link, and path-topology structure |

## Closeout Note

- When this bundle completes, move it from `active/` to `archive/`.
