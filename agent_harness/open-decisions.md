# Open Decisions

This file is the current decision authority for decisions that still matter outside an archived implementation bundle.

Do not use this file as a roadmap. Record only decisions already made, decisions required to continue the current implementation, and explicit user-provided next end goals.

## Current Decisions

If there are no current decisions, replace the table below with `No current decisions.`.

When a current decision exists, use:

| ID | Decision | Source | Status | Owner | Revisit Trigger |
| --- | --- | --- | --- | --- | --- |
| cd-01 | both | corpus folder sitting at root with snippet section taken into tests/fixtures so that we have both | complete | user & codex | if repo/project structure changes |
| cd-02 | tmp | implementation 01 artifacts | complete | user & codex | after a useable version of the project is developed , we'll look to adjust the artifacts that are currently dumping in the tmp folder from implementation 01 and these can be lumped in with that, so can be placed in same tmp location for now |
| cd-03 | live embeddings | prototyping success | approved | user & codex | if just a local sentence transformers embedding solution will not work (it should, I've done it before on this machine) so the embedding surface should be designed in such a way that if we wanted to change the embedding model on a whim to a different local model, or an API model, that would be super easy to adjust on a whim. Do not seek additional approval for this, consider this documentation approval for local sentence transformers embedding. |
| cd-04 | none ever | project_spec | complete | user & codex | if anything regarding frontmatter as nodes for graphs reappears, that was intended to be excised from the project spec but accidentally was not. all references to frontmatter as nodes should be removed now, this is not an intention at this time, only notes as nodes, and wikiklinks as edges. |
| cd-05 | SQLite as default artifact storage where possible | project_spec | complete | user & codex | if this is not the best decision for the specific project, not possible, and/or creates additional issues. whenever possible for the latent space data forms, sqlite is preferred with json manifests if possible to keep data orderly (sqlite) and machine readable (json manifest) | 

## Pending Decisions

If there are no pending decisions, replace the empty table below with `No pending decisions.`.

When a pending decision exists, use:

| ID | Question | Boundary | Needed For | Owner | Status |
| --- | --- | --- | --- | --- | --- |

## Notes

- Bundle-local decisions for the archived `implementation-01` live in `harness/implementation-projects/archive/semantic-traversal-implementation-01-summary.md`.
- Next end goal: human UAT over additive semantic extraction and retrieval interaction.
- Link to archived implementation summaries or decision files when a decision's evidence lives there.
- Do not point active decisions at stale files under `active/` after a bundle has moved to `archive/`.
- Remove decisions that no longer affect current or paused implementation work, or move their final context into the archived bundle summary.
