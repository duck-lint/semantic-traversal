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

Automated regression after implementation-07 repairs: 93 tests passing; compileall and diff check passing. Python production changes are uncommitted. Plugin build/type status was not rerun for this Python-only slice; the previously recorded `npx tsc --noEmit` blocker remains outside this seam.

The configured vault and active SQLite index were present, but corpus-level UAT was not run in this slice: no disposable full-corpus ingest was created, and querying the active index would create thread artifacts. Fixture evidence is therefore not presented as corpus-wide proof.

Focused fixture evidence now covers truthful multi-layer provenance, graph depth 0/1/2/default/clamping, runtime-vs-removed-hop-limit authority, deterministic round-robin graph candidates, graph provenance retention through merge, and deterministic source-layer ordering.

## Repair evidence

- Prompt SHA-256 before and after: semantic compiler `9CA92643B2550410743D494FC82E3AE67C718C8F7867D72E43DDB49925E28F4B`; frontier synthesis `FDAC281E48E765AF09578BE02E53AD65A443D49914FAC53D017ADE5391A18776`.
- YAML SHA-256 before: `43C9E7927D53B63B3924AC11C3627247A72ED0686F706ED16547483C107D2856`; after: `47CB325D8B1E2A290013563D93231B2BD47126E6A5A76EB9D1D6C495508B7385` (only duplicate graph hop-limit authority removed).
- Remaining open seams: hard/preferred scope representation, full exact-search contract, non-exact requiredness, inbound/both corpus UAT, vector thresholds/diversity, general fusion/selection, temporal substrate, persisted inventory, final scaling/UAT, and plugin TypeScript readiness.

## UAT decision

Do not archive implementation-07 until these checks either pass with artifacts or produce explicit bounded follow-up seams.
