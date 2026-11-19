#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Offline source/staging contracts, not compiled Gecko or native ECH wire proof."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import prepare_network_ech as ECH
import smoke_network as SMOKE


class NativeEchContracts(unittest.TestCase):
    def test_committed_patch_is_exactly_hash_bound_and_source_not_build(self):
        pin = json.loads(ECH.PIN.read_text())
        self.assertEqual(pin["upstream_revision"], ECH.REVISION)
        self.assertEqual(set(pin["upstream_sha256"]), set(ECH.EDITS))
        self.assertEqual(hashlib.sha256(ECH.PATCH.read_bytes()).hexdigest(), pin["patch_sha256"])
        self.assertFalse(pin["native_build_proven"])
        self.assertFalse(pin["esr140_native_abi_present"])
        self.assertEqual(pin["licenses"]["native_sources"], "MPL-2.0")
        self.assertEqual(pin["licenses"]["existing_native_test"], "CC0-1.0")

    def test_transforms_reject_missing_duplicated_or_already_patched_anchors(self):
        for path, edits in ECH.EDITS.items():
            with self.subTest(path=path):
                source = "\n".join(old * count for old, _new, count in edits)
                transformed = ECH.transform(path, source)
                self.assertNotEqual(source, transformed)
                with self.assertRaises(ValueError):
                    ECH.transform(path, source + edits[0][0])
                with self.assertRaises(ValueError):
                    ECH.transform(path, "")
                with self.assertRaises(ValueError):
                    ECH.transform(path, transformed)

    def test_scope_defaults_and_native_pipeline_are_explicit(self):
        change = ECH.PATCH.read_text()
        for fragment in (
            "+  StoreAllowECH(true);", "+  if (!XRE_IsParentProcess()) {",
            "+  ENSURE_CALLED_BEFORE_CONNECT();", "+    [must_use] attribute boolean allowECH;",
            "+  if (!aAllowECH && (LoadAllowHttp3() || LoadAllowAltSvc())) {",
            "+  if (!LoadAllowECH() && mConnectionInfo->IsHttp3()) {",
            "+    mCaps |= NS_HTTP_DISALLOW_ECH;",
            "+  if ((mCaps & NS_HTTP_DISALLOW_ECH) || mConnInfo->GetNoEch()) {",
            "+  if ((dnsAndSock->mCaps & NS_HTTP_DISALLOW_ECH) || ci->GetNoEch()) {",
            "+  bool noEch;", "+  aArgs.noEch() = aInfo->GetNoEch();",
            "+  cinfo->SetNoEch(aInfoArgs.noEch());",
            "+  bool isNoEch = GetNoEch();", "+  SetNoEch(isNoEch);",
            "+    NoEch,", "+  mConnectionInfo->SetNoEch(!LoadAllowECH());",
        ):
            self.assertIn(fragment, change)
        self.assertEqual(change.count("+  clone->SetNoEch(GetNoEch());"), 5)
        self.assertIn("ExplicitChannelNoEchDoesNotChangeTheNextChannel", change)
        added = "\n".join(line for line in change.splitlines() if line.startswith("+"))
        self.assertNotIn("SetTlsFlags", added)
        self.assertNotIn("BE_CONSERVATIVE", added)
        self.assertNotIn("prefs.set", added)

    def test_fixture_copy_is_explicitly_pinned_and_cannot_bypass_the_product_module(self):
        metadata = dict(version="140.16.0", source_stamp="d864999404b3032f682d74ccc60d1ce38c9ce609")
        original = ROOT / "integration/VolparossaNetwork.sys.mjs"
        before = original.read_bytes()
        with tempfile.TemporaryDirectory() as name:
            work = Path(name)
            (work / "tmp").mkdir(mode=0o700)
            directory, record = SMOKE.fixture_modules(work, metadata)
            self.assertTrue(record["fixture_only"])
            self.assertFalse(record["native_ech_wire_proven"])
            self.assertEqual(record["source_sha256"], hashlib.sha256(before).hexdigest())
            self.assertEqual(record["loaded_sha256"], hashlib.sha256((directory / original.name).read_bytes()).hexdigest())
            self.assertNotEqual(record["source_sha256"], record["loaded_sha256"])
            self.assertEqual((directory / original.name).read_text().count("    enforceChannelECH(internal);"), 0)
            self.assertEqual(original.read_bytes(), before)
            self.assertEqual(original.read_text().count("    enforceChannelECH(internal);"), 2)
            with self.assertRaises(FileExistsError):
                SMOKE.fixture_modules(work, metadata)
            with self.assertRaises(ValueError):
                SMOKE.fixture_modules(work, metadata | {"version": "157.0.1"})
            with patch.object(SMOKE, "ESR_FIXTURE_SOURCE_SHA256", "0" * 64), self.assertRaises(ValueError):
                SMOKE.fixture_modules(work, metadata)

    def test_no_unknown_file_or_stage_escape(self):
        with self.assertRaises(ValueError):
            ECH.transform("../unknown", "")
        with self.assertRaises(ValueError):
            ECH.source_patch({})
        with tempfile.TemporaryDirectory() as name, self.assertRaises(ValueError):
            ECH.stage_into(Path(name), {})


if __name__ == "__main__":
    unittest.main()
