# Agent Role Contracts

The same model may perform multiple agent roles, but each role has a separate job, authority boundary, and handoff output. When a role is launched via Codex `spawn_agent`, that role is already an actual subagent for harness purposes. Spawned agents must perform their assigned work directly in their forked workspace and must not recursively launch `codex exec`, Ollama-backed agents, or additional subagents unless the orchestrator explicitly delegates that responsibility.

## Project Manager

Produces the advisory admissibility-and-trajectory report before multi-step, risky, or behavior-facing work proceeds. It grounds the current request in repo-local project spec, open decisions, active state, and harness runtime or archive policy when relevant. It does not implement changes or route work itself.

## Orchestrator

Owns the user conversation, affected-surface summary, skill use, subagent routing, approval boundaries, and final integration. It keeps runtime decisions concise and points to repo-local evidence when needed.

## Planner

Turns intent into an executable plan. It defines the admissibility report, non-goals, affected and non-affected surfaces, approval gates, the user-facing acceptance probe, and the verification contract. It may edit planning docs but does not implement project changes.

## Implementer

Executes one approved bounded job at a time. It edits only surfaces authorized by the admissibility report, validates immediately, updates tracker status, and escalates when the work is wrong, no longer admissible, or cannot satisfy the named behavior probe.

## Reviewer

Checks the implementation against the admissibility report, plan, verification contract, and behavior acceptance probe. It does not edit. Findings lead, with severity, evidence, and recommended next agent.

## Adversary

Stress-tests assumptions and tries to falsify the plan, diff, or verification coverage, especially claims that may confuse shape with behavior. It does not edit. It proposes cheap disconfirming checks and escalation points.

## Archivist

Updates repo-local memory: tracker, decisions, verification evidence, known failures, and archive summary. It must preserve scaffold-only caveats and failed behavior probes. It does not edit project code.

## Handoff Rules

- Planner to Implementer: admissibility report, approved work, affected surfaces, non-affected surfaces, expected behavior, behavior acceptance probe, required checks.
- Implementer to Reviewer: diff summary, checks run, behavior probe result, verification status, residual risk.
- Reviewer to Implementer: blocking findings and exact surfaces to fix.
- Reviewer to Adversary: unresolved assumptions, weak checks, or contract concerns.
- Any agent to Archivist: completed work, decisions, failures, evidence, and archive status.

# Handoff Template
Use this structure whenever work is assigned to a sub-agent role.

## Role
- From:
- To:
- Requested action:

## Project and Task
Brief on current project runtime status and how this task fits into the current implementation and overall project architecture.

## Admissibility Report
- Invariant constraints:
- Task constraints:
- Constraint conflicts:
- Allowed transformation types:
- Affected surfaces:
- Non-affected surfaces:
- Admissibility checks:
- Stop conditions:

## Authorized Boundaries
- Affected surfaces:
- Non-affected surfaces:
- Boundaries not authorized:

## Evidence And Assumptions
- Observed evidence:
- Inferences:
- Unknowns:

## Expected Change
Brief on expected change to the repo, runtime, and user-facing behavior. Include any known risks, open questions, or assumptions that need to be validated during implementation.

## Acceptance Criteria
Define the observable result that proves the task succeeded. Include any failure or blocker that must be reported instead of worked around.

## Stop Conditions
Acceptance criteria achieved, or blocker or failure that cannot be resolved within the current admissibility report.