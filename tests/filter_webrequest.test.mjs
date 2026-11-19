// SPDX-License-Identifier: GPL-3.0-only
// Inert parent bridge tests: no browser, network, addon mutation or real timers.
import test from "node:test";
import assert from "node:assert/strict";
import { FilterAdmission, FilterAdmissionError } from "../integration/filters/Admission.sys.mjs";
import { WebRequestAdmission, WebRequestAdmissionRegistry } from "../integration/filters/WebRequestAdmission.sys.mjs";

const flush = async () => { for (let i = 0; i < 16; i += 1) await Promise.resolve(); };
const abort = error => error instanceof FilterAdmissionError && error.code === "abort_required"
  && error.message === "abort_required" && !error.message.includes("private-canary");
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
function fixture({ maxPending = 32, timeoutMs = 1000 } = {}) {
  const registry = new WebRequestAdmissionRegistry();
  const policy = { id: "uBlock0@raymondhill.net", active: true };
  const membership = Symbol("parent-owned membership");
  const now = { bootMs: 100, wallMs: 1_000_000 };
  const timers = new Map(), calls = [];
  let next = 0;
  const clock = () => ({ ...now });
  const schedule = (fn, ms) => {
    const id = ++next; timers.set(id, { fn, ms, at: now.bootMs }); return id;
  };
  const cancelTimer = id => timers.delete(id);
  const admission = new FilterAdmission({ membership, clock, schedule, cancelTimer, maxPending,
    reconcile: () => { const value = deferred(); calls.push(value); return value.promise; } });
  const options = { admission, membership, clock, schedule, cancelTimer, timeoutMs };
  const handle = registry.register(policy, options);
  const active = (ms = 500) => ({ mode: "active", generation: "a".repeat(64), expiresAtMs: now.wallMs + ms });
  const reconcile = async (value = active()) => {
    await flush(); assert.ok(calls.length); calls.at(-1).resolve(value); await flush();
  };
  const tick = async () => {
    for (const [id, timer] of [...timers]) {
      if (now.bootMs - timer.at < timer.ms) continue;
      timers.delete(id); timer.fn();
    }
    await flush();
  };
  return { registry, policy, membership, now, timers, calls, admission, options, handle, active, reconcile, tick };
}

test("import installs no registration; unregistered policies return null without invoking", () => {
  let invoked = 0;
  for (const policy of [null, undefined, {}, { id: "uBlock0@raymondhill.net", active: true }]) {
    assert.equal(WebRequestAdmission.start(policy, () => invoked++, {}), null);
  }
  assert.equal(invoked, 0);
});

test("registration requires expected identity, real admission and explicit bounded timing capabilities", () => {
  const f = fixture();
  for (const [policy, options] of [
    [{ id: "other", active: true }, f.options], [{ ...f.policy, active: false }, f.options],
    [f.policy, { ...f.options, admission: {} }], [f.policy, { ...f.options, membership: "id" }],
    [f.policy, { ...f.options, clock: null }], [f.policy, { ...f.options, schedule: null }],
    [f.policy, { ...f.options, cancelTimer: null }], [f.policy, { ...f.options, timeoutMs: 0 }],
    [f.policy, { ...f.options, timeoutMs: 60001 }],
  ]) assert.throws(() => f.registry.register(policy, options), error => error.code === "invalid_config");
  f.handle.close();
});

test("admission precedes exactly one callback, preserves output identity, and final validation is single-use", async () => {
  for (const value of [null, undefined, false, { cancel: true }, { responseHeaders: [] }]) {
    const f = fixture(); let invoked = 0;
    const data = { sentinel: "not exported" };
    const operation = f.registry.start(f.policy, received => { invoked++; assert.equal(received, data); return value; }, data);
    assert.ok(Object.isFrozen(operation));
    assert.deepEqual(Object.keys(operation).sort(), ["discard", "result", "validate"]);
    assert.equal(invoked, 0);
    await f.reconcile();
    assert.equal(await operation.result, value);
    assert.equal(invoked, 1);
    assert.equal(operation.validate(), true);
    assert.throws(() => operation.validate(), abort);
    operation.discard();
    assert.equal(f.admission.status.outstanding, 0);
    assert.equal(f.timers.size, 0);
  }
});

