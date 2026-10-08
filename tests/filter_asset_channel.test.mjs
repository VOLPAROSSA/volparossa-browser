// SPDX-License-Identifier: GPL-3.0-only
// Actual adapter and content validation with inert native capability doubles.
// No Firefox, real XPI validation, network delivery or engine-proof claim.
import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import { ID, VERSION, XPI_BYTES, XPI_SHA256 } from "../integration/filters/ActorContract.sys.mjs";

const digest = value => createHash("sha256").update(value).digest("hex");
const BODY = "[Adblock Plus 2.0]\n||ads.asset.invalid^\n||track.asset.invalid^\n";
const ENVELOPE = Buffer.from("synthetic fixture; not a signature proof");
const adapterSource = fs.readFileSync(new URL("../integration/filters/AssetChannel.sys.mjs", import.meta.url), "utf8");
let env, sequence = 0;
const constants = {
  nsIXULRuntime: { PROCESS_TYPE_DEFAULT: 0 }, nsICryptoHash: {}, nsIJARURI: {}, nsIFileURL: {},
  nsIHttpChannel: {}, nsIStringInputStream: {},
  nsICachingChannel: { LOAD_NO_NETWORK_IO: 1, LOAD_BYPASS_LOCAL_CACHE: 2 },
  nsIRequest: { INHIBIT_CACHING: 4, LOAD_FROM_CACHE: 8 },
  nsIChannel: { LOAD_DOCUMENT_URI: 16, LOAD_BYPASS_SERVICE_WORKER: 32 },
  nsIContentPolicy: { TYPE_XMLHTTPREQUEST: 11 },
  nsINetworkInterceptController: { equals(iid) { return iid === this; } },
};
globalThis.Ci = constants;
globalThis.Cr = { NS_BINDING_ABORTED: 99, NS_ERROR_NO_INTERFACE: 100 };
globalThis.Components = { isSuccessCode: code => code === 0 };
globalThis.Services = {
  appinfo: { processType: 0 },
  prefs: { getBoolPref: name => name === "xpinstall.signatures.required" ? env.signedRequired : env.workersEnabled },
  obs: {
    addObserver(observer, topic) { assert.equal(env.observers.has(topic), false); env.observers.set(topic, observer); },
    removeObserver(observer, topic) { assert.equal(env.observers.get(topic), observer); env.observers.delete(topic); },
  },
};
globalThis.Cc = {
  "@mozilla.org/security/hash;1": { createInstance() { let data; return {
    SHA256: 1, init() {}, update(bytes, length) { assert.equal(bytes.length, length); data = bytes; },
    finish() { return data.packageSpy ? Buffer.from(env.packageSHA, "hex").toString("latin1")
      : createHash("sha256").update(data).digest("latin1"); },
  }; } },
  "@mozilla.org/io/string-input-stream;1": { createInstance() {
    const stream = { setByteStringData(text) { this.text = text; }, close() { this.closed = true; } };
    env.streams.push(stream); return stream;
  } },
};
globalThis.IOUtils = { async read(path, options) {
  assert.equal(path, "/signed-original.xpi"); assert.deepEqual(options, { maxBytes: XPI_BYTES + 1 });
  env.reads++; if (env.readGate) await env.readGate;
  return { packageSpy: true, length: env.packageBytes };
} };
globalThis.WebExtensionPolicy = { getByID: id => id === ID ? env.policy : null };
globalThis.ChromeUtils = {
  generateQI: () => function() { return this; },
  importESModule(uri) {
    if (uri.endsWith("/AddonManager.sys.mjs")) return { AddonManager: { SIGNEDSTATE_SIGNED: 2,
      async getAddonByID(id) { assert.equal(id, ID); return env.addon; } } };
    if (uri.endsWith("/ExtensionParent.sys.mjs")) return { ExtensionParent: { GlobalManager: {
      getExtension: id => id === ID ? env.extension : null,
    } } };
    assert.ok(uri.endsWith("/Timer.sys.mjs"));
    return { setTimeout(fn, ms) { const id = ++env.nextTimer; env.timers.set(id, { fn, ms }); return id; },
      clearTimeout(id) { env.timers.delete(id); } };
  },
};
const errorCode = code => error => error.code === code && error.message === code;

