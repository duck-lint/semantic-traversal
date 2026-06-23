# Implementation 01 Plan

## Intent

Create the first active implementation bundle for `implementation_boundary.first_build_target` only. The implementation this bundle authorizes must be limited to creating a `conversation_thread`, accepting `user_input`, loading `prior_thread_state`, assembling a basic `synthesis_context_packet`, calling the LLM boundary, saving `assistant_response`, materializing `next_thread_state`, and appending a hash-chained `thread_ledger` record.

The smallest implementation seam authorized by this bundle is now explicit: one local CLI/dev runner as the operator-facing caller, backed by local filesystem JSON artifacts for `conversation_thread`, materialized `thread_state`, and append-only hash-chained `thread_ledger`, all wired through the same underlying runtime function that the acceptance probes will exercise.

Delivery posture for this bundle is conditional:

- report `live-wired` only if the local CLI/dev runner exercises a real LLM call boundary and the two named acceptance probes pass
- report `scaffold-only` if the path stops at stubs, fixtures, dry runs, or an unavailable LLM boundary

## Admissibility Report

- Invariant constraints:
  - `harness/project-spec/project_spec_0.1.2.json` is the only invariant authority.
  - `implementation_boundary.first_build_target` is limited to thread creation, minimal continuity loading, basic synthesis-context assembly, one LLM call, response persistence, next-state materialization, and append-only hash-chained ledger append.
  - `thread_ledger` must remain append-only and hash-chained.
  - `thread_state` must preserve enough continuity for the next fresh LLM call even if the first projection is minimal.
  - Traversal, retrieval, latent-space activation, graph work, synthetic-node promotion, and coverage-benchmark math are out of scope.
- Task constraints:
  - Implementation is now requested, but this planner refresh may edit only the owned bundle surfaces.
  - Caller and storage decisions are resolved in `harness/open-decisions.md` and must be treated as current task authority.
  - Reuse of the existing local `OPENAI_API_KEY` is authorized for this implementation run.
  - The implementation handoff must stay minimal and local.
  - Preserve the named user-facing acceptance probes and the `scaffold-only` downgrade rule.
- Constraint conflicts:
  - No remaining approval gap exists for the operator-facing caller or the local persistence spine.
  - The prior bundle state was stale because the plan and tracker described those settled decisions as blockers; this refresh reconciles that drift.
  - Repo hygiene may still require an ignored runtime-artifact path or a later `.gitignore` update if probe artifacts are written inside the repo.
- Allowed transformation types:
  - Refresh `harness/implementation-projects/active/implementation-01-plan.md`.
  - Refresh `harness/implementation-projects/active/implementation-01-tracker.md`.
  - Clarify the smallest implementation seam, generated-artifact expectations, and implementer handoff.
  - Do not edit implementation code, tests, `harness/open-decisions.md`, or the project spec in this turn.
- Affected surfaces:
  - `harness/implementation-projects/active/implementation-01-plan.md`
  - `harness/implementation-projects/active/implementation-01-tracker.md`
- Non-affected surfaces:
  - `harness/open-decisions.md`
  - `harness/project-spec/project_spec_0.1.2.json`
  - `harness/harness-runtime.md`
  - `harness/sub-agents.md`
  - `harness/archive-policy.md`
  - `harness/canon/bridge-schema.md`
  - `harness/canon/type-system-operational.md`
  - `.gitignore`
  - all non-harness product, runtime, test, schema, and deployment surfaces
- Admissibility checks:
  - The bundle must stay inside the first build target and not expand into retrieval, traversal, graph, or synthetic-node work.
  - The bundle must explicitly authorize the bounded local CLI/dev runner plus filesystem JSON artifact seam and no broader product surface.
  - The bundle must name one new-thread probe and one same-thread continuation probe.
  - The bundle must keep `thread_ledger` append-only and hash-chained as a non-negotiable invariant.
  - Any inability to execute a real LLM call boundary through the local CLI/dev runner must downgrade the eventual implementation result to `scaffold-only`.
