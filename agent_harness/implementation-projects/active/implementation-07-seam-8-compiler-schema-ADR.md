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

## Completion-repair contract (2026-07-20)

Canonicalization preserves the distinction between an absent planner list, an
explicit valid empty list, and an invalid list type. Missing fields use the
documented fallback and append `defaulted_missing_fields`; explicit empty
lists remain empty and append `explicit_empty_fields`; invalid types append
`invalid_planner_fields` and use the conservative fallback where necessary.
The diagnostic packet also reports `fallback_query_sources` and a bounded
`subject_preservation_status` without claiming semantic truth beyond structural
presence.

The top-level query and non-empty semantic, lexical, and graph query lists are
subject-bearing human-readable retrieval text. Identifier-like or intent-only
query labels receive generic structural subject context when model concepts or
resolved referents are available. Explicit empty lexical and graph lists are
never populated by this repair. Graph execution therefore retains the existing
`skipped_no_input` / `completed_no_candidates` contract.

The supplied live thread `thread-67608780055b` remains operator evidence of
temporal and journal-alias activation but exposed the subject-loss defect. A
fresh repaired runtime replay recovered subject-bearing queries, required
temporal retrieval, vector candidates, and persisted-inventory reuse, while
separate fresh qwen runs varied on whether they emitted the alias and temporal
layer together. Seam 8 completion remains gated on a stable normal live replay;
no query-specific alias or temporal inference is added.

## Live-UAT gate closeout (2026-07-20)

The operator-supplied normal live thread `thread-a16554d9aee8` closes the
remaining Seam 8 gate. It demonstrated persisted inventory source/status
`persisted`/`valid`, snapshot load count `1`, zero full rebuilds, the
subject-bearing query `origin of semantic geometry concept`, the
`personal_reflection` preferred-scope alias, retained concepts/referents,
subject-bearing semantic/lexical queries and graph seed, required ordered
temporal retrieval, and no compiler-owned selection or claim policy.

Runtime completed vector retrieval with `21` candidates and `14` selected
contributions, graph retrieval with `24` and `7`, and temporal retrieval with
`24` and `4`; required temporal contribution was satisfied. The packet held
`24` chunks from `18` notes, with maximum concentration `2`, representing
current conceptual notes, earlier dated personal evidence, graph-linked
material, and external formal sources. Synthesis separated early documented
formulations, later development, external formal reinforcement, and structural
resemblance; it did not assign sole origin to Gärdenfors and limited causal
and exhaustive claims.

Status: accepted and complete for the bounded Seam 8 contract. This stability
gate means the accepted prompt/schema/runtime contract has been demonstrated in
a normal live run and is guarded structurally by focused tests. It does not
mean stochastic model output will be identical on every invocation, and it does
not permit hidden query-specific inference. Preferred admission/reservoir,
configured-maximum filling, and sparse stopping remain deferred observations
or unapproved changes; weak temporal candidates may be reviewed in Seam 9
final UAT but are not a Seam 8 blocker. Seam 9 is next and unopened.

Operator-supplied visual CI evidence is recorded for
`docs(seam8): correct repair test count`, tests #105, commit `bce9270`, passed,
approximately 1 minute 39 seconds. No additional run or job identifier is
claimed. Local closeout verification passed: 53 focused tests, 170 full tests,
compileall, and `git diff --check`. The compiler template, rendered compiler,
frontier, and YAML hashes remain unchanged from `bce9270`; full values are
recorded in the UAT record.
## Post-Seam-9 canonicalization repair (2026-07-20)

The persisted operator payload from `thread-a65a4751fb1a` demonstrated a
generic overlap defect in the accepted Python canonicalizer. Model output was
already subject-bearing, but canonicalization required one concatenated phrase
containing every concept. It consequently appended repeated `regarding`
clauses to valid semantic and lexical queries and to the meaningful graph seed
`Geometry of Meaning`. This was a canonicalization defect, not a prompt,
inventory, retrieval, fusion, temporal, ingest, synthesis, or plugin defect.

The bounded repair retains the accepted prompt/schema contract and implements:

1. ordered, deterministically deduplicated subject candidates with source
   priority for planner referents, planner concepts, top-level referents,
   entities, explicit relation subjects, and only then natural-query/raw-input
   fallback;
