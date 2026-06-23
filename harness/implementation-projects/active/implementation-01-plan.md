# Implementation 01 Plan

## Intent

Create the first active implementation bundle for `implementation_boundary.first_build_target` only. The implementation this bundle authorizes must be limited to creating a `conversation_thread`, accepting `user_input`, loading `prior_thread_state`, assembling a basic `synthesis_context_packet`, calling the LLM boundary, saving `assistant_response`, materializing `next_thread_state`, and appending a hash-chained `thread_ledger` record.

Delivery posture for this bundle is conditional:

- report `live-wired` only if the chosen operator surface exercises a real LLM call boundary and the two named acceptance probes pass
- report `scaffold-only` if the path stops at stubs, fixtures, dry runs, or an unavailable LLM boundary

## Admissibility Report

- Invariant constraints:
  - `harness/project-spec/project_spec_0.1.2.json` is the only invariant authority.
  - `implementation_boundary.first_build_target` is limited to thread creation, minimal continuity loading, basic synthesis-context assembly, one LLM call, response persistence, next-state materialization, and append-only hash-chained ledger append.
  - `thread_ledger` must remain append-only and hash-chained.
  - `thread_state` must preserve enough continuity for the next fresh LLM call even if the first projection is minimal.
  - Traversal, retrieval, latent-space activation, graph work, synthetic-node promotion, and coverage-benchmark math are out of scope.
- Task constraints:
  - Planning only in this turn.
  - Use the harness templates and keep edits inside the owned planning surfaces.
  - Define falsifiable user-facing acceptance probes for a new thread and a continuation turn.
  - Do not silently decide product UI, API, storage backend, or durable schema shape as settled architecture.
- Constraint conflicts:
  - No invariant conflict is currently visible.
  - Authority is incomplete for the operator-facing `accept user_input` surface and the persistence representation for `conversation_thread`, `thread_state`, and `thread_ledger`.
- Allowed transformation types:
  - Create `harness/implementation-projects/active/implementation-01-plan.md`.
  - Create `harness/implementation-projects/active/implementation-01-tracker.md`.
  - Update `harness/open-decisions.md` with pending decisions required to start implementation truthfully.
- Affected surfaces:
  - `harness/implementation-projects/active/implementation-01-plan.md`
  - `harness/implementation-projects/active/implementation-01-tracker.md`
  - `harness/open-decisions.md`
- Non-affected surfaces:
  - `harness/project-spec/project_spec_0.1.2.json`
  - `harness/archive-policy.md`
  - `harness/known-failures.md`
  - all non-harness product, runtime, test, schema, and deployment surfaces
- Admissibility checks:
  - The bundle must stay inside the first build target and not expand into retrieval, traversal, graph, or synthetic-node work.
  - The bundle must name one new-thread probe and one same-thread continuation probe.
  - The bundle must keep `thread_ledger` append-only and hash-chained as a non-negotiable invariant.
  - Any inability to execute a real LLM call boundary must downgrade the eventual implementation result to `scaffold-only`.
- Stop conditions:
  - Stop if implementation would require choosing or implying approved UI, API contract, storage backend, or durable schema shape beyond current authority.
  - Stop if the bundle expands beyond the first build target.
  - Stop if the acceptance probes cannot be stated in falsifiable operator-facing terms.

## Observed Evidence

- `harness/implementation-projects/active/` contains only `.gitkeep`, so there is no live implementation bundle yet.
- `harness/open-decisions.md` contains no current or pending decisions.
- `harness/project-spec/project_spec_0.1.2.json` explicitly enumerates the first build target and defers retrieval, traversal, graph, synthetic-node, and long-term compression questions.
- `harness/README.md`, `harness/harness-runtime.md`, and `harness/sub-agents.md` require one live numbered bundle, explicit acceptance probes, and no silent override of spec authority.

## Planned Work

1. Seam 1: operator-facing entry boundary
   - Pick the narrowest operator-facing caller that can truthfully satisfy "accept `user_input`" for both a new thread and a continuation turn.
   - The chosen caller must expose a stable way to create a new thread and to continue an existing thread by identity.
   - Do not imply a long-lived public product surface or compatibility promise without explicit approval.
