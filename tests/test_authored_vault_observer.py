from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from tools.authored_vault_observer import SCHEMA_VERSION, _frontmatter, _json_safe, _shape, observe


SOURCE = """---
uuid: 019bc983-5620-7f08-9626-474a1e548272
aliases: [Shared]
new_field: {nested: true}
related: [[Target]]
---
# Heading

[[Target#Heading]] [[Target#^block-id]] [[Missing]] [[Alias|Shown]] [[Target\\|Shown]] [[Target#Heading\\|Shown]] [[Target#^block-id\\|Shown]] ![[Target]] [[Shared]]

This authored paragraph is intentionally larger than two thousand characters and must remain whole. """ + ("x" * 2200) + "\n\n> quoted material\n\n- list item\n\n| a | b |\n|---|---|\n| c | d |\n\n```python\nprint('code')\n```\n"


class AuthoredVaultObserverTests(unittest.TestCase):
    def test_schema_version_and_parser_native_date_family_fidelity(self):
        self.assertEqual(SCHEMA_VERSION, "vault-observation/v3")

        frontmatter = _frontmatter("""---
date_value: 2030-01-02
datetime_value: 2030-01-02 03:04:05
quoted_date: "2030-01-02"
month_day: "--12-31"
approximate_year: "~250 BCE"
null_value: null
boolean_value: true
number_value: 7
array_value: [one, two]
mapping_value: {nested: value}
nested_values:
  - 2030-01-02
  - when: 2030-01-02 03:04:05+02:00
---
body
""")

        self.assertEqual(frontmatter["value_shapes"], {
            "approximate_year": "string",
            "array_value": "array",
            "boolean_value": "boolean",
            "date_value": "date",
            "datetime_value": "datetime",
            "mapping_value": "mapping",
            "month_day": "string",
            "nested_values": "array",
            "null_value": "null",
            "number_value": "number",
            "quoted_date": "string",
        })
        self.assertEqual(frontmatter["values"]["date_value"], "2030-01-02")
        self.assertEqual(frontmatter["values"]["datetime_value"], "2030-01-02T03:04:05")
        self.assertEqual(frontmatter["values"]["quoted_date"], "2030-01-02")
        self.assertEqual(frontmatter["values"]["month_day"], "--12-31")
        self.assertEqual(frontmatter["values"]["approximate_year"], "~250 BCE")
        self.assertEqual(frontmatter["values"]["nested_values"], [
            "2030-01-02",
            {"when": "2030-01-02T03:04:05+02:00"},
        ])
        serialized = json.dumps(frontmatter, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        self.assertEqual(serialized, json.dumps(frontmatter, sort_keys=True, ensure_ascii=False, separators=(",", ":")))

    def test_datetime_is_classified_before_date(self):
        self.assertIsInstance(datetime(2030, 1, 2), date)
        self.assertEqual(_shape(datetime(2030, 1, 2, 3, 4, 5)), "datetime")
        self.assertEqual(_shape(date(2030, 1, 2)), "date")

    def test_json_safe_recurses_through_date_family_values(self):
        value = {"items": [date(2030, 1, 2), {"when": datetime(2030, 1, 2, 3, 4, 5)}]}
        self.assertEqual(_json_safe(value), {"items": ["2030-01-02", {"when": "2030-01-02T03:04:05"}]})
        json.dumps(_json_safe(value), sort_keys=True)

    def make_vault(self, root: Path) -> None:
        (root / ".git" / "objects").mkdir(parents=True)
        (root / ".git" / "objects" / "fake" ).write_bytes(b"git state")
        (root / ".semantic-traversal").mkdir()
        (root / ".semantic-traversal" / "state.db").write_bytes(b"generated")
        (root / ".obsidian").mkdir()
        (root / ".obsidian" / "app.json").write_text('{"app": true}', encoding="utf-8")
        (root / "empty-dir").mkdir()
        (root / "VAULT DESIGN" / "nested").mkdir(parents=True)
        (root / "INBOX").mkdir()
        (root / "Target.md").write_text("---\nuuid: 019bc983-a82b-70cd-b775-adcc3b323251\naliases: [Alias, Shared]\n---\n# Heading\n\nparagraph ^block-id\n", encoding="utf-8")
        (root / "Other" / "Target.md").parent.mkdir()
        (root / "Other" / "Target.md").write_text("---\nuuid: 019f99e6-b52c-7082-8913-e57ef42c5027\naliases: [Shared]\n---\nother\n", encoding="utf-8")
        with (root / "Source.md").open("w", encoding="utf-8", newline="") as source_file:
            source_file.write(SOURCE)
        (root / "OtherVersion.md").write_text("---\nuuid: 11111111-1111-4111-8111-111111111111\n---\nbody\n", encoding="utf-8")
        (root / "MalformedUuid.md").write_text("---\nuuid: not-a-uuid\n---\nbody\n", encoding="utf-8")
        (root / "MissingUuid.md").write_text("---\nnew_field: value\n---\n", encoding="utf-8")
        (root / "MalformedYaml.md").write_text("---\nkey: [not closed\n---\nbody\n", encoding="utf-8")
        (root / "VAULT DESIGN" / "Design.md").write_text("# design\n", encoding="utf-8")
        (root / "INBOX" / "Inbox.md").write_text("# inbox\n", encoding="utf-8")
        (root / "attachment.pdf").write_bytes(b"pdf")
        (root / "image.png").write_bytes(b"png")
        (root / "Diagram.PNG").write_bytes(b"png")
        (root / "Concept of Time (The).pdf").write_bytes(b"pdf")

    def run_observer(self, root: Path, output: Path):
        return observe(root, output)

    def test_shows_apparatus_topology_authored_structure_and_ugly_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, output = Path(tmp) / "vault", Path(tmp) / "out"
            root.mkdir()
            self.make_vault(root)
            observation, summary = self.run_observer(root, output)
            self.assertEqual({item["relative_root_path"] for item in observation["technical_apparatus_observations"]}, {".git", ".obsidian", ".semantic-traversal"})
            self.assertIn("empty-dir", {item["relative_path"] for item in observation["directory_observations"]})
            resident_paths = {item["relative_path"] for item in observation["file_observations"]}
            self.assertIn("VAULT DESIGN/Design.md", resident_paths)
            self.assertIn("INBOX/Inbox.md", resident_paths)
            records = {item["source"]["relative_path"]: item for item in observation["markdown_observations"]}
            self.assertEqual(records["Source.md"]["raw_markdown"], SOURCE)
            self.assertEqual(records["MalformedYaml.md"]["frontmatter"]["status"], "malformed")
            self.assertEqual(records["MissingUuid.md"]["uuid"]["parse_status"], "absent")
            self.assertEqual(records["MalformedUuid.md"]["uuid"]["parse_status"], "not_parseable")
            self.assertEqual(records["Source.md"]["uuid"]["parsed_version"], 7)
            self.assertEqual(records["OtherVersion.md"]["uuid"]["parsed_version"], 4)
            long_blocks = [block for block in records["Source.md"]["block_candidates"] if len(block["raw_markdown"]) > 2200]
            self.assertEqual(len(long_blocks), 1)
            self.assertEqual(SOURCE[long_blocks[0]["source_span"][0]:long_blocks[0]["source_span"][1]], long_blocks[0]["raw_markdown"])
            self.assertIn("table", summary["authored_block_kind_counts"])
            self.assertIn("code_fence", summary["authored_block_kind_counts"])

    def test_uuid_duplicates_and_link_provenance_are_independent_of_admission(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, output = Path(tmp) / "vault", Path(tmp) / "out"
            root.mkdir(); self.make_vault(root)
            (root / "DuplicateV7.md").write_text("---\nuuid: 019bc983-5620-7f08-9626-474a1e548272\n---\n", encoding="utf-8")
            observation, summary = self.run_observer(root, output)
            records = {item["source"]["relative_path"]: item for item in observation["markdown_observations"]}
            self.assertEqual(records["Source.md"]["uuid"]["occurrence_source_paths"], ["DuplicateV7.md", "Source.md"])
            self.assertEqual(records["Source.md"]["uuid"]["duplicate_source_paths"], ["DuplicateV7.md", "Source.md"])
            self.assertEqual(summary["duplicate_uuid_group_count"], 1)
            links = records["Source.md"]["authored_links"]
            self.assertTrue(any(link["source_surface"] == "frontmatter" and link["frontmatter_key_path"] == "related" for link in links))
            self.assertTrue(any(link["source_surface"] == "body" for link in links))
            self.assertTrue(any(link["display_alias"] == "Shown" for link in links))

            escaped = next(link for link in links if link["raw_link_markup"] == "[[Target\\|Shown]]")
            self.assertEqual(escaped["raw_target"], "Target")
            self.assertEqual(escaped["display_alias"], "Shown")
            escaped_heading = next(link for link in links if link["raw_link_markup"] == "[[Target#Heading\\|Shown]]")
            self.assertEqual(escaped_heading["heading_fragment"], "Heading")
            escaped_block = next(link for link in links if link["raw_link_markup"] == "[[Target#^block-id\\|Shown]]")
            self.assertEqual(escaped_block["block_fragment"], "block-id")

    def test_uuid_singletons_and_duplicate_pair_have_truthful_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, output = Path(tmp) / "vault", Path(tmp) / "out"
            root.mkdir()
            (root / "Unique.md").write_text("---\nuuid: 22222222-2222-4222-8222-222222222222\n---\nunique\n", encoding="utf-8")
            (root / "DuplicateA.md").write_text("---\nuuid: 33333333-3333-4333-8333-333333333333\n---\nduplicate a\n", encoding="utf-8")
            (root / "DuplicateB.md").write_text("---\nuuid: 33333333-3333-4333-8333-333333333333\n---\nduplicate b\n", encoding="utf-8")
            observation, summary = observe(root, output)
            records = {item["source"]["relative_path"]: item for item in observation["markdown_observations"]}
            self.assertEqual(records["Unique.md"]["uuid"]["occurrence_source_paths"], ["Unique.md"])
            self.assertEqual(records["Unique.md"]["uuid"]["duplicate_source_paths"], [])
            self.assertEqual(records["DuplicateA.md"]["uuid"]["occurrence_source_paths"], ["DuplicateA.md", "DuplicateB.md"])
            self.assertEqual(records["DuplicateB.md"]["uuid"]["duplicate_source_paths"], ["DuplicateA.md", "DuplicateB.md"])
            self.assertEqual(summary["duplicate_uuid_group_count"], 1)

    def test_candidate_surfaces_are_union_with_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, output = Path(tmp) / "vault", Path(tmp) / "out"
            root.mkdir()
            (root / "Target.md").write_text("---\nuuid: 44444444-4444-4444-8444-444444444444\n---\n", encoding="utf-8")
            (root / "AliasCarrier.md").write_text("---\nuuid: 55555555-5555-4555-8555-555555555555\naliases: [Target]\n---\n", encoding="utf-8")
            (root / "Source.md").write_text("[[Target]]\n", encoding="utf-8")
            observation, _ = observe(root, output)
            source = next(item for item in observation["markdown_observations"] if item["source"]["relative_path"] == "Source.md")
            link = source["authored_links"][0]
            self.assertEqual(link["target_candidates"]["cardinality"], "multiple_candidates")
            self.assertEqual(link["target_candidates"]["candidate_source_paths"], ["AliasCarrier.md", "Target.md"])
            evidence = {item["source_path"]: item["surfaces"] for item in link["target_candidates"]["candidate_evidence"]}
            self.assertEqual(evidence["Target.md"], ["basename_stem", "basename_stem_casefold", "exact_relative_path_without_extension", "exact_relative_path_without_extension_casefold"])
            self.assertEqual(evidence["AliasCarrier.md"], ["authored_alias", "authored_alias_casefold"])

    def test_casefolded_markdown_address_evidence_preserves_authored_spelling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, output = Path(tmp) / "vault", Path(tmp) / "out"
            root.mkdir()
            (root / "Time.md").write_text("# time\n", encoding="utf-8")
            (root / "peter.md").write_text("# peter\n", encoding="utf-8")
            (root / "Source.md").write_text("[[time]] [[Peter]]\n", encoding="utf-8")
            observation, _ = observe(root, output)
            source = next(item for item in observation["markdown_observations"] if item["source"]["relative_path"] == "Source.md")
            time_link, peter_link = source["authored_links"]
            self.assertEqual(time_link["raw_target_without_fragment"], "time")
            self.assertEqual(time_link["target_candidates"]["candidate_source_paths"], ["Time.md"])
            self.assertIn("basename_stem_casefold", time_link["target_candidates"]["candidate_evidence"][0]["surfaces"])
            self.assertEqual(peter_link["raw_target_without_fragment"], "Peter")
            self.assertEqual(peter_link["target_candidates"]["candidate_source_paths"], ["peter.md"])
            self.assertIn("basename_stem_casefold", peter_link["target_candidates"]["candidate_evidence"][0]["surfaces"])

    def test_casefolding_expands_candidates_and_preserves_exact_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, output = Path(tmp) / "vault", Path(tmp) / "out"
            root.mkdir()
            (root / "Upper" / "Time.md").parent.mkdir()
            (root / "Lower" / "time.md").parent.mkdir()
            (root / "Upper" / "Time.md").write_text("time\n", encoding="utf-8")
            (root / "Lower" / "time.md").write_text("lowercase time\n", encoding="utf-8")
            (root / "Source.md").write_text("[[Time]]\n", encoding="utf-8")
            observation, _ = observe(root, output)
            source = next(item for item in observation["markdown_observations"] if item["source"]["relative_path"] == "Source.md")
            link = source["authored_links"][0]
            self.assertEqual(link["target_candidates"]["cardinality"], "multiple_candidates")
            self.assertEqual(link["target_candidates"]["candidate_source_paths"], ["Lower/time.md", "Upper/Time.md"])
            evidence = {item["source_path"]: item["surfaces"] for item in link["target_candidates"]["candidate_evidence"]}
            self.assertIn("basename_stem", evidence["Upper/Time.md"])
            self.assertIn("basename_stem_casefold", evidence["Lower/time.md"])

    def test_non_markdown_files_are_resident_address_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, output = Path(tmp) / "vault", Path(tmp) / "out"
            root.mkdir()
            (root / "image.png").write_bytes(b"png")
            (root / "assets").mkdir()
            (root / "assets" / "nested.png").write_bytes(b"nested")
            (root / "Diagram.PNG").write_bytes(b"diagram")
            (root / "Concept of Time (The).pdf").write_bytes(b"pdf")
            (root / "Source.md").write_text("![[image.png]] ![[assets/nested.png]] ![[diagram.png]] ![[Concept of Time (The).pdf]]\n", encoding="utf-8")
            observation, _ = observe(root, output)
            source = next(item for item in observation["markdown_observations"] if item["source"]["relative_path"] == "Source.md")
            image, nested, diagram, pdf = source["authored_links"]
            self.assertEqual(image["raw_target_without_fragment"], "image.png")
            self.assertEqual(image["target_candidates"]["candidate_source_paths"], ["image.png"])
            self.assertIn("resident_basename", image["target_candidates"]["candidate_evidence"][0]["surfaces"])
            self.assertEqual(nested["target_candidates"]["candidate_source_paths"], ["assets/nested.png"])
            self.assertIn("exact_relative_path", nested["target_candidates"]["candidate_evidence"][0]["surfaces"])
            self.assertEqual(diagram["target_candidates"]["candidate_source_paths"], ["Diagram.PNG"])
            self.assertIn("resident_basename_casefold", diagram["target_candidates"]["candidate_evidence"][0]["surfaces"])
            self.assertEqual(pdf["raw_link_markup"], "![[Concept of Time (The).pdf]]")
            self.assertEqual(pdf["target_candidates"]["candidate_source_paths"], ["Concept of Time (The).pdf"])

    def test_fragment_evaluation_has_explicit_applicability_and_candidate_cardinality(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, output = Path(tmp) / "vault", Path(tmp) / "out"
            root.mkdir(); self.make_vault(root)
            observation, _ = self.run_observer(root, output)
            source = next(item for item in observation["markdown_observations"] if item["source"]["relative_path"] == "Source.md")
            links = source["authored_links"]
            plain = next(link for link in links if link["raw_target_without_fragment"] == "Target" and link["heading_fragment"] is None and not link["embedded"])
            self.assertEqual(plain["heading_target_evaluation"], "not_applicable")
            self.assertEqual(plain["block_target_evaluation"], "not_applicable")
            heading = next(link for link in links if link["heading_fragment"] == "Heading")
            self.assertEqual(heading["target_candidates"]["cardinality"], "multiple_candidates")
            self.assertEqual(heading["heading_target_evaluation"], "not_evaluable_parent_unresolved")
            ambiguous = next(link for link in links if link["raw_target_without_fragment"] == "Shared")
            self.assertEqual(ambiguous["target_candidates"]["cardinality"], "multiple_candidates")
            unresolved = next(link for link in links if link["raw_target_without_fragment"] == "Missing")
            self.assertEqual(unresolved["target_candidates"]["cardinality"], "zero_candidates")
            serialized = (output / "vault-observation-summary.json").read_text(encoding="utf-8")
            self.assertNotIn('"unknown"', serialized)

    def test_rendered_heading_addresses_preserve_table_links_and_resolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, output = Path(tmp) / "vault", Path(tmp) / "out"
            root.mkdir()
            (root / "2. Layer-1 — Pillars.md").write_text("""---
uuid: 019bc983-5620-7f08-9626-474a1e548272
---
### Pillar A | [[Semantic Geometry]]
### Pillar B: Dynamic [[Coherence]]
""", encoding="utf-8")
            (root / "3. Layer-2 — Interface.md").write_text("""---
uuid: 019bc983-a82b-70cd-b775-adcc3b323251
---
### [[3. Layer-2 — Interface|Interface]] Components
### A) Cash-out & [[Inferential Bridge (Rule)|Inferential Bridge]] Enforcement
### B) Isomorphic Mappings ([[Form]], without hallucinating [[Content]])
### D) [[Art]] / Creative Writing / Experiments (instantiation + test)
### E) [[3. Layer-2 — Interface|Ethics]] as [[3. Layer-2 — Interface|Interface]] Constraints ([[Epistemic Golden Rule]] / Post-Perennialism)
### Anti-reification Principle:
### Guardrail C: The process
""", encoding="utf-8")
            (root / "Source.md").write_text("""| SECTION | LINK |
|---|---|
| A | [[2. Layer-1 — Pillars#Pillar A Semantic Geometry\\|Semantic Geometry]] |
| B | [[2. Layer-1 — Pillars#Pillar B Dynamic Coherence\\|Dynamic Coherence]] |
""" + "\n".join([
                "[[3. Layer-2 — Interface#Interface Components]]",
                "[[3. Layer-2 — Interface#3. Layer-2 — Interface Interface Components]]",
                "[[3. Layer-2 — Interface#A) Cash-out & Inferential Bridge Enforcement]]",
                "[[3. Layer-2 — Interface#A) Cash-out & Inferential Bridge (Rule) Inferential Bridge Enforcement]]",
                "[[3. Layer-2 — Interface#B) Isomorphic Mappings (form, without hallucinating content)]]",
                "[[3. Layer-2 — Interface#D) Art / Creative Writing / Experiments (instantiation + test)]]",
                "[[3. Layer-2 — Interface#E) Ethics as Interface Constraints (Epistemic Golden Rule / Post-Perennialism)]]",
                "[[3. Layer-2 — Interface#E) 3. Layer-2 — Interface Ethics as 3. Layer-2 — Interface Interface Constraints ( Epistemic Golden Rule / Post-Perennialism)]]",
                "[[3. Layer-2 — Interface#Anti-reification Principle]]",
                "[[3. Layer-2 — Interface#Guardrail C The process]]",
            ]) + "\n", encoding="utf-8")
            observation, _ = observe(root, output)
            source = next(item for item in observation["markdown_observations"] if item["source"]["relative_path"] == "Source.md")
            links = source["authored_links"]
            table_links = links[:2]
            self.assertEqual(len(table_links), 2)
            self.assertEqual(table_links[0]["raw_link_markup"], "[[2. Layer-1 — Pillars#Pillar A Semantic Geometry\\|Semantic Geometry]]")
            self.assertEqual(table_links[0]["raw_target"], "2. Layer-1 — Pillars#Pillar A Semantic Geometry")
            self.assertEqual(table_links[0]["heading_fragment"], "Pillar A Semantic Geometry")
            self.assertEqual(table_links[0]["display_alias"], "Semantic Geometry")
            self.assertEqual(table_links[0]["target_candidates"]["cardinality"], "one_candidate")
            self.assertEqual(table_links[0]["heading_target_evaluation"], "observed")
            self.assertEqual(table_links[0]["heading_target_match_kind"], "normalized")
            self.assertEqual(table_links[0]["heading_target_matches"][0]["raw_text"], "Pillar A | [[Semantic Geometry]]")
            self.assertEqual(table_links[0]["heading_target_matches"][0]["rendered_text"], "Pillar A | Semantic Geometry")
            self.assertEqual(table_links[1]["heading_target_evaluation"], "observed")
            self.assertEqual(table_links[1]["heading_target_match_kind"], "normalized")
            self.assertTrue(all("\\|" not in link["raw_target"] for link in table_links))
            working = {link["heading_fragment"]: link for link in links if link["heading_fragment"].startswith(("3. Layer-2", "A) Cash-out & Inferential Bridge (Rule)", "E) 3. Layer-2"))}
            self.assertEqual(working["3. Layer-2 — Interface Interface Components"]["heading_target_match_kind"], "source_derived")
            self.assertEqual(working["A) Cash-out & Inferential Bridge (Rule) Inferential Bridge Enforcement"]["heading_target_match_kind"], "source_derived")
            self.assertEqual(working["E) 3. Layer-2 — Interface Ethics as 3. Layer-2 — Interface Interface Constraints ( Epistemic Golden Rule / Post-Perennialism)"]["heading_target_match_kind"], "source_derived")
            self.assertEqual(len([link for link in links if link["heading_target_evaluation"] == "observed"]), 12)

    def test_rendered_heading_collision_is_not_claimed_unique(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, output = Path(tmp) / "vault", Path(tmp) / "out"
            root.mkdir()
            (root / "Target.md").write_text("""### Pillar A | [[Semantic Geometry]]
### Pillar A: Semantic Geometry
""", encoding="utf-8")
            (root / "Source.md").write_text("[[Target#Pillar A Semantic Geometry]]\n", encoding="utf-8")
            observation, _ = observe(root, output)
            source = next(item for item in observation["markdown_observations"] if item["source"]["relative_path"] == "Source.md")
            link = source["authored_links"][0]
            self.assertEqual(link["target_candidates"]["cardinality"], "one_candidate")
            self.assertEqual(link["heading_target_evaluation"], "ambiguous")
            self.assertEqual(link["heading_target_match_kind"], "ambiguous")
            self.assertEqual(len(link["heading_target_matches"]), 2)

    def test_apparatus_does_not_affect_identity_but_authored_changes_do(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, one, two = Path(tmp) / "vault", Path(tmp) / "one", Path(tmp) / "two"
            root.mkdir(); self.make_vault(root)
            first, _ = self.run_observer(root, one)
            (root / ".git" / "objects" / "fake").write_bytes(b"changed git state")
            (root / ".semantic-traversal" / "state.db").write_bytes(b"changed generated state")
            second, _ = self.run_observer(root, two)
            self.assertEqual(first["vault_resident_snapshot_identity"], second["vault_resident_snapshot_identity"])
            (root / "INBOX" / "Inbox.md").write_text("# changed\n", encoding="utf-8")
            third, _ = self.run_observer(root, Path(tmp) / "three")
            self.assertNotEqual(second["vault_resident_snapshot_identity"], third["vault_resident_snapshot_identity"])
            (root / "new-empty").mkdir()
            fourth, _ = self.run_observer(root, Path(tmp) / "four")
            self.assertNotEqual(third["vault_resident_snapshot_identity"], fourth["vault_resident_snapshot_identity"])

    def test_identity_ignores_absolute_root_time_and_output_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            first_root, second_root = Path(tmp) / "first", Path(tmp) / "second"
            first_root.mkdir(); second_root.mkdir()
            self.make_vault(first_root); self.make_vault(second_root)
            first, _ = self.run_observer(first_root, Path(tmp) / "first-output")
            second, _ = self.run_observer(second_root, Path(tmp) / "nested" / "second-output")
            self.assertEqual(first["vault_resident_snapshot_identity"], second["vault_resident_snapshot_identity"])
            self.assertNotEqual(first["generated_at"], second["generated_at"])

    def test_cli_contract_and_outputs_are_vault_native(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, output = Path(tmp) / "vault", Path(tmp) / "out"
            root.mkdir(); self.make_vault(root)
            observe(root, output)
            self.assertEqual({path.name for path in output.iterdir()}, {"vault-observation.json", "vault-observation-summary.json"})
            self.assertFalse((output / "recovery-baseline-manifest.json").exists())
            summary = json.loads((output / "vault-observation-summary.json").read_text(encoding="utf-8"))
            self.assertNotIn("vault-secret-canary", json.dumps(summary))

    def test_observation_is_read_only_and_provider_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, output = Path(tmp) / "vault", Path(tmp) / "out"
            root.mkdir(); self.make_vault(root)
            before = sorted((path.relative_to(root).as_posix(), path.stat().st_mtime_ns) for path in root.rglob("*"))
            with patch("tools.authored_vault_observer._git_provenance", return_value={"status": "test_stub", "commit": "fixture"}):
                observe(root, output)
            after = sorted((path.relative_to(root).as_posix(), path.stat().st_mtime_ns) for path in root.rglob("*"))
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
