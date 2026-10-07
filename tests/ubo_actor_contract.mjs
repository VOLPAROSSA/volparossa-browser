// SPDX-License-Identifier: GPL-3.0-only
// Inert tests run the real contract and actor methods, not Firefox or an add-on.
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import {
  ACTOR, ID, VERSION, URL, STOCKS, changeSelection, enrollOnce, journal,
  PHASES, REASONS, ProofError, ProofFailure, failureEnvelope, loadedSelection, selection,
  step, successEnvelope, validateCommand, validateContext, validateReply,
} from "../integration/ubo-proof/Contract.sys.mjs";

const binding = () => ({ owner: `{${randomUUID()}}`, browserID: 31, innerID: 42,
  documentURL: "moz-extension://1234/3p-filters.html" });
const context = () => ({ id: ID, version: VERSION, principalID: ID, topLevel: true,
  active: true, extensionContext: true, browserID: 31, innerID: 42,
  documentURL: "moz-extension://1234/3p-filters.html", policyURL: "moz-extension://1234/3p-filters.html" });
const stored = present => ({ selectedFilterLists: [...STOCKS, ...(present ? [URL] : [])],
  importedLists: present ? [URL] : [] });

test("context binds exact extension, principal, document and owned top context", () => {
  const owner = binding();
  validateContext(context(), owner);
  for (const [key, replacement] of [
    ["id", "other"], ["version", "1.75.1"], ["principalID", "other"],
    ["documentURL", owner.documentURL + "?query"], ["policyURL", "https://example.org/"],
    ["topLevel", false], ["active", false], ["extensionContext", false],
    ["browserID", 32], ["innerID", 43],
  ]) assert.throws(() => validateContext({ ...context(), [key]: replacement }, owner));
});

test("commands are closed enums without scripts, URLs or caller payloads", () => {
  const owner = binding();
  for (const operation of ["observe", "enroll", "revoke"]) {
    assert.equal(validateCommand({ ...owner, operation }, owner), operation);
  }
  for (const value of [null, [], {}, { ...owner, operation: "eval" },
    { ...owner, operation: "enroll", script: "evil" },
    { ...owner, operation: "enroll", url: "https://example.org" },
    { ...owner, operation: "enroll", owner: binding().owner },
    { ...owner, operation: "enroll", browserID: 32 },
    { ...owner, operation: "enroll", innerID: 43 },
  ]) assert.throws(() => validateCommand(value, owner));
});

test("only the exact ten stock selections plus the single probe are accepted", () => {
  assert.deepEqual(selection(stored(false)), { selected: false, imported: false, stocks: 10 });
  assert.deepEqual(selection(stored(true)), { selected: true, imported: true, stocks: 10 });
  for (const value of [null, {}, { ...stored(false), importedLists: ["https://private.invalid"] },
    { ...stored(false), selectedFilterLists: STOCKS.slice(1) },
    { ...stored(false), selectedFilterLists: [...STOCKS, STOCKS[0]] },
    { ...stored(false), selectedFilterLists: [...STOCKS, "https://private.invalid"] },
  ]) assert.throws(() => selection(value));
  assert.equal(loadedSelection([...STOCKS, URL], true), true);
  assert.equal(loadedSelection(STOCKS, false), true);
  assert.equal(loadedSelection(STOCKS, true), false);
});

test("journal rejects unknown schema/state/extra fields", () => {
  for (const state of ["fresh", "pending", "enrolled", "opted-out"]) journal({ schema: 1, state });
  for (const value of [null, {}, { schema: 2, state: "fresh" }, { schema: 1, state: "retry" },
    { schema: 1, state: "fresh", reset: true }]) assert.throws(() => journal(value));
});

test("pending must be saved before any mutation, completion needs fresh reload", async () => {
  const calls = [];
  await enrollOnce({ schema: 1, state: "fresh" }, async value => calls.push(value.state), async operation => {
    calls.push(operation);
    return operation === "observe" ? selection(stored(false)) : {
      ...selection(stored(true)), freshReload: true, reloadEvents: 2,
    };
  });
  assert.deepEqual(calls, ["observe", "pending", "enroll", "enrolled"]);
  for (const result of [selection(stored(true)), { ...selection(stored(true)), freshReload: true, reloadEvents: 1 }]) {
    const states = [];
    await assert.rejects(enrollOnce({ schema: 1, state: "fresh" }, async v => states.push(v.state),
      async op => op === "observe" ? selection(stored(false)) : result));
    assert.deepEqual(states, ["pending"]);
  }
});

