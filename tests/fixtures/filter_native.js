// SPDX-License-Identifier: GPL-3.0-only
"use strict";

// Parent-only native xpcshell fixture. The driver must isolate networking and
// provide the reviewed resource overlay plus the original xpcshell head.js.
// This deliberately creates a SYNTHETIC policy bearing uBO's ID. It neither
// loads nor authenticates uBO, grants publication authority, or enrolls a user.
// Expiry advances an injected process clock, NOT the OS clock or suspend state.
function nativeHttpObservation(phase, request, result, hits) {
  if (!["baseline", "startup", "expired", "invalidated", "ordinary"].includes(phase)) {
    throw new Error("invalid_http_phase");
  }
  // No response bytes, URLs, headers, exception messages or native status text.
  const httpStatus = Number.isInteger(request.httpStatus)
    && request.httpStatus >= 100 && request.httpStatus <= 599 ? request.httpStatus : null;
  return { phase,
    transport: result?.status === Cr.NS_OK ? "ok"
      : result?.status === Cr.NS_ERROR_ABORT ? "aborted" : "other",
    http_status: httpStatus,
    body: request.fault ? "over_bound_or_read_error" : request.body === "ok" ? "ok"
      : request.body === "" ? "empty" : "other",
    origin_hits: Number.isSafeInteger(hits) && hits >= 0 ? Math.min(hits, 2) : null,
  };
}

