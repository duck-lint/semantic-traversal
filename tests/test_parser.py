import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ugh_parser import BuildConfig, NoteParseError, load_build_config, parse_note


ROOT = Path(__file__).parents[1]
CONFIG = load_build_config(ROOT / "docs/build_config_seed.yaml")


class SingleNoteParserTests(unittest.TestCase):
    def test_fixture_has_object_regions_and_markdown_blocks(self):
        parsed = parse_note(
            ROOT / "docs/07_Tuesday.md",
            vault_root=ROOT / "docs",
            build_config=CONFIG,
        )
        self.assertEqual(parsed.semantic_object.uuid, "019d6d8f-8b3c-752d-b2c8-5936367992bf")
        self.assertEqual(len(parsed.regions), 4)
        self.assertEqual(len(parsed.units), 6)
        self.assertEqual(parsed.semantic_object.authored_path, "07_Tuesday.md")
        self.assertEqual(parsed.units[0].region_path, ("region-0001",))
        self.assertEqual(parsed.units[-1].region_path, ("region-0004",))
        self.assertTrue(all(unit.source_object_uuid == parsed.semantic_object.uuid for unit in parsed.units))
        self.assertEqual([region.heading for region in parsed.regions], ["Dream Recall:", "Yesterday Review:", "Daily Intent:", "Freeform Journaling:"])
        self.assertEqual([region.parsed_text for region in parsed.regions], [region.address_text for region in parsed.regions])
        self.assertEqual(parsed.semantic_object.path_hierarchy, ())

    def test_heading_preserves_raw_rendered_and_address_text(self):
        source = """---
uuid: heading-forms
---
### [[3. Layer-2 — Interface|Interface]] Components
#### A) Cash-out & [[Inferential Bridge (Rule)|Inferential Bridge]] Enforcement
#### B) Isomorphic Mappings ([[Form]], without hallucinating [[Content]])
#### E) [[3. Layer-2 — Interface|Ethics]] as [[3. Layer-2 — Interface|Interface]] Constraints ([[Epistemic Golden Rule]] / Post-Perennialism)
"""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "heading-forms.md"
            path.write_text(source, encoding="utf-8")
            parsed = parse_note(path, vault_root=directory, build_config=BuildConfig("test", "uuid", (), ()))
        self.assertEqual(
            [(region.raw_markdown, region.parsed_text, region.address_text) for region in parsed.regions],
            [
                (
                    "### [[3. Layer-2 — Interface|Interface]] Components\n",
                    "Interface Components",
                    "3. Layer-2 — Interface Interface Components",
                ),
                (
                    "#### A) Cash-out & [[Inferential Bridge (Rule)|Inferential Bridge]] Enforcement\n",
                    "A) Cash-out & Inferential Bridge Enforcement",
                    "A) Cash-out & Inferential Bridge (Rule) Inferential Bridge Enforcement",
                ),
                (
                    "#### B) Isomorphic Mappings ([[Form]], without hallucinating [[Content]])\n",
                    "B) Isomorphic Mappings (Form, without hallucinating Content)",
                    "B) Isomorphic Mappings (Form, without hallucinating Content)",
                ),
                (
                    "#### E) [[3. Layer-2 — Interface|Ethics]] as [[3. Layer-2 — Interface|Interface]] Constraints ([[Epistemic Golden Rule]] / Post-Perennialism)\n",
                    "E) Ethics as Interface Constraints (Epistemic Golden Rule / Post-Perennialism)",
                    "E) 3. Layer-2 — Interface Ethics as 3. Layer-2 — Interface Interface Constraints (Epistemic Golden Rule / Post-Perennialism)",
                ),
            ],
        )

    def test_raw_markdown_and_visible_parsed_text_are_distinct(self):
        parsed = parse_note(ROOT / "docs/07_Tuesday.md", vault_root=ROOT / "docs", build_config=CONFIG)
        unit = parsed.units[0]
        self.assertIn("[[mission]]", unit.raw_markdown)
        self.assertIn("mission", unit.parsed_text)
        self.assertNotIn("[[mission]]", unit.parsed_text)
        self.assertEqual(unit.wikilinks[0].target, "mission")
        self.assertEqual(unit.wikilinks[0].label, "mission")
        self.assertIsNone(unit.wikilinks[0].target_region_fragment)

    def test_admitted_absence_and_blank_are_distinct(self):
        config = BuildConfig("test", "uuid", (), ("architect_or_operator", "missing"))
        parsed = parse_note(ROOT / "docs/07_Tuesday.md", vault_root=ROOT / "docs", build_config=config)
        states = {field.name: field.state for field in parsed.semantic_object.admitted_fields}
        self.assertEqual(states["architect_or_operator"], "present_blank")
        self.assertEqual(states["missing"], "absent")

    def test_block_parser_defines_units_for_non_paragraph_blocks(self):
        source = """---\nuuid: test-object\nempty: \"\"\n---\n# Scope\n\n- first\n- second\n\n```python\nprint('third')\n```\n\n| a | b |\n|---|---|\n| c | d |\n"""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "note.md"
            path.write_text(source, encoding="utf-8")
            config = BuildConfig("test", "uuid", (), ("empty",))
            parsed = parse_note(path, vault_root=directory, build_config=config)
        self.assertEqual(len(parsed.units), 3)
        self.assertEqual(parsed.units[0].parsed_text, "first\nsecond")
        self.assertIn("print('third')", parsed.units[1].parsed_text)
        self.assertEqual(parsed.units[2].raw_markdown, "| a | b |\n|---|---|\n| c | d |\n")
        self.assertIn("a", parsed.units[2].parsed_text)
        self.assertEqual(parsed.semantic_object.admitted_fields[0].state, "present_value")

    def test_nested_regions_assign_complete_region_path(self):
        source = "---\nuuid: nested\n---\n# One\n## Two\ntext\n### Three\nmore\n"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "nested.md"
            path.write_text(source, encoding="utf-8")
            parsed = parse_note(path, vault_root=directory, build_config=BuildConfig("test", "uuid", (), ()))
        self.assertEqual([unit.region_path for unit in parsed.units], [("region-0001", "region-0002"), ("region-0001", "region-0002", "region-0003")])

    def test_authored_path_and_scope_hierarchy_are_separate(self):
        source = "---\nuuid: nested\n---\ntext\n"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "scope" / "nested.md"
            path.parent.mkdir()
            path.write_text(source, encoding="utf-8")
            parsed = parse_note(path, vault_root=directory, build_config=BuildConfig("test", "uuid", (), ()))
        self.assertEqual(parsed.semantic_object.authored_path, "scope/nested.md")
        self.assertEqual(parsed.semantic_object.path_hierarchy, ("scope",))
        self.assertEqual(parsed.units[0].authored_path, "scope/nested.md")

    def test_fenced_code_is_one_unit_and_not_parsed_as_markdown(self):
        source = """---\nuuid: fenced\n---\n# Outside\n```markdown\n# Not a region\n\n[[not-a-link]]\n```\n"""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "fenced.md"
            path.write_text(source, encoding="utf-8")
            parsed = parse_note(path, vault_root=directory, build_config=BuildConfig("test", "uuid", (), ()))
        self.assertEqual(len(parsed.regions), 1)
        self.assertEqual(len(parsed.units), 1)
        self.assertEqual(parsed.units[0].wikilinks, ())
        self.assertIn("# Not a region", parsed.units[0].parsed_text)
        self.assertIn("[[not-a-link]]", parsed.units[0].parsed_text)

    def test_callout_is_preserved_as_one_parsed_block_without_extra_semantics(self):
        source = "---\nuuid: callout\n---\n> [!NOTE] Title\n> Body [[topic]]\n"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "callout.md"
            path.write_text(source, encoding="utf-8")
            parsed = parse_note(path, vault_root=directory, build_config=BuildConfig("test", "uuid", (), ()))
        self.assertEqual(len(parsed.units), 1)
        self.assertEqual(len(parsed.regions), 0)
        self.assertEqual(parsed.units[0].wikilinks[0].target, "topic")
        self.assertEqual(parsed.units[0].parsed_text, "Title\nBody topic")

    def test_build_config_drives_uuid_and_admitted_fields(self):
        source = "---\nidentity: configured-id\nkept: value\n---\ntext\n"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "configured.md"
            path.write_text(source, encoding="utf-8")
            config = BuildConfig("test", "identity", (), ("kept", "absent_field"))
            parsed = parse_note(path, vault_root=directory, build_config=config)
        self.assertEqual(parsed.semantic_object.uuid, "configured-id")
        self.assertEqual([field.name for field in parsed.semantic_object.admitted_fields], ["kept", "absent_field"])
        self.assertEqual(parsed.semantic_object.admitted_fields[1].state, "absent")

    def test_units_use_local_order_not_ingest_global_ids(self):
        parsed = parse_note(ROOT / "docs/07_Tuesday.md", vault_root=ROOT / "docs", build_config=CONFIG)
        self.assertEqual([unit.local_order for unit in parsed.units], list(range(1, 7)))
        self.assertFalse(hasattr(parsed.units[0], "unit_id"))

    def test_embeds_are_distinct_and_preserve_target_fragment(self):
        source = "---\nuuid: embeds\n---\nordinary [[note#Region|label]] and ![[assets/image.png]] plus ![[note#Section|shown]]\n"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "embeds.md"
            path.write_text(source, encoding="utf-8")
            parsed = parse_note(path, vault_root=directory, build_config=BuildConfig("test", "uuid", (), ()))
        unit = parsed.units[0]
        self.assertEqual([(link.target, link.label, link.target_region_fragment) for link in unit.wikilinks], [("note", "label", "Region")])
        self.assertEqual(
            [(embed.raw, embed.target, embed.label, embed.target_region_fragment) for embed in unit.embeds],
            [("![[assets/image.png]]", "assets/image.png", "assets/image.png", None), ("![[note#Section|shown]]", "note", "shown", "Section")],
        )
        self.assertNotIn("assets/image.png", unit.parsed_text)
        self.assertNotIn("Section", unit.parsed_text)

    def test_wikilink_region_fragment_is_structured_without_resolution(self):
        source = "---\nuuid: links\n---\n[[Object#Region]] and [[Object#Other|visible]]\n"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "links.md"
            path.write_text(source, encoding="utf-8")
            parsed = parse_note(path, vault_root=directory, build_config=BuildConfig("test", "uuid", (), ()))
        self.assertEqual(
            [(link.target, link.label, link.target_region_fragment) for link in parsed.units[0].wikilinks],
            [("Object", "Object", "Region"), ("Object", "visible", "Other")],
        )

    def test_config_rejects_uuid_field_as_semantic_identifier(self):
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "invalid.yaml"
            config_path.write_text("vault_name: test\nuuid_field: identity\nsemantic_identifier_fields:\n  - identity\n", encoding="utf-8")
            with self.assertRaises(NoteParseError):
                load_build_config(config_path)


if __name__ == "__main__":
    unittest.main()
