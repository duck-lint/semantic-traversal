# Implementation 07 Final Integration Report

Status: machine-complete; operator-UAT-pending. Implementation 07 remains
active and is not archived because interactive Obsidian UI evidence was not
available in this environment.

## Starting state and plan

- Starting commit: `0d4272977b2905ed82c847a353614ad8f9432635`.
- Branch: `codex/big-refactor-07.18.26`.
- Seam 9 plan: `implementation-07-seam-9-final-integration-plan.md`.
- The callable/deferred inventory was inspected. Delegated-agent tools exist;
  no delegation was used or claimed.

## Contracts and authority

Seams 5, 6A, 7, and 8 remain complete for their accepted bounded contracts.
Historical Seam 0/1A rows are reconciled as superseded by later verified
guards/projection work; Seam 1B is complete for machine build/type gates; Seam
2A is complete including normal live preferred-alias emission. No production
retrieval authority was redesigned in Seam 9.

No `retrieval_scoring.source_priority` consumer remains. Executor-local scoring
bonuses remain local ordering evidence. Compiler selection/claim fields remain
retired diagnostics, while runtime/YAML owns execution policy. Prompt/schema
fields are executed, bound, rejected, or diagnosed; no accepted field is
silently ignored.

## Database and corpus evidence

The active configured index was inspected read-only: 1,083 notes, 14,608
chunks, 14,608 FTS rows, 14,608 compatible vectors, 15,739 graph nodes,
32,142 graph edges, 506 temporal anchors, one valid inventory snapshot, and a
successful latest ingest manifest. No sidecar/candidate/staging artifact was
present. Vector identity is `all-MiniLM-L6-v2`, dimension 384, normalized,
`chunk_embedding_text_v1`. No reingest was required.

## UAT and synthesis boundaries

Exact positive/negative count/context, broad conceptual, the accepted normal
genealogy thread `thread-a16554d9aee8`, explicit temporal boundaries,
representative graph direction, two-query vector diversity, preferred scope,
narrow factual, referential carry/reset, and low-signal evidence are recorded
in the active UAT request. The normal genealogy synthesis demonstrated the
accepted distinction between documented development, later development,
external reinforcement, structural resemblance, and unresolved influence.

The full matrix combines controlled fixtures, disposable corpus evidence, and
the supplied normal live run; not every category was freshly rerun with a
frontier invocation during closeout. Deferred observations remain preferred
admission/reservoir, configured-max filling, sparse stopping, weak temporal
candidates, fuzzy redundancy suppression, hard note/source/byte caps, weighted
RRF, query/graph quotas, and broader semantic-frontmatter exposure.

## Plugin and operational readiness

`npm run build` and strict `npx tsc --noEmit` pass. The historical Electron
typing failure was repaired with a documented minimal declaration for the
desktop runtime's used `shell.openPath` API; no broad dependency upgrade was
made. No plugin lint or test script is declared. Python probe thread creation,
continuation, and fixture lexical retrieval passed. Visual Obsidian behavior
remains operator-only: reload, normal query rendering, artifact persistence,
runtime error/recovery, and restart-without-reingest.

README operational guidance now covers the actual CLI, UUID-gated ingest,
reingest conditions, active/staged database distinction, persisted-inventory
validation, thread artifacts, and plugin restart behavior. Privacy/security
review found no committed credentials, tokens, vector arrays, SQLite files, or
private family-note text; the tracked example path and placeholder credential
documentation remain deliberate configuration/evidence references.

## Verification and hashes

- Focused compiler/control-surface/prompt/retrieval tests: 53 passed.
- Full Python suite: 170 passed in 34.600 seconds.
- `python -m compileall semantic_traversal tests`: passed.
- `git diff --check`: passed.
- `npm run build`: passed.
- `npx tsc --noEmit`: passed after the narrow Electron declaration.
- No repository lint or plugin-test command is defined.
- Compiler template: `cd5c4a7cece251ee5ee82b7434dae3a3309753183cf63d06f1131d1b6c37b0cb`.
- Rendered compiler fixture: `bb767c9fc4a60cf5678f81b7b880b1f9586e4f5832998ca355af571082a0bda7`.
- Frontier prompt: `fdac281e48e765af09578be02e53ad65a443d49914fac53d017ade5391a18776`.
- YAML: `cd0bc0b6036643a360ea689ffbb2df2b13d9a76a6a7f254a9d4c99a535b819e9`.

Prior operator-supplied CI evidence remains limited to the recorded Seam 8
visual run (`tests #105`, commit `bce9270`). No CI evidence is claimed for
this Seam 9 closeout until independently available.

## Archive and next action

The branch is ready for operator UI UAT, not yet ready for final archive or a
fully machine-independent merge recommendation. After the bounded UI
checklist is supplied, update the active tracker/UAT records and archive only
if all gates remain satisfied. Do not create Implementation 08 automatically.

## Post-Seam-9 Seam 8 canonicalization repair addendum (2026-07-20)

The operator payload in `thread-a65a4751fb1a` exposed a generic canonicalizer
bug: overlapping concepts were collapsed into one concatenated subject phrase,
so already-valid semantic and lexical queries and the meaningful graph seed
were rewritten with repetitive `regarding` clauses. The repair uses an ordered
subject-candidate set, overlap-aware minimal basis, any-candidate preservation,
meaningful graph-seed preservation, conservative subjectless repair, and
idempotence. It does not alter the compiler prompt, YAML, runtime executors,
fusion, temporal, inventory, ingest, synthesis, or plugin behavior.

Persisted raw-payload replay produced unchanged subject-bearing semantic
queries, preserved `semantic geometry` lexically, minimally repaired only
`precursor concepts`, and preserved `Geometry of Meaning` exactly. The
read-only active-index replay retained persisted inventory validity and
returned 50 lexical, 8 vector, 0 graph, and 10 temporal candidates; selection
was 24 chunks from 13 notes. The graph index has no exact
`Geometry of Meaning` note node and requires token overlap 3, so zero graph
matches is recorded as index evidence rather than repaired with a topic rule.
The old artifact selected 24 chunks from 7 notes after the damaged seed and
the repaired replay selected from 13 notes; this comparison is evidence about
canonicalizer-induced damage, not a fixed acceptance count.

A direct fresh qwen3:8b compiler probe was available and emitted concise
subject-bearing queries and the unchanged graph seed, but stochastic output did
not include temporal activation or a scope alias. The accepted normal live
thread `thread-a16554d9aee8` remains the stability evidence for those fields.
The normal CLI could not create a fresh thread because the configured desktop
data root was not writable in this environment. No final operator UI evidence
was claimed.

The repair is machine-complete and operator-UAT-pending. The historical Seam 9
machine closeout remains valid and was not rewritten. New CI evidence is also
pending: operator screenshots for tests #106 and #107 showed queued runs, not
passed runs. Local verification after the repair: 18 compiler-schema tests,
175 full Python tests, compileall, and `git diff --check` passed. Hashes remain
unchanged: compiler template
`cd5c4a7cece251ee5ee82b7434dae3a3309753183cf63d06f1131d1b6c37b0cb`, rendered
compiler `bb767c9fc4a60cf5678f81b7b880b1f9586e4f5832998ca355af571082a0bda7`,
frontier `fdac281e48e765af09578be02e53ad65a443d49914fac53d017ade5391a18776`,
and YAML `cd0bc0b6036643a360ea689ffbb2df2b13d9a76a6a7f254a9d4c99a535b819e9`.
