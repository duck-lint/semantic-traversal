# Implementation 08 — Seam 0 Risk Ledger

## Status

Seam 0: in progress

| Risk | Failure mode | Mitigation / stop gate |
| --- | --- | --- |
| FTS grammar injection | User/planner text changes FTS meaning or raises SQLite error | One quoted-atom serializer; bound MATCH parameter; structured lexical failure |
| Route/outcome conflation | Blocked traversal is mislabeled as direct or `blocked` mode | Separate `response_mode` and `execution_outcome` in fixtures, records, and metrics |
| False baseline confidence | Final prose is scored without route/plan/evidence evidence | Persist component scores and unsupported-baseline capability explicitly |
| Harness data loss | Timeout/interruption erases earlier cases | Atomic per-case checkpoint; resume without overwrite |
| Private-data leakage | Generic evaluation contains vault-specific material | Synthetic IDs/text only; private UAT is a fillable request |
| Frozen-surface drift | Seam 0 changes routing, prompts, providers, or retrieval policy | Diff review against authority map; stop if any forbidden surface changes |
| Inventory overclaim | Missing local inventory is reported as healthy | Read-only inspection and explicit unavailable status |

## Stop conditions

Stop and leave Seam 0 in progress for any missing terminal case, persistence failure, unsupported lexical semantic change, forbidden architecture change, or failed machine gate.
