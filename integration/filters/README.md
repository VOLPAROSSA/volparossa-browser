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
machine for **one fixed, independently authorized supplement key**. The new actor
adapter and profile journal below are connected by an explicit parent-side owner;
production startup integration remains unfinished. The original signed uBO and
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

The selection lifecycle milestone recorded 157 passing selection, actor, journal,
owner, admission, request-hook and unchanged actor-contract checks. They use inert browser services and actual Node
SQLite for the journal; they do not execute original uBO or Firefox.

### Original extension actor

`Actor.sys.mjs` opens only the original signed uBO 1.75 `about.html` in a hidden,
parent-owned extension page. It verifies the pinned package before opening and
before and after each command. Commands are limited to observing, adding, removing
or reading the original asset for the independently supplied key; there is no general messaging API,
script injection or extension modification. Policy, principal, extension context
and document identities must remain unchanged across asynchronous work.

The original about page does not automatically request list changes. Observation
reads only the two selection-storage fields; it neither calls `getLists` nor
claims readiness. The first add or remove command waits for readiness with
`getLists`, after the lifecycle has durably recorded pending intent. It compares
the bounded baseline before, immediately after and after a delayed storage read.
That request can itself cause native migrations: detecting a change is not
preventing it. A failed pending write therefore sends neither readiness nor a
selection mutation.

Each operation has a 40-second, suspend-aware deadline. Cleanup closes the owned
page, actor binding and registration; uncertain cleanup blocks replacement.
The 34 actor tests run the actual methods against inert browser fixtures, not a
signed-addon session. List membership and reload receipts still do not prove the
downloaded or compiled filter contents.

### Native delivery of fixed filter bytes

`AssetChannel.sys.mjs` delivers one prefetched public filter snapshot to the
original signed uBO through its own background-page request. The parent validates
the text and envelope hashes, grammar and expiry, then binds a short-lived lease
to the exact extension policy, background context and native channel. Firefox's
internal replacement must retain the captured native callback identities; an
equal-looking channel or JavaScript object is not sufficient.

The [native asset report](../../docs/evidence/filter-asset-native-05.json) passes
with Firefox ESR 140.16.0 and signed uBO 1.75. Its original `getAssetContent`
handler returns all 63 expected bytes with the exact digest, without changing
selected or imported lists. The body travels through Firefox's interception
stream; there is no HTTP server or modified extension. One transfer completes.
Two unauthorized parent requests and a late request after close receive zero
bytes and abort. The malformed-query request also has a wrong principal, so it
does not independently prove query-only rejection in the native browser.

All four bundled extensions remain active, their package hashes and the privacy
preferences remain unchanged, and the browser exits with status zero. Temporary
profile and cache state are removed. The [separate host checks](../../docs/evidence/filter-asset-native-05-host-checks.json)
record matching before/after DNS-file, route and namespace snapshots, not a
continuous whole-host audit. The linked report is an indented copy of the retained
original JSON value; original report SHA-256:
`8377b9b20dc9010fbd2038f4d1f4d38f33ca783ffd11d0b61e02eb7f685793e0`.

This trial uses synthetic publication authority and a synthetic envelope. It
proves delivery to uBO's asset reader, not signed broker retrieval, consumption
by its filtering engine, automatic enrollment or the startup/resume barrier.
The actor may read uBO's raw cache in other executions; its receipt alone is not
a freshness proof. The permanent reserved-host denial guard prevents ordinary
opening requests after close. The redirect guard does not cover Firefox's
transparent redirects, which bypass category sinks; it is not an all-redirect
or universal no-DNS proof.

The focused checks include 26 inert asset-adapter tests, eight redirect tests,
34 actor tests and 28 wrapper/evidence tests. Four earlier failed native trials
remain retained; they exposed ESR background identity and XPConnect wrapper
differences that the inert fixtures now model. To repeat the successful scope
with the reviewed current source and an unused output directory:

```sh
python3 -B scripts/smoke_filter_asset.py \
  --stage /absolute/path/to/the/verified/firefox-consent-runtime \
  --output "$PWD/build/filter-asset-native-new" --execute
```

Without `--execute`, the wrapper prints its scope and starts no process.

### Explicit selection owner

`Owner.sys.mjs` connects the actor, lifecycle and private journal for one
independently configured publication and immutable list key. Its caller must be
browser-owned code supplying the current context, publication authorization,
suspend-inclusive clock and synchronous admission invalidation. Pages and broker
replies cannot choose those capabilities. The journal binds the subscription to
the manifest and key without retaining custom-list URLs.

The owner opens the actor only when an operation needs it. Enrollment with a
persisted opt-out requests no grant, sends no actor command and writes no new
choice. Explicit observation still checks the real extension state. Expired add
authority does not prevent removing a list the lifecycle owns.

Ordinary work, urgent suspension and permanent refusal have separate bounded
slots. A refusal can interrupt an already running suspension; it is not lost as
a busy error. Close joins their pending writes before closing the journal.
Twelve inert tests cover these connections and races. No startup hook, automatic
expiry timer, content verification or native admission registration is installed
by importing or opening this owner.

### Native selection and restart proof

The [native selection report](../../docs/evidence/filter-selection-native-01.json)
passes on source `056c9cdf` with the retained Firefox ESR 140.16.0 and original
signed uBO 1.75. Three real browser processes share one disposable profile.
The existing ten stock lists, an enabled custom import and a disabled custom
import remain intact throughout these operations:

