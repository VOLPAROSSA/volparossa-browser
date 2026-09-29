"""Bounded startup diagnostics only; no real Firefox/model execution claimed."""

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("startup_smoke", ROOT / "scripts/smoke_browser_startup.py")
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)


class BrowserStartupTests(unittest.TestCase):
    def test_only_separate_empty_profile_log_retained_with_exact_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            private = root / "firefox.log"
            private.write_bytes(b"combined private session MUST NOT be copied")
            raw = b"startup prefix\n" + b"a" * 17000 + b"synthetic empty startup\n"
            (root / SMOKE.RAW_LOG_NAME).write_bytes(raw)
            report = SMOKE.retain_empty_startup_log(root)
            self.assertEqual((root / SMOKE.LOG_NAME).read_bytes(), raw[-16384:])
            self.assertEqual(report["bytes"], 16384)
            self.assertEqual(report["observed_bytes"], len(raw))
            self.assertTrue(report["truncated"])
            self.assertEqual(report["sha256"], SMOKE.digest(root / SMOKE.LOG_NAME))
            self.assertEqual((root / SMOKE.LOG_NAME).stat().st_mode & 0o777, 0o600)
            self.assertFalse((root / SMOKE.RAW_LOG_NAME).exists())
            self.assertTrue(private.exists())
            self.assertNotIn(b"private session", (root / SMOKE.LOG_NAME).read_bytes())
            (root / SMOKE.RAW_LOG_NAME).symlink_to(private)
            with self.assertRaises(OSError):
                SMOKE.retain_empty_startup_log(root)

    def test_fixed_listener_and_loopback_status(self):
        header = "sl local_address rem_address st\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tcp").write_text(header + "0: 0100007F:0B0C 00000000:0000 0A\n")
            (root / "tcp6").write_text(header)
            value = SMOKE.network_snapshot([dict(ifname="lo", flags=["LOOPBACK", "UP"])], root)
            self.assertEqual(value, dict(interfaces=["lo"], loopback_up=True, marionette_port=2828,
                                        ipv4_listener=True, ipv6_listener=False))
            self.assertFalse(SMOKE.network_snapshot([dict(ifname="lo", flags=[])], root)["loopback_up"])
            for row in ("0: 0100007F:0B0C 00000000:0000 01\n", "0: 0100007F:0016 00000000:0000 0A\n"):
                self.assertFalse(SMOKE.port_listener(header + row))
            with self.assertRaises(ValueError):
                SMOKE.port_listener("x" * 65537)
            with self.assertRaises(ValueError):
                SMOKE.network_snapshot([dict(ifname="eth0")], root)

    def test_process_kernel_facts_without_argv_and_profile_cleanup(self):
        observed = SMOKE.process_snapshot(os.getpid())
        self.assertTrue(observed["readable"])
        self.assertGreater(observed["start_ticks"], 0)
        self.assertEqual(set(observed), {"pid", "start_ticks", "state", "wchan", "readable"})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in SMOKE.PROFILE_NAMES:
                (root / name).mkdir()
                (root / name / "empty-startup-state").write_bytes(b"temporary")
            (root / SMOKE.LOG_NAME).write_bytes(b"retained synthetic diagnostics")
            self.assertTrue(SMOKE.remove_profile(root))
            self.assertEqual([path.name for path in root.iterdir()], [SMOKE.LOG_NAME])
            self.assertFalse(SMOKE.process_snapshot(123, root)["readable"])

    def test_no_private_arguments_or_broker_and_original_startup_boundary(self):
        source = (ROOT / "scripts/smoke_browser_startup.py").read_text()
        self.assertEqual(SMOKE.CONNECT_SECONDS, 40)
        self.assertEqual(SMOKE.SCOPE, dict(private_input_used=False, broker_connected=False, model_executed=False))
        self.assertIn('socket.create_connection(("127.0.0.1", 2828)', source)
        self.assertIn('"--unshare-user", "--unshare-net"', source)
        self.assertIn('"--ro-bind", "/", "/"', source)
        self.assertIn('"about:blank"', source)
        for forbidden in ('add_argument("--socket"', 'add_argument("--canary"', 'add_argument("--service-pid"',
                          'add_argument("--work-parent"', 'ExecuteAsyncScript', 'MOZ_DISABLE_CONTENT_SANDBOX',
                          'MOZ_DISABLE_RDD_SANDBOX', 'security.sandbox.content.level'):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
