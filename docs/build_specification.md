## 1. Build configuration
### 1.1 Semantic-identifier admission list
The build configuration contains a mutable list of frontmatter fields that are admitted as semantic identifiers on ingest. 
[[Build Config Seed]] 
### 1.2 UUID is identity, not a semantic identifier
`uuid` identifies the semantic object. It is not a semantic identifier merely because it appears in frontmatter. Duplicate or missing `uuid` on ingest is hard fail.
### 1.3 Build and runtime configuration are separate concerns
The semantic-identifier admission list belongs to build configuration because changing the YAML schema changes ingest behavior. A runtime configuration is expected separately.
## 2. Read and parse one Markdown note
### 2.1 One Markdown note is one semantic object
Each `.md` note is one semantic object. If the Markdown note is renamed/moved while retaining its UUID, it must remain the same semantic object.
### 2.2 Preserve the authored path and its ordered hierarchy
Path represents authored scope hierarchy. Path components express the scope within which the author located a semantic object. Preserve both the complete authored path and its ordered hierarchy. Do not flatten authored scope topology into frontmatter identifiers. If the Markdown note is renamed/moved while retaining its UUID, it must remain the same semantic object while its meaningful path context changes.
### 2.3 Preserve three states for admitted frontmatter fields
For an admitted field, absence, authored blank, and authored value are different semantic states. `PresentBlank` is the semantic state. A programming language may internally use `null` or `None`, but that spelling is not the authored meaning.
### 2.4 Excluded folders
Each excluded_folders entry is an exact vault-relative directory path; that directory and its descendants are excluded, while directories with the same leaf name at other vault-relative paths remain included.
## 3. Build regions
### 3.1 Headings create nested semantic regions
Markdown headings from `#` through `######` form nested semantic regions. A region is a path-addressable semantic scope boundary that organizes semantic units. Addressing a region provides access to the units inhabiting that scope; the region itself is not a semantic unit. Region resolution first attempts exact canonical heading-address equality. If no exact match exists, compare the authored region fragment and canonical heading-address text using a normalized address key that applies Unicode case-folding, treats runs of non-letter/non-number characters as separators, collapses separator runs to one space, and trims leading/trailing space. Canonical authored heading text and heading-address text remain unchanged. Normalization is comparison-only and does not authorize substring, prefix, fuzzy, stemming, synonym, or semantic matching.
### 3.2 Units inherit the complete region path
A unit under nested headings inherits the complete region path, not only its immediate heading—store or resolve the ordered chain of scoped regions from the highest level heading to the most immediate region.
## 4. Build semantic units and chunk boundaries
### 4.1 Markdown-formatted semantic units become chunks
Authored content units under regions become semantic units/chunks. Blank separator lines do not become chunks. Regions do not become chunks. Markdown structure determines semantic-unit boundaries; paragraphs, tables, lists, code blocks, and other supported Markdown block forms may each materialize as semantic units/chunks.
### 4.2 Preserve raw Markdown and parsed text separately
Each unit preserves both its authored Markdown and parsed linguistic text.
### 4.3 Every unit remains traceable to its source object
Every semantic unit must remain connected to the object it came from. Hydration during runtime will surface provenance for synthesis. Each semantic unit receives a `unit_id` within the completed ingest. `unit_id` is the canonical retrieval identity for semantic-unit targets. Each unit separately retains `source_object_uuid` for object-level provenance. Retrieval surfaces may also identify canonical semantic objects or semantic regions where explicitly defined by that surface. Retrieval identities remain typed and resolve to canonical substrate state; internal database row IDs are not semantic retrieval identities. Unit IDs are not required to persist across ingests.
### 4.4 Vector-input overflow
Vector segmentation is a derivative embedding operation. It does not split or replace the canonical semantic object or semantic unit represented by the vector target.
- vector input is never silently truncated;
- if the complete authorized vector input fits the pinned embedding model's input capacity, it produces exactly one vector segment;
- fit is determined by the actual pinned embedding provider with provider-side truncation disabled, not by a character-count, approximate-token heuristic, or independently substituted tokenizer;
- if the input does not fit, segmentation greedily takes the largest fitting prefix;
- segmentation prefers, in order:
  1. the latest newline boundary that fits;
  2. the latest whitespace boundary that fits;
  3. the latest Unicode code-point boundary whose prefix is confirmed to fit by the pinned embedding provider when no earlier authored textual boundary can produce a fitting segment;
