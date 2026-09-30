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
`--grant-a`, `--grant-b`, `--control-directory`, `--test-ca`, `--url-a`, `--url-b`, `--expected-sha256`,
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

### Current combined failure and closed attachment diagnosis

The [combined run on core `cd3e630d`](https://github.com/VOLPAROSSA/volparossa/actions/runs/36732926403)
starts actual ESR 140.16 and reaches `attach-a`, then fails with `unavailable` before
Ready or any WireGuard payload. Its 18 original artifacts remain failed (ZIP SHA-256
`b468959bab11bdedb2020696fa6f1ffb6a74beec21b609045339d13d18d0b2d0`). Browser/profile,
private-file and topology cleanup pass; guest-parent host snapshots are identical.
The old error conflated socket creation, stream setup/write, peer EOF and timeout;
it does not identify a confirmed core or permissions defect.

The next candidate retains only a fixed attachment substage and optional unsigned
32-bit `nsresult`, never an exception message, socket path, grant or authority. The
guest driver first checks the actual app socket type, mode, parent owner/group and
kernel peer UID inside its sandbox. It connects and closes **without sending bytes**,
so it cannot consume a one-use capability. Existing UID, filesystem, policy, grant
and route restrictions are unchanged; no speculative production-core fix is made.

Eleven focused checks pass, including a real local socket proving the preflight sends
no data and closed-diagnostic preservation through outer failures. The existing isolated,
real-ESR/synthetic-gateway smoke also passes with the updated module at
`build/network-attach-diagnostic-01/report.json` (module SHA-256
`9379a848af31d2182a9e4529b83307dbfa5864ed6a9d9b08c279f0b98fab2444`): three TLS 1.3
responses, independent detach, denial checks and complete cleanup. This remains a
synthetic gateway test, **not** the pending real core/MPTCP browser proof.

The [next combined run on core `be2c6cd6`](https://github.com/VOLPAROSSA/volparossa/actions/runs/36738800599)
fails earlier in the new preflight with `socket-path / OS_ERROR / ENOENT`. The agent
advertises `/run/volparossa/control/agent.sock.apps` inside its systemd mount namespace;
the browser previously saw only the fixture's external `runtime-client/control` path.
The candidate driver now maps that **exact control directory**, read-only, into an
anonymous child-only `/run`. It does not publish the remaining runtime or agent state,
rewrite a grant, or alter ownership, group membership, peer-UID checks or network access.
The child compares both original socket and parent inodes, modes and owners before
the unchanged non-consuming socket probe. Binding the directory preserves parent
ownership checks that a lone socket below an app-owned directory would break.

Twelve pure checks and an explicitly enabled, actual unprivileged bubblewrap socket
check pass (`VOLPAROSSA_TEST_BWRAP=1 python3 -B tests/test_network_integration.py`).
The latter proves read-only original inodes, zero-byte connection, unchanged source
permissions and no other runtime subtree published below `/run/volparossa`. It is
not an overlay payload proof; the corrected combined KVM run remains pending.

### Current request boundary

The [combined run on core `4d60478a`](https://github.com/VOLPAROSSA/volparossa/actions/runs/36743469202)
passes the namespace mapping, non-consuming socket probe, both actual Ready attachments
and wrong-scope denial, then fails at `request-a / SCRIPT_FAILED`. Its 18 original
artifacts prove cleanup and unchanged guest-parent host state, but no WireGuard payload.
The generic error does not yet identify CONNECT rejection, route setup or origin TLS.

The candidate retains fixed request substages, numeric `nsresult`, CONNECT/HTTP statuses
and a body-present boolean, never exception text, addresses or response headers. It
preserves a pending second request's original failure rather than replacing it with a
marker timeout. The CONNECT status comes from Firefox's
[`nsIProxiedChannel.httpProxyConnectResponseCode`](https://github.com/mozilla-firefox/firefox/blob/47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1/netwerk/base/nsIProxiedChannel.idl),
also checked in the real pinned ESR runtime. The module and its authorization rules are
unchanged; a diagnostic record is not successful payload proof.

`build/network-port18443-02/report.json` passes the real ESR/synthetic-gateway replay at
port 18443, including three TLS responses, three native CONNECT status-200 observations,
independent detach and complete cleanup. Thirteen pure driver checks pass (the existing
namespace-specific check remains opt-in). Reproduce this bounded non-default-port case:

```sh
python3 -B scripts/smoke_network.py --stage /absolute/verified/firefox-stage \
  --output build/network-port18443 --origin-port 18443
```

This adds no host listener or network configuration: the existing disposable namespace
still contains only loopback. The real core/MPTCP proof remains pending.
