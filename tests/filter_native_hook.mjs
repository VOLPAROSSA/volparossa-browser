// SPDX-License-Identifier: GPL-3.0-only
// Explicit controlled-method evidence, NOT Firefox/Gecko runtime evidence.
// Only two methods from independently SHA-pinned staged source are evaluated.
// The rest of Firefox source, source.json and moz.build are never executed.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { lstatSync, readFileSync, realpathSync } from "node:fs";
import { dirname, isAbsolute, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const PREFIX = "toolkit/components/extensions/webrequest/";
const REQUEST = PREFIX + "WebRequest.sys.mjs";
const BUILD = PREFIX + "moz.build";
const REVISION = "47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1";
const ORIGINALS = Object.freeze({
  [REQUEST]: "a31dacd540c7335d8f703539de34b4f5eb5531a0e2eccf3bcb748bdcf2136e35",
  [BUILD]: "ae2e67c348a4cea54762a58db281da7b313881ed04bd90cb9215804f08dceaaa",
});
// These reviewed module/build hashes are additional local trust, not hashes
// learned from a caller-created source.json. Update only after source review.
const MODULES = Object.freeze({
  "Admission.sys.mjs": "ea1a170d32d5ca04c1edd1eefc7e896c3faec5b8ab0917373652a914710f37f4",
  "WebRequestAdmission.sys.mjs": "9dffa0205d88a393c944ad4d6969c30b09a066577c19eb439cad2d8e6e6917bf",
});
const PATCHED_BUILD = "921d8561ab389c4b81b2acede86bf4eddbd07b233ea08dfa4f9268ca0f9304cf";
const MAX_BYTES = 2 * 1024 * 1024;
const sha256 = bytes => createHash("sha256").update(bytes).digest("hex");
const json = bytes => JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
const keys = (value, names) => assert.deepEqual(Object.keys(value).sort(), [...names].sort());
const hash = value => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);

function read(path, maximum = MAX_BYTES) {
  assert.equal(realpathSync(path), path);
  const info = lstatSync(path);
  assert.ok(info.isFile() && !info.isSymbolicLink() && info.size > 0 && info.size <= maximum);
  const bytes = readFileSync(path);
  assert.equal(bytes.length, info.size);
  assert.ok(bytes.length <= maximum);
  return bytes;
}

function inputs(argv) {
  assert.equal(argv.length, 4);
  assert.equal(argv[0], "--source"); assert.equal(argv[2], "--sha256");
  const source = argv[1], expected = argv[3];
  assert.ok(isAbsolute(source) && hash(expected));
  const suffix = `${sep}patched${sep}${REQUEST}`;
  assert.ok(source.endsWith(suffix));
  const stage = source.slice(0, -suffix.length);
  assert.ok(stage.startsWith(join(ROOT, "build") + sep));
  assert.equal(realpathSync(stage), stage);
  const pins = json(read(join(ROOT, "patches/firefox-filter-admission.json"), 16384));
  const metadata = json(read(join(stage, "source.json"), 16384));
  const originals = {}, patched = {}, modules = {}, repositoryModules = {};
  for (const name of Object.keys(ORIGINALS)) {
    originals[name] = read(join(stage, "original", name));
    patched[name] = read(join(stage, "patched", name));
  }
  for (const name of Object.keys(MODULES)) {
    modules[name] = read(join(stage, "patched", PREFIX, "volparossa", name), 65536);
    repositoryModules[name] = read(join(ROOT, "integration/filters", name), 65536);
  }
  return { expected, pins, metadata, originals, patched, modules, repositoryModules,
    patch: read(join(stage, "filter-admission.patch")) };
}

