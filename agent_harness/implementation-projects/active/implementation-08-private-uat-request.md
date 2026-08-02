# Implementation 08 — Private UAT Metric Correction

## Status

Seam 0: operator-accepted. This pass repairs the private evaluator only. The
corrected private baseline remains operator-rerun-pending; Seam 1 has not
begun.

The initial schema-v1 run is historical evidence and must remain preserved
locally. It is not in this repository and must not be migrated, overwritten,
or committed.

## Preserve the v1 run before rerunning

Run these commands against the local repository before changing the fixture:

```powershell
Copy-Item -Recurse `
  agent_harness/private/runs/implementation-08-private-before `
  agent_harness/private/runs/implementation-08-private-before-v1-original

Copy-Item `
  agent_harness/private/implementation-08-private-uat.yaml `
  agent_harness/private/implementation-08-private-uat.v1.yaml
```

If either source is absent, preserve the existing local names using an
equivalent copy operation; do not invent or commit the private contents.

## Create and migrate the v2 fixture

Schema version 2 is intentionally incompatible with version 1. Expectations
now live inside every individual turn, including one-turn cases. A v1 fixture
fails validation with a migration message; there is no compatibility fallback.

```powershell
Copy-Item `
  agent_harness/implementation-projects/active/implementation-08-private-uat.example.yaml `
  agent_harness/private/implementation-08-private-uat.yaml
```

Replace the synthetic inputs and expected values locally. Keep private
questions, answers, subjects, referents, UUIDs, paths, note text, and literal
canaries only in the ignored fixture.

## Validate and run

Validation-only mode uses the tracked synthetic fixture and does not require a
vault, persisted inventory, model backend, or secrets:

```powershell
python tools/implementation08_private_uat.py `
  --fixture agent_harness/implementation-projects/active/implementation-08-private-uat.example.yaml `
  --repo-root . `
  --validate-only
```

Run the corrected private baseline locally only after populating the ignored
fixture:

```powershell
python tools/implementation08_private_uat.py `
  --fixture agent_harness/private/implementation-08-private-uat.yaml `
  --repo-root .
```

Raw records are written only to:

```text
agent_harness/private/runs/<suite_id>/raw/
```

The runner records the production compiler topology as one initial attempt plus
zero or one repair attempt. It identifies the repair call from the
production-owned `repair_context` request structure and separates the
`final_response_attempt` from the `plan_source_attempt`. A final attempt with
raw text is matched strictly by the runtime diagnostic's raw-response hash. A
timed-out or unavailable repair may have no raw response or hash; it is
recorded as contract `unavailable` only when production diagnostics agree and
the retained plan is deterministically matched to the initial attempt. The
runner records raw compiler status/text/metadata privately and independently measures
JSON syntax, canonical contract compliance, canonicalization/fallback use,
repair cost, and plan quality. It does not call another model or alter
production requests. It preserves one persisted thread per case and evaluates
every turn against that turn's expectations.

Required operators and evidence requirements use subset semantics: missing
required values fail, while additional requested operators remain visible for
efficiency review. Subject and referent results remain exact normalized
surface comparisons; mismatches are review signals, not independent semantic
failures. The redacted report contract is version 6 / evaluator contract 5,
and a run cannot resume under a different contract version. Use a fresh suite
ID after this correction rather than mixing records.

## Optional redacted export

After inspecting the private raw run, an operator may create a safe aggregate
without committing it:

```powershell
python tools/implementation08_private_uat.py `
  --fixture agent_harness/private/implementation-08-private-uat.yaml `
  --repo-root . `
  --export-redacted agent_harness/private/implementation-08-private-uat-v5-redacted.json
```

The v5 report exposes only allowlisted statuses, counts, safe operator names,
safe evidence enums, hashes, and reason categories. It excludes questions,
answers, subject/referent strings, UUIDs, paths, note/chunk identity, prompts,
raw compiler responses, arbitrary model-generated keys, and response prose.

Runtime completion remains a factual `runtime_status`, not evaluation success.
Evaluation can be `failed`, `incomplete`, `review_required`, or `passed`; this
runner has no independent answer/support oracle, so otherwise successful cases
normally remain `review_required`.

