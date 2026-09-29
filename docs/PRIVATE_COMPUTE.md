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

**Still pending:** building the complete pinned Firefox source, exercising its patched native sidebar, and the combined browser → real core → real model proof. The protocol fixture does not establish those results. Network attachment, shared-cache integration, browser kill switch and confidential peer inference are separate work.

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
