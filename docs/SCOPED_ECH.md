# Per-channel ECH — native build and scoped TLS/GREASE proof

VOLPAROSSA's current exit authorizes HTTPS against visible SNI and rejects both
real ECH and ECH GREASE. Disabling those globally would weaken unrelated browsing.
This candidate instead adds `nsIHttpChannelInternal.allowECH` at exact Firefox
source `47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1` (the 157 source target).

## Native boundary

- New channels default to **true**: existing ECH preferences and behavior remain.
- Only privileged parent-process code can set the attribute, before connection
  preparation. There is no web API or child-controlled channel IPC field.
- The TCP-only opt-out requires HTTP/3 and Alt-Svc disabled. A resolved HTTP/3
  connection is rejected, not silently called an ECH-controlled TCP connection.
- The VOLPAROSSA attachment sets `allowECH=false` only after its channel scope
  passes validation and before inserting its proxy credential. A missing or
  ineffective native property fails closed. No `tlsFlags`, conservative-mode
  shortcut, certificate exception or global privacy preference is substituted.
- A dedicated `NoEch` connection-pool key follows rebuilds, all five clone paths
  including wildcard, and socket-process IPC. Speculative and ordinary TCP setup
  both translate it to the existing `DONT_TRY_ECH` socket flag. NSS already uses
  that flag to suppress GREASE; socket transport also skips real ECH configs.

The product module still disables HTTP/3, Alt-Svc and HTTP/2 coalescing on its
owned channels. Ordinary channels, including newly authorized ordinary fallback
requests, are never passed through this native opt-out. This does not add an
HTTP/3 implementation or relax the exit's destination policy.

## Exact source and checks

`patches/firefox-network-ech.json` records the ten original SHA-256 hashes,
upstream revision, licenses and patch hash. `patches/0002-channel-ech.patch`
preserves the original native MPL-2.0 notices and the existing GREASE test's
public-domain notice. Original project staging/adapter code remains GPL-3.0-only.

```sh
python3 -B tests/test_network_ech.py
node --test tests/network_attachment.test.cjs
python3 -B scripts/prepare_network_source.py --output build/network-source-native-ech
```

The combined source overlay and native-only `prepare_network_ech.py` stage exact,
hash-verified originals plus patched files; no compiler or browser download runs.
The upstream GREASE test now includes an explicit opt-out, a subsequent default
channel with GREASE still enabled, and distinct pool-key assertions. On
2026-10-01 the exact combined C++/IDL/IPDL source built successfully, including
Firefox, libxul and xpcshell. The rebuilt xpcshell passed all five tasks in that
modified fixture with
`network.http.network_access_on_socket_process.enabled=false` explicitly set.
That is local native TLS/GREASE and pool-key evidence, not a raw ClientHello
capture, socket-process/IPC proof or ordinary-tab payload over the real core route.

Observed source staging: `build/network-source-native-ech-02`; exact patch applies
with `patch --dry-run --fuzz=0`. Five focused source/fixture checks and twelve
simulated transport/clock adapter checks pass. They do not replace a native build.

The separate completed build is `build/native-firefox-157`; its
`build-result.json` records exact source, overlay and binary hashes. The runtime
result preserved in `parent-testsummary-20261001.jsonl` is `PASS`, return code 0, at
2026-10-01 19:14:37 UTC. [Native build details](NATIVE_BUILD.md) describe the
verified toolchains, offline build, isolated HOME startup correction and unchanged
host DNS/routes. The ECH patch itself was not changed to obtain that result.
`native_ech_wire_proven` remains false until the separate wire boundary is proved.
Socket-process attempts 02 and 03 timed out during process launch, before visible
JavaScript assertions, and observed no socket child; they remain failed evidence.
Attempt 04 retained the same binary/patch and ordinary startup profiler, supplying
only a private workspace `MOZ_UPLOAD_DIR` to avoid the source-identified download
directory/launch-lock cycle. With socket-process networking enabled it passed
**28/28 subtests**, return code 0, at **2026-10-01 19:35:17 UTC**. An independent
observer recorded a real `plugin-container` child with process type `socket`.
The result, process observation, log and stable testsummary are retained under
`build/native-firefox-157/` with the `20261001-04` suffix; exact hashes and the
startup diagnosis are in the [native build record](NATIVE_BUILD.md).

This adds native TLS/ABI/pool-key evidence with an actual socket process, **not
GREASE-wire proof**: the existing test skips handshake-telemetry assertions in
socket mode. ECH-specific IPC enforcement, raw ClientHello bytes and ordinary
browser traffic over the real core route still require their own observation.
The earlier failures and parent-process PASS have not been overwritten or
promoted into broader evidence.

## Unmodified ESR test boundary

ESR 140.16 does **not** contain this ABI. The test driver therefore creates an
explicit temporary compatibility copy with exactly two native-control calls
omitted; it does not pretend that assigning a JavaScript property changes NSS.
Both source and loaded-copy SHA-256 hashes are fixed in `smoke_network.py`, and a
different source or ESR stamp is rejected. The temporary copy lives beneath the
disposable profile's work directory and is removed with it. Reports include
`fixture_module_overlay` and `native_ech_wire_proven:false`.

`build/network-native-ech-compat-01/report.json` passes real ordinary-tab navigation,
four synthetic-origin TLS 1.3 responses, origin credential absence and full
browser/profile/socket cleanup using that explicitly modified test copy. It is
**not native ECH or core-overlay proof**. The synthetic origin accepts GREASE;
the separate core fixture sets GREASE probability to zero only in its disposable
profile, reported as `profile_ech_grease_disabled:true`.

The earlier [actual core run 36776940049](https://github.com/VOLPAROSSA/volparossa/actions/runs/36776940049)
passed with **browser `198e288183b06d8a4ff584210ade449f124bc737`** and core
`b8a1dd6e52978c40ced4c92c006f8301a587c659`: two explicit 32 MiB Gecko requests
over genuine carrying MPTCP/WireGuard paths, independent detach and cleanup.
That proof used the older module and a GREASE-disabled test profile. It does not
prove this later native patch, ordinary tabs over the real overlay, general
availability fallback, or a browser-wide kill switch.

Primary source seams at the pinned revision:

- [Channel interface](https://github.com/mozilla-firefox/firefox/blob/47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1/netwerk/protocol/http/nsIHttpChannelInternal.idl) and [connection preparation](https://github.com/mozilla-firefox/firefox/blob/47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1/netwerk/protocol/http/nsHttpChannel.cpp).
- [TCP connection setup](https://github.com/mozilla-firefox/firefox/blob/47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1/netwerk/protocol/http/ConnectionEstablisher.cpp), [socket ECH-config gate](https://github.com/mozilla-firefox/firefox/blob/47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1/netwerk/base/nsSocketTransport2.cpp), and [NSS GREASE gate](https://github.com/mozilla-firefox/firefox/blob/47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1/security/manager/ssl/nsNSSIOLayer.cpp).
- [Existing native GREASE test](https://github.com/mozilla-firefox/firefox/blob/47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1/netwerk/test/unit/test_ech_grease.js).
