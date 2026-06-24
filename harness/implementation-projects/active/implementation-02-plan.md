# Implementation 02 Plan

## Intent

Create the next active implementation bundle for deterministic latent-space ingestion only. The implementation this bundle authorizes must be limited to materializing `primary_corpus` Markdown notes into provenance-rich `corpus_nodes` plus the minimum ingest artifacts needed to prove deterministic chunking, reingest stability, and localized change behavior.

The smallest implementation seam authorized by this bundle is now explicit: one bounded ingestion caller that takes an explicit `corpus_root` and explicit `artifact_root`, scans Markdown notes only, decomposes them by Markdown structure, preserves section ordinals plus note provenance, and writes deterministic `corpus_nodes` materialization artifacts through the same path the named acceptance probes will exercise.

This bundle intentionally stops before activation, traversal, retrieval-packet assembly, coverage evaluation, synthetic-node promotion, and live vector embeddings. Optional lexical indexing or initial graph projection may be attempted only after the core chunking and provenance seams succeed and only inside the gates named below.

## Admissibility Report

- Invariant constraints:
  - `harness/project-spec/project_spec_0.1.2.json` is the only invariant authority.
  - The next admissible transition after `implementation-01` is latent-space ingestion centered on `primary_corpus` to deterministic `corpus_nodes` materialization.
  - Semantic chunking must follow the spec: Markdown-structure decomposition, preserved section ordinals, preserved frontmatter references, preserved path-topology references, preserved source-note identity, and stable `chunk_id` derived from note identity plus section ordinal, with any content-sensitive differentiation carried separately rather than by widening scope into embeddings.
  - Any initial graph scope in this bundle, if attempted at all, must stay limited to Obsidian-native structure: wiki links, note nodes, chunk containment edges, path topology, and selected frontmatter values.
  - Activation, traversal, retrieval-packet assembly, coverage evaluation, and synthetic-node promotion remain out of scope.
- Task constraints:
  - Create `harness/implementation-projects/active/implementation-02-plan.md`.
  - Create `harness/implementation-projects/active/implementation-02-tracker.md`.
  - The plan must be specific enough that an implementer can start seam 1 without improvising the chunking contract.
  - The plan must carry forward targeted bundle-local unknowns, future-default questions, and optional-seam gates where task authority is still intentionally absent.
  - Optional lexical or initial graph follow-on may be described only after the core chunking and provenance seams, and live embedding work must remain explicitly blocked.
- Constraint conflicts:
  - No conflict blocks seams 1 through 3 so long as the implementation requires caller-supplied `corpus_root` and `artifact_root`.
  - Future operator defaults for corpus source and artifact placement remain bundle-local questions rather than present blockers for the core ingestion seams.
  - Live embeddings remain out of scope for this bundle unless a later task explicitly authorizes that separate seam.
  - Frontmatter allowlist work remains a gate for the optional graph seam only.
- Allowed transformation types:
  - Create the active `implementation-02` plan and tracker bundle only.
  - Define seam order, acceptance probes, verification duties, and approval gates for the next implementation transition.
  - Do not edit `harness/open-decisions.md`, runtime code, tests, or the project spec in this turn.
- Affected surfaces:
  - `harness/implementation-projects/active/implementation-02-plan.md`
  - `harness/implementation-projects/active/implementation-02-tracker.md`
- Non-affected surfaces:
  - `harness/open-decisions.md`
  - `semantic_traversal/runtime.py`
  - `semantic_traversal/storage.py`
  - `semantic_traversal/cli.py`
  - `semantic_traversal/probes.py`
  - `tests/test_first_build_target.py`
  - all activation, traversal, retrieval, synthetic-node, vector, and graph-runtime implementation surfaces
- Admissibility checks:
  - Name the smallest truthful seam order for deterministic ingestion only.
  - Keep the semantic chunking contract explicit enough that section decomposition and `chunk_id` rules are not left to implementer guesswork.
  - Include named acceptance probes for ingestion determinism and provenance.
  - Treat live embeddings as out of scope for the core seams unless explicit user authorization opens a later bundle.
  - Treat frontmatter allowlist selection as a gate for optional graph work only, not as a blocker for seams 1 through 3.
  - Make non-goals and conditional follow-on seams explicit.
