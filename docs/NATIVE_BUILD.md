# Native Firefox build preparation

This is an executable preparation path for the **real Firefox 157 C++/IDL/IPDL
build**, including the [per-channel ECH candidate](SCOPED_ECH.md). On 2026-10-01,
the exact source and all seven toolchain archives were fetched and verified;
offline `mach configure` and the full two-job native build passed. This was not an
artifact build or an ESR compatibility test. The rebuilt `xpcshell` subsequently
passed the modified GREASE fixture in a disposable loopback-only namespace with
socket-process networking both disabled and enabled in separate runs. The enabled
run independently observed an actual socket child and passed 28 subtests, but
the fixture skips GREASE telemetry checks in that mode. Raw ClientHello capture,
ECH-specific wire/IPC enforcement and ordinary browser traffic over the real core
route remain unproved.

## Inputs and explicit gates

### Ordinary native tab and product controller fix

The native ordinary-tab trial now passes using the real built Firefox 157 and
the product network modules. It found a concrete integration defect:
`browser.ownerGlobal` is unavailable in this build. The corrected controller
uses the standard `browser.ownerDocument.defaultView`; parent-process,
top-browsing-context and duplicate-binding checks remain in place.

```sh
python3 -B scripts/smoke_network_native.py --javascript-overlay \
  --output build/network-native-builtin-04
```

This command requires the existing exact build and a new output directory; it
does not download or rebuild anything. The opt-in overlay mounts the exact
tracked controller read-only over its built-in resource path in the private
namespace. It is new product JavaScript on the original native ABI, not proof
that the old build receipt contained the fix. Original source, build receipt
and binary hashes remain unchanged. The ECH module is imported unchanged,
without the ESR fixture's compatibility rewrite or global GREASE opt-out.

Attempt 04 passed all 19 runtime cases: four genuine TLS 1.3 responses through
two independent Unix-socket attachments, scope and denial checks, detach of one
attachment while the other survives, and an ordinary tab whose rendered body
matches the synthetic response. No gateway capability reached the origin.
Browser/profile/socket cleanup passed; host DNS, routes and network namespace
were unchanged. Attempts 01–03 retain their original binding failures.

| Evidence under `build/network-native-builtin-04/` | SHA-256 |
| --- | --- |
| `report.json` | `3018adf04ecb0565fbb9111efa3f1e608d632a82006e73149a47911207ffb067` |
| `host-state.json` | `6bff32e070b711c7d08b0a7ef70ac42286c503194f0c203d5cc84e65e1c64f69` |
| `resource-overlay/receipt.json` | `1c0e6bb1e2f7ed2a7f75c0e7bf295bd5ec2d9da9cc785ac56868e7113d2452e8` |

The receipt binds the original build receipt
`4be952653fe30d9516dde25c4ccd1cf870a6ab87514f8ba6a0961a7f4d62485d`
and controller transition from
`d2b0a5507b7fcc2b212d68ab344f4c7ae9364bd1fe146bf2963c91d4263d57cf`
to `fcc7eb622a1ff54e13e7ed40d5625e44f155c03b71fc0af9867bca6334379228`.
Thirteen controller checks, six native-driver checks and fifteen integration
checks also pass (one existing namespace-opt-in check is skipped).

This is a real native browser with an explicitly **synthetic gateway**, not a
new core/WireGuard/MPTCP proof, raw ClientHello/GREASE capture, full browser
killswitch or unrestricted everyday browsing proof.

### Pinned build inputs

`patches/firefox-native-build.json` pins Firefox commit
`47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1` and tree
`4a1d6e73d48bc5888e4da37d655b360a14db18db`. The corresponding Mozilla 157.0.1
release source archive and checksum URLs returned 404 on 2026-10-01. The script
therefore fetches that exact commit with depth one into a separate build directory;
it never substitutes ESR, another version or a floating branch.

Seven Mozilla Taskcluster compiler/toolchain archives are pinned to exact task
IDs, run 0, byte lengths and SHA-256 values from their HTTPS-served
`public/chain-of-trust.json`. The script does **not** claim validation of the
complete signed Taskcluster trust chain. The original artifacts/notices are
preserved. They provide Clang 22 including libclang, the Linux GTK/X11/audio
sysroot, WASI sysroot, cbindgen, NASM, Node 24 and dump_syms. The Linux sysroot is
Mozilla's build baseline, not a replacement for the development host's libraries.

