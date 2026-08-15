import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ugh_parser import (
    BuildConfig,
    GraphHandle,
    build_exact_index,
    build_graph,
    build_lexical_index,
    canonicalize_ingest,
    graph_discover,
    graph_integrity_check,
    graph_relation_lookup,
    graph_traverse,
    hydrate_object,
    hydrate_unit,
    materialize_context,
    parse_vault,
    resolve_relations,
    write_completed_ingest,
)


class GraphProjectionTests(unittest.TestCase):
    def _write(self, root: Path, relative: str, content: str) -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _build(self):
        config = BuildConfig(
            "test", "uuid", (), ("aliases", "tags", "book_read_today", "related", "other", "linked_to", "contains_unit"),
        )
        directory = TemporaryDirectory()
        root = Path(directory.name)
        self._write(root, "A/Marx, Karl - Capital.md", """---
uuid: marx
aliases: [Capital]
tags:
  - book
  - theory
  - "[[Target]]"
book_read_today:
  - "[[Target]]"
  - "[[Target]]"
related: "[[Target#Inner]]"
linked_to: "[[Target]]"
contains_unit: "[[Target]]"
---
# Outer
outer unit [[Target#Inner]] [[Target]]
## Inner
inner unit
""")
        self._write(root, "A/B/Target.md", """---
uuid: target
---
# Inner
target unit
""")
        self._write(root, "Zero.md", """---
uuid: zero
tags: [empty]
book_read_today: "[[Target]]"
---
""")
        parsed = parse_vault(root, config)
        completed = canonicalize_ingest(resolve_relations(materialize_context(parsed)))
        connection = sqlite3.connect(Path(directory.name) / "substrate.sqlite3")
        write_completed_ingest(connection, completed)
        build_exact_index(connection)
        build_lexical_index(connection)
        build_graph(connection)
        return directory, completed, connection

    def test_nodes_direct_edges_and_sqlite_only_rebuild(self):
        directory, completed, connection = self._build()
        try:
            counts = dict(connection.execute(
                "SELECT node_kind, COUNT(*) FROM graph_nodes GROUP BY node_kind"
            ))
            self.assertEqual(counts, {"scope": 2, "semantic_object": 3, "semantic_region": 3, "semantic_unit": 3})
            relations = {(row[0], row[1]): row[2] for row in connection.execute(
                "SELECT relation_class, relation_name, COUNT(*) FROM graph_edges GROUP BY relation_class, relation_name"
            )}
            self.assertEqual(relations[("semantic_identifier", "book_read_today")], 3)
            self.assertEqual(relations[("semantic_identifier", "related")], 1)
            self.assertEqual(relations[("semantic_identifier", "linked_to")], 1)
            self.assertEqual(relations[("semantic_identifier", "contains_unit")], 1)
            self.assertEqual(relations[("body_wikilink", "linked_to")], 2)
            self.assertEqual(relations[("structural", "contains_scope")], 1)
            self.assertEqual(relations[("structural", "contains_object")], 2)
            self.assertEqual(relations[("structural", "contains_region")], 3)
            self.assertEqual(relations[("structural", "contains_unit")], 3)
            registered = {
                name for _, name in connection.execute(
                    "SELECT relation_class, relation_name FROM graph_relation_types WHERE relation_class = 'semantic_identifier'"
                )
            }
            represented = {row[0] for row in connection.execute("SELECT DISTINCT relation_name FROM object_relations")}
            self.assertEqual(registered, represented - {"tags"})
            source = next(obj for obj in completed.objects if obj.source_object_uuid == "marx")
            self.assertEqual([relation.relation_name for relation in source.relations].count("tags"), 1)
            self.assertNotIn("aliases", registered)
            self.assertNotIn("tags", registered)
            self.assertNotIn("other", registered)
            related = graph_relation_lookup(connection, "semantic_identifier", "related")
            self.assertEqual(len(related), 1)
            self.assertEqual(related[0].target.node_kind, "semantic_region")
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM graph_discovery_registry WHERE node_kind = 'semantic_unit' OR dimension_name = 'scope'"
            ).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM graph_nodes WHERE node_kind = 'tag'"
            ).fetchone()[0], 0)
            self.assertEqual(graph_integrity_check(connection), ())
            connection.close()
            connection = sqlite3.connect(Path(directory.name) / "substrate.sqlite3")
            del completed
            build_graph(connection)
            self.assertEqual(graph_integrity_check(connection), ())
        finally:
            connection.close()
            directory.cleanup()

    def test_discovery_is_dimensioned_and_returns_canonical_handles(self):
        directory, _, connection = self._build()
        try:
            marx = graph_discover(connection, "semantic_object", "address_text", "terms", ["marx"])
            alias = graph_discover(connection, "semantic_object", "address_text", "terms", ["capital"])
            tag = graph_discover(connection, "semantic_object", "tag", "terms", ["book"])
            region = graph_discover(connection, "semantic_region", "address_text", "terms", ["inner"])
            self.assertEqual(marx[0].node, alias[0].node)
            self.assertEqual(tag[0].node, marx[0].node)
            self.assertEqual({hit.node.node_kind for hit in region}, {"semantic_region"})
            self.assertEqual(len(graph_discover(connection, "semantic_object", "tag", "terms", ["absent"])), 0)
            with self.assertRaises(ValueError):
                graph_discover(connection, "semantic_object", "address_text", "terms", ["marx capital"])
            with self.assertRaises(KeyError):
                graph_discover(connection, "semantic_unit", "address_text", "terms", ["unit"])
        finally:
            connection.close()
            directory.cleanup()

    def test_relation_lookup_and_one_hop_traversal_preserve_occurrences(self):
        directory, _, connection = self._build()
        try:
            occurrences = graph_relation_lookup(connection, "semantic_identifier", "book_read_today")
            self.assertEqual(len(occurrences), 3)
            self.assertEqual(occurrences[0].source, occurrences[1].source)
            self.assertEqual(occurrences[0].target, occurrences[1].target)
            marx = GraphHandle("semantic_object", ("marx",))
            outbound = graph_traverse(connection, marx, "semantic_identifier", "book_read_today", "outbound")
            self.assertEqual(len(outbound), 2)
            target = GraphHandle("semantic_object", ("target",))
            inbound = graph_traverse(connection, target, "semantic_identifier", "book_read_today", "inbound")
            self.assertEqual(len(inbound), 3)
            self.assertEqual(len(graph_relation_lookup(connection, "semantic_identifier", "linked_to")), 1)
            self.assertEqual(len(graph_relation_lookup(connection, "semantic_identifier", "contains_unit")), 1)
            with self.assertRaises(KeyError):
                graph_relation_lookup(connection, "semantic_identifier", "tags")
            with self.assertRaises(KeyError):
                graph_relation_lookup(connection, "semantic_identifier", "aliases")
            with self.assertRaises(KeyError):
                graph_relation_lookup(connection, "semantic_identifier", "other")
            with self.assertRaises(KeyError):
                graph_relation_lookup(connection, "body_wikilink", "missing")
            with self.assertRaises(ValueError):
                graph_traverse(connection, marx, "semantic_identifier", "book_read_today", "both")
            with self.assertRaises(KeyError):
                graph_traverse(connection, GraphHandle("semantic_object", ("missing",)), "structural", "contains_unit", "outbound")
        finally:
            connection.close()
            directory.cleanup()

    def test_existing_hydration_surfaces_remain_usable(self):
        directory, completed, connection = self._build()
        try:
            self.assertEqual(hydrate_object(connection, "zero"), next(obj for obj in completed.objects if obj.source_object_uuid == "zero"))
            for unit in completed.units:
                self.assertEqual(hydrate_unit(connection, unit.unit_id), unit)
        finally:
            connection.close()
            directory.cleanup()


if __name__ == "__main__":
    unittest.main()