test("exact policy object is required, same-ID lookalike remains unregistered", async () => {
  const f = fixture();
  assert.equal(f.registry.start({ ...f.policy }, () => { throw new Error(); }, null), null);
  const operation = f.registry.start(f.policy, () => 1, null);
  await f.reconcile(); assert.equal(await operation.result, 1); operation.discard();
});

test("registered invalid callback fails as an operation, never synchronous upstream-swallowed throw", async () => {
  const f = fixture();
  const operation = f.registry.start(f.policy, null, null);
  await assert.rejects(operation.result, abort);
  assert.throws(() => operation.validate(), abort);
  assert.equal(f.calls.length, 0);
});

test("discard before reconciliation prevents callback and discards the eventual ticket", async () => {
  const f = fixture(); let invoked = 0;
  const operation = f.registry.start(f.policy, () => invoked++, null);
  operation.discard(); operation.discard();
  await assert.rejects(operation.result, abort);
  assert.equal(f.admission.status.waiting, 1); // Existing admission has no per-waiter cancellation.
  await f.reconcile();
  assert.equal(invoked, 0);
  assert.equal(f.admission.status.waiting, 0);
  assert.equal(f.admission.status.outstanding, 0);
  assert.equal(f.timers.size, 0);
});

test("expiry or invalidation after admit resolves but before callback delivery invokes no callback", async () => {
  for (const mutate of [f => { f.now.bootMs += 50; }, f => { f.now.wallMs += 50; },
    f => f.admission.invalidate()]) {
    const f = fixture(); let invoked = 0;
    const original = f.admission.admit.bind(f.admission);
    f.admission.admit = async membership => {
      const ticket = await original(membership);
      mutate(f);
      return ticket;
    };
    const operation = f.registry.start(f.policy, () => invoked++, null);
    await f.reconcile(f.active(50));
    await assert.rejects(operation.result, abort);
    assert.equal(invoked, 0);
    assert.equal(f.admission.status.outstanding, 0);
    assert.equal(f.timers.size, 0);
  }
});

test("discard after callback rejects promptly even if callback never settles", async () => {
  const f = fixture(); let invoked = 0;
  const operation = f.registry.start(f.policy, () => { invoked++; return new Promise(() => {}); }, null);
  await f.reconcile(); operation.discard();
  await assert.rejects(operation.result, abort);
  assert.equal(invoked, 1);
  assert.equal(f.admission.status.outstanding, 0);
  assert.equal(f.timers.size, 0);
});

test("closing and replacing registrations cancel pending callbacks and keep weak tombstones", async () => {
  for (const action of ["close", "same-policy", "new-policy"]) {
    const f = fixture(); let invoked = 0;
    const operation = f.registry.start(f.policy, () => invoked++, null);
    if (action === "close") f.handle.close();
    else f.registry.register(action === "same-policy" ? f.policy : { ...f.policy }, f.options);
    await assert.rejects(operation.result, abort);
    assert.equal(invoked, 0);
    assert.throws(() => operation.validate(), abort);
    if (action !== "same-policy") {
      const late = f.registry.start(f.policy, () => invoked++, null);
      assert.notEqual(late, null); await assert.rejects(late.result, abort);
    }
  }
});

test("registration invalidation promptly rejects in-flight callback and permits fresh reconciliation", async () => {
  const f = fixture(); const callback = deferred(); let invoked = 0;
  const operation = f.registry.start(f.policy, () => { invoked++; return callback.promise; }, null);
  await f.reconcile(); f.handle.invalidate();
  await assert.rejects(operation.result, abort);
  callback.resolve({ cancel: true }); await flush();
  assert.throws(() => operation.validate(), abort);
  const fresh = f.registry.start(f.policy, () => { invoked++; return null; }, null);
  await f.reconcile(); assert.equal(await fresh.result, null); assert.equal(fresh.validate(), true);
  assert.equal(invoked, 2);
});

