// SPDX-License-Identifier: GPL-3.0-only
// Actual Owner + Selection methods, inert actor/journal services. Native combined
// extension/profile evidence is a separate fixture, not implied by these tests.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { createHash } from "node:crypto";
import { validateExpected } from "../integration/filters/Contract.sys.mjs";
import { validateKey } from "../integration/filters/ActorContract.sys.mjs";
import { FilterSelectionLifecycle, selectionJournal } from "../integration/filters/Selection.sys.mjs";

const source = fs.readFileSync(new URL("../integration/filters/Owner.sys.mjs", import.meta.url), "utf8");
const EXPECTED = { publisher_key: "1".repeat(64), name: "public-supplement",
  manifest_id: "2".repeat(64), authority_expires_unix_seconds: 2000 };
const KEY = "https://filters.example.invalid/fixed-publication.txt";
const state = (choice, phase) => ({ schema: 1, choice, state: phase });
const fail = code => error => error.code === code && error.message === code;
const gate = () => { let resolve; return { promise: new Promise(yes => { resolve = yes; }), resolve }; };
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };

function fixture(initial = state("eligible", "fresh")) {
  const f = { events: [], current: true, grant: true, bootMs: 500, wallMs: 1_000_000,
    record: selectionJournal(initial), present: initial.state === "active", opens: 0,
    actorOpens: 0, commands: 0, grants: 0, journalClosed: false, actorClosed: false };
  const journal = {
    get record() { assert.equal(f.journalClosed, false); return f.record; },
    async save(value) {
      assert.equal(f.journalClosed, false);
      f.events.push("save-start:" + value.choice + ":" + value.state);
      if (f.saveGate) await f.saveGate.promise;
      assert.equal(f.journalClosed, false);
      if (f.saveError) throw f.saveError;
      f.record = selectionJournal(value);
      f.events.push("saved:" + value.choice + ":" + value.state);
    },
    async close() { f.journalClosed = true; f.events.push("journal-close"); if (f.closeError) throw f.closeError; },
  };
  const openSelectionJournal = async binding => {
    f.opens++; f.binding = binding; f.events.push("journal-open");
    if (f.openGate) await f.openGate.promise;
    if (f.openError) throw f.openError;
    return journal;
  };
  const openSelectionActor = async config => {
    f.actorOpens++; f.events.push("actor-open");
    if (f.actorOpenGate) await f.actorOpenGate.promise;
    assert.equal(config.assertCurrent(), true);
    assert.equal(config.key, KEY);
    assert.equal(config.authorize("observe"), true);
    return {
      async command(operation) {
        assert.equal(f.actorClosed, false);
        assert.equal(config.assertCurrent(), true);
        assert.equal(config.authorize(operation), true);
        f.commands++; f.events.push("command:" + operation);
        if (f.commandGate) await f.commandGate.promise;
        if (f.commandError) throw f.commandError;
        assert.equal(config.assertCurrent(), true);
        assert.equal(config.authorize(operation), true);
        if (operation !== "observe") {
          assert.equal(f.record.state, "pending", "durable intent precedes actor mutation/readiness");
          f.present = operation === "add";
        }
        return { selected: f.present, imported: f.present, ...(operation === "observe" ? {}
          : { preserved: true, freshReload: true, reloadEvents: 2 }) };
      },
      close() { f.actorClosed = true; f.events.push("actor-close"); },
    };
  };
  const environment = {
    validateExpected, validateKey, openSelectionActor, openSelectionJournal, FilterSelectionLifecycle,
    TextEncoder, Services: { appinfo: { processType: 0 } },
    Ci: { nsIXULRuntime: { PROCESS_TYPE_DEFAULT: 0 }, nsICryptoHash: {} },
    Cc: { "@mozilla.org/security/hash;1": { createInstance: () => {
      let hash; return { SHA256: 1, init: () => { hash = createHash("sha256"); },
        update: bytes => hash.update(bytes), finish: () => hash.digest("latin1") };
    } } },
  };
  const context = vm.createContext(environment);
  let code = source;
  for (const line of [
    'import { validateExpected } from "./Contract.sys.mjs";',
    'import { validateKey } from "./ActorContract.sys.mjs";',
    'import { openSelectionActor } from "./Actor.sys.mjs";',
    'import { openSelectionJournal } from "./Journal.sys.mjs";',
    'import { FilterSelectionLifecycle } from "./Selection.sys.mjs";',
  ]) { assert.equal(code.split(line).length, 2); code = code.replace(line, ""); }
  vm.runInContext(code.replace("export class ", "class ").replace("export async function ", "async function ")
    + "\nglobalThis.open = openSelectionOwner;", context);
  f.options = { expected: EXPECTED, key: KEY, assertCurrent: () => f.current,
    authorize: () => { f.grants++; return f.grant; }, invalidate: () => { f.events.push("invalidate"); },
    clock: () => ({ bootMs: f.bootMs, wallMs: f.wallMs }) };
  f.open = (options = f.options) => context.open(options);
  return f;
}

