# Browser/core integration contract — development requirements

Status: private-compute IPC v1 has an executable client and source patch; network/cache integration is still in progress. This is not a complete browser build or network-proof claim.

## One reusable daemon

Network selection, policy, shared-cache verification and compute scheduling belong in `VOLPAROSSA/volparossa`. Firefox-specific UI and source integration belong here. Changes to both repositories must name compatible core/interface versions; the presence of a socket alone is not a capability handshake.

The existing core control socket uses bounded, correlated Protocol Buffers (`CONTROL_PROTOCOL_VERSION = 2` at core `f4e6aa79`). It exposes status, roles, route connection, content transfer and public compute operations. It does **not yet** supply a complete browser-scoped network attachment or arbitrary private-chat API. The current private-task CLI is a separate owner-authorized execution path. Do not forward browser context into the public inference broker merely because that broker already exists.

The new `compute private-serve` candidate exposes that private execution path over a **separate same-owner Unix socket**. Its bounded, correlated JSON protocol v1 is intentionally local-only, not an alternative peer/signature encoding. The browser's privileged client performs a private-capability handshake before sending any context. See [the implemented client and verification boundary](PRIVATE_COMPUTE.md).

## Network attachment and kill switch

The default browser mode is opportunistic, with a browser-specific kill switch off. Direct fallback is ordinary Internet access and carries no VOLPAROSSA privacy claim. With the switch on, direct access must remain blocked if the daemon crashes or disconnects; a preference or toolbar badge is not enforcement.

Backend enforcement and mode transitions should live in the core. The browser UI requests and reports that state through a scoped capability. Its lifecycle must not disconnect unrelated consumers, change global core security defaults, or mutate the development host's network. Network proofs belong in disposable namespaces/VMs.

Required transition coverage:

- No overlay available at startup: ordinary access only when fallback is enabled.
- Overlay becomes ready: new eligible traffic uses the verified route; existing flows are not silently moved to a different exit.
- Overlay fails: strict mode blocks; opportunistic mode may open new direct flows with the correct visible status. Do not replay non-idempotent requests automatically.
- Policy denial, malformed authority or unknown result: do not interpret these as permission for an ordinary-Internet retry.
- DNS, IPv4/IPv6, HTTP/3, WebRTC and browser background traffic are covered; an HTTP-only proxy is not a complete kill switch.

## Existing Firefox AI controls

The first UI target is Firefox's existing AI sidebar and explicit selection/page actions, not an unrelated remote chatbot or a replacement browser UI. Source inspection confirms a custom-provider hook in `browser/components/genai/GenAI.sys.mjs`, but a URL preference alone does not connect the daemon or establish privacy.

Private prompts/page content must not appear in provider URL query strings, browser history, diagnostic logs, public cache publications or training datasets. Send explicit actions through a local, authenticated, bounded interface with cancellation and resource admission. Page content and model output are untrusted data, never privileged commands. Local private execution and explicitly public peer work must remain distinct.

A successful transport or complete model response does not prove answer quality. Keep task status, local/peer execution and failure visible. No automatic export to a third-party AI provider when VOLPAROSSA compute is unavailable.

### Cooperative public-task integration

`VOLPAROSSA AI (cooperative network)` is a separate provider (`volparossa:public`),
not a broader interpretation of the existing private protocol. Configure
`browser.volparossa.compute.public_socket` with the owner's already running
`volparossa compute public-serve` socket. The core operator, not a webpage, fixes
the agent connection, signing identity, model/runtime assets and eligible peers.
No runtime/model download is triggered by opening the panel.

The privileged `VolparossaCooperativeCompute.sys.mjs` client uses public-cooperative
IPC v1: four-byte big-endian length, correlated JSON, 32768-byte request and
65536-byte response bounds. A question is at most 512 UTF-8 bytes; explicitly
public context is at most 4096. Capability negotiation is separate from proof of
available workers. Public tasks use the core's existing signed document packages,
peer execution and hierarchical answer synthesis; there is no browser-side AI
implementation or replacement remote chatbot.

`VolparossaCooperativePanel.sys.mjs` requires a selected license and two unchecked
confirmations: sharing rights and consent to disclose the exact reviewed text.
Editing the text/license resets those confirmations. Native **Ask only prefills**;
neither opening the provider nor prefilling connects to the service or starts work.
The private provider and its IPC remain unchanged. Public results report actual
contributing peers and synthesis levels, separately from merely selected peers.
All output is literal text. Incomplete answers and unconfirmed cleanup stay visible
as such; an EOS or complete workflow is not a quality guarantee.

Cancellation applies to the connection's task and waits for the core's terminal
cleanup result. Public publications and receipts may remain: cancellation is not
a promise of remote erasure. This interface does not provide confidential peer
inference; confidential browser context still uses the separate private-local mode.

Focused checks include executable JavaScript transport/consent contracts with inert
test doubles. `scripts/smoke_cooperative_compute.py` is the separate **real** Gecko
driver for a core-owned disposable KVM proof. It waits for an independent no-work
check before clicking consent, requires a completed two-peer synthesized answer,
then waits for observed live work before cancelling a second task. It exports only
closed metadata and the rendered answer's hash. Core evidence must independently
establish real workers, protected relay traffic, retained receipts and cleanup.
The combined run and a full Firefox 157 source build remain pending; passing pure
tests or preparing the exact-source patch does not complete either proof.

The existing source preparer can reuse verified local upstream files without
network access:

```sh
python3 -B scripts/prepare_compute_source.py --output build/cooperative-source \
  --source-directory /absolute/path/to/exact/original
```

Every original is still checked against `patches/firefox-source.json`; source
preparation is not a browser build. Pure tests use Python and Node (`node` on PATH,
or an explicit `VOLPAROSSA_TEST_NODE` executable), without launching Firefox.

## Shared cache

Integrate where Firefox has authenticated the origin and can distinguish public, reusable content from authenticated/personal content. Preserve freshness, validators, partial-object verification and origin fallback. Private browsing does not become a shared-cache or training opt-in. A page's own script cannot authorize publication of another origin's protected content.
