# Scoped HTTPS/TCP gateway — first executable slice

`integration/VolparossaNetwork.sys.mjs` connects explicitly authorized Firefox
channels to the core's application gateway v1. It is **not** a global browser proxy,
ordinary-browsing integration, availability fallback, or complete kill switch.
The 18 privacy defaults and other users of the core are unchanged.

## Boundary and use

An operator creates a new single-use, owner-private grant with the core CLI. A
privileged caller supplies that grant object to `await VolparossaNetwork.attach(grant)`
and opens a fresh HTTPS channel using `attachment.openChannel(channel, listener)`.
This API must never be exposed to page scripts. The caller owns the channel and
listener, and calls `attachment.close()` when finished.

The separate `.apps` Unix socket uses one bounded, length-prefixed JSON request
and a validated Ready response. Only the capability and partition are sent at
attachment; the privileged grant pins the exact hostname, port and expiry. The
core checks the operator-approved UID using peer credentials. This is not proof
of a particular executable's identity or a sandbox against all same-UID programs.
No admin credential is given to the browser. The held socket owns one independent
proxy/route; EOF revokes it, without disconnecting another attachment.

Each bound channel uses authenticated CONNECT to the pinned loopback gateway,
with a unique connection-isolation key. HTTP, raw-IP grants, other authorities,
redirects and direct fallback are rejected. HTTP/3, HTTP/2 coalescing and Alt-Svc
are disabled **only on those channels**. The browser retains normal origin TLS
validation; CONNECT 200 alone is not proof that the origin connected successfully.
The adapter does not log grants, headers or URLs and never stores capabilities in
preferences/history. Requests bypass the browser response cache in this first
transport proof; shared-cache integration remains separate.

### Firefox-specific proxy authentication

For the pinned Firefox source, `nsIProxyInfo.proxyAuthorizationHeader` is consumed
only for HTTPS/MASQUE **proxies**, not a plain local HTTP proxy. The adapter
therefore sets `Proxy-Authorization` on its strictly HTTPS-only channel. Firefox's
native CONNECT builder copies that header to CONNECT; its origin-request serializer
removes proxy headers before sending the inner TLS request. No redirects, ordinary
HTTP or conservative direct-failover path is allowed for these channels.

Primary source, all at `47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1`:

- [Proxy service and channel filter](https://github.com/mozilla-firefox/firefox/blob/47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1/netwerk/base/nsIProtocolProxyService.idl): per-channel proxy selection and connection isolation.
- [Proxy authorization handling](https://github.com/mozilla-firefox/firefox/blob/47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1/netwerk/protocol/http/nsHttpChannelAuthProvider.cpp): `SetAuthorizationHeader` limits the proxy-info field to TLS/MASQUE proxies.
- [CONNECT construction](https://github.com/mozilla-firefox/firefox/blob/47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1/netwerk/protocol/http/nsHttpConnection.cpp): `SetupProxyConnect` copies the proxy credential.
- [Request serialization](https://github.com/mozilla-firefox/firefox/blob/47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1/netwerk/protocol/http/nsHttp.cpp) and [header pruning](https://github.com/mozilla-firefox/firefox/blob/47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1/netwerk/protocol/http/nsHttpHeaderArray.cpp): CONNECT origin requests omit proxy headers.

## Checks and reproducible staging

```sh
python3 -B tests/test_network_integration.py
python3 -B scripts/prepare_network_source.py --output build/network-source
python3 -B scripts/smoke_network.py --stage /absolute/verified/firefox-stage --output build/network-smoke
```

Source staging reuses the exact pinned, hash-checked compute source overlay and
adds the network module to its build registry, preserving upstream MPL notices.
It does not automatically attach the browser or constitute a Firefox source build.
New original code remains GPL-3.0-only. The smoke uses the already verified ESR
140.16 runtime, a fresh profile, a read-only host and a disposable loopback-only
namespace; it does not install or download a browser.

Local `build/network-smoke-08/report.json` passed with real Gecko Unix IPC and
three TLS 1.3 responses. Two attachments were independently live; closing A left
B usable. Wrong scope, ordinary HTTP, expired/raw-IP grants, a mismatched Ready and
denial were blocked. The synthetic HTTPS origin verified the proxy credential was
absent. The real Gecko marker-I/O and streaming-hash APIs used by the core driver
also passed. Both browser/profile and socket cleanup passed. Module SHA-256:
`3cabd8849cb26257db527ef2d25dee43f59b49071c269b819528de6e1c66bb79`.
The gateway is explicitly
**synthetic**: this result is not real core, WireGuard, MPTCP or Internet-route proof.

## Actual core proof driver — pending disposable run

`scripts/smoke_network_core.py` is a separate guest-only driver for two real CLI
grants. The core fixture owns policy, provider selection, signed routes, HTTPS
origin, packet/kernel observations and cleanup. The driver verifies two 32 MiB
streaming hashes; after the fixture observes B's real subflows it detaches A,
leaving B's transfer active. It preserves the disposable client namespace because
the real proxy is loopback there. It refuses the VM's root namespace, root UID and
effective capabilities, and keeps the host filesystem read-only. Reports contain
fixed status and hash-verification results, not grant values or page bodies.

The driver takes `--stage`, `--output` (a new child of this checkout's `build/`),
`--grant-a`, `--grant-b`, `--test-ca`, `--url-a`, `--url-b`, `--expected-sha256`,
`--expected-bytes 33554432`, `--core-revision`, and `--parent-netns` (the VM root
network namespace, distinct from the client namespace). The fixture writes
`detach-a` as `{"version":1,"detach":true}` after observing B's paths; atomic
`a-complete.json` and `a-detached.json` are synchronization markers, not kernel
cleanup proof. The fixture must independently verify cleanup before releasing B.

Minimal guest source manifest: `integration/VolparossaNetwork.sys.mjs`,
`defaults/privacy.json`, and `scripts/{smoke_network_core,smoke_network,
smoke_browser_startup,smoke_compute_model,smoke_privacy,stage_firefox}.py`.
These reuse the existing exact ESR package/runtime hash pins. No compute model or
compute service runs in this scenario. Background-browser egress isolation, if
provided by the disposable fixture, is test containment—not a product kill switch.

The [second combined run](https://github.com/VOLPAROSSA/volparossa/actions/runs/36714093009)
on core `fd2d7eb2` and browser `18a74235` **failed before the first HTTPS request**.
The capless application/namespace/firewall boundary passed, but no browser report or
WireGuard payload was observed. The earlier driver did not preserve errors before its
browser `try/finally`, so that result does not identify a gateway or browser root cause.
The driver now records a closed stage and canonical error/errno from runtime validation,
namespace setup, grant validation, Firefox startup and attachment through completion.
The outer wrapper preserves a child's more specific failure. These diagnostic records
contain no grant, argument, environment, raw exception or browser-log contents. Seven
pure adapter/driver checks pass; the next real-core proof remains pending.

DNS prefetch/DoH/ECH, IPv6, general
browser loads, redirects, HTTP/3, WebRTC, browser background traffic and complete
crash-resistant kill-switch enforcement remain outside this narrow slice.
