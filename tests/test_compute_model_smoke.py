"""Focused fixture checks, not real model/browser execution evidence."""

import copy
import importlib.util
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("model_smoke", ROOT / "scripts/smoke_compute_model.py")
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)


def result():
    return {
        "capabilities": {"visibility": "private_local", "local_only": True,
            "model_profile": SMOKE.PROFILE, "network_access": False, "public_cache": False,
            "training": False, "cloud_fallback": False},
        "admitted": 1, "removed": True,
        "answer": {"answer_status": "eos", "complete": True, "canary_present": True, "generated_tokens": 8},
        "boundary": {"point": "decoded_result_before_panel_render", "ephemeral_children": 0,
            "observed_worker_lifetimes_ended": True},
        "panel": {"actual_sidebar_document": True, "connected": True, "canary_rendered": True,
            "text_only": True, "eos_status_visible": True},
    }


class ComputeModelSmokeTests(unittest.TestCase):
    def test_fixed_diagnostics_reject_private_fields_and_preserve_first_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            SMOKE.status(root, "submit-admitted", "BROKER_EXECUTION_FAILED")
            SMOKE.failed_status(root, RuntimeError("private question or worker output"))
            path = root / SMOKE.STATUS_NAME
            self.assertEqual(json.loads(path.read_text()), dict(version=1, phase="submit-admitted",
                failure="BROKER_EXECUTION_FAILED"))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("private", path.read_text())
            SMOKE.status(root, "marionette-connect")
            SMOKE.failed_status(root, RuntimeError("private question or worker output"))
            self.assertEqual(json.loads(path.read_text())["failure"], "RUNTIME_FAILED")
        for value in (
            dict(version=1, phase="module-import", failure="secret"),
            dict(version=1, phase="private question", failure=None),
            dict(version=1, phase="module-import", failure=None, text="private answer"),
            dict(version=True, phase="module-import", failure=None),
        ):
            with self.assertRaises(ValueError):
                SMOKE.check_status(value)

    def test_sanitized_result_rejects_incomplete_inference_cleanup_and_scope_changes(self):
        valid = result()
        SMOKE.check_result(valid, "CANARY12345678")
        mutations = [
            ("answer", "answer_status", "token_limit"), ("answer", "complete", False),
            ("answer", "canary_present", False), ("answer", "generated_tokens", 257),
            ("answer", "generated_tokens", True), ("answer", "text", "private answer"),
            ("capabilities", "training", True), ("capabilities", "network_access", True),
            ("capabilities", "model_profile", "synthetic-model"),
            ("boundary", "ephemeral_children", 1), ("boundary", "observed_worker_lifetimes_ended", False),
            ("boundary", "point", "first_socket_byte"), ("panel", "text_only", False),
            ("panel", "actual_sidebar_document", False), ("panel", "connected", False),
        ]
        for section, key, value in mutations:
            changed = copy.deepcopy(valid)
            changed[section][key] = value
            with self.subTest(section=section, key=key), self.assertRaises(ValueError):
                SMOKE.check_result(changed, "CANARY12345678")
        for key, value in (("admitted", 0), ("admitted", 2), ("admitted", True),
                           ("removed", False), ("raw_answer", "secret")):
            changed = copy.deepcopy(valid)
            changed[key] = value
            with self.assertRaises(ValueError):
                SMOKE.check_result(changed, "CANARY12345678")
        with self.assertRaises(ValueError):
            SMOKE.check_result(valid, "arbitrary private prompt")

    def test_owner_socket_requires_exact_private_parent_and_socket_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            path = root / "private.sock"
            with socket.socket(socket.AF_UNIX) as listener:
                listener.bind(str(path))
                path.chmod(0o600)
                self.assertEqual(SMOKE.validate_endpoint(path), (path.stat().st_dev, path.stat().st_ino))
                path.chmod(0o660)
                with self.assertRaises(ValueError):
                    SMOKE.validate_endpoint(path)
                path.chmod(0o600)
                root.chmod(0o755)
                with self.assertRaises(ValueError):
                    SMOKE.validate_endpoint(path)
                root.chmod(0o700)
                alias = root / "alias.sock"
                alias.symlink_to(path)
                with self.assertRaises(ValueError):
                    SMOKE.validate_endpoint(alias)

    def test_cleanup_removes_only_created_browser_data_and_preserves_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("profile", "config", "cache", "runtime", "tmp"):
                (root / name).mkdir(mode=0o700)
                (root / name / "synthetic-private-data").write_bytes(b"not an exported artifact")
            (root / "firefox.log").write_bytes(b"temporary browser log")
            (root / "admitted.json").write_text('{"version":1}')
            SMOKE.remove_browser_files(root)
            self.assertEqual([path.name for path in root.iterdir()], ["admitted.json"])
            SMOKE.remove_browser_files(root)
            (root / "profile").symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError):
                SMOKE.remove_browser_files(root)
            self.assertTrue((root / "admitted.json").is_file())

    def test_harness_has_real_panel_and_no_fake_service_or_automatic_provision(self):
        source = (ROOT / "scripts/smoke_compute_model.py").read_text()
        self.assertIn('await panel.ask(question, context)', SMOKE.SCRIPT)
        self.assertIn('SidebarController.show("viewGenaiChatSidebar")', SMOKE.SCRIPT)
        self.assertIn('return original.call(this, response)', SMOKE.SCRIPT)
        self.assertIn('observed.boundary = observeBoundary()', SMOKE.SCRIPT)
        self.assertIn('observation.cli?.pid !== servicePid', SMOKE.SCRIPT)
        self.assertIn('output.generation.stop_reason !== "eos"', SMOKE.SCRIPT)
        self.assertNotIn('socket.socket(', source)
        self.assertNotIn('protocol_fixture', source)
        self.assertNotIn('urllib.request', source)
        self.assertNotIn('pip install', source)
        self.assertFalse(any(SMOKE.SCOPE.values()))
        self.assertEqual(SMOKE.RUNTIME["bytes"], 71994716)
        self.assertRegex(SMOKE.RUNTIME["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(len(SMOKE.RUNTIME_SHA256), 4)
        self.assertNotIn("text", result()["answer"])


if __name__ == "__main__":
    unittest.main()
