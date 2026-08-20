import inspect
import json
import unittest
from dataclasses import replace

from semantic_traversal.runtime.conversation import Conversation, Message
from semantic_traversal.runtime.synthesis_input import (
    SYNTHESIS_INPUT_CONTRACT_VERSION,
    SynthesisInput,
    SynthesisInputError,
    SynthesisMessage,
    build_synthesis_input,
    serialize_synthesis_input,
    snapshot_synthesis_conversation,
    synthesis_input_json,
    synthesis_input_sha256,
)
from semantic_traversal.runtime.retrieval.evidence_projection import (
    EVIDENCE_PROJECTION_CONTRACT_VERSION,
    project_evidence,
)
from semantic_traversal.runtime.retrieval.candidate_hydration import hydrate_candidate_selection
from tests.test_runtime_candidate_hydration import CandidateHydrationTests


class SynthesisMessageTests(unittest.TestCase):
    def test_valid_message_and_exact_content(self):
        message = SynthesisMessage(0, "user", "  café\n")
        self.assertEqual(message.content, "  café\n")

    def test_valid_prior_synthesis_message(self):
        self.assertEqual(SynthesisMessage(1, "synthesis", "Earlier answer").role, "synthesis")

    def test_invalid_ordinals_roles_and_content(self):
        cases = (
            (True, "user", "x"),
            (-1, "user", "x"),
            (0, "assistant", "x"),
            (0, "user", "   "),
        )
        for ordinal, role, content in cases:
            with self.subTest(ordinal=ordinal, role=role, content=content):
                with self.assertRaises(SynthesisInputError):
                    SynthesisMessage(ordinal, role, content)


class SynthesisInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        CandidateHydrationTests.setUpClass()
        cls.fixture = CandidateHydrationTests()

    @classmethod
    def tearDownClass(cls):
        CandidateHydrationTests.tearDownClass()

    def conversation(self, *messages):
        return Conversation(
            "conversation-id",
            "created",
            tuple(Message(index + 1, "conversation-id", index, role, content, f"created-{index}") for index, (role, content) in enumerate(messages)),
        )

    def evidence(self, candidates=None, requests=()):
        candidates = (self.fixture.unit(1),) if candidates is None else candidates
        selection = self.fixture.selection(candidates, requests=requests)
        return project_evidence(hydrate_candidate_selection(self.fixture.package, selection))

    def project_selection(self, selection):
        return project_evidence(hydrate_candidate_selection(self.fixture.package, selection))

    def test_snapshot_preserves_dialogue_but_drops_lineage(self):
        conversation = self.conversation(
            ("user", "first"),
            ("synthesis", "earlier answer"),
            ("user", "follow-up"),
        )
        snapshot = snapshot_synthesis_conversation(conversation)
        self.assertEqual(snapshot, (
            SynthesisMessage(0, "user", "first"),
            SynthesisMessage(1, "synthesis", "earlier answer"),
            SynthesisMessage(2, "user", "follow-up"),
        ))
        self.assertEqual(set(vars(snapshot[0])), {"ordinal", "role", "content"})
        serialized = serialize_synthesis_input(build_synthesis_input(conversation, "direct", None))
        for value in ("message_id", "conversation_id", "created_at", "created-0"):
            self.assertNotIn(value, serialized)

    def test_empty_gapped_reordered_duplicate_and_latest_synthesis_rejected(self):
        cases = (
            (),
            (SynthesisMessage(1, "user", "x"),),
            (SynthesisMessage(0, "user", "x"), SynthesisMessage(2, "user", "y")),
            (SynthesisMessage(1, "user", "x"), SynthesisMessage(0, "user", "y")),
            (SynthesisMessage(0, "user", "x"), SynthesisMessage(0, "user", "y")),
            (SynthesisMessage(0, "user", "x"), SynthesisMessage(1, "synthesis", "y")),
        )
        for messages in cases:
            with self.subTest(messages=messages):
                with self.assertRaises(SynthesisInputError):
                    SynthesisInput(SYNTHESIS_INPUT_CONTRACT_VERSION, "direct", messages, None)

    def test_route_invariants_and_failed_execution_evidence(self):
        conversation = self.conversation(("user", "question"))
        evidence = self.evidence(requests=({"operator": "exact.equals"},))
        self.assertIs(build_synthesis_input(conversation, "direct", None).evidence, None)
        self.assertIs(build_synthesis_input(conversation, "semantic_retrieval", evidence).evidence, evidence)
        failed = self.project_selection(replace(evidence.source.selection, execution_status="failed"))
        self.assertEqual(build_synthesis_input(conversation, "semantic_retrieval", failed).evidence.coverage.execution_status, "failed")
        for route, supplied in (("direct", evidence), ("semantic_retrieval", None), ("unknown", None)):
            with self.subTest(route=route):
                with self.assertRaises(SynthesisInputError):
                    build_synthesis_input(conversation, route, supplied)
        with self.assertRaises(SynthesisInputError):
            build_synthesis_input(conversation, "semantic_retrieval", replace(evidence, contract_version="other"))

    def test_canonical_serialization_is_structured_deterministic_and_provider_neutral(self):
        value = build_synthesis_input(self.conversation(("user", "café")), "semantic_retrieval", self.evidence())
        first = serialize_synthesis_input(value)
        self.assertEqual(first, serialize_synthesis_input(value))
        parsed = json.loads(first)
        self.assertEqual(set(parsed), {"contract_version", "route", "conversation", "evidence"})
        self.assertEqual(parsed["evidence"]["contract_version"], EVIDENCE_PROJECTION_CONTRACT_VERSION)
        self.assertNotIn('"source"', first)
        self.assertNotIn("HydratedCandidateSelection", first)
        self.assertNotIn("lexical_score", first)
        self.assertNotIn("vector_score", first)
        self.assertIn("café", first)
        source = inspect.getsource(__import__("semantic_traversal.runtime.synthesis_input", fromlist=["SynthesisInput"]))
        self.assertNotIn("openai", source.lower())
        self.assertNotIn("assistant", source.lower())
        self.assertNotIn("ollama", source.lower())

    def test_direct_evidence_is_explicit_null(self):
        parsed = json.loads(serialize_synthesis_input(build_synthesis_input(self.conversation(("user", "question")), "direct", None)))
        self.assertIsNone(parsed["evidence"])

    def test_nonfinite_nested_evidence_fails_closed(self):
        evidence = self.evidence(requests=({"operator": "exact.equals"},))
        malformed = replace(
            evidence,
            requests=(replace(evidence.requests[0], request={"operator": "exact.equals", "bad": float("nan")}),),
        )
        with self.assertRaises(ValueError):
            serialize_synthesis_input(build_synthesis_input(self.conversation(("user", "question")), "semantic_retrieval", malformed))

    def test_exact_count_and_selection_truncation_survive_without_derived_authority(self):
        selection = self.fixture.selection(
            (self.fixture.unit(1), self.fixture.unit(2)),
            requests=( {"operator": "exact.equals"}, ),
        )
        selection = replace(
            selection,
            requests=(replace(selection.requests[0], returned_occurrences=5),),
            coverage=replace(
                selection.coverage,
                workspace_candidate_count=5,
                selected_candidate_count=2,
                omitted_candidate_count=3,
                selection_truncated=True,
            ),
        )
        evidence = self.project_selection(selection)
        parsed = synthesis_input_json(build_synthesis_input(self.conversation(("user", "count")), "semantic_retrieval", evidence))
        self.assertEqual(parsed["evidence"]["requests"][0]["returned_occurrences"], 5)
        self.assertEqual(parsed["evidence"]["coverage"]["selected_candidate_count"], 2)
        self.assertTrue(parsed["evidence"]["coverage"]["selection_truncated"])
        self.assertNotIn("exhaustive_count", json.dumps(parsed))

    def test_failed_and_not_executed_request_statuses_are_preserved(self):
        selection = self.fixture.selection(
            (self.fixture.unit(1),),
            requests=({"operator": "exact.equals"}, {"operator": "lexical.terms"}, {"operator": "vector.semantic_similarity"}),
            execution_status="failed",
            execution_failure={"kind": "surface_error"},
        )
        selection = replace(
            selection,
            requests=(
                replace(selection.requests[0], status="succeeded", returned_occurrences=1),
                replace(selection.requests[1], status="failed", returned_occurrences=None, failure={"kind": "surface_error"}),
                replace(selection.requests[2], status="not_executed", returned_occurrences=None, failure={"kind": "prior_request_failed"}),
            ),
        )
        evidence = self.project_selection(selection)
        parsed = synthesis_input_json(build_synthesis_input(self.conversation(("user", "partial")), "semantic_retrieval", evidence))
        self.assertEqual(parsed["evidence"]["coverage"]["execution_status"], "failed")
        self.assertEqual([item["status"] for item in parsed["evidence"]["requests"]], ["succeeded", "failed", "not_executed"])
        self.assertIsNone(parsed["evidence"]["requests"][1]["returned_occurrences"])
        self.assertIsNone(parsed["evidence"]["requests"][2]["returned_occurrences"])

    def test_hash_is_exact_canonical_content_identity(self):
        base = build_synthesis_input(self.conversation(("user", "question")), "direct", None)
        self.assertRegex(synthesis_input_sha256(base), r"^sha256:[0-9a-f]{64}$")
        import hashlib
        self.assertEqual(
            synthesis_input_sha256(base),
            "sha256:" + hashlib.sha256(serialize_synthesis_input(base).encode("utf-8")).hexdigest(),
        )
        variants = (
            replace(base, route="semantic_retrieval", evidence=self.evidence()),
            build_synthesis_input(self.conversation(("user", "changed")), "direct", None),
            build_synthesis_input(self.conversation(("user", "question"), ("synthesis", "answer"), ("user", "again")), "direct", None),
        )
        self.assertEqual(len({synthesis_input_sha256(item) for item in (base, *variants)}), 4)

    def test_hash_rejects_arbitrary_text_and_non_inputs(self):
        for value in ("{\"route\":\"direct\"}", {}, None, 42):
            with self.subTest(value=value):
                with self.assertRaises(SynthesisInputError):
                    synthesis_input_sha256(value)


if __name__ == "__main__":
    unittest.main()