- segments do not overlap;
- segmentation does not normalize, rewrite, summarize, or otherwise alter the represented text;
- concatenating the segment texts in segment-ordinal order must reproduce the exact authorized vector-input string;
- all segments retain the same `target_kind` and `target_identity`;
- hydration always returns the complete canonical target, never merely the winning vector segment.
Input fit is established by the actual pinned embedding provider with provider-side truncation disabled. A local tokenizer approximation is not authoritative for determining whether input fits.
### 4.5 Semantic Model Sample
path: `C:\Users\madis\Desktop\kháos\LAYER-1 PILLARS\PILLAR 2-DYNAMIC COHERENCE\JOURNAL\2026\2026-04\07_Tuesday.md`
- Lines 1-26: YAML **frontmatter** with 3 **wikilink** connections—I'd expect the semantics of those **wikilink** connections to be carried into the **units** along with the rest of the **frontmatter identifiers**
- Line 27: "Dream Recall" semantic **region**
	- Line 28: semantic **unit** with 2 **wikilink** connections—I'd expect the semantics of those **wikilink** connections to be carried into the **unit** itself
- Line 29: blank
- Line 30: "Yesterday Review" semantic **region**
	- Line 31: semantic **unit** with 2 **wikilink** connections—I'd expect the semantics of those **wikilink** connections to be carried into the **unit** itself
- Line 32: blank
- Line 33: "Daily Intent" semantic **region**
	- Line 34: semantic **unit*** with 1 **wikilink**—I'd expect the semantics of those **wikilink** connections to be carried into the **unit** itself
- Line 35: blank
- Line 36: "Freeform Journaling" semantic **region**
	- Line 37: semantic **unit*** with 2 **wikilink** connections—I'd expect the semantics of those **wikilink** connections to be carried into the **unit** itself
	- Line 38: blank
	- Line 39: semantic **unit*** with 2 **wikilink** connections—I'd expect the semantics of those **wikilink** connections to be carried into the **unit** itself
	- Line 40: blank
	- Line 41: semantic **unit*** with 4 **wikilink** connections—I'd expect the semantics of those **wikilink** connections to be carried into the **unit** itself
	- Line 42: blank

- x1 semantic **object**
- x4 semantic **regions**
- x6 semantic **units** = x6 **chunks**
	- all **units** inherit the region they inhabit, the **object frontmatter**, and the **substrate/hyperspace topology** through **path** 
	- semantic **regions** are not **chunks**, semantic **units** are
