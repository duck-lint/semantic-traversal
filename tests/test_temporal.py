import contextlib
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from semantic_traversal.cli import main
from semantic_traversal.runtime.retrieval.capability_catalog import load_capability_catalog
from tests.test_cli import CliProvider


class TemporalProjectionTests(unittest.TestCase):
    def _run(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def _build(self, root: Path) -> Path:
        vault = root / "vault"
        vault.mkdir()
        dates = ("2026-04-14", "2026-04-14", "2026-04-15", "2026-04-16", "2026-04-16", "2026-04-17", "2026-04-20")
        for index, value in enumerate(dates, 1):
            (vault / f"{index}.md").write_text(
                f"---\nuuid: unit-{index}\njournal_entry_date: {value}\nheadspace: future\n---\nunit {index}\n",
                encoding="utf-8",
            )
        config = root / "config.yaml"
        config.write_text(
            "vault_name: temporal\nuuid_field: uuid\nexcluded_folders: []\nsemantic_identifiers:\n"
            "  journal_entry_date:\n    description: authored journal date\n"
            "  headspace:\n    description: authored headspace\n",
            encoding="utf-8",
        )
        output = root / "build"
        with patch("semantic_traversal.cli.OllamaEmbeddingProvider", CliProvider):
            code, _, stderr = self._run("build", "--vault", str(vault), "--config", str(config), "--output", str(output))
        self.assertEqual(code, 0, stderr)
        catalog = output / "capability_catalog.json"
        code, _, stderr = self._run("catalog", "generate", "--build", str(output), "--config", str(config), "--output", str(catalog))
        self.assertEqual(code, 0, stderr)
        load_capability_catalog(catalog)
        return output

    def _query(self, build: Path, *args):
        code, stdout, stderr = self._run("temporal", *args, "--build", str(build), "--json")
        self.assertEqual(code, 0, stderr)
        return [item["unit_id"] for item in json.loads(stdout)]

    def test_all_modes_are_exhaustive_and_anchor_boundaries_are_strict(self):
        with TemporaryDirectory() as directory:
            build = self._build(Path(directory))
            self.assertEqual(self._query(build, "earliest"), [1, 2])
            self.assertEqual(self._query(build, "latest"), [7])
            self.assertEqual(self._query(build, "before", "--anchor", "2026-04-16"), [3, 1, 2])
            self.assertEqual(self._query(build, "after", "--anchor", "2026-04-16"), [6, 7])
            self.assertEqual(self._query(build, "between", "--start", "2026-04-15", "--end", "2026-04-16"), [3, 4, 5])
            self.assertEqual(self._query(build, "ordered", "--direction", "ascending"), [1, 2, 3, 4, 5, 6, 7])
            self.assertEqual(self._query(build, "ordered", "--direction", "descending"), [7, 6, 4, 5, 3, 1, 2])

    def test_headspace_is_not_temporal_and_invalid_between_is_rejected(self):
        with TemporaryDirectory() as directory:
            build = self._build(Path(directory))
            connection = __import__("sqlite3").connect(build / "substrate.sqlite3")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM temporal_index_entries WHERE field_name = 'headspace'").fetchone()[0], 0)
            connection.close()
            code, _, _ = self._run("temporal", "between", "--build", str(build), "--start", "2026-04-17", "--end", "2026-04-16")
            self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
