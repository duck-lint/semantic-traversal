# Implementation 08 — Seam 0 Incremental Report

## Status

Seam 0: in progress

## Baseline

- Starting branch: `stable-runtime`
- Starting SHA: `ad7c03fec4efcf86b773a2b377ccfada4eb24a67`
- Work branch: `codex/implementation-08-control-plane`
- Preflight: 192 Python tests passed; compileall passed; diff check passed; Obsidian production build passed; project-configured strict TypeScript passed.
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
| Baseline harness | passed | JSONL fsync checkpointing, resume skip, controlled unavailable/timeout/malformed doubles |
| Machine closeout | pending | Full test/build/push/clean gate |

## FTS evidence

Affected path: `semantic_traversal/runtime.py::_lexical_candidates`, called from `_semantic_traversal` for the `lexical_chunk_search` layer. Before repair, the independent fixture query `I'm` produced `i'm`; SQLite returned an FTS5 syntax error; lexical status was `failed`, candidate count was `0`, the exception did not escape the lexical executor, and the runtime traversal recorded lexical `unavailable` with no selected evidence. Frontier synthesis was not invoked by the traversal-only fixture.

The repair serializes literal atoms as doubled-double-quote FTS5 values. `exact_phrase` quotes the normalized complete phrase; `all_tokens` joins quoted atoms with `AND`; `any_tokens` and `ranked_fts` join quoted atoms with `OR`; `prefix` appends the FTS5 `*` grammar outside each quoted atom. ASCII/curly apostrophes, embedded quotes, punctuation, and operator-like tokens remain literal. The complete expression remains the bound value of `chunks_fts MATCH ?`; no user, corpus, or planner text is interpolated into SQL.

Focused lexical coverage passes: 8 tests, including candidates, zero candidates, skipped empty/punctuation-only input, structured index failure, all five modes, multiple streams, repeated deterministic execution, and the runtime coverage path.

## Baseline evidence

The persisted generic baseline is `implementation-08-baseline-records-final.jsonl`. It contains 29 terminal records and is resumable from its case IDs. Counts: completed 20; timed_out 1; unavailable 1; malformed 1; blocked 2; unsupported_baseline_capability 4; harness_error 0. Direct scenarios retain `emitted_response_mode: null` and `unsupported_baseline_capability`; the harness does not fabricate a production route field. Current routing is recorded as the existing always-traverse assumption. Planner failures use controlled backend doubles, not magic live-model inputs.

## Closeout rule

Only update every Seam-0 record to `Seam 0: machine-complete / operator-acceptance-pending` after every required machine gate passes. Then stop; do not begin Seam 1.
