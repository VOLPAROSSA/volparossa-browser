// SPDX-License-Identifier: GPL-3.0-only
// Inert tests of actual actor/controller methods with platform capability spies.
// No Firefox, addon execution, network, profile writes or signature-proof claim.
import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { ACTOR, ID, VERSION, DOCUMENT, XPI_BYTES, XPI_SHA256, DEADLINE_MS,
  FilterActorError, validateKey, validateBinding, validateCommand, validateReply,
  closedError, failed, reply, baseline, sameBaseline, makeDeadline } from "../integration/filters/ActorContract.sys.mjs";

const KEY = "https://owned.invalid/immutable/a.txt";
const CUSTOM = "https://custom.invalid/on.txt";
const OFF = "https://custom.invalid/off.txt";
const DOCUMENT_URL = "moz-extension://12345678-1234-1234-1234-123456789abc/about.html";
const copied = value => structuredClone(value);
const stored = () => ({ selectedFilterLists: ["user-filters", CUSTOM], importedLists: [OFF, CUSTOM] });
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
const code = wanted => error => error instanceof FilterActorError && error.code === wanted && error.message === wanted;
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
let env;
const contexts = new Map();
const timers = new Map();
let timerID = 0;
let moduleID = 0;
const Timer = { setTimeout(fn, ms) { const id = ++timerID; timers.set(id, { fn, ms }); return id; },
  clearTimeout(id) { timers.delete(id); } };
const AddonManager = { SIGNEDSTATE_SIGNED: 2, async getAddonByID(id) {
  assert.equal(id, ID); env.lookups++;
  if (env.lookupGate) await env.lookupGate.promise;
  return env.addon;
} };
const ExtensionParent = { GlobalManager: { getExtension: () => env.extension },
  apiManager: { emit(name, browser) { assert.equal(name, "extension-browser-inserted"); assert.equal(browser, env.browser); env.inserted++; } },
  HiddenExtensionPage: class {
    constructor(extension, viewType) {
      assert.equal(extension, env.extension); assert.equal(viewType, "tab");
      this.unloaded = false; env.pages.push(this);
    }
    async createBrowserElement() {
      if (env.createGate) await env.createGate.promise;
      if (this.unloaded) { env.lateBrowserReleased++; throw new Error("late hidden page"); }
      return env.browser;
    }
    shutdown() {
      assert.equal(this.unloaded, false); this.unloaded = true; env.shutdowns++;
      env.child.didDestroy(); env.parent.didDestroy();
    }
  } };
globalThis.JSWindowActorParent = class {};
globalThis.JSWindowActorChild = class {};
globalThis.Services = {
  uuid: { generateUUID: () => `{${randomUUID()}}` },
  telemetry: { msSinceProcessStartIncludingSuspend: () => env.bootMs },
  appinfo: { processType: 0 }, prefs: { getBoolPref: name => { assert.equal(name, "xpinstall.signatures.required"); return env.signaturesRequired; } },
};
globalThis.Ci = { nsIXULRuntime: { PROCESS_TYPE_DEFAULT: 0 }, nsIJARURI: {}, nsIFileURL: {}, nsICryptoHash: {} };
globalThis.Cc = { "@mozilla.org/security/hash;1": { createInstance() {
  return { SHA256: 1, init(n) { assert.equal(n, 1); }, update(bytes, size) {
    assert.equal(size, XPI_BYTES); assert.equal(bytes.length, XPI_BYTES); env.hashes++;
  }, finish() { return Buffer.from(env.digest, "hex").toString("latin1"); } };
} } };
globalThis.IOUtils = { async read(path, options) {
  assert.equal(path, "/private/signed-original.xpi"); assert.deepEqual(options, { maxBytes: XPI_BYTES + 1 });
  env.packageReads++; if (env.packageGate) await env.packageGate.promise;
  return { length: env.packageBytes }; // platform spy, not an actual XPI digest
} };
globalThis.ChromeUtils = {
  importESModule(uri) {
    const modules = { "resource://gre/modules/ExtensionPageChild.sys.mjs": { ExtensionPageChild: { extensionContexts: contexts } },
      "resource://gre/modules/Timer.sys.mjs": Timer, "resource://gre/modules/AddonManager.sys.mjs": { AddonManager },
      "resource://gre/modules/ExtensionParent.sys.mjs": { ExtensionParent } };
    assert.ok(Object.hasOwn(modules, uri)); return modules[uri];
  },
  registerWindowActor(name, spec) { assert.equal(name, ACTOR); env.registrations.push(copied(spec)); },
  unregisterWindowActor(name) { assert.equal(name, ACTOR); env.unregisters++; if (env.unregisterError) throw new Error("private native detail"); },
};
globalThis.WebExtensionPolicy = { getByID: id => id === ID ? env.policy : null };
globalThis.Cu = { waiveXrays: value => value, cloneInto: value => copied(value) };
const { VolparossaFilterSelectionChild } = await import("../integration/filters/VolparossaFilterSelectionChild.sys.mjs");
const { VolparossaFilterSelectionParent, bindSelectionActor } = await import("../integration/filters/VolparossaFilterSelectionParent.sys.mjs");

