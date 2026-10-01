# Firefox provenance and validation scope

## Upstream source integration target

- Repository: [mozilla-firefox/firefox](https://github.com/mozilla-firefox/firefox).
- Pinned source revision: `47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1`.
- Upstream target version supplied by the project's source audit: `157.0.1`.
- The later native integration build completed for this exact revision on
  2026-10-01, including Firefox, libxul and xpcshell. Its selected parent-process
  TLS/GREASE fixture passed; see [native build evidence](NATIVE_BUILD.md).
  Earlier privacy-defaults and extension smokes on ESR 140 remain separate and
  are not upgraded to Firefox 157 runtime evidence by that compilation.

## Native build and selected runtime — 2026-10-01

`patches/firefox-native-build.json` pins the exact source tree, seven Mozilla
toolchain archives and the reused workspace-only Rust toolchain. Offline
`mach configure` and full `mach build -j2` passed; no artifact build, substitute
source, host package installation or network-enabled compilation was used.
`build/native-firefox-157/build-result.json` contains the source-overlay and
rebuilt-output SHA-256 values. It does not claim bit-for-bit reproducibility or
validation of the complete signed Taskcluster trust chain.

The rebuilt xpcshell passed `netwerk/test/unit/test_ech_grease.js`, return code 0,
with socket-process networking explicitly disabled. The five fixture tasks cover
local TLS responses, scoped GREASE suppression, preservation on a following
default channel and different connection-pool keys. Its result is recorded in
`build/native-firefox-157/obj/.mozbuild/testsummary.jsonl`. This is not raw
ClientHello capture, socket-process/IPC proof or ordinary-tab traffic through
the real VOLPAROSSA route. The runtime used a disposable loopback-only namespace;
host DNS and route hashes were unchanged. Full command, binary hashes and the
isolated-HOME startup correction are in [Native build](NATIVE_BUILD.md).

## Installed runtime used by the workspace-only smoke

- Resolved executable: `/usr/lib/firefox-esr/firefox-esr`.
- Version output: `Mozilla Firefox 140.16.0esr`.
- `application.ini` version: `140.16.0`; build ID: `20260908152208`.
- Source repository: `https://hg.mozilla.org/releases/mozilla-esr140`.
- Source stamp: `d864999404b3032f682d74ccc60d1ce38c9ce609`.
- Stage-specific SHA-256 hashes of the executable, libxul and both resource archives
  are recorded in the generated `volparossa-staging.json` and smoke report.

The staging tool neither fetches nor replaces this runtime. Reproduction requires
an existing matching install. A changed version or source stamp is rejected unless
explicitly selected. This makes staging inputs auditable; it is not a claim of
bit-for-bit reproducible Firefox compilation.

### Observed local result — 2026-09-29

- Four configuration/renderer/path-confinement tests passed, including an actual
  symlink escape rejection.
- Real isolated browser smoke passed for all 18 project preferences.
- Fresh ETP category: Strict; actual tracking protection: enabled.
- Accounts/Sync, sponsored shortcuts and ETP category changes persisted through a
  browser restart. The project does not lock those choices.
- Test namespace contained only loopback; the host filesystem was mounted read-only.
- Report: `build/privacy-smoke-q4l1bgoz/report.json` (local ignored artifact).
- Validated GRE: `build/firefox-esr-smoke-v3` (local ignored artifact).
- Executable SHA-256:
  `22bb2d84f2289622e65fbcde266a30952e6ac64d27a2a1af75cea7229f2e6b14`.
- `libxul.so` SHA-256:
  `62cc326204668a2dae453c9e89d9c200e513c9e3ad0772b164091bf1e5333fb4`.
- Defaults SHA-256:
  `f6594ebca20585479635522e562f2da26a6422774b9628a0a906b320311076a7`.
- Rendered AutoConfig SHA-256:
  `daba7ee38a2c3ff594a4aa06674ca3e9e964d16959efb9b169c62033cc9430bb`.

This is specifically ESR 140.16.0 evidence, not a Firefox 157 build result or a
VOLPAROSSA datapath/kill-switch test. Earlier failed probes exposed the category
bootstrap issue; they were not counted as passing evidence.

### Private-compute source overlay and Gecko transport

The private-compute slice originally added an exact-source overlay; the later
native build above includes it, without claiming a new compute runtime result.
`patches/firefox-source.json` pins the SHA-256 of each of the six modified upstream
files. `patches/0001-private-compute-sidebar.patch` applies with `--fuzz=0` to the
original files at the revision above. MPL notices remain intact; the new integration
modules are original GPL-3.0-only code.

The real ESR Unix-transport/panel smoke passed nine cases on 2026-09-29, including
cancellation and literal script-like model text rendered without executable nodes.
Report: `build/csm-vad5w2pb/report.json`; module SHA-256:
`60c34936c0c0a3537e797dcd1c73be4f767c277aaad2c998fc39cbb5e958527b`.
Its peer is explicitly synthetic. The source overlay, ESR module execution and
model-through-core/browser integration are separate evidence boundaries. See
[reproduction and remaining work](PRIVATE_COMPUTE.md).

### Additional extension-bundle result — 2026-09-29

The same pinned runtime additionally passed actual signature/activation checks for
uBlock Origin 1.75.0, Decentraleyes 3.0.2 and Adaptive Tab Bar Color 4.2.0. All three
could be disabled and removed, with those choices retained across restart. The
18 privacy-default values and rendered AutoConfig hashes above are unchanged.
Exact package provenance and scope are in [Bundled extensions](BUNDLED_EXTENSIONS.md);
local report: `build/privacy-smoke-gnv0k89m/report.json`.

## Primary references checked on 2026-09-29

- [Mozilla AutoConfig documentation](https://support.mozilla.org/en-US/kb/customizing-firefox-using-autoconfig)
  describes default-only preferences, separate from user overrides and locked prefs.
- [Mozilla telemetry preferences](https://firefox-source-docs.mozilla.org/toolkit/components/telemetry/internals/preferences.html)
  explains the submission master switch and upload consent. The upstream
  `toolkit.telemetry.enabled` preference can itself be channel-locked, so the
  integration defaults it to false but does not try to unlock it.
- [Pinned enterprise-policy implementation](https://github.com/mozilla-firefox/firefox/blob/47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1/browser/components/enterprisepolicies/Policies.sys.mjs)
  identifies the separate usage-upload switch included in the telemetry defaults.
- [Pinned Firefox defaults](https://github.com/mozilla-firefox/firefox/blob/47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1/browser/app/profile/firefox.js)
  describe the Accounts/Sync gate, sponsored suggestions and telemetry switches.
- [Pinned New Tab preferences](https://github.com/mozilla-firefox/firefox/blob/47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1/browser/extensions/newtab/lib/ActivityStream.sys.mjs)
  define sponsored New Tab content and telemetry defaults.
- [Pinned ContentBlockingPrefs](https://github.com/mozilla-firefox/firefox/blob/47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1/browser/components/protections/ContentBlockingPrefs.sys.mjs)
  implements the native Strict/Standard categories and their version-specific rules.
- [Marionette protocol](https://firefox-source-docs.mozilla.org/remote/marionette/Protocol.html)
  and the [pinned driver](https://github.com/mozilla-firefox/firefox/blob/47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1/remote/marionette/driver.sys.mjs)
  document the test control channel. No third-party Python test dependency is used.
- [Current Mozilla enterprise policy reference](https://firefox-admin-docs.mozilla.org/reference/policies/)
  is relevant to later managed deployments; blanket locked policy is deliberately
  not used for these user-adjustable defaults.

The installed ESR resource files were also inspected. ESR and the target source
revision have different Strict feature sets; the integration does not pretend those
versions are interchangeable.

## Licenses and names

The later [scoped ECH candidate](SCOPED_ECH.md) adds a separate exact native-source
patch and parent-only channel ABI, including connection-pool/IPC propagation.
Its source hashes and MPL/public-domain notices are recorded independently in
`patches/firefox-network-ech.json`. It is now compiled into the exact Firefox 157
build above and has a selected native TLS/GREASE test result. It was not added to
the installed ESR binary: explicit ESR compatibility copies and their historical
tests must not be confused with execution of that native patch.

Original project scripts/configuration follow the repository's GPL-3.0-only license.
Firefox and its bundled components retain their own Mozilla/third-party licenses;
this repository's LICENSE does not relicense them. The local stage is ignored by
Git and is not a redistribution artifact. Consult Firefox's `about:license`, its
source notices, and the system package's `/usr/share/doc/firefox-esr/copyright`
before preparing any redistributable build. Mozilla/Firefox trademarks likewise
remain separate from the project's code license.
