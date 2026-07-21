# Implementation 07 UAT Gate

The bounded Seam 6A implementation is complete after its governing-anchor and
relation-ordering repair. Seam 7 is next but remains unopened. The historical
UAT requirements below remain the evidence record for the earlier gates.

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

## Seam 8 compiler/schema contract (2026-07-20)

The accepted bounded contract is recorded in
`implementation-07-seam-8-compiler-schema-ADR.md`. The model request shape now
contains semantic intent and supported retrieval-layer requests only. Compiler
selection/claim policy, ordinary budgets, max-chunks, coverage, and negative
claim fields are excluded from the canonical plan. Legacy payloads containing
those fields produce structured `retired` diagnostics and do not affect runtime
binding. The obsolete `retrieval.compiler_compatibility.selection_policy_budgets`
YAML surface was removed. The frontier prompt was not changed.

Controlled compiler UAT used the valid persisted inventory snapshot
`snapshot-bf02d87301f66393cba2374f` with logical inventory hash
`bf02d87301f66393cba2374f6394235103544edcf303f822678b1128e9545a56` and one
snapshot load. Generic cases covered exact exhaustive count, broad conceptual,
graph relation/depth, earliest and before temporal modes, preferred alias,
narrow factual, and unrelated control requests. Canonical plans preserved
requested exact match mode/requiredness/count reporting, lexical mode, graph
depth, temporal mode, and scope alias fields. Duplicate inputs were stable;
unknown and invalid values remained diagnostic. Binding a controlled
`journal` alias produced `scope_resolution.preferred: ["journal"]` and a
`bound_to_preferred_scope` adjustment; temporal defaults and limits were then
runtime-bound from YAML.

Normal live compiler UAT used configured `qwen3:8b` against the persisted
inventory. A bounded first probe timed out at 12 seconds while the endpoint was
loading; a configured 120-second retry succeeded. The genealogy-shaped query
returned a schema-valid request with a required `temporal_retrieve` layer,
mode `ordered`, limit 24, plus optional lexical, vector, and graph layers. It
did not emit a preferred scope alias in that particular run. This is recorded
as a compiler-output limitation, not repaired with query-specific inference;
controlled alias emission and runtime preferred binding remain proven. The
live compiler prompt hash for that request was
`39c82008f046012a7cf1b1a98a6f8b0e888bf13c7b8827b9c8f4938ace657d23`.

The normal live run did not synthesize an answer or claim historical causation.
The compiler gate is therefore limited to request activation and schema
truthfulness; final chronology and causal interpretation remain downstream
retrieval/synthesis concerns. Preferred admission before executor truncation,
reservoir behavior, and sparse stopping remain deferred as recorded in the
accepted Seam 5 decision.

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

## Seam 6A completion repair UAT (2026-07-19)

The repair corrected two bounded defects without changing the typed temporal
anchor index or retrieval surface. A complete synthetic multi-anchor fixture
used one note with qualifying 2020 and 2025 intervals. `earliest` and ordered
ascending selected the 2020 governing anchor; `latest` and ordered descending
selected the 2025 governing anchor; `before` selected the later qualifying
interval; `after` selected the earlier qualifying interval; and `between`
selected the earliest interval. Both anchor IDs and both provenance records
remained visible in every result, and `relation_status` came from the selected
governing anchor. A merge fixture confirmed that temporal provenance and the
governing ID survive when lexical supplies `selection_source`.

The representative relation-ordering fixture used synthetic candidates with
different internal ordinal relevance. In `before`, `after`, and `between`, the
higher-relevance candidate remained first regardless of temporal position.
Equal-relevance output followed deterministic temporal tie-breaks and stable
chunk identity. No global ordinary-fusion or final-packet date sort was added.

