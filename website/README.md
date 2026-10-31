# VOLPAROSSA Browser website

A static, English product page at **https://volparossa.org/volparossa-browser/**.
The station design presents Firefox as the foundation and VOLPAROSSA's shared
brain as the intended architecture. The isolated local-model trial is described
only as scoped development evidence. Publishing this page does not release a
finished browser distribution.

## Build and preview

Python 3 is the only build dependency. Run from the repository root:

```sh
python3 website/scripts/build_site.py
python3 website/scripts/check_site.py
python3 -m http.server 8777 --bind 127.0.0.1 --directory website/preview
```

Open `http://127.0.0.1:8777/volparossa-browser/`. Previews contain
`noindex,nofollow` and no sitemap. All runtime assets are local to the project
directory. No Firefox build, extensions, Core service, AI model or external
JavaScript is needed to build or view the website.

```sh
python3 website/scripts/build_site.py --production --output website/dist/volparossa-browser
python3 website/scripts/check_site.py --production --output website/dist/volparossa-browser
```

`--origin` and `--base-path` configure canonical, Open Graph and sitemap URLs.
The default path is `/volparossa-browser/`; this page does not install a root
`CNAME` or change DNS. Preview and production output directories are ignored.

## Source and shared theme

- `src/index.html`: eight semantic sections, ordinary links and English copy.
- `src/browser.css`: page composition and responsive layouts only.
- `src/content.json`: metadata, repository and the single documentation target.
- `vendor/volparossa-theme`: unchanged source-only theme 1.0.3 export, pinned to
  `d1900a7928dd31fcf70e07bbec60f3be2edd5348`. The builder verifies its file hashes
  and uses its header, footer, layout, navigation helper, fonts, mark and scripts.
- `masters/`: untouched generated scene masters, generation prompts and the
  original isolated Firefox UI capture. These are never deployed.
- `assets/`: committed responsive exports, original upstream icons and notices.
- `docs/`: source verification, asset provenance and acceptance results.
- `review/`: selected actual-render screenshots for reviewers.

The only interactive elements are navigation links and the shared native mobile
menu. The Shared Brain UI is a static, labelled HTML/CSS design illustration.
Its controls do not accept input or contact a service. Static route illustrations
do not indicate the visitor's actual network state.

## Asset maintenance

Normal builds use committed exports. To regenerate them, use **Pillow 12.3.0**
with AVIF and WebP support in a suitable existing development environment:

```sh
python3 website/scripts/export_images.py
```

Crop coordinates, output dimensions, hashes and sizes are recorded in
`asset-manifest.json`. `--inventory-only` refreshes that register after a reviewed
notice or upstream-source change. The five original generated PNGs are retained.
No upscaling of the scene masters is used. The original Firefox screenshot is
exported separately, with no generated or retouched browser controls.

## Optional browser review

The exact development dependency versions are locked. After `npm ci` in this
directory and using an existing compatible Playwright browser installation:

```sh
CHROME_PATH=/usr/bin/chromium node scripts/browser_check.mjs
```

The preview server must already be running. `SITE_QA_URL`, `QA_OUTPUT` and
`QA_ENGINES` override the URL, report directory and engines. The default engines
are Chromium and Firefox; add WebKit only where its system dependencies exist.
The checks cover nested paths, responsive rendering, accessibility, menu and
keyboard access, reduced motion, no JavaScript, enlarged text and reflow.

Repository source checks remain independent of website verification:

```sh
python3 -B -m unittest discover -s tests -v
node --test tests/ubo_actor_contract.mjs
node --test tests/filter_*.test.mjs
git diff --check
```

Run the file-mode tests with the standard development/CI umask `022`; a restrictive
`077` masks the intentionally public test-file mode and invalidates that fixture.
This changes only the test shell's umask, not the runtime's privacy defaults.

## Publication

The `Browser website` workflow validates every website pull request and main push,
and uploads a noindex review artifact. Its manual `workflow_dispatch` on `main`
builds and checks the production artifact, then deploys only that directory to
the repository's existing GitHub Pages configuration. Deployment is restricted
to this repository and receives Pages/OIDC permissions only in the deploy job.

The live `/docs/#browser` entry was separately merged and verified before using
it in product CTAs. The central application registration must remain unpublished
until the product URL has actually been deployed and checked. Website publication
does not imply a completed Browser/Core integration or promote Browser into the
homepage's featured application selection.
