import datetime as dt
import json
import unittest
from dataclasses import replace

from semantic_traversal.runtime.retrieval.candidate_hydration import hydrate_candidate_selection
from semantic_traversal.runtime.retrieval.candidates import Candidate, CandidateRef, ExactSupport, RelationEvidence, TemporalSupport
from semantic_traversal.runtime.retrieval.evidence_projection import (
    EVIDENCE_PROJECTION_CONTRACT_VERSION,
    EvidenceProjectionError,
    measure_evidence_projection,
    project_evidence,
    serialize_evidence_projection,
)
from semantic_traversal.projection.graph import GraphHandle
from tests.test_runtime_candidate_hydration import CandidateHydrationTests


class EvidenceProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        CandidateHydrationTests.setUpClass()
        cls.fixture = CandidateHydrationTests()

    @classmethod
    def tearDownClass(cls):
        CandidateHydrationTests.tearDownClass()

    def project(self, candidates, requests=(), relations=(), **kwargs):
        selection = self.fixture.selection(candidates, requests=requests, relations=relations, **kwargs)
        hydrated = hydrate_candidate_selection(self.fixture.package, selection)
        return project_evidence(hydrated)

    def test_normalizes_shared_object_context_and_keeps_order(self):
        projection = self.project((self.fixture.unit(1), self.fixture.unit(2), self.fixture.object()))
        self.assertEqual(projection.contract_version, EVIDENCE_PROJECTION_CONTRACT_VERSION)
        self.assertEqual(len(projection.object_contexts), 1)
        self.assertEqual(len(projection.object_contexts[0].semantic_identifiers), 2)
        self.assertEqual([item.target_ref.identity for item in projection.candidates], [(1,), (2,), ("source-uuid",)])
        self.assertNotIn("inherited_identifiers", serialize_evidence_projection(projection))

    def test_parsed_text_is_model_facing_and_raw_markdown_is_not(self):
        projection = self.project((self.fixture.unit(1), self.fixture.region()))
        model = json.loads(serialize_evidence_projection(projection))
        self.assertEqual(model["candidates"][0]["parsed_text"], "preamble")
        self.assertNotIn("raw_markdown", model["candidates"][0])
        self.assertNotIn("raw_markdown", model["candidates"][1])

    def test_identifier_mismatch_hard_fails_before_deduplication(self):
        from unittest.mock import patch
        substrate = __import__("semantic_traversal.projection.substrate", fromlist=["hydrate_unit"])
        real = substrate.hydrate_unit

        def contradictory(connection, unit_id):
            unit = real(connection, unit_id)
            return replace(unit, inherited_identifiers=tuple(reversed(unit.inherited_identifiers)))

        selection = self.fixture.selection((self.fixture.unit(1),))
        with patch("semantic_traversal.runtime.retrieval.candidate_hydration.hydrate_unit", side_effect=contradictory):
            hydrated = hydrate_candidate_selection(self.fixture.package, selection)
        with self.assertRaises(EvidenceProjectionError):
            project_evidence(hydrated)

    def test_scope_has_no_object_context_or_synthetic_text(self):
        scope = Candidate(CandidateRef("scope", (("vault",),)), ())
        projection = self.project((scope,))
        self.assertEqual(projection.object_contexts, ())
        self.assertIsNone(projection.candidates[0].parsed_text)
        self.assertIsNone(projection.candidates[0].object_ref)

    def test_supports_and_relations_preserve_provenance_without_scores(self):
        candidate = self.fixture.unit(1, (ExactSupport(0, 0, "exact.equals", 1), TemporalSupport(1, 0, "temporal.before", 1, dt.date(2026, 4, 16))))
        relation = RelationEvidence(0, 0, "graph.relation_occurrence_lookup", 9, "x", "y", candidate.target_ref, candidate.target_ref, GraphHandle("semantic_unit", (1,)), GraphHandle("semantic_unit", (1,)))
        projection = self.project(
            (candidate,),
            requests=(
                {"operator": "exact.equals", "field_class": "intrinsic", "field_name": "x", "target": "complete_value", "operand": {"shape": "scalar", "domain": "string", "value": "x"}},
                {"operator": "temporal.before", "field_class": "semantic_identifier", "field_name": "journal_entry_date", "target": "complete_value", "anchor": {"domain": "date", "value": "2026-04-17"}},
            ),
            relations=(relation,),
        )
        serialized = serialize_evidence_projection(projection)
        self.assertIn('"surface":"exact"', serialized)
        self.assertIn('"represented_date":{"type":"date","value":"2026-04-16"}', serialized)
        self.assertIn('"edge_id":9', serialized)
        self.assertNotIn("lexical_score", serialized)
        self.assertNotIn("vector_score", serialized)

    def test_exact_size_measurement_is_character_and_utf8_byte_exact(self):
        projection = self.project((self.fixture.unit(1),))
        serialized = serialize_evidence_projection(projection)
        measurement = measure_evidence_projection(projection)
        self.assertEqual(measurement["serialized_characters"], len(serialized))
        self.assertEqual(measurement["serialized_utf8_bytes"], len(serialized.encode("utf-8")))
        self.assertNotIn("token", " ".join(measurement))


if __name__ == "__main__":
    unittest.main()