Archive downloads total **572,580,353 bytes (about 546 MiB)**, excluding Firefox
source. The Git source transfer size is unknown until fetched. Plan for at least
60 GiB free space for source, unpacked tools and objects. The script uses two build
jobs; that is not a hard total-memory limit or a duration guarantee on a 16 GiB host.

The existing workspace-only Rust 1.98.1 stage can be reused read-only. Its complete
file inventory is checked against the pinned `TOOLCHAIN_REPORT.json`; no rustup,
system package installation or external installer is invoked. Its upstream Rust
manifest and original notices remain available in that stage.

Run the read-only, network-free plan first:

```sh
python3 -B scripts/native_build.py
python3 -B tests/test_native_build.py
```

Only when the download/build work is explicitly scheduled:

```sh
# Explicit network phase. Does not compile or execute downloaded toolchains.
python3 -B scripts/native_build.py --fetch

# Separate explicit build phase. Does not download missing dependencies.
python3 -B scripts/native_build.py --build \
  --rust-stage /media/erikleblansch/data/VSCodium/volparossa-mail/build/stalwart-rust-1.98.1
```

Use `--output build/<new-name>` for a separate attempt; paths outside this
checkout's `build/` and symlink escapes are rejected. Existing source changes,
partial downloads and incomplete tool extraction are preserved and rejected,
not silently reset, deleted or overwritten. A workspace-local lock excludes
concurrent preparations/builds of the same tree.
Reused extracted tools are checked against their complete file, mode and symlink
inventory before execution; an archive checksum alone is not used to trust a
previously unpacked compiler.

## What the build command actually does

The script verifies source identity, applies the already-reviewed compute/network
transformations and ECH patch to exact originals, and records every overlaid file
hash. It then runs `mach configure` and `mach build -j2` inside Bubblewrap with a
read-only host filesystem, writable build directory and **no network**. Downloaded
tools and archives are read-only during compilation. Owner credentials and proxy
environment are not inherited. Build state, Cargo cache and temporary paths stay
in the build directory. The build cannot alter host DNS, firewall or routes.
Inside the namespace, that workspace temporary directory is mounted at `/tmp`:
IPDL's multiprocessing sockets otherwise exceed Linux's AF_UNIX path-length limit
with this checkout's long absolute path. The host's `/tmp` is not used.

`--disable-bootstrap`, offline Cargo, no-index pip and
`MACH_BUILD_PYTHON_NATIVE_PACKAGE_SOURCE=none` prohibit surprise dependency fetches;
the source's vendored Python/Rust dependencies are used. Tests and WASI sandboxed
libraries remain enabled. Debug symbols are disabled to reduce disk use; browser
networking, certificate checks and process sandboxes are not disabled by this plan.

The result is accepted only when `mach build` succeeds and produces `firefox`,
`libxul.so` and **the rebuilt `xpcshell`**. Their hashes and source overlay go into
`build-result.json`. That receipt proves compilation only; its
`native_ech_wire_proven` field remains false. The subsequent local TLS/GREASE
fixture was run against that exact binary in a disposable loopback-only namespace:

```sh
# Inside the existing offline build sandbox, with isolated HOME as described below:
./mach xpcshell-test --sequential \
  --setpref network.http.network_access_on_socket_process.enabled=false \
  netwerk/test/unit/test_ech_grease.js
```

The five tasks in that fixture passed: local TLS responses, default-channel ECH
settings, scoped GREASE suppression, preservation on the next default channel,
and distinct connection-pool keys. GREASE checks use native handshake telemetry;
this parent-process result is not a raw packet-capture claim. The separate
socket-process result below has a narrower ECH assertion scope.
Ordinary-tab traffic through the real VOLPAROSSA route, application fallback and
the browser-wide kill switch need their own functional evidence. This build
preparation does not complete those features, packaging, the extension bundle or
the decentrally distributed AI layer.

### Recorded native result

