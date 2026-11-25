# Browser/core integration contract — development requirements

Private-compute IPC v1 has an executable client and Firefox source patch. Two ordinary tabs in source-built Firefox 157.0.1 each verified 32 MiB over the scoped Core HTTPS gateway. General network and shared-cache integration remain in progress. [Gateway evidence](NETWORK_GATEWAY.md)

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
A real disposable trial with Browser `326ce0f2` and Core `7621087a` reached
the completed ESR panel, joined seven signed peer jobs through a four-answer
EOS frontier and one synthesis, and observed cancellation of a second task.
The run failed at the final worker-lifetime observation. The follow-up on
Core `434431d8` stopped earlier while waiting for peer inventory.
[Joined peer results and cancellation](https://github.com/VOLPAROSSA/volparossa/actions/runs/37207405766)
· [Inventory failure](https://github.com/VOLPAROSSA/volparossa/actions/runs/37209763534)
· [Recorded evidence and observer correction](https://github.com/VOLPAROSSA/volparossa/blob/0efdac51259844c75a2e2b67313de1ea8ac5f7e0/docs/archive/IMPLEMENTATION_STATUS_20261023.md.txt#L8884)

On 19 November 2026 the same Browser source and Core `0efdac51` completed
21 signed jobs on two discovered peers. The Firefox ESR panel displayed the
verified answer after two synthesis levels and cancelled a second live task.
Independent observers saw all 23 workers finish. The initial route had expired
by the final observation, and the trial lacked per-transfer route observations.
The full KVM check failed. The separate Firefox 157.0.1 source build compiled
Browser `9e086168` with the cooperative compute and panel modules registered in
Mozilla's build and the provider selector wired into the sidebar. Selecting that
provider and completing a cooperative task in the source-built browser remain open.
[Original trial](https://github.com/VOLPAROSSA/volparossa/actions/runs/44434704508)
· [Panel, worker and route receipts](https://github.com/VOLPAROSSA/volparossa/tree/504cc62de2169079203ff863f7f9fef962657356/docs/development/evidence/browser-cooperative-44434704508)
· [Native source build](https://github.com/VOLPAROSSA/volparossa/actions/runs/44449063301)
· [Firefox source integration](https://github.com/VOLPAROSSA/volparossa-browser/blob/9e0861687a7ef2a032155edeebcb19bf0fef0712/scripts/prepare_compute_source.py)

The next trial on Core `d6a4f056` observed 16 workers. The client recorded 46
exact discovery replies that omitted at least one requested provider. The route
observer counted 281 completed flows before a Paths query timed out. The panel
ended before the first answer. The guest removed all its network objects and
the before/after host snapshots matched.
[Discovery and route observations](https://github.com/VOLPAROSSA/volparossa/actions/runs/44451658759)

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

## DeGoog search

The planned default for a fresh Firefox profile is DeGoog. Retain an existing
engine selection on upgrade and let users change the default through Firefox.
Reuse the upstream aggregator and evaluate its indexer against DICN content
and privacy requirements. Search must tolerate the loss of an instance without
depending on a fixed VOLPAROSSA endpoint. [Upstream DeGoog](https://github.com/degoog-org/degoog)

Upstream installs without a result source and can forward a query to several
enabled engines. Ship a usable, authorized search source or index with the
integration. Account for every recipient, including any suggestion provider
that receives text before submission. [DeGoog engine and suggestion configuration](https://degoog-org.github.io/docs/)

Keep the query inside its authorized search path. Personal queries, history,
identifiers and profiles stay out of shared cache keys, discovery records,
indexes, shared logs and public peer compute inputs. A public result URL can
contain query terms or credentials. Admit independently verified public sources under
their reuse rights and cache policy. Use Core publication and Cache to share
eligible sources and index updates with origin, version, expiry and validators.
Use Storage for encrypted index checkpoints and verify restored data against
the current authorized publication before reuse.
Use the shared brain on eligible public material to update the index, improve
ranking and generate labelled summaries citing the underlying sources. Personal
searches grant no permission to send prompts to public workers or train models.

The [public index integration trial](../.github/workflows/degoog-index.yml)
uses DeGoog 1.2.0 at `1f7d2f17`. Only three hash-pinned, GPL-licensed Browser
documents and their public headings enter its native SQLite index. A second
process imports a digest-checked export into a fresh data directory and queries
DeGoog’s built-in engine after the producer has gone away. Its isolated network
namespace has only loopback. It checks rejection of altered bytes and looks for
a synthetic private query marker in process output and retained trial files.
The first run recovered all three sources from a 45,056-byte snapshot, refused
the altered copy and made zero engine fetches. A portable checkpoint additionally
checks every public URL, title, snippet and normalized heading against enrollment
before the isolated workflow can export the original producer database for a peer
delivery trial. [Original receipt](evidence/degoog-public-index-45513380736.zip) ·
[Hosted upstream index trial](https://github.com/VOLPAROSSA/volparossa-browser/actions/runs/45513380736)

Core published the original 45,056-byte SQLite checkpoint. A fresh reader
recovered it from an independently enrolled holder after the publisher's source
and key had been removed. A separate, loopback-only DeGoog process then imported
the recovered bytes and returned each of the three enrolled documents. Its
unmatched query returned no documents and its engine made zero external
requests. The archived search database has SHA-256
`7ec92c63b697c290d7634027be4e41e2eaab237459100782910183dd62fe12e3`.
[Original producer checkpoint](evidence/degoog-public-checkpoint-45517799338.zip) ·
[Original delivery and search](evidence/degoog-peer-45518350752.zip) ·
[Hosted end-to-end run](https://github.com/VOLPAROSSA/volparossa/actions/runs/45518350752)

In the following trial the client could see a forwarded exit but could not
select an eligible protected path at startup. It stopped before a holder was
selected. The guest removed its objects and its network state matched its
pre-trial snapshot.
[Original startup failure](evidence/degoog-peer-45521482324.zip) ·
[Hosted run](https://github.com/VOLPAROSSA/volparossa/actions/runs/45521482324)

The full upstream HTTP search handler can record normalized queries when indexing
is enabled. A private live search integration still needs a separately verified
query path, independent publication authority and actual Firefox profile tests.
[Upstream query recording](https://github.com/degoog-org/degoog/blob/1f7d2f172aad3ed5e5a69aa227b384573203742c/src/server/search/handlers.ts) · [Upstream AGPL license](https://github.com/degoog-org/degoog/blob/1f7d2f172aad3ed5e5a69aa227b384573203742c/LICENSE)

Functional acceptance in a real browser will cover these cases.

- Search from the address bar in a fresh profile and retrieve a known result
  without manual setup. Select another engine, then restart and upgrade.
  Each search must reach the selected engine. Take a DeGoog
  instance offline and exercise an authorized alternative. Verify visible
  availability fallback with the browser kill switch off, and that a policy
  denial or the enabled kill switch prevents a direct retry.
- Run the same public search workload cold and warm across independent peers.
  The warm run must reuse eligible objects with fewer external requests and
  fewer duplicate compute jobs. Let another peer index a changed public source
  and retrieve the updated result with the first indexer offline. Reject forged
  results and expired entries. Compare ranking with upstream-only results on
  the same reference queries and check summary claims against the cited source
  versions. Keep ordinary results available when summaries lack support.
- Use distinct markers for a private query, partially typed text, history entry
  and profile. Inspect requests from Firefox and DeGoog, including calls to
  search and suggestion providers, peer traffic, cached and indexed objects,
  logs and model inputs. Verify that each recipient receives only what its
  authorized function requires and that fallback does not widen disclosure.
