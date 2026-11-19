#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Exact source-only Gecko TCP ECH option; no compiler, runtime or preference changes."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REVISION = "47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1"
BASE = f"https://raw.githubusercontent.com/mozilla-firefox/firefox/{REVISION}/"
PREFIX = "netwerk/protocol/http/"
PIN = ROOT / "patches/firefox-network-ech.json"
PATCH = ROOT / "patches/0002-channel-ech.patch"
MAX_FILE = 2 * 1024 * 1024

# Source transformations are explicit, count-checked and additionally SHA-bound.
# Native files retain MPL-2.0; the existing native test retains its public-domain notice.
EDITS = {
    PREFIX + "nsIHttpChannelInternal.idl": [
        ('uuid(4e28263d-1e03-46f4-aa5c-9512f91957f9)', 'uuid(2d60b61a-d4ce-4e35-991d-e3cfc85b7ed0)', 1),
        ('    [must_use] attribute boolean allowHttp3;\n', '''    [must_use] attribute boolean allowHttp3;

    /**
     * Parent-process privileged control of ECH and ECH GREASE on this channel.
     * Defaults to true. Set before connection preparation; false requires
     * allowHttp3=false and allowAltSvc=false. This TCP-only option does not
     * change TLS versions, certificate validation, DNS or global preferences.
     * Disabled ECH has a separate connection-pool key, including IPC/clones.
     */
    [must_use] attribute boolean allowECH;
''', 1),
    ],
    PREFIX + "HttpBaseChannel.h": [
        ('  NS_IMETHOD SetAllowHttp3(bool aAllowHttp3) override;\n',
         '  NS_IMETHOD SetAllowHttp3(bool aAllowHttp3) override;\n  NS_IMETHOD GetAllowECH(bool* aAllowECH) override;\n  NS_IMETHOD SetAllowECH(bool aAllowECH) override;\n', 1),
        ('    (uint32_t, AllowHttp3, 1),\n', '    (uint32_t, AllowHttp3, 1),\n    (uint32_t, AllowECH, 1),\n', 1),
    ],
    PREFIX + "HttpBaseChannel.cpp": [
        ('  StoreAllowHttp3(true);\n', '  StoreAllowHttp3(true);\n  StoreAllowECH(true);\n', 1),
        ('NS_IMETHODIMP\nHttpBaseChannel::GetAllowAltSvc(bool* aAllowAltSvc) {', '''NS_IMETHODIMP
HttpBaseChannel::GetAllowECH(bool* aAllowECH) {
  NS_ENSURE_ARG_POINTER(aAllowECH);
  *aAllowECH = LoadAllowECH();
  return NS_OK;
}

NS_IMETHODIMP
HttpBaseChannel::SetAllowECH(bool aAllowECH) {
  if (!XRE_IsParentProcess()) {
    return NS_ERROR_NOT_AVAILABLE;
  }
  ENSURE_CALLED_BEFORE_CONNECT();
  // No child-controlled IPC property and no implied QUIC/ECH implementation.
  if (!aAllowECH && (LoadAllowHttp3() || LoadAllowAltSvc())) {
    return NS_ERROR_NOT_AVAILABLE;
  }
  StoreAllowECH(aAllowECH);
  return NS_OK;
}

NS_IMETHODIMP
HttpBaseChannel::GetAllowAltSvc(bool* aAllowAltSvc) {''', 1),
        ('    rv = httpInternal->SetAllowAltSvc(LoadAllowAltSvc());\n    MOZ_ASSERT(NS_SUCCEEDED(rv));\n',
         '''    rv = httpInternal->SetAllowAltSvc(LoadAllowAltSvc());
    MOZ_ASSERT(NS_SUCCEEDED(rv));
    // Child channels cannot opt out; propagate an explicit parent decision only.
    if (!LoadAllowECH()) {
      rv = httpInternal->SetAllowECH(false);
      NS_ENSURE_SUCCESS(rv, rv);
    }
''', 1),
    ],
    PREFIX + "nsHttpChannel.cpp": [
        ('  // Finalize ConnectionInfo flags before SpeculativeConnect\n',
         '''  // Finalize ConnectionInfo flags before SpeculativeConnect
  if (!LoadAllowECH() && mConnectionInfo->IsHttp3()) {
    return NS_ERROR_NOT_AVAILABLE;
  }
  mConnectionInfo->SetNoEch(!LoadAllowECH());
''', 1),
        ('               NS_HTTP_DISALLOW_HTTP3 | NS_HTTP_REFRESH_DNS),\n',
         '               NS_HTTP_DISALLOW_HTTP3 | NS_HTTP_DISALLOW_ECH | NS_HTTP_REFRESH_DNS),\n', 1),
        ('  // Construct connection info object\n', '''  // Parent-selected TCP-only ECH policy must be fixed before any TLS attempt.
  if (!LoadAllowECH()) {
    if (LoadAllowHttp3() || LoadAllowAltSvc()) {
      return NS_ERROR_NOT_AVAILABLE;
    }
    mCaps |= NS_HTTP_DISALLOW_ECH;
  }

  // Construct connection info object
''', 1),
    ],
    PREFIX + "nsHttpConnectionInfo.h": [
        ('    HappyEyeballs,\n    End,\n', '    HappyEyeballs,\n    NoEch,\n    End,\n', 1),
        ('  void SetBeConservative(bool aBeConservative) {\n', '''  void SetNoEch(bool aNoEch) {
    SetHashCharAt(aNoEch ? 'E' : '.', HashKeyIndex::NoEch);
  }
  bool GetNoEch() const { return GetHashCharAt(HashKeyIndex::NoEch) == 'E'; }

  void SetBeConservative(bool aBeConservative) {
''', 1),
    ],
    PREFIX + "nsHttpConnectionInfo.cpp": [
        ('  // byte 10 is H/. H is for indicating HappyEyeballs is used\n',
         '  // byte 10 is H/. H is for indicating HappyEyeballs is used\n  // byte 11 is E/. E explicitly disables ECH and GREASE for this connection\n', 1),
        ('HashKeyIndex::End) == 11,', 'HashKeyIndex::End) == 12,', 1),
        ('mHashKey.AssignLiteral("...........[tlsflags0x00000000]");',
         'mHashKey.AssignLiteral("............[tlsflags0x00000000]");', 1),
        ('  bool isNoSpdy = GetNoSpdy();\n', '  bool isNoSpdy = GetNoSpdy();\n  bool isNoEch = GetNoEch();\n', 1),
        ('  SetNoSpdy(isNoSpdy);\n', '  SetNoSpdy(isNoSpdy);\n  SetNoEch(isNoEch);\n', 1),
        ('  clone->SetPrivate(GetPrivate());\n', '  clone->SetPrivate(GetPrivate());\n  clone->SetNoEch(GetNoEch());\n', 5),
        ('  aArgs.noSpdy() = aInfo->GetNoSpdy();\n', '  aArgs.noSpdy() = aInfo->GetNoSpdy();\n  aArgs.noEch() = aInfo->GetNoEch();\n', 1),
        ('  cinfo->SetNoSpdy(aInfoArgs.noSpdy());\n', '  cinfo->SetNoSpdy(aInfoArgs.noSpdy());\n  cinfo->SetNoEch(aInfoArgs.noEch());\n', 1),
    ],
    "netwerk/ipc/NeckoChannelParams.ipdlh": [
        ('  bool noSpdy;\n  bool beConservative;\n', '  bool noSpdy;\n  bool noEch;\n  bool beConservative;\n', 1),
    ],
    PREFIX + "ConnectionEstablisher.cpp": [
        ('  if (mCaps & NS_HTTP_DISALLOW_ECH) {\n',
         '  if ((mCaps & NS_HTTP_DISALLOW_ECH) || mConnInfo->GetNoEch()) {\n', 1),
    ],
    PREFIX + "DnsAndConnectSocket.cpp": [
        ('  if (dnsAndSock->mCaps & NS_HTTP_DISALLOW_ECH) {\n',
         '  if ((dnsAndSock->mCaps & NS_HTTP_DISALLOW_ECH) || ci->GetNoEch()) {\n', 1),
    ],
    "netwerk/test/unit/test_ech_grease.js": [
        ('function startClient(port, useGREASE, beConservative) {',
         'function startClient(port, useGREASE, beConservative, allowECH = true) {', 1),
        ('  if (beConservative) {\n    // We don\'t have a way to set DONT_TRY_ECH at the moment.\n', '''  const internal = req.channel.QueryInterface(Ci.nsIHttpChannelInternal);
  equal(internal.allowECH, true, "new channels preserve ordinary ECH defaults");
  if (!allowECH) {
    internal.allowHttp3 = false;
    internal.allowAltSvc = false;
    internal.allowECH = false;
    equal(internal.allowECH, false, "explicit parent channel opt-out");
  }

  if (beConservative) {
''', 1),
        ('  if (useGREASE && !beConservative) {\n', '  if (useGREASE && !beConservative && allowECH) {\n', 1),
        ('      resolve();\n    };\n    req.onerror =',
         '      resolve(internal.connectionInfoHashKey);\n    };\n    req.onerror =', 1),
        ('registerCleanupFunction(function () {\n', '''add_task(async function ExplicitChannelNoEchDoesNotChangeTheNextChannel() {
  const server = await startServer();
  try {
    const withoutEch = await startClient(server.port, true, false, false);
    // Same endpoint and global GREASE=100: no persistent/global opt-out.
    const withEch = await startClient(server.port, true, false, true);
    notEqual(withoutEch, withEch, "ECH policy separates connection pool keys");
    equal(withoutEch.charAt(11), "E", "explicit NoEch key bit");
    equal(withEch.charAt(11), ".", "default ECH key bit");
  } finally {
    server.close();
  }
});

registerCleanupFunction(function () {
''', 1),
    ],
}


