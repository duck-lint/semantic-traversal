# ADR: Seam 7 Persisted Resource-Inventory Snapshot

- Status: accepted for the bounded contract in Implementation 07
- Date: 2026-07-19
- Scope: deterministic observed-inventory persistence during atomic ingest and
  one-snapshot runtime reuse
- Next seam: Seam 8 compiler/schema contract remains unopened

## Context

Before Seam 7, a runtime turn scanned the active `notes`/`chunks` tables to
construct `resource_inventory_summary` for the semantic compiler, then scanned
the database again during semantic traversal before resolver binding. The
summary was useful observed-corpus evidence, but its construction was live,
repeated, and not tied to a validated ingest state.

## Decision

The staged candidate database contains one authoritative
`resource_inventory_snapshots` row. The snapshot stores a versioned JSON
observed-inventory payload, a logical hash, the source ingest run, an
inventory-policy hash, and validation status. It is built after notes, chunks,
FTS, vectors, graph, and temporal anchors exist; it is validated against those
candidate projections; and it activates atomically with the database.

The normal runtime loads the snapshot once, overlays current YAML scope
aliases and other policy-owned fields, and passes the resulting immutable
summary to compiler-request construction, resolver binding, and traversal
manifest serialization. The runtime never writes the active database.

## Observed facts versus YAML policy

The persisted payload contains only observed corpus facts: counts, observed
source labels, bounded configured semantic-frontmatter facets, path segments,
and capability facts for exact/FTS, vector, graph, and temporal projections.
Scope aliases, retrieval limits, allowed fields, path depth, facet bounds, and
other operational controls remain YAML-owned. Scope aliases are overlaid at
load time and excluded from the logical inventory hash. The snapshot records
the inventory-policy hash under which its observed projection was generated.

## Snapshot schema and hashes

The table `resource_inventory_snapshots` has one current row with
`snapshot_id`, `inventory_schema_version`, `source_ingest_run_id`,
`generated_at`, `inventory_policy_hash`, `logical_inventory_hash`,
`payload_json`, and `validation_status`. The logical hash is the canonical
hash of the payload only; it excludes generated time, run UUID, filesystem
paths/metadata, and YAML scope aliases. Identical observed inventory therefore
has the same logical hash across unchanged reingests. Freshness requires the
schema and inventory-policy hashes to match current runtime configuration and
the source ingest run to exist.

## Inventory controls and bounding

YAML owns `resource_inventory.path_depth`, `max_values_per_facet`,
`max_path_values`, `max_graph_types`, and `max_temporal_types`. Values are
sorted before bounding; each bounded section records observed count, returned
count, omitted count, and truncation. The existing
`chunking.semantic_frontmatter_fields` list is the sole admitted semantic
frontmatter-field authority. Nested arbitrary metadata is not flattened.

## Construction and validation

Ingest builds the snapshot after canonical projections and existing lexical,
vector, and temporal validation. Snapshot validation checks one-row
cardinality, JSON/schema/hash integrity, policy hash, ingest-run identity,
note/chunk/source/facet/path counts, FTS alignment, vector identity summary,
graph counts/types, temporal counts/types, bounded-section consistency, and
absence of note bodies, chunk text, vectors, and secrets. A build, persistence,
or validation failure is recorded as an inventory-stage failure; candidate
cleanup and prior-active-database preservation follow the existing atomic
ingest contract.

## Runtime loading and fallback

The canonical loader returns the observed payload, current YAML overlays, and
structured diagnostics. Valid snapshots report `source: persisted`,
`status: valid`, the logical/policy hashes, source run, and `snapshot_load_count:
1`. Legacy databases without the table report `recomputed_fallback` /
`legacy_missing`; stale or corrupt rows are never silently used and report the
invalidity before safe recomputation. If canonical tables cannot be scanned,
the runtime uses a minimal config-only summary and reports
`config_only_fallback`. Fallbacks never mutate an active database.

## Compatibility and privacy

The compiler-visible compatibility projection retains the existing keys:
configured source labels, observed source labels, `frontmatter_facets.note_type`,
`path_topology.top_level`/`second_level`, graph capabilities, and current
scope-alias overlay. Rich observed capability facts remain additive and do not
broaden resolver scope algebra. No prompt, compiler schema, retrieval
operator, or temporal behavior changes. Snapshot payloads contain no paragraph
text, note bodies, embeddings, full provenance paths, or secrets.

## Tests and UAT

Focused tests cover deterministic logical hashes, unchanged/changed corpus
facts, snapshot validation and fallback diagnostics, atomic failure
preservation, alias overlay, one-load same-turn reuse, compiler/resolver
compatibility, projection capability facts, bounds, privacy, and reversed
insertion order. The disposable configured-corpus UAT records snapshot scale,
capabilities, hash stability across unchanged reingest, and a controlled turn
whose compiler and resolver consume the same persisted summary.

## Migration and rollback

Legacy active databases remain executable through explicit in-memory fallback
until reingest creates a snapshot. No history table is introduced. Rollback is
a normal revert of Seam 7 commits; the prior runtime can ignore the additive
snapshot table, and failed staged builds cannot replace the active database.

No observed field in the bounded contract requires semantic interpretation
beyond configured field names, aliases, table identities, and existing schema
contracts. Any future semantic alias decision remains an explicit Seam 8
question rather than an ingest guess.
