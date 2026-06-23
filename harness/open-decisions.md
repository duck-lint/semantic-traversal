# Open Decisions

This file is the current decision authority for decisions that still matter outside an archived implementation bundle.

Do not use this file as a roadmap. Record only decisions already made, decisions required to continue the current implementation, and explicit user-provided next end goals.

## Current Decisions

No current Decisions.


When a current decision exists, use:

| ID | Decision | Source | Status | Owner | Revisit Trigger |
| --- | --- | --- | --- | --- | --- |

## Pending Decisions

| ID | Question | Boundary | Needed For | Owner | Status |
| --- | --- | --- | --- | --- | --- |
| implementation-01-pd-01 | Which operator-facing caller is authorized to satisfy `accept user_input` for the first build target and to host the two named acceptance probes? | API | Start implementation of the new-thread and continuation-turn runtime path without silently choosing product UX | orchestrator plus user | pending |
| implementation-01-pd-02 | Which persistence representation is authorized for `conversation_thread`, materialized `thread_state`, and append-only hash-chained `thread_ledger` in the first build target? | Storage | Start implementation of thread creation, continuity load/save, and ledger append without implying long-term backend commitments | orchestrator plus user | pending |

When a pending decision exists, use:

| ID | Question | Boundary | Needed For | Owner | Status |
| --- | --- | --- | --- | --- | --- |

## Notes

- Link to archived implementation summaries or decision files when a decision's evidence lives there.
- Do not point active decisions at stale files under `active/` after a bundle has moved to `archive/`.
- Remove decisions that no longer affect current or paused implementation work, or move their final context into the archived bundle summary.
