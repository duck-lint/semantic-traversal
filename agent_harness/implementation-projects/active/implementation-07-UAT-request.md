# Implementation 07 UAT Gate

The implementation is not marked complete. The next evidence required is operator UAT against a disposable ingest of the configured corpus or an explicitly complete representative corpus.

## Why UAT is required now

The current automated suite proves contracts on isolated fixtures, but cannot establish:

- truthful inbound and `both` graph traversal over the real wikilink topology;
- FTS5 index completeness and freshness after a full-corpus ingest;
- vector identity compatibility, threshold attrition, and multi-query diversity at corpus scale;
- chronology and cross-surface evidence for the genealogy query;
- plugin TypeScript readiness while the local `electron` declaration is absent.

## Required UAT checks

1. Run a disposable full-corpus ingest. Confirm the active database contains `chunks_fts` and that failed indexing leaves no replacement artifact or damaged prior active database.
2. Run exact search for `"semantic geometry"`; inspect `coverage.exact_status`, total count, selected count, scope, and negative-claim permission.
3. Run the genealogy query from the program. Confirm semantic-query execution diagnostics, FTS/graph/vector provenance, and journal preference without journal-only exclusion.
4. Set graph direction to `inbound`, then `both`, against a fixture or corpus topology where `journal -> concept -> reading` exists. Confirm direction and path are visible in selected packets and manifests.
5. Run two distinct semantic queries and confirm both execute and appear in chunk provenance.
6. Run referential continuation followed by an unrelated query. Confirm active-focus graph seeds carry only for the referential/comparison turn.
7. Record packet size, candidate attrition, and deterministic repeated-run output.

## Current implementation status

Seam 4A is complete. Automated regression after the graph direction/provenance changes: 118 tests passing; compileall and diff check passing. The Python changes are now committed and pushed on the implementation branch. Plugin build/type status was not rerun for this Python-only slice; the previously recorded `npx tsc --noEmit` blocker remains outside this seam.

The configured vault index was copied to a disposable workspace index before UAT. The active index was not mutated.

Actual compiler genealogy run:

- compiler scope requests: `idea_origin`, `precursor_concepts`;
- bound hard scope: empty; bound preferred scope: empty;
- candidate counts: lexical 50, vector 24, graph 24;
- graph: requested/effective depth 1/1, outbound, one matched seed, six expanded notes;
- selected packet: 24 chunks, with lexical, vector, graph, and multi-surface provenance present;
- synthesis was blocked because UAT synthesis was disabled;
- no journal preference was inferred from unsupported concept-like scope requests.

Controlled supported-scope genealogy run against the same disposable corpus:

- compiler scope request: `journal`;
- bound hard scope: empty; bound preferred scope: `note_type: [journal_entry]`;
- candidate counts: lexical 50, vector 24, graph 24; selected packet size 24;
- journal candidates were selected first and marked `preferred_scope_match: true`; non-journal conceptual/reading evidence remained eligible and appeared later;
- graph candidate pool represented multiple traversed notes, with the first round containing distinct note IDs before later representatives; repeated runs produced the same selected order;
- chronology was not established by this bounded retrieval run, and later conceptual resemblance was not treated as historical origin.

The three prior repairs are now corpus-demonstrated: provenance and graph depth were exercised by the actual run; round-robin fairness was exercised by the no-hard-scope actual run and the controlled preferred-scope run. Fixture evidence additionally covers graph fairness, provenance retention, depth 0/1/2/default/clamping, and deterministic source-layer ordering.

## Repair evidence

- Prompt SHA-256 before and after: semantic compiler `9CA92643B2550410743D494FC82E3AE67C718C8F7867D72E43DDB49925E28F4B`; frontier synthesis `FDAC281E48E765AF09578BE02E53AD65A443D49914FAC53D017ADE5391A18776`.
- YAML SHA-256 before: `47CB325D8B1E2A290013563D93231B2BD47126E6A5A76EB9D1D6C495508B7385`; after: `B0B2AA1F0EF45D1CADC52156DD79AB3E9E7379D3E626F541BBB5768065ABFBA4` (scope policy authority added).
- Remaining open seams: compiler/schema emission of preferred scope, vector thresholds/diversity, general fusion/selection, temporal substrate, persisted inventory, final scaling/UAT, and plugin TypeScript readiness. Seam 4A direction is complete against the complete representative topology recorded below; full-corpus graph direction remains optional follow-up evidence rather than a claimed result.