test("unknown outcome and owner opt-out survive retry/restart/reinstall", async () => {
  let durable = { schema: 1, state: "fresh" };
  await assert.rejects(enrollOnce(durable, async v => { durable = structuredClone(v); }, async op => {
    if (op === "observe") return selection(stored(false));
    throw new Error("simulated lost reply after mutation");
  }));
  assert.equal(durable.state, "pending");
  for (const state of [durable.state, "opted-out", "enrolled"]) {
    // New JS objects model reopened journal; add-on storage reset doesn't grant authority.
    const actual = await enrollOnce(JSON.parse(JSON.stringify({ schema: 1, state })),
      () => assert.fail("unexpected journal write"), () => assert.fail("unexpected command"));
    assert.deepEqual(actual, { state, attempted: false });
  }
});

test("failed pending save cannot send enrollment", async () => {
  const commands = [];
  await assert.rejects(enrollOnce({ schema: 1, state: "fresh" }, async () => { throw new Error("disk"); },
    async op => { commands.push(op); return selection(stored(false)); }));
  assert.deepEqual(commands, ["observe"]);
});

function transport({ oldLoad = false, emit = true, persist = true, initial = false } = {}) {
  let value = stored(initial), generation = 0, loaded = null;
  const calls = [];
  return { calls, read: async () => value, generation: () => generation,
    send: async request => {
      calls.push(request);
      if (request.what === "applyFilterListSelection") {
        if (persist) value = stored(Object.hasOwn(request, "toImport"));
      } else if (emit) {
        generation += 1;
        loaded = oldLoad && generation === 1 ? STOCKS : value.selectedFilterLists;
      }
    },
    waitStored: async desired => {
      assert.equal(selection(value).selected, desired, "ACK is not persisted selection");
    },
    waitLoaded: async (previous, desired) => {
      assert.ok(generation > previous, "ACK is not a reload event");
      if (desired !== null) assert.ok(loadedSelection(loaded, desired));
    },
  };
}

test("an older coalesced reload cannot be final evidence; second reload is required", async () => {
  const io = transport({ oldLoad: true });
  const result = await changeSelection("enroll", io);
  assert.equal(result.freshReload, true);
  assert.equal(result.reloadEvents, 2);
  assert.deepEqual(io.calls, [{ what: "applyFilterListSelection", toImport: URL },
    { what: "reloadAllFilters" }, { what: "reloadAllFilters" }]);
});

test("no persistence or no actual reload broadcast means no success", async () => {
  await assert.rejects(changeSelection("enroll", transport({ persist: false })));
  await assert.rejects(changeSelection("enroll", transport({ emit: false })));
});

test("revocation removes only the supplementary import; all stocks remain", async () => {
  const io = transport({ initial: true });
  assert.deepEqual(await changeSelection("revoke", io), {
    selected: false, imported: false, stocks: 10, freshReload: true, reloadEvents: 2,
  });
  assert.deepEqual(io.calls[0], { what: "applyFilterListSelection", toRemove: [URL] });
});

globalThis.JSWindowActorParent = class {};
globalThis.Services = { uuid: { generateUUID: () => `{${randomUUID()}}` } };
const { bindOwnedActor, VolparossaUboProofParent } = await import(
  "../integration/ubo-proof/VolparossaUboProofParent.sys.mjs");

test("actual parent actor refuses unowned context, navigation and unsolicited child messages", async () => {
  const actor = new VolparossaUboProofParent();
  const browser = { browsingContext: { id: 31 } };
  actor.browsingContext = browser.browsingContext;
  actor.manager = { innerWindowId: 42, documentPrincipal: { addonId: ID },
    documentURI: { spec: binding().documentURL } };
  browser.browsingContext.currentWindowGlobal = actor.manager;
  let count = 0;
  actor.sendQuery = async name => { assert.equal(name, ACTOR + ":fixed"); count++; return successEnvelope(selection(stored(false))); };
  await assert.rejects(actor.fixedCommand({ ...binding(), operation: "enroll" }));
  assert.throws(() => bindOwnedActor(actor, { browsingContext: {} }, binding().documentURL));
  const command = bindOwnedActor(actor, browser, binding().documentURL);
  assert.deepEqual(await command("observe"), selection(stored(false)));
  assert.throws(() => actor.receiveMessage({ name: "anything" }));
  actor.manager.documentURI.spec += "?query";
  await assert.rejects(command("enroll"));
  actor.manager.documentURI.spec = binding().documentURL;
  browser.browsingContext.currentWindowGlobal = {};
  await assert.rejects(command("enroll"));
  assert.equal(count, 1);
});

