// SPDX-License-Identifier: GPL-3.0-only
// Inert transport/state-machine evidence, NOT original-uBO or Firefox runtime.
import test from "node:test";
import assert from "node:assert/strict";
import { createSelectionCommand, FilterSelectionLifecycle, FilterSelectionError,
  selectionJournal, validateSelectionReceipt } from "../integration/filters/Selection.sys.mjs";
import { FilterAdmission } from "../integration/filters/Admission.sys.mjs";

const KEY = "https://owned.invalid/immutable/a.txt";
const CUSTOM = "https://custom.invalid/enabled.txt";
const DISABLED = "https://custom.invalid/disabled.txt";
const fresh = () => ({ schema: 1, choice: "eligible", state: "fresh" });
const copy = value => structuredClone(value);
const code = expected => error => error instanceof FilterSelectionError
  && error.code === expected && error.message === expected;
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function actorFixture(options = {}) {
  const state = copy(options.state ?? { selectedFilterLists: ["user-filters", CUSTOM, "easylist"],
    importedLists: [DISABLED, CUSTOM] });
  let events = 0;
  let lastKeys = [];
  let reads = 0;
  const trace = [];
  let current = true;
  const transport = {
    read: async () => {
      reads++;
      if (options.onRead) await options.onRead(state, reads);
      return copy(state);
    },
    send: async request => {
      trace.push(copy(request));
      if (options.onSend) await options.onSend(request, state, trace);
      if (request.what === "applyFilterListSelection") {
        if (request.toImport) {
          state.selectedFilterLists.push(KEY);
          state.importedLists.push(KEY);
          // Original uBO sorts imported lists; preserving order is not required.
          state.importedLists.sort();
        } else {
          state.selectedFilterLists = state.selectedFilterLists.filter(key => key !== KEY);
          state.importedLists = state.importedLists.filter(key => key !== KEY);
        }
      } else {
        events++;
        lastKeys = [...state.selectedFilterLists].reverse();
        if (options.onReload) options.onReload(state, events, lastKeys);
      }
    },
    waitStored: async desired => {
      trace.push({ waitStored: desired });
      if (options.onStored) await options.onStored(state);
    },
    generation: () => events,
    waitLoaded: async previous => {
      trace.push({ waitLoaded: previous });
      if (options.event) return options.event(previous, events, lastKeys);
      return { generation: events, listKeys: [...lastKeys] };
    },
  };
  const command = createSelectionCommand({ key: KEY, transport, assertCurrent: () => current });
  return { state, trace, command, transport, close: () => { current = false; } };
}
function ownerFixture(options = {}) {
  const actor = options.actor ?? actorFixture();
  const saves = [];
  const commands = [];
  let disk = copy(options.record ?? fresh());
  let authorized = true;
  let current = true;
  let invalidations = 0;
  const config = {
    record: disk,
    save: async value => {
      saves.push(copy(value));
      if (options.save) await options.save(value, saves.length);
      disk = copy(value);
    },
    command: async operation => {
      commands.push(operation);
      assert.ok(operation === "observe" || disk.state === "pending", "pending is durable before mutations");
      assert.ok(operation === "observe" || invalidations > 0, "admission was invalidated");
      return options.command ? options.command(operation, actor) : actor.command(operation);
    },
    invalidate: () => { invalidations++; options.invalidate?.(); },
    assertCurrent: () => current,
    authorize: () => authorized,
  };
  const machine = new FilterSelectionLifecycle(config);
  return { actor, machine, config, commands, saves, disk: () => copy(disk),
    invalidations: () => invalidations,
    revoke: () => { authorized = false; }, replace: () => { current = false; } };
}

test("arbitrary custom enabled/disabled selections survive delta add and remove", async () => {
  const f = actorFixture();
  const baseline = copy(f.state);
  const added = await f.command("add");
  assert.deepEqual(added, { selected: true, imported: true, preserved: true, freshReload: true, reloadEvents: 2 });
  assert.equal(f.state.selectedFilterLists.includes(DISABLED), false);
  assert.deepEqual(new Set(f.state.importedLists), new Set([...baseline.importedLists, KEY]));
  const removed = await f.command("remove");
  assert.equal(removed.selected, false);
  assert.deepEqual(new Set(f.state.selectedFilterLists), new Set(baseline.selectedFilterLists));
  assert.deepEqual(new Set(f.state.importedLists), new Set(baseline.importedLists));
  assert.deepEqual(f.trace.filter(value => value.what), [
    { what: "applyFilterListSelection", toImport: KEY }, { what: "reloadAllFilters" }, { what: "reloadAllFilters" },
    { what: "applyFilterListSelection", toRemove: [KEY] }, { what: "reloadAllFilters" }, { what: "reloadAllFilters" },
  ]);
  assert.ok(!JSON.stringify([added, removed]).includes("invalid"));
});

