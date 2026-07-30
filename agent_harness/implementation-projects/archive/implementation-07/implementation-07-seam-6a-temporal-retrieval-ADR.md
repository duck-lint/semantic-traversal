# ADR: Seam 6A Typed Temporal Anchors and Bounded Retrieval

- Status: accepted for the bounded contract in Implementation 07
- Date: 2026-07-19
- Scope: typed temporal-anchor persistence, bounded temporal retrieval, and
  fifth-surface ordinal-fusion integration
- Next seam: Seam 7 remains unopened

## Context and decision

The reverted `0b039ad` prototype annotated already selected chunks and sorted
them globally. That is not temporal retrieval: it cannot recover relevant
anchored evidence outside ordinary executor caps and it collapses distinct
date meanings. Seam 6A therefore adds a normalized temporal-anchor projection
and an explicit `temporal_retrieve` surface.

Temporal anchors remain typed facts about configured source fields. A note or
chunk may retain multiple anchors. Temporal retrieval first admits relevance
from the full temporally eligible indexed projection, then applies temporal
relations to anchor intervals. It never asserts causation or labels early
evidence as a precursor.

## Typed anchor model

Each anchor stores a stable ID, note ID, optional chunk ID, configured anchor
type, canonical start and end, precision, source field, original source value,
authority class, parsing status, conflict state, diagnostic reason, and ingest
run ID. The initial configured vocabulary is extensible and includes
`journal_entry`, `authored`, `created`, `modified`, `encountered_or_read`,
`publication`, and `event`. Only mappings explicitly present in YAML produce
anchors; unknown date-like fields are not guessed.

The initial checked-in mapping is `journal_entry_date -> journal_entry` with
`explicit_primary` authority because that field’s meaning is established by
the existing corpus contract. No filesystem timestamp is ingested.

Year, month, day, and ISO datetime values are parsed strictly. Partial values
become intervals spanning their stated precision and retain that precision.
Ambiguous locale dates and invalid values are diagnosed, not guessed. Multiple
valid fields remain separate. Same-type disagreement creates a deterministic
conflict group; no value wins by precedence. Conflicted or unresolved anchors
are excluded from definite relations by default.

## YAML authority

Runtime YAML owns temporal enablement, maximum candidates, default limit,
allowed modes, default anchor types, allowed authorities, conflict policy,
and explicit field mappings. Compiler requests can select supported modes,
types, authorities, boundaries, and bounded limits, but cannot raise limits,
admit disallowed authorities, or change mappings. No CLI control or hidden
temporal limit is introduced.

## Persisted storage and atomic ingest

A dedicated `temporal_anchors` table is created in the staged candidate
database. It is indexed by note, chunk, type, start/end, authority, and
status. Ingest derives anchors from every configured field, replaces anchors
for changed notes, removes deleted-note anchors, and does not duplicate
unchanged anchors. Validation checks orphan links, unique IDs, interval order,
known vocabulary/authority/precision, duplicate anchors, and conflict
metadata. Temporal validation failure occurs before activation and preserves
the previous active database and latest-success artifact under the existing
Seam 4B atomic-ingest contract.

Manifest diagnostics expose status, total/anchored/unanchored counts, valid,
invalid, ambiguous, and conflict counts, distributions by type/authority/
precision, and bounded identity/field-only samples. Paragraph text is never
copied into diagnostics.

## Temporal retrieval contract

The supported operator is `temporal_retrieve` with `required`, `mode`,
`anchor_types`, `authorities`, `limit`, `before`, `after`, `start`, `end`,
`direction`, and `include_unresolved` fields. Supported modes are
`earliest`, `latest`, `before`, `after`, `between`, and `ordered`. Contradictory
or malformed boundaries are diagnosed; limits are clamped to YAML maximums;
disabled, unavailable, empty, failed, and inadequate required execution are
structured statuses.

Temporal relation semantics use intervals. Definite before means anchor end
precedes comparison start; definite after means anchor start follows
comparison end. Overlap is unresolved for strict relations. `between` uses
the configured interval relation. Earliest/latest/ordered use only relevance-
admitted anchors and preserve unresolved status rather than inventing order.