## 5. Inherit object context into every unit
### 5.1 Inherit the path and admitted semantic identifiers
Every unit inherits its object's meaningful path and all frontmatter fields admitted by the current build configuration as semantic identifiers. This makes the unit traceable and lets the object's semantic context participate in retrieval through appropriate surfaces.
## 6. Parse and resolve wikilinks
### 6.1 A wikilink is structured topology, not only text
The Markdown brackets are authored syntax. The parser must preserve the authored form, visible text, and addressed target as distinct information. Each output must retain the correct target address and visible text without losing the raw Markdown.
### 6.2 Frontmatter wikilinks create typed inherited relations
When an admitted frontmatter field contains a wikilink, every unit in that object inherits a relation to the target. The relation name is the frontmatter field name.
### 6.3 Body wikilinks create `linked_to`
A body wikilink has no additional authored relation type. It creates a generic `linked_to` relation. Build runtime will not infer anything.
### 6.4 Preserve distinct reasons for the same connection
A frontmatter-derived relation and a body-derived relation remain distinct facts even when they share the same source unit and target object—differentiated by either inheriting the relation from frontmatter, or being `linked_to` if from body.
### 6.5 Authored wikilinks outside of escape characters must resolve
Every authored wikilink—not prefaced with an escape character (\) or inside of `codeblocks`—must resolve to the semantic target it addresses. An object link requires the object. An object-plus-region link requires both the object and region. Ingest hard fails otherwise—with vault-wide manifest of what target object is missing and where the source for the link is. Object-name and alias address comparison is case-insensitive using Unicode case-folding. Canonical authored names, aliases, paths, and link text remain unchanged. Case normalization is comparison-only and does not create fuzzy, substring, lexical, or semantic matching.
```text
WIKILINK RESOLUTION

1. A resolved relation stores the canonical target_object_uuid,
   not merely the authored filename.

2. If the wikilink contains a vault-relative path:
   resolve that exact path → target object → target UUID.

3. If the wikilink contains only a note name or alias:
   resolve against matching objects across the vault.

   exactly 1 match → resolve to that object's UUID
   0 matches       → ingest hard fail: unresolved
   >1 matches      → ingest hard fail: ambiguous, list candidates

4. [[Object#Region]]
   additionally resolves the region inside the resolved target object.

5. Once resolved, later retrieval uses the UUID, so two objects named
   02_Tuesday in different folders remain completely distinct.
```
## 7. Canonical semantic-unit record
### 7.1 The canonical unit owns its semantic-unit state
A semantic unit is canonical semantic state, not a retrieval-index record. Retrieval surfaces own derivative representations of, and pointers back to, canonical semantic targets. Retrieval indexes are not additional authoritative copies of canonical semantic state. Store rich semantic-unit context on the canonical unit. Store only the fields each retrieval surface requires to recover its canonical target identity unless a later explicit decision requires duplication. A canonical unit also retains structured authored embeds already parsed from that unit; persistence must preserve them without resolving or interpreting their targets unless a later explicit rule authorizes that behavior. Runtime hydration by `unit_id` will return the canonical record and its full differing semantic context without relying on duplicated index metadata. A canonical unit must be able to retain:
```text
unit_id
source_object_uuid
CanonicalUnit
source path
complete semantic path hierarchy
complete region path
raw Markdown
parsed text
admitted inherited identifier states
typed inherited relations
body linked_to relations
```
### 7.2 Retrieval hits identify canonical targets and hydrate
A retrieval hit identifies the canonical target represented by that surface. A semantic-unit hit identifies `unit_id`. A semantic-object hit identifies `source_object_uuid`. A semantic-region hit identifies its owning object UUID plus complete canonical region identity. Evidence hydration reconstructs the complete canonical target from the canonical substrate. When the target is a semantic unit, evidence hydration also hydrates its owning canonical semantic object for object-level provenance. When the target is a semantic region, evidence hydration also hydrates its owning canonical semantic object. Hydration does not automatically retrieve sibling units, surrounding units, all units in a containing region, graph neighbors, or other adjacent canonical targets. Such contextual expansion is a separate runtime/control-plane operation. A `scope` graph handle is structural execution state and does not automatically hydrate semantic objects or semantic units. It may participate in explicit graph traversal to a hydratable canonical target. That traversal is not hydration and does not occur implicitly.
### 7.3 Canonical objects preserve object-level authored state
A canonical semantic object retains the object-level authored state required to represent the object independently of its semantic units. 
At minimum preserve:
- source_object_uuid;
- authored source path and semantic path hierarchy;
- admitted frontmatter identifier states;
- authored frontmatter wikilink relation occurrences;
- canonical regions belonging to the object.
Unit inheritance does not replace object-level provenance. An object remains canonically represented even when it contains zero semantic units. Object-level frontmatter state must not be reconstructed later by deduplicating inherited copies from semantic units.
Admitted `tags`, when present, remain available as object-level authored grouping state for graph discovery without becoming graph relations.
## 8. Exact and lexical representations
### 8.1 Searchable fields remain separate
Structured semantic fields must not be flattened into one large string for search. Fields remain separately addressable:
```text
raw_markdown
parsed_text
every admitted semantic identifier
complete region path
semantic path hierarchy/components
```
### 8.2 Exact-index record shape
Exact retrieval is a fielded inverted lookup: each enabled exact-search field maps exact field values to the `unit_id`s carrying that value. The exact index is derivative of canonical units and returns `unit_id`s for hydration; it is not another semantic source of truth. `(field_class, field_name, normalized_value) -> unit_ids`.
#### 8.2.1 Exact fields are namespaced by semantic field class
Exact-searchable fields are identified by both semantic field class and field name.
Conceptually:
  (field_class, field_name, normalized_value) -> unit_ids
Field classes keep intrinsically different semantic dimensions separately addressable even when they have the same textual field name.
At minimum distinguish:
  intrinsic
      raw_markdown
      parsed_text

  semantic_identifier
      <admitted field name>

  region
      region_path

  semantic_path
      path_hierarchy
      path_component
An admitted semantic identifier may have the same textual name as an intrinsic or structural exact field without collision.
Example:
  intrinsic / parsed_text
and:
  semantic_identifier / parsed_text
are distinct exact-search dimensions.
Do not reserve authored semantic-identifier names merely because an intrinsic, region, semantic-path, database, or implementation field uses the same text.
Database table names, surrogate keys, parser-local identifiers, and other implementation details do not create semantic field classes.
### 8.3 Exact matching normalization
- Preserve the authored field value exactly as written.
- Compare text-like values case-insensitively.
- Ignore insignificant whitespace differences during comparison:
    - trim leading/trailing whitespace;
    - collapse runs of whitespace to a single space.