def transform(path, source):
    if path not in EDITS:
        raise ValueError("unknown_native_source")
    if any(new in source for _old, new, _count in EDITS[path]):
        raise ValueError("native_source_already_patched")
    for old, new, count in EDITS[path]:
        if source.count(old) != count:
            raise ValueError(f"native_source_anchor_mismatch:{path}")
        source = source.replace(old, new)
    return source


def source_patch(originals):
    if set(originals) != set(EDITS):
        raise ValueError("native_source_set_mismatch")
    return "".join("".join(difflib.unified_diff(originals[path].splitlines(keepends=True),
        transform(path, originals[path]).splitlines(keepends=True), fromfile="a/" + path, tofile="b/" + path))
        for path in EDITS)


def fetch_originals():
    pinned = json.loads(PIN.read_text())
    if pinned["upstream_revision"] != REVISION or set(pinned["upstream_sha256"]) != set(EDITS):
        raise ValueError("native_source_pin_mismatch")
    sources = {}
    for path in EDITS:
        with urllib.request.urlopen(BASE + path, timeout=30) as response:
            raw = response.read(MAX_FILE)
            if response.read(1):
                raise ValueError("native_source_oversize")
        if hashlib.sha256(raw).hexdigest() != pinned["upstream_sha256"][path]:
            raise ValueError(f"native_source_hash_mismatch:{path}")
        sources[path] = raw.decode("utf-8", "strict")
    return sources


