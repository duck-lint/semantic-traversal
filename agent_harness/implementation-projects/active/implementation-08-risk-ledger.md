# Implementation 08 — Seam 0 Risk Ledger

## Retrieval-surface completeness risks

| Risk | Failure mode | Mitigation |
| --- | --- | --- |
| Inventory advertises unreachable surfaces | Compiler requests a component or operator the runtime cannot execute | Generate one closed-world manifest from live index/configuration facts and validate it before activation |
| Frontmatter links disappear | Authored metadata links never become graph relations | Recursively scan admitted metadata with the existing wikilink parser |
| Backlinks become fake stored edges | Reverse traversal is mistaken for reverse authorship | Keep one directed authored edge and record navigational direction only in hop provenance |
| Duplicate authored links inflate ranking | Multiple occurrences create equivalent edges | Canonicalize by source/target/type and merge deterministic provenance |
| Non-admitted metadata leaks | Raw frontmatter enters retrieval surfaces | Derive metadata surfaces from the admitted semantic projection only |
| Manifest budget hides capabilities | Budget reduction omits operational truth | Preserve mandatory manifest structure and fail explicitly when it cannot fit |
| Stale index after surface change | Reingest leaves old FTS or graph projections active | Require complete staged reingest and atomic validation/activation |
| Manifest becomes hidden ontology | Capability facts turn into field-specific routing | Keep the manifest generic: components, operators, modes, relations, and execution boundaries only |

## Status

Seam 0: operator-accepted