function fixture(options = {}) {
  contexts.clear(); timers.clear();
  env = { bootMs: 1000, state: stored(), sends: [], reads: 0, channels: new Set(), pages: [],
    packageBytes: XPI_BYTES, digest: XPI_SHA256, signaturesRequired: true, hashes: 0,
    packageReads: 0, lookups: 0, inserted: 0, shutdowns: 0, unregisters: 0, registrations: [], lateBrowserReleased: 0,
    ...options };
  const f = env;
  f.policy = { active: true, getURL: path => { assert.equal(path, DOCUMENT); return DOCUMENT_URL; } };
  f.extension = { manifest: { version: VERSION }, policy: f.policy, principal: {} };
  f.addon = { id: ID, version: VERSION, isActive: true, userDisabled: false, appDisabled: false, signedState: 2,
    getResourceURI: () => ({ QueryInterface: () => ({ JARFile: { QueryInterface: () => ({ file: {
      path: "/private/signed-original.xpi", isSymlink: () => Boolean(f.symlink), isFile: () => true, fileSize: XPI_BYTES,
    } }) } }) }) };
  f.child = new VolparossaFilterSelectionChild();
  f.parent = new VolparossaFilterSelectionParent();
  const manager = { innerWindowId: 42, documentPrincipal: { addonId: ID }, documentURI: { spec: DOCUMENT_URL },
    getActor: name => { assert.equal(name, ACTOR); return f.parent; } };
  f.browser = { browsingContext: { id: 31, parent: null, currentWindowGlobal: manager },
    webProgress: { isLoadingDocument: false }, fixupAndLoadURIString(url, options) {
      assert.equal(url, DOCUMENT_URL); assert.equal(options.triggeringPrincipal, f.extension.principal); f.loadedURL = url;
    } };
  f.parent.manager = manager; f.parent.browsingContext = f.browser.browsingContext;
  f.child.manager = manager; f.child.browsingContext = f.browser.browsingContext;
  f.child.document = { nodePrincipal: { addonId: ID }, documentURI: DOCUMENT_URL };
  contexts.set(42, { active: true, viewType: "tab", extension: { id: ID, manifest: { version: VERSION } } });
  f.child.contentWindow = {
    setTimeout(resolve) { queueMicrotask(() => { f.bootMs += 50; resolve(); }); },
    BroadcastChannel: class {
      constructor(name) { assert.equal(name, "uBO"); f.channels.add(this); }
      close() { f.channels.delete(this); if (f.channelCloseError) throw new Error("private close detail"); }
    },
    browser: { storage: { local: { async get(keys) {
      assert.deepEqual(keys, { selectedFilterLists: null, importedLists: [] }); f.reads++;
      if (f.readGate) await f.readGate.promise;
      f.onRead?.(f.state, f.reads);
      return copied(f.state);
    } } } },
    vAPI: { messaging: { async send(channel, request) {
      assert.equal(channel, "dashboard"); f.sends.push(copied(request));
      if (f.sendGate) await f.sendGate.promise;
      if (request.what === "getLists") {
        assert.ok(f.reads >= 2, "baseline read must precede readiness mutation");
        f.onReady?.(f.state); return { metadataNeverExported: "private-list-canary" };
      }
      if (request.what === "applyFilterListSelection") {
        if (request.toImport) {
          assert.deepEqual(request, { what: "applyFilterListSelection", toImport: KEY });
          f.state.selectedFilterLists.push(KEY); f.state.importedLists.push(KEY); f.state.importedLists.sort();
        } else {
          assert.deepEqual(request, { what: "applyFilterListSelection", toRemove: [KEY] });
          f.state.selectedFilterLists = f.state.selectedFilterLists.filter(key => key !== KEY);
          f.state.importedLists = f.state.importedLists.filter(key => key !== KEY);
        }
      } else {
        assert.deepEqual(request, { what: "reloadAllFilters" });
        if (f.emit !== false) for (const channel of f.channels) channel.onmessage({ data: {
          what: "staticFilteringDataChanged", listKeys: [...f.state.selectedFilterLists].reverse(),
        } });
      }
    } } },
  };
  f.parent.sendQuery = async (name, data) => {
    const result = await f.child.receiveMessage({ name, data });
    f.afterReply?.(); return f.replyOverride ?? result;
  };
  f.binding = { owner: `{${randomUUID()}}`, browserID: 31, innerID: 42, documentURL: DOCUMENT_URL, key: KEY };
  f.query = (op, requestID = 1) => ({ name: ACTOR + ":fixed", data: { ...f.binding, schema: 1,
    operation: op, requestID, deadlineWallMs: Date.now() + DEADLINE_MS } });
  f.open = async () => {
    const module = await import(`../integration/filters/Actor.sys.mjs?inert=${++moduleID}`);
    f.module = module;
    f.config = { key: KEY, assertCurrent: () => f.ownerCurrent !== false,
      authorize: op => { f.authorizedOperations ??= []; f.authorizedOperations.push(op); return f.allowed !== false && op !== f.deniedOperation; },
      clock: () => ({ bootMs: f.bootMs, wallMs: Date.now() }) };
    return module.openSelectionActor(f.config);
  };
  return f;
}