function validate(value) {
  const { expected, pins, metadata, originals, patched, modules, repositoryModules, patch } = value;
  assert.ok(hash(expected));
  keys(pins, ["upstream_revision", "upstream_sha256", "kind"]);
  assert.equal(pins.upstream_revision, REVISION);
  assert.equal(pins.kind, "exact-source-overlay-not-a-firefox-build");
  assert.deepEqual(pins.upstream_sha256, ORIGINALS);
  keys(metadata, [...Object.keys(pins), "patch_sha256", "patched_sha256", "module_sha256",
    "native_build_proven", "default_enrollment", "publication_owner_integrated"]);
  for (const key of Object.keys(pins)) assert.deepEqual(metadata[key], pins[key]);
  for (const flag of ["native_build_proven", "default_enrollment", "publication_owner_integrated"]) {
    assert.equal(metadata[flag], false);
  }
  keys(metadata.patched_sha256, Object.keys(ORIGINALS));
  keys(metadata.module_sha256, Object.keys(MODULES));
  for (const [name, digest] of Object.entries(ORIGINALS)) assert.equal(sha256(originals[name]), digest);
  assert.equal(sha256(patched[REQUEST]), expected);
  assert.equal(sha256(patched[BUILD]), PATCHED_BUILD);
  for (const name of Object.keys(ORIGINALS)) assert.equal(sha256(patched[name]), metadata.patched_sha256[name]);
  for (const [name, digest] of Object.entries(MODULES)) {
    assert.equal(sha256(modules[name]), digest);
    assert.equal(sha256(repositoryModules[name]), digest);
    assert.equal(metadata.module_sha256[name], digest);
  }
  assert.equal(sha256(patch), metadata.patch_sha256);
}

function methods(bytes) {
  const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  const begin = "\n  runChannelListener(channel, kind, extraData = null) {\n";
  const middle = "\n  async applyChanges(\n";
  const end = "\n  shouldHookListener(listener, channel, extraData) {\n";
  for (const anchor of [begin, middle, end]) assert.equal(text.split(anchor).length, 2);
  const first = text.indexOf(begin), second = text.indexOf(middle), last = text.indexOf(end);
  assert.ok(first >= 0 && first < second && second < last);
  const run = text.slice(first, second), apply = text.slice(second, last);
  assert.ok(run.endsWith("  },\n") && apply.endsWith("  },\n"));
  assert.ok(run.length < 16384 && apply.length < 16384);
  return { run, apply };
}

const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

async function loadVerifiedModules(value) {
  // Import byte snapshots already checked above, not a second path read that
  // could execute changed/foreign files after verification. Only the exact
  // one relative dependency specifier is relocated; module logic is unchanged.
  const admissionURI = `data:text/javascript;base64,${value.modules["Admission.sys.mjs"].toString("base64")}`;
  const original = value.modules["WebRequestAdmission.sys.mjs"].toString("utf8");
  const dependency = 'from "./Admission.sys.mjs";';
  assert.equal(original.split(dependency).length, 2);
  const relocated = original.replace(dependency, `from ${JSON.stringify(admissionURI)};`);
  const bridgeURI = `data:text/javascript;base64,${Buffer.from(relocated).toString("base64")}`;
  const admission = await import(admissionURI), bridge = await import(bridgeURI);
  return { ...admission, ...bridge };
}

