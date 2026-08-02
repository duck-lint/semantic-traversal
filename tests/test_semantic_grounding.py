from __future__ import annotations

import unittest

from semantic_traversal.semantic_grounding import (
    build_grounding_spec,
    classify_candidate,
    merge_grounding_assessments,
)


class SemanticGroundingTests(unittest.TestCase):
    def plan(self, **overrides):
        plan = {
            "concepts": ["reading activity"],
            "resolved_referents": ["Alpha", "Beta"],
            "literal_terms": [],
            "semantic_queries": ["reading activity"],
            "lexical_queries": ["reading activity"],
            "graph_seeds": [],
            "evidence_requirements": ["chronology"],
            "retrieval_layers": [{"operator": "temporal_retrieve", "required": True}],
        }
        plan.update(overrides)
        return plan

    def candidate(self, **overrides):
        candidate = {
            "chunk_id": "chunk-1", "note_id": "note-1", "note_title": "Alpha",
            "relative_path": "alpha.md", "section_label": "Body",
            "paragraph_text": "reading activity occurred", "frontmatter_semantics_json": "{}",
            "selection_source": "lexical",
        }
        candidate.update(overrides)
        return candidate

    def test_named_subjects_preserve_order_and_shared_context(self):
        spec = build_grounding_spec(self.plan())
        self.assertEqual([bundle.referent for bundle in spec.bundles], ["Alpha", "Beta"])
        self.assertEqual(len(spec.shared_context_atoms), 3)
        self.assertTrue(all(atom.role == "predicate_only" for atom in spec.bundles[0].shared_predicate_atoms))

    def test_single_subject_context_is_associated_with_subject(self):
        spec = build_grounding_spec(self.plan(resolved_referents=["Alpha"]))
        self.assertEqual([atom.value for atom in spec.bundles[0].supporting_context_atoms], ["reading activity"] * 3)

    def test_subject_spans_are_decomposed_without_shared_literal_predicate(self):
        spec = build_grounding_spec(self.plan(
            concepts=["reading order"],
            semantic_queries=["reading order regarding Alpha Beta"],
            lexical_queries=["Alpha"],
            literal_terms=[{"term": "Beta", "required": True}],
        ))
        atom = next(atom for atom in spec.shared_context_atoms if atom.kind == "semantic_query")
        self.assertEqual(atom.role, "subject_and_predicate")
        self.assertEqual(atom.subject_spans, ("Alpha", "Beta"))
        self.assertEqual(atom.subject_ids, ("subject-0", "subject-1"))
        self.assertEqual(atom.predicate_residual, "reading order regarding")
        self.assertFalse(any(atom.role == "subject_only" for atom in spec.shared_context_atoms))
        self.assertEqual(next(atom for atom in spec.bundles[0].shared_predicate_atoms if atom.kind == "semantic_query").predicate_residual, "reading order regarding")

    def test_subject_only_identity_does_not_satisfy_another_subject(self):
        spec = build_grounding_spec(self.plan(
            concepts=["reading activity"],
            semantic_queries=["Alpha reading activity"],
            lexical_queries=[],
            literal_terms=[{"term": "Beta"}],
        ))
        candidate = self.candidate(note_title="Alpha", paragraph_text="Alpha")
        assessment = classify_candidate(candidate, spec)
        self.assertNotIn("subject-1", assessment["relation_proposition"]["subjects"])
        self.assertFalse(assessment["relation_proposition"]["eligible"])

    def test_resolved_wikilink_promotion_is_direct_target_identity(self):
        candidate = self.candidate(
            note_title="Activity",
            paragraph_text="reading activity",
            wikilink_identity_promotions=[{
                "subject_id": "subject-0", "referent": "Alpha", "target_note_id": "note-alpha",
                "occurrence": {"source_surface": "body", "target_base": "Alpha", "resolution_status": "resolved"},
            }],
        )
        assessment = classify_candidate(candidate, build_grounding_spec(self.plan(resolved_referents=["Alpha"])))
        self.assertEqual(next(item for item in assessment["object_identity"]["evidence"] if item.get("target_note_id"))["target_note_id"], "note-alpha")
        self.assertTrue(assessment["graph_authority"]["may_seed_subject_graph"])
        self.assertEqual(candidate["note_id"], "note-1")

    def test_no_referents_create_query_bundle_without_named_subject(self):
        spec = build_grounding_spec(self.plan(resolved_referents=[]))
        self.assertFalse(spec.named_subjects)
        self.assertEqual(spec.bundles[0].subject_id, "query-0")
        self.assertIsNone(spec.bundles[0].referent)

    def test_title_identity_can_authorize_subject_graph(self):
        assessment = classify_candidate(self.candidate(), build_grounding_spec(self.plan(resolved_referents=["Alpha"])))
        self.assertEqual(assessment["object_identity"]["subjects"], ["subject-0"])
        self.assertTrue(assessment["graph_authority"]["may_seed_subject_graph"])
        self.assertTrue(assessment["relation_proposition"]["eligible"])

    def test_prose_only_match_is_evidence_not_object_identity_or_graph_seed(self):
        candidate = self.candidate(note_title="Unrelated", relative_path="unrelated.md", paragraph_text="Alpha reading activity")
        assessment = classify_candidate(candidate, build_grounding_spec(self.plan(resolved_referents=["Alpha"])))
        self.assertEqual(assessment["object_identity"]["subjects"], [])
        self.assertFalse(assessment["graph_authority"]["may_seed_subject_graph"])
        self.assertTrue(assessment["relation_proposition"]["eligible"])

    def test_referent_only_match_does_not_ground_predicate(self):
        assessment = classify_candidate(
            self.candidate(paragraph_text="Alpha"),
            build_grounding_spec(self.plan(resolved_referents=["Alpha"], concepts=["unrelated predicate"], semantic_queries=["unrelated predicate"], lexical_queries=["unrelated predicate"])),
        )
        self.assertFalse(assessment["relation_proposition"]["eligible"])

    def test_temporal_anchor_is_not_predicate_evidence(self):
        assessment = classify_candidate(
            self.candidate(paragraph_text="Alpha"),
            build_grounding_spec(self.plan(resolved_referents=["Alpha"], concepts=[], semantic_queries=[], lexical_queries=[])),
        )
        self.assertFalse(assessment["relation_proposition"]["eligible"])

    def test_authorized_graph_subjects_can_ground_neighbor_evidence(self):
        candidate = self.candidate(note_title="Activity", relative_path="activity.md", authorized_subjects=["Alpha"])
        assessment = classify_candidate(candidate, build_grounding_spec(self.plan(resolved_referents=["Alpha"])))
        self.assertEqual(assessment["object_identity"]["subjects"], [])
        self.assertEqual(assessment["graph_authority"]["may_propagate_subjects"], ["subject-0"])
        self.assertTrue(assessment["relation_proposition"]["eligible"])

    def test_weak_evidence_remains_evidence_without_proposition_admission(self):
        assessment = classify_candidate(
            self.candidate(note_title="Noise", relative_path="noise.md", paragraph_text="reading"),
            build_grounding_spec(self.plan(resolved_referents=["Alpha"], concepts=["unrelated predicate"], semantic_queries=["unrelated predicate"], lexical_queries=["unrelated predicate"])),
        )
        self.assertFalse(assessment["relation_proposition"]["eligible"])

    def test_merge_preserves_stronger_identity_and_all_routes(self):
        spec = build_grounding_spec(self.plan(resolved_referents=["Alpha"]))
        strong = classify_candidate(self.candidate(), spec)
        weak = classify_candidate(self.candidate(note_title="Noise", relative_path="noise.md"), spec)
        merged = merge_grounding_assessments(weak, strong)
        self.assertTrue(merged["graph_authority"]["may_seed_subject_graph"])
        self.assertTrue(merged["relation_proposition"]["eligible"])


if __name__ == "__main__":
    unittest.main()
