import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ugh_parser import (
    BuildConfig,
    MaterializedCorpus,
    ResolutionError,
    VaultParseResult,
    materialize_context,
    parse_vault,
    resolve_relations,
)
from ugh_parser.vault import CorpusFailure


class ResolutionTests(unittest.TestCase):
    def _write(self, root: Path, relative: str, source: str) -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    def _note(self, uuid: str, body: str, fields: str = "") -> str:
        return f"---\nuuid: {uuid}\n{fields}---\n{body}\n"

    def _resolve(self, files: dict[str, str], fields: tuple[str, ...] = ("aliases", "relation")):
        directory = TemporaryDirectory()
        root = Path(directory.name)
        for relative, source in files.items():
            self._write(root, relative, source)
        config = BuildConfig("test", "uuid", (), fields)
        materialized = materialize_context(parse_vault(root, config))
        return directory, materialized

    def test_exact_path_and_period_preservation(self):
        directory, corpus = self._resolve({
            "source.md": self._note("source", "[[folder/report.v1]]"),
            "folder/report.v1.md": self._note("report", "report"),
            "other/report.v1.md": self._note("other", "other"),
        })
        try:
            result = resolve_relations(corpus)
            self.assertTrue(result.is_valid)
            self.assertEqual(result.resolved_relations[0].target_object_uuid, "report")
        finally:
            directory.cleanup()

    def test_failed_path_does_not_fall_back_to_same_leaf_name(self):
        directory, corpus = self._resolve({
            "source.md": self._note("source", "[[missing/a]]"),
            "one/a.md": self._note("one", "one"),
            "two/a.md": self._note("two", "two"),
        })
        try:
            result = resolve_relations(corpus)
            self.assertEqual([failure.kind for failure in result.failures], ["unresolved_object"])
        finally:
            directory.cleanup()

    def test_unique_name_alias_and_same_uuid_deduplication(self):
        directory, corpus = self._resolve({
            "source.md": self._note("source", "[[unique]] [[alias]] [[Same]]"),
            "unique.md": self._note("unique", "unique"),
            "aliased.md": self._note("aliased", "aliased", "aliases:\n  - alias\n"),
            "same.md": self._note("same", "same", "aliases:\n  - Same\n"),
        })
        try:
            result = resolve_relations(corpus)
            self.assertTrue(result.is_valid)
            self.assertEqual([relation.target_object_uuid for relation in result.resolved_relations], ["unique", "aliased", "same"])
        finally:
            directory.cleanup()

    def test_name_and_alias_addresses_casefold_without_rewriting_authored_values(self):
        directory, corpus = self._resolve({
            "source.md": self._note("source", "[[mission]] [[public]] [[STRASSE]]"),
            "Mission.md": self._note("mission", "mission"),
            "Public.md": self._note("public", "public"),
            "german.md": self._note("german", "german", "aliases:\n  - Straße\n"),
        })
        try:
            result = resolve_relations(corpus)
            self.assertTrue(result.is_valid)
            self.assertEqual([r.target_object_uuid for r in result.resolved_relations], ["mission", "public", "german"])
            self.assertEqual([r.authored_target for r in result.resolved_relations], ["mission", "public", "STRASSE"])
            self.assertEqual([r.raw for r in result.resolved_relations], ["[[mission]]", "[[public]]", "[[STRASSE]]"])
            notes = result.materialized_corpus.parsed_corpus.notes
            mission = next(note for note in notes if note.semantic_object.uuid == "mission")
            german = next(note for note in notes if note.semantic_object.uuid == "german")
            self.assertEqual(mission.semantic_object.authored_path, "Mission.md")
            self.assertEqual(german.semantic_object.frontmatter["aliases"], ["Straße"])
        finally:
            directory.cleanup()

    def test_casefold_collision_is_ambiguous_and_retains_all_candidates(self):
        directory, corpus = self._resolve({
            "source.md": self._note("source", "[[THING]]"),
            "Thing.md": self._note("one", "one"),
            "two.md": self._note("two", "two", "aliases:\n  - thing\n"),
        })
        try:
            result = resolve_relations(corpus)
            self.assertEqual([failure.kind for failure in result.failures], ["ambiguous_object"])
            self.assertEqual([candidate.object_uuid for candidate in result.failures[0].object_candidates], ["one", "two"])
        finally:
            directory.cleanup()

    def test_ambiguous_name_and_alias_report_all_candidates(self):
        directory, corpus = self._resolve({
            "source.md": self._note("source", "[[Thing]] [[common]]"),
            "one/Thing.md": self._note("one", "one", "aliases:\n  - common\n"),
            "two/Thing.md": self._note("two", "two", "aliases:\n  - common\n"),
        })
        try:
            result = resolve_relations(corpus)
            self.assertEqual([failure.kind for failure in result.failures], ["ambiguous_object", "ambiguous_object"])
            self.assertEqual(result.failures[0].object_candidates[0].authored_path, "one/Thing.md")
            self.assertEqual([candidate.object_uuid for candidate in result.failures[1].object_candidates], ["one", "two"])
        finally:
            directory.cleanup()

    def test_region_resolves_only_inside_object_and_retains_context(self):
        directory, corpus = self._resolve({
            "source.md": self._note("source", "[[one#Shared]]"),
            "one.md": self._note("one", "# Shared\ntext"),
            "two.md": self._note("two", "# Shared\ntext"),
        })
        try:
            result = resolve_relations(corpus)
            relation = result.resolved_relations[0]
            self.assertEqual(relation.target_object_uuid, "one")
            self.assertEqual(relation.target_region.region_path, ("region-0001",))
        finally:
            directory.cleanup()

    def test_region_resolves_against_authored_address_text_not_rendered_alias(self):
        directory, corpus = self._resolve({
            "source.md": self._note("source", "[[one#Target Visible]]"),
            "one.md": self._note("one", "# [[Target|Visible]]\ntext"),
        })
        try:
            result = resolve_relations(corpus)
            self.assertTrue(result.is_valid)
            target = result.resolved_relations[0].target_region
            self.assertEqual(target.heading, "Visible")
            self.assertEqual(target.address_text, "Target Visible")
        finally:
            directory.cleanup()

    def test_region_punctuation_fallback_resolves_authored_forms(self):
        directory, corpus = self._resolve({
            "source.md": self._note("source", "[[one#Anti-reification Principle]] [[one#Pillar A Semantic Geometry]] [[one#Pillar B Dynamic Coherence]]"),
            "one.md": self._note("one", "# Anti-reification Principle:\ntext\n# Pillar A | Semantic Geometry\na\n# Pillar B: Dynamic Coherence\nb"),
        })
        try:
            result = resolve_relations(corpus)
            self.assertTrue(result.is_valid)
            self.assertEqual([r.target_region.heading for r in result.resolved_relations], [
                "Anti-reification Principle:", "Pillar A | Semantic Geometry", "Pillar B: Dynamic Coherence"
            ])
        finally:
            directory.cleanup()

    def test_region_address_fallback_is_punctuation_insensitive_and_casefolded(self):
        directory, corpus = self._resolve({
            "source.md": self._note("source", "[[one#Foo Bar]] [[one#foo-bar]]"),
            "one.md": self._note("one", "# Foo-Bar\ntext"),
        })
        try:
            result = resolve_relations(corpus)
            self.assertTrue(result.is_valid)
            self.assertEqual(len(result.resolved_relations), 2)
            self.assertEqual(
                [relation.authored_region_fragment for relation in result.resolved_relations],
                ["Foo Bar", "foo-bar"],
            )
        finally:
            directory.cleanup()

    def test_exact_region_match_precedes_normalized_fallback(self):
        directory, corpus = self._resolve({
            "source.md": self._note("source", "[[one#Foo-Bar]]"),
            "one.md": self._note("one", "# Foo-Bar\nexact\n# Foo Bar\nnormalized"),
        })
        try:
            result = resolve_relations(corpus)
            self.assertTrue(result.is_valid)
            self.assertEqual(result.resolved_relations[0].target_region.heading, "Foo-Bar")
        finally:
            directory.cleanup()

    def test_colliding_normalized_region_keys_are_ambiguous(self):
        directory, corpus = self._resolve({
            "source.md": self._note("source", "[[one#Foo Bar]]"),
            "one.md": self._note("one", "# Foo-Bar\nfirst\n# Foo/Bar\nsecond"),
        })
        try:
            result = resolve_relations(corpus)
            self.assertEqual([failure.kind for failure in result.failures], ["ambiguous_region"])
            self.assertEqual([candidate.heading for candidate in result.failures[0].region_candidates], ["Foo-Bar", "Foo/Bar"])
        finally:
            directory.cleanup()

    def test_unresolved_and_ambiguous_regions_are_aggregated(self):
        directory, corpus = self._resolve({
            "source.md": self._note("source", "[[one#Missing]] [[two#Shared]]"),
            "one.md": self._note("one", "# Present\ntext"),
            "two.md": self._note("two", "# A\n## Shared\na\n# B\n## Shared\nb"),
        })
        try:
            result = resolve_relations(corpus)
            self.assertEqual([failure.kind for failure in result.failures], ["unresolved_region", "ambiguous_region"])
            self.assertEqual(len(result.failures[1].region_candidates), 2)
            self.assertEqual(result.resolved_relations, ())
        finally:
            directory.cleanup()

    def test_frontmatter_and_body_reasons_remain_distinct_after_resolution(self):
        directory, corpus = self._resolve({
            "source.md": self._note("source", "body [[target]]", "relation: \"[[target]]\"\n"),
            "target.md": self._note("target", "target"),
        })
        try:
            result = resolve_relations(corpus)
            self.assertEqual([(r.origin, r.relation_name, r.source_field) for r in result.resolved_relations], [("frontmatter", "relation", "relation"), ("body", "linked_to", None)])
        finally:
            directory.cleanup()

    def test_invalid_corpus_and_unexpected_errors_do_not_get_resolved(self):
        config = BuildConfig("test", "uuid", (), ())
        invalid_parse = VaultParseResult((), (CorpusFailure("parse", "bad", ("bad.md",)),), config)
        invalid_materialized = MaterializedCorpus(invalid_parse, ())
        with self.assertRaises(ResolutionError):
            resolve_relations(invalid_materialized)

        directory, corpus = self._resolve({"source.md": self._note("source", "text")})
        try:
            with patch("ugh_parser.resolve._build_index", side_effect=RuntimeError("implementation defect")):
                with self.assertRaisesRegex(RuntimeError, "implementation defect"):
                    resolve_relations(corpus)
        finally:
            directory.cleanup()

    def test_result_retains_exact_materialized_corpus_and_no_global_unit_id(self):
        directory, corpus = self._resolve({"source.md": self._note("source", "text")})
        try:
            result = resolve_relations(corpus)
            self.assertIs(result.materialized_corpus, corpus)
            self.assertFalse(hasattr(corpus.units[0], "unit_id"))
        finally:
            directory.cleanup()


if __name__ == "__main__":
    unittest.main()
