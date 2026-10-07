# Default extensions — pinned inputs, user control

The browser bundle includes four original Mozilla Add-ons (AMO) packages.
Isolated Firefox tests verify their signed activation and persistent user control.
A synthetic consent scenario also verifies Consent-O-Matic and uBlock Origin
cooperation; this is not a guarantee of compatibility with arbitrary websites.

| Extension | Pinned version | Original license |
| --- | --- | --- |
| [uBlock Origin](https://addons.mozilla.org/firefox/addon/ublock-origin/) | 1.75.0 | GPL-3.0-only |
| [Decentraleyes](https://addons.mozilla.org/firefox/addon/decentraleyes/) | 3.0.2 | MPL-2.0 |
| [Adaptive Tab Bar Color](https://addons.mozilla.org/firefox/addon/adaptive-tab-bar-colour/) | 4.2.0 | MIT |
| [Consent-O-Matic](https://addons.mozilla.org/firefox/addon/consent-o-matic/) | 1.1.5 | MIT |

[`extensions.lock.json`](../defaults/extensions.lock.json) records the exact IDs,
versions, AMO file URLs, byte lengths, SHA-256 digests, effective permissions,
compatibility and licenses. The original three pins were checked on 2026-09-29;
Consent-O-Matic's entry was checked on 2026-10-07. The metadata was obtained from
each extension's official AMO API, not a third-party package mirror. Changing the
lock is an explicit packaging decision; staging never resolves a `latest` URL.

These extensions have broad access appropriate to their functions. uBlock Origin
can inspect/block requests and access page contents; Decentraleyes can intercept
supported resource requests; Adaptive Tab Bar Color can read page/tab information,
change the theme and access its listed browser settings/management APIs.
Consent-O-Matic can read tabs and interact with page contents on all websites.
Their complete reviewed permission lists are in the lock. The original three
packages' `none` data-collection declarations are publisher declarations, not an
independent privacy audit. Consent-O-Matic 1.1.5 has no such manifest declaration:
its explicit `null` lock value means **absent**, not a claim of no collection. These
permissions do not authorize publishing private pages into VOLPAROSSA's cache or
training on them. Decentraleyes' local resource bundle is not the shared DICN cache.

## Installation and updates

Exact XPIs are copied **unchanged** into `distribution/extensions/<addon-id>.xpi`.
Firefox installs them as normal profile extensions on first launch, with its
ordinary signature checks. Users can disable or remove each one; removal persists
across restart. No `force_installed` or `normal_installed` enterprise policy is
used, no existing profile is edited, and no signature requirement is disabled.

The lock pins **build-time inputs**, not the future state of a user's profile.
Firefox's native extension/browser update behavior and user choices are unchanged;
there is no project-specific runtime downloader or hidden updater. User-installed
newer versions are not silently downgraded. Extension filter/resource-data updates
are distinct from this packaging step. Installation testing is not evidence that
all extension network activity already travels over VOLPAROSSA.

Original embedded licenses and third-party notices stay inside the unmodified
XPIs. Adaptive Tab Bar Color and Consent-O-Matic do not include their MIT texts,
so packaging also includes their exact upstream notices under
`distribution/licenses/`. The [Adaptive Tab Bar Color notice](licenses/Adaptive-Tab-Bar-Colour-MIT.txt)
comes from source revision `70045b52b8aae80b4402dbb9f2426bd514bec463`; the
[Consent-O-Matic notice](licenses/Consent-O-Matic-MIT.txt) comes from the upstream
v1.1.5 revision `a539a8e06101d53496ac71c2a45abe3f4287ac7c`. Both have separate hash pins. A public
binary release must also satisfy the respective source/notice obligations; this
slice is a workspace staging tool, not a completed release package.

## Consent handling and compatibility

Consent-O-Matic's [upstream defaults](https://github.com/cavi-au/Consent-O-Matic/blob/a539a8e06101d53496ac71c2a45abe3f4287ac7c/Extension/GDPRConfig.js)
set all six optional purpose categories to false and open first-run onboarding.
Packaging does not rewrite consent preferences, disable tracking protections or
enable cookie-banner hiding lists. Users can change their preferences or turn the
extension off. A disappeared dialog alone does not demonstrate a recorded refusal.

The [upstream rule loader](https://github.com/cavi-au/Consent-O-Matic/blob/a539a8e06101d53496ac71c2a45abe3f4287ac7c/Extension/background.js)
fetches a mutable GitHub rule list and its references, with a randomized roughly
22–48 hour cache interval. It can reuse cached rules after a fetch failure, but
the XPI contains no offline rule bundle. The package hash does not authenticate
future rule versions, and these fetches are not yet verified VOLPAROSSA delivery.

The [report action](https://github.com/cavi-au/Consent-O-Matic/blob/a539a8e06101d53496ac71c2a45abe3f4287ac7c/Extension/popup.js)
sends the selected tab's host to the upstream report service only when the user
requests a report, with confirmation enabled by default. Its
[privacy policy](https://addons.mozilla.org/firefox/addon/consent-o-matic/privacy/)
allows sharing submitted reports with other open-source developers. No automatic
reporting or VOLPAROSSA collection of browsing context is added here.

## Reproduce

Fetching is a separate, explicit build-time operation; it downloads only the four
locked AMO files into this checkout's ignored `build/` directory. Verification and
staging are offline. There is no host installation or normal-profile modification.

```sh
python3 -B -m unittest discover -s tests -v
python3 -B scripts/bundle_extensions.py fetch
python3 -B scripts/bundle_extensions.py verify
python3 -B scripts/stage_firefox.py \
  --output build/firefox-extensions \
  --expected-version 140.16.0 \
  --expected-source-stamp d864999404b3032f682d74ccc60d1ce38c9ce609
python3 -B scripts/smoke_privacy.py --stage build/firefox-extensions
```

The tool rejects size/hash mismatches, identity/version/permission drift, unsafe or
duplicate ZIP entries, excessive expansion and cache symlinks. It never extracts
or repacks executable content. Checking signature-envelope files is **not** a
cryptographic signature verdict; the real Firefox test supplies that evidence.
In particular, AMO's `is_mozilla_signed_extension: false` field is not interpreted
as an unsigned-XPI verdict.

### Observed result — 2026-09-29

Twelve offline tests passed (eight extension tests plus four existing privacy
tests). Real Firefox ESR **140.16.0** then installed all three exact versions with
`signedState=2` (`SIGNED`), `isActive=true`, and permission to disable/uninstall.
Signature enforcement remained enabled. All three user-disabled states survived a
restart; after removal all three stayed absent on the next launch. The same run
verified all 18 unchanged privacy defaults, native Strict ETP, and persistent user
overrides. The harness used a fresh workspace profile, loopback-only disposable
network namespace and read-only host/runtime mounts.

Local evidence: `build/privacy-smoke-gnv0k89m/report.json`; staged GRE:
`build/firefox-extensions-esr-v2`. Executable/source pins are unchanged from
[Firefox provenance](FIREFOX_PROVENANCE.md). This proves installation and user
control on the named ESR runtime, **not** Firefox 157 source integration, extension
behavior on arbitrary sites, or a VOLPAROSSA network/kill-switch datapath.

### Four extension packaging checks

On 2026-10-07, twelve bundle tests, two staging tests and four privacy-default tests
passed. All four locked AMO packages passed fetch and offline verification, then
were copied byte-for-byte with their notices to an add-on-only staging directory.
The original three lock entries remained unchanged. Tests reject missing or
altered manifests and distinguish the fourth package's absent declaration from
empty or explicit `none` declarations without weakening the other three checks.
These are packaging checks, not Firefox signature acceptance, runtime installation
or consent-behavior evidence; no browser was executed for this slice.

## Pending consent and filter work

- [x] Verify all four extensions' signatures, activation, disable and removal
  persistence in a disposable Firefox environment.
- [x] Run synthetic consent fixtures with all four extensions: recorded refusal
  of optional purposes, retained user choices, no optional requests, an unsupported
  dialog left available, essential resource loading and additive uBO list removal.
- [ ] Follow with separately scoped public-site regressions, real upstream consent
  rules and theme/resource-interaction tests; do not infer universal compatibility.
- [x] Prepare a navigation-state correction with a Gecko-executed callback
  regression for Consent-O-Matic upstream. The signed package remains unchanged.
- [ ] Submit the reviewed correction upstream; no contribution has been submitted
  by this slice.
- [ ] Develop a supplementary uBlock Origin list from explicitly public or
  synthetic evidence, with per-rule review, regression tests and rollback history.
  Cooperative discovery proposes changes; it does not authorize activation or
  publication of private browsing context.
- [ ] Distribute independently authorized, versioned filter objects through the
  VOLPAROSSA content layer and verify them before uBO consumption. Peer delivery
  and a hash alone are not publisher authority. Test tampering, expiry, rollback,
  bounds, interrupted updates and retention of a still-authorized prior version.
- [x] Prove ordinary additive list registration and persistent user removal in
  the isolated uBO fixture without replacing its ten original selections.
- [ ] Connect an authorized network-delivered list as a user-removable default;
  the ordinary UI test does not implement that product integration. The upstream
  [`toOverwrite.filterLists` policy](https://github.com/gorhill/uBlock/wiki/Deploying-uBlock-Origin:-configuration)
  replaces the selection; it is not a one-time, user-overridable default.

### Isolated consent and user control proof

[`smoke_consent.py`](../scripts/smoke_consent.py) runs a real-browser scenario
using the unchanged signed packages, a synthetic cookie dialog and a normal uBO
test-list subscription. Only its fresh profile receives local test rules; this
does not change the shipped Consent-O-Matic rules or connect uBO to VOLPAROSSA.
The process has a loopback-only network and read-only host/runtime mounts.

The scenario requires both stored consent and an independently observed submission
to the fixture server. Positive controls show that the optional and advertising
probes work with the relevant extensions disabled. With all four active, the
scenario requires recorded refusal, no optional request, uBO probe blocking,
an available unsupported dialog, preserved user preferences and unchanged Strict
tracking protection. A hidden-only banner must fail the refusal check.

On 2026-10-07, the pinned ESR 140.16.0 runtime passed the scenario with all four
original signed packages active. The baseline recorded essential, advertising
and optional requests. With the extensions active, all six optional purposes were
stored and independently submitted as false: essential requests remained allowed,
while advertising and optional requests were absent. An unsupported dialog stayed
visible and unanswered, and the hidden-only negative control was rejected.

The user's changed consent choice and added uBO subscription survived restart.
Removing the subscription through uBO's normal UI preserved all ten original
selections; another restart kept it absent and the advertising probe reached the
server again. All four add-ons then stayed disabled across restart and stayed
absent after uninstall/restart. Signature enforcement and Strict tracking
protection were never disabled. Temporary profiles and raw logs were removed.

Root independently repeated the complete browser run and the fifteen fixture
and five startup checks. The original report is
`build/consent-controls-01/report.json`, SHA-256
`fe2c61557a19c637b29387d80b4b38308142d176f06fde8fc99eb9a32e3f66f2`;
the independent report is `build/consent-controls-root-01/report.json`, SHA-256
`a0ba6ee8370b947bdb64f23310cc218346d7dfe74a0d0bdd794f055d8138b390`.
Their embedded source/runtime hashes identify the tested fixture. This proves
synthetic cooperation and user control, not upstream-rule coverage, arbitrary-site
compatibility or VOLPAROSSA filter distribution.

Run inside the disposable browser test environment with the pinned ESR staged:

```sh
python3 -B scripts/smoke_consent.py \
  --stage build/firefox-consent --output build/consent-smoke
```

The report is `build/consent-smoke/report.json`. Do not reuse an existing output
or normal browser profile. The separate
[upstream patch](../patches/consent-o-matic-navigation-reset.patch) changes the
navigation callback's `Loading` check to Firefox's `loading` event value. Its
callback regression passed inside Gecko: the original callback missed the loading
event, the correction handled it, and unrelated events remained unchanged.
No patched XPI or upstream submission is included. The original signed package
stays byte-for-byte intact.

## Primary implementation references

- [Mozilla: deploy Firefox with extensions](https://support.mozilla.org/en-US/kb/deploying-firefox-with-extensions)
  describes the distribution-directory mechanism.
- [Pinned Firefox `installDistributionAddons`](https://github.com/mozilla-firefox/firefox/blob/47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1/toolkit/mozapps/extensions/internal/XPIProvider.sys.mjs)
  documents retention of user removal and newer installed versions.
- [Mozilla ExtensionSettings policy reference](https://firefox-admin-docs.mozilla.org/reference/policies/extensionsettings/)
  distinguishes forced installation and disable-only managed installation; neither
  is used here.
- [Adaptive Tab Bar Color MIT source](https://github.com/atbc-org/Adaptive-Tab-Bar-Colour/blob/70045b52b8aae80b4402dbb9f2426bd514bec463/LICENSE)
  supplies the additional unchanged notice.
- [Consent-O-Matic MIT source](https://github.com/cavi-au/Consent-O-Matic/blob/a539a8e06101d53496ac71c2a45abe3f4287ac7c/LICENSE)
  supplies its additional unchanged notice.
