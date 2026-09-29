# Private compute in the existing Firefox sidebar

This slice connects explicit browser actions to the core's **private local execution** interface. It does not publish browsing context, enroll it for training or dispatch it to public peers. Missing configuration or unavailable compute produces a visible error, not cloud fallback.

## Integration

The source patch targets Firefox `157.0.1`, revision `47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1`. It adds **Project VOLPAROSSA (private local)** to the existing AI provider selector and routes its explicit Ask actions into the existing sidebar before the ordinary provider URL/Smart Window pipeline. Upstream MPL notices are preserved.

`VolparossaCompute.sys.mjs` runs only in privileged parent-process code. `VolparossaComputePanel.sys.mjs` provides bounded question/context fields, status, cancellation and text-only results. Closing the panel closes its owned connection and cancels that connection's work; it never sends a generic command or evaluates model output.

The owner explicitly configures `browser.volparossa.compute.socket` to the absolute socket path of an already running `volparossa compute private-serve`. The service requires owner-provisioned pinned runtime/model assets and an owned private work directory. No model or runtime download is triggered by selecting the provider. This pref is not a network kill switch and does not activate network participation.

## Interface contract

- Same-owner Unix socket; IPC version 1, four-byte big-endian length followed by UTF-8 JSON.
- Capabilities are checked before Submit: local-only execution, no network/public cache/training/cloud fallback, supported size and time limits.
- Unique correlated request IDs; bounded framing and deadlines; Cancel applies only to this connection's task.
- Question/context limits are 512/4096 UTF-8 bytes. Oversized text is refused, not silently truncated. A model can impose a smaller tokenizer budget.
- Results are displayed only with confirmed cleanup. `answer_complete` and `answer_status` remain visible; EOS is not proof of factual accuracy.
- Page context and model output remain untrusted text. No URL query, history entry, public broker or diagnostics carry the private prompt.

The core's authoritative schema is [`private_serve/WIRE.md`](https://github.com/VOLPAROSSA/volparossa/blob/feature/browser-private-compute/crates/volparossa/src/compute/private_serve/WIRE.md). This interface is separate from the core's protobuf public-compute API. Same UID does not isolate the service from malicious software already running as its owner.

## Reproduce the focused checks

```sh
python3 -B -m unittest discover -s tests -v
python3 scripts/prepare_compute_source.py --output build/compute-source-local
python3 scripts/smoke_compute.py --stage build/firefox-esr-smoke-v3
```

Source preparation explicitly downloads only six pinned source files and verifies their committed hashes. It creates a new source overlay under `build/`; it does not clone, build or install Firefox. The smoke requires the previously staged matching ESR runtime. It uses a disposable loopback-only namespace, read-only host filesystem and a synthetic protocol peer.

Observed on 2026-09-29: all eight repository tests and nine real Gecko ESR transport/panel cases passed. These cover success, cancellation, invalid UTF-8, oversized frames, wrong request IDs, unconfirmed cleanup, input limits, incompatible private capabilities and literal script-like model text rendered without executable nodes. Local report: `build/csm-vad5w2pb/report.json`.

The synthetic protocol fixture does not establish model execution. The separate real-core
proof below now does, within its stated scope. Building the complete pinned Firefox source
and exercising its patched native provider selector remain pending. Network attachment,
shared-cache integration, browser kill switch and confidential peer inference are separate work.

## Combined browser/core proof

