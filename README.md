# Semantic Traversal

Semantic Traversal is a provenance-preserving semantic retrieval substrate and
runtime foundation for an authored Markdown vault.

The project treats a vault as an authored, typed, relational corpus rather
than as an undifferentiated directory of text. The implementation currently
has two connected parts:

1. a deterministic build pipeline that turns a vault into a canonical
   semantic substrate and derivative retrieval surfaces; and
2. a small runtime control seam that persists conversation state and asks a
   model whether the current turn requires semantic retrieval.

The project is being reconstructed around an authority boundary:

> authored vault data and explicit build/runtime configuration determine what
> the system represents and what it is allowed to execute.

Indexes, embeddings, model outputs, and diagnostic observations are derivative
artifacts. They do not become a replacement ontology for the vault.

## Current implementation

The `import` branch contains an executable substrate build and the first
runtime seam. It is not yet the complete conversational traversal system.

Implemented now:

- deterministic whole-vault Markdown discovery;
- exact path-prefix folder exclusion from build configuration;
- YAML frontmatter parsing with native scalar types preserved;
- required UUID validation and duplicate-UUID detection;
- authored Markdown parsing into semantic objects, regions, and units;
- inherited object context on units, including admitted identifiers and path
  hierarchy;
- authored wikilink extraction and deterministic object/region resolution;
- canonical SQLite substrate materialization;
- exact fielded lookup with typed values;
- fielded lexical lookup using SQLite FTS5 and BM25 ranking;
- graph discovery, relation lookup, and directional traversal;
- vector indexing and similarity lookup through the configured Ollama
  embedding provider;
- integrity checks for foreign keys, lexical dimensions, graph topology,
  hydration, and vector alignment;
- capability-fact observation and model-facing catalog generation;
- durable append-only runtime conversations and model-run evidence;
- a strict router seam with `direct` and `semantic_retrieval` as its only
  accepted routes;
- explicit runtime configuration for the router provider, model, and timeout.

Not implemented in this branch:

- the full problem-space control plane;
- projection activation and semantic-access planning;
- a temporal retrieval surface;
- retrieval-plan execution and multi-surface fusion;
- retrieval packets and frontier synthesis;
- a user-facing conversational application;
- automatic context sizing, hidden fallbacks, tokenizer preflight, or
  automatic prompt truncation.

Those are architectural destinations, not claims about the current executable
surface.

## Build pipeline

The build command executes the following stages in order:

```text
build configuration
        ↓
vault discovery and parse
        ↓
context materialization
        ↓
authored relation resolution
        ↓
canonicalization
        ↓
SQLite substrate
        ↓
exact index · lexical index · graph
        ↓
Ollama vector index
        ↓
integrity verification
```

Each stage preserves the distinction between authored state and derivative
representation. A failed parse, unresolved authored relation, missing UUID, or
duplicate UUID prevents the candidate corpus from becoming a completed build.
The build also refuses to write into an existing non-empty output directory.

The resulting build directory contains:

- `substrate.sqlite3` — canonical semantic state and derivative SQLite
  retrieval structures;
- `vectors.npy` — the vector matrix aligned with persisted vector-segment
  records.

Example:

```bash
python -m pip install -e .

semantic-traversal build \
  --vault /path/to/vault \
  --config docs/build_config.yaml \
  --output build \
  --ollama-url http://127.0.0.1:11434
```

The checked-in build configuration is an example of the contract, not a
portable vault location. The vault root is supplied explicitly at invocation;
canonical paths are stored relative to that root.

## Canonical semantic model

The build preserves three related levels of authored structure:

```text
semantic object
  └── semantic region(s)
        └── semantic unit(s)
```

### Semantic objects

A semantic object represents an authored Markdown note. Its identity is the
required authored UUID. The object retains its source-relative path, path
hierarchy, admitted frontmatter state, authored frontmatter link occurrences,
and regions. An object remains canonical even when it contains no semantic
units.

### Semantic regions

Headings establish structural regions. Region addresses are represented by
their complete ordered heading path, not by parser-local or database-local
identifiers.

### Semantic units

Blank-line-separated authored Markdown blocks are materialized as semantic
units. A unit retains:

- canonical `unit_id`;
- owning object UUID;
- source-relative path and path hierarchy;
- complete region path;
- raw authored Markdown;
- parsed linguistic text;
- admitted inherited identifier values and their native types;
- typed authored relation occurrences;
- authored embeds where parsed.

Retrieval surfaces point back to these canonical targets. A hit is not itself a
new semantic object. Hydration reconstructs the complete canonical object or
unit from the substrate and preserves provenance.

Transport segmentation for an embedding input does not split or replace a
canonical unit. It creates derivative vector records that retain the same
target identity.

## Retrieval surfaces

The current build exposes four executable surface families.

| Surface | Current role | Canonical result identity |
| --- | --- | --- |
| Exact | Typed field-value equality | semantic-unit IDs |
| Lexical | Fielded term and phrase retrieval with BM25 | semantic-unit IDs |
| Graph | Authored and structural discovery/traversal | typed graph handles and canonical targets |
| Vector | Similarity over target-specific embedding inputs | semantic-unit or semantic-object identity |

