# Implementation 02 Plan

## Intent

Refresh `implementation-02` into a journal-first, provenance-rich ingestion bundle for latent-space preparation only. This bundle authorizes ingestion from both repo-root `corpus/` and snippet fixtures in `tests/fixtures/`, with tmp-root artifacts, deterministic section resolution from observable Markdown structure and label text, paragraph-first chunking inside resolved section context, stable paragraph chunk IDs derived from note identity plus resolved section identity plus paragraph ordinal, separate content hashes for drift detection, SQLite as the default materialization surface where possible, JSON manifests where possible, deterministic reingest verification, and a later local embedding seam that stays easily swappable.

Delivery posture for the eventual implementation under this bundle is conditional:

- report `live-wired` only if the named ingestion and reingest probes pass through the real caller path against the authorized corpus roots
- report `scaffold-only` if the path stops at fixtures, dry runs, incomplete storage materialization, or an unexercised embedding seam

## Admissibility Report

- Invariant constraints:
  - `harness/project-spec/project_spec_0.1.2.json` remains the invariant authority.
  - `implementation-02` must stay inside latent-space ingestion plus embedding preparation.
  - Retrieval, traversal, retrieval coverage, and synthetic-node promotion remain out of scope.
  - Do not silently normalize the graph conflict between current decision authority and the spec. Graph work must stay deferred or blocked until spec alignment is explicit.
- Task constraints:
  - Update only the active `implementation-02` plan and tracker.
  - Reflect current decision authority: `corpus/` plus `tests/fixtures/`, tmp-root artifacts, SQLite default storage where possible, JSON manifests where possible, and approved local swappable embeddings.
  - Replace whole-section chunking with paragraph-first chunking inside resolved section context.
  - Make journal section resolution deterministic across Markdown headings and inline `Label:` sections without hardcoding any specific label list.
  - Make the chunk identity formula explicit: stable `chunk_id = note identity + section identity + paragraph ordinal`.
  - Keep content hash as a separate stored field for change detection; do not bake content-derived hashing into `chunk_id`.
  - Keep retrieval and traversal out of scope.
- Constraint conflicts:
  - The spec still describes graph-layer surfaces and frontmatter-related graph-adjacent structures more broadly than current live decision authority allows.
  - The spec example `chunk_id = note identity + section ordinal + optional content hash` is too coarse for the authorized paragraph-addressable chunk unit and too permissive about mixing content hashing into identity.
- Allowed transformation types:
  - Create or revise `harness/implementation-projects/active/implementation-02-plan.md`.
  - Revise `harness/implementation-projects/active/implementation-02-tracker.md`.
  - Do not edit `harness/open-decisions.md`, the spec, runtime code, or tests.
- Affected surfaces:
  - `harness/implementation-projects/active/implementation-02-plan.md`
  - `harness/implementation-projects/active/implementation-02-tracker.md`
- Non-affected surfaces:
  - `harness/open-decisions.md`
  - `harness/project-spec/project_spec_0.1.2.json`
  - runtime code, tests, and archive files
- Admissibility checks:
  - The plan must treat each paragraph as the core chunk unit, with one ordinal chunk per paragraph under a resolved section label.
  - The plan must make `chunk_id` explicit as stable note identity plus stable section identity plus paragraph ordinal, with content hash stored separately.
  - The plan must scope SQLite plus JSON manifests into the core seams.
  - The plan must place local swappable embeddings later in the same bundle, not out of scope.
  - The plan must explicitly defer graph work on spec alignment instead of a frontmatter allowlist.
  - Acceptance probes must require resolved journal section identities that are more specific than the enclosing date heading when observable heading or label structure is present.
  - The fixture journal probe must require the note's actual inline section labels to survive resolution as their own sections, not only the enclosing date title.
  - The corpus journal probe must require the note's actual heading labels to survive resolution as their own sections.
  - Acceptance probes must reflect journal-shaped section identity and paragraph-level chunk identity.
  - The localized edit probe must prove the edited paragraph record keeps the same `chunk_id` and only updates content-derived fields.
- Stop conditions:
  - Stop if truthfully revising the bundle would require editing `harness/open-decisions.md` or the spec.
  - Stop if the bundle would expand into retrieval or traversal.

## Observed Evidence

