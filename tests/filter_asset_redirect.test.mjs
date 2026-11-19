// SPDX-License-Identifier: GPL-3.0-only
// Actual module methods with inert XPCOM registration/channel spies. No native
// redirect, browser, network, disk registry or all-redirect/no-DNS proof.
import test from "node:test";
import assert from "node:assert/strict";

const KEY = "https://filters.volparossa.invalid/v1/" + "a".repeat(64) + ".txt";
const CONTRACT = "@volparossa.org/filter-asset-redirect-guard;1";
const CATEGORY = "net-channel-event-sinks";
let serial = 0, env;
globalThis.Ci = { nsIXULRuntime: { PROCESS_TYPE_DEFAULT: 0 }, nsIComponentRegistrar: {},
  nsIChannelEventSink: { REDIRECT_INTERNAL: 4, REDIRECT_TEMPORARY: 1, REDIRECT_TRANSPARENT: 32 },
  nsICachingChannel: { LOAD_NO_NETWORK_IO: 1 << 26, LOAD_BYPASS_LOCAL_CACHE: 1 << 28 },
  nsIRequest: { INHIBIT_CACHING: 1 << 7 } };
globalThis.Cr = { NS_OK: 0, NS_BINDING_ABORTED: 0x804b0002, NS_ERROR_NOT_AVAILABLE: 0x80040111 };
globalThis.ChromeUtils = { generateQI(names) {
  assert.deepEqual(names, ["nsIChannelEventSink", "nsIFactory"]); return function () { return this; };
} };
globalThis.Components = { ID: value => value, manager: { QueryInterface(iid) {
  assert.equal(iid, Ci.nsIComponentRegistrar); return env.registrar;
} } };
globalThis.Services = { appinfo: { processType: 0 },
  catMan: {
    getCategoryEntry(category, key) {
      assert.equal(category, CATEGORY); assert.equal(key, CONTRACT);
      if (env.categoryCollision) return "foreign";
      if (env.categoryReadError) throw new Error("private category detail");
      throw { result: Cr.NS_ERROR_NOT_AVAILABLE };
    },
    addCategoryEntry(...args) {
      assert.deepEqual(args, [CATEGORY, CONTRACT, CONTRACT, false, false]);
      env.categoryAdds++; if (env.categoryError) throw new Error("private category detail");
    },
    deleteCategoryEntry(...args) {
      assert.deepEqual(args, [CATEGORY, CONTRACT, false]); env.categoryDeletes++;
      if (env.cleanupError) throw new Error("private category detail");
    },
  },
  obs: {
    addObserver(observer, topic) {
      assert.equal(topic, "xpcom-shutdown"); env.observerAdds++; env.shutdown = observer;
      if (env.observerError) throw new Error("private observer detail");
    },
    removeObserver(observer, topic) {
      assert.equal(topic, "xpcom-shutdown"); assert.equal(observer, env.shutdown); env.observerRemoves++;
    },
  },
};
async function fixture(options = {}) {
  Services.appinfo.processType = 0;
  env = { categoryAdds: 0, categoryDeletes: 0, observerAdds: 0, observerRemoves: 0,
    registrations: 0, unregisters: 0, transfers: 0, ...options };
  const f = env;
  f.registrar = {
    isCIDRegistered: () => Boolean(f.factoryCollision),
    isContractIDRegistered: () => Boolean(f.contractCollision),
    registerFactory(cid, _description, contract, sink) {
      assert.match(cid, /^\{[0-9a-f-]+\}$/); assert.equal(contract, CONTRACT);
      f.registrations++; f.sink = sink; f.cid = cid;
      if (f.factoryError) throw new Error("private factory detail");
    },
    unregisterFactory(cid, sink) { assert.equal(cid, f.cid); assert.equal(sink, f.sink); f.unregisters++; },
  };
  f.module = await import(`../integration/filters/AssetRedirect.sys.mjs?inert=${++serial}`);
  f.transfer = (oldChannel, newChannel) => {
    f.transfers++; f.transferred = [oldChannel, newChannel];
    if (f.transferError) throw new Error("private transfer detail");
    return f.transferResult ?? false;
  };
  f.install = () => f.module.installAssetRedirectGuard({ transfer: f.transfer });
  return f;
}
function channel(url = KEY, options = {}) {
  const uri = new URL(url);
  let flags = 0;
  return { URI: { scheme: uri.protocol.slice(0, -1), asciiHost: uri.hostname, spec: url },
    // newChannel.originalURI is intentionally not available at this phase.
    get originalURI() { assert.fail("read premature originalURI"); },
    get loadFlags() { return flags; }, set loadFlags(value) {
      if (options.flagsError) throw new Error("private native detail"); flags = value;
    },
    cancels: [], cancel(status) { this.cancels.push(status); if (options.cancelError) throw new Error("private cancel detail"); },
  };
}
function redirect(f, oldChannel, newChannel, flags = Ci.nsIChannelEventSink.REDIRECT_INTERNAL, callback = null) {
  const results = [];
  f.sink.asyncOnChannelRedirect(oldChannel, newChannel, flags, callback ?? {
    onRedirectVerifyCallback: status => results.push(status),
  });
  return results;
}
const denied = (f, oldChannel, newChannel, flags) => {
  assert.deepEqual(redirect(f, oldChannel, newChannel, flags), [Cr.NS_BINDING_ABORTED]);
  for (const item of [oldChannel, newChannel]) {
    assert.deepEqual(item.cancels, [Cr.NS_BINDING_ABORTED]);
    assert.ok(item.loadFlags & Ci.nsICachingChannel.LOAD_NO_NETWORK_IO);
  }
};

