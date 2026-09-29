# Firefox privacy defaults — executable integration slice

The defaults in [`defaults/privacy.json`](../defaults/privacy.json) disable Mozilla
data submission, local telemetry archiving and listed New Tab telemetry feeds;
disable Mozilla Accounts/Sync; hide sponsored New Tab/shortcut/Firefox Suggest
content; and select Firefox's **Strict** Enhanced Tracking Protection category.
Optional studies and automatic submission of pending crash reports also default off.

These are **defaults, not enforced policy**. The staging tool renders Mozilla's
`defaultPref` AutoConfig mechanism. It does not use `lockPref`, a forced per-startup
user value, or `user.js`. A user's choices can override defaults and survive a
restart. Accounts/Sync changes require a restart. Strict protection can affect site
compatibility; users retain Firefox's normal settings and exceptions.

One bootstrap exception is needed: Firefox only applies its category rules when
the category has a user-branch value. AutoConfig materializes the **current**
effective category (`getPref` to `pref`), not a forced Strict value. Fresh profiles
therefore start Strict; existing Standard/Custom/Strict choices remain unchanged.
The default is sticky so an explicit Strict choice is retained even when it equals
the default. No ETP category or subordinate tracking preference is locked.

The legacy `toolkit.telemetry.enabled` default is false too. Firefox's own release
channel can lock this particular preference; the project neither introduces nor
removes that upstream lock. The separate submission, health-report and usage-upload
defaults are false and remain unlocked.

We select the native Strict category instead of freezing a copied list of every
underlying tracking-protection switch. The smoke checks that the effective category
is Strict **and** tracking protection is actually enabled. It also changes the
category back to Standard and verifies persistence across a second browser launch.

## Reproduce locally without changing your installed browser

Requirements: Python 3.11+, the existing Firefox runtime, Linux unprivileged user
namespaces, `bwrap`, and `ip`. Nothing is installed or downloaded. The example pins
the runtime observed on this development host; a different runtime requires its own
explicit version/source stamp and a new test result.

```sh
python3 -m unittest discover -s tests -v
python3 scripts/stage_firefox.py \
  --firefox /usr/bin/firefox-esr \
  --output build/firefox-esr-smoke \
  --without-extensions \
  --expected-version 140.16.0 \
  --expected-source-stamp d864999404b3032f682d74ccc60d1ce38c9ce609
python3 scripts/smoke_privacy.py --stage build/firefox-esr-smoke
```

Staging resolves the real GRE directory, makes independent copies under `build/`,
records runtime hashes, adds local defaults, and refuses to overwrite an existing
stage. It omits machine-specific `distribution` policies. The original installation
is never patched or linked for writing.

The smoke runs two headless instances, sequentially, using one fresh profile under
`build/privacy-smoke-*`. Bubblewrap makes the host filesystem read-only, with only
that test directory writable, and creates a separate network namespace containing
only loopback. Marionette stays inside that namespace. The smoke reads the actual
GRE path and effective/default/locked preferences, changes several user choices,
restarts, and produces `report.json`. Logs and the disposable profile are retained
for inspection; they are ignored by Git. No normal browser profile is opened.
The test profile disables Marionette's recommended automation preferences, which
would otherwise change sponsored settings and tracking protection and mask the
actual defaults under test. It does not override any privacy feature under test.

The command above explicitly selects the original privacy-only fixture. The default
browser stage now includes three pinned, removable extensions; prepare its local
package cache as described in [Bundled extensions](BUNDLED_EXTENSIONS.md). Staging
itself stays offline. That combined smoke adds a third launch to verify removal.

## Scope and limits

This is an integration overlay on an already installed Firefox, **not** a completed
Firefox source fork or a VOLPAROSSA-network browser. The runtime smoke validates
preference application and user-choice persistence. It is not proof of zero browser
background traffic, complete telemetry removal, tracker blocking coverage, or a
production network kill switch. Network isolation is provided by the test harness,
not the preferences. Safe Browsing, browser security checks, certificates, extension
signatures and ordinary update mechanisms are not weakened by these defaults.

Source pin, version differences and primary references are recorded in
[`FIREFOX_PROVENANCE.md`](FIREFOX_PROVENANCE.md).