test("empty, customized and all-ten stock baselines have no hardcoded stock requirement", async () => {
  for (const selected of [[], ["user-filters"], ["easylist", "easyprivacy", "plowe-0", "ublock-badware",
    "ublock-filters", "ublock-privacy", "ublock-quick-fixes", "ublock-unbreak", "urlhaus-1", "user-filters"]]) {
    const f = actorFixture({ state: { selectedFilterLists: selected, importedLists: [] } });
    await f.command("add");
    await f.command("remove");
    assert.deepEqual(new Set(f.state.selectedFilterLists), new Set(selected));
  }
});

test("preexisting disabled or selected owned-key candidates cannot be silently adopted", async () => {
  for (const selected of [[], [KEY]]) {
    const f = actorFixture({ state: { selectedFilterLists: selected, importedLists: [KEY] } });
    await assert.rejects(f.command("add"), code("unowned_selection"));
    assert.equal(f.trace.length, 0);
  }
});

test("bounded canonical baseline validation runs before mutation", async () => {
  const malformed = [
    { selectedFilterLists: ["a", "a"], importedLists: [] },
    { selectedFilterLists: [], importedLists: ["HTTP://old.invalid/a"] },
    { selectedFilterLists: [], importedLists: [" https://old.invalid/a"] },
    { selectedFilterLists: [], importedLists: ["https://old.invalid/a\nprivate"] },
    { selectedFilterLists: Array.from({ length: 513 }, (_, i) => `asset-${i}`), importedLists: [] },
    { selectedFilterLists: ["x".repeat(2049)], importedLists: [] },
    { selectedFilterLists: Array.from({ length: 100 }, (_, i) => `${i}-${"x".repeat(1400)}`), importedLists: [] },
    { selectedFilterLists: [], importedLists: [], arbitraryStorage: true },
  ];
  for (const state of malformed) {
    const f = actorFixture({ state });
    await assert.rejects(f.command("add"), code("invalid_selection"));
    assert.equal(f.trace.length, 0);
  }
});

test("selection changes after settling or either reload fail without stale restore", async () => {
  for (const stage of ["stored", 1, 2]) {
    const alter = state => { state.selectedFilterLists = state.selectedFilterLists.filter(key => key !== CUSTOM); };
    const f = actorFixture({ onStored: stage === "stored" ? alter : undefined,
      onReload: (state, event) => { if (event === stage) alter(state); } });
    await assert.rejects(f.command("add"), code("selection_changed"));
    assert.equal(f.state.selectedFilterLists.includes(CUSTOM), false);
    assert.equal(f.trace.filter(value => value.what === "applyFilterListSelection").length, 1);
  }
});

test("native normalization of an unrelated disabled import is not accepted as preservation", async () => {
  const f = actorFixture({ onStored: state => { state.importedLists = state.importedLists.filter(key => key !== DISABLED); } });
  await assert.rejects(f.command("add"), code("selection_changed"));
  assert.equal(f.state.importedLists.includes(DISABLED), false, "no autonomous repair is attempted");
});

test("two fresh events required; repeated, missing, oversized or foreign reload receipts reject", async () => {
  for (const event of [
    (previous, _events, listKeys) => ({ generation: previous, listKeys }),
    (_previous, _events, listKeys) => ({ generation: 33, listKeys }),
    (_previous, events, listKeys) => ({ generation: events, listKeys, foreign: true }),
    (_previous, events, listKeys) => ({ generation: events, listKeys: [...listKeys, "unrelated"] }),
  ]) {
    const f = actorFixture({ event });
    await assert.rejects(f.command("add"), error => error instanceof FilterSelectionError);
  }
});

test("first reload may drain old own-key membership, second must show exact new set", async () => {
  const f = actorFixture({ event: (_previous, events, listKeys) => ({ generation: events,
    listKeys: events === 1 ? listKeys.filter(key => key !== KEY) : listKeys }) });
  assert.equal((await f.command("add")).freshReload, true);
  const stale = actorFixture({ event: (_previous, events, listKeys) => ({ generation: events,
    listKeys: listKeys.filter(key => key !== KEY) }) });
  await assert.rejects(stale.command("add"), code("reload_unproved"));
});