async function fixture() {
  const f = env = { observers: new Map(), timers: new Map(), nextTimer: 0, streams: [], reads: 0,
    signedRequired: true, workersEnabled: true, packageSHA: XPI_SHA256, packageBytes: XPI_BYTES,
    wallMs: 1_000_000, bootMs: 500, authorized: true, current: true };
  f.policy = { active: true };
  f.principal = { addonPolicy: f.policy, isSystemPrincipal: false, equals(value) { return value === this; } };
  f.global = { innerWindowId: 42, documentPrincipal: f.principal };
  f.context = { id: 27, parent: null, currentWindowGlobal: f.global };
  // ESR140's parent ProxyContext leaves BaseContext.innerWindowID at zero;
  // its native actor owns the authoritative WindowGlobal and BrowsingContext.
  f.background = { isBackgroundContext: true, active: true, browsingContext: f.context, innerWindowID: 0,
    actor: { manager: f.global, browsingContext: f.context } };
  f.extension = { manifest: { version: VERSION }, policy: f.policy, backgroundContext: f.background };
  f.addon = { id: ID, version: VERSION, isActive: true, signedState: 2, userDisabled: false, appDisabled: false,
    getResourceURI: () => ({ QueryInterface: () => ({ JARFile: { QueryInterface: () => ({ file: {
      path: "/signed-original.xpi", isSymlink: () => Boolean(f.symlink), isFile: () => true, fileSize: XPI_BYTES,
    } }) } }) }) };
  f.expected = { publisher_key: "1".repeat(64), name: "disposable-public-domain-filters",
    manifest_id: digest(ENVELOPE), authority_expires_unix_seconds: 1100 };
  f.fetched = { filters: BODY, manifest_hex: ENVELOPE.toString("hex"), snapshot: {
    generation: f.expected.manifest_id, publisher_key: f.expected.publisher_key, name: f.expected.name,
    revision: 1, sha256: digest(BODY), bytes: Buffer.byteLength(BODY), rules: 2, grammar: "ubo-domain-block-v1",
    verified_at_unix_seconds: 999, expires_unix_seconds: 1090, authorization_expires_unix_seconds: 1090,
  } };
  f.options = { expected: f.expected, fetched: f.fetched, assertCurrent: () => f.current,
    authorize: () => f.authorized, clock: () => ({ bootMs: f.bootMs, wallMs: f.wallMs }) };
  globalThis.__inertInstallRedirect = ({ transfer }) => { f.transfer = transfer; };
  const redirectImport = 'import { installAssetRedirectGuard } from "./AssetRedirect.sys.mjs";';
  assert.equal(adapterSource.split(redirectImport).length, 2);
  let source = adapterSource.replace(redirectImport,
    "const installAssetRedirectGuard = globalThis.__inertInstallRedirect;");
  for (const name of ["Contract", "ActorContract"]) source = source.replace(`"./${name}.sys.mjs"`,
    JSON.stringify(new URL(`../integration/filters/${name}.sys.mjs`, import.meta.url).href));
  f.module = await import("data:text/javascript;base64," + Buffer.from(source + `\n// fixture ${++sequence}`).toString("base64"));
  f.key = f.module.assetKey(f.expected);
  f.open = () => f.module.openAssetChannel(f.options);
  f.callbackRecords = new WeakMap();
  f.wrapCallbacks = implementation => {
    if (implementation === null) return null;
    const known = f.callbackRecords.get(implementation);
    if (known) return known.requestor;
    // XPConnect native getters return wrappers, not the original JS object.
    const record = { implementation, requestor: null, controller: null };
    record.controller = Object.freeze({
      shouldPrepareForIntercept: (...args) => implementation.shouldPrepareForIntercept(...args),
      channelIntercepted: (...args) => implementation.channelIntercepted(...args),
    });
    record.requestor = Object.freeze({ getInterface(iid) {
      if (iid.equals(Ci.nsINetworkInterceptController)) {
        assert.equal(implementation.getInterface(iid), implementation);
        return record.controller;
      }
      return implementation.getInterface(iid);
    } });
    f.callbackRecords.set(implementation, record);
    f.callbackRecords.set(record.requestor, record);
    return record.requestor;
  };
  f.controller = channel => channel.notificationCallbacks.getInterface(Ci.nsINetworkInterceptController);
  f.channel = (spec = f.key + "?_=7") => {
    const uri = { spec, asciiHost: new URL(spec).hostname };
    let callbacks = null;
    return { URI: uri, originalURI: uri, requestMethod: "GET", channelId: 9, loadFlags: 0, headers: [],
      loadInfo: { externalContentPolicyType: 11, browsingContextID: 27, innerWindowID: 42,
        loadingPrincipal: f.principal, triggeringPrincipal: f.principal },
      QueryInterface() { return this; }, visitRequestHeaders(visitor) { for (const pair of this.headers) visitor.visitHeader(...pair); },
      cancel(code) { this.canceled = code; },
      get notificationCallbacks() { return callbacks; },
      set notificationCallbacks(value) { callbacks = f.wrapCallbacks(value); } };
  };
  f.opening = channel => f.observers.get("http-on-opening-request").observe(channel, "http-on-opening-request");
  f.deliver = (channel, { defer = false, replacement = true, transfer = true, cloneAfterTransfer = false } = {}) => {
    const callbacks = f.controller(channel);
    assert.equal(callbacks.shouldPrepareForIntercept(channel.URI, channel), true);
    let next = replacement ? { ...channel } : channel;
    if (replacement && transfer) {
      // The native sink runs before originalURI is assigned to the replacement.
      next.originalURI = null;
      assert.equal(f.transfer(channel, next), true);
      next.originalURI = channel.originalURI;
    }
    const transferred = replacement && transfer ? next : null;
    if (cloneAfterTransfer) next = { ...next };
    const intercepted = { channel: next, headers: {},
      synthesizeStatus(status, text) { this.status = status; assert.equal(text, "OK"); },
      synthesizeHeader(name, value) { this.headers[name] = value; },
      startSynthesizedResponse(stream, callback, cache, url, redirected) {
        assert.equal(cache, null); assert.equal(url, ""); assert.equal(redirected, false);
        this.stream = stream; this.callback = callback;
      },
      finishSynthesizedResponse() { this.finished = true; },
      cancelInterception(code) { this.canceled = code; },
      resetInterception() { assert.fail("must never fall back to network"); },
    };
    callbacks.channelIntercepted(intercepted);
    if (!defer && intercepted.callback) intercepted.callback.bodyComplete(0);
    return { next, transferred, intercepted };
  };
  return f;
}