## UAT decision

Do not archive implementation-07 until these checks either pass with artifacts or produce explicit bounded follow-up seams.

## Seam 2B exact-search UAT (2026-07-18)

Root causes were bounded to the exact seam: the executor treated the candidate cap as the total-count boundary, flattened all terms into one chunk-level match count, discarded the requested literal `required` flag during adequacy checks, had no field-aware context materialization, and derived absence permission from aggregate status rather than exhaustive execution plus runtime policy. Required exact evidence also had no reservation/adequacy check after bounded selection.

The configured 229 MB SQLite index was copied to a disposable workspace index; the active index was not modified. Controlled compiler packets were used so exact runtime behavior was isolated from compiler and synthesis behavior. Synthesis was intentionally disabled; resulting `LLM backend unavailable` blocking is not a retrieval failure.

- Positive `semantic geometry`, case-insensitive, exhaustive count requested: `completed_with_matches`; 77 matching chunks, 16 matching notes, 100 non-overlapping substring occurrences, 5 returned exact candidates, and 5 selected exact-backed chunks for the required term. Context evidence retained the actual matching field, including paragraph, section-label, and relative-path matches. Repeated runs had identical status, selected IDs, evidence, and ordering.
- Negative unique phrase: `completed_no_matches`; exhaustive scoped scan, zero chunks/notes/occurrences/candidates, no selected evidence required, and `negative_claims_allowed: true` under the runtime YAML exact-layer policy.
- Mixed required terms (one present, one absent): independent per-term `completed_with_matches` and `completed_no_matches` results; the present term had adequate selected contribution, the absent term had valid absence evidence, and global negative permission remained false.
- Count control: `return_total_count: true` exposed exhaustive totals independently of a five-candidate return bound. `false` exposed `count_status: not_requested` and null exhaustive totals while retaining returned-candidate diagnostics.
- Match mode: case-sensitive lower-case phrase produced 3 chunks/3 occurrences; case-insensitive produced 77 chunks/100 occurrences, demonstrating the requested mode was executed rather than coerced.
- Exact provenance: selected chunks retained `selection_source` separately from canonical `source_layers`; exact evidence survived merge/selection fixture tests. The selected packet serializer now explicitly includes `selection_source` alongside `source_layers`.

Seam 2B is complete. At that point, the remaining open seams were non-exact requiredness (later completed as Seam 3), compiler/schema emission of preferred scope, inbound/both graph corpus UAT, vector thresholds/diversity, general fusion/selection, temporal substrate, persisted inventory, final synthesis UAT, and plugin TypeScript readiness. The preferred-scope ranking introduced in Seam 2A remains a narrow runtime behavior to reconcile later with Seam 5; compiler emission of preferred scope remains open for Seam 8.

## Seam 3 runtime authority and non-exact required-layer UAT (2026-07-18)

The remaining Seam 3 gaps were caused by accepted compiler fields being normalized without preserving invalid or unsupported requests for diagnosis, executor outcomes being represented as ad hoc booleans rather than structured layer statuses, and final selection checking only candidate presence rather than selected provenance contribution. Runtime selection policy, claim policy, budgets, and candidate limits are now bound from YAML; compiler values are retained only as requested inputs and produce explicit override diagnostics when they diverge. Unknown operators and invalid limits are retained and block when required rather than being silently discarded.

Structured manifests now distinguish `not_requested`, `skipped_no_input`, `disabled`, `unsupported`, `unavailable`, `failed`, `partial_failure`, `completed_no_candidates`, and `completed_with_candidates`. Required lexical, vector, and graph layers must execute successfully, produce candidates, and retain at least one selected chunk with the layer in `source_layers`. With YAML `preserve_required_layers` enabled, deterministic preselection reserves required non-exact sources within configured budgets and packet bounds; post-selection adequacy remains authoritative and reports bounded conflicts without expanding limits. This is a narrow required-layer rule, not a general Seam 5 fusion redesign.