test("explicit owner lazily connects original actor with manifest-bound private journal", async () => {
  const f = fixture(); const owner = await f.open();
  assert.equal(f.actorOpens, 0); assert.equal(f.grants, 0);
  assert.equal(f.binding.publisherKey, EXPECTED.publisher_key); assert.equal(f.binding.name, EXPECTED.name);
  assert.equal(f.binding.keyDigest, createHash("sha256").update(JSON.stringify(
    ["volparossa-filter-selection-key-v1", EXPECTED.manifest_id, KEY])).digest("hex"));
  assert.equal((await owner.enroll()).state, "active");
  assert.equal(f.actorOpens, 1);
  assert.ok(f.events.indexOf("saved:eligible:pending") < f.events.indexOf("command:add"));
  assert.ok(f.events.indexOf("command:add") < f.events.indexOf("saved:eligible:active"));
  await owner.optOut(); assert.equal(f.record.choice, "opted-out"); assert.equal(f.present, false);
  await owner.close(); assert.equal(owner.status.closed, true); assert.equal(f.journalClosed, true);
});

test("persisted refusal prevents enrollment without opening an actor or requesting a grant", async () => {
  for (const phase of ["fresh", "suspended"]) {
    const f = fixture(state("opted-out", phase)); const owner = await f.open();
    assert.equal((await owner.enroll()).attempted, false);
    assert.equal(f.actorOpens, 0); assert.equal(f.grants, 0);
    assert.equal(f.events.some(value => value.startsWith("save-")), false);
    await owner.close();
  }
});

test("explicit observation still verifies extension state even for an opted-out owner", async () => {
  const f = fixture(state("opted-out", "suspended")); const owner = await f.open();
  assert.equal((await owner.observe()).attempted, false);
  assert.equal(f.actorOpens, 1); assert.deepEqual(f.events.filter(v => v.startsWith("command:")), ["command:observe"]);
  assert.equal(f.grants, 0); await owner.close();
});

test("preexisting user key is not adopted or removed by a fresh owner", async () => {
  const f = fixture(); f.present = true; const owner = await f.open();
  await assert.rejects(owner.enroll(), fail("owner_failed"));
  assert.equal(f.present, true); assert.equal(f.record.state, "fresh");
  await owner.optOut(); assert.equal(f.record.choice, "opted-out"); assert.equal(f.present, true);
  assert.equal(f.events.includes("command:remove"), false); await owner.close();
});

test("suspend removes an owned list after expired add authority without permanently opting out", async () => {
  const f = fixture(state("eligible", "active")); const owner = await f.open();
  f.wallMs = 2_000_000; f.grant = false;
  assert.equal((await owner.suspend("expired")).state, "suspended");
  assert.equal(f.record.choice, "eligible"); assert.equal(f.present, false); assert.equal(f.grants, 0);
  await assert.rejects(owner.enroll(), fail("owner_failed")); await owner.close();
});

test("wall expiry, suspend-inclusive expiry and clock regression cannot grant add", async () => {
  for (const change of [f => { f.wallMs = 2_000_000; }, f => { f.bootMs = 1_000_500; }, f => { f.wallMs--; }]) {
    const f = fixture(); const owner = await f.open(); change(f);
    await assert.rejects(owner.enroll(), fail("owner_failed"));
    assert.equal(f.actorOpens, 0); assert.equal(f.events.includes("command:add"), false); await owner.close();
  }
});

