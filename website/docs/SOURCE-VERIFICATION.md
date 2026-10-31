# Content and implementation evidence

Reviewed 31 October 2026. Source verification is read-only. This website work
does not change Firefox, extensions, preferences, profiles, network policy,
model execution, Browser/Core runtime code or the open runtime PR #4.

## Authority and selected design

The user's current request authorises building and publishing the Browser page.
The supplied BUILD-INSTRUCTIONS, SHARED-BRAIN-CORRECTION and ASSET-BRIEF were read
in full, together with the sources list and both station references. The updated
station design is primary; the old local-only AI copy is superseded. The older
attachment restriction to preview/PR-only is superseded by the current explicit
publication request. No DNS, CNAME or organisation-policy changes are involved.

## Source snapshots

| Source | Verified revision | Use |
| --- | --- | --- |
| Browser main | `66d36cffd19e34374d8648c8f0cf02defd4085a5` | Runtime boundaries, privacy preferences, signed extensions and local trial |
| Core main | `a9248d6dbbed75bc255827580a97ff1aaf321e2a` | Shared-brain product goal, cooperative agents and protected-execution boundaries |
| Website theme | `d1900a7928dd31fcf70e07bbec60f3be2edd5348` | Exact shared export, family navigation and published Core destination |

Repository references:

- [Browser README](https://github.com/VOLPAROSSA/volparossa-browser/blob/66d36cffd19e34374d8648c8f0cf02defd4085a5/README.md)
- [Core integration](https://github.com/VOLPAROSSA/volparossa-browser/blob/66d36cffd19e34374d8648c8f0cf02defd4085a5/docs/CORE_INTEGRATION.md)
- [Private compute trial](https://github.com/VOLPAROSSA/volparossa-browser/blob/66d36cffd19e34374d8648c8f0cf02defd4085a5/docs/PRIVATE_COMPUTE.md)
- [Firefox privacy defaults](https://github.com/VOLPAROSSA/volparossa-browser/blob/66d36cffd19e34374d8648c8f0cf02defd4085a5/docs/FIREFOX_PRIVACY_DEFAULTS.md)
- [Bundled extensions](https://github.com/VOLPAROSSA/volparossa-browser/blob/66d36cffd19e34374d8648c8f0cf02defd4085a5/docs/BUNDLED_EXTENSIONS.md)
- [Firefox provenance](https://github.com/VOLPAROSSA/volparossa-browser/blob/66d36cffd19e34374d8648c8f0cf02defd4085a5/docs/FIREFOX_PROVENANCE.md)
- [Complete project goal](https://github.com/VOLPAROSSA/volparossa/blob/a9248d6dbbed75bc255827580a97ff1aaf321e2a/docs/development/GOAL.md)
- [Cooperative agents](https://github.com/VOLPAROSSA/volparossa/blob/a9248d6dbbed75bc255827580a97ff1aaf321e2a/docs/services/DECENTRALIZED_AGENTS.md)

## Public claims and their limits

| Page claim | Evidence and boundary |
| --- | --- |
| Shared brain is the intended Browser architecture | GOAL explicitly requires the shared VOLPAROSSA brain, not exclusively a local model. DECENTRALIZED_AGENTS describes suitable distributed work, combining and assessing results, and eligible improvements to reusable models. The page labels the complete connection as in development. |
| Owners' work comes first | Cooperative work depends on compatible agents, available resources, task policy and owner priority. The panorama shows several peer workspaces, not a central intelligence server. |
| Private context needs protected execution | Transport encryption alone does not protect against the execution host's operator. Shared private execution remains unfinished. Reading a page or asking a question does not automatically authorise publication or training. The illustration does not show private content distributed to peers. |
| Privacy defaults with retained choice | The documented ESR trials cover effective preferences and retained user choices. These are scoped findings, not a claim of zero background communication or universal tracking prevention. |
| Included extensions, removable by the user | Four exact signed packages are pinned in defaults/extensions.lock.json. Documented trials cover activation, disable/remove controls and synthetic consent/blocking. They do not prove compatibility on every site. Decentraleyes is local library substitution, not the shared Core cache. |
| Local execution proof | The documented 2026-09-29 trial (Core run 36614266330) used isolated Debian Firefox ESR 140.16.0, real Core and pinned SmolLM2-360M with synthetic context. It checked text delivery, cancellation, disconnect and cleanup. It does not prove complete shared-brain Browser integration, private peer inference, general model quality or the native Firefox 157 provider path. |
| Routes and shared cache | Full browser attachment, browser-wide kill switch and verified eligible public-content reuse remain in development. The browser kill switch starts off; ordinary fallback uses the ordinary Internet. Policy denial must not trigger a silent direct retry. Private pages and cookies are not automatically shared. |

The local-trial revisions are Core
`5beb8d2d79e44d2b4d2e4e3bb20aa4e67701e03b` and Browser
`4b1fdbe105c5cc23154778664c8d9fca9ef2454b`.
Firefox integration target 157.0.1 at upstream
`47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1` is a separate source pin; it is not
the browser used in that trial. The Firefox 153 screenshot made for this site
is merely an upstream UI example and supplies no integration evidence.

PR #4 was still open at head `c3311ebedcc02dc45ff9c9822cee62f091609f86` when
checked. No claim depends on treating its unmerged work as main-branch evidence.

## Central documentation

[Homepage PR #14](https://github.com/VOLPAROSSA/volparossa.github.io/pull/14)
adds the separate Browser reading path, with eight real source links. It was
merged at `e23beb78f01de4aa4ef86a55b275b017a3241d78`; Pages run 41248934282
succeeded and the live `/docs/#browser` anchor was checked before replacing the
temporary `/docs/#applications` fallback in `src/content.json`.

The Browser application publication flag is intentionally changed only after the
product page's own successful deployment and live verification. Browser remains
outside the homepage's featured Code/Image/Cloud selection.
