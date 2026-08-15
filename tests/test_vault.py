import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ugh_parser import (
    BuildConfig,
    NoteParseError,
    discover_markdown_notes,
    parse_vault,
)


class WholeVaultParsingTests(unittest.TestCase):
    def _write(self, root: Path, relative: str, content: str) -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_discovers_root_and_nested_notes_with_exact_subtree_exclusion(self):
        config = BuildConfig("test", "uuid", ("private/notes",), ())
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "root.md", "---\nuuid: root\n---\nroot\n")
            self._write(root, "included/child.md", "---\nuuid: child\n---\nchild\n")
            self._write(root, "private/notes/hidden.md", "---\nuuid: hidden\n---\nhidden\n")
            self._write(root, "other/notes/same-leaf.md", "---\nuuid: other\n---\nother\n")
            discovered = discover_markdown_notes(root, config)
        self.assertEqual([path.relative_to(root).as_posix() for path in discovered], ["included/child.md", "other/notes/same-leaf.md", "root.md"])

    def test_malformed_note_does_not_prevent_independent_note(self):
        config = BuildConfig("test", "uuid", (), ())
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "bad.md", "not frontmatter\n")
            self._write(root, "good.md", "---\nuuid: good\n---\nworks\n")
            result = parse_vault(root, config)
        self.assertEqual([note.semantic_object.uuid for note in result.notes], ["good"])
        self.assertEqual(result.failures[0].kind, "parse")
        self.assertEqual(result.failures[0].source_paths, ("bad.md",))

    def test_missing_and_duplicate_uuid_failures_are_aggregated(self):
        config = BuildConfig("test", "identity", (), ())
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "missing.md", "---\nname: missing\n---\nmissing\n")
            self._write(root, "a.md", "---\nidentity: same\n---\na\n")
            self._write(root, "nested/b.md", "---\nidentity: same\n---\nb\n")
            self._write(root, "valid.md", "---\nidentity: valid\n---\nvalid\n")
            result = parse_vault(root, config)
        self.assertFalse(result.is_valid)
        self.assertEqual([note.semantic_object.uuid for note in result.notes], ["same", None, "same", "valid"])
        parse_failures = [failure for failure in result.failures if failure.kind == "parse"]
        missing_failures = [failure for failure in result.failures if failure.kind == "missing_uuid"]
        duplicate_failures = [failure for failure in result.failures if failure.kind == "duplicate_uuid"]
        self.assertEqual(len(parse_failures), 0)
        self.assertEqual(len(missing_failures), 1)
        self.assertIn("identity", missing_failures[0].message)
        self.assertEqual(len(duplicate_failures), 1)
        self.assertEqual(duplicate_failures[0].source_paths, ("a.md", "nested/b.md"))
        self.assertIn("identity", duplicate_failures[0].message)

    def test_valid_corpus_is_distinguished_and_units_have_no_global_id(self):
        config = BuildConfig("test", "uuid", (), ())
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "a.md", "---\nuuid: a\n---\na\n")
            result = parse_vault(root, config)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.failures, ())
        self.assertFalse(hasattr(result.notes[0].units[0], "unit_id"))

    def test_unexpected_implementation_failure_propagates(self):
        config = BuildConfig("test", "uuid", (), ())
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "note.md", "---\nuuid: note\n---\nnote\n")
            with patch("ugh_parser.vault.parse_note", side_effect=RuntimeError("implementation defect")):
                with self.assertRaisesRegex(RuntimeError, "implementation defect"):
                    parse_vault(root, config)

    def test_excluded_folder_must_be_vault_relative_without_parent_traversal(self):
        with self.assertRaises(NoteParseError):
            BuildConfig("test", "uuid", ("../private",), ())
        with self.assertRaises(NoteParseError):
            BuildConfig("test", "uuid", ("C:/private",), ())
        with self.assertRaises(NoteParseError):
            BuildConfig("test", "uuid", ("private/../notes",), ())


if __name__ == "__main__":
    unittest.main()