test("pending save must resolve before add, and failed save sends no mutation", async () => {
  const f = fixture(); const owner = await f.open(); f.saveGate = gate();
  const enrolling = owner.enroll(); await flush();
  assert.equal(f.events.includes("command:add"), false);
  await assert.rejects(owner.observe(), fail("owner_busy"));
  f.saveGate.resolve(); await enrolling; await owner.close();
  const bad = fixture(); const other = await bad.open(); bad.saveError = Error("private detail");
  await assert.rejects(other.enroll(), fail("owner_failed"));
  assert.equal(bad.events.includes("command:add"), false); await other.close();
});

test("sticky opt-out interrupts an ordinary pending command and close joins its write", async () => {
  const f = fixture(state("eligible", "active")); const owner = await f.open(); f.commandGate = gate();
  const observing = owner.observe(); const rejected = assert.rejects(observing, fail("owner_failed"));
  await flush(); f.saveGate = gate();
  const refused = owner.optOut(); const refusal = assert.rejects(refused, fail("owner_failed"));
  await flush(); let done = false;
  const closing = owner.close().then(() => { done = true; }); await flush();
  assert.equal(done, false); assert.equal(f.journalClosed, false);
  f.saveGate.resolve(); await refusal;
  assert.equal(f.record.choice, "opted-out"); assert.equal(f.record.state, "pending");
  f.commandGate.resolve(); await rejected; await closing;
  assert.equal(f.journalClosed, true);
  assert.ok(f.events.indexOf("saved:opted-out:pending") < f.events.indexOf("journal-close"));
});

test("opt-out is not lost behind an already running suspension", async () => {
  const f = fixture(state("eligible", "active")); const owner = await f.open();
  // Let suspend's observation finish, then hold its pending journal write.
  f.saveGate = gate();
  const suspending = owner.suspend("revoked"); const rejected = assert.rejects(suspending, fail("owner_failed"));
  await flush(); assert.ok(f.events.includes("save-start:eligible:pending"));
  const optingOut = owner.optOut(); const refused = assert.rejects(optingOut, fail("owner_failed"));
  await flush(); f.saveGate.resolve(); await refused; await rejected;
  assert.equal(f.record.choice, "opted-out"); assert.equal(f.record.state, "pending");
  assert.equal(f.events.includes("command:remove"), false); await owner.close();
});

test("close during lazy actor opening joins it before closing the journal", async () => {
  const f = fixture(); const owner = await f.open(); f.actorOpenGate = gate();
  const opening = owner.observe(); const rejected = assert.rejects(opening, fail("owner_failed"));
  await flush(); const closing = owner.close(); await flush(); assert.equal(f.journalClosed, false);
  f.actorOpenGate.resolve(); await rejected; await closing;
  assert.equal(f.commands, 0); assert.equal(f.journalClosed, true);
});

test("unknown results poison further work and expose only closed errors", async () => {
  const f = fixture(); const owner = await f.open();
  f.commandError = { get message() { throw Error("must never inspect private message"); } };
  await assert.rejects(owner.enroll(), fail("owner_failed"));
  const commands = f.commands;
  await assert.rejects(owner.enroll(), fail("owner_failed")); assert.equal(f.commands, commands);
  assert.equal(owner.status.failed, true); await owner.close();
});

test("configuration, context loss and cleanup failures stay closed", async () => {
  const f = fixture();
  for (const invalid of [null, {}, { ...f.options, path: "/outside" }, { ...f.options, key: "file:///private" },
    { ...f.options, expected: { ...EXPECTED, authority_expires_unix_seconds: Number.MAX_SAFE_INTEGER } }]) {
    await assert.rejects(f.open(invalid), fail("owner_config"));
  }
  assert.equal(f.opens, 0);
  f.current = false; await assert.rejects(f.open(), fail("owner_context")); assert.equal(f.opens, 0);
  const bad = fixture(); const owner = await bad.open(); bad.closeError = Error("private-close");
  await assert.rejects(owner.close(), fail("owner_cleanup"));
  await assert.rejects(owner.close(), fail("owner_cleanup"));
  await assert.rejects(owner.enroll(), fail("owner_closed"));
});