def stage_into(output, originals=None):
    """Existing source-overlay directory only; refuse to replace any native file."""
    output = Path(output).resolve(strict=True)
    if not output.is_relative_to(ROOT / "build") or output == ROOT / "build":
        raise ValueError("native_stage_outside_build")
    originals = fetch_originals() if originals is None else originals
    patch = source_patch(originals)
    pinned = json.loads(PIN.read_text())
    if patch != PATCH.read_text() or hashlib.sha256(patch.encode()).hexdigest() != pinned["patch_sha256"]:
        raise ValueError("native_patch_mismatch")
    records = {}
    for path, source in originals.items():
        original_hash = hashlib.sha256(source.encode()).hexdigest()
        if original_hash != pinned["upstream_sha256"][path]:
            raise ValueError("native_stage_hash_mismatch")
        patched = transform(path, source)
        for kind, data in (("original", source), ("patched", patched)):
            destination = output / kind / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("x") as stream:
                stream.write(data)
        records[path] = {"original_sha256": original_hash,
                        "patched_sha256": hashlib.sha256(patched.encode()).hexdigest()}
    with (output / "channel-ech.patch").open("x") as stream:
        stream.write(patch)
    report = {"version": 1, "upstream_revision": REVISION, "kind": "native-ech-source-overlay-unbuilt",
              "patch_sha256": pinned["patch_sha256"], "files": records,
              "native_build_proven": False, "native_ech_wire_proven": False,
              "default_allow_ech": True, "parent_process_only": True, "tcp_only": True}
    with (output / "channel-ech.json").open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "build") or output == ROOT / "build" or output.exists():
        raise ValueError("output_must_be_new_build_child")
    output.mkdir(parents=True)
    print(json.dumps(stage_into(output)))
