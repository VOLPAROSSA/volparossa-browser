# Third-party provenance

Original VOLPAROSSA integration code is GPL-3.0-only. This does not relicense
Firefox, its modified upstream files, extensions or build tools.

- Firefox source is pinned to
  `47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1` (tree
  `4a1d6e73d48bc5888e4da37d655b360a14db18db`) at
  [Mozilla's upstream repository](https://github.com/mozilla-firefox/firefox/tree/47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1).
  Native changes retain MPL-2.0 notices; the existing GREASE test retains its
  public-domain notice. Exact originals and local patches are recorded in
  `patches/firefox-source.json` and `patches/firefox-network-ech.json`.
- Seven workspace-only Mozilla Taskcluster toolchain/sysroot archives are pinned
  by task, run, byte length and SHA-256 in `patches/firefox-native-build.json`.
  Archives and original notices are retained. Mixed component licenses and
  Mozilla trademarks require review before redistribution; the local build is
  not distribution clearance or proof of the full signed Taskcluster trust chain.
- Reused Rust 1.98.1 is verified against the exact inventory report and upstream
  manifest named in that same pin file; original toolchain notices are retained.
- The signed extension packages and their separate licenses are recorded in
  [Bundled extensions](docs/BUNDLED_EXTENSIONS.md). Existing ESR runtime provenance
  is recorded in [Firefox provenance](docs/FIREFOX_PROVENANCE.md).

See [native build inputs and reproduction](docs/NATIVE_BUILD.md). No third-party
binary or source tree is silently installed or added to the repository by these
integration modules.