| Risk | Failure mode | Mitigation / stop gate |
| --- | --- | --- |
| FTS grammar injection | User/planner text changes FTS meaning or raises SQLite error | One quoted-atom serializer; bound MATCH parameter; structured lexical failure |
| Route/outcome conflation | Blocked traversal is mislabeled as direct or `blocked` mode | Separate `response_mode` and `execution_outcome` in fixtures, records, and metrics |
| False baseline confidence | Final prose is scored without route/plan/evidence evidence | Persist component scores and unsupported-baseline capability explicitly |
| Harness data loss | Timeout/interruption erases earlier cases | Atomic per-case checkpoint; resume without overwrite |
| Private-data leakage | Generic evaluation contains vault-specific material | Synthetic IDs/text only; private UAT uses one ignored versioned fixture and private raw checkpoints |
| Parsed JSON mistaken for contract validity | Off-contract objects become canonical fallback plans and appear healthy | Record raw JSON status, exact compiler contract status, missing/unexpected allowlisted fields, canonicalization, and fallback use separately |
| Fallback masks compiler failure | Executable default plan hides malformed semantic compilation | Treat raw planner absence plus populated canonical plan as fallback use; contract failure independently fails evaluation |
| Generic coverage mistaken for negative authorization | `coverage_report.decision: approved` is treated as permission for corpus-wide absence | Read runtime `negative_claims_allowed` and exact manifest execution/status/scope/term adequacy separately |
| Runtime completion mistaken for evaluation success | Terminal answer is displayed as an overall pass | Preserve runtime status and compute independent `evaluation_status`; unavailable adjudication yields `review_required` |
| Case-global expectations corrupt multi-turn evaluation | Setup turn is scored against continuation expectations | Schema v2 requires one complete expectation object inside every turn |
| Redacted report conceals deterministic mismatches | Aggregate exposes only first-turn plan status | Redacted v5 includes safe per-turn component statuses and case aggregation over every turn |
| Evaluator assumes one compiler call per turn | Production one-shot repair produces a second legitimate observation | Validate initial-plus-repair topology and preserve both attempt contracts |
| Repair hides initial compiler weakness | Final repaired plan makes the initial defect invisible | Record initial contract status separately from final response attempt and retained plan source |
| Exact operator-set equality rejects valid plans | Optional operators appear as deterministic failures | Treat required operators as subset containment and expose additional operators |
| Surface string mismatch is mistaken for semantic failure | Equivalent subjects/referents use different wording | Preserve exact surface diagnostics but route mismatches to review-required |
| Mixed evaluator versions resume together | Old raw records are interpreted under new semantics | Store evaluator/report versions in the run manifest and require a fresh suite ID |
| Missing repair response is misclassified as malformed output | Timed-out or unavailable backend has no raw response | Use contract `unavailable`, preserve backend status, and never synthesize a hash |
| Final attempt is conflated with retained plan source | Failed repair preserves the initial packet | Record `final_response_attempt` and `plan_source_attempt` independently and compare retained packet data deterministically |
| Compiler timeout aborts all later UAT cases | A structured blocked turn stops the whole suite | Atomically record the failed turn/case and continue only to independent later cases |
| Hash validation is relaxed too broadly | Missing hashes are accepted for responses that contain raw text | Require exact hash matching whenever raw text exists; allow hash absence only for validated unavailable attempts |
| Frozen-surface drift | Seam 0 changes routing, prompts, providers, or retrieval policy | Diff review against authority map; stop if any forbidden surface changes |
| Inventory overclaim | Missing local inventory is reported as healthy | Read-only inspection and explicit unavailable status |
| Semantic metadata drift | Admitted metadata changes without vector invalidation, or metadata-bearing retrieval cannot be shown to synthesis | One canonical admitted chunk surface; deterministic identity and synthetic reingest checks |
| Projection disagreement | One retrieval layer hydrates a different chunk representation | Hydrate all candidate layers from stored canonical chunk rows and preserve structured metadata in the selected packet |
| Raw frontmatter leakage | Non-admitted frontmatter reaches embeddings, inventory, or synthesis | YAML admission remains the sole field authority; synthetic redaction/projection tests |
| Serialization instability | Equivalent metadata ordering changes chunk identity | Stable recursive JSON serialization with explicit field boundaries |
| Overbroad exact-scope normalization | Restricted or incomplete exact search is treated as corpus-wide coverage | Normalize only unrestricted filters; retain execution, exhaustiveness, term adequacy, and negative authorization separately |
| One-time vector regeneration | Canonical embedding input changes existing vector-source hashes | Document required post-merge complete reingest; unchanged semantic chunks remain reusable |
| Compiler imitates inventory schema | Malformed compiler output mirrors inventory vocabulary or capability mapping instead of the canonical plan | Give the compiler one compact descriptive projection and preserve the canonical response schema unchanged |
| Prompt inventory duplication | Full inventory appears both at its template marker and inside the serialized packet | Use a prompt-only packet copy without the already-rendered inventory field; assert single-copy rendering |
| High-cardinality value flooding | UUID-like or otherwise large observations consume compiler context | Apply generic observed-cardinality and configured per-field/global bounds; retain field names while omitting values |
| Projection truncates semantic surface | Budget reduction removes an admitted field name or required capability | Preserve mandatory structural keys and every admitted field name before trimming optional values/paths; fail clearly if mandatory content cannot fit |
| Projection becomes resolver authority | Compact compiler view silently limits executable scope or inventory validation | Keep full persisted inventory in resolver, traversal, manifests, and deep validation paths |
| Initial/repair projection disagreement | Repair receives a different inventory view and is measured against a different compiler context | Reuse the same request projection and projection hash for both production calls |
| Projection controls invalidate inventory | Compiler-input tuning changes persisted inventory policy or forces reingest | Keep projection controls under semantic_compiler and exclude them from inventory policy hashing |
| Projection diagnostic leakage | Omitted values or full inventory content escapes through redacted UAT output | Record only safe projection sizes, counts, status enums, and hashes; retain no raw projection values in reports |
| Resolved subjects disappear in binding | Compiler referents are omitted from the bound plan before temporal execution | Compare canonically coerced planner and bound lists at the runtime seam; preserve order and block required temporal execution on mismatch |
| Anonymous fallback masquerades as subject coverage | An empty-string query context is counted as a named subject | Keep query-context count separate; count satisfied and missing subjects only from nonempty referents |
| Synthetic temporal test bypasses production seam | Direct executor tests pass while compiler-to-binder propagation remains broken | Require an end-to-end synthetic path through canonical plan, binder, `_semantic_traversal`, executor, manifest, and packet |
| Same-chunk provenance is lost during temporal deduplication | First subject wins when one chunk supports multiple subjects | Merge temporal subject and provenance records before final chunk selection without duplicating the chunk |
| Unrelated global evidence appears corrective | Other retrieved evidence makes final prose look valid while intended temporal context is broken | Assert independent relevance admission, per-subject anchors, and exclusion of globally earlier unrelated chunks |
| Lexical multi-query flattening | Multiple lexical queries lose some subject separation | Record as deferred known weakness; do not redesign lexical retrieval in this correction |

## Typed-closure execution correction risks