- Exact matching is still **field-value equality**, not substring matching.
- Normalization is used only for lookup/comparison; it does not rewrite the canonical unit.
#### 8.3.1 Exact comparison preserves canonical value type
Exact retrieval preserves canonical value domains.
For text-like values, exact comparison uses the normalization defined in §8.3:
- Unicode case-folding;
- trim leading/trailing whitespace;
- collapse runs of whitespace to one space.
For non-text scalar values, exact comparison preserves canonical type and value.
Examples:
    integer 7        ≠ string "7"
    boolean true     ≠ integer 1
    date 2026-04-07  ≠ string "2026-04-07"
#### 8.3.2 Sequence-valued semantic identifiers emit independently searchable members
When an admitted semantic identifier has an authored sequence value, each authored member is independently exact-searchable under that field. The canonical unit retains the complete authored sequence unchanged.
Example:
  unity_level:
    - model
    - meta
may produce derivative exact entries equivalent to:
  (unity_level, model) → unit_id
  (unity_level, meta)  → unit_id
This does not replace, flatten, reorder, or rewrite the canonical inherited identifier value.
#### 8.3.3 Exact retrieval returns unique canonical units
A single exact lookup returns each matching `unit_id` at most once. If multiple derivative exact-index entries for the queried field/value refer to the same canonical unit, the lookup result contains that `unit_id` once. Deduplication at this retrieval boundary does not alter canonical authored occurrence multiplicity or canonical relation multiplicity. Exact lookup results are returned in ascending `unit_id` order. Sequence expansion belongs only to derivative retrieval representation. Normalization is comparison-only. Canonical authored values and parsed value types are not rewritten by the exact index.
#### 8.3.4 Exact region and semantic-path values use authored semantic representation
Exact retrieval must not expose parser-local or database-local region identifiers as semantic search values. For semantic regions, the exact-searchable value is the complete ordered canonical region path expressed through canonical heading-address values. For authored semantic path topology:
- the complete ordered vault-relative path hierarchy is exact-searchable as one
  structured value; and
- each authored path component is also independently exact-searchable as a path
  component value.
Internal region IDs, SQLite row keys, and other implementation identifiers are not semantic exact-search values.
### 8.4 Lexical content is searchable
Parsed unit content is lexically searchable. Lexical availability applies to text-bearing represented values. Non-text canonical values are not stringified to manufacture lexical capability. Lexical retrieval performs word/phrase matching over their authored text; it does not embed them or infer semantic similarity. Date-valued admitted semantic identifiers are exact-searchable by value. Aliases are alternative authored names resolving to the same canonical target. Aliases are exact- and lexical-searchable without replacing the canonical name. Tags, when admitted by build config, are multi-valued semantic identifiers. Each tag value is separately exact- and lexical-searchable. Tags express authored semantic grouping; they do not create graph relations unless a separate explicit rule later says they do.
### 8.5 lexical-index technology and tokenization
Lexical retrieval uses a fielded inverted full-text index with Unicode-aware tokenization, case-insensitive terms, BM25 ranking, and phrase-query support. Common words are retained and naturally downweighted by BM25. No custom stopword list, stemming, synonym expansion, or semantic query expansion is used. Parsed unit text and admitted semantic-identifier fields remain separate lexical fields. 
#### 8.5.1 `terms` and `phrase` operands
Each operand supplied to `terms` must correspond to exactly one lexical token under the configured lexical tokenizer. An operand that produces zero tokens or more than one token is invalid for the `terms` operator. Multi-token contiguous matching belongs to the separate `phrase` operator. Term validation must use behavior equivalent to the configured lexical tokenizer; it must not substitute whitespace splitting or an independently invented token grammar.
#### 8.5.2 BM25 ranking 
Lexical lookup is computed within the queried lexical field dimension. Unrelated lexical fields must not affect its document-frequency or document-length statistics.
## 9. Vector representation
### 9.1 Vector records derive from semantic objects, then units
for each canonical object:
  if object has >= 1 semantic unit:
    object contributes NO object-level fallback vector
    for each unit:
      if unit parsed_text is not empty or whitespace-only:
        vectorize the exact parsed_text
      else:
        that unit contributes no vector
  else:
    vectorize the canonical authored object name
    as semantic_object
