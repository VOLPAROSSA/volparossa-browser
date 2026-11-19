"""Inert overlay contracts; these do not execute native Firefox callbacks."""

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("filter_source", ROOT / "scripts/prepare_filter_source.py")
source = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(source)


class FilterSourceTests(unittest.TestCase):
    def original(self, path):
        # Explicit synthetic anchors, never claimed as upstream or build proof.
        anchors = [old for old, _new in source.EDITS[path]]
        if path == source.REQUEST:
            anchors.append(source.FINAL_ANCHOR)
        return "// original license remains\n" + "\n".join(anchors)

    def test_guard_preserves_other_callbacks_and_checks_after_await(self):
        result = source.transform(source.REQUEST, self.original(source.REQUEST))
        self.assertIn("opts.blocking\n", result)
        self.assertIn("start(opts.policy, callback, data)", result)
        self.assertIn("admission ? admission.result : callback(data)", result)
        self.assertIn("result = await result;\n", result)
        self.assertLess(result.index("result = await result;"), result.index("admission.validate();"))
        self.assertIn("} else if (isThenable(result)) {", result)
        self.assertTrue(result.startswith("// original license remains\n"))

    def test_guard_failure_cancels_not_ordinary_continue(self):
        result = source.transform(source.REQUEST, self.original(source.REQUEST))
        start = result.index("admission.validate();")
        end = result.index("if (!result || typeof result", start)
        refusal = result[start:end]
        self.assertIn("channel.cancel(", refusal)
        self.assertIn("Cr.NS_ERROR_ABORT", refusal)
        self.assertIn("BLOCKING_REASON_EXTENSION_WEBREQUEST", refusal)
        self.assertIn("return;", refusal)
        self.assertNotIn("continue;", refusal)
        self.assertLess(refusal.index("shouldResume = false"), refusal.index("channel.cancel("))
        self.assertLess(refusal.index("channel.cancel("), refusal.index("channel.resume();"))
        self.assertIn("try {\n            channel.suspend(markerText);", result)

    def test_early_dnr_and_finally_release_all_operations(self):
        result = source.transform(source.REQUEST, self.original(source.REQUEST))
        self.assertEqual(result.count("admission?.discard();"), 2)
        self.assertIn("} finally {", result)
        self.assertIn("for (const { admission } of handlerResults)", result)

    def test_reapplying_or_ambiguous_source_rejected(self):
        for path in source.EDITS:
            original = self.original(path)
            with self.assertRaises(ValueError):
                source.transform(path, source.transform(path, original))
            with self.assertRaises(ValueError):
                source.transform(path, original + source.EDITS[path][0][0])
            with self.assertRaises(ValueError):
                source.transform(path, "unrelated file")
        with self.assertRaises(ValueError):
            source.transform("arbitrary", "")

    def test_modules_not_webextension_api_or_enrollment(self):
        result = source.transform(source.BUILD, self.original(source.BUILD))
        self.assertIn('EXTRA_JS_MODULES["volparossa"]', result)
        for name in source.MODULES:
            self.assertIn('"volparossa/' + name + '"', result)
        self.assertNotIn("schemas", result)

    def test_inventory_pins_exact_files(self):
        pins = json.loads((ROOT / "patches/firefox-filter-admission.json").read_text())
        self.assertEqual(pins["upstream_revision"], source.REVISION)
        self.assertEqual(set(pins["upstream_sha256"]), set(source.EDITS))
        self.assertEqual(pins["upstream_sha256"][source.REQUEST],
                         "a31dacd540c7335d8f703539de34b4f5eb5531a0e2eccf3bcb748bdcf2136e35")

    def test_staging_is_fresh_bounded_and_self_describing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tree = root / "upstream"
            pins = {"upstream_revision": source.REVISION, "upstream_sha256": {},
                    "kind": "exact-source-overlay-not-a-firefox-build"}
            for path in source.EDITS:
                original = self.original(path).encode()
                dest = tree / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(original)
                pins["upstream_sha256"][path] = hashlib.sha256(original).hexdigest()
            (root / "patches").mkdir()
            (root / "patches/firefox-filter-admission.json").write_text(json.dumps(pins))
            originals = {path: self.original(path) for path in source.EDITS}
            transformed = {path: source.transform(path, original) for path, original in originals.items()}
            (root / "patches/0003-filter-admission.patch").write_bytes(source.render_patch(originals, transformed))
            modules = root / "integration/filters"
            modules.mkdir(parents=True)
            for name in source.MODULES:
                (modules / name).write_text("// synthetic inert module\n")
            with patch.object(source, "ROOT", root):
                output = root / "build/staged"
                source.prepare(tree, output)
                report = json.loads((output / "source.json").read_text())
                for flag in ("native_build_proven", "default_enrollment", "publication_owner_integrated"):
                    self.assertIs(report[flag], False)
                self.assertEqual(hashlib.sha256((output / "filter-admission.patch").read_bytes()).hexdigest(),
                                 report["patch_sha256"])
                with self.assertRaises(ValueError):
                    source.prepare(tree, output)
                with self.assertRaises(ValueError):
                    source.prepare(tree, root / "outside")
                patch_file = root / "patches/0003-filter-admission.patch"
                expected_patch = patch_file.read_bytes()
                patch_file.write_bytes(b"wrong\n")
                with self.assertRaises(ValueError):
                    source.prepare(tree, root / "build/wrong-patch")
                self.assertFalse((root / "build/wrong-patch").exists())
                patch_file.write_bytes(expected_patch)
                # Modified upstream source cannot produce a new stage.
                (tree / source.REQUEST).write_text("changed\n")
                with self.assertRaises(ValueError):
                    source.prepare(tree, root / "build/changed")
                self.assertFalse((root / "build/changed").exists())

    def test_bounded_file_refuses_symlink_and_oversize(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real = root / "regular"
            real.write_bytes(b"abcdef")
            link = root / "link"
            link.symlink_to(real)
            with self.assertRaises(ValueError):
                source.bounded_file(link)
            with patch.object(source, "MAX_SOURCE", 5):
                with self.assertRaises(ValueError):
                    source.bounded_file(real)


if __name__ == "__main__":
    unittest.main()
