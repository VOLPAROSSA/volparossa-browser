# Default extensions — pinned inputs, user control

The browser bundle includes three original Mozilla Add-ons (AMO) packages:

| Extension | Pinned version | Original license |
| --- | --- | --- |
| [uBlock Origin](https://addons.mozilla.org/firefox/addon/ublock-origin/) | 1.75.0 | GPL-3.0-only |
| [Decentraleyes](https://addons.mozilla.org/firefox/addon/decentraleyes/) | 3.0.2 | MPL-2.0 |
| [Adaptive Tab Bar Color](https://addons.mozilla.org/firefox/addon/adaptive-tab-bar-colour/) | 4.2.0 | MIT |

[`extensions.lock.json`](../defaults/extensions.lock.json) records the exact IDs,
versions, AMO file URLs, byte lengths, SHA-256 digests, effective permissions,
compatibility and licenses checked on 2026-09-29. The metadata was obtained from
each extension's official AMO API, not a third-party package mirror. Changing the
lock is an explicit packaging decision; staging never resolves a `latest` URL.

These extensions have broad access appropriate to their functions. uBlock Origin
can inspect/block requests and access page contents; Decentraleyes can intercept
supported resource requests; Adaptive Tab Bar Color can read page/tab information,
change the theme and access its listed browser settings/management APIs. Their
complete reviewed permission lists are in the lock. AMO's `none` data-collection
declarations are publisher declarations, not an independent privacy audit. These
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
XPIs. Adaptive Tab Bar Color's XPI does not include its MIT text, so packaging also
includes its exact upstream [MIT notice](licenses/Adaptive-Tab-Bar-Colour-MIT.txt)
under `distribution/licenses/`. The notice comes from source revision
`70045b52b8aae80b4402dbb9f2426bd514bec463` and has a separate hash pin. A public
binary release must also satisfy the respective source/notice obligations; this
slice is a workspace staging tool, not a completed release package.

## Reproduce

Fetching is a separate, explicit build-time operation; it downloads only the three
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