- Stop conditions:
  - Stop if the plan cannot be grounded in the spec and current runtime state without silently choosing missing decisions.
  - Stop if the plan would need to widen into activation, traversal, retrieval, synthetic-node, or live embedding work.
  - Stop if seam 1 would require a default corpus source or default artifact location rather than caller-supplied paths.

## Observed Evidence

- `harness/implementation-projects/active/` is empty except for `.gitkeep`, so `implementation-02` is the next live bundle slot.
- `implementation-01` is archived complete and its summary records a finished first-build-target runtime only.
- `harness/open-decisions.md` currently contains no live decisions and no pending decisions.
- `semantic_traversal/runtime.py`, `semantic_traversal/storage.py`, `semantic_traversal/cli.py`, and `semantic_traversal/probes.py` currently implement only the first-build-target thread, state, ledger, CLI, and probe surfaces.
- `harness/project-spec/project_spec_0.1.2.json` defines:
  - `primary_corpus` as an Obsidian-vault-like Markdown substrate where path topology and frontmatter are semantically meaningful.
  - `corpus_nodes` as Markdown-structure-derived chunks with preserved section ordinals, frontmatter references, path-topology references, source-note identity, and stable `chunk_id`.
  - `chunk_embed_store` as the ingestion boundary that may later emit corpus nodes, lexical records, graph records, and vector records.
  - `implementation_boundary.initial_graph_strategy` as Obsidian-native extraction only.
- The user-supplied PM evidence already names the intended core probes:
  - `probe_ingest_markdown_note_to_corpus_nodes`
  - `probe_reingest_unchanged_corpus_preserves_chunk_ids`
  - `probe_changed_section_only_changes_affected_chunks`
  - optional lexical and graph probes after explicit gates

## Planned Work

### Seam 1: Deterministic note discovery and chunk contract

Implement one bounded ingestion caller or entrypoint that requires:

- an explicit `corpus_root`
- an explicit `artifact_root`

No default live corpus source or default latent-space artifact location is authorized in this bundle. The caller may support probe-time temporary roots, but it must not silently choose a production-like default corpus or storage path.

The chunking contract for this seam is:

- Discover Markdown notes only from the supplied `corpus_root`.
- Normalize each note to one deterministic `source_note_id` based on the note's corpus-relative normalized path. This is the smallest admissible identity function over current observables because the spec treats file structure and path topology as semantically meaningful.
- Preserve the corpus-relative note path and path-topology components needed to reconstruct parent directory lineage later.
- Extract note frontmatter once per note and preserve it as note-level provenance available to each derived chunk.
- Decompose note content by Markdown structure in document order.
- Emit one monotonic `section_ordinal` per chunk, using 1-based ordering within a note.
- Treat a headingless preamble as its own chunk when present so the first heading section does not erase leading note content.
- Preserve the chunk's local heading label and heading ancestry when those observables exist.
- Materialize chunk text exactly as derived for that section, plus a separate `content_hash` field over normalized chunk text.
- Generate `chunk_id` from `source_note_id` plus `section_ordinal` only. In this bundle, content sensitivity belongs in `content_hash`, not in `chunk_id`, so unchanged sections keep stable identifiers across reingest.

Minimum required per-chunk semantics for seam 1:

- `chunk_id`
- `source_note_id`
- corpus-relative source path
- path-topology reference
- note frontmatter reference or embedded note-level frontmatter payload
- `section_ordinal`
- section heading or heading-path metadata when present
- `content_hash`
- chunk text

Exact file names, module names, and serialization format remain an implementer choice, but the observable contract above is fixed for this bundle.

### Seam 2: Core `corpus_nodes` materialization and ingest record

Using the seam 1 contract, materialize deterministic `corpus_nodes` artifacts under the supplied `artifact_root`.

Minimum artifact roles required for this seam:

- one durable corpus-node materialization surface containing all chunks in deterministic order
- one ingest or update record that captures:
  - source corpus root provenance
  - note count
  - chunk count
  - note-to-chunk coverage summary
  - enough run metadata to tell whether a later reingest overwrote or duplicated prior results

The initial ingestion posture for this bundle should be a deterministic rebuild or replace flow, not a background watcher, partial sync daemon, or append-only chunk accumulator. Reingesting the same corpus must not append duplicate chunk records.

### Seam 3: Reingest determinism and localized change behavior

After seam 2 exists, prove deterministic behavior over repeated runs:

- Reingesting an unchanged corpus through the same caller must preserve chunk ordering, `chunk_id`, `section_ordinal`, note provenance, and `content_hash`.
- Editing only the body text inside one existing Markdown section should affect only the corresponding derived chunk content for that section and any run-level summary hashes.
- Structural edits that add, remove, or reorder headings are allowed to renumber later sections within the same note; that broader renumbering is not the acceptance case for this seam.

The acceptance probe for localized change must therefore use a same-heading body-text edit, not a heading-topology edit, so the result is falsifiable and appropriately narrow.

### Seam 4: Optional lexical materialization after core seam pass

This seam is conditional and may begin only after seams 1 through 3 pass.

If the user wants it in `implementation-02`, the lexical follow-on may:

- index chunk text plus selected non-sensitive note metadata
- retain linkage back to `chunk_id` and `source_note_id`
- stay storage-local to the supplied `artifact_root` unless a later decision authorizes another location

Do not widen this seam into embeddings, hybrid ranking, activation, or retrieval packet assembly.

### Seam 5: Optional initial graph projection after explicit frontmatter decision

This seam is conditional and may begin only after seams 1 through 3 pass and the user answers the frontmatter allowlist question.

If attempted, graph extraction must stay limited to:

- note nodes
- chunk containment edges
- wiki-link note-to-note edges
- path-topology references
- only the explicitly allowlisted frontmatter fields

Do not infer a richer ontology, synthetic-node provenance, traversal ranking, or graph runtime in this bundle.

## Non-Goals

- latent-space activation, semantic traversal, retrieval packet assembly, or coverage evaluation
- vector embeddings, provider selection, model selection, or spend-bearing API work
- synthetic-node promotion or write-back into `primary_corpus`
- broader graph ontology, tag modeling, or graph runtime beyond the optional Obsidian-native extraction gate
- background watch mode, automatic sync loops, or incremental scheduler work
- choosing a default live corpus source or default artifact location without explicit user authority
- reworking the current first-build-target thread runtime under `semantic_traversal/runtime.py`, `semantic_traversal/storage.py`, `semantic_traversal/cli.py`, or `semantic_traversal/probes.py`

## Delivery Posture And Acceptance Criteria

Named user-facing acceptance probes for the future implementation:

1. `probe_ingest_markdown_note_to_corpus_nodes`
   - Run the intended ingestion caller against a supplied Markdown corpus root containing at least one note with frontmatter, a headingless preamble, nested headings, and a nested relative path.
   - Expected observable result: deterministic `corpus_nodes` materialization exists under the supplied artifact root and each chunk preserves source-note identity, path topology, note frontmatter provenance, ordered `section_ordinal`, stable `chunk_id`, `content_hash`, and chunk text.
2. `probe_reingest_unchanged_corpus_preserves_chunk_ids`
   - Re-run the same caller against the same unchanged corpus and artifact roots.
   - Expected observable result: chunk counts, ordering, `chunk_id`, `section_ordinal`, provenance fields, and `content_hash` remain unchanged, and the materialization does not duplicate nodes.
3. `probe_changed_section_only_changes_affected_chunks`
   - Modify only the body text inside one existing Markdown section while leaving heading topology unchanged, then re-run ingestion.
   - Expected observable result: the edited section's chunk text and `content_hash` change, unaffected chunks preserve `chunk_id` and `content_hash`, and notes outside the edited note remain unchanged.
4. Optional lexical probe
   - If lexical indexing is implemented in this bundle, run a keyword or exact-phrase query over the same caller-managed artifact root.
   - Expected observable result: the expected chunk is returned with the correct `chunk_id` and `source_note_id`.
5. Optional initial graph probe
   - If graph projection is implemented in this bundle after allowlist approval, ingest a sample note set with a wiki link and allowlisted frontmatter.
   - Expected observable result: the graph output includes the expected note node, chunk containment edge, wiki-link edge, path-topology linkage, and only the approved frontmatter projections.

Failure conditions that block completion:

- any core probe runs only against fixture serializers or internal helpers rather than the intended ingestion caller
- unchanged reingest duplicates chunk records or renumbers unchanged sections without a structural source-note change
- the implementation silently chooses a default live corpus source or default artifact location
- optional lexical or graph work begins before the core chunking probes pass
- any live embedding work begins without explicit user authorization

## Current Repo Runtime State