add_task(async function filter_native_parent_channels() {
  Assert.equal(Services.appinfo.processType, Ci.nsIXULRuntime.PROCESS_TYPE_DEFAULT,
    "fixture runs only in the native parent process");
  do_get_profile();

  const { setTimeout: nativeSetTimeout, clearTimeout: nativeClearTimeout } =
    ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
  const { NetUtil } = ChromeUtils.importESModule("resource://gre/modules/NetUtil.sys.mjs");
  const { HttpServer } = ChromeUtils.importESModule("resource://testing-common/httpd.sys.mjs");
  const { ExtensionParent } =
    ChromeUtils.importESModule("resource://gre/modules/ExtensionParent.sys.mjs");
  const { WebRequest } = ChromeUtils.importESModule("resource://gre/modules/WebRequest.sys.mjs");
  const { FilterAdmission } =
    ChromeUtils.importESModule("resource://gre/modules/volparossa/Admission.sys.mjs");
  const { WebRequestAdmission } =
    ChromeUtils.importESModule("resource://gre/modules/volparossa/WebRequestAdmission.sys.mjs");

  const timers = new Set(), gates = new Set(), listeners = new Set();
  const owners = [], policies = [], requests = [];
  const server = new HttpServer();
  let serverStarted = false, cleaned = false;
  const checks = {
    http_baseline: false,
    startup_waits: false,
    valid_null_once: false,
    expiry_aborts: false,
    invalidation_aborts: false,
    unregistered_unchanged: false,
    cleanup_complete: false,
  };

  function schedule(callback, milliseconds) {
    const timer = nativeSetTimeout(() => {
      timers.delete(timer);
      callback();
    }, milliseconds);
    timers.add(timer);
    return timer;
  }
  function cancelTimer(timer) {
    nativeClearTimeout(timer);
    timers.delete(timer);
  }
  function deferred(releaseOnCleanup = true) {
    let finish;
    const promise = new Promise(resolve => { finish = resolve; });
    const gate = { promise, resolve(value) { gates.delete(gate); finish(value); } };
    if (releaseOnCleanup) gates.add(gate);
    return gate;
  }
  async function bounded(promise, code, milliseconds = 5000) {
    let timer;
    try {
      return await Promise.race([promise, new Promise((_, reject) => {
        timer = schedule(() => reject(new Error(code)), milliseconds);
      })]);
    } finally {
      cancelTimer(timer);
    }
  }
  const nextTurn = () => new Promise(resolve => Services.tm.dispatchToMainThread(resolve));
  async function settle() {
    // Drain real main-thread work, not a synthesized ChannelWrapper dispatcher.
    await bounded(nextTurn(), "main_thread_deadline");
    await bounded(nextTurn(), "main_thread_deadline");
  }

  async function cleanup() {
    if (cleaned) return;
    let cleanupFailed = false;
    const attempt = action => { try { action(); } catch { cleanupFailed = true; } };
    // Removing listeners does not settle already-running callbacks. Retire all
    // owners, resolve every fixture supplier, and await native channel closure.
    for (const listener of [...listeners]) attempt(() => {
      WebRequest.onBeforeRequest.removeListener(listener);
      listeners.delete(listener);
    });
    for (const owner of owners) {
      attempt(() => owner.handle.close());
      attempt(() => owner.admission.close());
    }
    for (const gate of [...gates]) gate.resolve(null);
    for (const request of requests) {
      if (!request.stopped) attempt(() => request.channel.cancel(Cr.NS_BINDING_ABORTED));
    }
    for (const policy of policies) attempt(() => { policy.active = false; });
    try {
      await bounded(Promise.all(requests.map(request => request.done)), "cleanup_channels_deadline");
    } finally {
      try {
        if (serverStarted) {
          await bounded(server.stop(), "cleanup_server_deadline");
          serverStarted = false;
        }
      } finally {
        for (const timer of [...timers]) cancelTimer(timer);
      }
    }
    await settle();
    Assert.ok(!cleanupFailed, "all fixture resource teardown operations succeeded");
    Assert.ok(requests.every(request => request.stopped), "all native channels stopped");
    Assert.ok(policies.every(policy => !policy.active), "all synthetic policies inactive");
    Assert.ok(owners.every(owner => owner.admission.status.state === "closed"
      && owner.admission.status.waiting === 0 && owner.admission.status.outstanding === 0),
    "all admission waiters and tickets released");
    Assert.equal(listeners.size, 0, "all fixture listeners removed");
    Assert.equal(gates.size, 0, "all fixture supplier promises settled");
    Assert.equal(timers.size, 0, "all fixture timers removed");
    Assert.ok(!serverStarted, "local HTTP listener stopped");
    cleaned = true;
    checks.cleanup_complete = true;
  }
  registerCleanupFunction(cleanup);

  try {
    // Direct WebRequest use needs this initializer; no extensions test head,
    // unsigned-addon loader, content process, or security-pref override is used.
    await bounded(ExtensionParent.apiManager.lazyInit(), "extension_api_deadline");
    // _start binds only literal loopback. The pinned identity initializer still
    // chooses localhost on its first call, and skips the secondary literal host
    // when _start was given 127.0.0.1. Register that exact Host/port explicitly;
    // otherwise HTTP 400 occurs before any path handler, despite NS_OK transport.
    server._start(-1, "127.0.0.1");
    serverStarted = true;
    const port = server.identity.primaryPort;
    server.identity.setPrimary("http", "127.0.0.1", port);
    Assert.equal(server.identity.primaryHost, "127.0.0.1", "literal loopback is the HTTP identity");
    Assert.ok(server.identity.has("http", "127.0.0.1", port), "literal Host/port accepted by server");
    const base = `http://127.0.0.1:${port}`;
    const paths = ["baseline", "startup", "expired", "invalidated", "ordinary", "redirect"];
    const hits = Object.fromEntries(paths.map(path => [path, 0]));
    for (const path of paths) {
      server.registerPathHandler(`/${path}`, (request, response) => {
        hits[path] += 1;
        response.setStatusLine(request.httpVersion, 200, "OK");
        response.setHeader("Content-Type", "text/plain", false);
        response.setHeader("Cache-Control", "no-store", false);
        response.write("ok");
      });
    }

    function policy(id, hostname) {
      const value = new WebExtensionPolicy({
        id, mozExtensionHostname: hostname,
        baseURL: Services.io.newFileURI(do_get_profile()).spec,
        localizeCallback: text => text,
        allowedOrigins: new MatchPatternSet(["http://127.0.0.1/*"]),
        permissions: ["webRequest", "webRequestBlocking"],
      });
      policies.push(value);
      value.active = true;
      return value;
    }
    const guardedPolicy = policy("uBlock0@raymondhill.net",
      "f0cc6b19-1f73-4a0e-a0e4-82fa4ceff001");
    const ordinaryPolicy = policy("ordinary-filter-fixture@invalid",
      "f0cc6b19-1f73-4a0e-a0e4-82fa4ceff002");

    function owner(reconcile) {
      const now = { wallMs: 1_000_000, bootMs: 100 };
      const clock = () => ({ ...now });
      const membership = Symbol("synthetic-parent-only-membership");
      const admission = new FilterAdmission({ membership, clock, reconcile, schedule,
        cancelTimer, maxPending: 4, timeoutMs: 5000 });
      const handle = WebRequestAdmission.register(guardedPolicy,
        { admission, membership, clock, schedule, cancelTimer, timeoutMs: 5000 });
      const value = { admission, handle, now };
      owners.push(value);
      return value;
    }
    function listen(path, extensionPolicy, callback) {
      // Membership is tied to this real native policy object, not the ID string.
      WebRequest.onBeforeRequest.addListener(callback,
        { urls: new MatchPatternSet([`http://127.0.0.1/${path}`]) }, ["blocking"],
        { policy: extensionPolicy, addonId: extensionPolicy.id });
      listeners.add(callback);
      return () => {
        WebRequest.onBeforeRequest.removeListener(callback);
        listeners.delete(callback);
      };
    }
    function open(path) {
      const channel = NetUtil.newChannel({
        uri: `${base}/${path}`,
        loadingPrincipal: Services.scriptSecurityManager.createContentPrincipalFromOrigin(base),
        contentPolicyType: Ci.nsIContentPolicy.TYPE_XMLHTTPREQUEST,
        securityFlags: Ci.nsILoadInfo.SEC_ALLOW_CROSS_ORIGIN_SEC_CONTEXT_IS_NULL,
      }).QueryInterface(Ci.nsIHttpChannel);
      channel.loadFlags |= Ci.nsIRequest.LOAD_BYPASS_CACHE | Ci.nsIRequest.INHIBIT_CACHING;
      // Only native onStopRequest may finish this promise during cleanup.
      const finished = deferred(false);
      const request = { channel, done: finished.promise, stopped: false,
        httpStatus: null, body: "", fault: false };
      requests.push(request);
      try {
        channel.asyncOpen({
          QueryInterface: ChromeUtils.generateQI(["nsIStreamListener"]),
          onStartRequest() {
            try { request.httpStatus = channel.responseStatus; } catch { /* No HTTP response. */ }
          },
          onDataAvailable(_, stream, _offset, count) {
            if (count > 64 || request.body.length + count > 64) {
              request.fault = true;
              channel.cancel(Cr.NS_ERROR_FAILURE);
              return;
            }
            try { request.body += NetUtil.readInputStreamToString(stream, count); }
            catch { request.fault = true; channel.cancel(Cr.NS_ERROR_FAILURE); }
          },
          onStopRequest(_, status) {
            request.stopped = true;
            finished.resolve({ status, reason: channel.loadInfo.requestBlockingReason });
          },
        });
      } catch (error) {
        request.stopped = true;
        finished.resolve(null);
        throw error;
      }
      return request;
    }
    function observeHTTP(phase, request, result, originHits) {
      print("FILTER_NATIVE_HTTP:" + JSON.stringify(nativeHttpObservation(phase, request, result, originHits)));
    }
    function expectAbort(result, request, path) {
      observeHTTP(path, request, result, hits[path]);
      Assert.equal(result.status, Cr.NS_ERROR_ABORT, "guarded native channel aborted");
      Assert.equal(result.reason, Ci.nsILoadInfo.BLOCKING_REASON_EXTENSION_WEBREQUEST,
        "native cancellation records the WebRequest reason");
      Assert.equal(hits[path], 0, "cancelled request never reached the local origin");
      Assert.equal(request.body, "", "cancelled request received no payload");
      Assert.ok(!request.fault, "stream handling did not cause cancellation");
    }

    // Establish the same actual Necko/server path before registering ANY fixture
    // listener or owner. A transport NS_OK alone does not prove HTTP success.
    Assert.equal(listeners.size, 0, "HTTP baseline has no fixture WebRequest listener");
    Assert.equal(owners.length, 0, "HTTP baseline has no admission registration");
    const baseline = open("baseline");
    const baselineResult = await bounded(baseline.done, "http_baseline_deadline");
    observeHTTP("baseline", baseline, baselineResult, hits.baseline);
    Assert.equal(baselineResult.status, Cr.NS_OK, "baseline native transport succeeded");
    Assert.equal(baseline.httpStatus, 200, "baseline HTTP status is exactly 200");
    Assert.equal(hits.baseline, 1, "baseline reaches the owned origin exactly once");
    Assert.equal(baseline.body, "ok", "baseline receives the exact bounded fixture body");
    Assert.ok(!baseline.fault, "baseline stream stayed bounded");
    checks.http_baseline = true;

    // Startup is deliberately unreconciled. A real native request must not
    // invoke the callback or reach HTTP before the parent supplier is released.
    const reconcileEntered = deferred(), publication = deferred();
    let startupCalls = 0, reconciliationCalls = 0;
    const startupOwner = owner(() => {
      reconciliationCalls += 1;
      reconcileEntered.resolve();
      return publication.promise;
    });
    const removeStartup = listen("startup", guardedPolicy, () => { startupCalls += 1; return null; });
    Assert.equal(startupOwner.admission.status.state, "unreconciled", "startup begins closed");
    const startup = open("startup");
    await bounded(reconcileEntered.promise, "startup_admission_deadline");
    await settle();
    Assert.equal(startupCalls, 0, "no callback before reconciliation");
    Assert.equal(hits.startup, 0, "no origin request before reconciliation");
    Assert.ok(!startup.stopped, "real channel remains pending behind admission");
    Assert.equal(startupOwner.admission.status.waiting, 1, "one bounded admission waiter");
    checks.startup_waits = true;
    publication.resolve({ mode: "active", generation: "a".repeat(64), expiresAtMs: 1_001_000 });
    const startupResult = await bounded(startup.done, "valid_request_deadline");
    observeHTTP("startup", startup, startupResult, hits.startup);
    Assert.equal(startupResult.status, Cr.NS_OK, "valid null result permits native request");
    Assert.equal(startup.httpStatus, 200, "guarded valid request has HTTP status 200");
    Assert.equal(startupCalls, 1, "valid callback called once");
    Assert.equal(reconciliationCalls, 1, "single startup reconciliation");
    Assert.equal(hits.startup, 1, "valid request reaches origin once");
    Assert.equal(startup.body, "ok", "real HTTP response delivered");
    Assert.ok(!startup.fault, "valid stream stayed bounded");
    checks.valid_null_once = true;
    removeStartup();
    startupOwner.handle.close();
    startupOwner.admission.close();

    const expiredEntered = deferred(), expiredReturn = deferred();
    let expiredCalls = 0;
    const expiredOwner = owner(() => ({ mode: "active", generation: "b".repeat(64),
      expiresAtMs: 1_001_000 }));
    const removeExpired = listen("expired", guardedPolicy, () => {
      expiredCalls += 1;
      expiredEntered.resolve();
      return expiredReturn.promise;
    });
    const expired = open("expired");
    await bounded(expiredEntered.promise, "expired_callback_deadline");
    Assert.equal(hits.expired, 0, "delayed callback still holds the native request");
    // Only the injected monotonic clock advances: wall time stays unchanged.
    expiredOwner.now.bootMs += 1000;
    expiredReturn.resolve({ redirectUrl: `${base}/redirect` });
    expectAbort(await bounded(expired.done, "expired_abort_deadline"), expired, "expired");
    Assert.equal(hits.redirect, 0, "expired redirect result has no native effect");
    Assert.equal(expiredCalls, 1, "expired callback is never replayed");
    checks.expiry_aborts = true;
    removeExpired();
    expiredOwner.handle.close();
    expiredOwner.admission.close();

    const invalidatedEntered = deferred(), invalidatedReturn = deferred();
    let invalidatedCalls = 0;
    const invalidatedOwner = owner(() => ({ mode: "active", generation: "c".repeat(64),
      expiresAtMs: 1_001_000 }));
    const removeInvalidated = listen("invalidated", guardedPolicy, () => {
      invalidatedCalls += 1;
      invalidatedEntered.resolve();
      return invalidatedReturn.promise;
    });
    const invalidated = open("invalidated");
    await bounded(invalidatedEntered.promise, "invalidation_callback_deadline");
    invalidatedOwner.handle.invalidate();
    // Cancellation must settle the native request WITHOUT waiting for the
    // extension callback. Afterwards, settle the late result and drain it.
    expectAbort(await bounded(invalidated.done, "invalidation_abort_deadline"), invalidated, "invalidated");
    invalidatedReturn.resolve(null);
    await settle();
    Assert.equal(invalidatedCalls, 1, "invalidation never repeats the callback");
    Assert.equal(hits.invalidated, 0, "late null result cannot restart the channel");
    checks.invalidation_aborts = true;
    removeInvalidated();
    invalidatedOwner.handle.close();
    invalidatedOwner.admission.close();

    let ordinaryCalls = 0;
    const removeOrdinary = listen("ordinary", ordinaryPolicy, () => {
      ordinaryCalls += 1;
      return Promise.resolve(null);
    });
    const ordinary = open("ordinary");
    const ordinaryResult = await bounded(ordinary.done, "ordinary_request_deadline");
    observeHTTP("ordinary", ordinary, ordinaryResult, hits.ordinary);
    Assert.equal(ordinaryResult.status, Cr.NS_OK, "unregistered native policy remains unchanged");
    Assert.equal(ordinary.httpStatus, 200, "ordinary request has HTTP status 200");
    Assert.equal(ordinaryCalls, 1, "ordinary callback called once");
    Assert.equal(hits.ordinary, 1, "ordinary request reaches origin once");
    Assert.equal(ordinary.body, "ok", "ordinary native response delivered");
    Assert.ok(!ordinary.fault, "ordinary stream stayed bounded");
    checks.unregistered_unchanged = true;
    removeOrdinary();
  } finally {
    await cleanup();
  }

  Assert.ok(Object.values(checks).every(value => value === true), "all native fixture checks passed");
  // Closed, fixed-schema evidence only. No URLs, paths, browsing data, or errors.
  print("FILTER_NATIVE_RESULT:" + JSON.stringify({
    version: 1,
    kind: "parent-only-native-webrequest",
    synthetic_policy: true,
    process_clock_simulation: true,
    original_ubo: false,
    default_enrollment: false,
    checks,
  }));
});
