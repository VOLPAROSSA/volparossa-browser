# Scoped HTTPS/TCP gateway and ordinary-tab controller

`integration/VolparossaNetwork.sys.mjs` connects explicitly authorized Firefox
channels to the core's application gateway v1. The separate
`integration/VolparossaBrowserNetwork.sys.mjs` now adopts **ordinary Gecko HTTPS
channels for one explicitly bound browser element**, retaining Firefox's own
request/listener machinery. Neither is a global browser proxy or complete kill switch.
The 18 privacy defaults and other users of the core are unchanged.

The current source candidate now **requires a native per-channel ECH interface**
which unmodified ESR 140 does not have. Its exact Firefox 157 source patch now
builds, and the rebuilt xpcshell passes the selected parent-process TLS/GREASE
fixture. Raw ClientHello, socket-process/IPC and actual browser-to-core route
proof for that new binary remain separate. Explicit ESR compatibility fixtures
are separately identified. See
[scoped ECH and evidence boundaries](SCOPED_ECH.md). The passed real-core run
below belongs to earlier browser `198e288`, not this later native candidate.

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

The core must finish signed route preparation **before** publishing Ready. The
browser allows at most 95 seconds for that control-plane bootstrap, capped by the
grant's unchanged absolute expiry; timeout or EOF closes the attachment. No owned
HTTPS channel can open before Ready. This avoids spending Firefox's normal
CONNECT/origin-TLS deadline on route admission; no TLS/network preference is
increased. Ready is route-preparation acknowledgement, not origin or payload proof.

Each bound channel uses authenticated CONNECT to the pinned loopback gateway,
with a unique connection-isolation key. HTTP, raw-IP grants, other authorities,
redirects and direct fallback are rejected. HTTP/3, HTTP/2 coalescing and Alt-Svc
are disabled **only on those channels**. The browser retains normal origin TLS
validation; CONNECT 200 alone is not proof that the origin connected successfully.
The adapter does not log grants, headers or URLs and never stores capabilities in
preferences/history. Requests bypass the browser response cache in this first
transport proof; shared-cache integration remains separate.

### Ordinary browsing and bounded availability fallback

Privileged application code (not a page, content script or arbitrary extension)
can bind a native browser element, explicitly supply owner-authorized grants,
and let the browser navigate normally:

```js
const owner = VolparossaBrowserNetwork.bind(window.gBrowser.selectedBrowser);
await owner.authorize(grant); // Same exact authority, partition and original TTL.
// Ordinary navigation now passes through the native channel filter; no replay.
owner.setKillSwitch(true);    // Blocks new unprotected requests in this binding.
owner.close();                // Closes its attachments and keeps the live tab blocked.
// Only explicit owner consent re-enables ordinary networking in a live tab:
owner.release({ allowOrdinaryInternet: true });
```

The binding defaults to inactive with its killswitch **off**, preserving Firefox's
existing network/proxy configuration until an owner authorizes participation.
After activation, missing scope, pending preparation, expired or revoked grants,
policy denial, EOF, timeouts and malformed/ambiguous responses all block. Private
browsing/container attributes and the browser element's current top-level context
are checked at native proxy resolution. Other tabs are not claimed to be protected.
Redirects remain blocked; existing listeners, load groups and non-redirect callbacks
stay with Gecko. There is no profile startup activation, automatic grant acquisition
or renewal, preference-stored capability, or settings UI in this slice.

Only the authenticated attachment decoder can issue fallback authority from the
core's exact terminal response: `status=unavailable`, `reason=no_eligible_paths`,
matching hostname/port/partition/original expiry, and `direct_until_ms`. The core
must confirm route cleanup and current policy/grant authorization before sending
it. A matching `denied/blocked` response confers no fallback permission. Generic
exceptions with an `unavailable` name cannot substitute for this response.