For exhaustive negative claims, generic coverage approval is insufficient.
Authorization is read from runtime coverage and traversal data, including exact
request/execution, exact status, required-term adequacy, scope, and
`negative_claims_allowed`.

The corrected private baseline is still a pre-production-change measurement.
Inventory inspection or redesign, model replacement/bakeoff, corpus changes,
and Seam 1 remain separate future work. The report/evaluator contract for this
alignment correction is version 6 / evaluator contract 5; fixture schema
version 2 remains unchanged.

The completed pre-fix private baseline exposed a canonical semantic chunk
alignment defect: configured semantic frontmatter was present in FTS but absent
from vector input and selected synthesis evidence. The correction now measures
the same admitted chunk surface across applicable retrieval and synthesis
boundaries, preserves distinct operator semantics, and normalizes the runtime's
unrestricted exact-search scope to the fixture's `complete_eligible_corpus`
requirement. The original private run remains operator-preserved; complete
post-fix reingest and a fresh private baseline remain operator-pending. Fixture
schema version 2 is unchanged and Seam 1 has not begun.

The post-alignment private baseline established that canonical semantic chunk
propagation worked, while all six executed compiler turns emitted invalid
contracts and used deterministic fallback. The compiler-facing inventory had
grown oversized and schema-competitive because the full inventory was rendered
twice, facet projections duplicated observations, high-cardinality values were
enumerated, and capability mapping resembled an output schema. The bounded
correction preserves the single full persisted inventory and supplies the
compiler with one deterministic in-memory projection. Every admitted semantic
frontmatter field name remains visible; only generic low-cardinality values are
enumerated; optional paths and values are trimmed under the configured global
budget; and the projection is rendered exactly once. Resolver binding,
retrieval, manifests, and diagnostics retain the full inventory. The prompt
template, canonical response schema, models, timeout, repair, fallback, and
fixture schema remain unchanged. No reingest is required. Use a fresh suite ID
for the post-projection rerun. Seam 1 has not begun.
# Post-PR-14 correction boundary

PR #14 was validated by a completed private rerun: six compiler contracts were valid, with no repair or fallback; exact-absence measurement passed; and canonical metadata propagation remained healthy. The rerun exposed globally earliest unrelated evidence in a valid multi-subject temporal plan and positive alias rejection of compiler-emitted substrate terms.

The alias subsystem is now excised. The resolver performs negative executability validation only, while temporal relevance is established independently per existing subject before temporal operators run. Missing required subject evidence blocks a complete comparison. Legacy conversation state still loads while newly written state omits the dormant field. No reingest is required. The corrected private baseline remains operator-rerun-pending; Seam 1 has not begun.

Expected fresh suite ID:

```yaml
suite_id: implementation-08-private-before-v2-post-context-first-temporal
```

Expected directory: `agent_harness/private/runs/implementation-08-private-before-v2-post-context-first-temporal/`.

# Post-PR-15 resolved-referent propagation correction

# Post-PR-18 typed-closure execution correction

The post-PR-18 private rerun found that the declared typed closure was only
partly executable: automatic graph support carried zero effective depth,
grounded notes were rematched from text, seed hydration was not distinct from
edge traversal, lexical atoms were flattened, and contextual exact support
was absent without literal terms. The correction is runtime-only and keeps
the compiler contract, persisted substrate, temporal policy, repair, fallback,
and evaluator versions unchanged. It preserves canonical identities through
graph hops, applies one layer canonicalizer to requested and automatic
surfaces, executes lexical queries independently, and adds bounded optional
exact probes from contextual atoms. A production-path synthetic test proves
direct grounding, canonical note hydration, a real graph hop, temporal anchor
attachment, and relation evaluation while excluding unrelated global evidence.

No private questions, answers, titles, dates, paths, UUIDs, note contents,
metadata values, screenshots, or run artifacts were accessed. No reingest is
required. Use fresh suite ID:

```yaml
suite_id: implementation-08-private-before-v2-post-closure-execution-correction
```