1. Explicit enrollment adds the fixed synthetic supplement and blocks its probe.
2. Temporary revocation removes it and permits the probe. Re-enrollment restores
   blocking. Each mutation requires two fresh reload events and verified storage.
3. The ordinary uBO interface removes the supplement. Observation records a
   permanent refusal; enrollment declines both immediately and after restart.

All three processes exit with status zero. Six HTTP checks distinguish blocking
from unavailable test content, and an essential request succeeds in every case.
The actor, journal and owner are the actual candidate modules; list content,
publication authority and invalidation callbacks are synthetic. All four bundled
extensions retain their original signed packages and active state.

The fixture uses a loopback-only disposable network with a read-only host and
runtime. Private profiles, caches and temporary state are removed; bounded logs
remain private evidence. Runtime and extension-package hashes are reverified.
The [separate host checks](../../docs/evidence/filter-selection-native-01-host-checks.json)
record matching DNS-file, route and namespace snapshots;
these are before/after observations, not a continuous whole-host audit.

This does not prove arbitrary custom-list compatibility, immutable content-byte
verification, authorized broker retrieval, native admission registration, real
expiry or startup/resume protection. It is not automatic production enrollment
or a rebuilt Firefox. Report SHA-256:
`864ca9396621b40d7afc3cb9dab144d5ac6d0e7070cb5b9e7fd515417d12622d`.

The [repeat selection trial](../../docs/evidence/filter-selection-native-02.json)
also passes with the updated actor that supports asset reads. All three browser
processes exit zero, all six request probes retain their expected results, and
stock/custom selections and sticky opt-out survive unchanged. Its [host checks](../../docs/evidence/filter-selection-native-02-host-checks.json)
match before/after. This remains separate from the asset-delivery trial; neither
report establishes their combined production startup flow. Original report
SHA-256: `60cb46b9d0d4d82b5e15737ce21304c6d3e4944cc7af00981578556e13c55d20`;
the repository copy contains the same JSON value with indentation.

Eighteen inert wrapper checks cover evidence acceptance, fixed module pins,
bounded log retention and cleanup even when closing the control socket fails.
The opt-in wrapper requires the exact retained staged runtime and a fresh output:

```sh
python3 -B scripts/smoke_filter_selection.py \
  --stage /absolute/path/to/the/verified/firefox-consent-runtime \
  --output "$PWD/build/filter-selection-native-new" --execute
```

Without `--execute`, it prints the scope without starting a browser or server.

### Private profile journal

`Journal.sys.mjs` stores the subscription's choice and state in a private SQLite
database under the browser profile. It records pending intent before an explicit
selection mutation and acknowledges a write only after its transaction commits.
Separate attempted markers prevent a missing row or interrupted initialization
from becoming fresh permission to enroll. Failed transactions and unknown results
remain closed; they do not trigger automatic retries.

The journal contains public binding digests, choices and revision counters, not
browsing history or custom-list URLs. Its 32-subscription limit includes unfinished
attempts. Profile directories require mode 0700 and files mode 0600. This protects
against other local users, not a malicious process running as the same user or
root. Changing the fixed key requires separate recovery; it does not reset refusal
or implement safe key replacement.

The current SQLite wrapper does not prove that native close succeeded merely by
resolving its close promise. After an open attempt, this adapter therefore permits
no second connection to that database in the same module/process lifetime, even
after a normal close. A normal owner keeps its journal open; replacing that owner
requires a browser restart. The 17 journal tests cover real SQLite transactions
with Gecko service fixtures and simulated process restarts, not power-loss or
native-close guarantees.

The [native journal report](../../docs/evidence/filter-journal-native-01.json)
now also passes on the retained Firefox 157 runtime. Two separate xpcshell
processes share one disposable profile: the first commits an opt-out, exits, and
the second reads that refusal back. The actual lifecycle then refuses enrollment
without invoking an actor, requesting a grant or writing a replacement choice.
Both processes also verify that their own module cannot reopen its closed journal.

Both native exit statuses, exact semantic results and upstream harness completion
are required. The wrapper mounts only the two journal/selection modules over a
copy of the original resource tree, leaving WebRequest unchanged. This is actual
Gecko SQLite persistence across a process restart, not a newly compiled browser,
power-loss test, original-uBO session or production startup-owner integration.
The publisher and key binding are synthetic; no content is downloaded.

The test runs in fresh user/PID/network namespaces with a read-only host and
runtime. Its disposable profile, cache and temporary files are removed; original
resource inventories and the recorded DNS-file/route/namespace snapshots match
afterwards. These are scoped checks, not a continuous audit of the host.
Report SHA-256:
`76a063c74af6ea6247121e1fc577a1749b5598c4bbfe05146ffc43f4f8afbf2c`.

With the exact retained inputs in `scripts/smoke_filter_journal.py`, the opt-in
wrapper can reproduce this isolated test without a download or installation:

```sh
python3 -B scripts/smoke_filter_journal.py \
  --output "$PWD/build/filter-journal-native-new" --execute
```

Without `--execute`, it prints only the plan. Six inert driver tests check the
closed result parser, source pins, sandbox arguments and refusal to run unfrozen
inputs. The two native phases share a 90-second acceptance budget; the existing
outer process monitor uses 150 seconds. This is not a hard deadline on the
operating system's process-creation call itself.

Production work still includes startup and resume ownership,
immutable-key replacement, authorized maintenance fetches and combined runtime
evidence before automatic list activation.

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