test("only explicit opening verifies content and exact signed package before installing guard", async () => {
  const f = await fixture(); assert.equal(f.observers.size, 0);
  assert.equal(f.background.innerWindowID, 0);
  const service = await f.open(); assert.equal(f.reads, 1);
  assert.equal(service.key, f.key); assert.equal(f.observers.size, 2);
  assert.deepEqual(service.status, { closed: false, accepted: 0, completed: 0, denied: 0 });
  service.close(); assert.equal(f.timers.size, 0); assert.equal(f.observers.size, 2);
});

test("opening rejects missing or replaced native actor manager and browsing context", async () => {
  for (const change of [f => { f.background.actor = null; },
    f => { f.background.actor.manager = { ...f.global }; },
    f => { f.background.actor.browsingContext = { ...f.context }; },
    f => { f.context.currentWindowGlobal = { ...f.global }; }]) {
    const f = await fixture(); change(f);
    await assert.rejects(f.open(), errorCode("asset_context"));
    assert.equal(f.observers.size, 0); assert.equal(f.timers.size, 0);
  }
});

test("one exact background request receives original bounded bytes through local synthesis", async () => {
  const f = await fixture(); const service = await f.open(); const channel = f.channel(); f.opening(channel);
  assert.equal(channel.loadFlags, 7);
  const { intercepted } = f.deliver(channel);
  assert.equal(intercepted.stream.text, BODY); assert.equal(intercepted.stream.closed, true);
  assert.equal(intercepted.status, 200); assert.equal(intercepted.headers["Cache-Control"], "no-store");
  assert.equal(intercepted.headers["Content-Length"], String(BODY.length)); assert.equal(intercepted.finished, true);
  assert.deepEqual(service.status, { closed: false, accepted: 1, completed: 1, denied: 0 });
  service.close();
});

