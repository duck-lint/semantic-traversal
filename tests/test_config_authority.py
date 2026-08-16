import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import ugh_parser

from ugh_parser import (
    NoteParseError,
    SemanticIdentifierDeclaration,
    load_build_config,
    parse_note,
)


class ConfigAuthorityTests(unittest.TestCase):
    def _write_config(self, root: Path, declarations: str, *, old_list: str = "") -> Path:
        path = root / "config.yaml"
        path.write_text(
            "vault_name: test\nuuid_field: uuid\nexcluded_folders: []\n"
            + (old_list if old_list else "semantic_identifiers:\n" + declarations),
            encoding="utf-8",
        )
        return path

    def test_declaration_mapping_is_one_ordered_admission_and_meaning_authority(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = self._write_config(
                root,
                "  first:\n    description: First authored meaning\n"
                "  second:\n    description: |\n      Second authored meaning\n",
            )
            config = load_build_config(path)
            self.assertEqual(config.semantic_identifier_fields, ("first", "second"))
            self.assertEqual(config.semantic_identifiers, (
                SemanticIdentifierDeclaration("first", "First authored meaning"),
                SemanticIdentifierDeclaration("second", "Second authored meaning\n"),
            ))
            self.assertEqual(dict(config.semantic_identifier_descriptions), {
                "first": "First authored meaning",
                "second": "Second authored meaning\n",
            })
            with self.assertRaises(TypeError):
                config.semantic_identifier_descriptions["first"] = "changed"

    def test_old_sibling_admission_list_is_rejected(self):
        with TemporaryDirectory() as directory:
            path = self._write_config(
                Path(directory),
                "",
                old_list="semantic_identifier_fields: [old_field]\n",
            )
            with self.assertRaisesRegex(NoteParseError, "semantic_identifiers"):
                load_build_config(path)

    def test_invalid_declarations_fail_closed(self):
        cases = (
            "  field:\n    description: ''\n",
            "  field:\n",
            "  field:\n    description: 7\n",
            "  field: value\n",
            "  :\n    description: bad\n",
        )
        for declarations in cases:
            with self.subTest(declarations=declarations), TemporaryDirectory() as directory:
                with self.assertRaises(NoteParseError):
                    load_build_config(self._write_config(Path(directory), declarations))

    def test_incomplete_declarations_remain_validation_failures(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = self._write_config(
                root,
                "  represented:\n    description: supplied\n"
                "  unrepresented:\n    description: ''\n"
                "  absent_description: {}\n",
            )
            with self.assertRaises(NoteParseError):
                load_build_config(path)

    def test_homework_artifact_api_is_not_public(self):
        self.assertFalse(hasattr(ugh_parser, "semantic_identifier_homework"))
        self.assertFalse(hasattr(ugh_parser, "write_semantic_identifier_homework"))

    def test_adding_or_removing_a_declaration_changes_admission_without_touching_values(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "note.md"
            source.write_text("---\nuuid: object\nkept: authored\nadded: value\n---\nbody\n", encoding="utf-8")
            first_config = load_build_config(self._write_config(root, "  kept:\n    description: kept meaning\n"))
            second_config = load_build_config(self._write_config(root, "  kept:\n    description: kept meaning\n  added:\n    description: added meaning\n"))
            first = parse_note(source, vault_root=root, build_config=first_config)
            second = parse_note(source, vault_root=root, build_config=second_config)
            self.assertEqual(first_config.semantic_identifier_fields, ("kept",))
            self.assertEqual(second_config.semantic_identifier_fields, ("kept", "added"))
            self.assertEqual([field.name for field in first.semantic_object.admitted_fields], ["kept"])
            self.assertEqual([field.name for field in second.semantic_object.admitted_fields], ["kept", "added"])
            self.assertEqual(first.semantic_object.frontmatter["kept"], "authored")
            self.assertNotIn("kept meaning", first.semantic_object.frontmatter.values())
            self.assertNotIn("added meaning", second.semantic_object.frontmatter.values())


if __name__ == "__main__":
    unittest.main()
