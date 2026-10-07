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

## Checks and remaining integration

The in-process Node tests cover framing, content binding, expiry, suspend,
rollback, bounded reconciliation, cancellation and late results:

```sh
node --test tests/filter_*.test.mjs
```

All 55 component tests and 17 smoke-driver tests pass. An actual isolated
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

A combined protected-provider fetch and the native startup/resume admission
hooks remain separate integration steps. Publication approval, ongoing authority checks, persistent
user opt-out and safe replacement/removal must be connected before default
activation. Consent-O-Matic compatibility must then be rechecked with those actual
rules; the earlier synthetic consent fixture does not prove arbitrary-site
compatibility.