- Stop conditions:
  - Stop if implementing the seam would require reopening the settled caller or storage decisions.
  - Stop if implementation would require a broader UI, remote API contract, database/storage migration, or durable external schema beyond current authority.
  - Stop if keeping runtime artifacts local would require editing `.gitignore` or another non-authorized surface before a truthful first slice can be run.
  - Stop if the bundle expands beyond the first build target.
  - Stop if the acceptance probes cannot be stated in falsifiable operator-facing terms.

## Observed Evidence

- `harness/implementation-projects/active/` contains the live `implementation-01` plan and tracker bundle.
- `harness/open-decisions.md` records two current decided items: the local CLI/dev runner for the operator-facing caller and local filesystem JSON artifacts for `conversation_thread`, `thread_state`, and `thread_ledger`.
- `harness/open-decisions.md` contains no pending decisions.
- `harness/project-spec/project_spec_0.1.2.json` explicitly enumerates the first build target and defers retrieval, traversal, graph, synthetic-node, and long-term compression questions.
- The bundle required refresh because the prior tracker state reported the two now-decided items as blockers.
- `harness/README.md`, `harness/harness-runtime.md`, and `harness/sub-agents.md` require one live numbered bundle, explicit acceptance probes, and no silent override of spec authority.
- `.gitignore` currently ignores `.env.*` only, so runtime-artifact hygiene is not yet pre-authorized by ignore rules.

## Planned Work

1. Seam 1: local CLI plus filesystem JSON artifact spine
   - Implement one local CLI/dev runner as the only operator-facing caller for this slice.
   - The CLI must call the same underlying runtime function that the acceptance probes will exercise.
   - Persist one thread directory per `conversation_thread` using local filesystem JSON artifacts.
   - The minimum persisted surfaces are: visible thread identity and transcript continuity for `conversation_thread`, one current materialized `thread_state` snapshot or equivalent current-state lookup, and append-only `thread_ledger` records with parent-hash linkage.
   - Exact file and module names remain an implementer choice; the seam authorizes the artifact roles, not a fixed filename contract.
2. Seam 2: new-thread minimal live turn
   - Through that CLI/runtime path, implement the first-turn flow only as far as the first build target requires: create thread, accept user input, load empty or initial prior state, assemble a basic `synthesis_context_packet`, call the authorized LLM boundary, save the assistant response, materialize the next state, and append the first ledger record.
   - Keep the context packet basic and continuity-oriented; no traversal, retrieval, or synthetic-node artifacts are required in this slice.
3. Seam 3: same-thread continuation plus probe verification
   - Reuse the same CLI/runtime path for a second turn on the same thread.
   - Load prior state and parent hash from the JSON artifact spine, call the LLM boundary again, persist the second response, materialize the updated state, and append exactly one new ledger record whose parent hash matches the first record hash.
   - Run the named acceptance probes through the real CLI caller.
   - If the LLM boundary is stubbed, unavailable, or cannot run through the CLI path, report the implementation result as `scaffold-only` and record the missing live dependency explicitly.

## Non-Goals

- semantic traversal, retrieval packet assembly, coverage evaluation, graph expansion, or latent-space activation
- synthetic-node candidate handling or promotion back into latent space
- multi-thread interaction, branching, or long-term thread-state compression
- broader product UX, remote API contract design, deployment, auth, billing, or storage migrations
- compatibility shims for future callers not required by the first build target
- fixing repo-wide runtime-artifact hygiene beyond the minimum needed to run this slice truthfully

## Delivery Posture And Acceptance Criteria

Named user-facing acceptance probes for the future implementation:

1. `probe_new_thread_minimal_turn`
   - Through the local CLI/dev runner, submit a first user message without an existing thread.
   - Expected observable result: a new `conversation_thread` is created, a real LLM call returns an `assistant_response`, a persisted `next_thread_state` exists for that thread, and the `thread_ledger` contains a first append-only record with a valid root or null parent position and a stored self hash.
