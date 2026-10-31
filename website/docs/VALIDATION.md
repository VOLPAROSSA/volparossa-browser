# Browser website acceptance review

Reviewed 31 October 2026 against the updated station reference. The page is an
English, responsive static website, with eight separately rendered sections.
Screenshots and machine-readable summaries are in `../review/`.

## Build and repository checks

- Offline Python build and static checks pass for preview and production under
  `/volparossa-browser/`, including local assets, anchors, metadata and notices.
- An independent rebuild produced identical hashes for all 140 output files.
- Alternate `https://volparossa.github.io/volparossa-browser/` canonical, Open
  Graph and sitemap configuration was checked separately.
- The shared theme's original file hashes and Lucide source geometry match their
  pinned revisions. The asset inventory matches the committed originals/exports.
- Existing Browser checks pass: **163 Python tests and 241 Node tests**. The local
  shell initially used umask `077`, which masked an intentionally public `0644`
  test fixture. Running with standard CI umask `022` resolved that environmental
  mismatch without changing any test or runtime source.
- Twenty distinct external page destinations return HTTP 200, including the
  newly published `/docs/#browser` anchor and the published Core page. The
  Adaptive Tab Bar Color upstream repository redirects to its current owner.

## Rendered browser review

Chromium and Firefox were used for real rendering, with fonts and all visible
lazy images decoded before review. The main matrix includes **13 profiles**:

- Chromium: 320 × 740, 390 × 844, 768 × 1024, 1440 × 900, 1920 × 1080 and
  landscape 844 × 390.
- Firefox: 390 × 844, 1440 × 900 and 2560 × 1440.
- Chromium: JavaScript disabled, text enlarged to 200%, 400% reflow equivalent
  (360 × 225 CSS pixels), and image requests disabled.

Four supplementary checks cover 2560-wide Chromium and Firefox, a 390-wide
device at DPR 3, and enlarged-text geometry. The Core panorama never overlaps
its heading. There is no unintentional horizontal overflow, missing visible
image, header/copy overlap, console exception, failed page asset or external
runtime request in ordinary profiles. The intentionally blocked-image scenario
retains the page's text, structure and links.

Keyboard checks verify the first skip link, visible focus and transfer to main
content. The native mobile menu opens without JavaScript; with the shared script
it also closes with Escape and outside clicks. Reduced-motion scrolling is
honoured. The breadcrumb identifies Browser; the shared family navigation does
not falsely mark Applications as the current page.

The complete Lighthouse production check scores **100 accessibility**, and axe
finds **zero WCAG A/AA violations** at 390 and 1440 pixels. Manual review covered
image/text contrast, focus visibility, reading order, status labels and the
noninteractive nature of the target UI. These are scoped checks, not an
accessibility certification.

WebKit could not start: this environment lacks GTK 4, Graphene, HarfBuzz ICU,
Manette, Hyphen and GLESv2 libraries. No Safari result is claimed and no host
packages were installed as part of this website task.

## Visual comparison and refinements

- Hero: generous station canopy, natural fox and brass switch remain visible;
  the desktop title matches the two-line reference. Mobile gives the copy its
  own space and retains both fox and switch in the art-directed crop.
- Firefox: original upstream mark and an actual isolated browser-window capture,
  with the full interface visible. The station background fades at section edges;
  mobile limits it to the figure area, preserving calm ivory behind the copy.
- Extensions: four genuine upstream icons, equal desktop columns and a compact
  two-column mobile layout. Generated concept logos are deliberately replaced by
  the actual project marks and user-removal controls remain explicit.
- Core: the three image regions correspond to network, shared content and
  cooperating workspaces. A wide-screen refinement gives the panorama sufficient
  height and aligns the three columns with their scene regions. Mobile preserves
  multiple workspaces rather than implying an isolated local assistant.
- Shared Brain: large HTML/CSS target interface, selected fictional passage,
  cooperative-node symbol and explicit development status. The complete workflow,
  shared-model goals, owner priority and private-context boundary remain readable.
- Connection: matched open/closed route scenes, readable labels, default-off
  kill-switch copy and a separate policy-denial boundary. No live visitor state.
- Development: scoped evidence and unfinished work remain distinct; local proof
  does not become the final product architecture.
- Closing: resting fox and landscape continue across the section behind a soft
  gradient; the mobile crop preserves the scene below the contribution links.

## Performance

Lighthouse **13.6.0**, local production artifact, simulated mobile network:
412 × 823 CSS pixels, DPR 1.75, 150 ms RTT, 1,638.4 Kbit/s nominal throughput,
4× CPU slowdown. Raw configuration and results are in the review summary.

| Metric | Measured |
| --- | ---: |
| Performance | **96 / 100** |
| Accessibility / best practices / SEO | **100 / 100 / 100** |
| Largest contentful paint | **2.70 s** |
| Total blocking time | **0 ms** |
| Cumulative layout shift | **0.0000315** |
| HTML + CSS + JavaScript, gzip | **20.5 KB** |
| 390 px initial transfer, Chromium DPR 1 | **352.5 KB** |
| 390 px initial transfer, Firefox DPR 1 | **262.0 KB** |
| All loaded image transfers, 390 px DPR 1 | **358.4 KB** |
| All loaded image transfers, 390 px DPR 3 | **731.7 KB** |
| All loaded image transfers, 2560 px DPR 1 | **852.1 KB** |

The browser transfer totals include resource timing's response overhead on a
local server. These are reproducible lab observations, not production field
metrics. The earlier preview correctly scored lower on SEO because it is
intentionally `noindex`; the explicit production build is indexable.

## Publication boundary

GitHub Pages is already configured for Actions at the project's existing URL.
The workflow publishes only a checked production directory when manually
dispatched on main. The central Browser reading path is already live; the
application directory flag is enabled separately only after the product URL and
deployed assets have been verified. No runtime release or product-readiness
claim is implied by this website deployment.
