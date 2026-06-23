# Open Decisions

This file is the current decision authority for decisions that still matter outside an archived implementation bundle.

Do not use this file as a roadmap. Record only decisions already made, decisions required to continue the current implementation, and explicit user-provided next end goals.

## Current Decisions

ID | Decision | Source | Status | Owner | Revisit Trigger
| --- | --- | --- | --- | --- | --- |
implementation-01-pd-01 | Authorize a local CLI/dev runner as the operator-facing caller for accept_user_input in the first build target. The CLI should call the same underlying runtime function that acceptance probes use. No product UI is authorized for this slice. The OpenAI API key in .env.local authorizes llm_call_boundary only; it does not define the user_input caller. | user confirmed .env.local with OpenAI API key; project_spec first_build_target requires accept user_input and call LLM; PM review asked for caller authorization | decided | user | Revisit when moving from local prototype to persistent app/server UX, or when a UI/API endpoint is intentionally introduced
implementation-01-pd-02 | Persist conversation_thread, materialized thread_state, and append-only hash-chained thread_ledger as local filesystem JSON artifacts for the first build target. Use one thread directory per conversation_thread, with a current thread_state snapshot and append-only perturbation records. Do not introduce SQLite/database persistence until the JSON artifact spine works. | project_spec defines conversation_thread, thread_state, thread_ledger, state_perturbation, hash-chained ledger continuity, and artifact-first runtime flow | decided | user | Revisit when many threads, query needs, concurrency, or retrieval over thread histories makes filesystem JSON insufficient

When a current decision exists, use:

| ID | Decision | Source | Status | Owner | Revisit Trigger |
| --- | --- | --- | --- | --- | --- |

## Pending Decisions

No pending decisions.

When a pending decision exists, use:

| ID | Question | Boundary | Needed For | Owner | Status |
| --- | --- | --- | --- | --- | --- |

## Notes

- Link to archived implementation summaries or decision files when a decision's evidence lives there.
- Do not point active decisions at stale files under `active/` after a bundle has moved to `archive/`.
- Remove decisions that no longer affect current or paused implementation work, or move their final context into the archived bundle summary.