```text
VectorRecord {
    vector_id
    target_kind        # semantic_unit | semantic_object
    target_identity    # unit_id | source_object_uuid
    segment_ordinal
    vector
}
```
A vector hit identifies the canonical object or semantic unit represented by the winning vector record. A semantic-object hit hydrates that canonical object. A semantic-unit hit hydrates the complete canonical unit and identifies its owning canonical object for object-level provenance. Hydration does not automatically retrieve sibling units, surrounding units, all units in the containing region, or graph neighbors. Such contextual expansion is a separate later runtime/control-plane operation.
A semantic object containing zero semantic units produces one vector representation derived only from its canonical authored object name. If the object contains one or more semantic units, no object-name fallback vector is produced; vector representation instead derives from eligible unit parsed_text.
For zero-unit object vector fallback, the canonical authored object name is the authored source-path basename with its final `.md` suffix removed, using the same canonical object-name representation already used for object addressing and resolution. Aliases, tags, titles, semantic identifiers, path components, and relation metadata are not appended or substituted. A zero-unit semantic object produces one vector target representation. That representation may require one or more vector segment records under §4.4. Segmentation does not create additional canonical targets.
### 9.2 Vector input is target-kind-specific and minimal
For a `semantic_unit` vector target, embedding input is exactly that unit's parsed intrinsic text and nothing else.
Semantic-unit embedding input excludes:
- object UUID;
- unit ID;
- raw path;
- semantic path components;
- region heading text;
- frontmatter field names and values;
- inherited relations;
- body-link target metadata; and
- raw Markdown syntax.
Visible wikilink display text remains present where it is already part of the canonical parsed linguistic text. For a zero-unit `semantic_object` vector target, embedding input is exactly the canonical authored object name defined in §9.1 and nothing else.
Semantic-object fallback input does not append or substitute:
- source-object UUID;
- the complete authored source path;
- semantic path hierarchy/components;
- aliases;
- tags;
- title or canonical-name semantic identifiers;
- any other frontmatter value;
- canonical regions;
- relation metadata; or
- instructions or embedding prefixes.
### 9.3 The vector index is not a semantic source of truth
Canonical semantic metadata belongs to the canonical semantic object or semantic unit represented by the vector record, not to duplicated vector metadata. A vector record retains only the target identity and derivative embedding provenance required to recover its canonical target. Hydration follows the record's `target_kind` and `target_identity`.
### 9.4 Build-time and query-time embeddings must be compatible
The completed vector build records the exact embedding contract that produced its stored vectors.
At minimum preserve:
- requested model name/tag;
- resolved immutable model identity/digest;
- embedding dimensions;
- vector dtype;
- normalization rule;
- similarity metric; and
- the embedding-provider identity and input-capacity contract used for segmentation.
The vector projection persists this compatibility record with the built vector state so query-time compatibility validation does not depend on reconstructing build-time assumptions. Conversation-time query embedding must use the compatible completed-build embedding contract. A missing or incompatible encoder identity or vector contract fails explicitly. Matching dimensionality alone does not establish compatibility. The completed build determines the encoder contract first. Query runtime conforms to that completed build; query runtime does not substitute whatever embedding model happens to be available later.
### 9.5 Vector query input
Vector retrieval accepts one query-text operand. The query embedding is derived from exactly the supplied query text. The vector surface does not append conversation context, semantic identifiers, paths, regions, relation names, instructions, prefixes, or other semantic expansion. Any probabilistic reformulation of the user's intent into vector-query text belongs upstream to Model 1. A query that exceeds the compatible embedding model's input capacity fails explicitly. Query text is not silently truncated, segmented, pooled, averaged, or expanded into multiple query vectors. An empty or whitespace-only vector query is invalid.
### 9.6 Vector-result semantics
Vector retrieval performs exhaustive cosine comparison against the complete built vector matrix. 
Conceptually:
VectorHit {
    target_kind
    target_identity
    score
    segment_ordinal
}
When one canonical target has multiple vector segments, retrieval returns that canonical target once using the maximum cosine similarity produced by any of its segments. The winning segment's ordinal is retained as derivative match provenance. If multiple segments of the same target have exactly the same winning score, the lowest segment ordinal wins. Segment multiplicity does not increase, sum, or average a target's score. Results are ordered by cosine similarity descending. Equal-score target ties use deterministic canonical-target ordering and do not introduce an additional semantic relevance signal. 
The constitutive vector surface applies:
- no similarity threshold;
- no top-k result limit; and
- no other candidate-admission cutoff.
Runtime policy may later bound how many ranked results are consumed.
### 9.7 Vector-build completeness
Every vector-eligible canonical target must be represented successfully in a completed vector build. An embedding failure, incompatible embedding output, invalid vector dimension, non-finite vector value, normalization failure, missing target mapping, or missing required segment invalidates the vector build. The vector surface is not published or treated as complete when only a subset of vector-eligible targets was embedded successfully.
## 10. Graph representation
### 10.1 Graph edges come only from authored structure in this scope
Canonical object-level frontmatter wikilink occurrences project as field-named graph relations except `tags`, which remains canonical authored state and a graph-discovery descriptor but does not create graph topology. Body wikilinks produce `linked_to`. Prose does not generate inferred relation labels. Path-derived scope topology remains semantically meaningful regardless of future graph representation choices—implementation must accommodate this and not silently subsume decision.
### 10.2 Authored containment and scope topology is projected onto the graph
The graph represents direct authored structural containment in addition to wikilink-derived relations. Graph structural node kinds are: `scope` (an authored vault-relative path scope), `semantic_object`, `semantic_region`, `semantic_unit`. Direct structural relations are: `contains_scope`, `contains_object`, `contains_region`, `contains_unit`. Only direct authored containment edges are materialized. Transitive containment is derived by graph traversal and is not stored as additional semantic facts. Traversal may follow an edge outbound or inbound; inverse copies of the same structural relation are not separately materialized. Scope-node identity is determined by its complete vault-relative scope path, not by the component name alone. Region-node identity is contextual to its semantic object and complete region hierarchy, not by heading text alone. Structural graph edges are derivative representations of the authored path, object, region, and unit topology. They do not create additional semantic authority.
### 10.3 Conceptual rules
1. Graph discovery and graph traversal are distinct operations. Discovery locates represented corpus instances; traversal follows represented edges involving an already discovered instance.
2. Semantic objects and semantic regions are lexically discoverable through authored canonical address text. Discovery returns opaque canonical graph handles; internal UUIDs/region identities are not exposed as a corpus inventory to Model 1.
3. Relation types are not semantically inferred by deterministic discovery. Model 1 selects a relation class/name from the capability catalog; deterministic graph execution locates or traverses instances of that represented relation.
4. Graph discovery over semantic objects may inspect authored object-address text and admitted `tags`. Tags participate only as authored discovery/grouping descriptors. They do not create graph nodes or graph edges in this scope. Other admitted semantic identifiers are not automatically available to graph node discovery merely because they exist canonically or are searchable through another retrieval surface. Graph discovery and traversal may use canonical object, region, or relation handles as intermediate execution state. Graph results retain typed canonical graph handles. Evidence hydration follows §7.2 according to the resulting canonical target kind; graph execution does not automatically descend to units or perform contextual expansion.
## 11. Corpus semantic capability catalog
### 11.1 Capability catalog contents
The specification defines catalog classes and generation rules. The built capability catalog describes which semantic field classes, fields, node classes, relation classes, relation names, retrieval surfaces, value shapes, and operators are available in the completed build. The catalog describes availability and legal retrieval grammar, not corpus-instance inventory. It does not enumerate concrete semantic objects, semantic regions, semantic units, paths, region values, identifier values, or internal identities merely because those instances exist in the corpus. Concrete corpus instances are discovered by executing retrieval operations against the completed build. The capability catalog does not contain occurrence counts. **Availability not cardinality.**
### 11.2 Capability catalog representation
The capability catalog uses a hybrid structured representation:
1. a field/capability matrix describing semantic dimensions, represented value domains/shapes, retrieval surfaces, and supported operators;
2. a relation grammar describing available relation types and their allowed source and target kinds.
#### Fields

