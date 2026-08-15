import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ugh_parser import (
    BuildConfig,
    canonicalize_ingest,
    foreign_key_check,
    hydrate_unit,
    materialize_context,
    parse_vault,
    resolve_relations,
    write_completed_ingest,
)


class CanonicalSubstrateTests(unittest.TestCase):
    def _write(self, root: Path, name: str, source: str) -> None:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    def _build(self):
        directory = TemporaryDirectory()
        root = Path(directory.name)
        self._write(root, "a/source.md", """---
uuid: source-uuid
number: 7
ratio: 1.5
flag: true
items: [first, second]
blank:
relation: "[[target#Inner Target|front]]"
---
preamble ![[assets/image.png|diagram]]
# Outer
## Inner
body [[target|same]] [[target|same]]
""")
        self._write(root, "target.md", """---
uuid: target-uuid
---
# Inner Target
target body
""")
        config = BuildConfig(
            "test", "uuid", (),
            ("number", "ratio", "flag", "items", "blank", "missing", "relation"),
        )
        parsed = parse_vault(root, config)
        materialized = materialize_context(parsed)
        resolved = resolve_relations(materialized)
        return directory, canonicalize_ingest(resolved)

    def test_round_trip_preserves_canonical_semantics_and_order(self):
        directory, ingest = self._build()
        try:
            connection = sqlite3.connect(":memory:")
            write_completed_ingest(connection, ingest)
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(foreign_key_check(connection), ())
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM canonical_objects").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM canonical_regions").fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM canonical_units").fetchone()[0], len(ingest.units))
            for original in ingest.units:
                self.assertEqual(hydrate_unit(connection, original.unit_id), original)

            unit = hydrate_unit(connection, 1)
            self.assertEqual(unit.source_object_uuid, "source-uuid")
            self.assertEqual(unit.unit_id, 1)
            self.assertEqual(unit.source_local_order, 1)
            self.assertEqual(unit.path_hierarchy, ("a",))
            self.assertEqual(unit.region_path, ())
            self.assertEqual(unit.raw_markdown, "preamble ![[assets/image.png|diagram]]\n")
            self.assertEqual(unit.parsed_text, "preamble ")
            self.assertEqual([(f.name, f.state, f.value) for f in unit.inherited_identifiers], [
                ("number", "present_value", 7), ("ratio", "present_value", 1.5),
                ("flag", "present_value", True), ("items", "present_value", ["first", "second"]),
                ("blank", "present_blank", None), ("missing", "absent", None),
                ("relation", "present_value", "[[target#Inner Target|front]]"),
            ])
            self.assertEqual(len(unit.embeds), 1)
            self.assertEqual((unit.embeds[0].raw, unit.embeds[0].target, unit.embeds[0].label,
                              unit.embeds[0].target_region_fragment),
                             ("![[assets/image.png|diagram]]", "assets/image.png", "diagram", None))
            relation_unit = hydrate_unit(connection, 2)
            self.assertEqual(len(relation_unit.relations), 3)
            self.assertEqual(relation_unit.relations[1], relation_unit.relations[2])
            self.assertEqual([(r.origin, r.relation_name) for r in relation_unit.relations], [
                ("frontmatter", "relation"), ("body", "linked_to"), ("body", "linked_to")
            ])
            self.assertEqual(relation_unit.relations[0].target_object_uuid, "target-uuid")
            self.assertEqual(relation_unit.relations[0].target_region.region_path, ("region-0001",))
        finally:
            directory.cleanup()

    def test_hydration_uses_reopened_sqlite_state_and_unknown_id_fails(self):
        directory, ingest = self._build()
        try:
            with TemporaryDirectory() as storage:
                database = Path(storage) / "substrate.sqlite3"
                connection = sqlite3.connect(database)
                write_completed_ingest(connection, ingest)
                connection.close()
                reopened = sqlite3.connect(database)
                self.assertEqual(hydrate_unit(reopened, 1), ingest.units[0])
                with self.assertRaises(KeyError):
                    hydrate_unit(reopened, 999999)
                reopened.close()
        finally:
            directory.cleanup()

    def test_foreign_keys_reject_orphan_rows_and_stage_has_no_later_artifacts(self):
        directory, ingest = self._build()
        try:
            connection = sqlite3.connect(":memory:")
            write_completed_ingest(connection, ingest)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO unit_path_components VALUES (?, ?, ?)", (999, 0, "orphan"))
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )}
            self.assertEqual(tables, {
            "canonical_objects", "object_path_components", "object_identifiers", "object_relations", "canonical_regions",
                "region_path_components", "canonical_units", "unit_path_components",
                "unit_region_path", "inherited_identifiers", "canonical_relations",
                "structured_embeds",
            })
            self.assertFalse(any(name.endswith("_fts") for name in tables))
            self.assertFalse(any(term in name for name in tables for term in ("graph", "vector", "catalog", "manifest", "publication")))
        finally:
            directory.cleanup()


if __name__ == "__main__":
    unittest.main()