test("ACK alone and events before the second call cannot complete a transaction", async () => {
  const gate = deferred();
  const f = actorFixture({ onStored: () => gate.promise });
  let complete = false;
  const task = f.command("add").then(() => { complete = true; });
  await flush();
  assert.equal(complete, false);
  assert.equal(f.trace.filter(value => value.what === "reloadAllFilters").length, 0);
  gate.resolve(); await task;
  assert.equal(complete, true);
  assert.deepEqual(f.trace.filter(value => "waitLoaded" in value), [{ waitLoaded: 0 }, { waitLoaded: 1 }]);
});

test("actor context replacement while awaiting read prevents all writes", async () => {
  const gate = deferred();
  const f = actorFixture({ onRead: () => gate.promise });
  const task = f.command("add");
  await flush(); f.close(); gate.resolve();
  await assert.rejects(task, code("identity_lost"));
  assert.equal(f.trace.length, 0);
});

test("a newer load during final storage verification invalidates the old receipt", async () => {
  let extraEvent = 0;
  const f = actorFixture({ onRead: (_state, reads) => { if (reads === 4) extraEvent++; } });
  const generation = f.transport.generation;
  f.transport.generation = () => generation() + extraEvent;
  await assert.rejects(f.command("add"), code("reload_unproved"));
});

test("actor adapter serializes and keeps no baseline in returned receipt", async () => {
  const gate = deferred();
  const f = actorFixture({ onStored: () => gate.promise });
  const task = f.command("add");
  await flush();
  await assert.rejects(f.command("remove"), code("busy_or_closed"));
  gate.resolve();
  const result = await task;
  assert.deepEqual(Object.keys(result).sort(), ["freshReload", "imported", "preserved", "reloadEvents", "selected"]);
});

test("unknown errors export only a fixed code, never format hostile private values", async () => {
  const raw = { get message() { throw new Error("raw getter accessed"); }, toString() { throw new Error("formatted"); } };
  const f = actorFixture({ onRead: () => { throw raw; } });
  await assert.rejects(f.command("add"), code("selection_failed"));
});

test("journal and receipts are closed and typed", () => {
  for (const value of [{ ...fresh(), extra: "private" }, { ...fresh(), schema: true },
    { ...fresh(), choice: "opted-out", state: "active" }, { ...fresh(), state: "unknown" }]) {
    assert.throws(() => selectionJournal(value), code("invalid_journal"));
  }
  for (const value of [{ selected: 1, imported: true }, { selected: true, imported: true, url: KEY }]) {
    assert.throws(() => validateSelectionReceipt(value, "observe"), code("invalid_receipt"));
  }
  assert.throws(() => validateSelectionReceipt({ selected: true, imported: true,
    preserved: true, freshReload: true, reloadEvents: 1 }, "add"), code("invalid_receipt"));
});

test("pending persists before mutation, closed receipt commits active without private selections", async () => {
  const f = ownerFixture();
  const result = await f.machine.enroll();
  assert.equal(result.state, "active");
  assert.equal(result.attempted, true);
  assert.deepEqual(f.commands, ["observe", "add"]);
  assert.deepEqual(f.saves, [{ schema: 1, choice: "eligible", state: "pending" },
    { schema: 1, choice: "eligible", state: "active" }]);
  assert.equal(JSON.stringify(f.saves).includes("https:"), false);
  const again = await f.machine.enroll();
  assert.equal(again.attempted, false);
  assert.equal(again.receipt, null, "an active journal is not new load proof");
  assert.deepEqual(f.commands, ["observe", "add", "observe"]);
});

test("temporary expiry/revocation permits only subsequently reauthorized explicit enrollment", async () => {
  for (const reason of ["expired", "revoked"]) {
    const f = ownerFixture();
    await f.machine.enroll(); f.revoke();
    const result = await f.machine.suspend(reason);
    assert.equal(result.choice, "eligible");
    assert.equal(result.state, "suspended");
    assert.equal(result.receipt.selected, false);
    await assert.rejects(f.machine.enroll(), code("authority_lost"));
    const restarted = new FilterSelectionLifecycle({ ...f.config, record: f.disk(), authorize: () => true });
    assert.equal((await restarted.enroll()).state, "active");
    assert.equal(f.actor.state.selectedFilterLists.includes(DISABLED), false);
  }
});

