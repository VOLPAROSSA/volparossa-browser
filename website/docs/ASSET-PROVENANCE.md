# Asset provenance and visual decisions

The primary reference is `01-browser-station-SHARED-BRAIN-UPDATED.png` from the
uploaded Browser station brief. `reference-register.json` records the original
reference hashes. The original-style-only reference informed colour and station
materials, never the obsolete local-only AI positioning.

## Original station artwork

Five clean illustrations were generated with `image_gen` on 31 October 2026.
The exact prompts are in `masters/generation.json`. Untouched PNG masters are
retained; the site ships only the responsive compressed exports.

| Master | Page use and focal treatment |
| --- | --- |
| `hero-station.png` | Glass canopy, burgundy left copy area, branching tracks, natural red fox and brass switch. The mobile crop preserves the fox and switch together below the copy. |
| `firefox-station-surround.png` | Warm ivory station context, walnut luggage and fine foliage. It spans the whole second section and fades into the paper background, avoiding a rectangular background seam behind the screenshot. A subdued edge is reused in development. |
| `shared-foundation-station.png` | Tracks, public exchange pavilion and several cooperating peer workspaces. Desktop uses the panorama. Three deliberate mobile crops preserve the separate meanings, including multiple workspaces in the shared-brain crop. |
| `connection-routes.png` | Two matched halves: green open route and red closed barrier. Each half is exported separately and paired with readable HTML labels; neither reports a live visitor state. |
| `closing-station.png` | Resting fox on a station bench, overlooking the open valley. Full-width scene behind a soft copy gradient; a distinct mobile crop retains the fox and landscape. |

`scripts/export_images.py` records crop coordinates and exports AVIF, WebP and
JPEG without enlarging scene pixels. `asset-manifest.json` records every master,
export, icon and notice with byte size, dimensions where applicable and SHA-256.
The original references, long mockups and generated lettering are not used as
page content. All headings, statuses, controls, captions and links are HTML.

## Genuine upstream assets

The four extension icons are byte-identical files extracted from the signed XPI
packages in Browser's existing lockfile. Downloads were hash-checked before
extraction; the extensions were not installed. `assets/upstream/sources.json`
records package versions, URLs, SHA-256, icon paths, hashes and upstream sources.
Their original GPL-3.0, MPL-2.0 or MIT notices accompany the icons. Original marks
take priority over the concept's generated approximations.

The Firefox mark is Mozilla's unchanged official browser SVG, identifying and
linking to the upstream Firefox product. Its source and the trademark-policy URL
are registered in `assets/upstream/firefox-source.json`. Attribution and the
absence of an official Mozilla association are stated on the page. The mark is
not used as VOLPAROSSA's identity.

The Firefox interface example is a real whole-window screenshot from isolated
Firefox 153.0, build `20260722042904`, using its built-in dark theme. The local
fixture is `masters/firefox-example.html`; its scenic crop derives from the
closing master. It contains a fictional article and no personal data. A fresh
profile was created under the task workspace, with no account or extensions.
The screenshot was captured through Marionette's chrome context at 1280 × 850.
The actual loopback URL and automation indicator are retained. No browser pixels
were retouched. The image is labelled as an upstream interface example, not as
the VOLPAROSSA integration. Original PNG and fixture are retained, separately
from the generated station scenes.

The central Shared Brain interface is original, responsive HTML/CSS. It shows a
fictional article selection, the exact `VOLPAROSSA Shared Brain` title, development
status, `Ask VOLPAROSSA` and `Cancel` labels. These are noninteractive illustration
elements, hidden from assistive technology in favour of the normal explanatory
steps and figure caption. It has no input, model connection or simulated answer.

Lucide icon source geometry is pinned to
`a630cec77eac6568b989e87e4947a8bfe45caacd`; original SVG files, Git blob hashes
and ISC license are retained. `waypoints` depicts equal connected points in the
cooperative-intelligence illustration. No hand-drawn gear or satellite geometry
is introduced. The existing theme's GitHub mark and original VOLPAROSSA logo
remain unchanged.

Public notices are in `assets/CREDITS.txt`, linked from the product page. Software
license notices do not claim ownership of upstream marks or endorsement.
