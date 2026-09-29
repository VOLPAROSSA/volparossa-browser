"""Small configuration/renderer tests. The separate smoke launches real Firefox."""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from stage_firefox import ROOT, build_path, load_defaults, render_defaults


class PrivacyDefaultsTests(unittest.TestCase):
    def test_requested_features_have_explicit_defaults(self):
        values = load_defaults()
        for name in (
            "datareporting.policy.dataSubmissionEnabled",
            "datareporting.healthreport.uploadEnabled",
            "datareporting.usage.uploadEnabled",
            "toolkit.telemetry.enabled",
            "identity.fxaccounts.enabled",
            "browser.newtabpage.activity-stream.showSponsored",
            "browser.newtabpage.activity-stream.showSponsoredTopSites",
            "browser.urlbar.suggest.quicksuggest.sponsored",
        ):
            self.assertIs(values[name], False)
        self.assertEqual(values["browser.contentblocking.category"], "strict")

    def test_renderer_only_sets_defaults_never_locks_or_resets_users(self):
        values = load_defaults()
        rendered = render_defaults(values)
        lines = rendered.splitlines()
        self.assertTrue(lines[0].startswith("//"))
        self.assertEqual(len(lines), len(values) + 3)
        self.assertTrue(all(line.startswith("defaultPref(") for line in lines[1:-2]))
        self.assertEqual(lines[-1], 'pref("browser.contentblocking.category", getPref("browser.contentblocking.category"));')
        self.assertNotIn("lockPref(", rendered)
        self.assertNotIn("user_pref(", rendered)
        self.assertEqual(rendered, render_defaults(dict(reversed(list(values.items())))))

    def test_staging_is_confined_to_a_workspace_build_child(self):
        self.assertEqual(build_path(ROOT / "build/test-stage"), ROOT / "build/test-stage")
        for forbidden in (ROOT, ROOT / "build", "/usr/lib/firefox-esr", ROOT.parent):
            with self.subTest(path=forbidden), self.assertRaises(ValueError):
                build_path(forbidden)

    def test_staging_rejects_symlinks_that_escape_build(self):
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="path-test-", dir=ROOT / "build") as name:
            fixture = Path(name)
            (fixture / "outside").symlink_to(ROOT, target_is_directory=True)
            with self.assertRaises(ValueError):
                build_path(fixture / "outside/new-stage")
            (fixture / "inside").symlink_to(fixture, target_is_directory=True)
            self.assertEqual(build_path(fixture / "inside/new-stage"), fixture / "new-stage")


if __name__ == "__main__":
    unittest.main()