test("native XPConnect roundtrip wrappers deliver without equating them to the assigned JS object", async () => {
  const f = await fixture(); const service = await f.open(); const channel = f.channel(); f.opening(channel);
  const requestor = channel.notificationCallbacks;
  const { implementation, controller } = f.callbackRecords.get(requestor);
  assert.notEqual(requestor, implementation); assert.notEqual(controller, implementation);
  assert.equal(channel.notificationCallbacks, requestor); assert.equal(f.controller(channel), controller);
  const { next, intercepted } = f.deliver(channel);
  assert.equal(next.notificationCallbacks, requestor); assert.equal(f.controller(next), controller);
  assert.equal(intercepted.stream.text, BODY); assert.equal(service.status.completed, 1);
  service.close();
});

for (const fault of ["requestor_null", "getter_throw", "controller_null", "controller_throw", "unstable_requestor"]) {
  test(`native capture ${fault} cancels without bytes, leaked errors or a late fallback`, async () => {
    const f = await fixture(); const service = await f.open(); const channel = f.channel();
    const property = Object.getOwnPropertyDescriptor(channel, "notificationCallbacks");
    const canary = "private-native-capture-error";
    let installed = false, reads = 0, native = null;
    const badController = { getInterface() {
      if (fault === "controller_throw") throw new Error(canary);
      return null;
    } };
    Object.defineProperty(channel, "notificationCallbacks", {
      get() {
        if (!installed) return property.get.call(channel);
        reads++;
        if (fault === "requestor_null") return null;
        if (fault === "getter_throw") throw new Error(canary);
        if (fault === "controller_null" || fault === "controller_throw") return badController;
        return reads === 1 ? native : { ...native };
      },
      set(value) {
        property.set.call(channel, value);
        native = property.get.call(channel);
        installed = true;
      },
    });
    assert.doesNotThrow(() => f.opening(channel));
    assert.equal(installed, true);
    assert.equal(reads, fault === "unstable_requestor" ? 2 : 1);
    assert.deepEqual(service.status, { closed: true, accepted: 1, completed: 0, denied: 0 });
    assert.equal(channel.canceled, 99); assert.equal(channel.loadFlags, 7);
    assert.equal(f.streams.length, 0); assert.equal(f.timers.size, 0);
    assert.equal(JSON.stringify({ status: service.status, diagnostic: service.diagnostic }).includes(canary), false);
    const controller = native.getInterface(Ci.nsINetworkInterceptController);
    assert.equal(controller.shouldPrepareForIntercept(channel.URI, channel), false);
    const late = f.channel(); f.opening(late);
    assert.equal(late.canceled, 99); assert.equal(late.notificationCallbacks, null);
    assert.equal(f.streams.length, 0); assert.equal(service.status.completed, 0);
    service.close(); assert.equal(f.timers.size, 0);
  });
}

test("replacement cannot substitute requestor or controller even when the other native identity matches", async () => {
  for (const substitute of ["requestor", "controller"]) {
    const f = await fixture(); const service = await f.open(); const channel = f.channel(); f.opening(channel);
    const requestor = channel.notificationCallbacks;
    const native = f.callbackRecords.get(requestor);
    assert.equal(f.controller(channel).shouldPrepareForIntercept(channel.URI, channel), true);
    const next = { ...channel, originalURI: null };
    if (substitute === "requestor") {
      next.notificationCallbacks = { getInterface: iid => requestor.getInterface(iid) };
      assert.notEqual(next.notificationCallbacks, requestor);
      assert.equal(f.controller(next), native.controller);
    } else {
      const oldController = native.controller;
      native.controller = { ...oldController };
      assert.equal(next.notificationCallbacks, requestor);
      assert.notEqual(f.controller(next), oldController);
    }
    assert.throws(() => f.transfer(channel, next), errorCode("asset_context"));
    assert.deepEqual(service.diagnostic, { checkpoint: "transfer_callbacks" });
    assert.equal(f.streams.length, 0); assert.equal(service.status.completed, 0);
    service.close(); assert.equal(channel.canceled, 99); assert.equal(f.timers.size, 0);
  }
});

