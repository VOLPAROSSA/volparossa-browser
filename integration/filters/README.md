# Browser filter service integration

This development slice connects a parent-process Firefox consumer to the core's
explicit public-filter service. It is not automatic uBlock Origin enrollment:
the existing [closed actor proof](../ubo-proof/README.md) remains separate, and
no default startup hook is installed here.

## Scope and trust

The client can request capabilities, status or one fixed filter publication.
Requests carry no URL, browsing history, publisher selector, filesystem path or
general core command. The caller supplies the expected publisher, name, manifest
ID and authority deadline independently; the server cannot select its own trust.
The core verifies the signature and publication policy. The browser additionally
checks the original text and signed-envelope hashes, size, restricted domain-block
grammar and unchanged expiry.

`Gecko.sys.mjs` uses only the Unix socket
`<profile>/volparossa-filter-broker/filter.sock`. Its existing private directory
must have mode 0700 and the socket mode 0600, without symlink components. The
module neither creates the service nor falls back to a web endpoint. This is
local-account trust, not protection against the same account or root replacing
browser files. No web page or extension API exposes this module.

## Lifetime and cancellation

`Frame.sys.mjs`, `Contract.sys.mjs` and `Session.sys.mjs` implement bounded framing,
closed response schemas, request correlation and one pending request. A connection
accepts at most 32 unique requests. Errors, invalid frames and missed deadlines
close it without automatic retries. A complete final frame may finish its digest
checks after graceful EOF; an incomplete frame cannot become a snapshot.

`Clock.sys.mjs` combines wall time with Linux suspend-inclusive process uptime.
It rejects clock regression and does not restart a lease when a laptop resumes.
This floor is process-local, not trusted time across browser restarts. The Gecko
uptime getter does not enable telemetry or upload measurements.

`Admission.sys.mjs` provides single-use admission tickets for **new** callbacks.
Its owner must verify the exact addon, invalidate before changes, and prove either
the current authorized list or supplementary-only removal followed by a fresh
uBO reload. A stale ticket requires aborting the affected request, not replaying
the callback or discarding a combined uBO decision as if stock rules did not apply.
Already completed addon side effects cannot be undone by this component.

## Native request hook candidate

The separate [Firefox source patch](../../patches/0003-filter-admission.patch)
connects admission to blocking `WebRequest` listeners. A parent-only registry
binds an owner-supplied coordinator to one exact uBO policy object, not just its
addon name. The owner still has to check the signed addon and publication
authority; registration alone supplies neither approval.

The bridge checks the ticket immediately before calling the parent listener and
again after its result arrives, immediately before Firefox applies the result.
Expired, replaced or cancelled work aborts the affected channel rather than
ignoring a combined uBO decision or replaying the callback. Suspension failures
also reach that abort path; a failed cancellation cannot trigger fallback resume.
All unused operations are discarded, including after another addon or DNR has
already cancelled the request. Unregistered policies retain their original path.

Owner-provided clocks and timers bound an operation to at most 60 seconds, with
10 seconds as the default. Checks include suspend time and run even if a timer
has not yet fired. This bounds pending callbacks; it does not undo addon side
effects or turn a parent-side check into atomic authorization inside the
extension process. Background wakeup and IPC may follow the initial parent call.

This is a source-level integration candidate, **not a tested native Firefox
build or automatic enrollment**. The existing broker IPC proof did not execute
these hooks. A startup owner, fresh-list reconciliation, authority observation,
user opt-out and the protected text source still need connecting. The current
closed uBO actor's ten-stock-list fixture is not general support for customized
user selections.

The local stager verifies both upstream files against the
[exact inventory](../../patches/firefox-filter-admission.json), checks the
generated patch against the committed patch, and writes only a fresh build
directory. It does not download sources or modify the supplied source tree:

```sh
python3 -B scripts/prepare_filter_source.py \
  --source /absolute/path/to/pinned/firefox-source \
  --output build/filter-native-source
```

The [controlled-method report](../../docs/evidence/filter-native-methods-01.json)
records 15 passing checks against the exact staged method bytes, including failed
suspension, failed resumption, expired results and early cancellation by another
listener. The harness verifies source and module hashes before evaluating only
the two changed method bodies against in-memory channel fixtures. This is not a
Firefox runtime or native-build result. Reproduce it after staging with:

```sh
node tests/filter_native_hook.mjs \
  --source "$PWD/build/filter-native-source/patched/toolkit/components/extensions/webrequest/WebRequest.sys.mjs" \
  --sha256 a8a4527629508fd3f1dff70d7361a5fa028d92eb67011d8e6ddfefdebae8532d
```

Mozilla's original MPL notices remain unchanged. The two new browser-owned
modules are GPL-3.0-only and expose no WebExtension or page API.

## Checks and remaining integration

The in-process Node tests cover framing, content binding, expiry, suspend,
rollback, bounded reconciliation, cancellation and late results:

```sh
node --test tests/filter_*.test.mjs
```

All 82 component tests, 8 source-staging tests and 17 smoke-driver tests pass.
The source-staging tests are inert; they do not run native Firefox. An actual isolated
Firefox ESR 140.16.0 probe also passed against the core CLI built from
`0647f3e6730bec0bb9a02f0de81a10b13f3723a4`
([core PR #217](https://github.com/VOLPAROSSA/volparossa/pull/217)).
The [original report](../../docs/evidence/filter-broker-ipc-01.json) records two
negotiated Unix connections, an unavailable status, a real fetch failure with no
agent present, and rejection after removing the authority file. The broker
exited gracefully, its socket was removed and all disposable profiles were
deleted. The host filesystem and retained browser were mounted read-only.
No uBO settings were changed.

The report SHA-256 is
`560f74ed803daa1543bc0d95aaa5ccb152c758ee1095086cd653a6416b90ffc0`;
it binds the exact original ESR runtime, five staged modules, harness and core
executable. This is successful real IPC and refusal behavior, **not a successful
content download**. The fixture deliberately has no agent, resolved publication,
network provider or browser activation. It does not prove Firefox 157 native
source hooks.

With an independently checked retained runtime and reviewed core build, the
opt-in probe can be repeated into a new output directory:

```sh
python3 -B scripts/smoke_filter_broker.py \
  --stage /absolute/path/to/firefox-consent-runtime \
  --broker /absolute/path/to/reviewed/volparossa \
  --broker-sha256 REVIEWED_BINARY_SHA256 \
  --output build/filter-broker-check
```

A combined protected-provider fetch and actual native startup/resume execution
remain separate integration steps. Publication approval, ongoing authority checks, persistent
user opt-out and safe replacement/removal must be connected before default
activation. Consent-O-Matic compatibility must then be rechecked with those actual
rules; the earlier synthetic consent fixture does not prove arbitrary-site
compatibility.
