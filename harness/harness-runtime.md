# Runtime Contract

This document defines the standing behavior for the harness orchestrator and agent roles.

## Runtime Job

- Identify the controlling surface.
- Separate evidence, inference, unknowns, and speculation.
- Ground multi-step, risky, or behavior-facing work in a project admissibility report before choosing files or tasks.
- Identify affected and non-affected surfaces before behavior-changing edits.
- Route work to the correct agent.
- Keep verification explicit.
- Separate scaffolding, wiring, and user-facing behavior.
- Keep planning tied to the current task-authorized implementation goal inside the project's invariant space.
- Update repo-local memory when the project state changes.

## Canonical Memory Rule

- Treat `harness/` as the only canonical repo-local memory for project continuity.
- Canonical continuity lives in `harness/implementation-projects/archive/`, `harness/open-decisions.md`, and `harness/known-failures.md`.
- Do not create, update, or rely on repo-root `memories/`, `memories/repo/`, or similar host-runtime memory files for implementation history, decision authority, risk state, or verification evidence.
- If a host tool provides repo-memory features, ignore them for authoritative project state and inspect the canonical harness surfaces instead.

## Authority Lens

- Invariant authority lives in `harness/project-spec/**`. It defines what the project is allowed to become.
- Task authority selects or sequences the current work inside that invariant space. It usually comes from the current user instruction, open decisions, and the active plan.
- Open decisions and active plans may interpret or sequence project work, but they do not silently override project-spec invariants.
- If task authority conflicts with invariant authority, stop and surface the conflict instead of improvising around it.

## Project Admissibility Report

The project admissibility report carries relevant project constraints from `harness/project-spec/**` through PM review, planning, implementation, review, and archive. It is not a new project ontology or authority layer. It is a report format for naming what is admissible under the current project spec and request.

For multi-step, risky, or behavior-facing work, the report should state:

- Invariant constraints
- Task constraints
- Constraint conflicts
- Allowed transformation types
- Affected surfaces
- Non-affected surfaces
- Admissibility checks
- Stop conditions

If the report cannot be grounded in repo state, invariant authority, or task authority, stop and ask for the missing authority or clarification.

## Behavior Reality Discipline

- Every non-trivial behavior claim needs a named user-facing acceptance probe before implementation or as soon as the seam is understood.
- Types, fields, files, paths, routes, crates, DTOs, configs, nominal callers, mocks, fixtures, snapshots, dry runs, and unit tests can prove structure. They do not prove user-facing behavior by themselves.
- Use `scaffold-only` when the evidence proves only structure, internal plumbing, or fixture behavior.
- Use `live-wired` only when a non-test caller or operator surface exercises the intended path against the intended backend, target, or failure source and produces the expected user-facing consequence.
- If the behavior probe cannot run, name the missing caller, backend, target, data, credential, service, or operator action.

## Done Rule

Work is done only when:

- changed surfaces are named
- verification items are pass, fail, blocked, skipped with reason, or deferred with owner
- every behavior-facing claim maps to a passing named acceptance probe or an explicit downgrade
- remaining risk is explicit
- project memory is updated when relevant
- no duplicate project-state store was created outside `harness/`
- completed implementation bundles are moved out of `active/`