test("closed binding/key/command validation rejects injection and alternative contexts", () => {
  const f = fixture(); validateBinding(f.binding);
  for (const key of ["javascript:alert(1)", "https://u:p@owned.invalid/a", "https://owned.invalid/a#x", "https://owned.invalid/\n"])
    assert.throws(() => validateKey(key));
  for (const change of [{ operation: "eval" }, { script: "private" }, { key: KEY + "?other" },
    { requestID: 0 }, { requestID: 33 }, { deadlineWallMs: NaN }, { innerID: 43 }])
    assert.throws(() => validateCommand({ ...f.query("observe").data, ...change }, f.binding));
});

test("readiness snapshots precede original getLists and metadata never crosses reply", async () => {
  const f = fixture();
  assert.deepEqual(validateReply(await f.child.receiveMessage(f.query("observe")), 1, "observe"), { selected: false, imported: false });
  assert.deepEqual(f.sends, [{ what: "getLists" }]);
  await f.child.receiveMessage(f.query("observe", 2));
  assert.equal(f.sends.length, 1, "same instance reuses completed readiness, not mutations");
});

test("native readiness migration is rejected before explicit supplement mutation", async () => {
  const f = fixture({ onReady: state => { state.importedLists = [CUSTOM]; } });
  const value = await f.child.receiveMessage(f.query("add"));
  assert.throws(() => validateReply(value, 1, "add"), code("actor_readiness_changed"));
  assert.deepEqual(f.sends, [{ what: "getLists" }]);
  assert.equal(f.child.destroyed, true); assert.equal(f.channels.size, 0);
  assert.equal(JSON.stringify(value).includes(CUSTOM), false);
});

