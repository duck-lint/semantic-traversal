import sqlite3
import unittest
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from ugh_parser import (
    BuildConfig,
    LexicalHit,
    build_lexical_index,
    canonicalize_ingest,
    lexical_integrity_check,
    lexical_lookup,
    materialize_context,
    parse_vault,
    resolve_relations,
    write_completed_ingest,
    hydrate_unit,
)


class LexicalRetrievalTests(unittest.TestCase):
    def _write(self, root: Path, name: str, source: str) -> None:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    def _build(self):
        directory = TemporaryDirectory()
        root = Path(directory.name)
        self._write(root, "journal/2026/source.md", "---\nuuid: source-uuid\nparsed_text: metadata lexical namespace\ntitle: Café Running\nlabels: [Alpha, Beta, [Hidden]]\naliases: [AliasOne, AliasTwo]\ntags: [TagOne, TagTwo, repeat, repeat]\nnumber: 7\nflag: true\nday: 2026-01-02\nmoment: 2026-01-02T03:04:05\nmapping: {label: HiddenMapping}\nblank:\n---\nAlpha beta socially necessary labour time\n# Philosophy\nphilosophy body\n## Schopenhauer\nfourfold root socially necessary labour time\n")
        self._write(root, "other.md", "---\nuuid: other-uuid\n---\nAlpha independent text\n")
        config = BuildConfig(
            "test", "uuid", (),
            ("parsed_text", "title", "labels", "aliases", "tags", "number", "flag", "day", "moment", "mapping", "blank", "missing"),
        )
        parsed = parse_vault(root, config)
        materialized = materialize_context(parsed)
        resolved = resolve_relations(materialized)
        ingest = canonicalize_ingest(resolved)
        connection = sqlite3.connect(":memory:")
        write_completed_ingest(connection, ingest)
        return directory, ingest, connection

    def test_build_uses_persisted_sqlite_state_and_hydration_boundary(self):
        directory, ingest, connection = self._build()
        try:
            expected = ingest.units[1]
            ingest = None
            build_lexical_index(connection)
            hits = lexical_lookup(connection, "intrinsic", "parsed_text", "terms", ["philosophy"])
            self.assertTrue(hits)
            self.assertEqual(hits[0].unit_id, expected.unit_id)
            self.assertEqual(hydrate_unit(connection, hits[0].unit_id), expected)
        finally:
            connection.close()
            directory.cleanup()

    def test_field_classes_are_explicit_and_raw_markdown_is_not_lexical(self):
        directory, _, connection = self._build()
        try:
            build_lexical_index(connection)
            dimensions = set(connection.execute(
                "SELECT field_class, field_name FROM lexical_dimension_registry"
            ).fetchall())
            self.assertIn(("intrinsic", "parsed_text"), dimensions)
            self.assertIn(("semantic_identifier", "parsed_text"), dimensions)
            self.assertIn(("region", "region_text"), dimensions)
            self.assertIn(("semantic_path", "path_component"), dimensions)
            self.assertNotIn(("intrinsic", "raw_markdown"), dimensions)
            with self.assertRaises(KeyError):
                lexical_lookup(connection, "intrinsic", "raw_markdown", "terms", ["Alpha"])
            self.assertTrue(lexical_lookup(connection, "semantic_identifier", "parsed_text", "terms", ["metadata"]))
            self.assertFalse(lexical_lookup(connection, "intrinsic", "parsed_text", "terms", ["metadata"]))
        finally:
            connection.close()
            directory.cleanup()

    def test_textual_values_terms_or_phrase_and_tokenization(self):
        directory, _, connection = self._build()
        try:
            build_lexical_index(connection)
            terms = lexical_lookup(connection, "intrinsic", "parsed_text", "terms", ["socially", "independent"])
            self.assertEqual({hit.unit_id for hit in terms}, {1, 3, 4})
            phrase = lexical_lookup(connection, "intrinsic", "parsed_text", "phrase", "socially necessary labour time")
            self.assertEqual({hit.unit_id for hit in phrase}, {1, 3})
            self.assertEqual(lexical_lookup(connection, "intrinsic", "parsed_text", "phrase", "socially labour"), ())
            self.assertTrue(lexical_lookup(connection, "semantic_identifier", "title", "terms", ["café"]))
            self.assertEqual(lexical_lookup(connection, "semantic_identifier", "title", "terms", ["cafe"]), ())
            self.assertTrue(lexical_lookup(connection, "semantic_identifier", "title", "terms", ["running"]))
            self.assertEqual(lexical_lookup(connection, "semantic_identifier", "title", "terms", ["run"]), ())
        finally:
            connection.close()
            directory.cleanup()

    def test_terms_operands_must_be_single_configured_tokens(self):
        directory, _, connection = self._build()
        try:
            build_lexical_index(connection)
            self.assertEqual(
                {hit.unit_id for hit in lexical_lookup(connection, "intrinsic", "parsed_text", "terms", ["socially", "independent"])},
                {1, 3, 4},
            )
            with self.assertRaises(ValueError):
                lexical_lookup(connection, "intrinsic", "parsed_text", "terms", ["socially necessary"])
            with self.assertRaises(ValueError):
                lexical_lookup(connection, "intrinsic", "parsed_text", "terms", ["socially-necessary"])
            self.assertTrue(lexical_lookup(connection, "semantic_identifier", "title", "terms", ["café"]))
            self.assertTrue(lexical_lookup(connection, "intrinsic", "parsed_text", "phrase", "socially necessary"))
            with self.assertRaises(ValueError):
                lexical_lookup(connection, "intrinsic", "parsed_text", "terms", ["socially OR necessary"])
        finally:
            connection.close()
            directory.cleanup()

    def test_identifiers_only_lexicalize_direct_strings(self):
        directory, _, connection = self._build()
        try:
            build_lexical_index(connection)
            self.assertTrue(lexical_lookup(connection, "semantic_identifier", "labels", "terms", ["alpha"]))
            self.assertEqual(lexical_lookup(connection, "semantic_identifier", "labels", "terms", ["hidden"]), ())
            self.assertTrue(lexical_lookup(connection, "semantic_identifier", "aliases", "terms", ["aliasone"]))
            self.assertTrue(lexical_lookup(connection, "semantic_identifier", "tags", "terms", ["tagone"]))
            for field, value in (("number", "7"), ("flag", "true"), ("day", "2026"), ("moment", "03:04"), ("mapping", "hiddenmapping")):
                with self.assertRaises(KeyError):
                    lexical_lookup(connection, "semantic_identifier", field, "terms", [value])
            for field in ("blank", "missing"):
                with self.assertRaises(KeyError):
                    lexical_lookup(connection, "semantic_identifier", field, "terms", ["anything"])
        finally:
            connection.close()
            directory.cleanup()

    def test_regions_and_paths_are_independent_occurrences(self):
        directory, _, connection = self._build()
        try:
            build_lexical_index(connection)
            self.assertTrue(lexical_lookup(connection, "region", "region_text", "terms", ["philosophy"]))
            self.assertTrue(lexical_lookup(connection, "region", "region_text", "terms", ["schopenhauer"]))
            self.assertEqual(lexical_lookup(connection, "region", "region_text", "phrase", "philosophy schopenhauer"), ())
            self.assertTrue(lexical_lookup(connection, "semantic_path", "path_component", "terms", ["journal"]))
            self.assertTrue(lexical_lookup(connection, "semantic_path", "path_component", "terms", ["2026"]))
            self.assertEqual(lexical_lookup(connection, "semantic_path", "path_component", "phrase", "journal 2026"), ())
            self.assertEqual(lexical_lookup(connection, "region", "region_text", "terms", ["region"]), ())
        finally:
            connection.close()
            directory.cleanup()

    def test_duplicate_occurrences_use_one_best_score_and_field_isolation(self):
        directory, _, connection = self._build()
        try:
            build_lexical_index(connection)
            hits = lexical_lookup(connection, "semantic_identifier", "tags", "terms", ["repeat"])
            self.assertEqual([hit.unit_id for hit in hits], [1, 2, 3])
            self.assertTrue(all(isinstance(hit, LexicalHit) for hit in hits))
            registry = connection.execute(
                "SELECT table_name FROM lexical_dimension_registry WHERE field_class = 'semantic_identifier' AND field_name = 'tags'"
            ).fetchone()[0]
            raw_scores = [row[0] for row in connection.execute(
                f'SELECT bm25("{registry}") FROM "{registry}" WHERE "{registry}" MATCH ?', ('"repeat"',)
            ).fetchall()]
            self.assertEqual(hits[0].score, min(raw_scores))
            parsed_before = lexical_lookup(connection, "intrinsic", "parsed_text", "terms", ["alpha"])
            path_table = connection.execute(
                "SELECT table_name FROM lexical_dimension_registry WHERE field_class = 'semantic_path'"
            ).fetchone()[0]
            connection.execute(f'INSERT INTO "{path_table}" (unit_id, content) VALUES (?, ?)', (9999, "many unrelated words"))
            parsed_after = lexical_lookup(connection, "intrinsic", "parsed_text", "terms", ["alpha"])
            self.assertEqual(parsed_before, parsed_after)
            self.assertEqual(parsed_after, tuple(sorted(parsed_after, key=lambda hit: (hit.score, hit.unit_id))))
        finally:
            connection.close()
            directory.cleanup()

    def test_no_result_limit_and_integrity(self):
        directory, _, connection = self._build()
        try:
            build_lexical_index(connection)
            hits = lexical_lookup(connection, "intrinsic", "parsed_text", "terms", ["alpha", "independent"])
            self.assertEqual({hit.unit_id for hit in hits}, {1, 4})
            self.assertEqual(lexical_integrity_check(connection), ())
        finally:
            connection.close()
            directory.cleanup()


if __name__ == "__main__":
    unittest.main()