test("explicit user opt-out persists across restart and changed authority generation", async () => {
  const f = ownerFixture();
  await f.machine.enroll();
  assert.equal((await f.machine.optOut()).choice, "opted-out");
  const writes = f.commands.length;
  const restarted = new FilterSelectionLifecycle({ ...f.config, record: f.disk(), authorize: () => true });
  assert.equal((await restarted.enroll()).attempted, false);
  assert.equal(f.commands.length, writes);
  assert.equal(restarted.status.choice, "opted-out");
});

test("user deselection or import removal is sticky, unlike owner suspension", async () => {
  for (const field of ["selectedFilterLists", "importedLists"]) {
    const f = ownerFixture(); await f.machine.enroll();
    f.actor.state[field] = f.actor.state[field].filter(key => key !== KEY);
    const result = await f.machine.enroll();
    assert.equal(result.choice, "opted-out");
    assert.equal(result.attempted, false);
    assert.equal(f.commands.filter(value => value === "add").length, 1);
    const restarted = new FilterSelectionLifecycle({ ...f.config, record: f.disk() });
    assert.equal((await restarted.enroll()).attempted, false);
    const absent = await restarted.optOut();
    assert.equal(absent.receipt.freshReload, true);
    assert.equal(absent.receipt.selected, false);
  }
});

test("never-owned existing imports are not adopted or removed by lifecycle", async () => {
  const f = ownerFixture({ actor: actorFixture({ state: { selectedFilterLists: [], importedLists: [KEY] } }) });
  await assert.rejects(f.machine.enroll(), code("unowned_selection"));
  await assert.rejects(f.machine.suspend("expired"), code("unowned_selection"));
  assert.equal(f.actor.trace.length, 0);
  assert.equal(f.saves.length, 0);
});

test("fresh opt-out is durable without mutation or invented absence proof", async () => {
  for (const state of [undefined, { selectedFilterLists: [KEY], importedLists: [KEY] }]) {
    const f = ownerFixture({ actor: actorFixture({ state }) });
    const result = await f.machine.optOut();
    assert.equal(result.choice, "opted-out");
    assert.equal(result.receipt, null);
    assert.deepEqual(f.commands, []);
    assert.equal(f.actor.trace.length, 0);
    assert.equal((await f.machine.enroll()).attempted, false);
    assert.equal((await f.machine.optOut()).state, "fresh");
    assert.equal(f.actor.trace.length, 0, "a repeated opt-out cannot acquire ownership");
    const restarted = new FilterSelectionLifecycle({ ...f.config, record: f.disk() });
    assert.equal((await restarted.optOut()).state, "fresh");
    assert.equal(f.actor.trace.length, 0);
    if (state) await assert.rejects(restarted.suspend("revoked"), code("unowned_selection"));
    else assert.equal((await restarted.suspend("expired")).attempted, false);
    assert.equal(f.actor.trace.length, 0);
  }
});

test("pending forbids actor retries; explicit opt-out may only persist sticky refusal", async () => {
  for (const choice of ["eligible", "opted-out"]) {
    const f = ownerFixture({ record: { schema: 1, choice, state: "pending" } });
    for (const invoke of [() => f.machine.enroll(), () => f.machine.observe(),
      () => f.machine.suspend("revoked"), () => f.machine.optOut()]) {
      await assert.rejects(invoke(), code("uncertain"));
    }
    assert.equal(f.commands.length, 0);
    assert.deepEqual(f.saves, [{ schema: 1, choice: "opted-out", state: "pending" }]);
    await assert.rejects(f.machine.optOut(), code("uncertain"));
    assert.equal(f.saves.length, 1, "no repeated sticky-write retry");
  }
});

test("lost mutation reply remains pending and never retries or repairs", async () => {
  const f = ownerFixture({ command: async (operation, actor) => {
    const result = await actor.command(operation);
    if (operation === "add") throw new Error("private failure");
    return result;
  } });
  await assert.rejects(f.machine.enroll(), code("selection_failed"));
  assert.equal(f.machine.status.state, "pending");
  const restarted = new FilterSelectionLifecycle({ ...f.config, record: f.disk() });
  await assert.rejects(restarted.enroll(), code("uncertain"));
  assert.equal(f.commands.filter(value => value === "add").length, 1);
});