test("same-field policy/context replacements cannot survive an awaited child read", async () => {
  for (const replace of [f => { f.policy = { ...f.policy }; },
    () => { contexts.set(42, { ...contexts.get(42) }); },
    () => { contexts.get(42).extension = { ...contexts.get(42).extension }; }]) {
    const f = fixture(); f.onRead = (_value, reads) => { if (reads === 1) replace(f); };
    const result = await f.child.receiveMessage(f.query("add"));
    assert.equal(result.ok, false); assert.equal(result.code, "actor_context");
    assert.equal(f.sends.length, 0);
  }
});

test("delayed getLists persistence cannot silently become the first Selection baseline", async () => {
  const f = fixture({ onRead: (state, reads) => {
    // readiness wait read, pre-getLists baseline, post-getLists read, then the
    // first read made by createSelectionCommand after a delayed native write.
    if (reads === 4) state.importedLists = [CUSTOM];
  } });
  const result = await f.child.receiveMessage(f.query("add"));
  assert.equal(result.ok, false); assert.equal(result.code, "actor_readiness_changed");
  assert.deepEqual(f.sends, [{ what: "getLists" }]);
});

test("actual child feeds Selection with full custom baseline and two reload events", async () => {
  const f = fixture(); const original = copied(f.state);
  const added = validateReply(await f.child.receiveMessage(f.query("add")), 1, "add");
  assert.equal(added.freshReload, true); assert.equal(added.reloadEvents, 2);
  const removed = validateReply(await f.child.receiveMessage(f.query("remove", 2)), 2, "remove");
  assert.equal(removed.selected, false); assert.ok(sameBaseline(baseline(original), baseline(f.state)));
  assert.equal(f.channels.size, 0);
  assert.deepEqual(f.sends.map(value => value.what), ["getLists", "applyFilterListSelection", "reloadAllFilters",
    "reloadAllFilters", "applyFilterListSelection", "reloadAllFilters", "reloadAllFilters"]);
});

test("child rejects wrong principal, page, policy, nesting and inactive extension before I/O", async () => {
  for (const alter of [f => { f.child.document.nodePrincipal.addonId = "other"; },
    f => { f.child.document.documentURI = DOCUMENT_URL.replace("about.html", "3p-filters.html"); },
    f => { f.policy.active = false; }, f => { f.browser.browsingContext.parent = {}; },
    () => { contexts.get(42).viewType = "background"; }]) {
    const f = fixture(); alter(f);
    const value = await f.child.receiveMessage(f.query("add"));
    assert.equal(value.ok, false); assert.equal(f.reads, 0); assert.equal(f.sends.length, 0);
  }
});

test("child busy rejection cannot clear another operation; destroy rejects late completion", async () => {
  const f = fixture({ readGate: deferred() });
  const pending = f.child.receiveMessage(f.query("add")); await flush();
  assert.equal((await f.child.receiveMessage(f.query("observe", 2))).ok, false);
  assert.equal(f.child.busy, true);
  f.child.didDestroy(); f.readGate.resolve();
  assert.equal((await pending).ok, false); assert.equal(f.sends.length, 0); assert.equal(f.child.busy, false);
});

test("child replay, missing reload event, cleanup failure and suspend cannot return success", async () => {
  let f = fixture(); await f.child.receiveMessage(f.query("observe"));
  assert.equal((await f.child.receiveMessage(f.query("add", 1))).ok, false);
  f = fixture({ emit: false });
  assert.equal((await f.child.receiveMessage(f.query("add"))).ok, false); assert.equal(f.channels.size, 0);
  f = fixture({ channelCloseError: true });
  assert.equal((await f.child.receiveMessage(f.query("add"))).code, "actor_cleanup");
  f = fixture({ readGate: deferred() });
  const pending = f.child.receiveMessage(f.query("add")); await flush();
  f.bootMs += DEADLINE_MS; f.readGate.resolve();
  assert.equal((await pending).code, "actor_deadline"); assert.equal(f.sends.length, 0);
});

test("parent validates actual binding again after an otherwise valid reply", async () => {
  for (const change of [f => { f.browser.browsingContext.currentWindowGlobal = {}; },
    f => { f.parent.manager.documentURI.spec += "?changed"; }, f => { f.policy = { ...f.policy }; }]) {
    const f = fixture();
    const owner = bindSelectionActor(f.parent, f.browser, { key: KEY, documentURL: DOCUMENT_URL, policy: f.policy, current: () => true });
    f.afterReply = () => change(f);
    await assert.rejects(owner.command("observe", Date.now() + DEADLINE_MS), code("actor_context"));
  }
});