function fixture(fragment, real, { dnr = false, maxPending = 32,
    suspendThrows = false, resumeThrows = false, cancelThrows = false } = {}) {
  const registry = new real.WebRequestAdmissionRegistry();
  const policy = { id: "uBlock0@raymondhill.net", active: true };
  const clock = { bootMs: 100, wallMs: 1_000_000 };
  const membership = Symbol("controlled-parent-membership");
  const calls = [], timers = new Map(), effects = [], operations = [];
  const attempts = { suspend: 0, resume: 0, cancel: 0 };
  let timerID = 0, ordinaryErrors = 0;
  const schedule = (fn, ms) => { const id = ++timerID; timers.set(id, { fn, ms, at: clock.bootMs }); return id; };
  const cancelTimer = id => timers.delete(id);
  const admission = new real.FilterAdmission({ membership, clock: () => ({ ...clock }), schedule, cancelTimer,
    maxPending, reconcile: () => { const pending = deferred(); calls.push(pending); return pending.promise; } });
  const handle = registry.register(policy, { admission, membership, clock: () => ({ ...clock }),
    schedule, cancelTimer, timeoutMs: 1000 });
  const channel = {
    finalURL: "https://fixture.invalid/resource", id: 1, errorString: "", suspended: false,
    canModify: true, loadInfo: {},
    channel: { QueryInterface: () => ({ setProperty: () => effects.push("property") }) },
    matches: () => true,
    registerTraceableChannel: () => effects.push("traceable"),
    suspend() {
      attempts.suspend++;
      if (suspendThrows) throw new Error("fixed-suspend-failure");
      if (!this.suspended) { this.suspended = true; effects.push("suspend"); }
    },
    resume() {
      attempts.resume++;
      if (resumeThrows) throw new Error("fixed-resume-failure");
      if (this.suspended) { this.suspended = false; effects.push("resume"); }
    },
    cancel(code, reason) {
      attempts.cancel++;
      assert.equal(code, 0x80004004); assert.equal(reason, 1);
      if (cancelThrows) throw new Error("fixed-cancel-failure");
      effects.push("cancel");
    },
    redirectTo: () => effects.push("redirect"), upgradeToSecure: () => effects.push("upgrade"),
    getRequestHeader: () => "", getRequestHeaders: () => [], setResponseHeader: () => effects.push("header"),
    errorCheck: () => effects.push("error-check"),
    authPromptCallback: () => effects.push("auth"), authPromptForward: () => effects.push("auth-forward"),
  };
  class Headers {
    toArray() { return []; }
    applyChanges() { effects.push("headers"); }
  }
  const context = vm.createContext({
    lazy: {
      WebRequestAdmission: { start(...args) { const operation = registry.start(...args);
        if (operation) operations.push(operation); return operation; } },
      ExtensionDNR: { beforeWebRequestEvent() {}, handleRequest() {
        if (dnr) { channel.cancel(0x80004004, 1); return true; } return false;
      } },
      WebRequestUpload: { createRequestBody: () => ({}) },
    },
    Services: { profiler: { IsActive: () => false }, io: { newURI: spec => ({ spec }) } },
    Ci: { nsILoadInfo: { BLOCKING_REASON_EXTENSION_WEBREQUEST: 1 }, nsIWritablePropertyBag: {} },
    Cr: { NS_ERROR_ABORT: 0x80004004 },
    Cu: { reportError: () => { ordinaryErrors++; } },
    ChromeUtils: { addProfilerMarker() {} },
    RequestHeaderChanger: Headers, ResponseHeaderChanger: Headers,
    verifyRedirect() {},
    isThenable: value => value && typeof value === "object" && typeof value.then === "function",
  }, { codeGeneration: { strings: false, wasm: false } });
  // This explicit vm.Script contains ONLY the two unique, hash-checked method
  // spans. No imports, unrelated upstream initializers or other source execute.
  const observer = new vm.Script(`"use strict"; ({${fragment.run}${fragment.apply}})`,
    { filename: "verified-controlled-WebRequest-methods" }).runInContext(context, { timeout: 1000 });
  Object.assign(observer, { dnrActive: dnr, FILTER_TYPES: new Set(["onBeforeRequest", "onHeadersReceived"]),
    STATUS_TYPES: new Set(), listeners: {}, getRequestData: () => ({ urgentSend: false, url: channel.finalURL }),
    getBrowserData: () => ({ tabId: -1, windowId: -1 }) });
  for (const kind of ["onBeforeRequest", "onBeforeSendHeaders", "onHeadersReceived", "onAuthRequired", "onSendHeaders"]) {
    observer.listeners[kind] = new Map();
  }
  const listen = (callback, { member = policy, blocking = true, kind = "onBeforeRequest", ...extra } = {}) => {
    observer.listeners[kind].set(callback, { policy: member, addonId: member.id, blocking,
      filter: { tabId: null, windowId: null }, ...extra });
  };
  const run = (kind = "onBeforeRequest") => observer.runChannelListener(channel, kind);
  const reconcile = async (remaining = 100) => {
    await flush(); assert.equal(calls.length, 1);
    calls[0].resolve({ mode: "active", generation: "a".repeat(64), expiresAtMs: clock.wallMs + remaining });
    await flush();
  };
  const clean = () => {
    assert.equal(admission.status.outstanding, 0);
    assert.equal(admission.status.waiting, 0);
    assert.equal(timers.size, 0);
    for (const operation of operations) assert.throws(() => operation.validate(), error => error.code === "abort_required");
  };
  return { registry, policy, clock, admission, handle, calls, timers, effects, attempts, operations, channel,
    listen, run, reconcile, clean, ordinaryErrors: () => ordinaryErrors };
}