Expected ignored directory:
`agent_harness/private/runs/implementation-08-private-before-v2-post-closure-execution-correction/`.
Preserve the completed post-PR-18 run, do not use `--replace`, and keep Seam 1
operator-pending.

# Post-PR-16 retrieval-surface completeness boundary

# Post-PR-23 subtractive grounding and exact-contract correction

This correction is runtime interpretation and canonical-plan validation only.
It preserves compiler prompt/model/context (40,960), JSON schema, repair count,
fallback, persistence, embeddings, graph semantics, temporal semantics, fusion,
synthesis, and negative-claim policy. No reingest is required and private data
must not be accessed or committed.

After merge, preserve the current post-PR-23 run and change only the ignored
suite ID:

```yaml
suite_id: implementation-08-private-before-v2-post-subtractive-grounding-contract-correction
```

Expected directory:
`agent_harness/private/runs/implementation-08-private-before-v2-post-subtractive-grounding-contract-correction/`.

Before accepting the rerun, verify inventory source/status and versions:
inventory schema 4, manifest v2, projection v3, grounding v1, evaluator v5,
report v6, fixture v2, FTS/vector/graph/temporal validity, and compiler context
40,960. Run the unchanged private fixture without `--replace`; do not edit
private case definitions automatically. Confirm query-level chronology admits
anchored context without a named-subject gate, multi-subject typed relations
remain isolated, and malformed exact contracts are repaired rather than
silently weakened.

The retrieval-surface correction preserves the PR #16 compiler-to-temporal
context path while aligning the generic inventory legend with executable
runtime capability. Inventory schema becomes 3, compiler projection becomes 2,
and retrieval-surface manifest version 1 is added. Evaluator contract 5,
redacted report schema 6, fixture schema 2, and the temporal kernel remain
unchanged.

Complete post-merge reingest is required because FTS coverage, graph
materialization, graph provenance, inbound traversal, and the persisted
inventory shape change. Before private UAT, confirm FTS, vector, graph, and
temporal validation, inventory schema 3, projection version 2, and manifest
version 1.

Use this fresh ignored suite ID after reingest:

```yaml
suite_id: implementation-08-private-before-v2-post-retrieval-surface-completeness
```

Expected directory:
`agent_harness/private/runs/implementation-08-private-before-v2-post-retrieval-surface-completeness/`.

No private questions, answers, titles, paths, dates, UUIDs, note text, or
artifacts belong in the repository. Lexical multi-query flattening remains
deferred. No graph-to-temporal chaining, alias, filter language, query recipe,
field ontology, attachment mechanism, or Seam 1 work was added.

PR #15 successfully excised scope aliases. The post-PR-15 private rerun showed
compiler referents present upstream, but resolver binding omitted them, so
temporal execution used an anonymous query-level fallback. The improved answer
was supported by other evidence and did not validate per-subject temporal
execution.

The runtime correction preserves ordered resolved referents through the
compiler-to-binder-to-executor seam and records an explicit propagation
diagnostic. A genuine empty referent list retains query-level temporal
behavior; nonempty referents that disappear are a propagation failure, not a
satisfied anonymous subject. Temporal diagnostics count only real named
subjects, and same-chunk multi-subject provenance survives deduplication
without duplicating the selected chunk.

Lexical multi-query flattening remains a known deferred weakness. Repair,
fallback, compiler contract, prompt, model, YAML, inventory, chunk/index,
retrieval-surface, synthesis, and negative-claim behavior are unchanged. No
reingest is required. Use this fresh operator rerun after merge:

```yaml
suite_id: implementation-08-private-before-v2-post-resolved-referent-propagation
```

Expected directory:
`agent_harness/private/runs/implementation-08-private-before-v2-post-resolved-referent-propagation/`.
The corrected private rerun remains operator-pending. Do not edit or replace
the ignored fixture automatically. Seam 1 has not begun.

# Post-PR-17 typed semantic closure correction

