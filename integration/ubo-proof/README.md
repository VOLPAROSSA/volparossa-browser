# Isolated signed-uBO closed-actor experiment

This is an **explicitly invoked disposable-profile experiment**, not a default
browser integration. Attempt 05 passes the scoped runtime proof. Earlier attempts
remain failed evidence: attempt 01 stopped before
launch because the fresh checkout had no `build/` parent; attempt 02 reached
`user-enroll` and failed with `TimeoutError`. Its original report and successful
cleanup receipts are retained. A concrete fixture timeout mismatch has since
been corrected, without claiming it was the unique cause. Attempt 03 then reached
the actor's enrollment step but failed with an unclassified execution error;
its original report and complete cleanup remain preserved. Fixed phase/reason
envelopes now distinguish journal, actor, storage and reload failures without
exporting unknown exception text. The parent refuses failed or malformed replies
before they can authorize journal writes or enrollment. The inert tests do not
establish that Firefox's private interfaces interoperate with this adapter.
Attempt 04 failed at `actor_delivery` with the closed reason `ubo_proof_other`;
its original receipt and successful cleanup are retained. Its underlying cause
is not yet proven. The next fixture revision places the four unchanged owned
modules under the fresh profile's `chrome/volparossa-ubo-proof/` before launch,
rather than assuming the extension process can read arbitrary checkout files.
Attempt 05 passes with that layout; this does not retroactively establish the
unique cause of attempt 04 or its exact internal mutation state.

## Passing runtime evidence

The [closed report](../../docs/evidence/ubo-closed-actor-05.json) records two
campaigns, six browser sessions and four restart transitions. Each campaign
records refusal of all six optional purposes, zero advertising/optional endpoint
requests and a successfully loaded essential resource. An independent positive
control first confirms the advertising endpoint can be reached.

Both actor enrollments preserve the ten stock lists and observe two actual uBO
reload broadcasts. Removing the extra list through the original UI or through
the actor restores probe traffic. Opt-out survives restart in both campaigns
and original signed-addon reinstallation in the user campaign. No extra restart
after that reinstallation was tested.

Root executed the trial once on the final layout; an independent read-only
review checked the report, runtime/addon/module hashes and cleanup. All temporary
profiles and raw browser logs were removed. The report is local test evidence,
not a signed attestation or a complete Python-dependency provenance record.

- Report SHA-256: `c0c6e30d2a52789b21e946612b5bedda79a612b28522f18b2e260cab746925d1`.
- Original outer-cleanup receipt SHA-256:
  `6ee015a7f3e5f48a1fdac7c0278b7a97aa66db0524395e816eac9a7ab4f543c1`.
- Executed harness SHA-256:
  `603a4bc100bb553d4d31eb1ba0a06a0facb174f872c09dbf2d7d898ab2bba3ed`,
  on Browser baseline `0d2d21337173b9beb1eb014cae05da9a8ea28afe` plus
  this experiment and the explicit fixture-port change.

The separate core filter-snapshot proof demonstrates protected distribution and
warm-cache retrieval. These are two distinct proofs: the combined browser/core
broker, production startup/expiry protection and automatic default enrollment
remain open.

The experiment uses the original signed uBlock Origin **1.75.0** package and the
retained Firefox ESR **140.16.0**, source
`d864999404b3032f682d74ccc60d1ce38c9ce609`, build `20260908152208`.
The complete runtime and all four signed extension packages are checked by the
existing staging validators; the controller also checks uBO's exact package size,
SHA-256, signed state, active identity and version before commands. Nothing is
repacked, installed globally or connected to the daemon.

The four staged module files must exactly match the reported source hashes and
bytes. Their fixed private directory must be new and contain only those names;
symlinks, wrong owners, unsafe modes and changed files are refused. Files are
read-only (`0400`), and verification runs before and after each browser session.
The bootstrap derives its fixed module location from `ProfD`; callers cannot
supply a module directory. Firefox's existing sandbox and the original read-only
runtime remain unchanged, with no additional sandbox file-access permission.

## Bounded mechanism

The browser creates an original `3p-filters.html` page through ESR140's
`ExtensionParent.HiddenExtensionPage`. A closed `JSWindowActor` belongs to that
specific browser, current window, extension principal and exact document URI.
Only `observe`, `enroll` and `revoke` are admitted. There is no script/eval,
debugger, arbitrary URL, generic uBO command, raw storage write or daemon access.
Only the fixed synthetic list `http://127.0.0.1:18765/filters.txt` is controlled.

The original page sends ordinary `applyFilterListSelection` messages with
`toImport` or `toRemove`, never a replacement stock selection. Read-only
`browser.storage.local.get` checks the two relevant persisted keys. Two
sequential ordinary `reloadAllFilters` calls drain a possible pre-change
coalesced load and then require a post-change load. Original
`staticFilteringDataChanged` broadcasts must identify all ten stock lists and
the expected supplementary membership; request acknowledgments alone do not
pass. The proposed runtime fixture additionally tests real blocked/unblocked
synthetic requests and Consent-O-Matic's recorded refusal.

A private browser-owned profile journal is independent of uBO's storage. It is
flushed before the first enrollment command. Pending or uncertain outcomes do
not permit another enrollment. Explicit revocation, or observed user removal,
records opt-out; it must survive profile restart and original-addon reinstall.
Journal writes use a private temporary file, atomic replacement and file flush.
This is not yet evidence for power-loss durability, profile reset/migration or
unobserved removal while the browser-side experiment is not running.

## Checks and runtime reproduction

Inert checks (use an already verified Node executable, no downloads):

```sh
node --test tests/ubo_actor_contract.mjs
python3 -B -m unittest discover -s tests -p 'test_ubo_actor.py'
python3 -B -m unittest discover -s tests -p 'test_consent_fixture.py'
```

The separate runtime test must be reviewed before execution. `--stage` is the
existing verified read-only ESR140 staging directory; `--output` must be a fresh
child of this checkout's `build/`. The wrapper creates a private profile and
loopback-only network namespace, mounts host/runtime read-only, cleans profiles
and raw logs, and writes bounded synthetic evidence. The signed XPI is unchanged.

```sh
nice -n 19 python3 -B scripts/smoke_ubo_actor.py \
  --stage /path/to/verified/firefox-consent-runtime \
  --output build/ubo-closed-actor-new
```

## Unsolved product boundaries

The actor starts after extension initialization. ESR140's extension `startup`
listeners are not a pre-background filtering barrier, and uBO can restore cached
filtering state before this adapter runs. HTTP errors, offline mode or an empty
list do not revoke loaded rules. Consequently this does **not** prove safe
startup/resume expiry, default automatic enrollment, a signed network-list broker
or Firefox source-build integration. Stock protection is never disabled as an
expiry workaround; the fixture's brief positive control deliberately disables
only its own disposable-profile uBO/Consent-O-Matic instances.