Fallback lasts **at most five seconds**, bounded by both process-monotonic time
and the unchanged wall-clock grant/decision expiry. It permits only a **new GET
or HEAD**, with the killswitch off and no proxy credential. It preserves the
owner's existing ordinary Firefox proxy configuration rather than forcing DIRECT.
An already-overlay-owned channel is never retried directly, and request bodies
are never replayed. Toggling the killswitch governs new requests; it does not claim
to stop previously started ordinary flows, shared/service workers, WebRTC or all
browser background traffic. Complete browser-wide enforcement remains open.

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
node --test tests/network_attachment.test.cjs
python3 -B scripts/prepare_network_source.py --output build/network-source
python3 -B scripts/smoke_network.py --stage /absolute/verified/firefox-stage --output build/network-smoke
```

Source staging reuses the exact pinned, hash-checked compute source overlay and
adds both network modules to its build registry, preserving upstream MPL notices.
Staging alone does not automatically attach the browser or constitute a Firefox
source build. The separately scheduled [native build](NATIVE_BUILD.md) completed
on 2026-10-01, with a passing local TLS/GREASE fixture under an explicit
socket-process-off preference. That does not rerun the historical ESR gateway
or core-route tests below against the new binary.
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

The newer `build/network-ordinary-02/report.json` passes on the same pinned ESR:
three original explicit-channel responses plus one **ordinary tab navigation** via
`browser.loadURI`, whose rendered `document.body` was checked independently.
The origin received four TLS 1.3 responses without a proxy credential; independent
detach and browser/profile/socket cleanup passed. Network module SHA-256:
`f18a4a5b5d9ffab4a15980911df56ee6a08dd56b48add0e040f93a808704867f`;
controller SHA-256:
`d2b0a5507b7fcc2b212d68ab344f4c7ae9364bd1fe146bf2963c91d4263d57cf`.
This proves the real Gecko adoption path, **not** real core availability fallback
or overlay payload. Eleven simulated-clock/transport checks separately exercise
typed decisions, malformed scopes, five-second and grant bounds, clock rollback,
kill-switch behavior, denial/EOF and preserving native listeners. ESR's `Cu.now()`
and the newer pinned source's `ChromeUtils.now()` provide the process-monotonic
clock; no wall-clock substitute is used.

## Actual core proof driver — scoped run passed

The [run on core `b8a1dd6e`](https://github.com/VOLPAROSSA/volparossa/actions/runs/36776940049)
and browser `198e288` passed: both explicit Gecko HTTPS requests transferred and
verified 32 MiB, each over two genuinely carrying MPTCP/WireGuard relay paths.
Closing A left B active; the TLS 1.3 origin saw only the exit and no proxy credential.
Private/application cleanup and unchanged guest-parent host state passed.
This used a disposable ESR profile with GREASE disabled. It does **not** prove
ordinary tabs over the real overlay, native per-channel ECH control, general
fallback or a browser-wide kill switch. Earlier failures below remain historical
failures; later source changes do not inherit this run's proof.

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

Attachments remain serial because they share core route-admission state. The
candidate driver bounds orchestration to a 300-second script, 315-second
Marionette read and 360-second wrapper; these are not browser networking timeout
settings. Neither grant is renewed, and its original absolute expiry can end the
trial earlier. Three offline adapter tests exercise delayed Ready, expiry-capped
bootstrap and EOF/timeout shutdown using a simulated transport/clock. They do not
prove real Gecko/core route preparation; the combined run remains required.

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

DNS prefetch/DoH/ECH, IPv6, complete multi-origin
browser coverage, redirects, HTTP/3, WebRTC, browser background traffic and complete
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

### Visible-SNI fixture compatibility, not a product TLS relaxation

The [run on core `d58e5514`](https://github.com/VOLPAROSSA/volparossa/actions/runs/36773190344)
reaches both prepared routes and CONNECT 200 / forwarding-start acknowledgements,
then fails at Gecko stream start with `0x804b0047` (`NS_ERROR_NET_INTERRUPT`).
Cleanup and unchanged guest-parent host state pass; this is not successful payload
proof. Exact ESR source enables ECH GREASE on every TLS 1.3 ClientHello, while the
current exit parser deliberately rejects the ECH extension, including GREASE.
That is a source-confirmed incompatibility, not a proven diagnosis from the old
closed failure report alone.

The next **disposable core-proof profile only** sets
`security.tls.ech.grease_probability=0` to exercise the v1 visible-SNI path. Normal
TLS/certificate validation stays enabled. No production preference, product
controller flag or exit policy is weakened. A later [native per-channel ECH source
candidate](SCOPED_ECH.md) has since built and passed its selected parent-process
TLS/GREASE fixture, not the real-core/browser route with that binary. A successful restricted fixture
must not be presented as unrestricted everyday browsing support.
