# Project VOLPAROSSA Browser

A Firefox-based client for **VOLPAROSSA — the Decentralized Intelligent Cooperative Network**.

The browser is an integration of the reusable [VOLPAROSSA core](https://github.com/VOLPAROSSA/volparossa), not another implementation of its network, shared cache or cooperative compute layer. Other applications can use the same core interfaces.

## Development status

This is **not yet a working VOLPAROSSA-enabled Firefox distribution**. The first executable slice configures privacy defaults in a separate, workspace-local Firefox installation. A real, isolated Firefox ESR 140.16 smoke verified all 18 preferences, native Strict tracking protection and preservation of user choices after restart. The daemon connection, browser kill switch and compute UI are still being built.

The upstream source is Mozilla's Firefox. Original integration code uses this repository's GPL-3.0-only license; upstream files and modifications retain their applicable licenses and notices. No Mozilla source tree or executable is silently downloaded by the browser.

## The intended experience

- **Network:** use VOLPAROSSA when available. The browser-specific kill switch starts **off**, allowing ordinary Internet access when the overlay is unavailable. Switching it on must prevent direct fallback, including DNS, UDP/HTTP/3 and WebRTC paths—not just page HTTP requests. A visible connection indicator distinguishes protected access from ordinary access, which exposes the user's ordinary public IP.
- **Cache:** use the core's verified shared-content retrieval when applicable. Cookies, private pages and selected AI context are not permission to publish or train on those bytes. Public HTTPS cache hits must retain origin authenticity; neither TLS interception nor trusting a peer's claim is a substitute.
- **Compute:** connect Firefox's existing AI sidebar and explicit page/selection actions to VOLPAROSSA. Browser-private context stays local unless the owner explicitly authorizes a supported sharing mode. No Mozilla account or automatic cloud-AI fallback is required.
- **Privacy defaults:** telemetry, Mozilla account integration and sponsored suggestions are off; Enhanced Tracking Protection starts in strict mode. Default settings remain changeable. Browser sandboxing, certificate verification and security-update mechanisms are not disabled to achieve this.

## Integration boundaries

The browser has its own lifecycle and network policy. It must not silently weaken other applications' core kill-switch defaults or turn on relay/exit participation without the required contribution acknowledgement. Availability fallback is distinct from a policy denial: a rejected VOLPAROSSA request must not be silently retried directly as a way around that decision.

Firefox source integration is tracked against exact upstream revisions. The existing Debian Firefox ESR executable can be used for isolated development checks, but that does not demonstrate that a different Firefox revision builds or works.

See [privacy defaults](docs/FIREFOX_PRIVACY_DEFAULTS.md), [upstream provenance](docs/FIREFOX_PROVENANCE.md) and the [core integration contract](docs/CORE_INTEGRATION.md).
