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

The hooks now pass the parent-only native request proof below, using a resource
overlay on the retained Firefox build. This is **not a newly compiled admission
build or automatic enrollment**. A startup owner, fresh-list reconciliation,
authority observation, user opt-out and the protected text source still need
connecting. The current closed uBO actor's ten-stock-list fixture is not general
support for customized user selections.

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

## Supplementary selection lifecycle

`Selection.sys.mjs` supplies the internal selection transaction and owner state
machine for **one fixed, independently authorized supplement key**. It is not yet
connected to the production actor or startup owner. The original signed uBO and
the existing closed actor proof remain unchanged.

The child keeps bounded snapshots of selected and imported lists in memory and
changes only the supplement through uBO's original delta commands. It checks
that all other selections and imports remain the same after storage settles and
after two distinct reload events. Enabled and disabled custom imports are
compared as sets, so sorting alone does not count as a changed user choice.
Only closed booleans and event counts cross the parent boundary; receipts and
journals contain no custom-list URLs. Reload membership does not prove the
filter bytes: uBO can report keys for failed assets or reuse compiled caches.

This is a preservation **postcondition**, not prevention of uBO's own migrations.
Original uBO 1.75 can normalize or remove other imports during a load, and its
`getLists` readiness request can itself change selections. Unsupported baseline
syntax is rejected before mutation; an observed migration or concurrent user
change leaves the transaction uncertain and admission closed. No stale baseline
is restored, no repair is attempted and no automatic mutation retry occurs.

The parent requires durable pending intent before mutation and a fresh receipt
before recording success. Temporary removal after expiry or revocation differs
from a permanent user opt-out. An explicit opt-out remains sticky across restarts;
an existing user-owned import is neither adopted nor removed. Revocation and
opt-out invalidate native admission immediately, even while an earlier operation
is waiting. Late results cannot revive that operation. If an opt-out interrupts
work, one bounded journal write preserves the refusal without waiting for the
actor; it waits only for an already-running journal write. Uncertain removal
still needs explicit recovery, not silent re-enrollment.

All 32 selection tests and 102 combined selection, admission, request-hook and
unchanged actor-contract checks pass. These use inert transports plus the real
admission state machine; they do not execute original uBO or Firefox.
Production work still includes durable
profile-owned storage, actor/readiness binding, immutable-key replacement,
authorized maintenance fetches and combined runtime evidence. Reusing a journal
with a different key does not implement safe list replacement.

## Native request proof

The [original native report](../../docs/evidence/filter-native-runtime-04.json)
records seven passing checks on the retained Firefox 157 `xpcshell`: an unfiltered
HTTP baseline, startup waiting, one valid callback, cancellation after expiry,
cancellation after invalidation, unchanged unregistered listeners and cleanup.
The baseline, admitted request and ordinary listener each received HTTP 200 with
the exact fixture response. Expired and invalidated requests reached neither
their origin handlers nor the redirect target. A late callback could not restart
an invalidated request.

The test uses real Gecko channels and native extension-policy objects, but its
uBO identity and publication authority are **synthetic**. Expiry advances only an
injected process clock. It mounts the reviewed WebRequest module and two new
modules over the retained resource tree; it does not rebuild Firefox or prove
`moz.build` packaging. Networking runs in the parent process, following upstream
xpcshell's `MOZ_DISABLE_SOCKET_PROCESS=1` setting. Content-process, socket-process,
original signed-uBO, broker-fetch, real suspend and production-owner integration
remain outside this proof. No sandbox-disable setting is supplied.

The wrapper verifies exact runtime, module and supporting-file hashes before
and after execution. Its fresh user/PID/network namespace exposes only loopback;
the host and original runtime are read-only. Channels, listeners, policies,
timers and admission operations are closed; the disposable profile, temporary
files and cache are removed. The original resource inventory and recorded host
namespace, DNS-file and IPv4/IPv6 route snapshots match afterwards. These are
bounded before/after checks, not a continuous whole-host audit. Original logs
remain private test evidence. They include caught IndexedDB/remote-settings
shutdown diagnostics; the report does not claim an error-free Firefox session.

Report SHA-256:
`26a52ece6c9abcece467bb92fc1d8e2eff4ea40873cc43079cd5948b24355c42`.
The opt-in wrapper requires the exact retained workspace layout and staged
source specified in `scripts/smoke_filter_native.py`; it performs no download or
installation. With those reviewed inputs present:

```sh
python3 -B scripts/smoke_filter_native.py \
  --output "$PWD/build/filter-native-runtime-new" --execute
```

Earlier reports remain failed: [01](../../docs/evidence/filter-native-runtime-01.json)
and [02](../../docs/evidence/filter-native-runtime-02.json) timed out before
bootstrap progress; [03](../../docs/evidence/filter-native-runtime-03.json)
reached the test but failed its HTTP-origin assertion and logged an Android-only
working-directory call on Linux. The wrapper now selects parent-process
networking, lets the existing Linux launcher set the working directory, and
keeps the desktop cache inside the disposable workspace. The fixture explicitly
registers its literal loopback HTTP identity and checks an unfiltered baseline
before testing admission. The passing result does not relabel those failures or
establish a unique cause for every earlier symptom. Their original driver and
fixture bytes remain available in the local Git object store.

## Checks and remaining integration

The in-process Node tests cover framing, content binding, expiry, suspend,
rollback, bounded reconciliation, cancellation and late results:

```sh
node --test tests/filter_*.test.mjs
```

All 82 component tests, 8 source-staging tests and 17 smoke-driver tests pass.
The native wrapper adds 19 inert driver tests and 6 fixture-contract tests;
these are distinct from its actual Gecko execution.
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

A combined protected-provider fetch, production startup/resume owner and actual
signed-uBO admission remain separate integration steps. Publication approval,
ongoing authority checks, persistent user opt-out and safe replacement/removal
must be connected before default activation. Temporary removal on expiry must
not become a permanent user opt-out. Customized lists must be preserved, and
uBO's own maintenance fetches need a narrowly authorized native path that cannot
deadlock behind reconciliation or bypass it through a URL exception.
Consent-O-Matic compatibility must then be rechecked with the actual rules;
the earlier synthetic consent fixture does not prove arbitrary-site compatibility.