| field class                  | values                                                                        | exact | lexical | vector |
| ---------------------------- | ----------------------------------------------------------------------------- | ----- | ------- | ------ |
| `parsed_text`                | semantic unit free text                                               | yes   | yes     | yes    |
| admitted semantic identifier | represented canonical value shapes                       | yes*   | yes**     | no     |
| semantic region              | canonical region address text/path                             | yes   | yes     | no     |
| semantic path component      | authored vault-relative scope/path text                      | yes   | yes     | no     |
| `raw_markdown`               | authored unit Markdown                                            | yes   | no      | no     |
| semantic object authored name | canonical authored object-name text for zero-unit objects | no | no | yes |

`*` exact: supported typed scalar / sequence-member semantics

`**` lexical: text-bearing values only
#### Relations

| relation class | relation name | source | target |
|---|---|---|---|
| `semantic_identifier` | represented admitted frontmatter wikilink field name, except `tags` | `semantic_object` | resolved `semantic_object` / `semantic_region` as permitted by the authored wikilink |
| `body_wikilink` | `linked_to` | `semantic_unit` | resolved `semantic_object` / `semantic_region` |
| `structural` | `contains_scope` | `scope` | `scope` |
| `structural` | `contains_object` | `scope` | `semantic_object` |
| `structural` | `contains_region` | `semantic_object` / `semantic_region` | `semantic_region` |
| `structural` | `contains_unit` | `semantic_object` / `semantic_region` | `semantic_unit` |

