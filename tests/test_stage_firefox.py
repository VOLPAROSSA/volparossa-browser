"""Small synthetic staging regressions; no browser execution or model proof."""

from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from stage_firefox import ROOT, stage


class StageFirefoxTests(unittest.TestCase):
    def fixture(self, parent):
        source = parent / "runtime"
        (source / "browser").mkdir(parents=True)
        (source / "defaults/pref").mkdir(parents=True)
        shared_defaults = parent / "package-share/browser/defaults"
        (shared_defaults / "preferences").mkdir(parents=True)
        # The real Debian package also puts browser/defaults behind a relative link.
        (source / "browser/defaults").symlink_to(
            "../../package-share/browser/defaults", target_is_directory=True
        )
        (shared_defaults / "preferences/vendor.js").write_text("// package preference\n")
        (source / "defaults/pref/syspref").write_text("not the excluded host directory\n")
        for name in ("firefox-esr", "libxul.so", "omni.ja", "browser/omni.ja"):
            (source / name).write_bytes(b"synthetic runtime fixture\n")
        (source / "application.ini").write_text(
            "[App]\nVersion=fixture\nSourceStamp=fixture-stamp\n"
            "SourceRepository=https://example.invalid/test\nBuildID=fixture-build\n"
        )
        (source / "distribution").mkdir()
        (source / "distribution/policies.json").write_text('{"synthetic_host_policy":true}')
        return source, shared_defaults

    def test_staging_excludes_absent_and_external_system_preferences(self):
        (ROOT / "build").mkdir(exist_ok=True)
        for target_exists in (False, True):
            with self.subTest(target_exists=target_exists), tempfile.TemporaryDirectory(
                prefix="stage-syspref-test-", dir=ROOT / "build"
            ) as name:
                parent = Path(name)
                source, defaults = self.fixture(parent)
                external = parent / "host-etc/firefox-esr"
                if target_exists:
                    external.mkdir(parents=True)
                    (external / "host-private.js").write_text("// must never be imported\n")
                (defaults / "syspref").symlink_to(external, target_is_directory=True)
                output = parent / "staged"
                record = stage(source / "firefox-esr", output, "fixture", "fixture-stamp")
                self.assertFalse((output / "browser/defaults/syspref").exists())
                self.assertFalse((output / "browser/defaults/syspref").is_symlink())
                self.assertFalse((output / "distribution").exists())
                self.assertEqual(
                    (output / "browser/defaults/preferences/vendor.js").read_text(),
                    "// package preference\n",
                )
                self.assertEqual(
                    (output / "defaults/pref/syspref").read_text(),
                    "not the excluded host directory\n",
                )
                self.assertTrue(record["excluded_host_system_preferences"])
                self.assertTrue(record["excluded_host_distribution_policies"])
                self.assertFalse((output / "browser/defaults").is_symlink())
                self.assertEqual((output / "libxul.so").read_bytes(), b"synthetic runtime fixture\n")

    def test_staging_does_not_skip_other_missing_runtime_dependencies(self):
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="stage-dependency-test-", dir=ROOT / "build") as name:
            parent = Path(name)
            source, _ = self.fixture(parent)
            (source / "runtime-dependency.so").symlink_to(parent / "missing-library.so")
            with self.assertRaises(shutil.Error):
                stage(source / "firefox-esr", parent / "staged", "fixture", "fixture-stamp")


if __name__ == "__main__":
    unittest.main()