The configured 229 MB SQLite index was copied to a disposable workspace index; the active index was not mutated. Controlled compiler packets were used to isolate runtime behavior, with synthesis disabled. `LLM backend unavailable` therefore represents the intentionally disabled synthesis boundary, not retrieval failure.

- Required lexical success: `completed_with_candidates`, requested/effective limit `5/5`, candidate count `5`, selected contribution `5`, adequate `true`; compiler selection and claim-policy divergence emitted runtime override diagnostics.
- Required vector success: `completed_with_candidates`, requested/effective limit `5/5`, candidate count `5`, selected contribution `5`, adequate `true`; the semantic query embedded successfully and returned five bounded candidates after a full backend search.
- Required lexical no-candidate: `completed_no_candidates`, coverage blocked with an explicit no-candidate/inadequate-contribution reason, and negative permission remained false.
- Required vector unavailable: `unavailable`, coverage blocked, and negative permission remained false.
- Required graph fixture: structured graph execution status and selected graph contribution were recorded; existing graph depth, provenance, and round-robin fixture coverage remained green. Inbound/`both` corpus validation remains open for Seam 4A.

Prompt hashes remained unchanged: semantic compiler `9CA92643B2550410743D494FC82E3AE67C718C8F7867D72E43DDB49925E28F4B`; frontier synthesis `FDAC281E48E765AF09578BE02E53AD65A443D49914FAC53D017ADE5391A18776`. YAML remained unchanged from the prior Seam 2B baseline: `B0B2AA1F0EF45D1CADC52156DD79AB3E9E7379D3E626F541BBB5768065ABFBA4`.

Seam 3 was complete before this graph slice. Remaining open seams at that point were compiler/schema emission of preferred scope, graph inbound/`both` corpus UAT, FTS full-ingest freshness, vector thresholds/diversity, general fusion/selection, temporal substrate, persisted inventory, final synthesis UAT, and plugin TypeScript readiness. Seam 4A is now complete; Seam 3 does not generalize requiredness to all future operators beyond the implemented lexical/vector/graph contract.

## Seam 4A graph direction and complete representative UAT (2026-07-18)

The configured corpus was not used as proof of direction because its full wikilink topology was not established as the required distinguishing chain. Instead, a complete disposable representative corpus was used and the active index was not mutated. The topology stored these directed `note_links_note` edges:

```text
Journal -> Concept -> Reading
```

The compiler packet submitted `Concept` as the graph seed and requested depth `1`; runtime direction came from the copied config, not the compiler. Synthesis used an unavailable controlled backend, so retrieval approval/blocking was inspected independently of the intentional synthesis boundary.

- Outbound: matched seed note `Concept`; one unique expanded note `Reading`; graph candidates represented `Concept` and `Reading`; no `Journal` candidate was reached. Structured hop evidence recorded `edge_source_note_id=Concept`, `edge_target_note_id=Reading`, `from_note_id=Concept`, `to_note_id=Reading`, and `traversal_direction=outbound`.
- Inbound: matched seed note `Concept`; one unique expanded note `Journal`; graph candidates represented `Concept` and `Journal`; no `Reading` candidate was reached. Structured hop evidence recorded the stored edge as `Journal -> Concept` while the traversal step was `Concept -> Journal`, with `traversal_direction=inbound`.
- Both: matched seed note `Concept`; two unique expanded notes, `Journal` and `Reading`; both hop directions appeared in one bounded union and no note was duplicated in `selected_note_ids`. Candidate chunks remained bounded and deterministic; multiple chunks from one note are distinguished from `candidate_unique_note_ids`.
- Depth 2: outbound from `Journal` reached `Concept`, then `Reading`; inbound from `Reading` reached `Concept`, then `Journal`. The manifest recorded requested/effective depth `2/2` and hop numbers `1` and `2`.
- Determinism: repeated runs and the same topology with reversed edge-row insertion order produced identical graph manifests, candidate ordering, selected packet ordering, and structured hop evidence. Reciprocal links, a self-link, and a directed cycle terminated without duplicate unique note identities or unbounded hop evidence; hop evidence is capped at eight distinct paths per note.
- Scope: preferred journal scope retained non-preferred graph candidates and ranked journal evidence first; hard journal scope excluded non-journal graph candidates. Existing graph edge/node allowlists, disabled/unavailable/no-seed statuses, depth behavior, fairness, required graph contribution, and active-focus seed tests remained green.
- Required graph: existing outbound fixture and the Seam 3 required graph integration test continue to show selected graph contribution and adequate coverage; the direction topology uses optional graph layers because its purpose is directional evidence.

