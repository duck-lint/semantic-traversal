# Implementation 08 — Seam 0 Risk Ledger

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

## Stop conditions

Stop and leave Seam 0 in progress for any missing terminal case, persistence failure, unsupported lexical semantic change, forbidden architecture change, or failed machine gate.
# Kernel correction risks

Added risks: chronology before semantic admission; global earliest candidates displacing required subjects; one subject satisfying another; missing subjects hidden by a nonempty global pool; aliases reappearing under another name; alias removal permitting model-generated filters; legacy state failing to load; subject provenance disappearing during deduplication; requested limits dropping a comparison subject; and redacted context diagnostics leaking private referents.

Mitigation for model-generated filters: the compiler schema contains semantic queries and operators, not database filters. No speculative parser, sanitizer, or policy layer was added.
