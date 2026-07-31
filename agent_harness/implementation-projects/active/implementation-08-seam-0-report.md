# Implementation 08 — Seam 0 Incremental Report

## Status

Seam 0: operator-accepted

## Baseline

- Starting branch: `stable-runtime`
- Starting SHA: `ad7c03fec4efcf86b773a2b377ccfada4eb24a67`
- Work branch: `codex/implementation-08-control-plane`
- Correction verification: 206 local Python tests passed; 14 focused Seam-0 tests passed; compileall passed; diff check passed; Obsidian production build passed; project-configured strict TypeScript passed.
- Persisted inventory: unavailable; no repository-local `latent_space.sqlite3` or latest manifest was found; no reingest performed.
- Semantic compiler prompt SHA-256: `7c115b3441e5e2dda6805c1ad73f0718b67d329ea0ed92a6460707578c982537`.
- Frontier synthesis prompt SHA-256 (runtime's stripped instruction text): `e384b20feee70851c9df57c1b1a38a3765c382ac983b6edcfb066385bd0c351b`.
- Runtime YAML SHA-256: `e103f1e1836b82216e3b3d448dd40bc8b1c0f1090a36720727188dcbc59f7600`.
- The source semantic compiler template hash is `7c115b3441e5e2dda6805c1ad73f0718b67d329ea0ed92a6460707578c982537`; each rendered baseline prompt hash is preserved in its case record (26 distinct rendered hashes because packet content is included).

## Evidence ledger

| Gate | Status | Evidence |
| --- | --- | --- |
| Starting-state gate | passed | Exact SHA, clean tree, archived Implementation 07, empty active bundle |
| FTS reproduction | passed | Starting-tree fixture generated raw `i'm`; executor returned structured FTS failure, zero candidates, no escaped exception |
| Serializer repair | passed | `_lexical_candidates` now uses quoted FTS5 atoms and bound MATCH parameter across five modes |
| Evaluation matrix | passed | 29 generic scenario-turn records; real two-turn shared-thread cases included |
| Baseline harness | passed | Atomic per-case JSON checkpoints, generated JSONL summary, state reload, truncation quarantine, controlled failure doubles |
| Machine closeout | passed | Correction pass and all required local machine gates passed; commit, push, and clean-worktree state are recorded below |

## FTS evidence

Affected path: `semantic_traversal/runtime.py::_lexical_candidates`, called from `_semantic_traversal` for the `lexical_chunk_search` layer. Before repair, the independent fixture query `I'm` produced `i'm`; SQLite returned an FTS5 syntax error; lexical status was `failed`, candidate count was `0`, the exception did not escape the lexical executor, and the runtime traversal recorded lexical `unavailable` with no selected evidence. Frontier synthesis was not invoked by the traversal-only fixture.

The repair serializes literal atoms as doubled-double-quote FTS5 values. `exact_phrase` quotes the normalized complete phrase; `all_tokens` joins quoted atoms with `AND`; `any_tokens` and `ranked_fts` join quoted atoms with `OR`; `prefix` appends the FTS5 `*` grammar outside each quoted atom. ASCII/curly apostrophes, embedded quotes, punctuation, and operator-like tokens remain literal. The complete expression remains the bound value of `chunks_fts MATCH ?`; no user, corpus, or planner text is interpolated into SQL.

Focused lexical coverage passes: 8 tests, including candidates, zero candidates, skipped empty/punctuation-only input, structured index failure, all five modes, multiple streams, repeated deterministic execution, and the runtime coverage path.

## Baseline evidence

The old baseline was discarded as non-authoritative. The replacement baseline is `implementation-08-baseline-records-final.jsonl`, with one authoritative per-case JSON checkpoint under `implementation-08-baseline-records-final-cases/` and persisted scenario state under `implementation-08-baseline-records-final-state/`. It contains 29 terminal records and 29 unique case IDs. Planner-call status counts: parsed 26, timed_out 1, unavailable 1, invalid_json 1. Response/execution terminal counts: completed 20, timed_out 1, unavailable 1, malformed 1, blocked 2, unsupported_baseline_capability 4, harness_error 0.

`completed` means only that the planner call reached a terminal parsed state. It does not mean retrieval or synthesis succeeded. The unavailable, controlled timeout, and malformed cases use backend doubles. The blocked records are the expected zero-evidence/private-evidence cases. Direct-mode records are expected unsupported baseline capabilities because production response-mode routing is not implemented. Persisted inventory was unavailable, so retrieval and synthesis were not executed. This baseline succeeded as a planner-evaluation baseline; it is not evidence of acceptable qwen3:8b quality or successful retrieval/synthesis.

Real multi-turn state is persisted after each turn using the current production context shape: actual `prior_thread_state`, `conversation_thread`, recent message history, recent semantic turns, active focus, compiler response status/raw output, and canonical packet. Turn 2 reloads that state and its packet contains the actual turn-1 input/history. Restart recovery creates a new backend instance for turn 2. Topic reset emitted no stale turn-1 subjects. The qwen3:8b ambiguous-reference continuation did not preserve ambiguity (`ambiguity_preserved: false`); this is recorded as a baseline observation, not hidden or repaired in Seam 0.

Expected and observed fields are separate. Expected fields are `expected_response_mode`, `expected_current_turn_subjects`, `expected_referents`, `expected_operators`, and `expected_evidence_requirements`. Observed fields come from the canonical packet/plan: `emitted_current_turn_subjects`, `resolved_referents`, `requested_operators`, `evidence_requirements`, `requiredness`, `canonical_plan`, `plan_completeness`, and `plan_executability`. Plan validators were run for all records. Component comparison counts are: current-turn target correctness match 11/mismatch 18; referent correctness match 16/mismatch 13; operator correctness match 5/mismatch 24; evidence-requirement correctness match 13/mismatch 16; completeness complete 24/incomplete 5; executability executable 26/non-executable 3.

Measured metrics are limited to planner status, canonical plan fields, validator results, and expectation/observation comparisons. Candidate recall, selection precision, synthesis support, and final-answer quality are explicitly unavailable because retrieval, inventory, direct routing, and frontier synthesis were not executed.

Checkpoint recovery uses atomic per-case JSON (`temp` + flush/fsync + `os.replace`) and regenerates the summary from valid case files. A damaged case is explicitly quarantined and rerun; valid cases are not rerun. Focused tests cover truncated recovery, deterministic clean resume, unique authoritative IDs, genuine state carry, reset, ambiguity, restart, and observed operator/evidence extraction.

The Python-owned FTS query limit was removed. No replacement YAML limit was added. The serializer retains bound `MATCH ?` SQL, safe literal quoting, embedded-quote doubling, accepted modes, and structured failure. The post-repair regression is named for repaired behavior; the independent pre-repair reproduction remains evidence here rather than a misleading test name.

## Closeout rule

Seam 0: operator-accepted. The machine gates passed locally, the corrected records are committed and pushed, and the worktree is clean. Do not begin Seam 1 in this documentation correction.