The bounded graph repair was production code, not evidence-only: `semantic_traversal/runtime.py` now sorts adjacency by edge type and stable node ID, retains structured hop orientation separately from traversal direction, propagates bounded hop provenance through merge and packet serialization, and exposes submitted seeds plus matched, selected, candidate, and unique-note counts. `tests/test_ingest_runtime.py` gained the complete directed topology, depth-2, insertion-order, inbound provenance, and cycle/reciprocal/self-link regressions. `tests/test_runtime_config.py` now locks invalid YAML direction rejection.

Seam 4A is complete. Remaining open seams are FTS5 full-ingest freshness and failure atomicity (Seam 4B), vector thresholds/diversity (Seam 4C), general fusion/selection, temporal substrate, persisted inventory, compiler/schema emission of preferred scope, final synthesis UAT, and plugin TypeScript readiness. No full configured-corpus direction claim is made beyond the complete representative evidence recorded here.

## Seam 4B FTS5 freshness, failure atomicity, and packet-scale UAT (2026-07-19)

Seam 4B is complete. The production gaps were bounded to three causes: ingestion mutated the active SQLite database before all later stages had succeeded; there was no post-materialization proof that `chunks_fts` exactly projected canonical chunk rows; and lexical diagnostics exposed only the post-limit candidate count, making raw FTS matches and scope attrition unauditable.

The repair stages each ingest into a same-directory candidate database, validate schema/materialization/FTS alignment, and activate with `os.replace` only after candidate connections are closed. Failure manifests record the stage, whether activation occurred, whether the prior active database was preserved, candidate cleanup, and the path of the preserved `latest-success.json`. FTS validation checks canonical/FTS counts, missing and orphan IDs, duplicate IDs, searchable-field equality, and a bounded query probe. The active vector degradation policy was preserved. Lexical diagnostics now distinguish FTS index rows, raw matches, scope-admissible matches, and returned candidates while retaining the compatibility `candidate_count`.

Focused evidence:

- Freshness lifecycle covered initial ingest, paragraph update, frontmatter metadata update, rename, unchanged reingest, and delete; FTS rows and searchable metadata tracked the canonical chunks.
- Validator regressions covered duplicate IDs, orphan rows, and field mismatches.
- Failure regressions covered before/schema initialization, materialization/FTS refresh, candidate validation, and activation. Initial failure left no active database; later failures preserved the prior active database bytes and latest-success manifest, and cleaned candidate files/sidecars.
- All five YAML-owned lexical modes remained exercised: `exact_phrase`, `all_tokens`, `any_tokens`, `prefix`, and `ranked_fts`.

Disposable full configured-vault ingest used the YAML source root `C:\Users\madis\Desktop\kháos` and a temporary data root; the active configured index was not modified. The successful run recorded 1,078 notes, 14,576 chunks, and 14,576 FTS rows, with zero missing, orphan, duplicate, or field-mismatch rows and a passed query probe. The resulting database was 94,416,896 bytes and had no SQLite sidecars after activation.

For the bounded lexical query `semantic geometry` with a return limit of 50, the disposable corpus reported:

- `exact_phrase`: 92 raw/scoped matches, 50 returned, 13 unique notes;
- `all_tokens`: 378 raw/scoped matches, 50 returned, 5 unique notes;
- `any_tokens`: 2,035 raw/scoped matches, 50 returned, 5 unique notes;
- `prefix`: 2,071 raw/scoped matches, 50 returned, 4 unique notes;
- `ranked_fts`: 2,035 raw/scoped matches, 50 returned, 5 unique notes.

Bounded serialized candidate packets were approximately 58–61 KB for these direct executor probes. Repeated runs produced identical candidate ordering and diagnostics for every mode. This is lexical executor evidence, not a claim that final cross-surface fusion is complete. Vector execution was intentionally unavailable in this lexical-only UAT and remains Seam 4C work.

Operator verification: CI was manually verified as passing for commit `7cad93683773c20e5bc0ff299c4842e95fa78ada`; no GitHub check artifact or registered status was available in this session, so this is recorded as operator evidence rather than CI-service evidence.

