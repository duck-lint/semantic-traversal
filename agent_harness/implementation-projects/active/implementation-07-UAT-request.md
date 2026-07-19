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

Automated regression: 86 tests passing; compileall and diff check passing. Python production changes are uncommitted. Plugin build passes; `npx tsc --noEmit` remains blocked by missing `electron` declarations (`TS2307`).

## UAT decision

Do not archive implementation-07 until these checks either pass with artifacts or produce explicit bounded follow-up seams.