Relevance admission queries the full eligible indexed projection directly,
using existing FTS5 semantics and vector identity/threshold validation. It
does not consume ordinary lexical/vector top-N lists. Temporal internal
relevance uses one lexical rank stream and one vector rank stream, deduplicates
by chunk, and combines them with the accepted unweighted ordinal rule. Raw
scores remain provenance only.

For `earliest`, `latest`, and `ordered`, the ordering tuple is temporal
position, internal ordinal relevance, stable anchor identity, then chunk ID.
For `before`, `after`, and `between`, relation admission is a filter; among
admitted candidates the ordering tuple is internal ordinal relevance
(descending), temporal value, stable anchor identity, then chunk ID. Exact
interval comparisons never become string sorting.

### Relation-specific temporal tie-break clarification

The temporal value is deliberately secondary to internal ordinal relevance.
Its direction is fixed and mode-specific so that equal-relevance results are
stable without making date outrank relevance:

- `before`: later qualifying intervals first, using descending canonical end
  (the interval closest to a supplied upper boundary); if an end is not
  available, use descending canonical start.
- `after`: earlier qualifying intervals first, using ascending canonical start
  (the interval closest to a supplied lower boundary); if a start is not
  available, use ascending canonical end.
- `between`: ascending canonical start, then ascending canonical end.

The complete runtime tuples are therefore:

- `earliest`: `(canonical_start, -internal_ordinal_relevance, stable_id)`.
- `latest`: `(-canonical_start, -internal_ordinal_relevance, stable_id)`.
- `ordered ascending`: `(canonical_start, -internal_ordinal_relevance, stable_id)`.
- `ordered descending`: `(-canonical_start, -internal_ordinal_relevance, stable_id)`.
- `before`: `(-internal_ordinal_relevance, -canonical_end_or_start, stable_id)`.
- `after`: `(-internal_ordinal_relevance, canonical_start_or_end, stable_id)`.
- `between`: `(-internal_ordinal_relevance, canonical_start, canonical_end,
  stable_id)`.

The implementation uses stable deterministic sorting rather than comparing raw
lexical, vector, or temporal scores across surfaces. A candidate's governing
anchor supplies the tuple's temporal fields; all other qualifying anchors stay
visible in temporal provenance.

## Seam 5 integration and provenance

Temporal is an explicit fifth source surface only when requested and executed.
It contributes `temporal` to `source_layers`, preserves `selection_source`,
and participates in the existing sum-of-reciprocal-ranks fusion. Required
reservations, breadth-before-depth by note ID, and the global configured
`max_chunks` ceiling remain unchanged. The ordinary merged pool and final
packet are not globally date-sorted.

Each temporal candidate retains mode, anchor identity/type, canonical
interval, precision, authority, source field, relation and certainty,
internal lexical/vector ranks, internal ordinal relevance, temporal surface
rank, and merged ordinary provenance. “Earlier relevant evidence” is neutral
runtime language; causal influence is outside the runtime contract.

If required temporal contribution cannot survive bounded selection, coverage
blocks with a structured inadequate-contribution reason. Temporal completion
does not authorize negative or causal claims.

## Compiler, UAT, and rollback boundaries

The semantic compiler prompt, compiler schema, frontier prompt, and normal
compiler behavior are unchanged. Controlled plans may request the operator
directly; Seam 8 remains responsible for normal compiler emission. Preferred
scope remains non-exclusionary and no preferred reservoir is added. Sparse
stopping is not added.

Acceptance requires synthetic parser/index/retrieval tests plus a disposable
staged-corpus UAT proving that relevant anchored evidence is recovered beyond
the ordinary selected packet. The UAT must separately report anchor
distribution, relation certainty, earlier/later/undated evidence, packet
composition, deterministic repeated output, preferred-admission findings,
and configured-max-fill observations. Publication date must not be described
as encounter/read date when that anchor is absent.

Rollback is a normal revert of the Seam 6A commits. Candidate temporal schema
and data are staged and activated atomically; no active index is mutated by a
failed build. Seam 7 is not part of this decision.