2. `probe_same_thread_continuation_turn`
   - Through the same CLI/dev runner, submit a second user message against the previously created thread.
   - Expected observable result: the runtime loads the prior materialized `thread_state`, the LLM call returns a second `assistant_response`, a new persisted `next_thread_state` replaces the previous current state for that thread, and the `thread_ledger` appends exactly one new record whose parent hash matches the first record hash.

Failure conditions that block completion:

- either probe can run only against fixtures, mocks, or dry-run paths
- the continuation turn does not read prior state from the same thread
- the ledger is rewritten, mutated in place, or appended without a valid parent-hash link

## Current Repo Runtime State

- The repo currently exposes harness surfaces only; no runtime implementation surfaces are present yet.
- `implementation-01` is the single active implementation bundle.
- `harness/open-decisions.md` now authorizes the local CLI/dev runner and the local filesystem JSON artifact spine for this bundle.
- Current task authority also authorizes reuse of the existing local `OPENAI_API_KEY` for this implementation run.
- Availability of a real LLM boundary at probe time remains a verification question, not an approval gap.

## Assumptions And Unknowns

Assumptions:

- The first build target can use a minimal `thread_state` projection as long as it preserves continuity for the next fresh LLM call.
- The initial implementation should stay on the authorized narrow local CLI/dev runner instead of inventing another caller.
- A reversible local filesystem JSON representation is preferable to a durable architecture commitment at this stage.

Unknowns:

- Exact file names, module names, and runtime-artifact paths the implementer will choose.
- Whether the chosen artifact path can stay local without requiring a `.gitignore` update or other repo-hygiene follow-up.
- Whether the implementation environment will support a real LLM boundary during verification or force a `scaffold-only` downgrade.

## Affected And Non-Affected Surfaces

Planning surfaces changed in this bundle:

- `harness/implementation-projects/active/implementation-01-plan.md`
- `harness/implementation-projects/active/implementation-01-tracker.md`

Runtime surfaces that must eventually move together for the first build target to be truthful:

- one bounded local CLI/dev runner entry point
- one underlying runtime function shared by the CLI caller and the named probes
- conversation-thread creation and lookup path
- local filesystem JSON load and save path for materialized `thread_state`
- append-only hash-chained local filesystem JSON ledger append path
- basic synthesis-context assembly path
- LLM call boundary
- verification surface for the two named probes

Runtime surfaces that must not move in this bundle:

- latent-space ingestion and indexing
- traversal, retrieval, coverage, and graph surfaces
- synthetic-node write-back
- project spec, archive surfaces, and known-failures surfaces

## Verification Contract Summary

- Structural verification:
  - confirm the chosen implementation preserves append-only ledger semantics and parent-hash linkage across at least two turns
  - confirm the thread surface can reload the latest persisted state for the continuation probe
- Behavior verification:
  - run `probe_new_thread_minimal_turn`
  - run `probe_same_thread_continuation_turn`
- Downgrade rule:
  - if the implementation path cannot exercise a real LLM boundary through the authorized local CLI/dev runner, mark behavior as `scaffold-only` and report the missing dependency explicitly
- Review obligation:
  - reviewer must reject any implementation claim that is based only on types, files, serialization, tests, or dry-run traces without a passing named acceptance probe

## Completion Rule

- Do not mark behavior complete on fixture, mock, dry-run, serialization, type, field, file, path, route, crate, config, or nominal-caller evidence alone.

## Approval Gates

- [ ] Schema
- [ ] API
- [ ] Auth
- [ ] Storage
- [ ] Deployment
- [ ] Destructive operation
- [ ] Broad architecture
- [ ] Project-intent authority not covered by spec or current authorization

Gate notes:

- No current approval gate remains for the local CLI/dev runner or the local filesystem JSON persistence spine because both are now explicitly authorized in `harness/open-decisions.md`.
- Raise a new gate only if implementation expands beyond the local caller, the local JSON artifact spine, or the first build target.
- Schema becomes a gate only if the implementer proposes a durable external contract rather than a narrow local prototype surface.