test("parent diagnostic is a closed checkpoint only, never request values or content", async () => {
  const f = await fixture(); const service = await f.open();
  assert.deepEqual(service.diagnostic, { checkpoint: "ready" });
  const channel = f.channel(); channel.loadInfo.browsingContextID = 0;
  f.opening(channel);
  assert.deepEqual(service.diagnostic, { checkpoint: "request_context" });
  assert.equal(Object.isFrozen(service.diagnostic), true);
  assert.deepEqual(service.status, { closed: false, accepted: 0, completed: 0, denied: 1 });
  service.close();
});

test("an equal-looking replacement without native transfer receives no bytes or success", async () => {
  const f = await fixture(); const service = await f.open(); const channel = f.channel(); f.opening(channel);
  const { next, intercepted } = f.deliver(channel, { transfer: false });
  assert.notEqual(next, channel); assert.equal(next.channelId, channel.channelId);
  assert.equal(intercepted.status, undefined); assert.equal(intercepted.callback, undefined);
  assert.equal(intercepted.canceled, 99); assert.equal(f.streams.length, 0);
  assert.equal(next.canceled, 99); assert.equal(channel.canceled, 99);
  assert.equal(service.status.completed, 0); assert.equal(service.status.closed, true);
  assert.equal(f.timers.size, 0);
});

test("native transfer authorizes the exact replacement object, not a later matching clone", async () => {
  const f = await fixture(); const service = await f.open(); const channel = f.channel(); f.opening(channel);
  const { next, transferred, intercepted } = f.deliver(channel, { cloneAfterTransfer: true });
  assert.notEqual(next, transferred); assert.equal(next.channelId, transferred.channelId);
  assert.equal(intercepted.status, undefined); assert.equal(intercepted.callback, undefined);
  assert.equal(intercepted.canceled, 99); assert.equal(f.streams.length, 0);
  assert.equal(next.canceled, 99); assert.equal(transferred.canceled, 99); assert.equal(channel.canceled, 99);
  assert.equal(service.status.completed, 0); assert.equal(service.status.closed, true);
  assert.equal(f.timers.size, 0);
});

test("strict original-uBO query forms only, with no escaped variants, redirects or injected URLs", async () => {
  for (const suffix of ["", "?_=0", "?_=12", "?_=86412"]) {
    const f = await fixture(); const service = await f.open(); const channel = f.channel(f.key + suffix);
    f.opening(channel); f.deliver(channel); assert.equal(service.status.completed, 1); service.close();
  }
  for (const suffix of ["?_=86413", "?_=01", "?_=+1", "?_=1&x=2", "?_=1#x", "?%5f=1", "/other", "?_=", "#x"]) {
    const f = await fixture(); const service = await f.open(); const channel = f.channel(f.key + suffix);
    f.opening(channel); assert.equal(channel.canceled, 99); assert.equal(channel.notificationCallbacks, null);
    assert.equal(service.status.denied, 1); service.close();
  }
});