The full build completed successfully in 120 minutes 44 seconds with two jobs.
The build receipt is `build/native-firefox-157/build-result.json`; the selected
parent-process test result is preserved byte-for-byte as
`build/native-firefox-157/parent-testsummary-20261001.jsonl`, which
records `test_ech_grease.js` as `PASS`, return code 0, on 2026-10-01 at
19:14:37 UTC (21:14:37 Europe/Amsterdam). The prepared ECH patch SHA-256 is
`6d87eb3a00275caad042f10e8ebfe14c579a45bb40dab6f4461822cf5e5dffc1`.
The rebuilt outputs were rehashed before the runtime probe:

| Output | SHA-256 |
| --- | --- |
| `obj/dist/bin/firefox` | `6c28d9bdb250f0ead4ebcb18fbe44f50aecfe4c3b4c60e972f925f929f54dfb1` |
| `obj/dist/bin/libxul.so` | `a49a8c7fc85b61d1f14e0c4107153f00af56a03317f3b4031db5da642bfb7972` |
| `obj/dist/bin/xpcshell` | `43de7531b38ba73f64acdb07449e89ac500041ef883c1bc6a8a262adae822f19` |

The first runtime attempt failed during native startup, before test assertions:
the build sandbox's empty environment omitted `HOME`, and upstream
`GetUnixHomeDir` passed that null value to `nsDependentCString`. The corrected
runtime invocation preserved only the existing passwd-matching `HOME` value,
plus an explicit sanitized build/test environment, and mounted a fresh private
tmpfs over that home directory. It did not change `HOME` to another path or expose
host home files or credentials. The wrapper verified that the private namespace
contained only loopback. No source correction or rebuild was needed. An
intermediate minimal-shell diagnostic timed out; only the subsequent full test
harness run is the recorded passing result.

After the runtime process and namespace exited, the original host network
namespace and the SHA-256 hashes of DNS configuration, IPv4 routes and IPv6 routes
were unchanged. No host package installation or network reconfiguration occurred.

### Historical socket-process attempts — not passing evidence

The same binary was subsequently tested with
`network.http.network_access_on_socket_process.enabled=true`. The pinned harness
set `MOZ_FORCE_USE_SOCKET_PROCESS=1`, but the run timed out after 30 seconds before
visible JavaScript assertions. No socket child was observed. The timeout dump was
generated by the harness's termination, not by a spontaneous Firefox crash. Its
main-thread stack contains `GeckoChildProcessHost::LaunchAndWaitForProcessHandle`,
`SocketProcessHost::Launch` and `nsIOService::LaunchSocketProcess`; that localizes
the wait, but does not establish its underlying cause or an ECH implementation bug.

The exact failed-attempt receipt and process observation are
`build/native-firefox-157/socket-runtime-result-20261001-02.json` and
`socket-process-observation-20261001-02.json`; its log is
`socket-runtime-20261001-02.log`. All reside in the ignored build directory.
The preceding wrapper-only attempt stopped before launching mach because it
incorrectly inspected the host-mounted `/sys/class/net`; the corrected attempt
verified the actual namespace's sole loopback interface with `socket.if_nameindex`.
Host DNS/routes/netns were unchanged. The parent-process PASS above is preserved
separately because mach replaces `obj/.mozbuild/testsummary.jsonl` on each run.
Neither attempt establishes socket-process/IPC or raw ClientHello behavior.
Attempt 03 changed only `MOZ_PROFILER_STARTUP=0` and also timed out without an
observed socket child; its separate `socket-runtime-result-20261001-03.json`,
observation and log are retained, not relabeled as successful.

### Socket-process attempt 04 — selected runtime passed

Attempt 04 restored the ordinary startup-profiler setting from attempt 02 and
changed only the runtime's `MOZ_UPLOAD_DIR` to a new mode-0700 directory,
`build/native-firefox-157/socket-upload-20261001-04`. This uses Firefox's existing
CI path for profiler output; it does not disable profiling, weaken ECH or require
a source change/rebuild. The same command ran with
`network.http.network_access_on_socket_process.enabled=true`, and the pinned
harness set `MOZ_FORCE_USE_SOCKET_PROCESS=1`.