test("failed initial/final journal writes cannot authorize success or automatic retry", async () => {
  for (const failedWrite of [1, 2]) {
    const f = ownerFixture({ save: (_value, count) => { if (count === failedWrite) throw new Error("disk"); } });
    await assert.rejects(f.machine.enroll(), code("selection_failed"));
    assert.equal(f.machine.status.state, "pending");
    await assert.rejects(f.machine.enroll(), code("uncertain"));
    assert.equal(f.commands.filter(value => value === "add").length, failedWrite === 1 ? 0 : 1);
  }
});

test("unrelated native migration leaves lifecycle pending and admission invalidated", async () => {
  const f = ownerFixture({ actor: actorFixture({ onReload: state => {
    state.importedLists = state.importedLists.filter(key => key !== DISABLED);
  } }) });
  await assert.rejects(f.machine.enroll(), code("selection_changed"));
  assert.equal(f.machine.status.state, "pending");
  assert.equal(f.invalidations(), 1);
  await assert.rejects(f.machine.suspend("expired"), code("uncertain"));
});

test("authority loss after pending persistence prevents add; after reply prevents active commit", async () => {
  for (const stage of ["pending", "reply"]) {
    let f;
    f = ownerFixture({ save: (_value, count) => { if (stage === "pending" && count === 1) f.revoke(); },
      command: async (operation, actor) => {
        const result = await actor.command(operation);
        if (stage === "reply" && operation === "add") f.revoke();
        return result;
      } });
    await assert.rejects(f.machine.enroll(), code("authority_lost"));
    assert.equal(f.machine.status.state, "pending");
    assert.equal(f.commands.filter(value => value === "add").length, stage === "pending" ? 0 : 1);
  }
});

test("closed, replaced and busy owners do not overlap outstanding work", async () => {
  for (const action of ["close", "replace"]) {
    const gate = deferred();
    const f = ownerFixture({ command: async (operation, actor) => {
      if (operation === "observe") await gate.promise;
      return actor.command(operation);
    } });
    const task = f.machine.enroll(); await flush();
    await assert.rejects(f.machine.observe(), code("busy_or_closed"));
    if (action === "close") f.machine.close(); else f.replace();
    gate.resolve();
    await assert.rejects(task, code(action === "close" ? "busy_or_closed" : "identity_lost"));
    assert.equal(f.actor.trace.length, 0);
  }
});

test("sticky opt-out survives uncertain removal without re-enrollment", async () => {
  const f = ownerFixture({ command: async (operation, actor) => {
    if (operation === "remove") throw new Error("lost IPC");
    return actor.command(operation);
  } });
  await f.machine.enroll();
  await assert.rejects(f.machine.optOut(), code("selection_failed"));
  assert.deepEqual(f.disk(), { schema: 1, choice: "opted-out", state: "pending" });
  const restarted = new FilterSelectionLifecycle({ ...f.config, record: f.disk() });
  await assert.rejects(restarted.enroll(), code("uncertain"));
});

test("removal intent closes admission before any actor await; opt-out is pending before send", async () => {
  for (const action of ["suspend", "optOut"]) {
    const gate = deferred();
    let hold = false;
    let ticketCurrent = false;
    const f = ownerFixture({ invalidate: () => { ticketCurrent = false; },
      command: async (operation, actor) => {
        if (hold) await gate.promise;
        return actor.command(operation);
      } });
    await f.machine.enroll();
    hold = true; ticketCurrent = true;
    const task = action === "suspend" ? f.machine.suspend("revoked") : f.machine.optOut();
    await flush();
    assert.equal(ticketCurrent, false);
    if (action === "optOut") assert.deepEqual(f.disk(), { schema: 1, choice: "opted-out", state: "pending" });
    gate.resolve(); await task;
  }
});

test("only successful reload receipt can reconcile real Admission after owner invalidation", async () => {
  const membership = Symbol();
  const timers = new Map();
  let next = 0;
  const clock = { bootMs: 100, wallMs: 10000 };
  let confirmed = null;
  const admission = new FilterAdmission({ membership, clock: () => ({ ...clock }),
    schedule: fn => { timers.set(++next, fn); return next; }, cancelTimer: id => timers.delete(id),
    reconcile: async () => {
      assert.equal(confirmed?.receipt?.freshReload, true);
      return confirmed.receipt.selected
        ? { mode: "active", generation: "a".repeat(64), expiresAtMs: 15000 } : { mode: "absent" };
    } });
  // The owner performs mutations behind a closed barrier, then supplies a
  // completed receipt to reconciliation; never invalidates its own flight.
  const f = ownerFixture({ invalidate: () => { confirmed = null; admission.invalidate(); } });
  confirmed = await f.machine.enroll();
  const ticket = await admission.admit(membership);
  assert.equal(admission.validate(ticket), true);
  const stale = await admission.admit(membership);
  confirmed = await f.machine.suspend("expired");
  assert.throws(() => admission.validate(stale));
  assert.equal(admission.validate(await admission.admit(membership)), true);
  admission.close(); f.machine.close();
  assert.equal(timers.size, 0);
});

