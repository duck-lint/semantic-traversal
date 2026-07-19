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
