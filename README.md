# Project VOLPAROSSA Browser

A Firefox-based client for **VOLPAROSSA — the Decentralized Intelligent Cooperative Network**.

The browser is an integration of the reusable [VOLPAROSSA core](https://github.com/VOLPAROSSA/volparossa), not another implementation of its network, shared cache or cooperative compute layer. Other applications can use the same core interfaces.

## Development status

This is **not yet a working VOLPAROSSA-enabled Firefox distribution**. Three executable development slices are available:

- **Privacy defaults:** a separate, workspace-local Firefox installation. A real, isolated Firefox ESR 140.16 smoke verified all 18 preferences, native Strict tracking protection and preservation of user choices after restart.
- **Default extensions:** the same isolated ESR runtime verified signed activation of uBlock Origin, Decentraleyes and Adaptive Tab Bar Color, including persistent user choices to disable or remove them.
- **Private compute:** the real ESR 140.16.0 sidebar now passes a [combined core/model proof](https://github.com/VOLPAROSSA/volparossa/actions/runs/36614266330): one synthetic answer from the actual local 360M worker, rendered as text only after confirmed worker cleanup. Real Cancel/Disconnect and complete cleanup also pass. This evidence on `main` is ESR-specific; it does not prove the Firefox 157 native provider selector or general answer quality. See [the precise scope](docs/PRIVATE_COMPUTE.md#combined-browsercore-proof).

**Pending integration, not yet on `main`:** [PR #4](https://github.com/VOLPAROSSA/volparossa-browser/pull/4)
adds the scoped HTTPS gateway and ordinary-tab controller. Its recorded candidate
evidence includes a pinned Firefox 157 source build and a native tab rendering
HTTPS content through a **synthetic gateway**, using an explicit, hashed
JavaScript overlay on that build. This is useful native-browser integration
evidence, not proof that the new candidate works with the live core route, nor
that the complete integration has reached `main`. The earlier ESR/core proof
above remains a separate result.

The full daemon/network attachment, browser kill switch and shared-cache integration are still being built. Private compute does not send browsing context to public peer jobs or silently fall back to cloud AI.

The upstream source is Mozilla's Firefox. Original integration code uses this repository's GPL-3.0-only license; upstream files and modifications retain their applicable licenses and notices. No Mozilla source tree or executable is silently downloaded by the browser.

## The intended experience

- **Network:** use VOLPAROSSA when available. The browser-specific kill switch starts **off**, allowing ordinary Internet access when the overlay is unavailable. Switching it on must prevent direct fallback, including DNS, UDP/HTTP/3 and WebRTC paths—not just page HTTP requests. A visible connection indicator distinguishes protected access from ordinary access, which exposes the user's ordinary public IP.
- **Cache:** use the core's verified shared-content retrieval when applicable. Cookies, private pages and selected AI context are not permission to publish or train on those bytes. Public HTTPS cache hits must retain origin authenticity; neither TLS interception nor trusting a peer's claim is a substitute.
- **Compute:** connect Firefox's existing AI sidebar and explicit page/selection actions to VOLPAROSSA. Browser-private context stays local unless the owner explicitly authorizes a supported sharing mode. No Mozilla account or automatic cloud-AI fallback is required.
- **Privacy defaults:** telemetry, Mozilla account integration and sponsored suggestions are off; Enhanced Tracking Protection starts in strict mode. Default settings remain changeable. Browser sandboxing, certificate verification and security-update mechanisms are not disabled to achieve this.

## Useful extensions, included by default

- **uBlock Origin** blocks unwanted content with user-configurable filters.
- **Decentraleyes** serves supported common web libraries from its bundled local resources; this complements, but does not implement, VOLPAROSSA's shared network cache.
- **Adaptive Tab Bar Color** adapts the browser's colors to the page.

These are ordinary extensions, not mandatory components: disable or remove any of them in Firefox's Add-ons Manager. Packaging uses exact, hash-checked Mozilla Add-ons packages; Firefox still verifies their signatures. Native update behavior remains intact. See [the extension bundle](docs/BUNDLED_EXTENSIONS.md) for versions, permissions, licenses and the isolated installation test.

## Integration boundaries

The browser has its own lifecycle and network policy. It must not silently weaken other applications' core kill-switch defaults or turn on relay/exit participation without the required contribution acknowledgement. Availability fallback is distinct from a policy denial: a rejected VOLPAROSSA request must not be silently retried directly as a way around that decision.

Firefox source integration is tracked against exact upstream revisions. The existing Debian Firefox ESR executable can be used for isolated development checks, but that does not demonstrate that a different Firefox revision builds or works.

See [privacy defaults](docs/FIREFOX_PRIVACY_DEFAULTS.md), [private compute integration](docs/PRIVATE_COMPUTE.md), [upstream provenance](docs/FIREFOX_PROVENANCE.md) and the [core integration contract](docs/CORE_INTEGRATION.md).