2. Seam 2: continuity persistence baseline
   - Choose a minimal persistence representation for `conversation_thread`, materialized `thread_state`, and append-only hash-chained `thread_ledger`.
   - Preserve a visible thread identity, a current-thread-state pointer or equivalent lookup path, and a parent-hash chain for every turn.
   - Keep the representation reversible and local; do not convert a first-build storage choice into long-term architecture by default.
3. Seam 3: first-turn runtime slice
   - Implement only the minimal single-turn path: create thread, accept user input, load empty or initial prior state, assemble a basic `synthesis_context_packet`, call the LLM boundary, save the assistant response, materialize the next state, and append the first ledger record.
   - Keep the context packet basic and continuity-oriented; no retrieval or traversal artifacts are required in this slice.
4. Seam 4: continuation runtime slice
   - Implement the same operator-facing path for a second turn on the same thread.
   - Load prior state and parent hash from the existing persisted thread surfaces, call the LLM boundary again, persist the second response, materialize updated state, and append the second ledger record with the first record hash as parent.
5. Seam 5: verification and closeout
   - Run the named acceptance probes through the real chosen caller.
   - Verify ledger append order, hash-parent linkage, and state continuity after each turn.
   - If the LLM boundary is stubbed or unavailable, report the result as `scaffold-only` and record which live dependency blocked a live-wired claim.

## Non-Goals

- semantic traversal, retrieval packet assembly, coverage evaluation, graph expansion, or latent-space activation
- synthetic-node candidate handling or promotion back into latent space
- multi-thread interaction, branching, or long-term thread-state compression
- broader product UX, remote API contract design, deployment, auth, billing, or storage migrations
- compatibility shims for future callers not required by the first build target

## Delivery Posture And Acceptance Criteria

Named user-facing acceptance probes for the future implementation:

1. `probe_new_thread_minimal_turn`
   - Through the chosen operator-facing caller, submit a first user message without an existing thread.
   - Expected observable result: a new `conversation_thread` is created, a real LLM call returns an `assistant_response`, a persisted `next_thread_state` exists for that thread, and the `thread_ledger` contains a first append-only record with a valid root or null parent position and a stored self hash.
2. `probe_same_thread_continuation_turn`
   - Through the same caller, submit a second user message against the previously created thread.
   - Expected observable result: the runtime loads the prior materialized `thread_state`, the LLM call returns a second `assistant_response`, a new persisted `next_thread_state` replaces the previous current state for that thread, and the `thread_ledger` appends exactly one new record whose parent hash matches the first record hash.

Failure conditions that block completion:

- either probe can run only against fixtures, mocks, or dry-run paths
- the continuation turn does not read prior state from the same thread
- the ledger is rewritten, mutated in place, or appended without a valid parent-hash link

## Current Repo Runtime State

- The repo currently exposes harness surfaces only; no runtime implementation surfaces are present yet.
- There is no active implementation bundle before this one.
- There are no recorded current decisions or pending decisions before this bundle.
- Availability of a real LLM boundary, credentials, and the eventual caller path is unknown at planning time; that uncertainty affects verification posture, not scope.

## Assumptions And Unknowns

Assumptions:

- The first build target can use a minimal `thread_state` projection as long as it preserves continuity for the next fresh LLM call.
- The initial implementation may choose a narrow local operator surface instead of a full product UI, subject to explicit approval of that surface.
- A reversible local persistence choice is preferable to a durable architecture commitment at this stage.

Unknowns:

- Which operator-facing caller is authorized for `accept user_input`.
- Which persistence representation will hold `conversation_thread`, `thread_state`, and append-only `thread_ledger` for the first build target.
- Whether the implementation environment will support a real LLM boundary during verification or force a `scaffold-only` downgrade.

## Affected And Non-Affected Surfaces

Planning surfaces changed in this bundle:

- `harness/implementation-projects/active/implementation-01-plan.md`
- `harness/implementation-projects/active/implementation-01-tracker.md`
- `harness/open-decisions.md`

Runtime surfaces that must eventually move together for the first build target to be truthful:

