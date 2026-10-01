# Native Firefox build preparation

This is an executable preparation path for the **real Firefox 157 C++/IDL/IPDL
build**, including the [per-channel ECH candidate](SCOPED_ECH.md). On 2026-10-01,
the exact source and all seven toolchain archives were fetched and verified;
offline `mach configure` and the full two-job native build passed. This was not an
artifact build or an ESR compatibility test. The rebuilt `xpcshell` subsequently
passed the modified GREASE fixture in a disposable loopback-only namespace, with
socket-process networking explicitly disabled. Raw ClientHello capture, the
socket-process/IPC variant and ordinary browser traffic over the real core route
remain unproved.

## Inputs and explicit gates

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
this is not a raw packet-capture claim or evidence for socket-process/IPC behavior.
Ordinary-tab traffic through the real VOLPAROSSA route, application fallback and
the browser-wide kill switch need their own functional evidence. This build
preparation does not complete those features, packaging, the extension bundle or
the decentrally distributed AI layer.

### Recorded native result

The full build completed successfully in 120 minutes 44 seconds with two jobs.
The build receipt is `build/native-firefox-157/build-result.json`; the selected
test result is `build/native-firefox-157/obj/.mozbuild/testsummary.jsonl`, which
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

## Remaining observed boundaries

- Full Firefox source and the seven verified archives are staged. Configure and
  native compilation passed; the selected parent-process TLS/GREASE fixture passed.
  Full ECH wire/IPC and real browser-to-core route evidence remain outstanding.
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
