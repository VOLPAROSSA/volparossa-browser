#!/usr/bin/env python3
"""Explicitly fetch pinned AMO packages, or verify them offline before staging.

Never installs into a host Firefox/profile. ZIP metadata checks are not Mozilla
signature verification: the unchanged Firefox runtime must perform that check.
"""

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import urllib.parse
import urllib.request
import zipfile

from stage_firefox import ROOT, build_path

LOCK = ROOT / "defaults/extensions.lock.json"
CACHE = ROOT / "build/extensions-cache"
MAX_XPI = 16 * 1024 * 1024
MAX_UNPACKED = 128 * 1024 * 1024
MAX_MANIFEST = 1024 * 1024
ALLOWED_HOSTS = {"addons.mozilla.org", "addons.cdn.mozilla.net"}


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def load_lock():
    lock = json.loads(LOCK.read_text(), object_pairs_hook=strict_object)
    if lock["schema"] != 1 or lock["installation"] != "distribution/extensions":
        raise ValueError("unsupported extension lock schema")
    entries = lock["extensions"]
    if len(entries) != 3 or len({e["id"] for e in entries}) != len(entries):
        raise ValueError("expected three unique extension pins")
    for entry in entries:
        if not re.fullmatch(r"[A-Za-z0-9_{}@.+-]{1,128}", entry["id"]):
            raise ValueError("unsafe extension ID")
        if not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
            raise ValueError("invalid extension digest")
        if type(entry["size"]) is not int or not 0 < entry["size"] <= MAX_XPI:
            raise ValueError("unbounded extension package")
        url = urllib.parse.urlsplit(entry["url"])
        if (url.scheme != "https" or url.netloc != "addons.mozilla.org"
                or not re.fullmatch(r"/firefox/downloads/file/[0-9]+/[^/]+\.xpi", url.path)
                or url.query or url.fragment):
            raise ValueError("extension URL must pin an exact official AMO file")
    return lock


def verified_package(data, entry):
    if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise ValueError(f"extension size/hash mismatch: {entry['id']}")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        names = [item.filename for item in infos]
        if not 1 <= len(infos) <= 10000 or len(set(names)) != len(names):
            raise ValueError("duplicate or excessive ZIP entries")
        total = 0
        for item in infos:
            path = PurePosixPath(item.filename)
            mode = item.external_attr >> 16
            if (not item.filename or path.is_absolute() or ".." in path.parts
                    or "\\" in item.filename or "\x00" in item.filename
                    or any(ord(char) < 32 for char in item.filename)
                    or stat.S_ISLNK(mode) or item.flag_bits & 1
                    or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                raise ValueError("unsafe ZIP entry")
            total += item.file_size
            if item.file_size > 32 * 1024 * 1024 or total > MAX_UNPACKED:
                raise ValueError("unbounded ZIP expansion")
        # Preserve each original package byte-for-byte. Do not unpack/repack signed XPIs.
        if not {"META-INF/mozilla.rsa", "META-INF/mozilla.sf"}.issubset(names):
            raise ValueError("missing signature envelope; not a signature validity verdict")
        if archive.getinfo("manifest.json").file_size > MAX_MANIFEST:
            raise ValueError("unbounded extension manifest")
        manifest = json.loads(archive.read("manifest.json"), object_pairs_hook=strict_object)
        gecko = manifest.get("browser_specific_settings", {}).get("gecko", {})
        if (gecko.get("id") != entry["id"] or manifest.get("version") != entry["version"]
                or manifest.get("manifest_version") != entry["manifest_version"]
                or gecko.get("strict_min_version") != entry["minimum_firefox"]):
            raise ValueError("extension identity/version/compatibility mismatch")
        # AMO includes content-script matches in its effective permission list.
        permissions = set(manifest.get("permissions", []))
        for script in manifest.get("content_scripts", []):
            permissions.update(script.get("matches", []))
        if permissions != set(entry["permissions"]):
            raise ValueError("extension permissions differ from reviewed pin")
        for field in ("host_permissions", "optional_permissions"):
            if set(manifest.get(field, [])) != set(entry[field]):
                raise ValueError("extension permission scope mismatch")
        if gecko.get("data_collection_permissions", {}).get("required") != entry["data_collection_permissions"]:
            raise ValueError("extension data-collection declaration mismatch")
        if not set(entry["license_files"]).issubset(names):
            raise ValueError("missing original extension license")
        return {"id": entry["id"], "version": entry["version"], "sha256": entry["sha256"],
                "size": len(data), "zip_entries": len(infos), "unpacked_bytes": total}


def read_package(cache, entry):
    path = cache / (entry["id"] + ".xpi")
    if not stat.S_ISREG(path.lstat().st_mode) or path.stat().st_size != entry["size"]:
        raise ValueError("package must be a regular file with its pinned size")
    with path.open("rb") as stream:
        data = stream.read(entry["size"] + 1)
    return data, verified_package(data, entry)


class OfficialRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, url):
        target = urllib.parse.urlsplit(url)
        if (target.scheme != "https" or target.hostname not in ALLOWED_HOSTS
                or target.username or target.password or target.port not in (None, 443)):
            raise ValueError("refusing non-official or non-HTTPS download redirect")
        return super().redirect_request(request, file, code, message, headers, url)