The source and timeout stack explain the scoped environment correction:
`profiler_lookup_async_signal_dump_directory` holds `PSAutoLock` while looking up
the download directory. In the private empty HOME, the fallback loads a localized
`chrome://` string bundle, initializes networking and waits for socket launch;
new launch threads need the same profiler lock during registration. The explicit
workspace upload directory takes the existing direct-file path instead. The
successful run supports that diagnosis; disabling the startup profiler alone in
attempt 03 did not resolve it.

On **2026-10-01 at 19:35:17 UTC**, the fixture passed **28/28 subtests**, return
code 0. An independent process observer recorded a real socket child (namespace
PID 112), with type argument `socket`, executing `obj/dist/bin/plugin-container`.
Its SHA-256 was
`8e3d79d8bea685047c94b72bdbc8ee2dbecd9781905483a6cc6237530e477eec`.
The original Firefox/libxul/xpcshell and native patch hashes above are unchanged.

These preserved ignored artifacts are all beneath `build/native-firefox-157/`:

| Evidence | SHA-256 |
| --- | --- |
| `socket-runtime-result-20261001-04.json` | `86242bc0f5a634baa3e80cf56d980c7d697731bee67a489259a748855fd5fe93` |
| `socket-process-observation-20261001-04.json` | `14c79d36dace19cfeaa80b42f5ab9743b87018cd8fa0ba2b209ea042eaacddfa` |
| `socket-runtime-20261001-04.log` | `924695a04433c4ef85614e43e2cd640989077dd738c38696730cd07e357ad2ad` |
| `socket-testsummary-20261001-04.jsonl` | `15ddf3b5df27ec79a0ce45c23e9e1aefb7e167ed531792021b228665f0195da5` |

This proves successful native TLS requests, channel-ABI and pool-key assertions
with a real socket process. **It is not GREASE-wire proof:** the upstream fixture
skips handshake-telemetry assertions when socket-process networking is enabled.
`raw_clienthello_proven` and `native_ech_wire_proven` remain false; ECH-specific
IPC propagation still needs an independent observation of the resulting handshake.
Host DNS/routes/netns were unchanged, no test processes remained, and the
parent-process PASS and preceding failures remain separately preserved.

## Remaining observed boundaries

- Full Firefox source and the seven verified archives are staged. Configure and
  native compilation passed; the selected parent-process TLS/GREASE fixture passed,
  as did its socket-process variant with an independently observed native child.
  ECH-specific wire/IPC and real browser-to-core route evidence remain outstanding.
- Host Clang 19 lacks the required installed libclang; GTK/audio/X11 development
  packages and cbindgen are absent. The pinned toolchain/sysroot path supplies
  these without host installation. The actual configure accepted Clang 22.1.8,
  libclang, Rust 1.98.1, cbindgen, NASM 3.02, GTK/audio/X11 and the WASI sysroot.
- The first real configure exposed an unsorted `EXTRA_JS_MODULES` overlay. The
  source transform now places `VolparossaBrowserNetwork` before the compute modules;
  configure passed after that correction. The native ECH patch was not changed.
- `git`, Python 3.13, Bubblewrap, zstd, make, Perl, unzip and pkg-config are present.
  A later configure error is evidence of a missing prerequisite, not permission
  to install packages or download unpinned tools automatically.
- Taskcluster artifacts can expire. If an exact input disappears, update and review
  the pin explicitly; never resolve a new `latest` artifact during reproduction.
- These are local build inputs. Mixed toolchain/sysroot licenses and Mozilla
  trademarks still require review before redistribution; no notice is relicensed.

Primary references: [Mozilla's native Linux build instructions](https://firefox-source-docs.mozilla.org/setup/linux_build.html),
[pinned cbindgen requirement](https://github.com/mozilla-firefox/firefox/blob/47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1/build/moz.configure/bindgen.configure),
[pinned sysroot recipes](https://github.com/mozilla-firefox/firefox/blob/47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1/taskcluster/kinds/toolchain/sysroot.yml),
and [vendored/offline Python selection](https://github.com/mozilla-firefox/firefox/blob/47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1/python/mach/mach/site.py).