Prompt hashes remained unchanged: semantic compiler `9CA92643B2550410743D494FC82E3AE67C718C8F7867D72E43DDB49925E28F4B`; frontier synthesis `FDAC281E48E765AF09578BE02E53AD65A443D49914FAC53D017ADE5391A18776`. YAML remained `B0B2AA1F0EF45D1CADC52156DD79AB3E9E7379D3E626F541BBB5768065ABFBA4` before and after.

Seam 4B is complete. Remaining open seams are Seam 4C vector identity/threshold/diversity, Seam 5 general fusion/selection, Seam 6A temporal substrate, Seam 7 persisted inventory, Seam 8 compiler/schema emission of preferred scope, final synthesis UAT, and plugin TypeScript readiness. The next planned seam is Seam 4C; no Seam 4C implementation was started here.
## Seam 4C vector identity, thresholds, and diversity UAT (2026-07-19)

Seam 4C is complete. Vector rows now persist canonical provider/model/dimensions/normalization/encoding identity and an identity hash; changed or incompatible rows are re-embedded, and malformed, stale, or mixed rows are diagnosed. YAML owns `min_similarity: 0.5`, `per_query_max_candidates: 50`, `max_chunks_per_note: 4`, and `max_candidates: 200`. Each accepted query executes independently, with canonical query ordering, per-query score provenance, and deterministic round-robin allocation. This is executor behavior, not Seam 5 fusion.

Corpus calibration at `.2/.35/.5/.65` was recorded before selecting `.5` as a bounded tradeoff: `semantic geometry` produced `7251/2256/1350/46`, `autobiographical chronology journal development` `4918/1059/24/0`, `conceptual precursor influence` `6969/1443/7/0`, and an unrelated control `735/1/0/0`. The selected value removes the unrelated control while retaining positive signals; it is not corpus-specific ranking logic.

The disposable configured corpus contained 1,078 notes and 14,577 chunks. Ingest produced 14,577 compatible vectors. Identity was homogeneous: provider `sentence_transformers`, model `all-MiniLM-L6-v2`, dimensions 384, normalized vectors, encoding `chunk_embedding_text_v1`; missing, orphan, malformed, numeric, empty, zero-norm, dimension, identity, and mixed counts were all zero. Final threshold-0.5 probes returned:

| Probe | Status | Returned | Unique notes | Max chunks/note |
| --- | --- | ---: | ---: | ---: |
| `semantic geometry` | completed_with_candidates | 8 | 2 | 4 |
| `autobiographical chronology journal development` | completed_with_candidates | 14 | 11 | 4 |
| `conceptual precursor influence` | completed_with_candidates | 4 | 1 | 4 |
| unrelated control | completed_no_candidates | 0 | 0 | 0 |
| two-query combination | completed_with_candidates | 22 | 13 | 4 |

The two-query packet was 29,867 bytes. Both queries contributed, and reversing query input order produced identical status, counts, diagnostics, candidate ordering, and packet ordering. The per-query chronology result was 24 candidates before its cap and the semantic-geometry result was 1,350 before its cap; the final combined selection represented both query surfaces after duplicate reconciliation. Focused vector/ingest/config tests passed (33); the complete local Python suite passed (133); compileall and `git diff --check` passed. Fixture evidence covered identity reuse, model invalidation, malformed rows, threshold inclusivity, per-query/per-note caps, deterministic ordering, and score provenance surviving a lexical merge.

Operator-supplied visual evidence for the preceding Seam 4B gate: GitHub Actions run title `complete seam 4b fts freshness and atomic ingest`, `tests #93`, commit `e4087f1`, branch `codex/big-refactor-07.18.26`, passed, approximately 1m46s. No run/job ID was available in this session, so no GitHub status was independently claimed.

Open seams remain Seam 5 fusion/selection, Seam 6A temporal substrate, Seam 7 persisted inventory, Seam 8 compiler/schema emission of preferred scope, final synthesis UAT, and plugin TypeScript readiness. No prompt, compiler, temporal, inventory, or plugin surface changed in this seam. Before starting Seam 5, create or update its ADR to define how executor provenance and bounded diversity are reconciled with selection policy.