- The repo currently contains a complete first-build-target turn runtime only.
- Current product/runtime code is limited to thread storage, CLI turn execution, and first-target probes under `semantic_traversal/`.
- No ingestion caller, corpus-node materializer, lexical indexer, graph extractor, or vector embedding surface exists yet.
- `implementation-01` lives only in archive, so `implementation-02` is the next active execution bundle.
- `harness/open-decisions.md` is empty, so defaults, embedding choices, and graph-frontmatter choices remain bundle-local unknowns or optional-seam gates rather than live pending decisions.

## Assumptions And Unknowns

Assumptions:

- The narrowest admissible seam is a caller that requires explicit corpus and artifact roots instead of choosing defaults.
- A normalized corpus-relative Markdown path is the smallest truthful first-pass `source_note_id`.
- `implementation-02` should use a deterministic rebuild or replace ingestion posture before any incremental sync logic.
- Stable `chunk_id` should remain content-insensitive in this bundle, with `content_hash` carrying content drift.

Unknowns:

- Which corpus root should become the authorized operator default, if any.
- Where latent-space artifacts should live by default outside temporary or explicitly supplied probe paths.
- Whether the user wants optional lexical indexing in this bundle after the core seam passes.
- Whether the user wants optional graph projection in this bundle and, if so, which frontmatter fields are allowlisted.
- Whether a later bundle should derive note identity from an explicit note-level identifier instead of the normalized relative path.

## Affected And Non-Affected Surfaces

Planning surfaces changed in this bundle:

- `harness/implementation-projects/active/implementation-02-plan.md`
- `harness/implementation-projects/active/implementation-02-tracker.md`

Runtime surfaces that must eventually move together for the core seam to be truthful:

- one bounded ingestion caller or entrypoint
- Markdown note discovery and parsing
- frontmatter extraction and note-level provenance preservation
- path-topology preservation
- deterministic chunk decomposition and `chunk_id` plus `content_hash` generation
- corpus-node materialization writer
- ingest or update record writer
- acceptance-probe surface for the three named core probes

Runtime surfaces that may move only if the conditional gates open:

- lexical materialization over corpus nodes
- initial graph projection limited to Obsidian-native structure and allowlisted frontmatter

Runtime surfaces that must not move in this bundle:

- existing thread-turn runtime surfaces from `implementation-01`
- latent-space activation, traversal, retrieval, coverage, and synthesis packet expansion
- vector embedding, hybrid retrieval, or provider spend surfaces
- synthetic-node promotion or any write-back path

## Verification Contract Summary

- Structural verification:
  - confirm every materialized chunk preserves note identity, path topology, frontmatter provenance, ordered `section_ordinal`, stable `chunk_id`, and `content_hash`
  - confirm reingest replaces or deterministically refreshes prior materialization rather than appending duplicates
- Behavior verification:
  - run `probe_ingest_markdown_note_to_corpus_nodes`
  - run `probe_reingest_unchanged_corpus_preserves_chunk_ids`
  - run `probe_changed_section_only_changes_affected_chunks`
  - run optional lexical or graph probes only if those conditional seams were actually implemented
- Review obligation:
  - reviewer must reject any claim based only on schemas, JSON examples, fixtures, unit tests, or helper-level chunk outputs without a passing named caller-level probe
- Downgrade rule:
  - if the implementation stops at serializers, helper functions, or fixture-only checks without the intended caller-level probes, report `scaffold-only` rather than complete behavior

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

- Storage becomes an approval gate if the implementer proposes a default artifact location rather than requiring a caller-supplied path.
- Project-intent authority remains open for any live embedding, vector backend, or spend-bearing work; that work is blocked in this bundle unless explicitly approved first.
- Initial graph projection remains gated on a frontmatter allowlist decision.
- Broad architecture becomes a gate if the implementer widens the seam into background sync, services, watchers, databases, or a richer graph runtime.

## Targeted Bundle-Local Unknowns And Optional-Seam Gates

1. Future authorized corpus source
   - Seam 1 remains admissible without this answer because it requires an explicit `corpus_root`; choose a live default only if a later operator-facing seam needs one.
2. Future latent-space artifact location
   - Seam 1 remains admissible without this answer because it requires an explicit `artifact_root`; choose a durable default only if a later workflow needs one.
3. Any later live vector embedding seam
   - Embeddings are out of scope for `implementation-02`; if a later bundle reopens them, backend, model, storage posture, and spend boundary will need fresh authority then.