globalThis.JSWindowActorChild = class {};
const contexts = new Map([[42, { active: true, viewType: "tab", extension: {
  id: ID, manifest: { version: VERSION },
} }]]);
globalThis.ChromeUtils = { importESModule: () => ({ ExtensionPageChild: { extensionContexts: contexts } }) };
globalThis.WebExtensionPolicy = { getByID: id => id === ID ? {
  active: true, getURL: () => binding().documentURL,
} : null };
globalThis.Cu = { waiveXrays: value => value, cloneInto: value => structuredClone(value) };
const { VolparossaUboProofChild } = await import("../integration/ubo-proof/VolparossaUboProofChild.sys.mjs");

test("actual child checks context before invoking any original extension function", async () => {
  const actor = new VolparossaUboProofChild(), owner = binding();
  actor.manager = { innerWindowId: 42 };
  actor.browsingContext = { id: 31, parent: null };
  actor.document = { nodePrincipal: { addonId: ID }, documentURI: owner.documentURL };
  const sent = [];
  actor.contentWindow = { setTimeout, vAPI: { messaging: { send: async (channel, request) => {
    sent.push([channel, request]);
  } } }, browser: { storage: { local: { get: async () => stored(false) } } } };
  const query = { name: ACTOR + ":fixed", data: { ...owner, operation: "observe" } };
  assert.deepEqual(validateReply(await actor.receiveMessage(query), "observe"), selection(stored(false)));
  assert.deepEqual(sent, [["dashboard", { what: "getLists" }]]);
  const refused = async request => {
    const reply = await actor.receiveMessage(request);
    assert.equal(reply.ok, false);
    assert.throws(() => validateReply(reply, "observe"), ProofFailure);
  };
  actor.document.nodePrincipal.addonId = "attacker";
  await refused(query);
  actor.document.nodePrincipal.addonId = ID;
  actor.browsingContext.parent = {};
  await refused(query);
  actor.browsingContext.parent = null;
  contexts.get(42).viewType = "background";
  await refused(query);
  contexts.get(42).viewType = "tab";
  await refused({ ...query, data: { ...query.data, owner: binding().owner } });
  actor.didDestroy();
  await refused(query);
  assert.equal(sent.length, 1);
});

test("diagnostics use only owned fixed fields, never unknown exception properties", async () => {
  const privateError = new Error("private detail");
  for (const key of ["message", "name", "stack", "phase", "reason"]) {
    Object.defineProperty(privateError, key, { get() { assert.fail("read unknown exception property"); } });
  }
  for (const error of [privateError, "private text", { phase: "mutate", reason: "ubo_proof_deadline" }, null]) {
    assert.deepEqual(failureEnvelope(error, "observe"), {
      schema: 1, ok: false, phase: "observe", reason: "ubo_proof_other",
    });
  }
  assert.deepEqual(failureEnvelope(privateError, "private phase"), {
    schema: 1, ok: false, phase: "reply_validate", reason: "ubo_proof_other",
  });
  assert.equal(failureEnvelope(new ProofError("private detail")).reason, "ubo_proof_other");
  for (const phase of PHASES) {
    await assert.rejects(step(phase, () => { throw privateError; }), error =>
      error instanceof ProofFailure && error.phase === phase && error.reason === "ubo_proof_other");
  }
  for (const reason of REASONS) {
    await assert.rejects(step("mutate", () => { throw new ProofFailure("reload_fresh", reason); }), error =>
      error.phase === "reload_fresh" && error.reason === reason);
  }
});

test("closed reply validation refuses missing, unknown and enlarged envelopes", () => {
  const good = successEnvelope(selection(stored(false)));
  const failed = { schema: 1, ok: false, phase: "child_storage_read", reason: "ubo_proof_other" };
  assert.deepEqual(validateReply(good, "observe"), selection(stored(false)));
  assert.throws(() => validateReply(failed, "observe"), error =>
    error instanceof ProofFailure && error.phase === failed.phase && error.reason === failed.reason);
  for (const reply of [null, undefined, [], "observe", {}, { ok: false }, { ...good, raw: "private" },
    { ...good, schema: true }, { ...good, value: { ...good.value, raw: "private" } },
    { ...good, value: { ...good.value, stocks: 9 } }, { ...good, value: { ...good.value, selected: 1 } },
    { ...failed, reason: "ubo_proof_arbitrary" }, { ...failed, phase: "private" },
    { ...failed, message: "private" }, { ...failed, value: good.value }, { ...failed, ok: 0 }]) {
    assert.throws(() => validateReply(reply, "observe"), error =>
      error.phase === "reply_validate" && error.reason === "ubo_proof_invalid_reply");
  }
  for (const operation of ["enroll", "revoke"]) {
    const value = { ...selection(stored(operation === "enroll")), freshReload: true, reloadEvents: 2 };
    assert.deepEqual(validateReply(successEnvelope(value), operation), value);
    for (const replacement of [{ reloadEvents: 1 }, { reloadEvents: 33 }, { reloadEvents: true },
      { freshReload: false }, { selected: !value.selected }, { imported: !value.imported }]) {
      assert.throws(() => validateReply(successEnvelope({ ...value, ...replacement }), operation), ProofFailure);
    }
  }
});