Final hashes: semantic compiler `9ca92643b2550410743d494fc82e3ae67c718c8f7867d72e43ddb49925e28f4b`; frontier synthesis `fdac281e48e765af09578be02e53ad65a443d49914fac53d017ade5391a18776`; YAML before `b0b2aa1f0ef45d1cadc52156dd79ab3e9e7379d3e626f541bbb5768065abfba4`, after `2178698b9086aed79747618ca792eddc2e885732d8a952750850e45a7f029743`.

## Seam 5 bounded ordinal-fusion experiment (2026-07-19)

This continuation used a new branch, `codex/seam5-ordinal-note-breadth`, from `b5883fbce243d1cc61c28fe06a19f4d5eda7181f`; the baseline branch was not changed. The original genealogy turn remained available at `C:\Users\madis\Desktop\kháos\.semantic-traversal\threads\thread-872f7213b0c1\turns\turn-000001`. The original packet evidence was retrieval packet 47,895 bytes and synthesis context 93,029 bytes, with hashes recorded in the tracker. Because the original run did not persist its merged candidate pool, temporary instrumentation reconstructed the pool from the same compiler packet, database, and executor backends. The capture hash was `797a5d8bfd507c0e15ca03dc3736061c1773da6552d57f9593f3bda437f387b9`.

The reconstructed baseline reproduced the supplied failure: exact 0, lexical 50, vector 8, graph 24, merged 77, selected 24; 4 selected notes with maximum concentration 13; title counts 13 Gärdenfors, 8 Semantic Geometry, 2 December 31 2025, and 1 Semantic Traversal; selected support lexical 19, vector 6, graph 4; selected-source counts lexical 19, vector 3, graph 2; preferred selected 2. The same candidate pool under the approved experiment selected 24 chunks from 11 notes with maximum concentration 4. Selected support counts were lexical 13, vector 3, graph 12; selected-source counts were lexical 11, vector 3, graph 10. Both semantic-query provenance streams remained present, graph support represented four notes, and the preferred selected count did not decrease in the same-pool replay. Repeated replay was identical: selected-ID hash `d8248dc0a14c20aabc8a51353656e4597763165c7ce02fbc9dfdc8428fcd1ef`, fusion hash `09722de61abd7a797d228e524ce891fbcdc656c237e77ccf9c18b1d2e472e500`.

The selected experiment is unweighted ordinal surface fusion: one-based per-surface rank streams, `sum(1/rank)`, canonical deterministic tie-breaking, and `selection_source` from best surface rank rather than raw-score magnitude. Required exact and required-layer reservations remain first. Ordinary layer-budget allocation is retired explicitly and diagnosed; it is not silently reused as a cap or quota. Ordinary selection is evidence-source breadth-before-depth by note ID, with the existing global packet maximum and deduplication retained. No preferred reservoir, hard note cap, fuzzy duplicate suppression, byte ceiling, weighted RRF, query/graph quota, or marginal threshold was added.

Representative direct executor probes completed for: genealogy, exact literal, broad conceptual, narrow factual, graph relation, preferred-scope, multi-query, and unrelated control. They were deterministic and returned 24 selected chunks in each case. The observed packet sizes were 39,094 genealogy, 63,960 exact literal, 35,468 broad conceptual, 31,404 narrow factual, 36,473 graph relation, 66,856 preferred-scope, 33,599 multi-query, and 35,982 unrelated bytes under the direct probe serializer. These are retrieval-packet measurements only; the direct executor probe did not build synthesis context. The preferred-scope probe’s controlled plan did not request a preferred scope, so it does not close the preferred-admission gap. Two near-duplicate journal chunks remain in the genealogy evidence because fuzzy redundancy control is intentionally deferred. This experiment therefore passes its bounded same-pool breadth/provenance/determinism gate while leaving the broader Seam 5 ADR proposed and unapproved.

Final experiment verification is local, not a claim about registered CI: focused tests, full suite, compileall, diff check, prompt hashes, YAML hash, and prohibited-surface inspection are recorded in the tracker and closeout. Remaining open seams are preferred-scope reservoir/admission, full fusion design, temporal ordering, persisted inventory, compiler/schema emission, final synthesis UAT, and plugin readiness.

