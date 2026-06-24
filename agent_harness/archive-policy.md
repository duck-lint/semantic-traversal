# Archive Policy

Archive completed implementation work so later sessions can resume from repo-local memory instead of chat history.

## Archive When

- the verification contract is complete, blocked with explicit owner, or deferred with owner
- behavior claims have passing named user-facing acceptance probes or explicit downgrades
- decisions are recorded
- known failures are updated or ruled out
- remaining risks are explicit
- the same turn also updates `harness/open-decisions.md` and any paused or deferred pointers that still target the completed bundle

## Archive Summary Must Include

- project prefix
- goal and final status
- files or surfaces changed
- verification evidence
- user-facing acceptance result
- decisions made
- known failures added or updated
- unresolved risks and revisit triggers
- next end goal only if the user has already provided it

## State Folders

Use explicit state folders under `harness/implementation-projects/`:

- `active/` for the single implementation bundle currently in live execution
- `archive/` for completed implementation bundles

Keep only `templates/` and the state folders in the root `implementation-projects/` directory.

## Same-Turn Closeout

When work changes implementation state from active to complete, do the archive move and pointer cleanup in the same turn:

- move the numbered bundle from `active/` to `archive/`
- add or update the archive summary
- repoint `harness/open-decisions.md` and any paused or deferred references that still target old active paths
- if any of this cannot be completed, mark closeout blocked with owner