The disposable staged-corpus regression remains the accepted 1,083-note /
14,608-chunk run with 506 valid journal-entry anchors. Projection validation
and atomic activation passed; full-projection temporal admission recovered
24 candidates from 19 notes beyond the ordinary lexical top-50, repeated IDs
were deterministic with hash
`f9a0c656fdb14ceda7dd2cb8c9e26f02837f77b3c577a0cc04feb848ce12ec71`, and the
active index was not mutated. The configured corpus has one journal anchor per
note, so the multi-anchor behavior is proven by the complete representative
fixture rather than claimed from corpus scale. No causal claim was made.

The operator-supplied live thread `thread-9472357bec4f` is recorded as a
successful normal Seam 5 result: the genealogy question received lexical,
vector, and graph layers only; temporal was not requested; the packet had 24
selected chunks, 12 selected notes, and maximum concentration 4. The answer
was epistemically cautious and explicitly described chronology as suggestive,
not demonstrated, because no temporal traversal or exact search ran. This is
not a Seam 6A failure; it confirms that normal compiler temporal activation
was deferred to Seam 8, whose bounded activation is recorded below.

Repair verification is committed as `77645ae` (`fix(seam6a): enforce
mode-correct anchors and relation ranking`). Focused temporal tests run 8 and
the focused temporal/retrieval/runtime regression set runs green; the complete
local suite runs 150 tests with zero failures or errors;
compileall and `git diff --check` pass. Prompt hashes remain:
compiler `21e89d824fa2bf13515c49170d375cee1abb8c9d71f80f5c61215e63073b34f4`
and frontier
`fdac281e48e765af09578be02e53ad65a443d49914fac53d017ade5391a18776`; YAML
remains
`854418104a07946075d919a647af3bf4ac785c5844dbd6be4773e19387f99324`. No
compiler/frontier prompt, preferred reservoir, sparse stopping, global date
sort, or private corpus material changed.

## Seam 7 persisted inventory UAT (2026-07-20)

The active database path from the checked-in YAML was not accessible in this
session, so it was not opened or mutated. The configured vault was available;
a fresh disposable ingest used data root
`C:\Users\madis\AppData\Local\Temp\seam7-uat-b1e25np3` and unavailable
embeddings. It produced 1,083 notes and 14,608 chunks, one valid snapshot row,
schema version 1, snapshot ID `snapshot-c8837a4f109ca9d591983f3b`, logical hash
`c8837a4f109ca9d591983f3b1f5dff142c0390591e2b8e3d0bc751ff07b5afe6`, and
inventory-policy hash
`da32f05d48ffc2e64a666e0a62bb5ef38520f2775ac14316739499a9a43d7f8e`.
The serialized payload was 17,690 bytes. Activation and snapshot validation
passed.

Observed source labels were `vault: 1,083`. The bounded note-type facet had
14 observed values; creator had 16; title had 21; tags had 48 with deterministic
32-value truncation; journal-entry-date had 505 with deterministic 32-value
truncation. Path depth 2 observed 5 top-level and 19 second-level values with
no truncation. FTS was valid at 14,608 rows. Vector table was present with zero
rows and unavailable embeddings, so no vector arrays were persisted. Graph
capabilities were 15,739 nodes and 32,142 edges: 3 node types and 4 edge
types. Temporal capabilities were 506 `journal_entry` anchors, all
`explicit_primary` and day precision, with zero conflicts/unresolved rows.

An unchanged disposable reingest created a new ingest run
`ingest-20260720T000528Z-1150258b`, retained exactly one active snapshot, and
reproduced the same logical hash. Volatile source-run metadata changed while
the logical inventory identity remained stable.

Same-turn controlled UAT used the persisted database and a recording compiler.
The compiler request and traversal manifest both reported source `persisted`,
status `valid`, snapshot load count `1`, full inventory rebuilds `0`, and the
same logical hash `c8837a4f109ca9d591983f3b1f5dff142c0390591e2b8e3d0bc751ff07b5afe6`.
The controlled turn completed. A focused regression patches live inventory
recomputation to fail and confirms traversal succeeds with the explicitly
passed persisted summary.