Final hashes for this experiment: rendered compiler fixture `21e89d824fa2bf13515c49170d375cee1abb8c9d71f80f5c61215e63073b34f4`; frontier instructions `fdac281e48e765af09578be02e53ad65a443d49914fac53d017ade5391a18776`; YAML `070f4a528d845e2a8d31a7c174062a7444caca66b2325e682079f5f8432c8bd6`. The source UAT's generated compiler prompt hash `9ca92643b2550410743d494fc82e3ae67c718c8f7867d72e43ddb49925e28f4b` remains recorded unchanged.

## Seam 5 normalization and closeout (2026-07-19)

The accepted experiment commit `3dd1579de29a5b960489a3b4f1bb6eba05f40f73` descended directly from `b5883fbce243d1cc61c28fe06a19f4d5eda7181f` and was fast-forwarded into `codex/big-refactor-07.18.26`. Both worktrees were clean before normalization; no conflict or independent target divergence was found. Seam 5 is complete for the bounded ordinal-fusion experiment only. The broader weighted-RRF/hybrid ADR is superseded historical analysis and unapproved.

The next planned seam is Seam 6A: temporal substrate and temporal retrieval. Its implementation must preserve the accepted fusion contract, use runtime/YAML authority for temporal policy and bounds, and keep prompts, compiler schema, packet field compatibility, and final historical-causation claims outside scope.

## Seam 5 architecture baseline and ADR (2026-07-19)

This is a design-only phase. No production fusion, ranking, selection, prompt, YAML, or packet-construction code changed. The active baseline at `ac4516fa853762f3d4efa9b304085581cd889f0f` was 133 passing Python tests. Prompt hashes were unchanged: compiler `9ca92643b2550410743d494fc82e3ae67c718c8f7867d72e43ddb49925e28f4b`, frontier `fdac281e48e765af09578be02e53ad65a443d49914fac53d017ade5391a18776`; YAML remained `2178698b9086aed79747618ca792eddc2e885732d8a952750850e45a7f029743`.

The supplied corpus genealogy run is recorded as the principal Seam 5 counterexample: lexical 2,498 raw/scoped and 50 returned; vector 14,577 compatible rows, threshold 0.5, 8 returned with both semantic queries represented; graph 24 returned across 4 candidate notes from one seed and six expanded notes; final packet 24 chunks but only 4 notes, including 13 Gärdenfors chunks, 8 Semantic Geometry chunks, 2 near-duplicates from one journal entry, and 1 Semantic Traversal chunk. Only 2 preferred-scope journal chunks survived. This is a fusion/selection failure, not a vector-executor failure. The synthesis answer blurred later conceptual resemblance with historical origin and did not recover previously demonstrated journal chronology.

The current selector merges by chunk ID, retains `source_layers`, chooses `selection_source` from the highest raw score with source-priority tie-breaking, ranks by demotion, preferred-scope match, source priority, raw score, and chunk ID, reserves required evidence, performs source-layer budget passes, then globally fills the remaining packet without enforcing those budgets. Current budgets are therefore ambiguous reservation targets rather than hard caps. Preferred scope is annotated after executor truncation, final selection has no note cap, and near-duplicate text can consume multiple slots. Four characterization tests now freeze these current behaviors and are explicitly expected to change only after approval: budget overflow, one-note concentration, unlike-score ordering, and preferred-scope loss after lexical truncation.

The original analysis is retained at [implementation-07-seam-5-fusion-ADR.md](implementation-07-seam-5-fusion-ADR.md), now superseded and unapproved. The accepted bounded decision is [implementation-07-seam-5-accepted-ADR.md](implementation-07-seam-5-accepted-ADR.md): unweighted ordinal fusion, required reservations, retired ordinary layer budgets, breadth-before-depth by note ID, and deterministic diagnostics. The full hybrid is historical analysis, not routine planned Seam 5 work.

Operator-supplied visual GitHub Actions evidence for Seam 4C: run title `complete seam 4c vector identity and diversity`, tests/run identifier `tests #94`, commit `ac4516f`, branch `codex/big-refactor-07.18.26`, passed, approximately 1m49s. No run/job ID was independently available, so this is recorded as operator-supplied visual evidence only.

