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

The runner delegates the existing semantic compiler exactly once per turn,
records raw compiler status/text/metadata privately, and independently
measures JSON syntax, canonical contract compliance, canonicalization/fallback
use, and plan quality. It does not call another model or alter production
requests. It preserves one persisted thread per case and evaluates every turn
against that turn's expectations.

## Optional redacted export

After inspecting the private raw run, an operator may create a safe aggregate
without committing it:

```powershell
python tools/implementation08_private_uat.py `
  --fixture agent_harness/private/implementation-08-private-uat.yaml `
  --repo-root . `
  --export-redacted agent_harness/private/implementation-08-private-uat-v2-redacted.json
```

The v2 report exposes only allowlisted statuses, counts, safe operator names,
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
and Seam 1 remain separate future work.