test("actual parent throws failed observation before any journal write or mutation", async () => {
  const actor = new VolparossaUboProofParent();
  const browser = { browsingContext: { id: 31 } };
  actor.browsingContext = browser.browsingContext;
  actor.manager = { innerWindowId: 42, documentPrincipal: { addonId: ID },
    documentURI: { spec: binding().documentURL } };
  browser.browsingContext.currentWindowGlobal = actor.manager;
  const command = bindOwnedActor(actor, browser, binding().documentURL);
  for (const reply of [{ schema: 1, ok: false, phase: "child_dashboard_ready", reason: "ubo_proof_other" },
    {}, { schema: 1, ok: false, phase: "unknown", reason: "ubo_proof_other" }]) {
    let calls = 0;
    actor.sendQuery = async (_, data) => { assert.equal(data.operation, "observe"); calls++; return reply; };
    await assert.rejects(enrollOnce({ schema: 1, state: "fresh" },
      () => assert.fail("failed observation authorized journal write"), command), ProofFailure);
    assert.equal(calls, 1);
  }
});

test("journal failures retain their stage and unknown mutation leaves pending", async () => {
  for (const state of ["pending", "enrolled"]) {
    const writes = [], commands = [];
    await assert.rejects(enrollOnce({ schema: 1, state: "fresh" }, async record => {
      writes.push(record.state);
      if (record.state === state) throw new Error("private path");
    }, async operation => {
      commands.push(operation);
      return operation === "observe" ? selection(stored(false)) : {
        ...selection(stored(true)), freshReload: true, reloadEvents: 2,
      };
    }), error => error.phase === (state === "pending" ? "pending_journal" : "completed_journal")
      && error.reason === "ubo_proof_other");
    assert.deepEqual(writes, state === "pending" ? ["pending"] : ["pending", "enrolled"]);
    assert.deepEqual(commands, state === "pending" ? ["observe"] : ["observe", "enroll"]);
  }
});

test("reload diagnostics retain the failed stage without retrying or skipping any call", async () => {
  for (const phase of ["mutate", "wait_stored", "reload_drain", "reload_drain_event",
    "reload_fresh", "reload_fresh_event", "verify_final"]) {
    const io = transport();
    let reads = 0, sends = 0, waits = 0;
    const read = io.read, send = io.send, waitStored = io.waitStored, waitLoaded = io.waitLoaded;
    const fail = () => { throw new Error("private detail"); };
    io.read = async () => { if (++reads === 2 && phase === "verify_final") fail(); return read(); };
    io.send = async request => {
      const current = ["mutate", "reload_drain", "reload_fresh"][sends++];
      if (phase === current) fail();
      return send(request);
    };
    io.waitStored = async desired => { if (phase === "wait_stored") fail(); return waitStored(desired); };
    io.waitLoaded = async (...args) => {
      const current = ["reload_drain_event", "reload_fresh_event"][waits++];
      if (phase === current) fail();
      return waitLoaded(...args);
    };
    await assert.rejects(changeSelection("enroll", io), error =>
      error.phase === phase && error.reason === "ubo_proof_other");
    assert.ok(sends <= 3);
    assert.ok(waits <= 2);
  }
});

test("actual child sanitizes foreign exception and busy rejection cannot release another request", async () => {
  const actor = new VolparossaUboProofChild(), owner = binding();
  actor.manager = { innerWindowId: 42 };
  actor.browsingContext = { id: 31, parent: null };
  actor.document = { nodePrincipal: { addonId: ID }, documentURI: owner.documentURL };
  let release, started;
  const ready = new Promise(resolve => { started = resolve; });
  actor.contentWindow = { setTimeout, vAPI: { messaging: { send: () => {
    started(); return new Promise((_, reject) => { release = reject; });
  } } }, browser: { storage: { local: { get: async () => stored(false) } } } };
  const query = { name: ACTOR + ":fixed", data: { ...owner, operation: "observe" } };
  const pending = actor.receiveMessage(query);
  await ready;
  const busy = await actor.receiveMessage(query);
  assert.equal(busy.ok, false);
  assert.equal(busy.phase, "child_validate");
  assert.equal(actor.busy, true);
  release(new Error("private extension detail"));
  assert.deepEqual(await pending, {
    schema: 1, ok: false, phase: "child_dashboard_ready", reason: "ubo_proof_other",
  });
  assert.equal(actor.busy, false);
});