test("wrong principal, context, method, upload type, security flags or auth headers never get a controller", async () => {
  for (const change of [c => { c.loadInfo.loadingPrincipal = null; },
    c => { c.loadInfo.triggeringPrincipal = { addonPolicy: env.policy, equals: () => false }; },
    c => { c.loadInfo.innerWindowID++; }, c => { c.loadInfo.browsingContextID++; },
    c => { c.loadInfo.externalContentPolicyType = 1; }, c => { c.requestMethod = "POST"; },
    c => { c.loadFlags = 8; }, c => { c.loadFlags = 16; }, c => { c.loadFlags = 32; },
    c => { c.headers = [["Cookie", "secret"]]; }, c => { c.headers = [["Authorization", "secret"]]; },
    c => { c.headers = [["Range", "bytes=0-1"]]; }, c => { c.headers = [["If-None-Match", "old"]]; },
    c => { c.originalURI = { spec: "https://other.invalid/" }; },
    () => { env.workersEnabled = false; }]) {
    const f = await fixture(); const service = await f.open(); const channel = f.channel(); change(channel);
    f.opening(channel); assert.equal(channel.canceled, 99); assert.equal(channel.notificationCallbacks, null);
    service.close();
  }
});

test("header allocation and request count stay bounded, unrelated native callbacks are delegated", async () => {
  const f = await fixture(); const service = await f.open(); const channel = f.channel();
  channel.notificationCallbacks = { getInterface(iid) { assert.equal(iid.name, "other"); return 123; } };
  f.opening(channel);
  assert.equal(channel.notificationCallbacks.getInterface({ name: "other", equals: () => false }), 123);
  const extra = f.channel(); f.opening(extra); assert.equal(extra.canceled, 99);
  f.deliver(channel); service.close();
  const large = await fixture(); const next = await large.open(); const bad = large.channel();
  bad.headers = [["Accept", "x".repeat(8193)]]; large.opening(bad); assert.equal(bad.canceled, 99); next.close();
});

test("unrelated domains remain untouched and closed virtual namespace never falls through", async () => {
  const f = await fixture(); const service = await f.open(); const unrelated = f.channel("https://ordinary.invalid/");
  f.opening(unrelated); assert.equal(unrelated.loadFlags, 0); assert.equal(unrelated.canceled, undefined);
  service.close(); const late = f.channel(); f.opening(late); assert.equal(late.canceled, 99); assert.equal(late.loadFlags, 7);
});

test("snapshot bytes, metadata, envelope, policy expiry and grammar are independently revalidated", async () => {
  for (const change of [f => { f.fetched.filters += "||evil.invalid^\n"; },
    f => { f.fetched.manifest_hex = "ff"; }, f => { f.fetched.snapshot.bytes--; },
    f => { f.fetched.snapshot.publisher_key = "3".repeat(64); },
    f => { f.fetched.snapshot.authorization_expires_unix_seconds = 2000; },
    f => { f.fetched.filters = "!#include https://other.invalid/"; },
    f => { f.expected.authority_expires_unix_seconds = 1000; }]) {
    const f = await fixture(); change(f); await assert.rejects(f.open());
    assert.equal(f.reads, 0); assert.equal(f.observers.size, 0);
  }
});

test("wrong signature, package digest, symlink or version never activates virtual delivery", async () => {
  for (const change of [f => { f.packageSHA = "0".repeat(64); }, f => { f.packageBytes--; },
    f => { f.addon.signedState = 0; }, f => { f.addon.version = "other"; },
    f => { f.signedRequired = false; }, f => { f.symlink = true; }]) {
    const f = await fixture(); change(f); await assert.rejects(f.open(), errorCode("asset_package"));
    assert.equal(f.observers.size, 0); assert.equal(f.timers.size, 0);
  }
});

test("expiry, suspend, clock regression and owner/authority/context loss cancel mid-stream", async () => {
  for (const change of [f => { f.wallMs += 40_000; }, f => { f.bootMs += 40_000; },
    f => { f.bootMs--; }, f => { f.wallMs--; }, f => { f.current = false; }, f => { f.authorized = false; },
    f => { f.policy.active = false; }, f => { f.context.currentWindowGlobal = { ...f.global }; },
    f => { f.background.actor.manager = { ...f.global }; },
    f => { f.background.actor.browsingContext = { ...f.context }; },
    f => { f.background.actor = null; },
    f => { f.extension.backgroundContext = { ...f.background }; }]) {
    const f = await fixture(); const service = await f.open(); const channel = f.channel(); f.opening(channel);
    const { next, intercepted } = f.deliver(channel, { defer: true }); change(f);
    intercepted.callback.bodyComplete(0);
    assert.equal(next.canceled, 99); assert.equal(channel.canceled, 99); assert.equal(service.status.completed, 0);
    assert.equal(service.status.closed, true); assert.equal(f.timers.size, 0);
  }
});

