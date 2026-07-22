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
| Compiler-declared evidence requirements | Semantic compiler declaration checked against YAML operator mapping | `semantic_compiler.py`, `retrieval_resolver.py`, `runtime.py` | Completeness and bounded-repair tests |
| Compiler-facing scope authority | YAML-configured scope aliases only; observed inventory is descriptive | `retrieval_resolver.py`, `resource_inventory.py` | Unauthorized observed-value scope regressions |

## Conversation-state execution boundary (2026-07-21)

Persisted conversation state is descriptive interpretive context. It may be
supplied to the compiler and bounded synthesis context, and the deterministic
fallback may inspect compact prior concepts or resolved referents only for an
explicit referential/comparison input. It has no direct retrieval authority.
Only fields serialized into the current planner retrieval plan supply executor
inputs. Active focus, prior selected evidence, prior queries, prior graph
seeds, selected titles/sections, and prior raw user input are not graph seeds
or other executor coordinates. Compiler semantic targeting remains an
unresolved control-plane issue outside this narrow boundary repair.

## Forward-only clean-cutover invariant

Implementation 07 uses forward-only clean-cutover development. Superseded
authority branches are removed rather than retained for backward compatibility;
all affected producers, binders, consumers, fallbacks, and tests are reconciled
in the same seam. Historical artifacts may remain readable, but they do not
preserve obsolete execution semantics. Rollback is Git-based or performed with
a duplicated codebase; compatibility is opt-in and requires explicit approval.

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
