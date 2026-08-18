import datetime as dt
import json
import sqlite3
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from semantic_traversal.cli import main
from semantic_traversal.projection.graph import GraphHandle
from semantic_traversal.runtime.openai_provider import ProviderUsage
from semantic_traversal.runtime.retrieval.candidate_hydration import (
    CANDIDATE_HYDRATION_CONTRACT_VERSION,
    CandidateHydrationError,
    HydratedObjectTarget,
    HydratedRegionTarget,
    HydratedScopeTarget,
    HydratedUnitTarget,
    hydrate_candidate_selection,
)
from semantic_traversal.runtime.retrieval.candidates import (
    Candidate,
    ExactSupport,
    LexicalSupport,
    TemporalSupport,
    VectorSupport,
    CandidateRef,
    RelationEvidence,
)
from semantic_traversal.runtime.retrieval.package import load_retrieval_package
from semantic_traversal.runtime.retrieval.package_verification import verify_retrieval_package
from semantic_traversal.runtime.retrieval.selection import (
    CANDIDATE_SELECTION_CONTRACT_VERSION,
    CandidateAdmission,
    CandidateSelection,
    SelectionCoverage,
    SelectionRequestCoverage,
)
from tests.test_cli import CliProvider


class CandidateHydrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = TemporaryDirectory()
        root = Path(cls.directory.name)
        vault = root / "vault"
        vault.mkdir()
        (vault / "source.md").write_text(
            "---\nuuid: source-uuid\njournal_entry_date: 2026-04-16\nheadspace: future\n---\n"
            "preamble\n# Outer\n## Inner\nbody\n",
            encoding="utf-8",
        )
        config = root / "config.yaml"
        config.write_text(
            "vault_name: hydration\nuuid_field: uuid\nexcluded_folders: []\nsemantic_identifiers:\n"
            "  journal_entry_date:\n    description: authored journal date\n"
            "  headspace:\n    description: authored headspace\n",
            encoding="utf-8",
        )
        build = root / "build"
        with patch("semantic_traversal.cli.OllamaEmbeddingProvider", CliProvider):
            assert main(["build", "--vault", str(vault), "--config", str(config), "--output", str(build)]) == 0
            catalog = build / "capability_catalog.json"
            assert main(["catalog", "generate", "--build", str(build), "--config", str(config), "--output", str(catalog)]) == 0
        cls.package = verify_retrieval_package(load_retrieval_package(build))
        connection = sqlite3.connect(build / "substrate.sqlite3")
        cls.region_path = tuple(json.loads(connection.execute("SELECT region_path_json FROM canonical_regions ORDER BY canonical_ordinal LIMIT 1").fetchone()[0]))
        connection.close()

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def selection(self, candidates, requests=(), relations=(), execution_status="succeeded", execution_failure=None):
        requests = tuple(
            SelectionRequestCoverage(index, request, request["operator"], "succeeded", None, 1)
            for index, request in enumerate(requests)
        )
        refs = tuple(candidate.target_ref for candidate in candidates)
        admissions = tuple(
            CandidateAdmission(index, candidate.target_ref, 0, index, "unary", "ordinary")
            for index, candidate in enumerate(candidates)
        )
        coverage = SelectionCoverage(len(candidates), len(candidates), 0, len(relations), len(relations), 0, len(candidates), False, False)
        identity = self.package.package.identity
        return CandidateSelection(
            CANDIDATE_SELECTION_CONTRACT_VERSION, "candidate-workspace-v1", "execution", "conformance", "retrieval",
            "proposal", identity.capability_catalog_sha256, identity.package_id, "retrieval-package-v1",
            identity.substrate_sha256, identity.vectors_sha256, "retrieval-package-verification-v1", "retrieval-execution-v1",
            execution_status, execution_failure, len(candidates), requests, admissions, tuple(candidates), tuple(relations), (), coverage,
        )

    def unit(self, unit_id=1, supports=()):
        return Candidate(CandidateRef("semantic_unit", (unit_id,)), tuple(supports))

    def object(self):
        return Candidate(CandidateRef("semantic_object", ("source-uuid",)), ())

    def region(self):
        return Candidate(CandidateRef("semantic_region", ("source-uuid", self.region_path)), ())

    def test_unit_object_region_scope_reconstruct_as_typed_targets(self):
        selection = self.selection((self.unit(), self.object(), self.region(), Candidate(CandidateRef("scope", (("vault",),)), ())))
        result = hydrate_candidate_selection(self.package, selection)
        self.assertEqual(result.contract_version, CANDIDATE_HYDRATION_CONTRACT_VERSION)
        self.assertIs(result.selection, selection)
        self.assertIsInstance(result.hydrated_candidates[0].canonical_target, HydratedUnitTarget)
        self.assertIsInstance(result.hydrated_candidates[1].canonical_target, HydratedObjectTarget)
        self.assertIsInstance(result.hydrated_candidates[2].canonical_target, HydratedRegionTarget)
        scope = result.hydrated_candidates[3].canonical_target
        self.assertIsInstance(scope, HydratedScopeTarget)
        self.assertEqual(scope.handle, GraphHandle("scope", (("vault",),)))
        self.assertEqual(result.hydrated_candidates[0].canonical_target.unit.unit_id, 1)
        self.assertEqual(result.hydrated_candidates[0].canonical_target.owning_object.source_object_uuid, "source-uuid")
        self.assertEqual(result.hydrated_candidates[2].canonical_target.region.reference.region_path, self.region_path)

    def test_supports_are_retained_and_each_candidate_hydrates_once(self):
        supports = (
            ExactSupport(0, 0, "exact.equals", 1),
            LexicalSupport(1, 0, "lexical.terms", 1, -1.0),
            TemporalSupport(2, 0, "temporal.before", 1, dt.date(2026, 4, 16)),
            VectorSupport(3, 0, "vector.semantic_similarity", "semantic_unit", 1, 0.5, 0),
        )
        requests = (
            {"operator": "exact.equals"}, {"operator": "lexical.terms"},
            {"operator": "temporal.before", "field_class": "semantic_identifier", "field_name": "journal_entry_date", "target": "complete_value", "anchor": {"domain": "date", "value": "2026-04-16"}},
            {"operator": "vector.semantic_similarity"},
        )
        candidate = self.unit(supports=supports)
        with patch("semantic_traversal.runtime.retrieval.candidate_hydration.hydrate_unit", wraps=__import__("semantic_traversal.projection.substrate", fromlist=["hydrate_unit"]).hydrate_unit) as hydrate_unit:
            result = hydrate_candidate_selection(self.package, self.selection((candidate,), requests=requests))
        self.assertEqual(hydrate_unit.call_count, 1)
        self.assertEqual(result.hydrated_candidates[0].candidate, candidate)
        self.assertEqual(result.hydrated_candidates[0].candidate.supports, supports)

    def test_shared_owner_and_region_caches_read_each_canonical_value_once(self):
        candidates = (self.unit(1), self.unit(1), self.object(), self.region())
        # Duplicate candidates are not a valid selection in normal operation;
        # this direct fixture isolates the operation-local cache contract.
        substrate = __import__("semantic_traversal.projection.substrate", fromlist=["hydrate_object", "hydrate_region", "hydrate_unit"])
        with patch("semantic_traversal.runtime.retrieval.candidate_hydration.hydrate_unit", wraps=substrate.hydrate_unit) as unit, patch("semantic_traversal.runtime.retrieval.candidate_hydration.hydrate_object", wraps=substrate.hydrate_object) as obj, patch("semantic_traversal.runtime.retrieval.candidate_hydration.hydrate_region", wraps=substrate.hydrate_region) as region:
            result = hydrate_candidate_selection(self.package, self.selection(candidates))
        self.assertEqual(len(result.hydrated_candidates), 4)
        self.assertEqual(unit.call_count, 1)
        self.assertEqual(obj.call_count, 1)
        self.assertEqual(region.call_count, 1)

    def test_relation_is_carried_and_does_not_create_hydration_work(self):
        source = self.unit(1)
        target = self.unit(1)
        relation = RelationEvidence(0, 0, "graph.relation_occurrence_lookup", 7, "x", "y", source.target_ref, target.target_ref, GraphHandle("semantic_unit", (1,)), GraphHandle("semantic_unit", (1,)))
        selection = self.selection((source,), relations=(relation,))
        result = hydrate_candidate_selection(self.package, selection)
        self.assertEqual(result.selection.selected_relation_evidence, (relation,))

    def test_relation_only_endpoints_hydrate_normally_without_relation_hydration(self):
        source = self.unit(1)
        target = self.object()
        relation = RelationEvidence(
            0, 0, "graph.relation_occurrence_lookup", 8, "x", "y",
            source.target_ref, target.target_ref,
            GraphHandle("semantic_unit", (1,)), GraphHandle("semantic_object", ("source-uuid",)),
        )
        result = hydrate_candidate_selection(self.package, self.selection((source, target), relations=(relation,)))
        self.assertEqual([type(item.canonical_target).__name__ for item in result.hydrated_candidates], ["HydratedUnitTarget", "HydratedObjectTarget"])
        self.assertEqual(result.selection.selected_relation_evidence, (relation,))

    def test_failed_execution_and_zero_selection_preserve_selection(self):
        failed = self.selection((self.unit(),), execution_status="failed", execution_failure={"kind": "surface_error"})
        self.assertEqual(hydrate_candidate_selection(self.package, failed).selection, failed)
        empty = self.selection(())
        empty_result = hydrate_candidate_selection(self.package, empty)
        self.assertIs(empty_result.selection, empty)
        self.assertEqual(empty_result.hydrated_candidates, ())

    def test_temporal_contradiction_is_all_or_nothing(self):
        support = TemporalSupport(0, 0, "temporal.before", 1, dt.date(2026, 4, 16))
        request = {"operator": "temporal.before", "field_class": "semantic_identifier", "field_name": "journal_entry_date", "target": "complete_value", "anchor": {"domain": "date", "value": "2026-04-16"}}
        candidate = self.unit(supports=(support,))
        substrate = __import__("semantic_traversal.projection.substrate", fromlist=["hydrate_unit"])
        real = substrate.hydrate_unit
        def contradictory(connection, unit_id):
            unit = real(connection, unit_id)
            field = replace(unit.inherited_identifiers[0], value=dt.date(2026, 4, 17))
            return replace(unit, inherited_identifiers=(field, *unit.inherited_identifiers[1:]))
        with patch("semantic_traversal.runtime.retrieval.candidate_hydration.hydrate_unit", side_effect=contradictory):
            with self.assertRaises(CandidateHydrationError):
                hydrate_candidate_selection(self.package, self.selection((candidate,), requests=(request,)))

    def test_lineage_and_verified_package_boundaries_fail_before_canonical_reads(self):
        selection = self.selection((self.unit(),))
        with patch("semantic_traversal.runtime.retrieval.candidate_hydration.hydrate_unit") as hydrate:
            with self.assertRaises(CandidateHydrationError):
                hydrate_candidate_selection(self.package.package, selection)
            mismatched = replace(selection, substrate_sha256="sha256:wrong")
            with self.assertRaises(CandidateHydrationError):
                hydrate_candidate_selection(self.package, mismatched)
            hydrate.assert_not_called()

    def test_order_and_selection_are_unchanged(self):
        candidates = (self.object(), self.unit(), Candidate(CandidateRef("scope", (("z",),)), ()))
        selection = self.selection(candidates)
        result = hydrate_candidate_selection(self.package, selection)
        self.assertEqual(tuple(item.candidate for item in result.hydrated_candidates), candidates)
        self.assertIs(result.selection, selection)

    def test_currentness_and_substrate_session_are_operation_local(self):
        selection = self.selection((self.unit(), self.object(), self.region()))
        package_module = __import__("semantic_traversal.runtime.retrieval.package", fromlist=["require_current_package_identity"])
        with patch("semantic_traversal.runtime.retrieval.candidate_hydration.require_current_package_identity", wraps=package_module.require_current_package_identity) as current, patch("semantic_traversal.runtime.retrieval.candidate_hydration._open_read_only_substrate", wraps=__import__("semantic_traversal.runtime.retrieval.candidate_hydration", fromlist=["_open_read_only_substrate"])._open_read_only_substrate) as opened:
            hydrate_candidate_selection(self.package, selection)
        self.assertEqual(current.call_count, 1)
        self.assertEqual(opened.call_count, 1)


if __name__ == "__main__":
    unittest.main()
