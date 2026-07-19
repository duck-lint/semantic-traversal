# Implementation 07 Baseline

Captured: 2026-07-18

## Repository state

- Existing archived implementation bundles: implementation-01 through implementation-06.
- Active bundle created: implementation-07.
- Baseline commit: `af34c33 Move retrieval planner defaults into runtime config`.
- Working tree was clean before these governance files were added.

## Required baseline surfaces

Baseline SHA-256 hashes captured before production changes:

- `semantic_traversal.runtime.yaml`: `582F1959C9CF9D5D6EC39294A940CE3D15F675532E844E8ACFD5FE15B6DA9FE1`
- `semantic_traversal/retrieval_plan.py`: `04D26E4CC857FB7FDB7E8C622E11E151BB923A693C77E88B88585663EC6B857B`
- `semantic_traversal/retrieval_resolver.py`: `EFC15BD504CC8A932AF08CE7EAF38696C3DD22BF7C57CC1B9B9D16A9AB2BF8A9`
- `semantic_traversal/resource_inventory.py`: `8F64407EC522D0CF99A430283D274DE1489EAB1CBA3C67B2941A5366D487F8D5`
- `semantic_traversal/runtime.py`: baseline command captured the pre-change hash prefix `05D588C4ACCFA78AFC9253343F139C34B9DA986B2`; the terminal output truncated the remainder, so this surface is tracked by the baseline commit plus the post-change hash below until a non-truncated artifact writer is added.
- `semantic_traversal/ingest.py`: `4700F7E1C12CD28E37480681B9607456FE2278986CF35D618D381C2BC990DB77`

The relevant structural surfaces are:

- `semantic_traversal.runtime.yaml`
- semantic compiler prompt and frontier synthesis prompt in that YAML
- `semantic_traversal/retrieval_plan.py`
- `semantic_traversal/runtime.py`
- `semantic_traversal/retrieval_resolver.py`
- `semantic_traversal/resource_inventory.py`
- ingest schema/index setup in `semantic_traversal/ingest.py`
- tests and plugin build/type-check commands

## Representative probes required before production changes

1. exact scoped query;
2. semantic traversal query;
3. graph-seeded query;
4. referential continuation;
5. comparison query;
6. exact no-match query;
7. multi-semantic-query packet;
8. graph expansion with zero graph-selected chunks;
9. genealogy query: `Where did my current idea of semantic geometry actually come from, including precursors that I did not name that way at the time?`

## Baseline interpretation

The current runtime has production retrieval code, but the attached acceptance criteria are not yet proven by the checkout. Before this bundle’s runtime slice, the current path passed only `semantic_queries[0]` to vector retrieval, built inventory from live tables, and had no visible FTS5 table in the ingest schema excerpt. The vector and exact gaps are now covered by focused tests; inventory persistence, FTS5, graph direction/fairness, ingest atomicity, temporal substrate, and final UAT remain open.
