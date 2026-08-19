import unittest
from unittest.mock import patch

from semantic_traversal.runtime.config import CandidateSelectionConfig
from semantic_traversal.runtime.retrieval.candidate_hydration import (
    CandidateHydrationError,
    hydrate_candidate_selection,
)
from semantic_traversal.runtime.retrieval.candidates import compose_candidate_workspace
from semantic_traversal.runtime.retrieval.evidence_preparation import prepare_evidence
from semantic_traversal.runtime.retrieval.evidence_projection import project_evidence
from semantic_traversal.runtime.retrieval.execution import (
    EXECUTION_CONTRACT_VERSION,
    RetrievalExecutionRequestResult,
    RetrievalExecutionResult,
)
from semantic_traversal.runtime.retrieval.ownership_topology import (
    CandidateOwnershipTopologyError,
    derive_candidate_ownership_topology,
)
from semantic_traversal.runtime.retrieval.package_verification import VERIFICATION_CONTRACT_VERSION
from semantic_traversal.runtime.retrieval.selection import (
    CandidateSelectionError,
    select_candidates,
)
from tests.test_runtime_candidate_hydration import CandidateHydrationTests


def execution(package, requests, status="succeeded", execution_failure=None):
    identity = package.package.identity
    return RetrievalExecutionResult(
        "execution", "conformance", "run", "proposal", identity.capability_catalog_sha256,
        identity.package_id, "retrieval-package-v1", identity.substrate_sha256,
        identity.vectors_sha256, VERIFICATION_CONTRACT_VERSION, EXECUTION_CONTRACT_VERSION,
        status, "started", "completed", tuple(requests), execution_failure,
    )


def exact_request(unit_ids, ordinal=0):
    return RetrievalExecutionRequestResult(
        ordinal, {"operator": "exact.equals"}, "succeeded",
        {"kind": "exact", "unit_ids": list(unit_ids)}, None,
    )


class EvidencePreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        CandidateHydrationTests.setUpClass()
        cls.package = CandidateHydrationTests.package

    @classmethod
    def tearDownClass(cls):
        CandidateHydrationTests.tearDownClass()

    def exact_execution(self, unit_ids=(1,)):
        return execution(self.package, (exact_request(unit_ids),))

    def test_exact_composition_matches_manual_stage_sequence_structurally(self):
        completed = self.exact_execution((1,))
        policy = CandidateSelectionConfig(120, 0.20)
        prepared = prepare_evidence(self.package, completed, policy)

        workspace = compose_candidate_workspace(completed)
        topology = derive_candidate_ownership_topology(self.package, workspace)
        selection = select_candidates(workspace, topology, policy.max_candidates, policy.protected_owner_fraction)
        hydrated = hydrate_candidate_selection(self.package, selection)
        manual = project_evidence(hydrated)

        self.assertEqual(prepared, manual)

    def test_policy_handoff_reaches_selection_without_coordinator_policy_logic(self):
        request = RetrievalExecutionRequestResult(
            0, {"operator": "vector.semantic_similarity"}, "succeeded",
            {"kind": "vector", "hits": [
                {"target_kind": "semantic_unit", "target_identity": 1, "score": 0.1, "segment_ordinal": 0},
                {"target_kind": "semantic_unit", "target_identity": 2, "score": 0.2, "segment_ordinal": 0},
            ]}, None,
        )
        completed = execution(self.package, (request,))
        prepared = prepare_evidence(self.package, completed, CandidateSelectionConfig(2, 0.50))
        selection = prepared.source.selection
        self.assertEqual((selection.max_candidates, selection.protected_owner_fraction), (2, 0.50))
        self.assertEqual(selection.protected_owner_limit, 1)
        self.assertEqual([item.target_ref.identity for item in selection.selected_candidates], [(1,), (2,)])

    def test_zero_capacity_preserves_request_and_coverage_evidence(self):
        prepared = prepare_evidence(self.package, self.exact_execution((1,)), CandidateSelectionConfig(0, 0.20))
        selection = prepared.source.selection
        self.assertEqual(selection.selected_candidates, ())
        self.assertEqual(prepared.candidates, ())
        self.assertEqual(prepared.requests[0].status, "succeeded")
        self.assertEqual(prepared.coverage.workspace_candidate_count, 1)
        self.assertEqual(prepared.coverage.selected_candidate_count, 0)
        self.assertTrue(prepared.coverage.selection_truncated)

    def test_failed_execution_prefix_is_composed_without_success_requirement(self):
        requests = (
            exact_request((1,)),
            RetrievalExecutionRequestResult(
                1, {"operator": "exact.equals"}, "failed", None,
                {"kind": "surface_error", "surface": "exact.equals", "exception_type": "X", "message": "failed"},
            ),
            RetrievalExecutionRequestResult(
                2, {"operator": "exact.equals"}, "not_executed", None,
                {"kind": "prior_request_failed", "failed_ordinal": 1},
            ),
        )
        completed = execution(self.package, requests, status="failed", execution_failure=None)
        prepared = prepare_evidence(self.package, completed, CandidateSelectionConfig(120, 0.20))
        self.assertEqual(prepared.coverage.execution_status, "failed")
        self.assertEqual([item.target_ref.identity for item in prepared.candidates], [(1,)])
        self.assertEqual([item.status for item in prepared.requests], ["succeeded", "failed", "not_executed"])

    def test_native_stage_errors_are_not_wrapped(self):
        with self.assertRaises(CandidateOwnershipTopologyError):
            prepare_evidence(self.package.package, self.exact_execution(), CandidateSelectionConfig(120, 0.20))
        with self.assertRaises(CandidateSelectionError):
            prepare_evidence(self.package, self.exact_execution(), CandidateSelectionConfig(-1, 0.20))
        with patch(
            "semantic_traversal.runtime.retrieval.evidence_preparation.hydrate_candidate_selection",
            side_effect=CandidateHydrationError("contradiction"),
        ):
            with self.assertRaises(CandidateHydrationError):
                prepare_evidence(self.package, self.exact_execution(), CandidateSelectionConfig(120, 0.20))

    def test_coordinator_preserves_two_operation_local_currentness_checks(self):
        import semantic_traversal.runtime.retrieval.candidate_hydration as hydration_module
        import semantic_traversal.runtime.retrieval.ownership_topology as topology_module
        import semantic_traversal.runtime.retrieval.package_verification as verification_module

        with patch.object(topology_module, "require_current_package_identity", wraps=topology_module.require_current_package_identity) as topology_check, \
             patch.object(hydration_module, "require_current_package_identity", wraps=hydration_module.require_current_package_identity) as hydration_check, \
             patch.object(verification_module, "require_current_package_identity", wraps=verification_module.require_current_package_identity) as verification_check:
            prepare_evidence(self.package, self.exact_execution(), CandidateSelectionConfig(120, 0.20))
        self.assertEqual(topology_check.call_count, 1)
        self.assertEqual(hydration_check.call_count, 1)
        self.assertEqual(verification_check.call_count, 0)


if __name__ == "__main__":
    unittest.main()