| Risk | Failure mode | Mitigation |
| --- | --- | --- |
| Automatic layer bypass | Runtime-expanded graph support receives zero depth or inconsistent limits | Requested and automatic layers share one canonicalization helper and expose requested/effective diagnostics |
| Identity re-materialization | Grounded notes are title/path strings and fuzzy-rematched | Canonical `note_id` seeds hydrate directly; text matching remains only for unresolved compiler seeds |
| Seed/traversal conflation | Diagnostics call seed hydration a graph hop or claim edges were traversed | Record canonical seed hydration separately from expanded/traversed notes and hop provenance |
| Contextual exact overreach | Optional contextual atoms authorize exhaustive or negative claims | Runtime probes are automatic, non-required, non-exhaustive, and provenance-linked to their source atom |
| Lexical atom flattening | Independent queries become one shared FTS expression | Execute each query separately and preserve per-query provenance before deterministic merge |
| Production seam bypass | Synthetic tests pass only by injecting graph or temporal candidates | End-to-end synthetic coverage passes through `_semantic_traversal`, graph tables, closure, manifest, and selected packet |

## Stop conditions

Stop and leave Seam 0 in progress for any missing terminal case, persistence failure, unsupported lexical semantic change, forbidden architecture change, or failed machine gate.
# Kernel correction risks

Added risks: chronology before semantic admission; global earliest candidates displacing required subjects; one subject satisfying another; missing subjects hidden by a nonempty global pool; aliases reappearing under another name; alias removal permitting model-generated filters; legacy state failing to load; subject provenance disappearing during deduplication; requested limits dropping a comparison subject; and redacted context diagnostics leaking private referents.

Mitigation for model-generated filters: the compiler schema contains semantic queries and operators, not database filters. No speculative parser, sanitizer, or policy layer was added.

## Typed semantic closure risks

| Risk | Failure mode | Mitigation / stop gate |
| --- | --- | --- |
| Isolated manifest surfaces | Inventory lists operators but omits valid transitions | Persist and validate one generated finite semantic-space grammar |
| Compiler omission suppresses support | Chronology-only plans prevent useful grounding | Preserve required layers while adding optional compatible direct surfaces |
| Referent over-admission | Subject identity is mistaken for broad relevance | Require non-referent probe, canonical identity, or grounded graph provenance |
| Temporal restart | Chronology forms a new full-corpus relevance pool | Evaluate only anchored candidates already in contextual closure |
| Graph evidence stranded | Reached notes cannot participate in later relations | Hydrate canonical units and carry route/subject provenance into evaluators |
| Unbounded closure | Retrieved prose becomes recursive search input | Use finite declared transitions, visited identities, graph depth, and existing caps |
| Duplicate-route inflation | Convergent routes multiply rank or selected chunks | Merge by canonical chunk identity and preserve all provenance |
| Grammar drift | Manifest declares a transition without an executor | Validate transition pairs during staged ingest and projection construction |
| Stale private substrate | UAT runs against old inventory or indexes | Preflight persisted source, validity, versions, policy hash, and index status |

## Semantic grounding authority risks

| Risk | Failure mode | Mitigation / stop gate |
| --- | --- | --- |
| Relevance mistaken for identity | A prose, lexical, or vector match turns its parent note into the subject object | Require canonical identity surfaces or explicit graph seeds for object identity |
| Parent promotion | A child evidence match authorizes the parent note as the subject object | Keep evidence-unit grounding distinct from object-identity grounding |
| Unauthorized graph propagation | A broad seed causes every neighbor to inherit every named subject | Propagate only recorded subject authority across authored graph hops |
| Referent mistaken for predicate | A subject string alone satisfies a relation evaluator | Require non-referent context atoms for proposition grounding |
| Anchor mistaken for proposition | A dated note enters chronology solely because it has an accepted anchor | Temporal admission consumes proposition-grounded candidates only |
| Descriptive exact-string bias | Supporting evidence with different wording is rejected | Permit descriptive proposition grounding through associated context without fabricating an object |
| Weak evidence discarded | Non-proposition evidence disappears before synthesis | Retain evidence-unit grounding while excluding it from required relation pools |
| Structured/boolean divergence | A compatibility flag becomes a second authority model | Derive compatibility booleans from structured assessments and never branch on them |
| Temporal ownership drift | Temporal code begins interpreting subject identity or predicate meaning | Keep semantic classification in the grounding layer and temporal parsing/order in `temporal.py` |
| Hidden ontology | Generic runtime rules acquire field-specific or corpus-specific meaning | Stop on aliases, field mappings, query recipes, domain predicates, or model-directed grounding |

## Subject–predicate and canonical-link risks

