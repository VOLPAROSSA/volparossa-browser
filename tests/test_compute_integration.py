import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("compute_source", ROOT / "scripts/prepare_compute_source.py")
SOURCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SOURCE)


class ComputeIntegrationTests(unittest.TestCase):
    def test_source_revision_and_all_modified_upstream_files_are_pinned(self):
        provenance = json.loads((ROOT / "patches/firefox-source.json").read_text())
        self.assertEqual(provenance["upstream_revision"], SOURCE.REVISION)
        self.assertEqual(set(provenance["upstream_sha256"]), {*SOURCE.EDITS, SOURCE.LOCALE})
        for digest in provenance["upstream_sha256"].values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_changed_or_duplicate_source_anchors_are_rejected(self):
        for path, edits in SOURCE.EDITS.items():
            original = "/* retained upstream MPL notice */\n" + "".join(old for old, _ in edits)
            patched = SOURCE.transform(path, original)
            self.assertTrue(patched.startswith("/* retained upstream MPL notice */"))
            for old, new in edits:
                self.assertIn(new, patched)
                with self.assertRaises(ValueError):
                    SOURCE.transform(path, original + old)
            with self.assertRaises(ValueError):
                SOURCE.transform(path, "changed source")

    def test_private_ask_returns_before_existing_provider_pipeline(self):
        path = SOURCE.PREFIX + "GenAI.sys.mjs"
        old, branch = next((old, new) for old, new in SOURCE.EDITS[path] if "async handleAskChat" in old)
        self.assertTrue(branch.startswith(old))
        self.assertIn('lazy.chatProvider === "volparossa:private"', branch)
        self.assertIn("await sidebar.volparossaAsk(promptObj, context);", branch)
        self.assertIn("return;", branch)
        self.assertNotIn("new URL", branch)
        self.assertNotIn("AIWindowUI", branch)

    def test_panel_uses_text_only_without_cloud_or_markup_fallback(self):
        panel = (ROOT / "integration/VolparossaComputePanel.sys.mjs").read_text()
        self.assertIn("output.textContent = result.output.text;", panel)
        self.assertIn("await task?.cancel()", panel)
        self.assertIn("client?.close()", panel)
        for forbidden in ("innerHTML", "insertAdjacentHTML", "eval(", "fetch(", "XMLHttpRequest", "https://"):
            self.assertNotIn(forbidden, panel)


if __name__ == "__main__":
    unittest.main()
