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
| Redacted report conceals deterministic mismatches | Aggregate exposes only first-turn plan status | Redacted v3 includes safe per-turn component statuses and case aggregation over every turn |
| Evaluator assumes one compiler call per turn | Production one-shot repair produces a second legitimate observation | Validate initial-plus-repair topology and preserve both attempt contracts |
| Repair hides initial compiler weakness | Final repaired plan makes the initial defect invisible | Record initial and authoritative contract statuses separately, including repair outcome |
| Exact operator-set equality rejects valid plans | Optional operators appear as deterministic failures | Treat required operators as subset containment and expose additional operators |
| Surface string mismatch is mistaken for semantic failure | Equivalent subjects/referents use different wording | Preserve exact surface diagnostics but route mismatches to review-required |
| Mixed evaluator versions resume together | Old raw records are interpreted under new semantics | Store evaluator/report versions in the run manifest and require a fresh suite ID |
| Frozen-surface drift | Seam 0 changes routing, prompts, providers, or retrieval policy | Diff review against authority map; stop if any forbidden surface changes |
| Inventory overclaim | Missing local inventory is reported as healthy | Read-only inspection and explicit unavailable status |

## Stop conditions

Stop and leave Seam 0 in progress for any missing terminal case, persistence failure, unsupported lexical semantic change, forbidden architecture change, or failed machine gate.