No observed field required an unresolved semantic decision. Values were
recorded under configured frontmatter fields; scope aliases remained current
YAML overlays. Normal compiler temporal activation and richer compiler/schema
emission were deferred to Seam 8 and are recorded above.

## Seam 8 completion repair UAT (2026-07-20)

The operator-supplied live thread `thread-67608780055b` is the principal
counterexample. It used valid persisted inventory and emitted
`scope_requests: ["journal"]` plus required ordered temporal retrieval, but
the top-level query was `origin_of_idea`, semantic queries were bare intent
words, lexical queries were empty, and graph seeds were empty. Canonicalization
then supplied fallback lexical and graph values from the poor query. Runtime
returned 24 temporal chunks across 19 notes with 101 temporal lexical-relevance
matches; vector and graph returned no candidates. This was successful temporal,
alias, inventory, and runtime-authority activation, but incomplete
subject-preserving decomposition—not a temporal, fusion, or inventory failure.

The repair adds generic prompt rules and structural canonicalization. Controlled
cases show that explicit empty `semantic_queries`, `lexical_queries`,
`graph_seeds`, and `retrieval_layers` remain empty; missing lists use fallback
and report `defaulted_missing_fields`; invalid list types report
`invalid_planner_fields`; fallback sources prefer planner concepts, resolved
referents, entities/relations, natural query, then raw input; identifier-like
queries become human-readable subject-bearing text; non-empty intent-labelled
semantic/lexical/graph values receive subject context; and explicit empty graph
seeds never become an intent label.

A fresh repaired runtime replay against the active persisted index reported
`source: persisted`, `status: valid`, `snapshot_load_count: 1`, and zero full
inventory rebuilds. Its canonical compiler request was subject-bearing:
`query: origin of semantic geometry`, semantic queries included origin and
precursors to the subject, lexical queries included the subject, and graph
seeds were subject-bearing. It requested required ordered temporal retrieval.
Executor evidence was lexical 50 candidates, vector 8, graph 0, temporal 10;
selection was 24 chunks with selected support counts lexical 16, vector 7,
temporal 5. The active index was read only; no synthesis or causal claim was
made. This replay did not emit a scope alias.

Additional fresh qwen3:8b compiler probes varied: one emitted `journal` but
omitted temporal and used an explicit empty lexical list; another emitted an
invalid corpus-like scope phrase and omitted temporal. The supplied live thread
proves that journal alias and temporal activation can co-occur, but no single
post-repair normal replay in this run jointly reproduced all acceptance fields.
The Seam 8 repair therefore remains live-UAT gated; no query-specific Python
inference, hidden alias default, temporal executor change, fusion change,
preferred reservoir, or sparse stopping was introduced.

## Seam 8 live-UAT gate closeout (2026-07-20)

The operator-supplied normal live thread `thread-a16554d9aee8` closes the
remaining Seam 8 gate. It demonstrated persisted inventory source `persisted`,
status `valid`, snapshot load count `1`, and `0` full inventory rebuilds. The
compiler emitted the human-readable subject-bearing query `origin of semantic
geometry concept`, preferred scope alias `personal_reflection`, retained the
concept and resolved referent, subject-bearing semantic and lexical queries,
and a subject-bearing graph seed. It requested required ordered temporal
retrieval and emitted no compiler-owned selection or claim policy.

Runtime completed vector retrieval with `21` candidates and `14` selected
contributions, graph retrieval with `24` candidates and `7` selected
contributions, and temporal retrieval with `24` candidates and `4` selected
contributions; required temporal contribution was satisfied. The final packet
contained `24` chunks from `18` notes with maximum note concentration `2`,
representing current explicit conceptual notes, earlier dated personal
evidence, graph-linked material, and external formal sources. Synthesis
distinguished early documented formulations, later development, external formal
reinforcement, and structural resemblance, did not treat Gärdenfors as the
sole origin, and limited causal and exhaustive claims.