2. an overlap-aware minimal subject basis used only when a repair is needed;
3. any-candidate subject detection for semantic and lexical queries;
4. byte-for-byte preservation of already subject-bearing non-empty queries;
5. preservation of meaningful graph seeds without requiring the principal
   subject to occur in the seed;
6. conservative repair or rejection of only empty, identifier-like, or
   intent-only graph seeds; and
7. idempotent canonicalization.

The canonicalizer does not append every concept to an existing query, does not
rewrite graph seeds as prose, and does not introduce query-specific inference.
Diagnostics record the candidate set, minimal basis, overlaps, preserved and
repaired fields, and graph-seed decisions. The persisted payload replay passed
without regenerating candidates. A read-only active-index replay preserved the
original scope and layer requests, returned 50 lexical, 8 vector, 0 graph,
and 10 temporal candidates, and selected 24 chunks from 13 notes. The graph
seed remained usable as a submitted seed; the configured graph index still
returned no match because no exact `Geometry of Meaning` note node exists and
the configured token-overlap threshold is 3. This is recorded as index
evidence, not compensated with a corpus-specific rule.

The direct fresh qwen3:8b compiler probe emitted concise subject-bearing
queries and the unchanged meaningful graph seed, but stochastic output did not
include the temporal layer or a scope alias. The previously accepted normal
live thread `thread-a16554d9aee8` remains the live stability evidence for those
conditions. Stability means the accepted contract is demonstrated in a normal
run and structurally guarded by tests; it does not require identical stochastic
model output and does not authorize hidden query-specific inference.

This is a narrow post-closeout repair. Seam 9's historical machine closeout is
preserved; Implementation 07 is machine-complete/operator-UAT-pending until
new CI and the operator UI gate are supplied. Preferred admission/reservoir,
configured-max filling, sparse stopping, and weak temporal-candidate review
remain deferred. No prompt or YAML hash changed.

The later bounded evidence-requirement repair is the explicit exception to that
historical sentence: the compiler prompt and YAML changed narrowly to expose
and bind `evidence_requirements`; the frontier prompt remained unchanged. The
repair is machine-verified and operator-UAT-pending.

### Post-closeout bounded repair (2026-07-20)

Two generic contract defects were repaired without changing retrieval, fusion,
temporal, inventory, ingest, or frontier behavior. Raw observed inventory
values are no longer accepted as executable scope: only configured YAML
aliases bind hard or preferred scope, while unmatched observed values remain
visible as unauthorized-inventory-scope diagnostics.

The compiler schema now accepts an ordered, deduplicated
`evidence_requirements` list with the enums `literal_exhaustive`,
`lexical_relevance`, `semantic_similarity`, `graph_relation`, and `chronology`.
Runtime binds those enums through the YAML-owned
`retrieval.evidence_requirement_operators` map and validates that every
declared requirement has a corresponding accepted operator. A single bounded
compiler repair may add the missing operator or revise the requirement while
preserving raw input and valid fields. If the repaired plan remains
incomplete, retrieval and synthesis are blocked with structured diagnostics;
there is no all-surfaces fallback. Parsed model fields that omit the new list
remain explicitly empty and diagnosed, rather than acquiring hidden semantic
requirements. Deterministic fallback plans declare their own requirements.

This is a forward-only clean cutover. Superseded observed-value scope binders
and incomplete-plan execution behavior are not retained for compatibility. The
accepted subject-preserving and explicit-empty contract remains intact, and
the frontier prompt remains unchanged.

### Post-Seam-8 runtime-integrity repair (2026-07-21)

Compiler responses now require an exact runtime binding envelope before any
semantic field is canonicalized. One fresh retry uses a new request ID; a
second missing or mismatched binding blocks retrieval and synthesis. The
Ollama backend requests JSON output but returns parsed model content unchanged
so runtime validation precedes normalization.

Conversation context remains available to compiler and frontier synthesis, but
only the current bound plan supplies retrieval coordinates. The YAML graph seed
source list no longer includes `active_focus`; prior selected titles, sections,
chunk IDs, queries, and seeds have no direct executor path. Referential
continuity remains possible only when the current compiler or deterministic
referential fallback materializes compact referents into current plan fields.
This repair does not address entity retrieval correctness.
