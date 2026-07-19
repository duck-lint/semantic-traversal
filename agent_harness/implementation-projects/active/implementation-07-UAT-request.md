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

Automated regression after the serialization and Seam 2A changes: 96 tests passing; compileall and diff check passing. Python production changes are uncommitted. Plugin build/type status was not rerun for this Python-only slice; the previously recorded `npx tsc --noEmit` blocker remains outside this seam.

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
- Remaining open seams: compiler/schema emission of preferred scope, full exact-search contract, non-exact requiredness, inbound/both corpus UAT, vector thresholds/diversity, general fusion/selection, temporal substrate, persisted inventory, final scaling/UAT, and plugin TypeScript readiness.

## UAT decision

Do not archive implementation-07 until these checks either pass with artifacts or produce explicit bounded follow-up seams.