## Handoff Packet For Next Agent

## Role

- From: planner
- To: implementer
- Requested action: implement the smallest first-build-target seam using the authorized local CLI/dev runner and local filesystem JSON artifact spine, then validate the two named probes truthfully

## Project And Task

This repo is still harness-only. `implementation-01` is the single active bundle and is bounded to the spec's `implementation_boundary.first_build_target`. Caller and storage authority are now settled: use the local CLI/dev runner as the operator-facing caller and local filesystem JSON artifacts for `conversation_thread`, materialized `thread_state`, and append-only hash-chained `thread_ledger`. The implementer must keep the slice minimal and local, not widen it into a product UI, remote API, or storage redesign.

## Admissibility Report

- Invariant constraints:
  - first build target only
  - append-only hash-chained ledger
  - next-call continuity via `thread_state`
  - no traversal, retrieval, graph, or synthetic-node work
- Task constraints:
  - implementation is authorized only for the bounded local CLI plus JSON artifact seam
  - explicit acceptance probes
  - no silent UI, storage, or artifact-hygiene expansion beyond current authority
- Constraint conflicts:
  - no remaining caller or storage approval gap
  - runtime-artifact hygiene may require escalation if the chosen path would need `.gitignore` edits
- Allowed transformation types:
  - create the local CLI/dev runner and the minimum runtime/artifact surfaces needed for the two probes
- Affected surfaces:
  - one local CLI/dev runner surface
  - one shared runtime function for turn execution
  - local filesystem JSON artifacts for `conversation_thread`, `thread_state`, and `thread_ledger`
  - the minimal LLM-boundary integration needed to support the named probes
- Non-affected surfaces:
  - traversal, retrieval, graph, synthetic-node, deployment, and broader product surfaces
- Admissibility checks:
  - bundle stays inside first build target
  - probes are falsifiable and operator-facing
  - the implemented caller and persistence path match the decided local CLI plus JSON artifact seam
- Stop conditions:
  - stop if implementation would widen beyond the local CLI/dev runner, the local JSON artifact spine, or the first build target
  - stop if a truthful run would require editing `.gitignore` or other non-authorized surfaces before the named probes can run

## Authorized Boundaries

- Affected surfaces:
  - implementation surfaces required for the local CLI/dev runner, shared runtime function, local JSON persistence spine, and minimal LLM call path
- Non-affected surfaces:
  - everything outside that bounded first-target seam
- Boundaries not authorized:
  - traversal, retrieval, graph, synthetic-node, deployment, broader product UX, storage redesign, or non-essential repo-hygiene changes

## Evidence And Assumptions

- Observed evidence:
  - active bundle exists and was stale relative to the now-decided `harness/open-decisions.md`
  - `harness/open-decisions.md` authorizes the local CLI/dev runner and local filesystem JSON artifact spine
  - first build target is explicitly listed in the project spec
- Inferences:
  - implementation can begin after this bundle refresh
  - the narrowest truthful first slice is the local caller plus JSON artifact spine that supports both named probes
- Unknowns:
  - exact file and module names
  - exact runtime-artifact path
  - live LLM-boundary availability for verification

## Expected Change

Implementer should build only the authorized local caller plus JSON artifact seam, then return the concrete changed surfaces, checks run, probe results, and any downgrade or hygiene blocker instead of widening scope.

## Acceptance Criteria

The implementer completes the handoff only if it:

- stays fully inside the first build target
- names both user-facing probes
- uses the authorized local CLI/dev runner and local filesystem JSON artifact spine
- keeps `scaffold-only` as the required downgrade when a live LLM path cannot run

## Stop Conditions

Acceptance criteria achieved, or a blocker is reported without widening scope.

## Closeout Note

- When this bundle completes, move it from `active/` to `archive/`.