#### Operations

| surface | operations          |
| ------- | ------------------- |
| exact   | equals              |
| lexical | terms, phrase       |
| vector  | semantic similarity |
| graph | node discovery, relation occurrence lookup, inbound traversal, outbound traversal |

**SPEC:** defines the rule that generates relation types
**BUILD CONFIG:** defines which semantic-identifier fields are admitted
**CORPUS:** supplies the actual values and wikilink targets
**BUILT CAPABILITY CATALOG:** contains the concrete available field classes and field names, represented value domains/shapes, node classes, relation classes and relation names, retrieval surfaces, and operators; it does not enumerate corpus-instance values merely because those values exist.
### 11.3 Control-plane boundary
The retrieval control plane is the deterministic execution boundary between a model-selected retrieval request and the retrieval surfaces exposed by a completed build. The capability catalog declares which retrieval field classes, fields, relations, operators, and surface capabilities are available in that build. Model 1 may select only operations exposed by the capability catalog.
The control plane must:
- validate a requested retrieval operation against the completed build's capability catalog;
- reject unavailable fields, relations, operators, or surface capabilities;
- dispatch valid requests to the corresponding deterministic retrieval surface;
- preserve the retrieval surface's defined semantics;
- return canonical retrieval identities for hydration.
The control plane does not invent semantic capabilities, infer unavailable operators, reinterpret canonical values, or expand the capability catalog. Runtime limits, budgets, and other runtime policy remain separate from projection identity and from the capability catalog. The concrete control-plane request schema, dispatch interface, and shared operator representation are intentionally deferred until the constitutive retrieval surfaces have been implemented and validated. That later design must be derived from the actual accepted exact, lexical, vector, and graph surface contracts rather than requiring those surfaces to conform to a prematurely chosen common interface.
The capability catalog describes retrieval availability and legal operation grammar, not corpus-instance inventory. It may expose available semantic field classes, node classes, relation classes, relation names, retrieval surfaces, and supported operators. It does not enumerate canonical semantic objects, semantic regions, semantic units, or their internal identities for Model 1. Corpus instances are discovered by executing retrieval operations against the completed build.
## 12. Build publication and repair behavior
### 12.1 Publish only a referentially valid completed build
An ingest with unresolved authored links does not publish a completed build. Complete parsing and validation before marking the build publishable. On failure, keep the prior valid published build unaffected if one exists and emit the repair manifest.
### 12.2 Build validation and repair manifest
Validation is aggregate rather than fail-fast. Before rejecting an ingest, the build must collect and report every independently detectable validation failure across the included corpus. One validation failure must not prevent validation of unrelated objects or relations. The repair manifest records all detected failures with sufficient source location and target/collision information to repair them. A failure that prevents further parsing of a particular source may limit validation downstream of that source, but must not stop validation of the remainder of the corpus. Any validation failure prevents publication of the new build.
## 13 Tech stack
### 13.1 The below technologies are intended to implement the contracts above, not the other way around
#### Build runtime
- Python 3.14.x
#### Markdown parsing
- markdown-it-py
- mdit-py-plugins for supported extended Markdown structures
- a small Semantic Traversal markdown-it-py extension for Obsidian wikilinks/callout syntax that the upstream parser does not represent
#### Frontmatter parsing
- ruamel.yaml
- YAML 1.2
- safe, pure-Python parsing mode
#### Canonical substrate storage
- SQLite
- foreign-key enforcement enabled
- canonical objects, regions, units, semantic identifiers, aliases/tags, and graph relations stored in ordinary relational tables
#### Exact retrieval
- SQLite ordinary indexed tables
- fielded inverted lookup: `(field_class, field_name, value_type, normalized_value) -> unit_id`
- normalized text comparison key uses Unicode case-folding and collapsed/trimmed whitespace
- canonical authored values remain unchanged
#### Lexical retrieval
- SQLite FTS5
- separate searchable fields
- unicode61 tokenizer
- case-insensitive
- preserve diacritics
- no stemming
- no custom stopword removal
- no synonym expansion
- BM25 ranking
- phrase queries enabled
- `unicode61 remove_diacritics 0`
- BM25 populations isolated by lexical field dimension
#### Graph storage
- ordinary SQLite relation/edge tables
- indexed source, target, and relation-type columns
- no separate graph database
- isolated SQLite FTS5 graph-discovery dimensions for:
  - semantic_object / address_text
  - semantic_object / tag
  - semantic_region / address_text