Design-slice verification after adding the characterization tests: focused characterization suite `4` passed; complete local Python suite `137` passed; compileall and `git diff --check` passed. Prompt and YAML hashes remained unchanged, and no production ranking or packet surface appeared in the diff.

## Recovery and governance correction (2026-07-19)

Pushed commit `0b039ad0d0bfe7adc974ad1ed1fee81c853697d8` was normally reverted
as `d6c770a`. It was a narrower temporal annotation/order prototype: it did
not implement typed temporal anchors, temporal retrieval operations, recovery
outside ordinary fusion, or chronology in UAT. It remains historical Git
evidence only. Seam 6A is reopened/proposed; Seam 7 has not begun.

The accepted Seam 5 operator UAT aggregates are bounded as follows. The
genealogy run produced the strongest conceptual-development reconstruction
observed so far, but did not establish historical chronology or causal
influence. This supports fusion functioning while showing temporal retrieval
is a distinct epistemic requirement. The direct sister/family-relation run
preserved sister/full/half-sister distinctions and refused stronger inference
than indexed evidence licensed; broad lexical material filled the configured
maximum. That is a max-fill observation, not a demonstrated reasoning failure.
No private family-note text is recorded.

Deferred live-UAT findings remain open: preferred evidence may be removed by
executor-local truncation before fusion admission; no reservoir is introduced
until genuine temporal retrieval exists. The selector may fill the configured
`selection_policy.max_chunks` when enough admissible candidates remain; no
sparse stopping is introduced, and the configured value is not inherently 24.
Reassess only if later UAT shows noise, reasoning degradation, or material
context cost.

## Seam 6A temporal retrieval UAT (2026-07-19)

The accepted ADR is
`implementation-07-seam-6a-temporal-retrieval-ADR.md`. The implementation
adds a normalized `temporal_anchors` projection and a controlled
`temporal_retrieve` operator without changing compiler or frontier prompts.

Disposable configured-corpus ingest used a temporary staged data root and an
unavailable embedding backend; the active index was not overwritten. It
recorded 1,083 notes, 14,608 chunks, and 506 valid anchors. All anchors were
`journal_entry`, `explicit_primary`, and day precision; invalid, ambiguous,
and conflict counts were zero. Projection validation passed and activation
completed atomically.

For the controlled earliest-relevant `semantic geometry` comparison, ordinary
lexical retrieval returned 50 candidates from 5 notes after its cap. Direct
temporal relevance admission scanned the full FTS projection, then applied
the configured journal-entry anchor filter and returned 24 candidates from 19
notes. All 24 temporal IDs were outside the ordinary lexical top-50 set,
demonstrating recovery beyond ordinary executor truncation rather than date
sorting of the selected packet. Repeated temporal runs were deterministic with
candidate-ID hash
`f9a0c656fdb14ceda7dd2cb8c9e26f02837f77b3c577a0cc04feb848ce12ec71`.

A controlled runtime plan with required `temporal_retrieve` completed with 24
candidates and 24 selected temporal contributions. The packet retained
temporal source provenance, anchor IDs, intervals, precision, authority,
relation status, and the fifth-surface fusion order. Synthetic tests covered
latest, before, after, between, vector identity/threshold admission, and
required temporal contribution behavior through the existing runtime path.

The corpus did not provide a trustworthy Gärdenfors encounter/read anchor in
the configured mapping. It is therefore not described as a historical origin
or influence based on publication order. Earlier anchored journal evidence,
later conceptual evidence, and undated structural evidence remain distinct;
causal influence is unresolved. Temporal retrieval independently recovered
anchored evidence, so the deferred preferred-scope reservoir issue remains
deferred. The configured max-fill observation remains open; no sparse stopping
was added.

Local verification for this UAT: temporal focused suite `5` passed; complete
Python suite `147` passed; compileall and `git diff --check` passed. Prompt
hashes remained compiler
`21e89d824fa2bf13515c49170d375cee1abb8c9d71f80f5c61215e63073b34f4` and
frontier `fdac281e48e765af09578be02e53ad65a443d49914fac53d017ade5391a18776`.
YAML changed from `09d7d5ea139d66b98ce56e17a469b70fbdbbbd3c4bd4258ea5f9217eb6c15052`
to `854418104a07946075d919a647af3bf4ac785c5844dbd6be4773e19387f99324`.
No private corpus text was committed.
