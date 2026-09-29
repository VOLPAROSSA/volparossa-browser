# Browser/core integration contract — development requirements

Status: integration work in progress, not an implemented API or a network-proof claim.

## One reusable daemon

Network selection, policy, shared-cache verification and compute scheduling belong in `VOLPAROSSA/volparossa`. Firefox-specific UI and source integration belong here. Changes to both repositories must name compatible core/interface versions; the presence of a socket alone is not a capability handshake.

The existing core control socket uses bounded, correlated Protocol Buffers (`CONTROL_PROTOCOL_VERSION = 2` at core `f4e6aa79`). It exposes status, roles, route connection, content transfer and public compute operations. It does **not yet** supply a complete browser-scoped network attachment or arbitrary private-chat API. The current private-task CLI is a separate owner-authorized execution path. Do not forward browser context into the public inference broker merely because that broker already exists.

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

## Shared cache

Integrate where Firefox has authenticated the origin and can distinguish public, reusable content from authenticated/personal content. Preserve freshness, validators, partial-object verification and origin fallback. Private browsing does not become a shared-cache or training opt-in. A page's own script cannot authorize publication of another origin's protected content.