- `harness/implementation-projects/active/` contained an `implementation-02` tracker in the real workspace, but no corresponding active plan file.
- `harness/open-decisions.md` now records five material decisions for the current bundle: dual corpus roots, tmp artifact posture, approved local embeddings, no frontmatter-as-nodes intent, and SQLite-plus-JSON storage preference.
- `tests/fixtures/JOURNAL/2025-09/01_Monday.md` already shows journal-shaped inline section labels.
- `corpus/LAYER-1 PILLARS/PILLAR 2-DYNAMIC COHERENCE/JOURNAL/2025/2025-08/24_Sunday.md` shows heading-style journal sections whose heading text becomes the section label.
- Repo-root `corpus/` contains broader vault-style material, including longform notes whose file topology is semantically meaningful.
- `harness/project-spec/project_spec_0.1.2.json` still names path topology, frontmatter metadata, `corpus_nodes`, `vector_index_surface`, and graph-layer surfaces, but its chunk example is section-level and its graph posture is broader than the current live decision authority.
- `harness/donor_code/embed_sentence_transformers.py` takes `chunk_id`, `profile_ref`, and paragraph text as separate fields, then returns vectors keyed by the same `chunk_id`.
- `harness/donor_code/bumblebee-source-pipeline/src/lib.rs` models `chunk_id` separately from `chunk_hash` and compares prior versus current entries by stable `chunk_id` plus changed `chunk_hash`, which matches the requested identity-versus-content split.

## Planned Work

1. Seam 1: corpus authority and tmp artifact posture
   - Treat repo-root `corpus/` as the primary corpus authority and `tests/fixtures/` snippet notes as the deterministic fixture corpus for focused verification.
   - Keep ingestion markdown-note-first for this bundle. Non-markdown attachments inside `corpus/` are not part of the current ingestion contract.
   - Keep artifact posture aligned with `implementation-01`: tmp-root output only for this bundle.
   - Every ingest record must preserve which root a note came from, the note path, and path-topology context needed for later retrieval preparation.
2. Seam 2: paragraph-first chunk contract inside resolved section context
   - Resolve section identity deterministically before assigning one ordinal chunk per paragraph.
   - For journal-shaped notes, treat the note date title as note-level context, not as the resolved section identity, whenever a more specific observed heading or inline label is present.
   - If a markdown heading resolves to journal section content, normalize the heading by removing markdown heading markers plus one trailing colon and use the resulting heading text as the section identity.
   - If a paragraph begins with an inline `Label:` prefix, use that observed label text as the section identity after removing the trailing colon, and treat the remainder of that paragraph plus following paragraphs up to the next resolved section boundary as that section body.
   - Preserve the observed heading or label text itself as the section identity; do not collapse it back to the enclosing date heading and do not invent synonym remaps that are not directly observable in the note.
   - Once the section identity is resolved from the heading or inline label, each paragraph under that section becomes its own ordinal chunk.
   - Replace whole-section payloads with paragraph-level payloads.
   - Preserve note identity, note path topology, resolved section identity, and local paragraph ordinals inside that section.
   - Chunk payload text must contain only the local paragraph, not the full section body.
   - The contract must support inline-label journal examples in the fixture corpus and heading-style journal examples in the repo corpus, but those examples are validation inputs, not a hardcoded label list.
   - The serialized `chunk_id` must be explicit and stable: note identity plus deterministic section identity plus paragraph ordinal within that section.
   - Content hash must be stored as a separate field over the local paragraph payload and used for drift detection or update decisions only; it must not be part of `chunk_id`.
   - A paragraph text edit that leaves note identity, section identity, and paragraph ordinal unchanged must update content-derived fields in place for the same `chunk_id`, not mint a replacement identity.
3. Seam 3: SQLite materialization plus JSON manifests
   - Materialize note and chunk outputs into SQLite by default where possible.
   - Emit JSON manifests where possible so operators and downstream code can inspect source roots, ingest runs, note identities, chunk identities, and storage locations without scraping SQLite directly.
   - Keep the SQLite-plus-manifest surface inside tmp-root artifacts for this bundle.
4. Seam 4: deterministic reingest verification
   - Prove unchanged whole-vault reingest preserves the same `chunk_id` and content hash for unchanged paragraph chunks.
   - Prove a localized paragraph text edit preserves the edited paragraph's `chunk_id` and only updates its content hash and payload, while unaffected paragraph records in the same note and section remain unchanged.
   - Reingest verification must use at least one journal-shaped fixture note and one repo-root longform markdown note so the plan stays honest about both corpus roots.