let currentCase = "source_binding", binding = null;
try {
  const value = inputs(process.argv.slice(2)); validate(value);
  const fragment = methods(value.patched[REQUEST]);
  binding = { upstream_revision: REVISION, original_sha256: ORIGINALS,
    patched_sha256: value.metadata.patched_sha256, module_sha256: MODULES,
    patch_sha256: value.metadata.patch_sha256,
    method_sha256: { runChannelListener: sha256(fragment.run), applyChanges: sha256(fragment.apply) } };
  // These refusals happen before imports or execution of the controlled source.
  for (const field of ["originals", "patched", "modules", "repositoryModules"]) {
    const name = field.includes("odules") ? "Admission.sys.mjs" : REQUEST;
    const altered = { ...value, [field]: { ...value[field], [name]: Buffer.concat([value[field][name], Buffer.from("\n")]) } };
    assert.throws(() => validate(altered));
  }
  assert.throws(() => validate({ ...value, expected: "0".repeat(64) }));
  assert.throws(() => validate({ ...value, patched: { ...value.patched, [BUILD]: Buffer.from("foreign build") } }));
  assert.throws(() => validate({ ...value, metadata: { ...value.metadata, module_sha256: { ...MODULES, foreign: "0".repeat(64) } } }));
  assert.throws(() => methods(Buffer.concat([value.patched[REQUEST], Buffer.from("\n  async applyChanges(\n")])));
  const real = await loadVerifiedModules(value);
  const checks = ["source_module_tamper_rejected_before_execution"];
  const check = async (name, action) => {
    currentCase = name;
    let timer;
    try {
      await Promise.race([Promise.resolve().then(action), new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error()), 3000);
      })]);
      checks.push(name);
    } finally { clearTimeout(timer); }
  };

  await check("callback_waits_for_admission_once", async () => {
    const f = fixture(fragment, real); let calls = 0;
    f.listen(data => { calls++; assert.equal(data.url, f.channel.finalURL); return { cancel: true }; });
    const finished = f.run(); assert.equal(calls, 0); assert.equal(f.channel.suspended, true);
    await f.reconcile(); await finished;
    assert.equal(calls, 1); assert.equal(f.effects.filter(item => item === "cancel").length, 1);
    assert.ok(!f.effects.includes("error-check")); assert.equal(f.ordinaryErrors(), 0); f.clean();
  });

  await check("null_result_still_validated", async () => {
    for (const stale of [false, true]) {
      const f = fixture(fragment, real); const answer = deferred(); let calls = 0;
      f.listen(() => { calls++; return answer.promise; });
      const finished = f.run(); await f.reconcile();
      if (stale) f.clock.bootMs += 100;
      answer.resolve(null); await finished;
      assert.equal(calls, 1); assert.equal(f.effects.includes("cancel"), stale);
      assert.equal(f.effects.includes("error-check"), !stale); f.clean();
    }
  });

  await check("stale_redirect_headers_and_auth_abort_not_continue", async () => {
    for (const [kind, answer, forbidden, extra] of [
      ["onBeforeRequest", { redirectUrl: "https://fixture.invalid/redirect" }, "redirect", {}],
      ["onHeadersReceived", { responseHeaders: [] }, "headers", { responseHeaders: true }],
      ["onAuthRequired", { authCredentials: { username: "fixture", password: "fixture" } }, "auth", {}],
    ]) {
      const f = fixture(fragment, real); const pending = deferred(); let calls = 0;
      f.listen(() => { calls++; return pending.promise; }, { kind, ...extra });
      const finished = f.run(kind); await f.reconcile(); f.clock.bootMs += 100;
      pending.resolve(answer); await finished;
      assert.equal(calls, 1); assert.ok(f.effects.includes("cancel"));
      assert.ok(!f.effects.includes(forbidden) && !f.effects.includes("error-check") && !f.effects.includes("auth-forward"));
      assert.equal(f.ordinaryErrors(), 0); f.clean();
    }
  });

  await check("expiry_before_callback_microtask_prevents_callback", async () => {
    const f = fixture(fragment, real); let calls = 0;
    const original = f.admission.admit.bind(f.admission);
    f.admission.admit = async member => { const ticket = await original(member); f.clock.bootMs += 100; return ticket; };
    f.listen(() => { calls++; return null; }); const finished = f.run();
    await f.reconcile(); await finished;
    assert.equal(calls, 0); assert.ok(f.effects.includes("cancel")); f.clean();
  });

  await check("invalidation_aborts_ignored_callback_and_observes_late_rejection", async () => {
    const f = fixture(fragment, real); const answer = deferred(); let calls = 0;
    f.listen(() => { calls++; return answer.promise; }); const finished = f.run();
    await f.reconcile(); f.handle.invalidate(); await finished;
    answer.reject(new Error("fixed-public-canary")); await flush();
    assert.equal(calls, 1); assert.equal(f.effects.filter(item => item === "cancel").length, 1);
    assert.equal(f.ordinaryErrors(), 0); f.clean();
  });

  await check("early_other_listener_cancel_or_redirect_discards_all", async () => {
    for (const decision of [{ cancel: true }, { redirectUrl: "https://fixture.invalid/redirect" }]) {
      const f = fixture(fragment, real); let calls = 0;
      f.listen(() => decision, { member: { id: "other-addon", active: true } });
      f.listen(() => { calls++; return null; });
      await f.run(); await f.reconcile();
      assert.equal(calls, 0); assert.equal(f.operations.length, 1); f.clean();
    }
  });

  await check("early_listener_cancel_discards_already_resolved_later_ticket", async () => {
    const f = fixture(fragment, real); const earlier = deferred(); let calls = 0;
    f.listen(() => earlier.promise, { member: { id: "other-addon", active: true } });
    f.listen(() => { calls++; return { redirectUrl: "https://fixture.invalid/unused" }; });
    const finished = f.run(); await f.reconcile();
    assert.equal(calls, 1); assert.equal(f.admission.status.outstanding, 1);
    earlier.resolve({ cancel: true }); await finished;
    assert.ok(!f.effects.includes("redirect")); f.clean();
  });

  await check("dnr_shortcut_discards_all_without_callback", async () => {
    const f = fixture(fragment, real, { dnr: true }); let calls = 0;
    f.listen(() => { calls++; return null; });
    assert.equal(f.run(), undefined); await f.reconcile();
    assert.equal(calls, 0); assert.ok(f.effects.includes("cancel")); f.clean();
  });

  await check("unregistered_and_nonblocking_callbacks_unchanged", async () => {
    for (const guardedIdentity of [false, true]) {
      const f = fixture(fragment, real); let calls = 0;
      f.listen(() => { calls++; return { upgradeToSecure: true }; }, {
        member: guardedIdentity ? f.policy : { id: "other-addon", active: true }, blocking: !guardedIdentity,
      });
      const finished = f.run(); assert.equal(calls, 1); await finished;
      assert.equal(f.calls.length, 0); assert.equal(f.operations.length, 0);
      assert.equal(f.effects.includes("upgrade"), !guardedIdentity); f.clean();
    }
    const f = fixture(fragment, real);
    f.listen(() => Promise.reject(new Error("fixed-public-canary")), { member: { id: "other-addon", active: true } });
    await f.run(); assert.equal(f.ordinaryErrors(), 1); assert.ok(f.effects.includes("error-check")); f.clean();
  });

  await check("capacity_failure_aborts_instead_of_skipping_guard", async () => {
    const f = fixture(fragment, real, { maxPending: 1 }); let first = 0, second = 0;
    f.listen(() => { first++; return null; }); f.listen(() => { second++; return null; });
    const finished = f.run(); await f.reconcile(); await finished;
    assert.equal(first, 1); assert.equal(second, 0); assert.ok(f.effects.includes("cancel")); f.clean();
  });

  await check("never_settling_callback_deadline_aborts_channel", async () => {
    const f = fixture(fragment, real); let calls = 0;
    f.listen(() => { calls++; return new Promise(() => {}); }); const finished = f.run();
    await f.reconcile(5000); f.clock.bootMs += 1000;
    for (const [id, timer] of [...f.timers]) { f.timers.delete(id); timer.fn(); }
    await finished; assert.equal(calls, 1); assert.ok(f.effects.includes("cancel")); f.clean();
  });

  await check("suspend_failure_attempts_cancel_before_any_callback", async () => {
    const f = fixture(fragment, real, { suspendThrows: true }); let calls = 0;
    f.listen(() => { calls++; return { upgradeToSecure: true }; });
    await f.run(); await f.reconcile();
    assert.equal(calls, 0); assert.equal(f.attempts.cancel, 1);
    assert.ok(!f.effects.includes("upgrade") && !f.effects.includes("error-check")); f.clean();
  });

  await check("resume_failure_happens_after_cancel_and_never_applies_stale_result", async () => {
    const f = fixture(fragment, real, { resumeThrows: true }); const pending = deferred();
    f.listen(() => pending.promise); const finished = f.run();
    await f.reconcile(); f.clock.bootMs += 100; pending.resolve({ upgradeToSecure: true });
    await finished;
    assert.equal(f.attempts.cancel, 1); assert.equal(f.attempts.resume, 1);
    assert.ok(f.effects.includes("cancel"));
    assert.ok(!f.effects.includes("upgrade") && !f.effects.includes("error-check")); f.clean();
  });

  await check("cancel_failure_has_no_resume_fallback_or_later_result_effect", async () => {
    const f = fixture(fragment, real, { cancelThrows: true }); const pending = deferred();
    f.listen(() => pending.promise);
    f.listen(() => ({ upgradeToSecure: true }), { member: { id: "later-other-addon", active: true } });
    const finished = f.run(); await f.reconcile(); f.clock.bootMs += 100;
    pending.resolve({ redirectUrl: "https://fixture.invalid/never" }); await finished;
    assert.equal(f.attempts.cancel, 1); assert.equal(f.attempts.resume, 0);
    assert.ok(!f.effects.includes("redirect") && !f.effects.includes("upgrade") && !f.effects.includes("error-check"));
    f.clean();
  });

  // Flush Node's unhandled-rejection reporting after every late-result case.
  await new Promise(resolve => setImmediate(resolve));
  console.log(JSON.stringify({ schema: 1, ok: true, scope: "controlled_verified_method_bodies_only",
    firefox_runtime: false, native_build: false, default_registration: false,
    publication_owner_integrated: false, binding, checks, count: checks.length }));
} catch {
  console.log(JSON.stringify({ schema: 1, ok: false, scope: "controlled_verified_method_bodies_only",
    firefox_runtime: false, code: "controlled_method_check_failed", case: currentCase, binding }));
  process.exitCode = 1;
}