test("parent rejects unbound/unsolicited messages and malformed or uncorrelated replies", async () => {
  const f = fixture();
  await assert.rejects(f.parent.fixedCommand(f.query("observe").data));
  assert.throws(() => f.parent.receiveMessage());
  const owner = bindSelectionActor(f.parent, f.browser, { key: KEY, documentURL: DOCUMENT_URL, policy: f.policy, current: () => true });
  for (const value of [{}, { ...reply({ selected: false, imported: false }, 1), requestID: 9 },
    { schema: 1, requestID: 3, ok: false, code: "private-canary" }]) {
    f.replyOverride = value;
    await assert.rejects(owner.command("observe", Date.now() + DEADLINE_MS), code("actor_reply"));
  }
  owner.close(); await assert.rejects(owner.command("observe", Date.now() + DEADLINE_MS));
});

test("unknown exceptions are sanitized without reading private properties", () => {
  const value = Object.defineProperty({}, "message", { get() { assert.fail("read private error"); } });
  assert.equal(closedError(value).code, "actor_other");
  assert.deepEqual(failed(value, 1), { schema: 1, requestID: 1, ok: false, code: "actor_other" });
});

test("full actor opening binds only original about page and rechecks signed package each command", async () => {
  const f = fixture(); const actor = await f.open();
  assert.equal(f.loadedURL, DOCUMENT_URL); assert.equal(f.inserted, 1); assert.equal(f.hashes, 1);
  assert.deepEqual(f.registrations[0].matches, [DOCUMENT_URL]);
  assert.equal(f.registrations[0].allFrames, false);
  assert.deepEqual(f.registrations[0].messageManagerGroups, ["webext-browsers"]);
  assert.equal(actor.current(), true);
  assert.deepEqual(await actor.command("observe"), { selected: false, imported: false });
  assert.equal(f.hashes, 3); assert.equal(f.lookups, 3);
  assert.deepEqual(Object.keys(actor).sort(), ["close", "command", "current"]);
  actor.close(); assert.equal(actor.current(), false);
  assert.equal(f.shutdowns, 1); assert.equal(f.unregisters, 1); assert.equal(timers.size, 0);
});

test("signed-state, disabled, wrong version, symlink, digest and length failures stop before page open", async () => {
  for (const alter of [f => { f.addon.signedState = 0; }, f => { f.addon.userDisabled = true; },
    f => { f.addon.version = "1.75.1"; }, f => { f.signaturesRequired = false; }, f => { f.symlink = true; },
    f => { f.digest = "0".repeat(64); }, f => { f.packageBytes--; }]) {
    const f = fixture(); alter(f);
    await assert.rejects(f.open(), code("actor_package"));
    assert.equal(f.registrations.length, 0); assert.equal(f.pages.length, 0); assert.equal(timers.size, 0);
  }
});

test("package replacement after reply and owner/authority loss reject receipts and close", async () => {
  for (const change of [f => { f.digest = "0".repeat(64); }, f => { f.extension = { ...f.extension }; },
    f => { f.ownerCurrent = false; }, f => { f.allowed = false; }]) {
    const f = fixture(); const actor = await f.open(); f.afterReply = () => change(f);
    await assert.rejects(actor.command("observe"), error => error instanceof FilterActorError);
    assert.equal(actor.current(), false); assert.equal(f.shutdowns, 1); assert.equal(f.unregisters, 1);
  }
});

test("idle current is identity only; expiry of add grant does not deny separately authorized removal", async () => {
  const f = fixture(); const actor = await f.open();
  await actor.command("add"); f.deniedOperation = "add";
  assert.equal(actor.current(), true);
  assert.equal((await actor.command("remove")).selected, false);
  actor.close();
});