This is a successful normal live demonstration of the accepted prompt/schema/
runtime contract and closes the live-UAT stability gate. Stability means the
contract is demonstrated in a normal live run and structurally guarded by
focused tests. It does not require identical stochastic model output on every
invocation and does not justify hidden query-specific inference. Preferred
admission/reservoir remains deferred; configured-maximum filling remains an
observation; sparse stopping remains unapproved; weak temporal candidates can
be reviewed in final Seam 9 UAT but are not a Seam 8 blocker. No production
behavior changed in this record-only closeout.

Operator-supplied visual CI evidence is recorded: title
`docs(seam8): correct repair test count`, tests #105, commit `bce9270`, passed,
approximately 1 minute 39 seconds. No run or job ID beyond the supplied tests
identifier is claimed.

Closeout verification passed locally: focused compiler/schema/control-surface/
prompt/retrieval tests `53`, full Python suite `170`, compileall, and
`git diff --check`. Hashes were unchanged from `bce9270`: compiler template
`cd5c4a7cece251ee5ee82b7434dae3a3309753183cf63d06f1131d1b6c37b0cb`, rendered
compiler fixture `bb767c9fc4a60cf5678f81b7b880b1f9586e4f5832998ca355af571082a0bda7`,
frontier `fdac281e48e765af09578be02e53ad65a443d49914fac53d017ade5391a18776`,
and YAML `cd0bc0b6036643a360ea689ffbb2df2b13d9a76a6a7f254a9d4c99a535b819e9`.
Seam 8 is complete for the accepted bounded contract; Seam 9 is next and
unopened.

## Seam 9 final integration and closeout UAT (2026-07-19)

Starting state and audit: HEAD was exactly `0d4272977b2905ed82c847a353614ad8f9432635`;
the working tree was clean before this closeout. The callable/deferred tool
inventory was inspected and included delegated-agent tools, but no sub-agent
was spawned or claimed. The bounded closeout plan is
`implementation-07-seam-9-final-integration-plan.md`.

### Active index and full-corpus integrity

The checked-in configured index was opened read-only and was not reingested or
mutated. It contains 1,083 notes, 14,608 chunks, 14,608 `chunks_fts` rows,
14,608 compatible vectors, 15,739 graph nodes, 32,142 graph edges, 506
temporal anchors, and exactly one `resource_inventory_snapshots` row with
status `valid`. The latest ingest manifest reports `success`. The recorded
vector identity is sentence-transformers `all-MiniLM-L6-v2`, dimension 384,
normalized, encoding `chunk_embedding_text_v1`. The inventory logical hash is
`bf02d87301f66393cba2374f6394235103544edcf303f822678b1128e9545a56`; its
policy hash is `da32f05d48ffc2e64a666e0a62bb5ef38520f2775ac14316739499a9a43d7f8e`.
No candidate database, SQLite sidecar, or staging artifact was present beside
the active database. No reingest was required because the accepted projections
and inventory snapshot were already present and this closeout did not change
ingest/index shaping.

### Final matrix evidence

- Exact literal/count: the Seam 2B disposable UAT recorded positive
  `semantic geometry` exhaustive counts, bounded context, distinct chunk/note/
  occurrence/returned/selected units, exact provenance after merge, and a
  unique absent phrase with conservative runtime-owned absence permission.
- Broad conceptual: the Seam 5 representative probes and accepted normal
  compiler/runtime records show subject-bearing lexical/vector decomposition,
  cross-surface provenance, bounded note breadth, and cautious synthesis. No
  exact or temporal layer was required unless the request made it constitutive.
- Genealogy: operator-supplied normal thread `thread-a16554d9aee8` demonstrated
  persisted inventory, subject-bearing compiler decomposition, the
  `personal_reflection` alias, required ordered temporal retrieval, vector/
  graph/temporal contributions, 24 chunks from 18 notes, maximum concentration
  2, and synthesis distinctions among earlier formulation, later development,
  external reinforcement, resemblance, and unresolved influence. This remains
  the accepted normal live synthesis evidence; identical stochastic prose is
  not required.
