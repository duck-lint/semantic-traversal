# Type System Operational Guide

Use this during normal coding work so evidence, inference, user intent, proposed action, and validated result do not blur together.

## Practical Rule

For normal coding work, say the source, inference, unknowns, expected consequence, and validation path. Use the full bridge only when the move is risky or conceptually easy to confuse.

## Registers

- Agent-independent constraints: runtime behavior, code contracts, schemas, tests, environmental limits, and observable regularities.
- Indexical/user commitments: goals, preferences, workflow, risk tolerance, aesthetics, and approvals.

## Provenance

- Observed artifact: code, docs, schemas, config, traces, dashboards, tickets, or tests as representations.
- Observed runtime behavior: command output, test result, measurement, app behavior, or probe.
- User report: stated goal, preference, memory, or approval.
- External source: web docs, articles, upstream references, or citations.
- Inference: derived from typed sources by a stated method.
- Speculation or unknown: quarantined until checked.

## Common Category Errors

- Treating a user preference as a runtime constraint.
- Treating a test as proof of behavior it did not exercise.
- Treating persuasive wording as evidence.
- Treating a representation of behavior as the behavior itself.
- Treating a proposed fix as implemented or validated.