test("explicit parent installation uses only a new nonpersistent native category/factory", async () => {
  const f = await fixture(); f.install(); f.install();
  assert.equal(f.registrations, 1); assert.equal(f.categoryAdds, 1); assert.equal(f.observerAdds, 1);
  assert.equal(f.sink.createInstance({}), f.sink);
  assert.throws(() => f.module.installAssetRedirectGuard({ transfer: () => true }), error => error.code === "redirect_config");
  const newer = await fixture(); Services.appinfo.processType = 1;
  assert.throws(newer.install, error => error.code === "redirect_config");
  assert.equal(newer.registrations, 0);
});

test("foreign redirects into and owned redirects out of the namespace never consult transfer", async () => {
  const f = await fixture({ transferResult: true }); f.install();
  denied(f, channel("https://foreign.invalid/start"), channel());
  denied(f, channel(), channel("https://foreign.invalid/end"));
  denied(f, channel(), channel(KEY + "?_=1"));
  denied(f, channel(), channel(), Ci.nsIChannelEventSink.REDIRECT_TEMPORARY);
  denied(f, channel(), channel(), Ci.nsIChannelEventSink.REDIRECT_INTERNAL | Ci.nsIChannelEventSink.REDIRECT_TRANSPARENT);
  const http = KEY.replace("https:", "http:"); denied(f, channel(http), channel(http));
  assert.equal(f.transfers, 0);
});

test("only exact internal HTTPS replacement may pass the synchronous private transfer check", async () => {
  const f = await fixture({ transferResult: true }); f.install();
  const old = channel(), next = channel();
  assert.deepEqual(redirect(f, old, next), [Cr.NS_OK]);
  assert.deepEqual(f.transferred, [old, next]);
  assert.deepEqual(old.cancels, []); assert.deepEqual(next.cancels, []);
  assert.equal(old.loadFlags, 0); assert.equal(next.loadFlags, 0);
  f.transferResult = false; denied(f, channel(), channel());
  f.transferResult = Promise.resolve(true); denied(f, channel(), channel());
  f.transferError = true; denied(f, channel(), channel());
});

test("ordinary unrelated redirects including hostless schemes remain unchanged", async () => {
  const f = await fixture({ transferError: true }); f.install();
  for (const url of ["https://elsewhere.invalid/end", "file:///private/file", "data:text/plain,local"]) {
    const old = channel("https://foreign.invalid/start"), next = channel(url);
    assert.deepEqual(redirect(f, old, next), [Cr.NS_OK]);
    assert.equal(old.loadFlags, 0); assert.equal(next.loadFlags, 0);
    assert.deepEqual(old.cancels, []); assert.deepEqual(next.cancels, []);
  }
  assert.equal(f.transfers, 0);
});

test("revoked transfer stays denied after a prior success and shutdown leaves a poisoned module", async () => {
  const f = await fixture({ transferResult: true }); f.install();
  assert.deepEqual(redirect(f, channel(), channel()), [Cr.NS_OK]);
  f.transferResult = false; denied(f, channel(), channel());
  f.shutdown.observe(null, "xpcom-shutdown");
  f.shutdown.observe(null, "xpcom-shutdown"); // cleanup is idempotent
  assert.equal(f.categoryDeletes, 1); assert.equal(f.unregisters, 1); assert.equal(f.observerRemoves, 1);
  f.transferResult = true; denied(f, channel(), channel());
  assert.throws(f.install, error => error.code === "redirect_config");
});

test("cancellation failure still vetoes once and poisons future reserved transfers", async () => {
  for (const options of [{ cancelError: true }, { flagsError: true }]) {
    const f = await fixture({ transferResult: true }); f.install();
    const old = channel("https://foreign.invalid/start", options), next = channel();
    assert.deepEqual(redirect(f, old, next), [Cr.NS_BINDING_ABORTED]);
    assert.equal(old.cancels.length, 1); assert.equal(next.cancels.length, 1);
    denied(f, channel(), channel()); assert.equal(f.transfers, 0);
  }
});

test("callback failure is not retried and does not export an unknown exception", async () => {
  const f = await fixture(); f.install(); let calls = 0;
  assert.throws(() => redirect(f, channel(), channel(), 4, { onRedirectVerifyCallback() {
    calls++; throw new Error("private callback canary");
  } }), error => error === Cr.NS_BINDING_ABORTED);
  assert.equal(calls, 1);
});

test("collisions are not overwritten and partial registration is cleaned without retry", async () => {
  for (const option of ["factoryCollision", "contractCollision", "categoryCollision", "categoryReadError"]) {
    const f = await fixture({ [option]: true });
    assert.throws(f.install, error => ["redirect_config", "redirect_failed"].includes(error.code));
    assert.equal(f.registrations, 0); assert.equal(f.categoryDeletes, 0); assert.equal(f.unregisters, 0);
  }
  for (const option of ["factoryError", "categoryError", "observerError"]) {
    const f = await fixture({ [option]: true });
    assert.throws(f.install, error => error.code === "redirect_failed");
    assert.equal(f.unregisters, 1);
    assert.equal(f.categoryDeletes, option === "factoryError" ? 0 : 1);
    assert.equal(f.observerRemoves, option === "observerError" ? 1 : 0);
    assert.throws(f.install, error => error.code === "redirect_config");
    assert.equal(f.registrations, 1);
  }
  const f = await fixture({ observerError: true, cleanupError: true });
  assert.throws(f.install, error => error.code === "redirect_cleanup");
  assert.equal(f.unregisters, 1);
});