- one bounded operator-facing input surface
- conversation-thread creation and lookup path
- materialized thread-state load and save path
- append-only hash-chained ledger append path
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
  - if the implementation path cannot exercise a real LLM boundary through the operator-facing caller, mark behavior as `scaffold-only` and report the missing dependency explicitly
- Review obligation:
  - reviewer must reject any implementation claim that is based only on types, files, serialization, tests, or dry-run traces without a passing named acceptance probe

## Completion Rule

- Do not mark behavior complete on fixture, mock, dry-run, serialization, type, field, file, path, route, crate, config, or nominal-caller evidence alone.

## Approval Gates

- [ ] Schema
- [x] API
- [ ] Auth
- [x] Storage
- [ ] Deployment
- [ ] Destructive operation
- [ ] Broad architecture
- [ ] Project-intent authority not covered by spec or current authorization

Gate notes:

- API gate: the operator-facing `accept user_input` surface is not yet authorized.
- Storage gate: the persistence representation for `conversation_thread`, `thread_state`, and `thread_ledger` is not yet authorized.
- Schema becomes a gate only if the chosen persistence representation introduces a durable external contract rather than a narrow local prototype surface.

## Handoff Packet For Next Agent

## Role

- From: planner
- To: reviewer
- Requested action: audit this bundle against the first build target, the pending decision capture, the acceptance probes, and the downgrade rule before any implementation begins

## Project And Task

This repo is still harness-only. `implementation-01` is intended to be the first live bundle and is bounded to the spec's `implementation_boundary.first_build_target`. The plan is meant to prepare a truthful implementation handoff without deciding product UI or persistence architecture by stealth.

## Admissibility Report

- Invariant constraints:
  - first build target only
  - append-only hash-chained ledger
  - next-call continuity via `thread_state`
  - no traversal, retrieval, graph, or synthetic-node work
- Task constraints:
  - planning bundle only
  - explicit acceptance probes
  - no silent UI or storage decisions
- Constraint conflicts:
  - no invariant conflict
  - pending operator-surface and persistence-representation authority gaps
- Allowed transformation types:
  - review the plan, tracker, and pending decisions for correctness and completeness
- Affected surfaces:
  - `harness/implementation-projects/active/implementation-01-plan.md`
  - `harness/implementation-projects/active/implementation-01-tracker.md`
  - `harness/open-decisions.md`
- Non-affected surfaces:
  - all non-harness implementation surfaces
- Admissibility checks:
  - bundle stays inside first build target
  - probes are falsifiable and operator-facing
  - open decisions capture the real authority gaps
- Stop conditions:
  - reject the bundle if it silently chooses UI, storage, or durable schema shape

## Authorized Boundaries

- Affected surfaces:
  - `harness/implementation-projects/active/implementation-01-plan.md`
  - `harness/implementation-projects/active/implementation-01-tracker.md`
  - `harness/open-decisions.md`
- Non-affected surfaces:
  - everything outside those planning surfaces
- Boundaries not authorized:
  - runtime code, tests, product architecture decisions, schema/API/storage decisions represented as already settled

## Evidence And Assumptions

- Observed evidence:
  - no existing active bundle
  - no open decisions recorded
  - first build target explicitly listed in the project spec
- Inferences:
  - `implementation-01` is the correct first live bundle
  - implementation work will need narrow approval on operator surface and persistence
- Unknowns:
  - exact operator-facing caller
  - exact persistence representation
  - live LLM-boundary availability for verification

## Expected Change

Reviewer should either confirm that the bundle is implementation-ready after decision review, or return concrete findings about missing seams, missing gates, overstated behavior claims, or unrecorded decision dependencies.

## Acceptance Criteria

The reviewer accepts the bundle only if it:

- stays fully inside the first build target
- names both user-facing probes
- captures the real authority gaps in `harness/open-decisions.md`
- keeps `scaffold-only` as the required downgrade when a live LLM path cannot run

## Stop Conditions

Acceptance criteria achieved, or findings require planner revision before implementation handoff.

## Closeout Note

- When this bundle completes, move it from `active/` to `archive/`.
