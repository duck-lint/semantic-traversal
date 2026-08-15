import sqlite3
import unittest
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from ugh_parser import (
    BuildConfig, build_exact_index, canonicalize_ingest, exact_lookup,
    hydrate_unit, materialize_context, parse_vault, resolve_relations,
    write_completed_ingest,
)


class ExactRetrievalTests(unittest.TestCase):
    def _write(self, root: Path, name: str, source: str) -> None:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    def _build(self):
        directory = TemporaryDirectory()
        root = Path(directory.name)
        self._write(root, "a/b/source.md", "---\nuuid: source-uuid\ntitle: Work\nnumber: 7\nflag: true\nday: 2024-01-02\nmoment: 2024-01-02T03:04:05\nunity_level: [model, meta, model]\naliases: [Alpha Name, Second Name]\ntags: [One, Two]\nparsed_text: authored parsed metadata\nregion_path: authored region metadata\npath_component: authored path metadata\nblank:\n---\nunheaded raw\n# Outer Heading\nouter text\n## Inner Heading\ninner text\n")
        self._write(root, "target.md", "---\nuuid: target-uuid\n---\n# Target Region\ntarget text\n")
        config = BuildConfig(
            "test", "uuid", (),
            ("title", "number", "flag", "day", "moment", "unity_level", "aliases", "tags", "parsed_text", "region_path", "path_component", "blank", "missing"),
        )
        parsed = parse_vault(root, config)
        materialized = materialize_context(parsed)
        resolved = resolve_relations(materialized)
        ingest = canonicalize_ingest(resolved)
        connection = sqlite3.connect(":memory:")
        write_completed_ingest(connection, ingest)
        return directory, ingest, connection

    def test_build_is_derived_from_sqlite_and_lookup_hydrates_canonical_units(self):
        directory, ingest, connection = self._build()
        try:
            expected_unit = ingest.units[1]
            ingest = None
            build_exact_index(connection)
            for unit_id in exact_lookup(connection, "intrinsic", "parsed_text", "OUTER   TEXT"):
                self.assertEqual(hydrate_unit(connection, unit_id), expected_unit)
            self.assertEqual(exact_lookup(connection, "intrinsic", "raw_markdown", "  outer text\n "), (2,))
        finally:
            connection.close()
            directory.cleanup()

    def test_text_equality_normalization_and_no_substring_matching(self):
        directory, _, connection = self._build()
        try:
            build_exact_index(connection)
            self.assertEqual(exact_lookup(connection, "intrinsic", "parsed_text", "  INNER   TEXT  "), (3,))
            self.assertEqual(exact_lookup(connection, "intrinsic", "parsed_text", "inner"), ())
            self.assertEqual(exact_lookup(connection, "intrinsic", "parsed_text", "text"), ())
        finally:
            connection.close()
            directory.cleanup()

    def test_typed_values_and_identifier_states_remain_distinct(self):
        directory, _, connection = self._build()
        try:
            build_exact_index(connection)
            self.assertEqual(exact_lookup(connection, "semantic_identifier", "number", 7), (1, 2, 3))
            self.assertEqual(exact_lookup(connection, "semantic_identifier", "number", "7"), ())
            self.assertEqual(exact_lookup(connection, "semantic_identifier", "flag", True), (1, 2, 3))
            self.assertEqual(exact_lookup(connection, "semantic_identifier", "flag", 1), ())
            self.assertEqual(exact_lookup(connection, "semantic_identifier", "day", date(2024, 1, 2)), (1, 2, 3))
            self.assertEqual(exact_lookup(connection, "semantic_identifier", "day", "2024-01-02"), ())
            self.assertEqual(exact_lookup(connection, "semantic_identifier", "moment", datetime(2024, 1, 2, 3, 4, 5)), (1, 2, 3))
            self.assertEqual(exact_lookup(connection, "semantic_identifier", "moment", "2024-01-02T03:04:05"), ())
            self.assertEqual(exact_lookup(connection, "semantic_identifier", "blank", ""), ())
            self.assertEqual(exact_lookup(connection, "semantic_identifier", "missing", "anything"), ())
        finally:
            connection.close()
            directory.cleanup()

    def test_sequence_alias_and_tag_members_are_independently_searchable(self):
        directory, ingest, connection = self._build()
        try:
            build_exact_index(connection)
            self.assertEqual(exact_lookup(connection, "semantic_identifier", "unity_level", "model"), (1, 2, 3))
            self.assertEqual(exact_lookup(connection, "semantic_identifier", "unity_level", "meta"), (1, 2, 3))
            self.assertEqual(exact_lookup(connection, "semantic_identifier", "aliases", "alpha name"), (1, 2, 3))
            self.assertEqual(exact_lookup(connection, "semantic_identifier", "tags", "one"), (1, 2, 3))
            self.assertEqual(exact_lookup(connection, "semantic_identifier", "parsed_text", "authored parsed metadata"), (1, 2, 3))
            self.assertEqual(exact_lookup(connection, "semantic_identifier", "region_path", "authored region metadata"), (1, 2, 3))
            self.assertEqual(exact_lookup(connection, "semantic_identifier", "path_component", "authored path metadata"), (1, 2, 3))
            self.assertEqual(exact_lookup(connection, "intrinsic", "parsed_text", "authored parsed metadata"), ())
            self.assertEqual(exact_lookup(connection, "region", "region_path", ("authored region metadata",)), ())
            self.assertEqual(
                [row[1] for row in connection.execute("PRAGMA table_info(exact_index_entries)")],
                ["exact_entry_id", "field_class", "field_name", "value_type", "normalized_value", "unit_id"],
            )
            self.assertEqual(
                [field.name for field in ingest.units[0].inherited_identifiers],
                ["title", "number", "flag", "day", "moment", "unity_level", "aliases", "tags", "parsed_text", "region_path", "path_component", "blank", "missing"],
            )
            self.assertEqual(ingest.units[0].inherited_identifiers[5].value, ["model", "meta", "model"])
            self.assertEqual(exact_lookup(connection, "semantic_identifier", "unity_level", "model"), (1, 2, 3))
        finally:
            connection.close()
            directory.cleanup()

    def test_region_and_semantic_path_values_are_complete_and_ordered(self):
        directory, _, connection = self._build()
        try:
            build_exact_index(connection)
            self.assertEqual(exact_lookup(connection, "region", "region_path", ()), (1,))
            self.assertEqual(exact_lookup(connection, "region", "region_path", ("Outer Heading",)), (2,))
            self.assertEqual(exact_lookup(connection, "region", "region_path", ("Outer Heading", "Inner Heading")), (3,))
            self.assertEqual(exact_lookup(connection, "region", "region_path", ("region-0001",)), ())
            self.assertEqual(exact_lookup(connection, "semantic_path", "path_hierarchy", ("a", "b")), (1, 2, 3))
            self.assertEqual(exact_lookup(connection, "semantic_path", "path_component", "A"), (1, 2, 3))
        finally:
            connection.close()
            directory.cleanup()

    def test_index_invariant_and_no_later_stage_tables(self):
        directory, _, connection = self._build()
        try:
            build_exact_index(connection)
            invalid = connection.execute(
                """SELECT e.unit_id FROM exact_index_entries e
                LEFT JOIN canonical_units u ON u.unit_id = e.unit_id
                WHERE u.unit_id IS NULL"""
            ).fetchall()
            self.assertEqual(invalid, [])
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )}
            self.assertIn("exact_index_entries", tables)
            self.assertFalse(any(name.endswith("_fts") for name in tables))
            self.assertFalse(any(term in name for name in tables for term in (
                "graph", "vector", "catalog", "manifest", "publication", "planner", "runtime"
            )))
        finally:
            connection.close()
            directory.cleanup()


if __name__ == "__main__":
    unittest.main()