def fetch(cache, lock):
    cache = build_path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(OfficialRedirects())
    for entry in lock["extensions"]:
        target = cache / (entry["id"] + ".xpi")
        if target.exists() or target.is_symlink():
            read_package(cache, entry)
            continue
        with opener.open(entry["url"], timeout=30) as response:
            length = response.headers.get("Content-Length")
            if length is not None and int(length) != entry["size"]:
                raise ValueError("download Content-Length differs from pin")
            data = response.read(entry["size"] + 1)
        verified_package(data, entry)
        # A failed fetch never installs or replaces anything. Existing files are immutable.
        with target.open("xb") as output:
            output.write(data)
    return verify(cache, lock)


def verify(cache, lock):
    cache = build_path(cache)
    return [read_package(cache, entry)[1] for entry in lock["extensions"]]


def install(destination, cache):
    """Offline staging into a newly copied, workspace-confined GRE only."""
    destination, cache = build_path(destination), build_path(cache)
    lock = load_lock()
    packages = [(entry, *read_package(cache, entry)) for entry in lock["extensions"]]
    distribution = destination / "distribution"
    if distribution.exists() or distribution.is_symlink():
        raise ValueError("refusing to replace an existing distribution directory")
    notices = []
    for entry, _, _ in packages:
        if "additional_notice" in entry:
            notice = (ROOT / entry["additional_notice"]).resolve(strict=True)
            if not notice.is_relative_to(ROOT / "docs/licenses"):
                raise ValueError("notice is outside project license directory")
            data = notice.read_bytes()
            if hashlib.sha256(data).hexdigest() != entry["additional_notice_sha256"]:
                raise ValueError("third-party license notice hash mismatch")
            notices.append((notice.name, data))
    (distribution / "extensions").mkdir(parents=True)
    for entry, data, _ in packages:
        (distribution / "extensions" / (entry["id"] + ".xpi")).write_bytes(data)
    (distribution / "licenses").mkdir()
    for name, data in notices:
        (distribution / "licenses" / name).write_bytes(data)
    (distribution / "extensions.lock.json").write_bytes(LOCK.read_bytes())
    return {"lock_sha256": hashlib.sha256(LOCK.read_bytes()).hexdigest(),
            "installation": lock["installation"], "packages": [p[2] for p in packages],
            "signature_verification": "deferred to unmodified Firefox; never bypassed"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("fetch", "verify"))
    parser.add_argument("--cache", default=str(CACHE))
    args = parser.parse_args()
    result = globals()[args.action](args.cache, load_lock())
    print(json.dumps({"packages": result, "signature_validated": False}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(f"extension bundle failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
