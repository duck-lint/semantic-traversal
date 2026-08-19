import datetime as dt
import json
import unittest
from dataclasses import replace

from semantic_traversal.build.canonical import CanonicalObjectRelation, CanonicalRelation
from semantic_traversal.build.parser import Embed
from semantic_traversal.runtime.retrieval.candidate_hydration import (
    CANDIDATE_HYDRATION_CONTRACT_VERSION,
    HydratedCandidate,
    HydratedCandidateSelection,
    HydratedUnitTarget,
    hydrate_candidate_selection,
)
from semantic_traversal.runtime.retrieval.candidates import (
    Candidate,
    CandidateRef,
    ExactSupport,
    GraphDiscoverySupport,
    LexicalSupport,
    RelationEvidence,
    TemporalSupport,
    VectorSupport,
)
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

    def manual_unit_projection(self, candidate, unit=None, owner=None, selection=None):
        if selection is None:
            selection = self.fixture.selection((candidate,))
        if unit is None or owner is None:
            hydrated = hydrate_candidate_selection(self.fixture.package, selection)
            return project_evidence(hydrated)
        hydrated = HydratedCandidateSelection(
            CANDIDATE_HYDRATION_CONTRACT_VERSION,
            selection,
            (HydratedCandidate(candidate, HydratedUnitTarget(unit, owner)),),
        )
        return project_evidence(hydrated)

    def canonical_relation(self, origin="frontmatter", target="target-uuid", local_order=0, name="related"):
        return CanonicalRelation(name, origin, "links", "[[Target]]", target, "Target", None, "source-uuid", "source.md", local_order, target, None)

    def canonical_object_relation(self, target="target-uuid", name="related"):
        return CanonicalObjectRelation(name, "frontmatter", "links", "[[Target]]", target, "Target", None, "source-uuid", "source.md", target, None)

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

    def test_frontmatter_relations_are_normalized_once_and_body_relations_stay_on_units(self):
        hydrated = hydrate_candidate_selection(self.fixture.package, self.fixture.selection((self.fixture.unit(1), self.fixture.unit(2))))
        first = hydrated.hydrated_candidates[0].canonical_target
        second = hydrated.hydrated_candidates[1].canonical_target
        frontmatter = self.canonical_relation()
        body = self.canonical_relation("body", target="body-target", local_order=1, name="body-link")
        owner = replace(first.owning_object, relations=(self.canonical_object_relation(),))
        first_unit = replace(first.unit, relations=(frontmatter, body))
        second_unit = replace(second.unit, relations=(frontmatter,))
        selection = hydrated.selection
        manual = HydratedCandidateSelection(
            CANDIDATE_HYDRATION_CONTRACT_VERSION,
            selection,
            (
                HydratedCandidate(selection.selected_candidates[0], HydratedUnitTarget(first_unit, owner)),
                HydratedCandidate(selection.selected_candidates[1], HydratedUnitTarget(second_unit, owner)),
            ),
        )
        projection = project_evidence(manual)
        self.assertEqual(len(projection.object_contexts[0].relations), 1)
        self.assertEqual(projection.object_contexts[0].relations[0]["origin"], "frontmatter")
        self.assertEqual([relation["origin"] for relation in projection.candidates[0].relations], ["body"])
        self.assertEqual(projection.candidates[1].relations, ())

    def test_frontmatter_relation_contradiction_hard_fails(self):
        hydrated = hydrate_candidate_selection(self.fixture.package, self.fixture.selection((self.fixture.unit(1),)))
        target = hydrated.hydrated_candidates[0].canonical_target
        relation = self.canonical_relation()
        owner = replace(target.owning_object, relations=(self.canonical_object_relation(target="different-target"),))
        unit = replace(target.unit, relations=(relation,))
        manual = replace(hydrated, hydrated_candidates=(HydratedCandidate(hydrated.selection.selected_candidates[0], HydratedUnitTarget(unit, owner)),))
        with self.assertRaises(EvidenceProjectionError):
            project_evidence(manual)

    def test_frontmatter_relation_multiplicity_and_order_survive_once(self):
        hydrated = hydrate_candidate_selection(self.fixture.package, self.fixture.selection((self.fixture.unit(1), self.fixture.unit(2))))
        first = hydrated.hydrated_candidates[0].canonical_target
        second = hydrated.hydrated_candidates[1].canonical_target
        relations = (self.canonical_relation(target="first-target", name="first"), self.canonical_relation(target="second-target", name="second"))
        owner_relations = tuple(CanonicalObjectRelation(item.relation_name, item.origin, item.source_field, item.raw, item.authored_target, item.authored_label, item.authored_region_fragment, item.source_object_uuid, item.source_path, item.target_object_uuid, item.target_region) for item in relations)
        owner = replace(first.owning_object, relations=owner_relations)
        manual = replace(
            hydrated,
            hydrated_candidates=(
                HydratedCandidate(hydrated.selection.selected_candidates[0], HydratedUnitTarget(replace(first.unit, relations=relations), owner)),
                HydratedCandidate(hydrated.selection.selected_candidates[1], HydratedUnitTarget(replace(second.unit, relations=relations), owner)),
            ),
        )
        projection = project_evidence(manual)
        self.assertEqual([item["relation_name"] for item in projection.object_contexts[0].relations], ["first", "second"])
        self.assertEqual(projection.candidates[0].relations, ())
        self.assertEqual(projection.candidates[1].relations, ())

    def test_body_relation_and_embed_are_structured_without_raw_markup(self):
        hydrated = hydrate_candidate_selection(self.fixture.package, self.fixture.selection((self.fixture.unit(1),)))
        target = hydrated.hydrated_candidates[0].canonical_target
        body = self.canonical_relation("body", target="target-uuid", local_order=1, name="body-link")
        unit = replace(target.unit, raw_markdown="*Visible* [[Target|Alias]] ![[Image.png#part|Caption]]", parsed_text="Visible Alias", relations=(body,), embeds=(Embed("![[Image.png#part|Caption]]", "Image.png", "Caption", "part"),))
        manual = replace(hydrated, hydrated_candidates=(HydratedCandidate(hydrated.selection.selected_candidates[0], HydratedUnitTarget(unit, target.owning_object)),))
        model = json.loads(serialize_evidence_projection(project_evidence(manual)))
        candidate = model["candidates"][0]
        self.assertEqual(candidate["parsed_text"], "Visible Alias")
        self.assertEqual(candidate["relations"][0]["origin"], "body")
        self.assertEqual(candidate["embeds"], [{"label": "Caption", "target": "Image.png", "target_region_fragment": "part"}])
        self.assertNotIn("raw_markdown", candidate)
        self.assertNotIn("raw", candidate["relations"][0])

    def test_hydrated_selection_exact_agreement_is_required(self):
        hydrated = hydrate_candidate_selection(self.fixture.package, self.fixture.selection((self.fixture.unit(1), self.fixture.object())))
        swapped = replace(hydrated, hydrated_candidates=tuple(reversed(hydrated.hydrated_candidates)))
        with self.assertRaises(EvidenceProjectionError):
            project_evidence(swapped)
        changed = replace(hydrated.hydrated_candidates[0].candidate, supports=(ExactSupport(0, 99, "exact.equals", 1),))
        replaced = replace(hydrated, hydrated_candidates=(HydratedCandidate(changed, hydrated.hydrated_candidates[0].canonical_target), hydrated.hydrated_candidates[1]))
        with self.assertRaises(EvidenceProjectionError):
            project_evidence(replaced)

    def test_object_owned_relation_origin_is_enforced(self):
        hydrated = hydrate_candidate_selection(self.fixture.package, self.fixture.selection((self.fixture.object(),)))
        target = hydrated.hydrated_candidates[0].canonical_target
        invalid = replace(target.object, relations=(CanonicalObjectRelation("x", "body", "links", "raw", "target", "target", None, "source-uuid", "source.md", "target", None),))
        manual = replace(hydrated, hydrated_candidates=(HydratedCandidate(hydrated.selection.selected_candidates[0], replace(hydrated.hydrated_candidates[0].canonical_target, object=invalid)),))
        with self.assertRaises(EvidenceProjectionError):
            project_evidence(manual)

    def test_relation_endpoint_failure_does_not_emit_half_edge(self):
        candidate = self.fixture.unit(1)
        missing = CandidateRef("semantic_unit", (999,))
        relation = RelationEvidence(0, 0, "graph.relation_occurrence_lookup", 4, "x", "y", candidate.target_ref, missing, GraphHandle("semantic_unit", (1,)), GraphHandle("semantic_unit", (999,)))
        with self.assertRaises(EvidenceProjectionError):
            self.project((candidate,), relations=(relation,))

    def test_score_perturbations_do_not_change_model_facing_bytes(self):
        base = self.fixture.unit(1, (LexicalSupport(0, 0, "lexical.terms", 1, -1.0), VectorSupport(1, 0, "vector.semantic_similarity", "semantic_unit", 1, 0.5, 0), GraphDiscoverySupport(2, 0, "graph.discovery.terms", GraphHandle("semantic_unit", (1,)), 0.2)))
        altered = replace(base, supports=(LexicalSupport(0, 0, "lexical.terms", 1, -9.0), VectorSupport(1, 0, "vector.semantic_similarity", "semantic_unit", 1, 9.5, 0), GraphDiscoverySupport(2, 0, "graph.discovery.terms", GraphHandle("semantic_unit", (1,)), 9.2)))
        requests = ({"operator": "lexical.terms"}, {"operator": "vector.semantic_similarity"}, {"operator": "graph.discovery.terms"})
        self.assertEqual(serialize_evidence_projection(self.project((base,), requests=requests)), serialize_evidence_projection(self.project((altered,), requests=requests)))

    def test_coverage_keeps_truncation_visible_but_omitted_identity_internal(self):
        selection = self.fixture.selection((self.fixture.unit(1),))
        coverage = replace(selection.coverage, workspace_candidate_count=3, omitted_candidate_count=2, selection_truncated=True)
        selection = replace(selection, coverage=coverage, omitted_candidate_refs=(CandidateRef("semantic_unit", (2,)), CandidateRef("semantic_unit", (3,))))
        hydrated = hydrate_candidate_selection(self.fixture.package, selection)
        model = json.loads(serialize_evidence_projection(project_evidence(hydrated)))
        self.assertEqual(model["coverage"]["omitted_candidate_count"], 2)
        self.assertTrue(model["coverage"]["selection_truncated"])
        self.assertNotIn("omitted_candidate_refs", model)
        failed = replace(selection, execution_status="failed", execution_failure={"kind": "surface_error"})
        failed_model = json.loads(serialize_evidence_projection(project_evidence(hydrate_candidate_selection(self.fixture.package, failed))))
        self.assertEqual(failed_model["coverage"]["execution_status"], "failed")
        self.assertEqual(failed_model["coverage"]["execution_failure"], {"kind": "surface_error"})

    def test_region_and_object_projection_do_not_expand_canonical_regions(self):
        region_model = json.loads(serialize_evidence_projection(self.project((self.fixture.region(),))))
        object_model = json.loads(serialize_evidence_projection(self.project((self.fixture.object(),))))
        self.assertEqual(region_model["candidates"][0]["parsed_text"], "Outer")
        self.assertNotIn("regions", region_model["object_contexts"][0])
        self.assertNotIn("Inner", serialize_evidence_projection(project_evidence(hydrate_candidate_selection(self.fixture.package, self.fixture.selection((self.fixture.region(),))))))
        self.assertIsNone(object_model["candidates"][0]["parsed_text"])
        self.assertNotIn("regions", object_model["object_contexts"][0])

    def test_multi_object_contexts_follow_candidate_first_encounter_order(self):
        base = hydrate_candidate_selection(self.fixture.package, self.fixture.selection((self.fixture.unit(1),))).hydrated_candidates[0].canonical_target
        owner_b = replace(base.owning_object, source_object_uuid="object-b", source_path="b.md")
        owner_a = replace(base.owning_object, source_object_uuid="object-a", source_path="a.md")
        unit_b1 = replace(base.unit, unit_id=1, source_object_uuid="object-b", source_path="b.md", inherited_identifiers=owner_b.admitted_identifiers)
        unit_a1 = replace(base.unit, unit_id=2, source_object_uuid="object-a", source_path="a.md", inherited_identifiers=owner_a.admitted_identifiers)
        unit_b2 = replace(base.unit, unit_id=3, source_object_uuid="object-b", source_path="b.md", inherited_identifiers=owner_b.admitted_identifiers)
        candidates = (Candidate(CandidateRef("semantic_unit", (1,)), ()), Candidate(CandidateRef("semantic_unit", (2,)), ()), Candidate(CandidateRef("semantic_unit", (3,)), ()))
        selection = self.fixture.selection(candidates)
        manual = HydratedCandidateSelection(CANDIDATE_HYDRATION_CONTRACT_VERSION, selection, (HydratedCandidate(candidates[0], HydratedUnitTarget(unit_b1, owner_b)), HydratedCandidate(candidates[1], HydratedUnitTarget(unit_a1, owner_a)), HydratedCandidate(candidates[2], HydratedUnitTarget(unit_b2, owner_b))))
        projection = project_evidence(manual)
        self.assertEqual([item.target_ref.identity for item in projection.candidates], [(1,), (2,), (3,)])
        self.assertEqual([item.source_object_uuid for item in projection.object_contexts], ["object-b", "object-a"])

    def test_typed_non_ascii_identifier_and_measurement_are_preserved(self):
        hydrated = hydrate_candidate_selection(self.fixture.package, self.fixture.selection((self.fixture.unit(1),)))
        target = hydrated.hydrated_candidates[0].canonical_target
        identifier = replace(target.owning_object.admitted_identifiers[0], value=dt.date(2026, 4, 16))
        owner = replace(target.owning_object, source_path="café.md", admitted_identifiers=(identifier, *target.owning_object.admitted_identifiers[1:]))
        unit = replace(target.unit, source_path="café.md", inherited_identifiers=owner.admitted_identifiers, parsed_text="café")
        manual = replace(hydrated, hydrated_candidates=(HydratedCandidate(hydrated.selection.selected_candidates[0], HydratedUnitTarget(unit, owner)),))
        projection = project_evidence(manual)
        serialized = serialize_evidence_projection(projection)
        measurement = measure_evidence_projection(projection)
        self.assertIn('"type":"date","value":"2026-04-16"', serialized)
        self.assertIn('"state":"present_value"', serialized)
        self.assertGreater(measurement["serialized_utf8_bytes"], measurement["serialized_characters"])

    def test_projection_is_deterministic(self):
        first = self.project((self.fixture.unit(1), self.fixture.object()))
        second = self.project((self.fixture.unit(1), self.fixture.object()))
        self.assertEqual(first, second)
        self.assertEqual(serialize_evidence_projection(first), serialize_evidence_projection(second))
        self.assertEqual(measure_evidence_projection(first), measure_evidence_projection(second))


if __name__ == "__main__":
    unittest.main()