- Explicit temporal boundary: synthetic and controlled temporal tests cover
  before/after/between, ISO boundaries, relation admission, mode-correct
  governing anchors, partial/conflicted certainty, and required contribution.
  No invented date or global date sort was introduced.
- Graph direction: the complete disposable `Journal -> Concept -> Reading`
  topology proves outbound, inbound, and `both`, depth 2, stored edge versus
  traversal orientation, bounded cycles/self-links/reciprocal links, stable
  path ordering, scope interaction, and graph provenance. This is complete
  representative evidence, not an unsupported claim about every live-vault
  topology.
- Two-query vector: the configured corpus UAT recorded independent subject-
  bearing query execution, threshold `0.5`, per-query cap `50`, per-note cap
  `4`, round-robin allocation, valid identity, and deterministic reversed-order
  output with both query streams represented.
- Preferred scope: the accepted normal live thread emitted and bound
  `personal_reflection` as a preferred alias while retaining non-preferred
  evidence eligibility. Preferred executor admission before local truncation
  remains deferred; no reservoir was added.
- Narrow factual: controlled compiler/runtime probes covered a bounded factual
  request without unnecessary temporal or graph activation. No private family
  note text is committed.
- Referential/reset: fixture tests cover active-focus and resolved-referent
  carry for referential/comparison turns, bounded recent-message/semantic-turn
  tails, and unrelated-turn reset. No raw artifact-field leakage was observed.
- Low-signal control: probe and controlled low-signal cases remain truthful when
  optional surfaces return no candidates; no semantic no-match is promoted to
  exact absence and synthesis is not authorized from an unapproved packet.

The final matrix is an evidence ledger across controlled fixtures, disposable
corpus runs, and the supplied normal live thread. It does not claim that every
category was rerun with a fresh frontier invocation during this closeout.

### Packet, attrition, and synthesis findings

Recorded packet sizes range from approximately 31,404 to 66,856 bytes in the
representative executor probes; the accepted genealogy closeout packet was 24
chunks from 18 notes with maximum note concentration 2. The system has a hard
selected-chunk ceiling but no independent byte ceiling, and canonical text is
not silently truncated. Configured-max filling, weak temporal candidates,
near-duplicates, and preferred candidates lost before fusion are classified as
documented tradeoffs or deferred enhancements, not Seam 9 contract failures.
Synthesis evaluation remains separate from retrieval: the accepted normal live
answer used the approved packet cautiously, but prose quality alone is not
retrieval proof.

### Plugin/runtime evidence

`obsidian-plugin` declares npm scripts only for `dev` and `build`; no lint or
plugin-test script exists. `npm run build` passed. Strict `npx tsc --noEmit`
originally reproduced `TS2307` for `electron`; the narrow documented
`electron.d.ts` declaration for the desktop runtime's used `shell.openPath`
API now makes typecheck pass without adding an Electron runtime dependency or
weakening strictness. Machine plugin/runtime boundary evidence consists of the
passing Python probe suite: new thread and continuation artifact/ledger probes
passed, and fixture lexical retrieval completed with approved coverage. Visual
Obsidian UI behavior was not exercised, so the following remains operator-only:

1. Reload/enable the plugin and send one normal conceptual query.
2. Confirm the response renders and the expected thread artifacts persist.
3. Stop the runtime and confirm a clean error is shown; restore it and confirm
   recovery.
4. Restart/reload the plugin and confirm no unnecessary reingest occurs.

### Final verification and status

Focused compiler/control-surface/prompt/retrieval tests previously passed `53`;
the final complete Python suite passed `170` tests in `34.600s`. `compileall`
and `git diff --check` passed. There is no repository lint or plugin-test
command to report. Hashes remain unchanged: compiler template
`cd5c4a7cece251ee5ee82b7434dae3a3309753183cf63d06f1131d1b6c37b0cb`, rendered
compiler fixture `bb767c9fc4a60cf5678f81b7b880b1f9586e4f5832998ca355af571082a0bda7`,
frontier `fdac281e48e765af09578be02e53ad65a443d49914fac53d017ade5391a18776`,
and YAML `cd0bc0b6036643a360ea689ffbb2df2b13d9a76a6a7f254a9d4c99a535b819e9`.

