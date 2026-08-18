import contextlib
import datetime as dt
import io
import json
import shutil
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from semantic_traversal.cli import main
from semantic_traversal.projection.substrate import _json_value
from semantic_traversal.projection.temporal import (
    TemporalProjectionError,
    after,
    build_temporal_index,
    earliest,
    latest,
    temporal_integrity_check,
)
from semantic_traversal.projection.verification import verify_completed_build
from semantic_traversal.runtime.retrieval.capability_catalog import CapabilityCatalogError, load_capability_catalog
from semantic_traversal.runtime.retrieval.package import RetrievalPackageError, load_retrieval_package
from semantic_traversal.runtime.retrieval.package_verification import RetrievalPackageVerificationError, verify_retrieval_package
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
        admitted = load_capability_catalog(catalog)
        temporal_dimension = next(item for item in admitted.parsed["semantic_dimensions"] if item["field_name"] == "journal_entry_date")
        self.assertEqual([item["operator"] for item in temporal_dimension["access"] if item["operator"].startswith("temporal.")], [
            "temporal.earliest", "temporal.latest", "temporal.before", "temporal.after", "temporal.between", "temporal.ordered",
        ])
        self.assertEqual({name for name, item in admitted.parsed["operators"].items() if item["surface"] == "temporal"}, {
            "temporal.earliest", "temporal.latest", "temporal.before", "temporal.after", "temporal.between", "temporal.ordered",
        })
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

    def test_extrema_scope_their_own_dimension(self):
        with TemporaryDirectory() as directory:
            build = self._build(Path(directory))
            connection = sqlite3.connect(build / "substrate.sqlite3")
            connection.execute(
                "INSERT INTO temporal_index_entries (unit_id, field_class, field_name, domain, date_value) VALUES (?, ?, ?, ?, ?)",
                (1, "semantic_identifier", "other_dimension", "date", "1900-01-01"),
            )
            connection.execute(
                "INSERT INTO temporal_index_entries (unit_id, field_class, field_name, domain, date_value) VALUES (?, ?, ?, ?, ?)",
                (1, "semantic_identifier", "other_dimension", "date", "2999-01-01"),
            )
            connection.commit()
            self.assertEqual([hit.unit_id for hit in earliest(connection)], [1, 2])
            self.assertEqual([hit.unit_id for hit in latest(connection)], [7])
            connection.close()

    def test_temporal_projection_type_fidelity_boundary(self):
        with TemporaryDirectory() as directory:
            build = self._build(Path(directory))
            database = build / "substrate.sqlite3"
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM temporal_index_entries").fetchone()[0], 7)

            connection.execute("UPDATE inherited_identifiers SET state = 'absent', value_json = NULL WHERE unit_id = 1 AND field_name = 'journal_entry_date'")
            connection.commit()
            build_temporal_index(connection)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM temporal_index_entries WHERE unit_id = 1").fetchone()[0], 0)

            connection.execute("UPDATE inherited_identifiers SET state = 'present_blank', value_json = NULL WHERE unit_id = 1 AND field_name = 'journal_entry_date'")
            connection.commit()
            build_temporal_index(connection)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM temporal_index_entries WHERE unit_id = 1").fetchone()[0], 0)

            connection.execute("UPDATE inherited_identifiers SET state = 'present_value', value_json = ? WHERE unit_id = 1 AND field_name = 'journal_entry_date'", (_json_value("2026-04-16"),))
            connection.commit()
            with self.assertRaises(TemporalProjectionError):
                build_temporal_index(connection)
            connection.execute("UPDATE inherited_identifiers SET value_json = ? WHERE unit_id = 1 AND field_name = 'journal_entry_date'", (_json_value(dt.datetime(2026, 4, 16)),))
            connection.commit()
            with self.assertRaises(TemporalProjectionError):
                build_temporal_index(connection)
            connection.close()

    def test_integrity_rejects_each_independent_temporal_mutation(self):
        with TemporaryDirectory() as directory:
            source = self._build(Path(directory))
            mutations = {
                "delete": lambda db: db.execute("DELETE FROM temporal_index_entries WHERE unit_id = 1"),
                "extra": lambda db: db.execute("INSERT INTO temporal_index_entries (unit_id, field_class, field_name, domain, date_value) VALUES (1, 'semantic_identifier', 'journal_entry_date', 'date', '2026-04-14')"),
                "date": lambda db: db.execute("UPDATE temporal_index_entries SET date_value = '2026-01-01' WHERE unit_id = 1"),
                "field": lambda db: db.execute("UPDATE temporal_index_entries SET field_name = 'headspace' WHERE unit_id = 1"),
                "domain": lambda db: db.execute("UPDATE temporal_index_entries SET domain = 'string' WHERE unit_id = 1"),
                "unit": lambda db: db.execute("UPDATE temporal_index_entries SET unit_id = 2 WHERE unit_id = 1"),
            }
            for name, mutate in mutations.items():
                with self.subTest(name=name):
                    target = Path(directory) / name
                    target.mkdir()
                    shutil.copy2(source / "substrate.sqlite3", target / "substrate.sqlite3")
                    shutil.copy2(source / "vectors.npy", target / "vectors.npy")
                    connection = sqlite3.connect(target / "substrate.sqlite3")
                    mutate(connection)
                    connection.commit()
                    self.assertTrue(temporal_integrity_check(connection), name)
                    connection.close()

            target = Path(directory) / "verify-delete"
            target.mkdir()
            shutil.copy2(source / "substrate.sqlite3", target / "substrate.sqlite3")
            shutil.copy2(source / "vectors.npy", target / "vectors.npy")
            connection = sqlite3.connect(target / "substrate.sqlite3")
            connection.execute("DELETE FROM temporal_index_entries WHERE unit_id = 1")
            connection.commit()
            with self.assertRaises(ValueError):
                verify_completed_build(connection, target / "vectors.npy")
            connection.close()

    def test_cli_rejects_non_exact_or_non_calendar_temporal_literals(self):
        with TemporaryDirectory() as directory:
            build = self._build(Path(directory))
            for option, value in (("--anchor", "2026-02-30"), ("--anchor", "2026-04-16T00:00:00"), ("--anchor", "2026-4-16")):
                code, _, _ = self._run("temporal", "before", "--build", str(build), option, value)
                self.assertNotEqual(code, 0)

    def test_package_verification_rejects_temporal_catalog_or_state_disagreement(self):
        with TemporaryDirectory() as directory:
            source = self._build(Path(directory))
            omitted = Path(directory) / "catalog-omitted"
            shutil.copytree(source, omitted)
            catalog_path = omitted / "capability_catalog.json"
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            dimension = next(item for item in catalog["semantic_dimensions"] if item["field_name"] == "journal_entry_date")
            dimension["access"] = [item for item in dimension["access"] if not item["operator"].startswith("temporal.")]
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
            with self.assertRaises((RetrievalPackageError, RetrievalPackageVerificationError)):
                verify_retrieval_package(load_retrieval_package(omitted))

            corrupt = Path(directory) / "state-corrupt"
            shutil.copytree(source, corrupt)
            connection = sqlite3.connect(corrupt / "substrate.sqlite3")
            connection.execute("DELETE FROM temporal_index_entries WHERE unit_id = 1")
            connection.commit()
            connection.close()
            with self.assertRaises(RetrievalPackageVerificationError):
                verify_retrieval_package(load_retrieval_package(corrupt))

    def test_catalog_admission_closes_over_temporal_v1(self):
        with TemporaryDirectory() as directory:
            source = self._build(Path(directory))
            base = json.loads((source / "capability_catalog.json").read_text(encoding="utf-8"))

            def rejects(name, mutate):
                catalog = json.loads(json.dumps(base))
                mutate(catalog)
                path = Path(directory) / f"{name}.json"
                path.write_text(json.dumps(catalog), encoding="utf-8")
                with self.assertRaises(CapabilityCatalogError):
                    load_capability_catalog(path)

            for operator in ("temporal.earliest", "temporal.latest", "temporal.before", "temporal.after", "temporal.between", "temporal.ordered"):
                rejects("missing-" + operator.rsplit(".", 1)[1], lambda catalog, operator=operator: next(item for item in catalog["semantic_dimensions"] if item["field_name"] == "journal_entry_date")["access"].remove(next(access for access in next(item for item in catalog["semantic_dimensions"] if item["field_name"] == "journal_entry_date")["access"] if access["operator"] == operator)))
            temporal_access = {"operator": "temporal.earliest", "target": "complete_value", "domains": ["date"]}
            rejects("headspace", lambda catalog: next(item for item in catalog["semantic_dimensions"] if item["field_name"] == "headspace")["access"].append(temporal_access))
            rejects("parsed-text", lambda catalog: next(item for item in catalog["semantic_dimensions"] if item["field_name"] == "parsed_text")["access"].append(temporal_access))
            rejects("string-domain", lambda catalog: next(access for access in next(item for item in catalog["semantic_dimensions"] if item["field_name"] == "journal_entry_date")["access"] if access["operator"] == "temporal.earliest").update(domains=["string"]))
            rejects("wrong-target", lambda catalog: next(access for access in next(item for item in catalog["semantic_dimensions"] if item["field_name"] == "journal_entry_date")["access"] if access["operator"] == "temporal.earliest").update(target="member"))
            rejects("unknown-global", lambda catalog: catalog["operators"].update({"temporal.unknown": {"surface": "temporal", "meaning": "unknown"}}))
            rejects("missing-global", lambda catalog: catalog["operators"].pop("temporal.ordered"))
            rejects("non-date-model", lambda catalog: next(item for item in catalog["semantic_dimensions"] if item["field_name"] == "journal_entry_date").update(value={"shapes": [{"shape": "scalar", "domains": ["string"]}]}))


if __name__ == "__main__":
    unittest.main()