| Risk | Failure mode | Mitigation |
| --- | --- | --- |
| Subject literals become shared predicates | A referent-only atom satisfies another subject's relation | Explicit atom roles; subject-only atoms cannot ground predicates |
| Subject spans satisfy predicate residuals | Matching the name is treated as matching the remaining query | Remove exact spans and evaluate only nonempty residuals |
| Cross-subject contamination | Shared predicate evidence is attached to every subject without subject support | Independent subject-bearing and predicate assessments |
| Source/target identity collapse | A note containing a wikilink is treated as the linked object | Promote only the resolved target note ID; retain source as evidence |
| Unresolved-link fabrication | An unresolved target is fuzzy-matched to a note | Preserve occurrence intent without canonical identity |
| Link-form drift | Aliases, fragments, embeds, or frontmatter paths lose target provenance | Canonical occurrence model with body/frontmatter parity |
| Graph metadata loss | Occurrence provenance disappears during canonical edge deduplication | Merge occurrence records in existing edge metadata; require reingest |
| Fuzzy target promotion | Target identity is serialized back into title/path text | Direct target note ID and graph node ID promotion |
| Compatibility authority drift | Legacy booleans override structured grounding | Derive compatibility fields after authoritative merge |
| Stale grounding counts | Route-local assessments disagree with aggregate diagnostics | Count canonical chunk/note identities from merged candidates |

## Subtractive grounding and exact-contract risks

| Risk | Failure mode | Mitigation |
| --- | --- | --- |
| Historical truncation compensation | Old under-context planner behavior drives new runtime heuristics | Use only the post-PR-23 full-context baseline for planner corrections; keep compiler prompt/model/context frozen |
| Query bundle treated as named object | Subject coverage gate rejects valid subjectless context | Admit query-level proposition from matched query context; preserve query-level-only graph status |
| Subject-and-predicate broadcast | One subject's residual predicate is shared with unrelated subjects | Share only predicate-only atoms; assign residuals only to explicit subject IDs |
| Boolean merge authority | Route union pairs one subject's evidence with another's predicate | Merge tagged per-subject evidence and recompute eligibility for the same subject |
| Typed evidence discarded | Authored relation is rejected because prose lacks a repeated predicate | Consume existing resolved frontmatter/link/graph provenance before considering a bridge |
| Arbitrary typed-link admission | Every resolved link becomes relation truth | Require resolved canonical target, admitted field path, source unit, authorized subject, and authored graph-edge provenance |
| Bare literal coercion | Required exact contract becomes optional substring search | Preserve raw entry type, mark incomplete, and invoke the existing one-shot repair |
| Automatic exact masking explicit failure | Context probes make a malformed or unmet required literal look complete | Keep explicit required status and automatic support status separate |
| Compatibility authority drift | Legacy booleans create a second grounding model | Use structured per-subject records for all authority decisions; retain booleans only as derived diagnostics |
| Gate-removal overgrowth | New ontology, matcher, or field map is added where information already exists | Record provenance audit and stop if a generic bridge cannot be justified |
| Proposition/evidence contradiction | Proposition count exceeds evidence-unit count | Deterministic invariant diagnostic and required-evidence rejection |

## Descriptive grounding, exact repair, and thread-isolation risks

| Risk | Failure mode | Mitigation |
| --- | --- | --- |
| Descriptive subject over-gating | A single descriptive subject is rejected because its exact phrase is absent from the candidate | Admit associated predicate/context evidence while retaining no-object-identity and no-graph-seed safeguards |
| Exact-layer mode authority drift | A mode label blocks an otherwise executable exact retrieval contract | Preserve the raw mode only as diagnostic metadata; do not treat it as an execution requirement |
| Incomplete exhaustive exactness | Optional literals, automatic probes, missing totals, or scoped searches are reported as exhaustive | Require the existing explicit literal, required status, executable matching, unrestricted scope, and total-count contract |
| Repair success unproven | A repair response is accepted without proving the repaired exact plan executes | Production-shaped regression runs the initial malformed plan, one repair, and the repaired exact retrieval |
| Repair diagnostic split-brain | Initial-plan diagnostics overwrite the repaired final plan | Preserve final completeness and literal-contract diagnostics consistently across packet, compiler, manifest, and coverage artifacts |
| False retrieval implication | A structurally blocked plan is described as having found no matches | Use explicit non-execution wording; reserve insufficient-evidence wording for retrieval that actually ran |
| Contaminated deterministic thread | Reused private suite state changes later model calls or outcomes | Read-only preflight all pending deterministic threads before backend resolution; require a fresh suite ID |
| Destructive replace | `--replace` silently clears conversational state | Replace only run artifacts; never reset or delete thread state |
| Private-state disclosure | Preflight diagnostics expose message or turn content | Emit only clean/contaminated status and counts; never serialize thread payloads |
| Historical truncation compensation | Prior under-context behavior drives new planner heuristics | Keep compiler prompt/model/provider/context frozen and make no historical-UAT-derived compensation |
