"""Offline packaging tests; synthetic ZIPs never establish Firefox signature validity."""

import copy
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bundle_extensions as bundle


def fixture(extra=(), mutate=None, entry_index=1, include_manifest=True):
    entry = copy.deepcopy(bundle.load_lock()["extensions"][entry_index])
    manifest = {
        "manifest_version": entry["manifest_version"], "version": entry["version"],
        "permissions": entry["permissions"], "host_permissions": entry["host_permissions"],
        "browser_specific_settings": {"gecko": {
            "id": entry["id"], "strict_min_version": entry["minimum_firefox"]}},
    }
    if entry["data_collection_permissions"] is not None:
        manifest["browser_specific_settings"]["gecko"]["data_collection_permissions"] = {
            "required": entry["data_collection_permissions"]}
    if mutate:
        mutate(manifest)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive, warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        if include_manifest:
            archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("META-INF/mozilla.rsa", b"synthetic envelope, NOT a signature")
        archive.writestr("META-INF/mozilla.sf", b"synthetic, never installed in Firefox")
        archive.writestr("LICENSE.txt", b"synthetic fixture only")
        for name, value in extra:
            archive.writestr(name, value)
    data = stream.getvalue()
    entry.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
    return data, entry


class BundledExtensionsTests(unittest.TestCase):
    def test_four_explicit_pins_and_preserved_additional_licenses(self):
        entries = bundle.load_lock()["extensions"]
        self.assertEqual([e["id"] for e in entries], ["uBlock0@raymondhill.net",
            "jid1-BoFifL9Vbdl2zQ@jetpack", "ATBC@EasonWong", "gdpr@cavi.au.dk"])
        self.assertEqual([e["version"] for e in entries], ["1.75.0", "3.0.2", "4.2.0", "1.1.5"])
        self.assertEqual([e["license"] for e in entries], ["GPL-3.0-only", "MPL-2.0", "MIT", "MIT"])
        self.assertEqual([e["data_collection_permissions"] for e in entries],
                         [["none"], ["none"], ["none"], None])
        self.assertEqual([e["sha256"] for e in entries], [
            "5b74415860456370644bd80f16125e865b0e6c356bb5dfcfb84069967eaa5287",
            "e749d0d1985b579332b36aa00d43bd11c9e820b1375701657cf9aceedbb47581",
            "4c83fa7322261e59a5f97bb51a10cd6ba6958e840ff75eec8cd27f153be1e0c6",
            "a2119abc329638d6e7af1ab4e5548a348465e02eec11de08dee0af84919923dc",
        ])
        for entry in entries:
            if "additional_notice" in entry:
                notice = (bundle.ROOT / entry["additional_notice"]).read_bytes()
                self.assertEqual(hashlib.sha256(notice).hexdigest(), entry["additional_notice_sha256"])

    def test_lock_requires_four_unique_pins(self):
        lock = bundle.load_lock()
        for entries in (lock["extensions"][:3], lock["extensions"] + [lock["extensions"][0]],
                        lock["extensions"][:3] + [lock["extensions"][0]]):
            invalid = dict(lock, extensions=entries)
            with patch.object(bundle.Path, "read_text", return_value=json.dumps(invalid)):
                with self.assertRaisesRegex(ValueError, "four unique"):
                    bundle.load_lock()

    def test_absent_collection_declaration_is_not_an_empty_or_none_declaration(self):
        data, entry = fixture(entry_index=3)
        self.assertEqual(bundle.verified_package(data, entry)["id"], "gdpr@cavi.au.dk")
        for declaration in (None, {}, {"required": []}, {"required": ["none"]},
                            {"required": ["browsingActivity"]}):
            with self.subTest(declaration=declaration):
                data, entry = fixture(entry_index=3, mutate=lambda m:
                    m["browser_specific_settings"]["gecko"].update(
                        data_collection_permissions=declaration))
                with self.assertRaisesRegex(ValueError, "data-collection declaration"):
                    bundle.verified_package(data, entry)

    def test_original_three_still_require_their_none_declaration(self):
        for index in range(3):
            data, entry = fixture(entry_index=index)
            bundle.verified_package(data, entry)
            for mutate in (
                lambda m: m["browser_specific_settings"]["gecko"].pop("data_collection_permissions"),
                lambda m: m["browser_specific_settings"]["gecko"].update(data_collection_permissions=None),
                lambda m: m["browser_specific_settings"]["gecko"].update(data_collection_permissions={}),
                lambda m: m["browser_specific_settings"]["gecko"].update(
                    data_collection_permissions={"required": []}),
            ):
                data, entry = fixture(entry_index=index, mutate=mutate)
                with self.subTest(id=entry["id"]), self.assertRaisesRegex(
                    ValueError, "data-collection declaration"
                ):
                    bundle.verified_package(data, entry)

    def test_consent_identity_compatibility_and_permissions_remain_exact(self):
        for mutate in (
            lambda m: m.update(version="1.1.6"),
            lambda m: m.update(manifest_version=2),
            lambda m: m["browser_specific_settings"]["gecko"].update(id="changed@example"),
            lambda m: m["browser_specific_settings"]["gecko"].update(strict_min_version="115.0"),
            lambda m: m.update(permissions=["storage"]),
            lambda m: m.update(host_permissions=[]),
            lambda m: m.update(optional_permissions=["history"]),
        ):
            data, entry = fixture(entry_index=3, mutate=mutate)
            with self.assertRaises(ValueError):
                bundle.verified_package(data, entry)
        data, entry = fixture(entry_index=3, include_manifest=False)
        with self.assertRaisesRegex(ValueError, "missing extension manifest"):
            bundle.verified_package(data, entry)

    def test_integrity_check_is_not_a_signature_verdict(self):
        data, entry = fixture()
        result = bundle.verified_package(data, entry)
        self.assertEqual(result["id"], entry["id"])
        self.assertNotIn("signature_validated", result)
        for corrupted in (data[:-1], data + b"extra", bytes([data[0] ^ 1]) + data[1:]):
            with self.assertRaises(ValueError):
                bundle.verified_package(corrupted, entry)

    def test_wrong_identity_version_and_permission_scope_rejected(self):
        for mutate in (
            lambda m: m.update(version="999"),
            lambda m: m["browser_specific_settings"]["gecko"].update(id="unexpected@example"),
            lambda m: m.update(permissions=["nativeMessaging"]),
            lambda m: m.update(host_permissions=["<all_urls>"]),
            lambda m: m.update(optional_permissions=["history"]),
        ):
            data, entry = fixture(mutate=mutate)
            with self.assertRaises(ValueError):
                bundle.verified_package(data, entry)

    def test_duplicate_traversal_absolute_and_symlink_zip_entries_rejected(self):
        link = zipfile.ZipInfo("linked")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        for name in ("../escape", "/absolute", "windows\\escape", "manifest.json", link):
            data, entry = fixture([(name, b"bad")])
            with self.subTest(name=str(name)), self.assertRaises(ValueError):
                bundle.verified_package(data, entry)

    def test_manifest_expansion_and_duplicate_json_are_bounded(self):
        data, entry = fixture()
        with patch.object(bundle, "MAX_UNPACKED", 1), self.assertRaises(ValueError):
            bundle.verified_package(data, entry)
        with patch.object(bundle, "MAX_MANIFEST", 1), self.assertRaises(ValueError):
            bundle.verified_package(data, entry)
        with self.assertRaises(ValueError):
            json.loads('{"version":1,"version":2}', object_pairs_hook=bundle.strict_object)

    def test_offline_install_preserves_all_four_packages_notices_and_no_policies(self):
        (bundle.ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="extensions-test-", dir=bundle.ROOT / "build") as name:
            directory = Path(name)
            cache, stage = directory / "cache", directory / "runtime"
            cache.mkdir()
            stage.mkdir()
            packages = [fixture(entry_index=index) for index in range(4)]
            for data, entry in packages:
                (cache / (entry["id"] + ".xpi")).write_bytes(data)
            lock = {"installation": "distribution/extensions",
                    "extensions": [entry for _, entry in packages]}
            with patch.object(bundle, "load_lock", return_value=lock):
                result = bundle.install(stage, cache)
                self.assertEqual(len(result["packages"]), 4)
                for result_entry, (data, entry) in zip(result["packages"], packages):
                    self.assertEqual(result_entry["sha256"], entry["sha256"])
                    copied = stage / "distribution/extensions" / (entry["id"] + ".xpi")
                    self.assertEqual(copied.read_bytes(), data)
                    if "additional_notice" in entry:
                        original = bundle.ROOT / entry["additional_notice"]
                        copied = stage / "distribution/licenses" / original.name
                        self.assertEqual(copied.read_bytes(), original.read_bytes())
                self.assertFalse((stage / "distribution/policies.json").exists())
                with self.assertRaises(ValueError):
                    bundle.install(stage, cache)

    def test_cache_symlink_rejected_even_if_target_bytes_match(self):
        (bundle.ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="extensions-link-", dir=bundle.ROOT / "build") as name:
            cache = Path(name)
            data, entry = fixture()
            target = cache / "actual.xpi"
            target.write_bytes(data)
            (cache / (entry["id"] + ".xpi")).symlink_to(target)
            with self.assertRaises(ValueError):
                bundle.read_package(cache, entry)

    def test_downloader_rejects_untrusted_redirect_without_contacting_it(self):
        handler = bundle.OfficialRedirects()
        for url in ("http://addons.mozilla.org/file.xpi", "https://example.com/file.xpi",
                    "https://user:password@addons.mozilla.org/file.xpi"):
            with self.assertRaises(ValueError):
                handler.redirect_request(None, None, 302, "", {}, url)


if __name__ == "__main__":
    unittest.main()
