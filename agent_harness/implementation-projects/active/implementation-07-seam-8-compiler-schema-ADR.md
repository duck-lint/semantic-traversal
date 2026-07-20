# Seam 8 Compiler and Schema Contract

- Status: accepted bounded contract
- Date: 2026-07-20
- Scope: semantic compiler request shape, canonicalization, inventory-aware intent, and runtime binding diagnostics

## Context

Seam 7 provides a validated persisted inventory projection. Runtime and YAML
already own operational policy, limits, coverage, claims, fusion, diversity,
graph direction, temporal authorities, and final execution. The compiler is
currently over-specified: its prompt and fallback plan emit `selection_policy`,
`claim_policy`, and ordinary layer budgets even though the resolver discards
those values. That makes the model-request schema misleading and leaves
retired compatibility markers in the prompt.

## Decision

The model-emitted request contains only semantic intent and supported retrieval
requests. Its canonical top-level fields are `raw_user_input`, `intent`,
`query`, `entities`, `relations`, `resolved_referents`,
`planner_retrieval_plan`, and `limitations`. The planner plan may contain:
`intent_type`, `scope_requests`, `concepts`, `resolved_referents`,
`literal_terms`, `semantic_queries`, `lexical_queries`, `graph_seeds`, and
`retrieval_layers`.

Supported retrieval layers are `exact_chunk_search`,
`lexical_chunk_search`, `vector_search`, `graph_expand`, and
`temporal_retrieve`. Layer fields are retained and diagnosed when accepted:
`required`, bounded `limit`, exact `literal_terms`/match mode and
`return_total_count`, lexical `mode`, graph `depth`, and temporal mode,
anchor types, authorities, boundaries, direction, and unresolved inclusion.
Runtime canonicalization binds defaults, clamps limits, validates operators and
aliases, and records requested/effective values.

The model schema excludes `selection_policy`, `budgets`, `max_chunks`,
`preserve_required_layers`, `claim_policy`, coverage flags, and
negative-claim permission. A legacy payload containing these fields is not
silently accepted: canonicalization emits a structured `retired` diagnostic
and the resolver derives the effective runtime policy from YAML. Unknown
fields remain structured diagnostics. The raw user input remains byte-for-byte
preserved.

Scope requests are inventory-facing aliases only. The compiler may request an
available alias and express hard/preferred intent through the supported scope
request shape; it may not emit raw note types, paths, sources, or corpus terms
as policy. Runtime decides whether the request is hard or preferred from the
operator and YAML scope policy. Unknown aliases and unavailable operators or
types are diagnosed; required unsupported requests block through the existing
resolver contract.

Temporal activation is generic and evidence-bound. Terms such as origin,
development, earliest, latest, before, after, between, and chronological may
map to supported temporal modes when the request makes chronology constitutive.
The compiler must not infer dates, causal influence, or filesystem chronology,
and must not turn ordinary uses of current, old, or history into temporal
retrieval without an explicit temporal intent.

## Authority boundary

YAML/runtime remains authoritative for enablement, defaults, bounds, exact
context and count policy, lexical/vector thresholds and caps, graph direction
and bounds, temporal authorities and allowed modes, hard/preferred semantics,
coverage, negative claims, fusion, and packet limits. The compiler supplies
requests only. The resolver is the sole request-to-effective-plan boundary.

The obsolete `retrieval.compiler_compatibility.selection_policy_budgets`
surface is retired. Planner defaults retain runtime selection policy because
the runtime selector consumes it; no compiler prompt marker or model field
duplicates that authority.

## Determinism and fallback

Canonicalization deduplicates lists in first-seen order, preserves requested
field values when invalid so diagnostics can identify them, and uses stable
operator and identity ordering. Fallback is schema-valid, conservative, and
does not use hidden keyword-specific retrieval logic. Repeated requests with
the same input and inventory projection produce the same canonical plan and
diagnostics.

## Inventory boundary

The compiler receives only the validated persisted inventory summary: schema
metadata, scope aliases, bounded facet values, paths, exact/FTS capability,
vector identity, graph node/edge types, and temporal types/authorities/
precision. It does not receive private note text, vectors, or unbounded corpus
inventory. Prompt examples are generic and do not encode corpus-specific names
or genealogy language.

## Compatibility and diagnostics

The canonical compiler packet remains the runtime packet boundary. Existing
runtime-owned `selection_policy` and `claim_policy` are injected into the
bound plan, not model output. Legacy fields are retained only in diagnostics
with `action: retired`; they cannot affect execution. Prompt and schema golden
fixtures are updated for the accepted contract, while the prior hashes are
recorded in the tracker as migration evidence. The frontier prompt is
unchanged.

## Verification contract

Focused tests cover schema exclusion, legacy-field retirement, inventory-aware
aliases, exact count intent, lexical mode, graph depth, temporal modes,
unsupported required/optional requests, contradictory temporal bounds,
deduplication, raw-input preservation, deterministic fallback, runtime policy
injection, and prompt guards. Controlled compiler UAT covers exact count,
broad conceptual, graph relation, earliest/before/after/chronology, preferred
aliases, narrow factual, and unrelated control requests. Normal live compiler
UAT is required for temporal activation and must be recorded separately from
runtime execution evidence.

## Non-goals and open boundaries

Preferred-scope reservoirs, sparse stopping, temporal causal reasoning, general
fusion redesign, temporal substrate changes, persisted inventory changes, Seam
9 cleanup, prompt changes for the frontier model, and plugin work remain out of
scope. Compiler preferred-scope emission is completed only when a live or
controlled compiler UAT demonstrates supported alias emission; runtime scope
semantics remain a separate authority contract.

