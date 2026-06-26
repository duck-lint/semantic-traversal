# Implementation 05 Plan

## Intent

Add a bounded lexical-query-discipline layer so retrieval ranking can prefer anchor-bearing content over noisy wrapper terms without leaving the lexical, deterministic, inspectable retrieval model.

This is not semantic search, embeddings, graph traversal, or a broad rewrite of ingestion.

## In Scope

- deterministic query analysis with role classes
- role-aware lexical retrieval ranking
- explicit `weak_lexical_match` behavior when only weak evidence is present
- preserved `no_query_terms`, `no_index`, and `no_matches` behavior
- persisted inspectable query-analysis fields in the turn artifacts
- CLI and probe observability for query roles and ranking diagnostics
- tests and probes proving wrapper-term discipline and anchor precedence

## Out of Scope

- vector retrieval
- embeddings
- graph traversal
- graph ontology
- synthetic node promotion
- character/persona lenses
- semantic coverage math beyond simple lexical diagnostics
- LLM-based query rewriting
- user-specific hard-coded examples
- broad CLI redesign
- broad storage redesign

## Admissibility Report

- Invariant constraints:
  - preserve deterministic, topic-agnostic lexical retrieval
  - do not add an LLM pre-call
  - do not add embeddings or graph logic
  - keep persisted artifact hash truthfulness intact
- Task constraints:
  - classify lexical terms by retrieval role
  - rank retrieval by role rather than flat matched-term count
  - keep the human-readable artifacts inspectable
  - distinguish weak lexical matches from usable minimal passes
- Constraint conflicts:
  - short wrapper terms can cause noisy matches; the ranking layer must correct for that without broadening the architecture
- Allowed transformation types:
  - runtime, storage-adjacent artifact shaping, CLI payloads, probes, tests, and archive docs
- Affected surfaces:
  - `semantic_traversal/runtime.py`
  - `semantic_traversal/cli.py`
  - `semantic_traversal/probes.py`
  - `tests/test_ingest_runtime.py`
  - `README.md` if the operator guide needs a short update
- Non-affected surfaces:
  - ingestion parsing and storage schema
  - embeddings, graphs, synthetic-node promotion
  - project-spec and open-decision files
- Admissibility checks:
  - query analysis is deterministic and topic-agnostic
  - semantic_context_packet includes the structured query analysis
  - retrieval ranking prefers anchor terms over weak wrapper terms
  - coverage reporting can distinguish weak lexical matches from minimal passes
  - existing hash integrity checks still pass
- Stop conditions:
  - stop if the work starts requiring embeddings, graph traversal, or an LLM pre-call
  - stop if deterministic ranking cannot be made inspectable without a broad redesign

## Observed Evidence

- `implementation-04` is archived complete and already leaves the repo with truthful artifact hashing and explicit retrieval statuses
- `semantic_traversal/runtime.py` already persists retrieval artifacts and hash-chained thread records
- `semantic_traversal/probes.py` already provides a lightweight probe pattern for deterministic inspection
- `tests/test_ingest_runtime.py` already covers lexical retrieval, no-index, no-match, no-query-terms, hash integrity, and CLI artifact-path observability
- the current flat lexical scoring is the only part that still treats wrapper words too equally

## Planned Work

1. Query analysis
   - Add a deterministic lexical query analysis function that classifies terms into ignored instruction terms, weak question terms, anchor terms, and support terms.
   - Preserve explicit `no_query_terms` behavior when no usable lexical terms remain.
   - Produce a query-intent classification with a minimal stable set of values.
2. Role-aware ranking
   - Rank chunks by role-aware evidence rather than raw matched-term count.
   - Prefer anchor-term evidence over weak wrapper-term evidence.
   - Avoid short-term substring noise where practical.
3. Artifact and CLI observability
   - Persist the structured query analysis in the turn artifacts.
   - Expose query roles and ranking diagnostics through the CLI and probes.
4. Tests and probes
   - Add wrapper-term discipline tests and anchor-precedence tests.
   - Keep existing hash-integrity and retrieval-status tests passing.

## Acceptance Criteria

- `implementation-05` plan and tracker exist
- query analysis is deterministic and topic-agnostic
- semantic_context_packet includes structured query role analysis
- retrieval ranking uses role-aware scoring
- wrapper terms no longer dominate anchor/content terms
- coverage reporting distinguishes weak lexical matches from minimal passes
- selected chunks expose score diagnostics
- previous hash-integrity guarantees still pass
- existing tests pass
- new tests and probes pass
- an archive summary records what changed, what was validated, and what remains out of scope

## Validation Commands

- `python -m unittest discover -s tests -v`
- `python -m semantic_traversal.probes probe_lexical_retrieval_fixture_hit --repo-root . --data-root %TEMP%\semantic-traversal-probes-hit`
- `python -m semantic_traversal.probes probe_lexical_retrieval_no_index --repo-root . --data-root %TEMP%\semantic-traversal-probes-noindex`
- `python -m semantic_traversal.probes probe_lexical_retrieval_no_match --repo-root . --data-root %TEMP%\semantic-traversal-probes-nomatch`
- `python -m semantic_traversal.probes probe_lexical_retrieval_no_query_terms --repo-root . --data-root %TEMP%\semantic-traversal-probes-noquery`
- `python -m semantic_traversal.probes probe_ledger_hash_artifact_integrity --repo-root . --data-root %TEMP%\semantic-traversal-probes-integrity`
- `python -m semantic_traversal.probes probe_turn_cli_artifact_paths --repo-root . --data-root %TEMP%\semantic-traversal-probes-cli`
- `python -m semantic_traversal.probes probe_lexical_query_analysis_roles --repo-root . --data-root %TEMP%\semantic-traversal-probes-queryroles`
- `python -m semantic_traversal.probes probe_anchor_term_retrieval_precedence --repo-root . --data-root %TEMP%\semantic-traversal-probes-anchor`

## Stop Conditions

- if the implementation needs embeddings, graph traversal, or an LLM pre-call
- if ranking cannot be kept deterministic and inspectable
- if any change breaks persisted artifact hash truthfulness

## Handoff Note

- The next human step after this bundle is continued human UAT over lexical query discipline