test("replacement after completed result prevents stale combined decision", async () => {
  const f = fixture();
  const operation = f.registry.start(f.policy, () => ({ cancel: true }), null);
  await f.reconcile(); await operation.result;
  f.registry.register({ ...f.policy }, f.options);
  assert.throws(() => operation.validate(), abort);
  assert.equal(f.admission.status.outstanding, 0);
});

test("policy deactivation or identity mutation refuses final effects", async () => {
  for (const change of [policy => { policy.active = false; }, policy => { policy.id = "other"; }]) {
    const f = fixture();
    const operation = f.registry.start(f.policy, () => ({ redirectUrl: "private-canary" }), null);
    await f.reconcile(); await operation.result; change(f.policy);
    assert.throws(() => operation.validate(), abort);
    assert.equal(f.admission.status.outstanding, 0);
  }
});

test("expiry during delayed callback rejects final ticket without callback replay", async () => {
  const f = fixture(); const callback = deferred(); let invoked = 0;
  const operation = f.registry.start(f.policy, () => { invoked++; return callback.promise; }, null);
  await f.reconcile(f.active(50)); f.now.bootMs += 50;
  callback.resolve({ cancel: true }); await operation.result;
  assert.throws(() => operation.validate(), abort);
  assert.equal(invoked, 1);
  assert.equal(f.admission.status.outstanding, 0);
});

test("callback that never settles is bounded by owner timer and suspend-inclusive deadline", async () => {
  const f = fixture({ timeoutMs: 100 });
  const operation = f.registry.start(f.policy, () => new Promise(() => {}), null);
  await f.reconcile(); f.now.bootMs += 100; await f.tick();
  await assert.rejects(operation.result, abort);
  assert.equal(f.admission.status.outstanding, 0);
  assert.equal(f.timers.size, 0);
});

test("queued completed result cannot outlive its deadline even when timer has not fired", async () => {
  for (const key of ["bootMs", "wallMs"]) {
    const f = fixture({ timeoutMs: 100 });
    const operation = f.registry.start(f.policy, () => null, null);
    await f.reconcile(); await operation.result; f.now[key] += 100;
    assert.throws(() => operation.validate(), abort);
    assert.equal(f.timers.size, 0);
  }
});

test("clock regression and clock exceptions never expose raw data", async () => {
  for (const key of ["bootMs", "wallMs"]) {
    const f = fixture();
    const operation = f.registry.start(f.policy, () => null, null);
    await f.reconcile(); await operation.result; f.now[key]--;
    assert.throws(() => operation.validate(), abort);
  }
  const f = fixture(); f.handle.close();
  f.registry.register(f.policy, { ...f.options, clock: () => { throw new Error("private-canary"); } });
  await assert.rejects(f.registry.start(f.policy, () => null, null).result, abort);
});

test("capacity failure remains fail-closed while admitted work keeps its one callback", async () => {
  const f = fixture({ maxPending: 1 }); let invoked = 0;
  const first = f.registry.start(f.policy, () => invoked++, null);
  const excess = f.registry.start(f.policy, () => invoked++, null);
  await assert.rejects(excess.result, abort);
  await f.reconcile(); await first.result; first.validate();
  assert.equal(invoked, 1);
});

test("callback throws and rejects are sanitized and tickets released", async () => {
  for (const callback of [() => { throw new Error("private-canary"); },
    () => Promise.reject({ url: "private-canary" })]) {
    const f = fixture(); const operation = f.registry.start(f.policy, callback, null);
    await f.reconcile(); await assert.rejects(operation.result, abort);
    assert.equal(f.admission.status.outstanding, 0);
    assert.equal(f.timers.size, 0);
  }
});