5. Seam 5: local swappable embedding preparation
   - After SQLite materialization and deterministic reingest probes pass, add a local sentence-transformers-style embedding seam for the materialized chunks.
   - The embedding boundary must stay easily swappable so a different local model or API-backed model can be substituted later without rewriting the upstream note-discovery, chunking, or storage seams.
   - Retrieval and traversal remain out of scope even after this seam lands.

## Non-Goals

- retrieval packet assembly, traversal, ranking, or coverage evaluation
- graph extraction or graph projection inside `implementation-02`
- frontmatter-as-node behavior
- synthetic-node write-back or promotion
- non-markdown attachment ingestion
- durable storage migration outside tmp-root SQLite plus JSON manifests

## Delivery Posture And Acceptance Criteria

Named user-facing acceptance probes for the future implementation under this bundle:

1. `probe_fixture_journal_section_paragraph_chunking`
   - Ingest a journal fixture note such as `tests/fixtures/JOURNAL/2025-09/01_Monday.md`.
   - Expected observable result: the ingest output resolves the note's actual inline section labels rather than only `September 01, 2025`, then emits one ordinal chunk per paragraph inside those resolved sections, derives each `chunk_id` from note identity plus resolved section identity plus paragraph ordinal, stores content hash separately, and keeps each chunk payload to only the local paragraph text.
2. `probe_repo_corpus_journal_heading_section_resolution`
   - Ingest a heading-style corpus journal note such as `corpus/LAYER-1 PILLARS/PILLAR 2-DYNAMIC COHERENCE/JOURNAL/2025/2025-08/24_Sunday.md`.
   - Expected observable result: the ingest output resolves the note's actual heading labels as section identities in their own right instead of collapsing them under the enclosing note title, then emits one ordinal chunk per paragraph under each resolved section with stable paragraph-addressable `chunk_id` values and separate content hashes.
3. `probe_repo_corpus_longform_paragraph_chunking`
   - Ingest one repo-root longform markdown note from `corpus/`.
   - Expected observable result: the ingest output preserves corpus-root provenance, note identity, section identity, and paragraph boundaries without collapsing the note into whole-section payloads, with one ordinal chunk per paragraph inside each resolved section and stable paragraph-addressable `chunk_id` values that do not depend on paragraph text.
4. `probe_reingest_unchanged_preserves_chunk_ids`
   - Reingest the whole authorized vault without changing any note content.
   - Expected observable result: note identities, paragraph `chunk_id` values, and content hashes for unchanged paragraphs remain stable across reingest.
5. `probe_localized_paragraph_edit_changes_only_affected_paragraphs`
   - Modify one paragraph inside a stable section in a journal-shaped note and reingest.
   - Expected observable result: the edited paragraph record is updated in place at the same `chunk_id`, with changed payload and content hash only for that paragraph, while unaffected paragraphs in the same section and note keep both their prior `chunk_id` values and prior content hashes.
6. `probe_sqlite_manifest_materialization`
   - Run the ingest path through SQLite plus JSON manifest output.
   - Expected observable result: operators can see the materialized note and chunk records in SQLite and can inspect the corresponding ingest manifest in JSON without ambiguity about source roots, stable chunk identity, or separate content-hash state.

Failure conditions that block completion:

- chunk payloads still contain whole sections instead of local paragraph text
- chunk identity does not distinguish multiple chunks inside one section
- chunk identity is derived from paragraph content or embeds content hash
- a localized paragraph text edit mints a new `chunk_id` for the same paragraph address
- SQLite or manifest outputs are missing from the core ingestion path
- graph work is reported as active behavior despite the unresolved spec conflict

## Current Repo Runtime State

- `implementation-01` is archived complete.
- The active workspace held only `implementation-02-tracker.md`; the active plan file was missing and had to be restored as part of this planner refresh.
- `harness/open-decisions.md` now carries the current task authority for corpus roots, artifact posture, embeddings, graph intent, and storage preference.
- `corpus/` is present at repo root and `tests/fixtures/` contains deterministic journal snippets, including inline-label journal sections that must not collapse into the enclosing date title.

## Assumptions And Unknowns

Assumptions:

- Markdown-note ingestion is the intended first slice for both authorized corpus roots.
- SQLite and JSON manifests can coexist in tmp-root artifacts without creating a broader storage commitment.

Unknowns:

- The exact SQLite schema and manifest filenames the implementer will choose.
- The exact normalization rule the implementer will use before computing the separate paragraph content hash field.
- Which repo-root longform note will serve as the best standing acceptance sample for chapter-like identity during implementation.
- Whether the implementer will need a small, explicit allowlist for journal-only section-label normalization beyond stripping markdown markers and one trailing colon.
- The spec amendment path needed before graph work can become active again.

