# Implementation 08 — Private UAT Request

## Status

Seam 0: operator-accepted

This request is intentionally fillable and contains no private corpus assumptions. The repository-safe contract and runner are prepared in this pass; execution against the real private corpus remains operator-pending.

Populate the ignored fixture at `agent_harness/private/implementation-08-private-uat.yaml` by copying the tracked sanitized example:

```powershell
Copy-Item agent_harness/implementation-projects/active/implementation-08-private-uat.example.yaml agent_harness/private/implementation-08-private-uat.yaml
```

Replace the synthetic inputs and expected values locally. Keep questions, answers, UUIDs, paths, note text, and literal absence values in the ignored fixture only. The fixture supports these case shapes:

- one private narrow-fact question;
- one private exact-count or exact-absence question;
- one private multi-turn referential continuation;
- one private comparative or temporal question;
- the expected evidence boundary for each question;
- whether any question is permitted to make a negative claim.

Run the private baseline from the repository root:

```powershell
python tools/implementation08_private_uat.py --fixture agent_harness/private/implementation-08-private-uat.yaml --repo-root .
```

The runner validates the fixture, uses the existing production runtime, creates a fresh persisted thread per case, resumes completed checkpoints, writes raw records only beneath `agent_harness/private/`, and never ingests automatically. It fails clearly if the configured vault, persisted inventory, model backend, or required secrets are unavailable. The runtime's normal configured data-root artifacts remain operator-private and are not exported by this tool.

An operator may generate an aggregate report for inspection without committing it:

```powershell
python tools/implementation08_private_uat.py --fixture agent_harness/private/implementation-08-private-uat.yaml --repo-root . --export-redacted agent_harness/private/implementation-08-private-uat-redacted.json
```

The redacted report contains only case IDs, statuses, component statuses, operator names, counts, latency, coverage, support grades, safe reason categories, and hashes. GitHub Actions must never receive the private fixture. A redacted report may be committed only after operator inspection and an explicit decision; this pass does not commit one.
