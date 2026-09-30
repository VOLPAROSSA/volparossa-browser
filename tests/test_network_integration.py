#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Narrow source/driver contracts; not a replacement for the real Gecko/overlay proof."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import prepare_network_source as SOURCE
import smoke_network as SMOKE
import smoke_network_core as CORE


class NetworkIntegrationTests(unittest.TestCase):
    def test_attachment_diagnostics_are_closed_and_survive_outer_failure(self):
        source = (ROOT / "integration/VolparossaNetwork.sys.mjs").read_text()
        stages = json.loads(re.search(r"ATTACH_STAGES = new Set\((\[.*?\])\)", source, re.S)[1])
        self.assertEqual(set(stages), CORE.ATTACH_STAGES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            detail = dict(stage="bootstrap-eof", nsresult=0x804B000D)
            (root / CORE.STATUS_NAME).write_text(json.dumps(CORE.status_record("attach-a", "unavailable", attachment=detail)))
            CORE.driver_status(root, "wrapper-launch", CORE.subprocess.CalledProcessError(1, ["secret-capability"]))
            result = json.loads((root / CORE.STATUS_NAME).read_text())
            self.assertEqual(result["attachment"], detail)
            self.assertEqual(result["phase"], "attach-a")
            self.assertNotIn("secret", json.dumps(result))
        for detail in (dict(stage="private socket path", nsresult=None),
                       dict(stage="bootstrap-eof", nsresult=True),
                       dict(stage="bootstrap-eof", nsresult=-1),
                       dict(stage="bootstrap-eof", nsresult=1 << 32),
                       dict(stage="bootstrap-eof", nsresult=1, message="secret")):
            with self.assertRaises(ValueError):
                CORE.attach_diagnostic(detail)

    def test_actual_socket_probe_sends_no_bootstrap_or_capability(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "app.sock"
            with socket.socket(socket.AF_UNIX) as listener:
                listener.bind(str(path))
                path.chmod(0o660)
                listener.listen(1)
                result = CORE.probe_app_socket(root, [dict(app_socket=str(path), capability="secret-canary")])
                connection, _ = listener.accept()
                with connection:
                    self.assertEqual(connection.recv(1), b"")
                self.assertEqual(result, dict(path_type_verified=True, socket_parent_owner_group_match=True,
                    peer_uid_matches_socket=True, unix_connect_verified=True, capability_sent=False))
                self.assertEqual(json.loads((root / CORE.STATUS_NAME).read_text())["phase"], "socket-access")
                path.chmod(0o666)
                with self.assertRaises(ValueError):
                    CORE.probe_app_socket(root, [dict(app_socket=str(path))])
                path.chmod(0o660)
                alias = root / "alias"
                alias.symlink_to(path)
                with self.assertRaises(ValueError):
                    CORE.probe_app_socket(root, [dict(app_socket=str(alias))])
            with self.assertRaises(ConnectionRefusedError):
                CORE.probe_app_socket(root, [dict(app_socket=str(path))])

    def test_driver_status_keeps_child_phase_and_only_closed_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            CORE.driver_status(work, "grant-validation")
            CORE.driver_status(work, "runtime-validation", PermissionError(13, "secret capability and private path"))
            CORE.driver_status(work, "wrapper-launch", CORE.subprocess.CalledProcessError(1, ["private", "argv"]))
            raw = (work / CORE.STATUS_NAME).read_text()
            self.assertEqual(json.loads(raw), CORE.status_record("grant-validation", "OS_ERROR", 13, 1))
            self.assertNotIn("secret", raw)
            self.assertNotIn("private", raw)
            self.assertEqual((work / CORE.STATUS_NAME).stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError):
                CORE.driver_status(work, "unknown private phase")

    def test_outer_runtime_failure_is_recorded_before_bubblewrap(self):
        with tempfile.TemporaryDirectory() as directory:
            stage, work = Path(directory) / "stage", Path(directory) / "work"
            stage.mkdir()
            argv = ["smoke_network_core.py", "--stage", str(stage), "--output", str(work),
                "--grant-a", "/private/a", "--grant-b", "/private/b", "--test-ca", "/private/ca",
                "--url-a", "https://fixture.invalid/a", "--url-b", "https://fixture.invalid/b",
                "--expected-sha256", "a" * 64, "--core-revision", "b" * 40,
                "--parent-netns", "net:[123]", "--expected-bytes", "33554432"]
            with patch.object(CORE.sys, "argv", argv), patch.object(CORE, "guest_guard"), \
                 patch.object(CORE, "build_path", return_value=work), \
                 patch.object(CORE, "validate_stage", side_effect=PermissionError(13, "private filename")), \
                 patch.object(CORE.subprocess, "run") as launch:
                with self.assertRaises(PermissionError):
                    CORE.main()
                launch.assert_not_called()
            self.assertEqual(json.loads((work / CORE.STATUS_NAME).read_text()),
                CORE.status_record("runtime-validation", "OS_ERROR", 13))

    def test_wrapper_uses_owned_cwd_before_and_inside_bubblewrap(self):
        with tempfile.TemporaryDirectory() as directory:
            stage, work = Path(directory) / "stage", Path(directory) / "work"
            stage.mkdir()
            argv = ["smoke_network_core.py", "--stage", str(stage), "--output", str(work),
                "--grant-a", "/private/a", "--grant-b", "/private/b", "--test-ca", "/private/ca",
                "--url-a", "https://fixture.invalid/a", "--url-b", "https://fixture.invalid/b",
                "--expected-sha256", "a" * 64, "--core-revision", "b" * 40,
                "--parent-netns", "net:[123]", "--expected-bytes", "33554432"]
            with patch.object(CORE.sys, "argv", argv), patch.object(CORE, "guest_guard"), \
                 patch.object(CORE, "build_path", return_value=work), \
                 patch.object(CORE, "validate_stage"), \
                 patch.object(CORE, "isolated_browser_home", return_value=[]), \
                 patch.object(CORE, "isolated_runtime_parent", return_value=["--tmpfs", "/home/vpci",
                     "--ro-bind", "/home/vpci/runtime", "/home/vpci/runtime", "--remount-ro", "/home/vpci"]), \
                 patch.object(CORE.subprocess, "run") as launch:
                CORE.main()
            command = launch.call_args.args[0]
            self.assertEqual(launch.call_args.kwargs["cwd"], work)
            self.assertEqual(command[command.index("--chdir") + 1], str(work))
            self.assertEqual(command[command.index("--ro-bind") + 1:command.index("--ro-bind") + 3], ["/", "/"])
            self.assertLess(command.index("--remount-ro"), command.index("--bind"))
            self.assertEqual(command[command.index("--remount-ro") + 1], "/home/vpci")
            self.assertNotIn("--unshare-net", command)
            self.assertEqual(work.stat().st_mode & 0o777, 0o700)

    def test_runtime_parent_is_anonymous_readonly_without_permission_relaxation(self):
        runtime = Path("/home/vpci/browser-network-runtime")
        with patch.object(CORE, "ROOT", runtime), patch.object(CORE.Path, "home", return_value=Path("/opt/fixture/home")):
            self.assertEqual(CORE.isolated_runtime_parent(runtime / "build/firefox-esr", runtime / "build/proofs/session"),
                ["--tmpfs", "/home/vpci", "--ro-bind", str(runtime), str(runtime), "--remount-ro", "/home/vpci"])
            with self.assertRaises(ValueError):
                CORE.isolated_runtime_parent(Path("/outside/runtime"), runtime / "build/proofs/session")
            with self.assertRaises(ValueError):
                CORE.isolated_runtime_parent(runtime / "build/firefox-esr", Path("/outside/work"))
        with patch.object(CORE, "ROOT", runtime), patch.object(CORE.Path, "home", return_value=Path("/home/vpci")):
            with self.assertRaises(ValueError):
                CORE.isolated_runtime_parent(runtime / "build/firefox-esr", runtime / "build/proofs/session")

    def test_network_source_hook_keeps_existing_compute_and_rejects_ambiguous_anchors(self):
        source = "# Original MPL notice retained\nEXTRA_JS_MODULES += [\n" + SOURCE.ANCHOR + "]\n"
        result = SOURCE.transform(source)
        self.assertTrue(result.startswith("# Original MPL notice retained"))
        self.assertIn(SOURCE.ANCHOR, result)
        self.assertEqual(result.count('"VolparossaNetwork.sys.mjs"'), 1)
        for malformed in (source + SOURCE.ANCHOR, result, "changed upstream"):
            with self.assertRaises(ValueError):
                SOURCE.transform(malformed)

    def test_bounded_fixture_framing_and_headers(self):
        left, right = socket.socketpair()
        with left, right:
            SMOKE.send(left, {"version":1})
            self.assertEqual(SMOKE.receive(right), {"version":1})
            left.sendall(struct.pack("!I", 4097))
            with self.assertRaises(ValueError):
                SMOKE.receive(right)
        left, right = socket.socketpair()
        with left, right:
            left.sendall(b"CONNECT fixture.invalid:443 HTTP/1.1\r\nHost: fixture.invalid:443\r\n\r\n")
            self.assertEqual(SMOKE.request_headers(right),
                             ("CONNECT fixture.invalid:443 HTTP/1.1", {"host":"fixture.invalid:443"}))

    def test_core_grant_file_is_private_bounded_owner_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "grant.json"
            grant = dict(version=1,app_uid=os.getuid(),app_socket="/run/fixture.sock",capability="a"*64,
                hostname="fixture.invalid",port=443,partition="b"*64,expires_at_ms=int(time.time()*1000)+60000,
                overlay_only=True)
            path.write_text(json.dumps(grant))
            path.chmod(0o600)
            self.assertEqual(CORE.grant_file(path), grant)
            CORE.pinned_url("https://fixture.invalid/file", grant)
            for url in ("http://fixture.invalid/file", "https://other.invalid/file", "https://fixture.invalid:8443/file",
                        "https://user@fixture.invalid/file", "https://fixture.invalid/file#fragment"):
                with self.assertRaises(ValueError):
                    CORE.pinned_url(url, grant)
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                CORE.grant_file(path)
            path.chmod(0o600)
            alias = Path(directory) / "alias"
            alias.symlink_to(path)
            with self.assertRaises(ValueError):
                CORE.grant_file(alias)
            grant["unknown"] = True
            path.write_text(json.dumps(grant))
            with self.assertRaises(ValueError):
                CORE.grant_file(path)

    def test_core_result_requires_both_full_hashes_and_independent_detach(self):
        result = dict(independent_attachments=True, wrong_scope_blocked=True, a_detached=True,
            b_survives_a_detach=True, a={"bytes":33554432,"sha256_verified":True},
            b={"bytes":33554432,"sha256_verified":True})
        CORE.check_result(result, 33554432)
        for field in ("independent_attachments", "wrong_scope_blocked", "a_detached", "b_survives_a_detach"):
            invalid = copy.deepcopy(result)
            invalid[field] = False
            with self.assertRaises(ValueError):
                CORE.check_result(invalid, 33554432)
        for field in ("a", "b"):
            invalid = copy.deepcopy(result)
            invalid[field]["bytes"] -= 1
            with self.assertRaises(ValueError):
                CORE.check_result(invalid, 33554432)

    def test_scoped_adapter_has_no_preference_or_direct_fallback_mutation(self):
        source = (ROOT / "integration/VolparossaNetwork.sys.mjs").read_text()
        for required in ("internal.allowHttp3 = false", "internal.allowAltSvc = false",
                         "internal.beConservative = false", "internal.bypassProxy = false",
                         "ALWAYS_TUNNEL_VIA_PROXY", "channel.cancel(Cr.NS_ERROR_ABORT)",
                         'http.setRequestHeader("Proxy-Authorization", this._proxy.proxyAuthorizationHeader, false)',
                         "callback.onRedirectVerifyCallback(Cr.NS_ERROR_ABORT)"):
            self.assertIn(required, source)
        self.assertNotIn("Services.prefs.set", source)
        self.assertNotIn('newProxyInfo("direct"', source)
        self.assertNotIn("console.", source)
        self.assertNotIn("dump(", source)
        driver = (ROOT / "scripts/smoke_network_core.py").read_text()
        self.assertNotIn('"--unshare-net"', driver)
        self.assertIn('os.readlink("/proc/self/ns/net") != args.parent_netns', driver)
        self.assertIn('"--ro-bind", "/", "/"', driver)


if __name__ == "__main__":
    unittest.main()