4. Frontmatter allowlist for optional initial graph projection
   - Optional graph work must not project arbitrary frontmatter keys into nodes or edges before this gate is answered, but the core ingestion seams do not depend on it.

## Handoff Packet For Next Agent

## Role

- From: planner
- To: implementer
- Requested action: implement seam 1 first, using explicit `corpus_root` and `artifact_root` inputs to materialize deterministic `corpus_nodes` with the chunking contract defined here, then validate the first core probe before widening to reingest behavior

## Project And Task

`implementation-01` is archived complete and the current runtime only supports the first-build-target turn path. This bundle is the next admissible transition: deterministic ingestion from `primary_corpus` into `corpus_nodes`. The implementer must keep the work centered on Markdown chunking, provenance preservation, and deterministic materialization. Optional lexical or graph work is conditional and must not begin until the core chunking probes pass.

## Admissibility Report

- Invariant constraints:
  - deterministic `primary_corpus` to `corpus_nodes` materialization only
  - Markdown-structure chunking with preserved ordinals, frontmatter provenance, path topology, and source-note identity
  - no activation, traversal, retrieval, synthetic-node, or embedding work
- Task constraints:
  - start with seam 1 and caller-supplied corpus and artifact roots
  - follow the fixed chunking contract from this plan
  - keep optional lexical or graph work behind successful core probes
- Constraint conflicts:
  - none for seams 1 through 3 while explicit `corpus_root` and `artifact_root` inputs remain required
  - live embeddings stay out of scope unless a later task explicitly authorizes them
  - frontmatter allowlist remains a gate for optional graph projection only
- Allowed transformation types:
  - implement the bounded ingestion caller and core chunking/materialization behavior needed for the named core probes
- Affected surfaces:
  - ingestion caller surface
  - Markdown parsing and provenance preservation surfaces
  - chunk materialization and ingest-record surfaces
  - core probe surfaces for deterministic ingestion behavior
- Non-affected surfaces:
  - current thread runtime
  - activation, traversal, retrieval, synthetic-node, and vector surfaces
- Admissibility checks:
  - caller requires explicit roots
  - `chunk_id` is stable by note identity plus section ordinal
  - `content_hash` carries content drift
  - caller-level probes prove behavior
- Stop conditions:
  - stop if implementation requires a default corpus source or default artifact location
  - stop if implementation widens into vector, retrieval, activation, or richer graph runtime work

## Authorized Boundaries

- Affected surfaces:
  - new ingestion caller and its directly supporting chunking and materialization surfaces
  - probe surfaces needed to verify ingestion determinism
- Non-affected surfaces:
  - existing first-build-target runtime surfaces unless a narrow shared helper is truly necessary
  - any optional lexical or graph surface before the core probes pass
- Boundaries not authorized:
  - `harness/open-decisions.md`
  - activation, traversal, retrieval, coverage, synthetic-node, and embedding implementation
  - any default operator corpus source or default artifact placement chosen without approval

## Evidence And Assumptions

- Observed evidence:
  - active bundle slot is empty
  - current runtime supports first-build-target only
  - spec defines `corpus_nodes` and `chunk_embed_store` semantics directly
  - no live decisions authorize defaults or embeddings
- Inferences:
  - caller-supplied paths are the narrowest truthful way to start implementation without silently resolving open decisions
  - the core probes can run against a small supplied Markdown corpus without requiring user production data
- Unknowns:
  - exact filenames and modules
  - optional lexical and graph inclusion in this bundle
  - future operator defaults for corpus and artifact roots

## Expected Change

Implementer should add only the ingestion surfaces needed to turn a supplied Markdown corpus into deterministic `corpus_nodes` materialization and prove the first core probe. The implementation must preserve note provenance and keep `chunk_id` stable across unchanged reingest. If the work cannot stay inside caller-supplied roots or would require vector, retrieval, or graph-runtime expansion, report a blocker instead of improvising.

## Acceptance Criteria

The implementer completes the first handoff only if:

- the ingestion caller accepts explicit `corpus_root` and `artifact_root`
- one supplied Markdown corpus can be materialized into deterministic `corpus_nodes`
- the materialized output preserves the planned chunking contract
- `probe_ingest_markdown_note_to_corpus_nodes` passes through the intended caller path

## Stop Conditions

Acceptance criteria achieved, or a blocker is reported without widening scope.

## Closeout Note

- When this bundle completes, move it from `active/` to `archive/`.