The machine portion of Seam 9 is complete. Implementation 07 remains active
and is `machine-complete / operator-UAT-pending`; it is not archived because
interactive Obsidian UI evidence has not been supplied. Deferred observations
remain preferred reservoir/admission, configured-max filling, sparse stopping,
weak temporal candidates, fuzzy redundancy suppression, hard note/source/byte
caps, weighted RRF, query/graph quotas, and broader semantic-frontmatter
exposure. None is promoted to an unclassified blocker.
## Post-Seam-9 Seam 8 canonicalization repair (2026-07-20)

The operator-supplied persisted thread `thread-a65a4751fb1a` is the
post-closeout counterexample. The raw qwen payload was already structurally
reasonable: it retained `semantic geometry` and `precursors of semantic
geometry` as concepts, subject-bearing semantic and lexical queries, and the
meaningful graph seed `Geometry of Meaning`. The old canonicalizer incorrectly
joined all concepts into one required phrase and rewrote those valid fields
with repeated `regarding ...` prose. The old artifact therefore submitted no
matching graph seed and recorded zero graph candidates.

The repair replayed the same raw payload without regenerating candidates. The
effective plan now preserves:

- semantic queries: `origin of semantic geometry`; `development of semantic geometry`;
- lexical query `semantic geometry` byte-for-byte and repairs only the
  subjectless `precursor concepts` query to `precursor concepts regarding
  semantic geometry`;
- graph seed `Geometry of Meaning` byte-for-byte;
- the original scope requests and retrieval layers, including required ordered
  temporal retrieval;
- no concatenated subject phrase and no repeated `regarding` clause.

Diagnostics expose `subject_candidates`, `minimal_subject_basis`,
`overlapping_subject_candidates`, preserved/repaired fields, preserved graph
seeds, and rejected/repaired graph seeds. Reapplying canonicalization produces
the identical planner plan.

The controlled active-index replay was read-only. Persisted inventory remained
`source: persisted`, `status: valid`, `snapshot_load_count: 1`, and
`full_inventory_rebuilds: 0`. The repaired plan produced 50 lexical
candidates, 8 vector candidates, 0 graph candidates, and 10 temporal
candidates; selection produced 24 chunks from 13 notes, with selected counts
of lexical 13, vector 5, graph 0, and temporal 10. The old artifact had 50
lexical, 8 vector, 0 graph, and 10 temporal candidates and selected 24 chunks
from 7 notes, with graph seed text damaged by canonicalization. The repaired
packet was approximately 57,192 bytes versus the old artifact's 58,254 bytes.
The graph index contains note labels including `Geometry of Meaning` only as
part of longer source titles and has `min_token_overlap: 3`; an exact
`Geometry of Meaning` note node was not present, so the remaining zero graph
result is recorded as index evidence, not repaired with a corpus-specific
seed.

An active-index normal CLI replay could not create a fresh thread because the
configured desktop data root was not writable in this environment. A direct
fresh qwen3:8b compiler call was available and parsed successfully; it emitted
subject-bearing `origin of semantic geometry`, `semantic geometry`, and
`Geometry of Meaning`, with no canonicalizer repetition. That stochastic call
did not emit the temporal layer or a configured scope alias, so it is recorded
as supporting compiler evidence, not as a replacement for the accepted normal
live thread `thread-a16554d9aee8`.

The earlier normal live thread remains the accepted Seam 8 stability evidence:
it demonstrated persisted inventory reuse, preferred alias binding, required
temporal retrieval, vector/graph/temporal contributions, and cautious
synthesis. The new repair is a narrow post-closeout operator-UAT repair, not a
retrieval, fusion, temporal, inventory, ingest, synthesis, or prompt failure.

