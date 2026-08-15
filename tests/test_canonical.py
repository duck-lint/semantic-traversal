import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from ugh_parser import (
    BuildConfig,
    CanonicalizationError,
    ResolutionFailure,
    canonicalize_ingest,
    materialize_context,
    parse_vault,
    resolve_relations,
)


class CanonicalIngestTests(unittest.TestCase):
    def _write(self, root: Path, relative: str, source: str) -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    def _build(self):
        directory = TemporaryDirectory()
        root = Path(directory.name)
        self._write(root, "source.md", "---\nuuid: source\nrelation: \"[[target#Inner Target|front]]\"\nblank:\n---\npreamble\n# Outer\n## Inner\nbody [[target|same]] [[target|same]]\n# Other\noutside\n")
        self._write(root, "target.md", "---\nuuid: target\n---\n# Inner Target\ntarget body\n")
        config = BuildConfig("test", "uuid", (), ("relation", "blank", "missing"))
        parsed = parse_vault(root, config)
        materialized = materialize_context(parsed)
        resolved = resolve_relations(materialized)
        return directory, root, parsed, materialized, resolved

    def test_valid_resolution_materializes_canonical_ingest(self):
        directory, _, parsed, materialized, resolved = self._build()
        try:
            ingest = canonicalize_ingest(resolved)
            self.assertIs(ingest.parsed_corpus, parsed)
            self.assertIs(ingest.materialized_corpus, materialized)
            self.assertIs(ingest.resolution_result, resolved)
            self.assertEqual(len(ingest.objects), 2)
            self.assertEqual(len(ingest.regions), 4)
            self.assertEqual([unit.unit_id for unit in ingest.units], [1, 2, 3, 4])
            self.assertEqual([(unit.source_path, unit.source_local_order) for unit in ingest.units], [("source.md", 1), ("source.md", 2), ("source.md", 3), ("target.md", 1)])
        finally:
            directory.cleanup()

    def test_ids_are_deterministic_dense_and_not_semantic_identifiers(self):
        first_directory, _, _, _, first_result = self._build()
        second_directory, _, _, _, second_result = self._build()
        try:
            first = canonicalize_ingest(first_result)
            second = canonicalize_ingest(second_result)
            self.assertEqual([unit.unit_id for unit in first.units], [1, 2, 3, 4])
            self.assertEqual([unit.unit_id for unit in first.units], [unit.unit_id for unit in second.units])
            self.assertTrue(all(isinstance(unit.unit_id, int) for unit in first.units))
            self.assertTrue(all(field.name != "uuid" for unit in first.units for field in unit.inherited_identifiers))
        finally:
            first_directory.cleanup()
            second_directory.cleanup()

    def test_region_paths_are_ordered_references_and_unheaded_units_are_empty(self):
        directory, _, _, _, resolved = self._build()
        try:
            ingest = canonicalize_ingest(resolved)
            self.assertEqual(ingest.units[0].region_path, ())
            self.assertEqual([reference.region_path for reference in ingest.units[1].region_path], [("region-0001",), ("region-0001", "region-0002")])
            self.assertEqual([reference.region_path for reference in ingest.units[2].region_path], [("region-0003",)])
            self.assertEqual(ingest.units[1].region_path[0].source_object_uuid, "source")
            self.assertEqual(ingest.regions[1].raw_markdown, "## Inner\n")
            self.assertEqual(ingest.regions[1].parsed_text, "Inner")
            self.assertEqual(ingest.regions[1].address_text, "Inner")
        finally:
            directory.cleanup()

    def test_identifier_states_and_relation_occurrences_are_preserved(self):
        directory, _, _, _, resolved = self._build()
        try:
            ingest = canonicalize_ingest(resolved)
            fields = {field.name: field for field in ingest.units[1].inherited_identifiers}
            self.assertEqual((fields["missing"].state, fields["missing"].value), ("absent", None))
            self.assertEqual((fields["blank"].state, fields["blank"].value), ("present_blank", None))
            self.assertEqual(fields["relation"].value, "[[target#Inner Target|front]]")
            relations = ingest.units[1].relations
            self.assertEqual(len(relations), 3)
            self.assertEqual([(relation.origin, relation.relation_name) for relation in relations], [("frontmatter", "relation"), ("body", "linked_to"), ("body", "linked_to")])
            self.assertEqual(relations[1], relations[2])
            self.assertEqual(relations[0].target_object_uuid, "target")
            self.assertEqual(relations[0].target_region.region_path, ("region-0001",))
            self.assertEqual((relations[0].raw, relations[0].authored_target, relations[0].authored_label, relations[0].authored_region_fragment), ("[[target#Inner Target|front]]", "target", "front", "Inner Target"))
        finally:
            directory.cleanup()

    def test_invalid_resolution_is_rejected_without_partial_ingest(self):
        directory, _, _, materialized, resolved = self._build()
        try:
            invalid = replace(resolved, failures=(ResolutionFailure("unresolved_object", "invalid test result", materialized.units[0].relations[0], "source", "source.md", 1),))
            with self.assertRaises(CanonicalizationError):
                canonicalize_ingest(invalid)
        finally:
            directory.cleanup()

    def test_reordered_relations_across_units_are_rejected(self):
        directory, _, _, _, resolved = self._build()
        try:
            relations = list(resolved.resolved_relations)
            relations[0], relations[1] = relations[1], relations[0]
            reordered = replace(resolved, resolved_relations=tuple(relations))
            with self.assertRaises(CanonicalizationError):
                canonicalize_ingest(reordered)
        finally:
            directory.cleanup()

    def test_reordered_relations_within_a_unit_are_rejected(self):
        directory, _, _, _, resolved = self._build()
        try:
            relations = list(resolved.resolved_relations)
            relations[1], relations[2] = relations[2], relations[1]
            reordered = replace(resolved, resolved_relations=tuple(relations))
            with self.assertRaises(CanonicalizationError):
                canonicalize_ingest(reordered)
        finally:
            directory.cleanup()

    def test_stage_creates_no_persistence_or_retrieval_artifacts(self):
        directory, root, _, _, resolved = self._build()
        try:
            canonicalize_ingest(resolved)
            self.assertEqual(list(root.glob("*.sqlite3")), [])
            self.assertEqual(list(root.glob("*.db")), [])
            self.assertEqual(list(root.glob("*.npy")), [])
            self.assertEqual(list(root.glob("*catalog*")), [])
            self.assertEqual(list(root.glob("*manifest*")), [])
        finally:
            directory.cleanup()


if __name__ == "__main__":
    unittest.main()