Exact and lexical dimensions remain fielded. Intrinsic text, admitted semantic
identifiers, region paths, and semantic paths are not flattened into one
anonymous search field. Exact lookup preserves scalar type distinctions such
as integer versus string and date versus string.

Graph relations preserve their authored or structural reason. A body wikilink
is represented as `linked_to`; a frontmatter-derived relation retains its
admitted field name. Tags are authored grouping data, not graph edges merely
because they are searchable.

Vector input is deliberately target-specific:

- a unit embeds its exact parsed text;
- an object with units does not also receive an object-name fallback vector;
- an object with no units embeds its canonical authored object name.

The vector provider is responsible for establishing input fit. The build does
not silently truncate vector input or substitute an approximate tokenizer as
the authority for provider capacity.

## Runtime foundation

The runtime seam currently persists conversation state in SQLite schema v2.
Conversations contain only `user` and `synthesis` messages. Model-run records
retain the router's provider, model, prompt version, status, response ID,
usage, and failure classification without appending the router's classification
as a conversational message.

For each routing attempt, the router receives the exact persisted conversation
thread and returns one structured result:

```json
{"route":"direct"}
```

or:

```json
{"route":"semantic_retrieval"}
```

The router does not answer the user and does not choose retrieval operations.
It is a bounded control decision. The actual retrieval and synthesis stages
remain future runtime seams.

Runtime configuration is explicit and secret-free:

```yaml
router:
  provider: openai
  model: gpt-5.6-luna
  timeout_seconds: 30
```

Credentials are read from `OPENAI_API_KEY`; they are not stored in YAML or
runtime records. The configuration loader rejects unknown sections, unknown
router keys, invalid providers, empty model identifiers, and non-positive or
non-finite timeouts.

Initialize and use the runtime database with:

```bash
semantic-traversal runtime init --database runtime.sqlite3
semantic-traversal runtime conversation create --database runtime.sqlite3
semantic-traversal runtime message append \
  --database runtime.sqlite3 \
  --conversation-id CONVERSATION_ID \
  --role user \
  --content "What did I write about this?"
semantic-traversal runtime router infer \
  --database runtime.sqlite3 \
  --config docs/runtime_config.yaml \
  --conversation-id CONVERSATION_ID
```

The runtime does not dynamically size context, detect truncation after the
fact, or invent a missing configuration. Context behavior is controlled by the
explicit provider request and the configured model/provider boundary.

## Inspection and direct surface access

The CLI exposes bounded inspection and surface operations without making the
SQLite schema itself the public semantic contract:

```bash
semantic-traversal inspect artifacts --build build
semantic-traversal inspect counts --build build
semantic-traversal inspect verify --build build
semantic-traversal inspect capability-facts --build build --json
semantic-traversal inspect object --build build --uuid OBJECT_UUID
semantic-traversal inspect unit --build build --unit-id UNIT_ID

semantic-traversal exact --build build \
  --field-class semantic_identifier \
  --field-name note_type \
  --value-type string \
  --value journal

semantic-traversal lexical terms --build build \
  --field-class intrinsic \
  --field-name parsed_text \
  --term coherence

semantic-traversal graph relations --build build \
  --relation-class body_wikilink \
  --relation-name linked_to

semantic-traversal vector --build build --query "semantic coherence"
```

Use `--json` where supported for machine-readable output.

## Authority and design boundaries

The implementation is intentionally strict about what may establish meaning:

- authored vault values establish the represented content;
- build configuration establishes admitted semantic identifiers and their
  authored descriptions;
- runtime configuration establishes executable router policy;
- canonical substrate records establish retrievable semantic state;
- retrieval indexes and embeddings provide derivative access paths;
- model output is validated at its boundary and cannot silently expand the
  route grammar;
- diagnostic capability facts describe what a completed build contains but do
  not create capabilities.

The project therefore keeps several facts separate:

1. a surface family may be structurally represented by the projection;
2. a particular target may or may not be applicable to that surface;
3. a completed build may or may not contain relevant records;
4. a provider or index may or may not be executable in the current environment;
5. a later runtime may or may not invoke the surface.

These are not interchangeable claims.

## Development

Run the test suite with:

```bash
python -m unittest discover -s tests -v
```

The tests cover parser and vault validation, canonicalization and hydration,
exact/lexical/graph/vector projections, capability facts, CLI boundaries,
runtime schema migration, conversation persistence, provider request shape,
router validation, and durable failure handling.

The repository uses Python 3.11.9 or newer. Runtime dependencies are declared
in `pyproject.toml`.

## Project documents

The checked-in `docs/` directory contains the implementation-facing build and
runtime specifications. The broader design vocabulary is documented separately
in the clean-room materials that define the semantic object/unit model,
projection requirements, activation boundaries, semantic-access language, and
runtime invariants.

Those documents describe the authority model and the intended sequence of
future seams. This README is narrower: it reports the executable shape that
exists in this repository at the current implementation boundary.

## Status

This is an active reconstruction project. The canonical substrate and the
first runtime control seam are executable and tested. The full traversal loop
from a user turn through planning, multi-surface execution, evidence packet
assembly, and frontier synthesis remains to be implemented and verified.
