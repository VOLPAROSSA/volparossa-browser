# SPDX-License-Identifier: GPL-3.0-only
"""Narrow native runtime/fixture boundaries; no browser is launched by these checks."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import smoke_network as network
import smoke_network_native as native


class NativeNetworkTests(unittest.TestCase):
    def test_changed_receipt_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as directory:
            root = Path(directory)
            (root / "build-result.json").write_text(json.dumps({"native_build_proven": True}))
            with mock.patch.object(native.native_build.subprocess, "Popen", side_effect=AssertionError("execution")):
                with self.assertRaises(ValueError):
                    native.validate_native_runtime(root)

    def test_esr_fixture_does_not_accept_native_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                network.fixture_modules(Path(directory), dict(version="157.0.1",
                    source_stamp="47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1"))

    def test_missing_overlay_is_rejected_without_preparation_or_write(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as directory:
            root = Path(directory)
            pins = json.loads(native.native_build.PINS.read_text())
            (root / "build-result.json").write_text(json.dumps(dict(source=pins["source"],
                native_build_proven=True, build_network_disabled=True, system_installation=False,
                output=str(root), outputs={})))
            with mock.patch.object(native, "digest", return_value=native.BUILD_RECEIPT_SHA256), \
                 mock.patch.object(native.native_build, "prepare_overlay", side_effect=AssertionError("write path")):
                with self.assertRaises(ValueError):
                    native.validate_native_runtime(root)
            self.assertEqual([p.name for p in root.iterdir()], ["build-result.json"])

    def test_builtin_module_path_has_no_native_opt_out_removal(self):
        self.assertIn('let modulePrefix = "resource:///modules/";', network.SCRIPT)
        self.assertIn('if (moduleRoot !== null)', network.SCRIPT)
        self.assertIn('result.native_ech_abi = "allowECH" in fresh && fresh.allowECH === true;', network.SCRIPT)
        self.assertIn('browser.loadURI(', network.SCRIPT)
        self.assertIn('first = await VolparossaNetwork.attach(grant(0));', network.SCRIPT)
        self.assertIn('second = await VolparossaNetwork.attach(grant(1));', network.SCRIPT)

    def test_explicit_resource_overlay_is_exact_product_bytes_and_separate_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            native.stage_javascript_overlay(work)
            native.validate_javascript_overlay(work)
            folder = work / "resource-overlay"
            self.assertEqual((folder / native.CONTROLLER).read_bytes(),
                             (ROOT / "integration" / native.CONTROLLER).read_bytes())
            record = json.loads((folder / "receipt.json").read_text())
            self.assertEqual(record["original_build_receipt_sha256"], native.BUILD_RECEIPT_SHA256)
            self.assertNotEqual(record["original_sha256"], record["runtime_sha256"])
            self.assertFalse(record["native_rebuild"])
            self.assertFalse(record["compatibility_rewrite"])
            self.assertFalse(record["original_build_modified"])
            with self.assertRaises(FileExistsError):
                native.stage_javascript_overlay(work)
            (folder / native.CONTROLLER).write_text("changed")
            with self.assertRaises(ValueError):
                native.validate_javascript_overlay(work)

    def test_resource_mount_is_only_the_pinned_builtin_controller(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = ROOT / "build/native-firefox-157"
            native.stage_javascript_overlay(work)
            command = native.sandbox_command(work, root, [], javascript_overlay=True)
            target = str(root / "source" / native.CONTROLLER_SOURCE)
            index = command.index(target)
            self.assertEqual(command[index - 2:index],
                ["--ro-bind", str(work / "resource-overlay" / native.CONTROLLER)])
            self.assertIn("--unshare-net", command)
            self.assertIn("--unshare-pid", command)


if __name__ == "__main__":
    unittest.main()