Status after local verification: machine-complete / operator-UAT-pending.
Implementation 07 remains active; the final interactive Obsidian UI gate and
new CI evidence remain open. Operator screenshots for tests #106 (commit
`0d42729`) and #107 (commit `78ca8aa`) showed queued runs, not passed runs;
neither is recorded as CI success. No query-specific inference, preferred
reservoir, sparse stopping, or production executor change was introduced.

## Bounded scope-authority and evidence-requirement replay (2026-07-20)

The persisted operator artifact `thread-9a0afd164966` was replayed read-only.
Its raw `journal_entry` scope request no longer binds hard scope because that
value is not a configured alias; the replay records an
`unauthorized_inventory_scope` adjustment and leaves effective scope filters
broad. The existing lexical, vector, and graph requests remain intact. The
observed value was not demoted into concepts or queries.

The same repair adds the compiler-facing `evidence_requirements` contract:
`literal_exhaustive`, `lexical_relevance`, `semantic_similarity`,
`graph_relation`, and `chronology`. YAML maps these requirements to their
operators. A plan that declares chronology without `temporal_retrieve` gets
one bounded compiler repair opportunity; if the repaired plan remains
incomplete, retrieval is blocked with structured diagnostics and no
all-surfaces fallback. Focused tests cover complete surface-specific plans,
missing operators, unknown requirements, one successful repair, and failed
repair blocking.

The active-index replay was read-only and did not reingest or mutate the
configured index. Persisted inventory remained valid and reused. No temporal
or graph activation was inferred from the old raw payload merely because the
question was genealogical; the new completeness contract uses only the
compiler-declared requirements. A fresh direct qwen compiler probe remained
subject-bearing but stochastic, so the accepted normal live UAT remains the
separate evidence for temporal and alias activation.

This is a forward-only clean cutover. Raw observed inventory values are
diagnostic-only, configured YAML aliases are authoritative, and incomplete
declared evidence is fail-closed after one repair attempt. Seam 9 remains
machine-complete/operator-UAT-pending; no new CI success is claimed for the
queued operator screenshots.

Final local verification for this bounded repair: the focused compiler,
resolver, and runtime slice passed 138 tests; the complete Python suite passed
184 tests; compileall and `git diff --check` passed. The status is
machine-complete/operator-UAT-pending. No new CI success is claimed for queued
operator screenshots.

## Current-turn integrity replay (2026-07-21)

Persisted thread `thread-70c60d78beaf` was inspected without altering its
historical artifacts. Turn 6 asked `what is my sisters name`, while the raw
compiler response was a parsed noncanonical `retrieval_request` for “building
good habits here”. Canonicalization produced `development of what sisters
name`, lexical terms `what`, `sisters`, `name`, and graph seed `what sisters
name`. Its graph manifest listed `active_focus` among seed sources and included
habit, semantic-geometry, note-title, date, and section material. The turn had
50 lexical, 0 vector, and 24 graph candidates, with 7 lexical and 17 graph
selected contributions. Turns 3 through 5 likewise show lagging compiler
content and active-focus graph sources.

The repair introduces a runtime-owned binding envelope containing thread ID,
turn ID, unpredictable compiler request ID, and the SHA-256 of the exact raw
input. Binding is checked before canonicalization, fallback, completeness,
scope binding, retrieval, focus update, or synthesis. A missing/mismatched
response gets one fresh current-turn retry; a second failure blocks with
complete artifacts and no stale semantic fields or retrieval execution.

The graph seed source list now contains only current-plan `graph_seeds` and
`semantic_queries`. Active focus remains in bounded compiler/synthesis context
and persisted state, but selected titles, sections, prior queries, and prior
seeds cannot directly enter an executor. Deterministic fallback carries only
compact prior concepts/referents for genuinely referential/comparison input;
self-contained input uses current text only.

The entity/sister retrieval result remains explicitly out of scope. Controlled
fixtures prove current-plan graph execution, one retry, failed-retry blocking,
identity mismatch detection, and cross-artifact thread/turn consistency. A
fresh normal live qwen/plugin UAT is still an operator gate, not claimed here.
