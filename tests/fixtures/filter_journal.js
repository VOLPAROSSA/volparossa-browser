// SPDX-License-Identifier: GPL-3.0-only
"use strict";

// The isolated driver launches this fixture in TWO real parent processes with
// the same disposable ProfD. No module-cache reset, addon or publication owner.
add_task(async function filter_journal_native_restart() {
  Assert.equal(Services.appinfo.processType, Ci.nsIXULRuntime.PROCESS_TYPE_DEFAULT);
  Assert.ok(["store", "reopen"].includes(_FILTER_JOURNAL_PHASE));
  const profile = do_get_profile();
  Assert.equal(profile.permissions & 0o7777, 0o700);
  const { openSelectionJournal } = ChromeUtils.importESModule(
    "resource://gre/modules/volparossa-journal/Journal.sys.mjs");
  const { FilterSelectionLifecycle } = ChromeUtils.importESModule(
    "resource://gre/modules/volparossa-journal/Selection.sys.mjs");
  // Public synthetic scope labels, not a signed publisher or filter grant.
  const binding = Object.freeze({ publisherKey: "a".repeat(64),
    name: "fixture-filter-selection", keyDigest: "b".repeat(64) });
  const refused = { schema: 1, choice: "opted-out", state: "suspended" };
  const journal = await openSelectionJournal(binding);
  let lifecycle = null;
  const checks = _FILTER_JOURNAL_PHASE === "store"
    ? { fresh: false, committed_refusal: false, wrapper_close: false, process_lease: false }
    : { persisted_refusal: false, reset_rejected: false, sticky_enrollment_refused: false,
      no_actor_or_grant: false, wrapper_close: false, process_lease: false };
  try {
    if (_FILTER_JOURNAL_PHASE === "store") {
      Assert.deepEqual(journal.record, { schema: 1, choice: "eligible", state: "fresh" });
      checks.fresh = true;
      await journal.save({ schema: 1, choice: "opted-out", state: "pending" });
      await journal.save(refused);
      Assert.deepEqual(journal.record, refused);
      checks.committed_refusal = true;
    } else {
      Assert.deepEqual(journal.record, refused);
      checks.persisted_refusal = true;
      await Assert.rejects(journal.save({ schema: 1, choice: "eligible", state: "pending" }),
        error => error.code === "journal_transition", "sticky refusal cannot become eligible");
      Assert.deepEqual(journal.record, refused);
      checks.reset_rejected = true;
      let commands = 0, grants = 0, saves = 0;
      lifecycle = new FilterSelectionLifecycle({ record: journal.record,
        save: async value => { saves++; await journal.save(value); },
        command: async () => { commands++; throw new Error("unexpected_actor"); },
        authorize: () => { grants++; return false; },
        assertCurrent: () => true, invalidate: () => {},
      });
      Assert.deepEqual(await lifecycle.enroll(), { ...refused, attempted: false, receipt: null });
      Assert.deepEqual(lifecycle.status, refused);
      checks.sticky_enrollment_refused = true;
      Assert.equal(commands, 0); Assert.equal(grants, 0); Assert.equal(saves, 0);
      checks.no_actor_or_grant = true;
    }
  } finally {
    try { if (lifecycle) lifecycle.close(); }
    finally { await journal.close(); }
  }
  // Wrapper close is NOT evidence of successful native asyncClose. Even after
  // it resolves, this module/process must refuse another open attempt.
  checks.wrapper_close = true;
  await Assert.rejects(openSelectionJournal(binding), error => error.code === "journal_busy",
    "a real process restart, not a second module instance, is required");
  checks.process_lease = true;
  Assert.ok(Object.values(checks).every(value => value === true));
  print("FILTER_JOURNAL_RESULT:" + JSON.stringify({ version: 1,
    kind: "parent-only-native-sqlite-journal", phase: _FILTER_JOURNAL_PHASE,
    synthetic_binding: true, original_ubo: false, native_close_proven: false,
    default_enrollment: false, checks }));
});