test("timeout includes addon lookup and late open cannot register or leak a page", async () => {
  const f = fixture({ lookupGate: deferred() });
  const opening = f.open(); await flush();
  for (let i = 0; i < 20 && timers.size === 0; i++) await new Promise(resolve => setImmediate(resolve));
  assert.equal(timers.size, 1);
  const failedOpen = assert.rejects(opening, code("actor_deadline"));
  [...timers.values()][0].fn(); await failedOpen;
  f.lookupGate.resolve(); await flush();
  assert.equal(f.registrations.length, 0); assert.equal(f.pages.length, 0); assert.equal(timers.size, 0);
});

test("timeout during native browser creation retains claim even after late cleanup", async () => {
  const f = fixture({ createGate: deferred() });
  const opening = f.open();
  for (let i = 0; i < 20 && f.pages.length === 0; i++) await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.pages.length, 1);
  assert.equal(f.pages[0].unloaded, false);
  assert.equal(timers.size, 1);
  const failedOpen = assert.rejects(opening, code("actor_cleanup"));
  [...timers.values()][0].fn();
  await failedOpen;
  assert.equal(f.pages[0].unloaded, true);
  assert.equal(f.shutdowns, 1);
  assert.equal(f.lateBrowserReleased, 0, "shutdown cannot join an unresolved browser creation");
  await assert.rejects(f.module.openSelectionActor(f.config), code("actor_invalid"));
  assert.equal(f.pages.length, 1);
  f.createGate.resolve();
  await flush();
  assert.equal(f.lateBrowserReleased, 1);
  assert.equal(f.inserted, 0);
  assert.equal(f.loadedURL, undefined);
  assert.equal(f.registrations.length, 1);
  assert.equal(f.unregisters, 1);
  assert.equal(timers.size, 0);
  await assert.rejects(f.module.openSelectionActor(f.config), code("actor_invalid"));
  assert.equal(f.pages.length, 1, "late completion cannot revive or release the poisoned claim");
});

test("explicit close rejects pending IPC; late child completion cannot restart or reopen", async () => {
  const f = fixture(); const actor = await f.open(); f.readGate = deferred();
  const task = actor.command("add"); const rejection = assert.rejects(task, code("actor_closed"));
  await flush(); actor.close(); await rejection;
  f.readGate.resolve(); await flush();
  assert.equal(f.sends.length, 0); assert.equal(timers.size, 0); assert.equal(f.channels.size, 0);
});

test("suspend-aware command budget rejects delayed reply even before timer delivery", async () => {
  const f = fixture(); const actor = await f.open(); f.afterReply = () => { f.bootMs += DEADLINE_MS; };
  await assert.rejects(actor.command("observe"), code("actor_deadline"));
  assert.equal(actor.current(), false); assert.equal(timers.size, 0);
});

test("old closed handle cannot release a newer owner's singleton claim", async () => {
  const f = fixture(); const old = await f.open(); old.close();
  // Reuse this module singleton with a new native context, not a fresh module.
  const module = f.module;
  const newerEnv = fixture();
  const config = { key: KEY, assertCurrent: () => true, authorize: () => true,
    clock: () => ({ bootMs: newerEnv.bootMs, wallMs: Date.now() }) };
  const newer = await module.openSelectionActor(config);
  old.close();
  await assert.rejects(module.openSelectionActor(config), code("actor_invalid"));
  assert.equal(newer.current(), true); newer.close();
});

test("failed unregister poisons claim across repeated close rather than permitting replacement", async () => {
  const f = fixture(); const actor = await f.open(); f.unregisterError = true;
  assert.throws(() => actor.close(), code("actor_cleanup"));
  f.unregisterError = false;
  assert.throws(() => actor.close(), code("actor_cleanup"));
  await assert.rejects(f.module.openSelectionActor(f.config), code("actor_invalid"));
});

test("deadline rejects clock regression and excessive child lease", () => {
  let now = { bootMs: 100, wallMs: 1000 };
  const deadline = makeDeadline(() => now); deadline.check();
  now = { bootMs: 99, wallMs: 1000 }; assert.throws(() => deadline.check(), code("actor_clock"));
  assert.throws(() => makeDeadline(() => ({ bootMs: 0, wallMs: 0 }), DEADLINE_MS + 1), code("actor_deadline"));
});
