#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Narrow source/driver contracts; not a replacement for the real Gecko/overlay proof."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import prepare_network_source as SOURCE
import smoke_network as SMOKE
import smoke_network_core as CORE


class NetworkIntegrationTests(unittest.TestCase):
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
