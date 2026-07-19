# Implementation 07 Authority Map

## Authority

| Concern | Sole authority | Current consumer evidence | Guard |
| --- | --- | --- | --- |
| Operational limits, layer budgets, graph bounds, claim policy | `semantic_traversal.runtime.yaml` | `semantic_traversal/config.py`, `runtime.py` | Hash and config-consumer tests |
| Raw user input | Runtime input packet | `semantic_traversal/runtime.py` | Raw-input retention tests |
| Compiler proposal | Semantic compiler packet | `semantic_traversal/semantic_compiler.py`, `retrieval_plan.py` | Canonicalization diagnostics |
| Final selection and coverage | Runtime | `semantic_traversal/runtime.py` | Required-layer/coverage tests |
| Synthesis instructions | Frontier prompt in YAML | `config.py`, `runtime.py` | Prompt hash guard; prohibited outside Seam 8 |
| Full traversal provenance | Manifest | `runtime.py` | Manifest structure tests |
| Compact selected provenance | Retrieval packet | `runtime.py` | Packet-key/provenance tests |

## Seam file boundaries

| Seam | Allowed files | Prohibited files | Required evidence |
| --- | --- | --- | --- |
| 0 | tests, guards, implementation records, golden fixtures | production retrieval, prompts, YAML values | compile, unittest, plugin build, hashes, diff check |
| 1A | ingest, embeddings, config, runtime compatibility, ingest tests | compiler/frontier prompts, UI, unrelated ranking | failure isolation, identity invalidation, full-corpus gate |
| 1B | plugin metadata/config/tests/docs | Python runtime, YAML policy, prompts, packets | build and type check |
| 2A | resolver, runtime, plan, config, scope tests | prompts, frontier, UI | OR/AND/exact facet and soft/hard diagnostics |
| 2B | runtime, plan, coverage tests | prompts, UI | enumerated exact status and absence validity |
| 3 | runtime, config, tests | prompts, UI | unsupported/required blocking and runtime-owned policy |
| 4A | runtime/graph helpers, config, graph tests | prompts, compiler, UI | direction, fairness, provenance |
| 4B | ingest/storage/runtime, lexical tests | prompts, compiler, UI | FTS5 modes and safe query handling |
| 4C | embeddings/runtime/config, vector tests | prompts, compiler, UI | independent queries, thresholds, caps, identity |
| 5 | runtime, ADR, fusion tests | prompts, compiler, UI | deterministic fusion and bounded packet |
| 6A | ingest/storage/runtime, temporal tests | prompts, UI | date precedence and ordering |
| 7 | inventory/ingest/runtime, inventory tests | prompts, UI | persisted inventory reused without full scans |
| 8 | compiler prompt/schema only | frontier prompt unless proven incompatible | hashes, line diff, golden output retention |
| 9 | docs/tests/benchmarks/guards | aspirational capability claims | final UAT and risk ledger |
