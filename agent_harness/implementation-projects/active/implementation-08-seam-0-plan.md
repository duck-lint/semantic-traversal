# Implementation 08 — Seam 0 Plan

## Status

Seam 0: operator-accepted

## Intent

Establish the control-plane evaluation boundary without introducing production response-mode routing. Seam 0 repairs only the lexical FTS5 serializer and creates a generic, resumable baseline harness.

## Authority and admissibility

- `stable-runtime` at `ad7c03fec4efcf86b773a2b377ccfada4eb24a67` is the starting authority.
- Runtime/YAML remains authoritative for executable retrieval policy, limits, scope, selection, coverage, claim permission, evidence admission, and state transitions.
- The control assistant classifies/plans; the frontier synthesizer writes the user-visible answer.
- Response mode is only `direct` or `traverse`; execution outcome is separate.
- Implementation 07 retrieval contracts remain frozen except for the explicitly opened FTS5 serializer defect.

## Work in scope

1. Capture the preflight baseline and prompt/config hashes.
2. Reproduce the current FTS5 defect, then serialize supported lexical modes safely.
3. Add generic single-turn and genuine multi-turn evaluation scenarios.
4. Add a bounded, checkpointed baseline harness using controlled planner failure doubles.
5. Run the current `qwen3:8b` baseline where available and persist every terminal case.
6. Verify, commit, push, and stop for operator acceptance.

## Explicit non-goals

Provider abstraction, planner bakeoff, production direct/traverse routing, direct mode, control-packet cutover, focus migration, inventory/schema work, structured facts, candidate adjudication, adaptive retrieval, and any frozen retrieval/synthesis boundary.

## Later dependency order

1. Provider-neutral control boundary and planner-model comparison; depends on accepted Seam 0 evidence.
2. Canonical control/response-mode schema; depends on the provider-neutral boundary and comparison evidence.
3. Production direct/traverse routing; depends on the canonical schema and runtime validation review.
4. Conversation-context packet cleanup and the explicit `conversation_focus` / `retrieval_focus` split; depends on production routing and state-migration review.

The focus objects do not own routing. `conversation_focus` supplies bounded conversational context; `retrieval_focus` supplies bounded referents from the last successful approved traversal. Runtime owns whether either focus transition is accepted. Blocked turns preserve both; fallback plans and unapproved candidates update neither. None of these later seams is implemented here.

## Completion rule

The correction pass, replacement baseline, and complete verification pass are complete. Seam 0 is operator-accepted; stop here and do not begin Seam 1.