#### Embedding runtime
- Ollama
- qwen3-embedding:0.6b
- pin the exact resolved model identity/digest used by the build
- semantic-unit vector input is exact eligible `parsed_text`
- zero-unit semantic-object vector input is exact canonical authored object name
- no metadata enrichment or embedding instruction prefix
- 1024-dimensional float vectors
- L2-normalize corpus and query vectors
- cosine similarity
#### Vector storage/search
- NumPy float32 matrix stored as `.npy`
- one matrix row per vector record/segment
- SQLite stores the mapping between each matrix row and:
  - target_kind
  - target_identity
  - segment_ordinal
- multiple segments of one represented target retain the same target_kind and target_identity
- SQLite persists the completed-build embedding compatibility record
- exhaustive cosine search over the complete matrix
- maximum segment score collapses to one result per canonical target
- no constitutive similarity threshold
- no constitutive top-k result limit
- no ANN/vector database
#### Generated capability catalog
- JSON
- structured field/capability matrix + relation grammar + operator table
#### Build manifest
- JSON
- records build schema/version, build configuration, admitted semantic-identifier fields, parser versions/configuration, embedding model name/tag and resolved digest, embedding dimensions, vector dtype, normalization, similarity metric, and input-capacity/segmentation contract, lexical tokenizer configuration, and generated artifact identities.
#### Published build artifacts
- substrate.sqlite3
- vectors.npy
- capability_catalog.json
- manifest.json
## Specimen acceptance checklist
The specimen acceptance checks use `07_Tuesday.md` for its note-local object, region, unit, inheritance, and relation behavior, plus a separate zero-unit semantic-object fixture for object-fallback behavior. Together the specimen fixtures must prove all of the following:
- One semantic object is created with the recorded UUID.
- Four regions are created.
- Six semantic units are created with individual `unit_id`.
- Blank separator lines create no units.
- Every unit retains source-object traceability through `unit_id`.
- Every unit inherits the complete meaningful path.
- Every unit inherits the complete containing region path.
- Every unit preserves raw Markdown and parsed text separately.
- Every admitted blank frontmatter field differs from an absent field.
- Every unit inherits admitted scalar identifier state.
- Every unit inherits typed relations from admitted frontmatter wikilinks.
- Every body wikilink creates `linked_to`.
- Frontmatter-derived and body-derived relations to the same target remain distinct denoted through path type either carrying frontmatter semantics or body default `linked_to`.
- Object and region wikilinks resolve or the ingest fails with a repair report.
- Every unit inherits admitted frontmatter as semantic identifiers.
- Every vector-eligible semantic unit produces one or more vector records derived only from its parsed intrinsic text.
- A semantic unit whose parsed_text is empty or whitespace-only produces no vector record.
- A semantic object containing zero semantic units produces one semantic-object vector representation derived only from its canonical authored object name.
- An object containing one or more semantic units produces no object-name fallback vector.
- Semantic-unit vector input contains only eligible canonical `parsed_text` and excludes UUID, unit ID, path, region heading text, frontmatter semantic identifiers, relations, and raw Markdown syntax.
- Zero-unit semantic-object vector input contains only its canonical authored object name and excludes full source path/hierarchy, aliases, tags, titles, semantic identifiers, regions, and relations.
- Every retrieval representation returns a typed canonical retrieval identity that hydrates its complete canonical target. Semantic-unit evidence also identifies and hydrates its owning canonical object for object-level provenance.
- No invalid build is published as a completed capability catalog.
- The semantic capability catalog exposes every represented semantic and relation type and accurately states surface availability.
- Concatenating overflow vector segments exactly reproduces the authorized vector input and segments do not overlap.
- Multiple vector segments for one canonical target produce one retrieval hit using the maximum cosine score.
- Vector retrieval applies no constitutive similarity threshold or result cap.
- An incompatible query-time embedding contract fails explicitly.
- An over-capacity query fails rather than being truncated or segmented.
- Failure to embed any vector-eligible canonical target prevents a completed vector build.