## Affected And Non-Affected Surfaces

Planning surfaces changed in this bundle:

- `harness/implementation-projects/active/implementation-02-plan.md`
- `harness/implementation-projects/active/implementation-02-tracker.md`

Implementation surfaces that must eventually move together for this bundle to be truthful:

- note discovery across `corpus/` and `tests/fixtures/`
- tmp-root artifact routing
- resolved journal section identity detection across inline-label and heading-style notes
- paragraph-first chunking logic with resolved section identity preservation
- SQLite materialization for notes and chunks
- JSON ingest-manifest emission
- deterministic reingest verification surfaces
- later local embedding seam with a swappable backend boundary

Surfaces that must not move under this bundle:

- retrieval and traversal runtime paths
- graph extraction or graph storage
- synthetic-node promotion
- spec and open-decision authority files

## Verification Contract Summary

- Structural verification:
  - confirm both authorized corpus roots are represented in the ingestion contract
  - confirm journal section resolution prefers stable inline or heading labels over the enclosing date title when both are present
  - confirm chunk identity is paragraph-addressable within resolved section context, with `chunk_id = note identity + section identity + paragraph ordinal`
  - confirm paragraph content hash is stored separately from `chunk_id`
  - confirm SQLite plus JSON manifests are part of the core seams
- Behavior verification:
  - run `probe_fixture_journal_section_paragraph_chunking`
  - run `probe_repo_corpus_journal_heading_section_resolution`
  - run `probe_repo_corpus_longform_paragraph_chunking`
  - run `probe_reingest_unchanged_preserves_chunk_ids`
  - run `probe_localized_paragraph_edit_changes_only_affected_paragraphs`
  - run `probe_sqlite_manifest_materialization`
- Downgrade rule:
  - if implementation stops at fixture-only shape proof, missing SQLite materialization, or an unverified embedding path, report `scaffold-only`
- Review obligation:
  - reviewer must reject any claim that paragraph-level chunking, SQLite materialization, or swappable embeddings are implemented if the evidence proves only file presence, tests, or schema shape

## Completion Rule

- Do not mark behavior complete on fixture, mock, dry-run, serialization, type, field, file, path, route, crate, config, or nominal-caller evidence alone.

## Approval Gates

- [ ] Schema
- [ ] API
- [ ] Auth
- [ ] Storage
- [ ] Deployment
- [ ] Destructive operation
- [ ] Broad architecture
- [ ] Project-intent authority not covered by spec or current authorization

Gate notes:

- Storage is covered for this bundle because current decision authority already approves tmp-root SQLite plus JSON manifests where possible.
- Broad architecture remains blocked for graph work until the spec and current decision authority are aligned.
- Raise a new gate if implementation tries to widen `implementation-02` into retrieval, traversal, graph activation, or a durable non-tmp storage posture.

## Handoff Packet For Next Agent

## Role

- From: planner
- To: implementer
- Requested action: implement seams 1 through 4 against the explicit stable paragraph chunk identity contract, then start seam 5 only after the named ingestion and reingest probes pass

## Project And Task

`implementation-02` is now the active journal-first ingestion bundle for latent-space preparation only. The plan authorizes corpus discovery from repo-root `corpus/` plus `tests/fixtures/`, tmp-root artifact routing, deterministic journal section resolution from observable heading or inline label text, paragraph-first chunking inside resolved section context, stable `chunk_id = note identity + section identity + paragraph ordinal`, separate paragraph content hashes for drift detection, SQLite plus JSON manifest materialization, deterministic reingest probes, and a later local swappable embedding seam. Retrieval and traversal remain out of scope. Graph work is intentionally deferred because current decision authority conflicts with the spec.

## Admissibility Report

- Invariant constraints:
  - latent-space ingestion plus embedding preparation only
  - no retrieval, traversal, or synthetic-node work
  - no silent graph normalization around the spec conflict
- Task constraints:
  - current bundle must reflect dual corpus roots, tmp artifacts, deterministic journal section resolution, SQLite-plus-JSON materialization, paragraph-first chunking, explicit stable paragraph chunk IDs, separate content hashes, and later local embeddings
- Constraint conflicts:
  - graph posture conflict between current decision authority and the spec
  - section-only `chunk_id` example in the spec is too coarse for the current chunk unit and too permissive about content-derived identity
- Allowed transformation types:
  - implement seams 1 through 5 within the active bundle