PR #17 made isolated retrieval surfaces truthful and complete. The subsequent
private rerun showed that the manifest still enumerated surfaces without their
valid cross-surface transitions. This correction models notes as semantic
objects and chunks as contained units; admitted fields remain human-authored
type identifiers whose structural affordances are generated from ingest facts.

The compiler schema and model remain unchanged. Compiler layers express
required evidence or explicit operator intent, while resolver binding expands
context across every compatible direct surface. Runtime performs bounded
contextual closure, derives graph seeds from grounded objects, hydrates reached
units, and evaluates temporal relations over the closure rather than restarting
from a broad temporal projection. Referents identify subjects but do not alone
authorize broad relevance. No field ontology, alias, query recipe, filter
language, reverse graph edge, new operator, or model-directed recursion was
introduced. Repair, fallback, synthesis, negative-claim policy, and evaluator
contracts remain unchanged.

Complete reingest is required for inventory schema 4, manifest v2, projection
v3, and the changed inventory policy hash. The UAT runner now refuses stale or
invalid persisted substrate before model calls. Use:

```yaml
suite_id: implementation-08-private-before-v2-post-typed-semantic-closure
```

Expected directory:
`agent_harness/private/runs/implementation-08-private-before-v2-post-typed-semantic-closure/`.

Seam 1 has not begun.

# Post-PR-19 semantic grounding authority correction

PR #19 made the typed-closure execution path real. The post-PR-19 private
rerun also exposed that broad relevance could still be treated as object
identity, graph descendants could inherit subjects without explicit authority,
and temporal anchors could bypass predicate grounding. This correction adds a
pure internal grounding specification and preserves the existing compiler
contract, persisted substrate, retrieval operators, temporal modes, repair,
fallback, synthesis, and negative-claim policy.

The resolver now records ordered subject proposition bundles or one query-level
bundle. Runtime distinguishes object-identity, evidence-unit, and
relation-proposition grounding; only identity-authorized notes or explicit
graph seeds may start named-subject graph traversal. Temporal evaluation is
fed only proposition-grounded closure candidates. Descriptive subjects may be
grounded through supporting context without a canonical object or exact
referent-string equality. Weak evidence remains available as evidence without
satisfying required relation coverage.

No private data, referents, titles, paths, UUIDs, dates, or corpus text belong
in repository records. No reingest is required because canonical chunks,
embeddings, FTS, graph storage, temporal anchors, persisted inventory, and
manifest versions are unchanged. Confirm the existing substrate remains valid
before the operator rerun. Use:

```yaml
suite_id: implementation-08-private-before-v2-post-semantic-grounding-authority
```

Expected directory:
`agent_harness/private/runs/implementation-08-private-before-v2-post-semantic-grounding-authority/`.

Do not edit or replace the ignored fixture automatically. The model bakeoff is
still pending, and Seam 1 has not begun.

# Post-PR-20 subject–predicate and canonical-link identity correction

The correction preserves the semantic-grounding authority boundary while
partitioning subject-bearing atoms from predicate/context atoms. Subject-only
atoms cannot satisfy predicates, shared predicate residuals are evaluated
independently for each named subject, and query-level plans remain subjectless.
Resolved body and admitted-frontmatter wikilinks now promote their canonical
target note IDs directly into authorized graph seeds; source notes remain
evidence and unresolved links cannot establish identity. Aggregate grounding
diagnostics are derived from final merged assessments and enforce the invariant
that proposition-grounded units imply evidence-grounded units.

No exact-canary correction, compiler prompt/schema/model change, temporal
interpretation change, frontier prompt change, alias, field route, filter,
query recipe, reverse edge, or new operator was added. The operator's private
corpus heading change is not represented in production code. Because graph-edge
occurrence metadata changed, complete reingest is required before rerun. Use:

```yaml
suite_id: implementation-08-private-before-v2-post-grounding-link-identity-correction
```

Expected directory:
`agent_harness/private/runs/implementation-08-private-before-v2-post-grounding-link-identity-correction/`.

Do not access or reproduce private case content, and do not run the private
UAT in the repository environment. The rerun remains operator-pending; model
bakeoff remains pending; Seam 1 has not begun.
