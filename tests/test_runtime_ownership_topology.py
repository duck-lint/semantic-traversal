import dataclasses
import unittest

from semantic_traversal.runtime.retrieval.candidates import compose_candidate_workspace
from semantic_traversal.runtime.retrieval.execution import (
    EXECUTION_CONTRACT_VERSION, RetrievalExecutionRequestResult, RetrievalExecutionResult,
)
from semantic_traversal.runtime.retrieval.ownership_topology import (
    CANDIDATE_OWNERSHIP_TOPOLOGY_CONTRACT_VERSION, CandidateOwnershipTopologyError,
    derive_candidate_ownership_topology, serialize_candidate_ownership_topology,
)
from semantic_traversal.runtime.retrieval.package import RetrievalPackage
from semantic_traversal.runtime.retrieval.package_verification import VerifiedRetrievalPackage
from tests.test_runtime_candidate_hydration import CandidateHydrationTests


def exact_workspace(unit_ids=(1,)):
    execution = RetrievalExecutionResult(
        "execution", "conformance", "run", "proposal", "catalog", "package", "v1",
        "substrate", "vectors", "verification", EXECUTION_CONTRACT_VERSION, "succeeded",
        "started", "completed", (
            RetrievalExecutionRequestResult(
                0, {"operator": "exact.equals"}, "succeeded",
                {"kind": "exact", "unit_ids": list(unit_ids)}, None,
            ),
        ), None,
    )
    return compose_candidate_workspace(execution)


class CandidateOwnershipTopologyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        CandidateHydrationTests.setUpClass()
        cls.package = CandidateHydrationTests.package

    @classmethod
    def tearDownClass(cls):
        CandidateHydrationTests.tearDownClass()

    def workspace(self, candidates):
        current = exact_workspace(tuple(candidates))
        identity = self.package.package.identity
        return dataclasses.replace(
            current,
            retrieval_package_id=identity.package_id,
            retrieval_package_identity_version="retrieval-package-v1",
            substrate_sha256=identity.substrate_sha256,
            vectors_sha256=identity.vectors_sha256,
            capability_catalog_sha256=identity.capability_catalog_sha256,
            package_verification_contract_version="retrieval-package-verification-v1",
        )

    def test_all_target_kinds_resolve_in_workspace_order(self):
        current = self.workspace((1, 2))
        # The fixture's canonical object and region are added through the
        # hydration fixture, while the exact workspace supplies real units.
        from semantic_traversal.runtime.retrieval.candidates import Candidate, CandidateRef
        region_path = CandidateHydrationTests.region_path
        current = dataclasses.replace(current, candidates=(
            current.candidates[0],
            Candidate(CandidateRef("semantic_object", ("source-uuid",)), ()),
            Candidate(CandidateRef("semantic_region", ("source-uuid", region_path)), ()),
            Candidate(CandidateRef("scope", (("vault",),)), ()),
        ))
        topology = derive_candidate_ownership_topology(self.package, current)
        self.assertEqual(topology.contract_version, CANDIDATE_OWNERSHIP_TOPOLOGY_CONTRACT_VERSION)
        self.assertEqual([entry.owner_object_uuid for entry in topology.entries], ["source-uuid", "source-uuid", "source-uuid", None])

    def test_zero_candidates_are_valid_and_deterministic(self):
        current = self.workspace(())
        first = derive_candidate_ownership_topology(self.package, current)
        second = derive_candidate_ownership_topology(self.package, current)
        self.assertEqual(first.entries, ())
        self.assertEqual(first, second)
        self.assertEqual(serialize_candidate_ownership_topology(first), serialize_candidate_ownership_topology(second))

    def test_missing_canonical_targets_fail_closed(self):
        from semantic_traversal.runtime.retrieval.candidates import Candidate, CandidateRef
        for ref in (
            CandidateRef("semantic_unit", (999999,)),
            CandidateRef("semantic_object", ("missing",)),
            CandidateRef("semantic_region", ("missing", ("Outer",))),
        ):
            current = self.workspace((1,))
            current = dataclasses.replace(current, candidates=(Candidate(ref, ()),))
            with self.subTest(ref=ref), self.assertRaises(CandidateOwnershipTopologyError):
                derive_candidate_ownership_topology(self.package, current)

    def test_bare_package_and_lineage_mismatch_are_rejected(self):
        current = self.workspace((1,))
        with self.assertRaises(CandidateOwnershipTopologyError):
            derive_candidate_ownership_topology(self.package.package, current)
        wrong = dataclasses.replace(current, retrieval_package_id="wrong")
        with self.assertRaises(CandidateOwnershipTopologyError):
            derive_candidate_ownership_topology(self.package, wrong)

    def test_topology_is_immutable_and_currentness_is_checked(self):
        current = self.workspace((1,))
        topology = derive_candidate_ownership_topology(self.package, current)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            topology.entries = ()
        bad_package = dataclasses.replace(self.package.package, substrate_path=self.package.package.vectors_path)
        with self.assertRaises(CandidateOwnershipTopologyError):
            derive_candidate_ownership_topology(VerifiedRetrievalPackage(bad_package), current)


if __name__ == "__main__":
    unittest.main()