- Affected surfaces:
  - note discovery across `corpus/` and `tests/fixtures/`
  - tmp-root artifact routing
  - resolved journal section identity detection across inline-label and heading-style notes
  - paragraph-first chunking logic with stable paragraph-addressable `chunk_id` values and separate content hashes
  - SQLite materialization for notes and chunks
  - JSON ingest-manifest emission
  - deterministic reingest verification surfaces
  - later local embedding seam with a swappable backend boundary
  - active tracker updates needed to record implementation progress truthfully
- Non-affected surfaces:
  - spec and open decisions
  - retrieval, traversal, graph activation, and synthetic-node runtime paths
  - archive files
- Admissibility checks:
  - whole-section chunks removed from the core contract
  - journal section resolution prefers the note's observed heading or inline label text over the enclosing date title
  - `chunk_id` is stable note identity plus section identity plus paragraph ordinal
  - content hash is stored separately from `chunk_id` and drives change detection
  - SQLite plus JSON manifests in core seams
  - embeddings no longer out of scope
  - graph work deferred on spec alignment
- Stop conditions:
  - report a blocker if the bundle still implies retrieval, traversal, or active graph work

## Authorized Boundaries

- Affected surfaces:
  - note discovery across `corpus/` and `tests/fixtures/`
  - tmp-root artifact routing
  - resolved journal section identity detection across inline-label and heading-style notes
  - paragraph-first chunking logic with stable paragraph-addressable `chunk_id` values and separate content hashes
  - SQLite materialization for notes and chunks
  - JSON ingest-manifest emission
  - deterministic reingest verification surfaces
  - later local embedding seam with a swappable backend boundary
  - active tracker updates needed to record implementation progress truthfully
- Non-affected surfaces:
  - spec and open decisions
  - retrieval, traversal, graph activation, and synthetic-node runtime paths
  - archive files
- Boundaries not authorized:
  - spec edits
  - open-decision edits
  - retrieval, traversal, graph activation, or synthetic-node implementation
  - durable non-tmp storage expansion
  - compatibility or fallback shims that create a longer-lived support path

## Evidence And Assumptions

- Observed evidence:
  - `harness/open-decisions.md` records the current bundle decisions
  - `tests/fixtures/` includes inline-label journal-shaped notes with stable section labels
  - `corpus/` includes heading-style journal notes whose section labels are carried by markdown headings
  - repo-root `corpus/` contains broader longform markdown material
  - `harness/donor_code/embed_sentence_transformers.py` keys embedding output by `chunk_id` while accepting content separately as `text`
  - `harness/donor_code/bumblebee-source-pipeline/src/lib.rs` carries `chunk_id` and `chunk_hash` separately and compares same-ID entries by hash across reingest
- Inferences:
  - the active bundle needed restoration plus seam-order revision, not just wording cleanup
  - stable paragraph identity and content drift need to be modeled as separate fields
  - graph work cannot truthfully remain an optional near-term seam under current authority
- Unknowns:
  - exact longform acceptance sample to use during implementation
  - exact SQLite schema and manifest filenames
  - exact normalization rule for the separate paragraph content hash

## Expected Change

The next agent should implement the authorized ingestion seams so stable paragraph-addressable `chunk_id` values and separate content hashes survive whole-vault reprocessing, while preserving the graph-deferral boundary and the resolved journal-section contract for both inline-label and heading-style notes.

## Acceptance Criteria

- the runtime resolves heading-style and inline-label journal sections as their own section identities rather than collapsing them to the enclosing date title
- paragraph-first chunking is implemented with stable `chunk_id = note identity + section identity + paragraph ordinal`
- paragraph content hash is stored separately from `chunk_id` and used for change detection only
- `probe_reingest_unchanged_preserves_chunk_ids` proves unchanged whole-vault reprocessing preserves both paragraph `chunk_id` values and content hashes
- `probe_localized_paragraph_edit_changes_only_affected_paragraphs` proves the edited paragraph record updates in place at the same `chunk_id` while unaffected paragraph records remain unchanged
- SQLite plus JSON manifests are part of the real ingest path, not just helper output
- any started embedding seam consumes stable upstream `chunk_id` values without redefining chunk identity
- graph work remains deferred because of the spec conflict

## Stop Conditions

Acceptance criteria achieved, or the implementer reports a blocker that would require spec edits, open-decision edits, or widening into retrieval, traversal, graph activation, or durable non-tmp storage.

## Closeout Note

- When this bundle completes, move it from `active/` to `archive/`.