[Run 36614266330](https://github.com/VOLPAROSSA/volparossa/actions/runs/36614266330) passed on
2026-09-29 with core `5beb8d2d79e44d2b4d2e4e3bb20aa4e67701e03b`, browser integration
`4b1fdbe105c5cc23154778664c8d9fca9ef2454b` and the exact Debian ESR 140.16.0 runtime.
Empty-profile startup succeeded in 1,961 ms. The actual Gecko sidebar then submitted to
the real `compute private-serve` and pinned SmolLM2-360M worker: the synthetic note's canary
returned with EOS after 12 generated tokens and appeared as text in the panel.

Before panel rendering, the decoded-result observer saw **zero ephemeral children** and
ended worker lifetimes. Actual private-service Cancel/Disconnect, same-owner IPC, isolated
input/model access, ordinary cleanup without fallback signals and unchanged host state pass.
The temporary browser/profile/appdata and model/job roots were removed. Private prompts and
raw model answers are not exported; only scoped observations and synthetic canary metadata
are retained. Original artifact ZIP SHA-256:
`8da26ef025ff2a539124693ecd098c2e3d8a9d47a34102b8b1351278d8842925`.
The identical before/after host-state SHA-256 is
`7293aa05b9868c420b2634dcecec728da9a9b8b276cd8e090e8b91d2bd441766`.

This proves the bounded **ESR panel → real core → local model** path, not a Firefox 157
source build, its patched native provider selector, general answer quality, confidential
peer inference or completion of the core's B04 milestone. The decoded-before-render boundary
is distinct from the older core-only first-frame-byte proof. Earlier failures remain failed.

## Startup failure and correction history

The [source-bound empty-startup KVM run](https://github.com/VOLPAROSSA/volparossa/actions/runs/36607457567)
fails before model provisioning: the exact ESR process remains alive for the original
40 seconds, with loopback up but no Marionette listener. Its separate 424-byte startup
log reports missing `libGL.so.1` and a software-compositor warning. A controlled local
read-only mount-namespace probe with that library hidden reproduces both warnings but
still opens a real Marionette session in about 3.2 seconds: the warnings alone do not
explain the guest hang. The original artifact remains a failure.

The empty-profile preflight now enables Firefox's own startup trace, retaining the
same 16 KiB limit and deadline. Only this input-free `about:blank` profile enables
`remote.log.level=Trace` and its dump output. The combined private-compute session
does not enable or export those logs; its privacy and sandbox settings are unchanged.

The later [exact-source run 36610233935](https://github.com/VOLPAROSSA/volparossa/actions/runs/36610233935)
also remains **failed**: 40,002 ms, a sleeping Firefox process, and no Marionette listener.
A controlled local reproduction isolated a startup prerequisite rather than a graphics dependency:

- Masking only `.mozilla` with an empty read-only directory reproduced the hang: 40,015 ms,
  `S/do_sys_poll`, no listener, and forced termination.
- With the same runtime and preferences, pre-creating only the empty `firefox` and
  `firefox-esr` app-data directories in that read-only mask allowed startup in 2,574 ms,
  followed by a normal exit.

Firefox initializes its global profile service **before** selecting the explicit `--profile`:
the pinned [profile-service initialization](https://hg.mozilla.org/releases/mozilla-esr140/file/d864999404b3032f682d74ccc60d1ce38c9ce609/toolkit/profile/nsToolkitProfileService.cpp#l971)
requires [the app-data directory to exist](https://hg.mozilla.org/releases/mozilla-esr140/file/d864999404b3032f682d74ccc60d1ce38c9ce609/toolkit/xre/nsXREDirProvider.cpp#l1154).
Failure enters the [profile-missing dialog](https://hg.mozilla.org/releases/mozilla-esr140/file/d864999404b3032f682d74ccc60d1ce38c9ce609/toolkit/xre/nsAppRunner.cpp#l5001),
which is invisible in this headless fixture. An existing local Firefox installation had concealed
this dependency; the clean guest's read-only home could not create it.

Both runners now provide a fresh, owned `appdata` directory through a child-only `.mozilla`
mount. `HOME` is unchanged. To handle a missing mountpoint without writing the host, bubblewrap
creates an anonymous home-directory shell, restores existing immediate entries read-only
(preserving symlinks), then remounts that shell read-only. Only the new browser workspace and
app-data mount are writable; no home contents are copied or recursively scanned. The temporary
app-data tree is removed with the profile, including on normal failure cleanup.

The fixed empty-profile runner passed locally in 2,681 ms and in a second disposable namespace
with a pristine read-only home and the staged repository under that home in 3,389 ms. Both
retained the 40-second deadline, loopback-only network, read-only host and normal Firefox exit;
all generated profile/app-data files were removed. These were startup-only results; the
later combined KVM/sidebar/model pass is recorded above. Local reports are
`build/browser-appdata-fixed.KTaRUg/proof/report.json` and
`build/browser-appdata-guest.rg1gLc/proof/report.json`.