test("early listener cancellation discards ALL operations without unhandled late rejection", async () => {
  const unhandled = []; const listener = error => unhandled.push(error);
  process.on("unhandledRejection", listener);
  try {
    const f = fixture(); const late = deferred();
    const operations = [f.registry.start(f.policy, () => ({ cancel: true }), null),
      f.registry.start(f.policy, () => late.promise, null), f.registry.start(f.policy, null, null)];
    await f.reconcile();
    try { await operations[0].result; operations[0].validate(); }
    finally { for (const operation of operations) operation.discard(); }
    late.reject(new Error("private-canary"));
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(unhandled, []);
    assert.equal(f.admission.status.outstanding, 0);
    assert.equal(f.timers.size, 0);
  } finally { process.removeListener("unhandledRejection", listener); }
});

test("malformed synchronous scheduler fails closed without invoking callback", async () => {
  const f = fixture(); f.handle.close(); let invoked = 0;
  f.registry.register(f.policy, { ...f.options, schedule: fn => { fn(); return 99; } });
  const operation = f.registry.start(f.policy, () => invoked++, null);
  await assert.rejects(operation.result, abort);
  assert.equal(invoked, 0);
  assert.equal(f.admission.status.outstanding, 0);
});

test("early timer delivery rearms remaining budget rather than extending it", async () => {
  const f = fixture({ timeoutMs: 100 });
  const operation = f.registry.start(f.policy, () => new Promise(() => {}), null);
  await f.reconcile();
  const [id, timer] = [...f.timers][0]; f.timers.delete(id);
  f.now.bootMs += 40; timer.fn();
  assert.equal([...f.timers.values()][0].ms, 60);
  f.now.bootMs += 60; await f.tick();
  await assert.rejects(operation.result, abort);
  assert.equal(f.timers.size, 0);
});

test("timer failures remain closed and final cancellation failure cannot authorize effects", async () => {
  const f = fixture(); f.handle.close();
  f.registry.register(f.policy, { ...f.options, schedule: () => { throw new Error("private-canary"); } });
  await assert.rejects(f.registry.start(f.policy, () => null, null).result, abort);
  assert.equal(f.calls.length, 0);
  const g = fixture(); g.handle.close();
  g.registry.register(g.policy, { ...g.options, cancelTimer: id => {
    g.options.cancelTimer(id); throw new Error("private-canary");
  } });
  const operation = g.registry.start(g.policy, () => null, null);
  await g.reconcile(); await operation.result;
  assert.throws(() => operation.validate(), abort);
  assert.equal(g.admission.status.outstanding, 0);
  assert.equal(g.timers.size, 0);
});

test("premature validation refuses and cancels work instead of manufacturing a result", async () => {
  const f = fixture(); let invoked = 0;
  const operation = f.registry.start(f.policy, () => invoked++, null);
  assert.throws(() => operation.validate(), abort);
  await assert.rejects(operation.result, abort);
  await f.reconcile();
  assert.equal(invoked, 0);
  assert.equal(f.admission.status.outstanding, 0);
});

test("bridge hard cap bounds operations independently of admission scheduling", async () => {
  const f = fixture({ maxPending: 256 }); let invoked = 0;
  const operations = Array.from({ length: 256 }, () => f.registry.start(f.policy, () => invoked++, null));
  const excess = f.registry.start(f.policy, () => invoked++, null);
  await assert.rejects(excess.result, abort);
  assert.equal(f.admission.status.waiting, 256);
  for (const operation of operations) operation.discard();
  await f.reconcile();
  assert.equal(invoked, 0);
  assert.equal(f.admission.status.outstanding, 0);
  assert.equal(f.admission.status.waiting, 0);
  assert.equal(f.timers.size, 0);
});

test("old closed handle cannot invalidate a replacement registration", async () => {
  const f = fixture(); const next = f.registry.register(f.policy, f.options);
  f.handle.close(); f.handle.invalidate();
  const operation = f.registry.start(f.policy, () => null, null);
  await f.reconcile(); await operation.result; assert.equal(operation.validate(), true);
  next.close();
});
