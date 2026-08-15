import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ugh_parser import (
    BuildConfig,
    MaterializationError,
    VaultParseResult,
    materialize_context,
    parse_vault,
)
from ugh_parser.vault import CorpusFailure


class ContextMaterializationTests(unittest.TestCase):
    def _write(self, root: Path, name: str, source: str) -> Path:
        path = root / name
        path.write_text(source, encoding="utf-8")
        return path

    def test_inherits_states_and_materializes_distinct_frontmatter_and_body_reasons(self):
        config = BuildConfig("test", "uuid", (), ("admitted", "blank", "missing"))
        source = """---
uuid: object
admitted: "[[Object#Region|front label]]"
blank:
---
# One
body [[Object#Region|body label]]
# Two
no link here
"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "note.md", source)
            materialized = materialize_context(parse_vault(root, config))
        self.assertEqual(len(materialized.units), 2)
        for unit in materialized.units:
            self.assertEqual([(field.name, field.state) for field in unit.inherited_identifiers], [
                ("admitted", "present_value"), ("blank", "present_blank"), ("missing", "absent")
            ])
            self.assertNotIn("uuid", [field.name for field in unit.inherited_identifiers])
        self.assertEqual([(r.relation_name, r.origin, r.target, r.label, r.target_region_fragment) for r in materialized.units[0].relations], [
            ("admitted", "frontmatter", "Object", "front label", "Region"),
            ("linked_to", "body", "Object", "body label", "Region"),
        ])
        self.assertEqual(materialized.units[1].relations[0].origin, "frontmatter")
        self.assertEqual(materialized.units[1].relations[0].relation_name, "admitted")

    def test_non_admitted_frontmatter_wikilink_is_not_materialized(self):
        config = BuildConfig("test", "uuid", (), ("admitted",))
        source = "---\nuuid: object\nadmitted: \"[[admitted]]\"\nnot_admitted: \"[[ignored]]\"\n---\ntext\n"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "note.md", source)
            materialized = materialize_context(parse_vault(root, config))
        self.assertEqual([(r.relation_name, r.target) for r in materialized.units[0].relations], [("admitted", "admitted")])

    def test_heading_wikilinks_are_region_structure_not_body_relations(self):
        config = BuildConfig("test", "uuid", (), ())
        source = "---\nuuid: object\n---\n# [[Target|Visible]]\nbody text\n"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "note.md", source)
            materialized = materialize_context(parse_vault(root, config))
        self.assertEqual(materialized.units[0].relations, ())
        self.assertEqual(materialized.parsed_corpus.notes[0].regions[0].parsed_text, "Visible")
        self.assertEqual(materialized.parsed_corpus.notes[0].regions[0].address_text, "Target Visible")

    def test_list_valued_frontmatter_and_embeds_remain_structured(self):
        config = BuildConfig("test", "uuid", (), ("book_read_today",))
        source = "---\nuuid: object\nbook_read_today:\n  - \"[[Book#Chapter|book label]]\"\n---\n![[assets/image.png]]\n"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "note.md", source)
            materialized = materialize_context(parse_vault(root, config))
        unit = materialized.units[0]
        self.assertEqual(unit.relations[0].relation_name, "book_read_today")
        self.assertEqual(unit.relations[0].target_region_fragment, "Chapter")
        self.assertEqual(unit.embeds[0].target, "assets/image.png")
        self.assertEqual(unit.embeds[0].raw, "![[assets/image.png]]")
        self.assertFalse(any(relation.origin == "body" for relation in unit.relations))

    def test_invalid_corpus_is_rejected_before_materialization(self):
        config = BuildConfig("test", "uuid", (), ())
        invalid = VaultParseResult((), (CorpusFailure("missing_uuid", "missing", ("bad.md",)),), config)
        with self.assertRaises(MaterializationError):
            materialize_context(invalid)

    def test_no_global_unit_id_is_introduced(self):
        config = BuildConfig("test", "uuid", (), ())
        source = "---\nuuid: object\n---\ntext\n"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "note.md", source)
            materialized = materialize_context(parse_vault(root, config))
        self.assertFalse(hasattr(materialized.units[0], "unit_id"))

    def test_parsed_configuration_is_bound_and_retained_by_reference(self):
        config = BuildConfig("test", "uuid", (), ("admitted",))
        other = BuildConfig("other", "identity", (), ("different",))
        source = "---\nuuid: object\nadmitted: value\n---\ntext\n"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "note.md", source)
            parsed = parse_vault(root, config)
            materialized = materialize_context(parsed)
        self.assertIs(parsed.build_config, config)
        self.assertIs(materialized.parsed_corpus, parsed)
        self.assertIs(materialized.build_config, config)
        self.assertEqual(materialized.units[0].inherited_identifiers[0].name, "admitted")
        with self.assertRaises(TypeError):
            materialize_context(parsed, other)


if __name__ == "__main__":
    unittest.main()
