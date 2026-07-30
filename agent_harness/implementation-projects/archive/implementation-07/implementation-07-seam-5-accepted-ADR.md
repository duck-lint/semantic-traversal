# ADR: Seam 5 Accepted Bounded Ordinal Fusion

- Status: accepted
- Date: 2026-07-19
- Supersedes: `implementation-07-seam-5-fusion-ADR.md` as the active decision
- Scope: bounded Seam 5 fusion only; no temporal retrieval

## Decision

The accepted Seam 5 contract is deliberately small:

- Each executor contributes a deterministic one-based rank stream. Fusion uses
  unweighted ordinal support (`sum(1 / rank)`); executor raw scores remain
  executor-local evidence and are not compared across surfaces.
- `selection_source` is the winning surface by best ordinal rank and canonical
  surface tie-breaks. `source_layers` retains every supporting surface.
- Required exact-term and required-layer evidence is reserved first. If bounded
  selection cannot preserve required evidence, coverage blocks with diagnostics.
- Ordinary layer budgets are retired as cross-surface allocation authority.
  Runtime emits the retirement diagnostic; compiler budget fields remain
  compatibility input only and do not become hidden caps or quotas.
- Ordinary selection allocates breadth before depth by deterministic `note_id`
  order, then stable chunk order, under the single runtime/YAML-owned global
  `selection_policy.max_chunks` ceiling. The configured value is a ceiling, not
  an inherent value and not a promise to fill every slot.
- Merge, reservation, selection, and rejection diagnostics are deterministic
  and retain provenance, raw executor scores, and coverage reasons.

## Authority and boundaries

Runtime/YAML owns admissibility, requiredness, operational bounds, and final
selection. Compiler fields may request supported retrieval intent and bounded
limits but do not define cross-surface ranking policy. Hard scope remains an
admissibility filter; preferred scope remains non-exclusionary and is only a
bounded ordering signal in this accepted experiment. Exact absence remains the
exact/runtime contract.

This ADR does not implement or approve weighted RRF, preferred-scope
reservoirs, sparse stopping, hard note/source caps, byte ceilings, fuzzy
redundancy suppression, query/graph quotas, temporal ordering, compiler prompt
or schema changes, or synthesis claims about chronology.

## Deferred live-UAT findings

- Preferred-scope evidence can be removed by executor-local truncation before
  fusion sees it. No reservoir is introduced here; reassess after genuine
  temporal retrieval exists.
- The selector tends to fill the configured `max_chunks` when enough
  admissible candidates remain. Sparse stopping is deferred until later UAT
  demonstrates noise, reasoning degradation, or material context cost.

## Evidence

The accepted ordinal experiment is preserved in commit
`3dd1579de29a5b960489a3b4f1bb6eba05f40f73` and normalized by
`c84df8da0924446d9a88776ed663ee935d9392d0`. Same-pool replay reduced the
genealogy counterexample from four notes/max thirteen chunks to eleven
notes/max four chunks, while preserving multi-surface provenance and both
semantic-query streams. The direct sister/family live UAT preserved the
sister/full/half-sister distinction but also observed configured-max filling;
that is recorded as an observation, not a demonstrated reasoning failure.

## Compatibility and rollback

The original full weighted-RRF/hybrid analysis remains in
`implementation-07-seam-5-fusion-ADR.md` as historical evidence, but is
superseded and unapproved. Rollback is bounded to reverting the ordinal
experiment commit if a later verified contract requires it; no prompt or
packet schema migration is implied by this ADR.

## Required verification and next seam

Focused fusion/config tests, the complete Python suite, compileall, diff
checks, and prompt/YAML hash guards must pass. Seam 6A is reopened separately
for typed temporal anchors and temporal retrieval operations. Seam 7 has not
begun.