test("timer and explicit close both abort outstanding bytes; late callbacks cannot count success", async () => {
  for (const useTimer of [false, true]) {
    const f = await fixture(); const service = await f.open(); const channel = f.channel(); f.opening(channel);
    const { intercepted } = f.deliver(channel, { defer: true });
    if (useTimer) { f.authorized = false; const [id, timer] = [...f.timers.entries()][0]; f.timers.delete(id); timer.fn(); }
    else service.close();
    intercepted.callback.bodyComplete(0);
    assert.equal(service.status.completed, 0); assert.equal(channel.canceled, 99); assert.equal(f.timers.size, 0);
  }
});

test("replacement identities and repeat interception cannot authorize an unrelated channel", async () => {
  const f = await fixture(); const service = await f.open(); const channel = f.channel(); f.opening(channel);
  const other = { ...channel, channelId: 10 };
  assert.equal(f.controller(channel).shouldPrepareForIntercept(other.URI, other), false);
  assert.equal(service.status.closed, true); assert.equal(channel.canceled, 99);
});

test("redirect transfer requires the owned native object plus unchanged context, callbacks and key", async () => {
  const f = await fixture(); const service = await f.open(); const channel = f.channel(); f.opening(channel);
  assert.equal(f.controller(channel).shouldPrepareForIntercept(channel.URI, channel), true);
  const next = { ...channel };
  next.originalURI = null; // IDL: not yet assigned during redirect verification.
  assert.throws(() => f.transfer({ ...channel }, next), errorCode("asset_context"));
  assert.throws(() => f.transfer(channel, { ...next, channelId: 10 }), errorCode("asset_context"));
  assert.deepEqual(service.diagnostic, { checkpoint: "transfer_identity" });
  assert.throws(() => f.transfer(channel, { ...next, notificationCallbacks: null }), errorCode("asset_context"));
  assert.deepEqual(service.diagnostic, { checkpoint: "transfer_callbacks" });
  assert.equal(f.transfer(channel, next), true);
  assert.deepEqual(service.diagnostic, { checkpoint: "transfer" });
  assert.throws(() => f.transfer(channel, next), errorCode("asset_context"));
  service.close(); assert.equal(next.canceled, 99); assert.equal(channel.canceled, 99);
  assert.equal(f.transfer(channel, next), false);
});

test("overlapping owners refuse and a stale close cannot release the next owner", async () => {
  const f = await fixture(); const old = await f.open();
  await assert.rejects(f.open(), errorCode("asset_busy")); old.close();
  const next = await f.open(); old.close(); await assert.rejects(f.open(), errorCode("asset_busy")); next.close();
});

test("process shutdown removes only its own guard and permanently refuses reopening", async () => {
  const f = await fixture(); const service = await f.open();
  f.observers.get("xpcom-shutdown").observe(null, "xpcom-shutdown");
  assert.equal(service.status.closed, true); assert.equal(f.observers.size, 0); assert.equal(f.timers.size, 0);
  await assert.rejects(f.open(), errorCode("asset_busy"));
});

test("opening timeout settles a stuck native read and its late result cannot activate a lease", async () => {
  const f = await fixture(); let finish;
  f.readGate = new Promise(resolve => { finish = resolve; });
  const opening = f.open(); const rejected = assert.rejects(opening, errorCode("asset_expired"));
  for (let i = 0; i < 20 && f.reads === 0; i++) await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.reads, 1); assert.equal(f.timers.size, 1);
  [...f.timers.values()][0].fn(); await rejected;
  assert.equal(f.timers.size, 0); assert.equal(f.observers.size, 0);
  finish(); for (let i = 0; i < 5; i++) await Promise.resolve();
  assert.equal(f.observers.size, 0); await assert.rejects(f.open(), errorCode("asset_busy"));
});
