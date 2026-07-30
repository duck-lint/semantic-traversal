# Implementation 08 — Seam 0 Plan

## Status

Seam 0: machine-complete / operator-acceptance-pending

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

## Later seams

- Seam 1: provider/control assistant boundary; depends on accepted Seam 0 records and stops at route-schema review.
- Seam 2: production response-mode routing; depends on accepted control schema and stops before traversal execution cutover.
- Later retrieval/context seams: distinct `conversation_focus` and `retrieval_focus`; depend on explicit state migration review.

## Completion rule

Use `Seam 0: machine-complete / operator-acceptance-pending` only after defect reproduction, repair, regressions, evaluation persistence, baseline terminal records, and all machine gates pass. Stop here; do not begin Seam 1.
