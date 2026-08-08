from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.authored_vault_observer import observe


FIXTURE = """---
uuid: 11111111-1111-4111-8111-111111111111
aliases: [Shared]
secret: vault-secret-canary
---
# Heading

[[Missing]] [[Target#Heading]] [[Target#Nope]] [[Target^block]] [[Target|Shown]] ![[Target]] [[Shared]]

This paragraph is deliberately longer than the production chunk limit and must remain one authored block. """ + ("x" * 2200) + "\n\n> quoted material\n\n- list item\n\n| a | b |\n|---|---|\n| c | d |\n\n```python\nprint('code')\n```\n\n123\n"""


class AuthoredVaultObserverTests(unittest.TestCase):
    def make_vault(self, root: Path) -> None:
        (root / "Target.md").write_text("---\nuuid: 22222222-2222-4222-8222-222222222222\naliases: [Shared]\n---\n# Heading\n\nbody\n", encoding="utf-8")
        (root / "Source.md").write_text(FIXTURE, encoding="utf-8")
        (root / "MissingUuid.md").write_text("---\ntitle: missing\n---\nbody\n", encoding="utf-8")
        (root / "MalformedUuid.md").write_text("---\nuuid: definitely-not-a-uuid\n---\nbody\n", encoding="utf-8")
        (root / "Malformed.md").write_text("---\nuuid: [not closed\n---\nbody\n", encoding="utf-8")
        (root / "Duplicate.md").write_text("---\nuuid: 11111111-1111-4111-8111-111111111111\n---\n", encoding="utf-8")
        (root / "excluded" / "Policy.md").parent.mkdir()
        (root / "excluded" / "Policy.md").write_text("---\nuuid: 33333333-3333-4333-8333-333333333333\n---\npolicy\n", encoding="utf-8")
        (root / "asset.bin").write_bytes(b"binary")

    def observe_fixture(self, vault: Path, output: Path, config: Path | None = None):
        return observe(vault, output, runtime_config=config)

    def test_fixture_preserves_raw_sources_and_observes_ugly_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, out = Path(tmp) / "vault", Path(tmp) / "out"
            root.mkdir(); self.make_vault(root)
            observation, summary, _ = self.observe_fixture(root, out)
            records = {r["source"]["relative_path"]: r for r in observation["markdown_observations"]}
            self.assertEqual(records["Source.md"]["raw_markdown"], FIXTURE)
            self.assertEqual(records["MissingUuid.md"]["uuid"]["parse_status"], "missing")
            self.assertEqual(records["MalformedUuid.md"]["uuid"]["parse_status"], "invalid")
            self.assertEqual(records["Malformed.md"]["frontmatter"]["status"], "malformed")
            self.assertEqual(records["Duplicate.md"]["uuid"]["duplicate_source_paths"], ["Duplicate.md", "Source.md"])
            self.assertEqual(summary["file_kind_counts"]["binary_or_non_markdown"], 1)
            self.assertGreaterEqual(summary["ambiguous_target_count"], 1)
            self.assertGreaterEqual(summary["unresolved_target_count"], 1)
            links = records["Source.md"]["authored_links"]
            self.assertTrue(any(link["embedded"] for link in links))
            self.assertTrue(any(link["display_alias"] == "Shown" for link in links))
            self.assertTrue(any(link["heading_fragment"] == "Heading" for link in links))
            self.assertTrue(any(link["block_fragment"] == "block" for link in links))
            heading_missing = next(link for link in links if link["heading_fragment"] == "Nope")
            self.assertEqual(heading_missing["target_resolution"]["heading_target_status"], "absent")
            long_blocks = [b for b in records["Source.md"]["block_candidates"] if len(b["raw_markdown"]) > 2200]
            self.assertEqual(len(long_blocks), 1)
            start, end = long_blocks[0]["source_span"]
            self.assertEqual(records["Source.md"]["raw_markdown"][start:end], long_blocks[0]["raw_markdown"])

    def test_identity_ignores_time_absolute_root_and_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "one"; second = Path(tmp) / "two"
            first.mkdir(); second.mkdir(); self.make_vault(first); self.make_vault(second)
            a = self.observe_fixture(first, Path(tmp) / "out-a"); b = self.observe_fixture(second, Path(tmp) / "nested" / "out-b")
            self.assertEqual(a[0]["corpus_snapshot_identity"], b[0]["corpus_snapshot_identity"])
            self.assertEqual(a[0]["logical_observation_hash"], b[0]["logical_observation_hash"])
            self.assertNotEqual(a[0]["generated_at"], b[0]["generated_at"])

    def test_runtime_exclusion_is_annotation_only_and_summary_redacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, out, config = Path(tmp) / "vault", Path(tmp) / "out", Path(tmp) / "config.yaml"
            root.mkdir(); self.make_vault(root)
            config.write_text("paths:\n  vault_exclude_globs: ['excluded/**']\n", encoding="utf-8")
            observation, _, _ = self.observe_fixture(root, out, config)
            policy = next(e for e in observation["source_inventory"] if e["relative_path"] == "excluded/Policy.md")["legacy_runtime_policy"]
            self.assertTrue(policy["excluded_by_current_runtime"])
            self.assertIn("excluded/Policy.md", {r["source"]["relative_path"] for r in observation["markdown_observations"]})
            self.assertNotIn("vault-secret-canary", (out / "authored-vault-summary.json").read_text())

    def test_read_only_and_provider_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, out = Path(tmp) / "vault", Path(tmp) / "out"
            root.mkdir(); self.make_vault(root)
            before = sorted((p.relative_to(root).as_posix(), p.stat().st_mtime_ns) for p in root.rglob("*"))
            with patch("tools.authored_vault_observer.subprocess.check_output", side_effect=AssertionError("provider call")):
                observe(root, out)
            after = sorted((p.relative_to(root).as_posix(), p.stat().st_mtime_ns) for p in root.rglob("*"))
            self.assertEqual(before, after)
            self.assertFalse(any(p.name.endswith(".db") for p in out.rglob("*")))


if __name__ == "__main__":
    unittest.main()
