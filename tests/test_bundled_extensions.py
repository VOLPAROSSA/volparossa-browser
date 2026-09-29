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


def fixture(extra=(), mutate=None):
    entry = copy.deepcopy(bundle.load_lock()["extensions"][1])
    manifest = {
        "manifest_version": entry["manifest_version"], "version": entry["version"],
        "permissions": entry["permissions"], "host_permissions": entry["host_permissions"],
        "browser_specific_settings": {"gecko": {
            "id": entry["id"], "strict_min_version": entry["minimum_firefox"],
            "data_collection_permissions": {"required": ["none"]}}},
    }
    if mutate:
        mutate(manifest)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive, warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
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
    def test_three_explicit_pins_and_preserved_additional_license(self):
        entries = bundle.load_lock()["extensions"]
        self.assertEqual([e["version"] for e in entries], ["1.75.0", "3.0.2", "4.2.0"])
        self.assertEqual([e["license"] for e in entries], ["GPL-3.0-only", "MPL-2.0", "MIT"])
        for entry in entries:
            if "additional_notice" in entry:
                notice = (bundle.ROOT / entry["additional_notice"]).read_bytes()
                self.assertEqual(hashlib.sha256(notice).hexdigest(), entry["additional_notice_sha256"])

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

    def test_offline_install_preserves_bytes_and_refuses_existing_distribution(self):
        (bundle.ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="extensions-test-", dir=bundle.ROOT / "build") as name:
            directory = Path(name)
            cache, stage = directory / "cache", directory / "runtime"
            cache.mkdir()
            stage.mkdir()
            data, entry = fixture()
            (cache / (entry["id"] + ".xpi")).write_bytes(data)
            lock = {"installation": "distribution/extensions", "extensions": [entry]}
            with patch.object(bundle, "load_lock", return_value=lock):
                result = bundle.install(stage, cache)
                self.assertEqual(result["packages"][0]["sha256"], entry["sha256"])
                copied = stage / "distribution/extensions" / (entry["id"] + ".xpi")
                self.assertEqual(copied.read_bytes(), data)
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