test("revocation and opt-out invalidate real ready/pending Admission even while observe is busy", async () => {
  for (const action of ["suspend", "optOut"]) {
    const membership = Symbol();
    const clock = { bootMs: 100, wallMs: 10000 };
    const timers = new Map();
    let next = 0;
    let reconcileGate = null;
    const admission = new FilterAdmission({ membership, clock: () => ({ ...clock }),
      schedule: fn => { timers.set(++next, fn); return next; }, cancelTimer: id => timers.delete(id),
      reconcile: async () => {
        if (reconcileGate) await reconcileGate.promise;
        return { mode: "active", generation: "a".repeat(64), expiresAtMs: 15000 };
      } });
    const gate = deferred();
    let hold = false;
    const f = ownerFixture({ invalidate: () => admission.invalidate(), command: async (operation, actor) => {
      if (hold && operation === "observe") await gate.promise;
      return actor.command(operation);
    } });
    await f.machine.enroll();
    const ticket = await admission.admit(membership);
    hold = true;
    const observe = f.machine.observe();
    const observedFailure = assert.rejects(observe, code("intent_changed"));
    await flush();
    const intent = action === "suspend" ? f.machine.suspend("revoked") : f.machine.optOut();
    const intendedFailure = assert.rejects(intent, code("busy_or_closed"));
    assert.throws(() => admission.validate(ticket), "busy cannot delay synchronous revocation");
    await intendedFailure;
    if (action === "optOut") assert.deepEqual(f.disk(), { schema: 1, choice: "opted-out", state: "pending" });
    // Repeat while a native admission waiter is also unresolved. No ready or
    // pending path may survive merely because the actor's observation is stuck.
    reconcileGate = deferred();
    const pending = admission.admit(membership);
    const pendingFailure = assert.rejects(pending, error => error.code === "admission_invalidated");
    await flush();
    await assert.rejects(action === "suspend" ? f.machine.suspend("revoked") : f.machine.optOut(),
      error => ["busy_or_closed", "uncertain"].includes(error.code));
    await pendingFailure;
    gate.resolve(); reconcileGate.resolve(); await observedFailure;
    admission.close(); f.machine.close();
    assert.equal(timers.size, 0);
  }
});

test("busy opt-out queues one durable sticky write behind journal I/O, never actor work", async () => {
  const gate = deferred();
  const f = ownerFixture({ save: async (_record, count) => { if (count === 2) await gate.promise; } });
  const enroll = f.machine.enroll();
  const enrolledFailure = assert.rejects(enroll, code("intent_changed"));
  for (let i = 0; i < 100 && f.saves.length < 2; i++) await flush();
  assert.equal(f.saves.length, 2);
  const choices = [f.machine.optOut(), f.machine.optOut(), f.machine.optOut()];
  const failures = choices.map(task => assert.rejects(task, code("busy_or_closed")));
  await flush();
  assert.equal(f.saves.length, 2, "no concurrent writes or unbounded opt-out queue");
  gate.resolve();
  await Promise.all([enrolledFailure, ...failures]);
  assert.equal(f.saves.length, 3);
  assert.deepEqual(f.disk(), { schema: 1, choice: "opted-out", state: "pending" });
  const restarted = new FilterSelectionLifecycle({ ...f.config, record: f.disk() });
  await assert.rejects(restarted.enroll(), code("uncertain"));
});

test("opt-out between final durable commit and promise delivery discards the old success receipt", async () => {
  const f = ownerFixture();
  const enroll = f.machine.enroll();
  const failed = assert.rejects(enroll, code("intent_changed"));
  for (let i = 0; i < 1000 && f.machine.status.state !== "active"; i++) await Promise.resolve();
  assert.equal(f.machine.status.state, "active");
  await assert.rejects(f.machine.optOut(), code("busy_or_closed"));
  await failed;
  assert.deepEqual(f.disk(), { schema: 1, choice: "opted-out", state: "pending" });
});
