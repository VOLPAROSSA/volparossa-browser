// SPDX-License-Identifier: GPL-3.0-only
// Inert state-machine tests: no Firefox, network, profile or real clock changes.
import test from "node:test";
import assert from "node:assert/strict";
import { FilterAdmission, FilterAdmissionError } from "../integration/filters/Admission.sys.mjs";

const flush = async () => { for (let i = 0; i < 8; i += 1) await Promise.resolve(); };
const code = expected => error => error instanceof FilterAdmissionError && error.code === expected
  && error.message === expected;
function fixture(overrides = {}) {
  const member = Symbol("parent-verified member");
  const clock = { bootMs: 100, wallMs: 1_000_000 };
  const timers = new Map();
  const calls = [];
  let nextTimer = 0;
  const machine = new FilterAdmission({ membership: member, clock: () => ({ ...clock }),
    reconcile: ({ signal }) => new Promise((resolve, reject) => calls.push({ signal, resolve, reject })),
    schedule: (fn, delay) => { const id = ++nextTimer; timers.set(id, { fn, delay }); return id; },
    cancelTimer: id => timers.delete(id), ...overrides });
  const active = (remaining = 5000, generation = "a".repeat(64)) => ({ mode: "active", generation,
    expiresAtMs: clock.wallMs + remaining });
  const tick = async () => {
    const values = [...timers.values()]; timers.clear();
    for (const value of values) value.fn();
    await flush();
  };
  return { machine, member, clock, timers, calls, active, tick };
}
async function ready(f, value = f.active()) {
  const promise = f.machine.admit(f.member);
  await flush();
  f.calls.at(-1).resolve(value);
  return promise;
}

test("fresh process starts unreconciled, invokes no work until admission", () => {
  const f = fixture();
  assert.deepEqual(f.machine.status, { state: "unreconciled", waiting: 0, outstanding: 0 });
  assert.equal(f.calls.length, 0);
  assert.equal(f.timers.size, 0);
});

test("exact opaque membership is required before clock or reconciliation", async () => {
  const f = fixture({ clock: () => { throw new Error("must not read"); } });
  await assert.rejects(f.machine.admit({ addonId: "uBlock0@raymondhill.net" }), code("admission_identity"));
  assert.equal(f.calls.length, 0);
  assert.equal(f.machine.status.state, "unreconciled");
});

test("simultaneous admissions coalesce, tickets are opaque and single-use", async () => {
  const f = fixture();
  const p = [f.machine.admit(f.member), f.machine.admit(f.member)];
  await flush();
  assert.equal(f.calls.length, 1);
  assert.equal(f.timers.size, 1);
  f.calls[0].resolve(f.active());
  const [a, b] = await Promise.all(p);
  assert.deepEqual(a, {});
  assert.ok(Object.isFrozen(a));
  assert.notEqual(a, b);
  assert.equal(f.machine.validate(a), true);
  assert.throws(() => f.machine.validate(a), code("abort_required"));
  assert.equal(f.machine.validate(b), true);
  assert.equal(f.timers.size, 0);
  assert.deepEqual(f.machine.status, { state: "ready", waiting: 0, outstanding: 0 });
});

test("capacity bounds waiting and outstanding tickets without growing queue", async () => {
  const f = fixture({ maxPending: 2 });
  const a = f.machine.admit(f.member), b = f.machine.admit(f.member);
  await assert.rejects(f.machine.admit(f.member), code("admission_capacity"));
  await flush();
  f.calls[0].resolve({ mode: "absent" });
  const tickets = await Promise.all([a, b]);
  await assert.rejects(f.machine.admit(f.member), code("admission_capacity"));
  f.machine.discard(tickets[0]);
  assert.throws(() => f.machine.validate(tickets[0]), code("abort_required"));
  assert.equal(f.machine.validate(await f.machine.admit(f.member)), true);
  assert.equal(f.machine.validate(tickets[1]), true);
});

test("generation rollover invalidates old tickets without replaying callbacks", async () => {
  const f = fixture();
  const old = await ready(f);
  f.machine.invalidate();
  assert.throws(() => f.machine.validate(old), code("abort_required"));
  const fresh = await ready(f, f.active(5000, "b".repeat(64)));
  assert.equal(f.calls.length, 2);
  assert.equal(f.machine.validate(fresh), true);
});

test("expiry requires reconciliation before a new callback and aborts old result", async () => {
  const f = fixture();
  const old = await ready(f, f.active(100));
  f.clock.bootMs += 100; f.clock.wallMs += 100;
  assert.throws(() => f.machine.validate(old), code("abort_required"));
  const pending = f.machine.admit(f.member);
  await flush();
  assert.equal(f.machine.status.state, "reconciling");
  assert.equal(f.calls.length, 2);
  f.calls[1].resolve({ mode: "absent" });
  assert.equal(f.machine.validate(await pending), true);
});

test("suspend-inclusive clock invalidates even if wall clock did not advance", async () => {
  const f = fixture();
  const ticket = await ready(f, f.active(100));
  f.clock.bootMs += 101;
  assert.throws(() => f.machine.validate(ticket), code("abort_required"));
  assert.equal(f.machine.status.state, "unreconciled");
});

test("wall expiry invalidates even if boot clock has barely advanced", async () => {
  const f = fixture();
  const ticket = await ready(f, f.active(100));
  f.clock.wallMs += 100;
  assert.throws(() => f.machine.validate(ticket), code("abort_required"));
});

test("clock rollback poisons the coordinator, never extending authorization", async () => {
  for (const key of ["bootMs", "wallMs"]) {
    const f = fixture();
    const ticket = await ready(f);
    f.clock[key] -= 1;
    assert.throws(() => f.machine.validate(ticket), code("abort_required"));
    assert.equal(f.machine.status.state, "closed");
    f.clock[key] += 100;
    await assert.rejects(f.machine.admit(f.member), code("admission_closed"));
  }
});

test("clock failures and malformed readings are closed, without raw messages", async () => {
  for (const clock of [() => { throw new Error("private details"); }, () => null,
    () => ({ bootMs: NaN, wallMs: 1 }), () => ({ bootMs: 1, wallMs: -1 }),
    () => ({ bootMs: 1, wallMs: 1, url: "private" }), () => ({ bootMs: true, wallMs: 1 })]) {
    const f = fixture({ clock });
    await assert.rejects(f.machine.admit(f.member), code("clock_invalid"));
    assert.equal(f.calls.length, 0);
    assert.equal(f.machine.status.state, "closed");
  }
});

test("timeout rejects all waiters; ignored abort cannot authorize or overlap", async () => {
  const f = fixture({ timeoutMs: 100 });
  const a = assert.rejects(f.machine.admit(f.member), code("reconcile_timeout"));
  const b = assert.rejects(f.machine.admit(f.member), code("reconcile_timeout"));
  await flush();
  f.clock.bootMs += 100;
  await f.tick();
  await Promise.all([a, b]);
  assert.ok(f.calls[0].signal.aborted);
  await assert.rejects(f.machine.admit(f.member), code("reconcile_busy"));
  assert.equal(f.calls.length, 1);
  f.calls[0].resolve(f.active());
  await flush();
  assert.equal(f.machine.status.state, "unreconciled");
  assert.equal(f.machine.status.outstanding, 0);
  assert.equal(f.machine.validate(await ready(f, { mode: "absent" })), true);
});

test("late completion checks elapsed bound even when timer never ran after suspend", async () => {
  const f = fixture({ timeoutMs: 100 });
  const rejected = assert.rejects(f.machine.admit(f.member), code("reconcile_timeout"));
  await flush();
  f.clock.bootMs += 101;
  f.calls[0].resolve({ mode: "absent" });
  await rejected;
  assert.equal(f.machine.status.state, "unreconciled");
  assert.equal(f.timers.size, 0);
});

test("new admission detects timed-out reconciliation before delayed timer delivery", async () => {
  for (const key of ["bootMs", "wallMs"]) {
    const f = fixture({ timeoutMs: 100 });
    const rejected = assert.rejects(f.machine.admit(f.member), code("reconcile_timeout"));
    await flush(); f.clock[key] += 100;
    await assert.rejects(f.machine.admit(f.member), code("reconcile_busy"));
    await rejected;
    assert.ok(f.calls[0].signal.aborted);
    assert.equal(f.timers.size, 0);
    f.calls[0].resolve({ mode: "absent" }); await flush();
    assert.equal(f.machine.status.state, "unreconciled");
  }
});

test("clock rollback while waiting aborts flight and rejects all queued requests", async () => {
  const f = fixture();
  const rejected = assert.rejects(f.machine.admit(f.member), code("clock_invalid"));
  await flush(); f.clock.wallMs -= 1;
  await assert.rejects(f.machine.admit(f.member), code("clock_invalid"));
  await rejected;
  assert.ok(f.calls[0].signal.aborted);
  f.calls[0].resolve({ mode: "absent" }); await flush();
  assert.equal(f.machine.status.state, "closed");
});

test("early timer callback re-arms only remaining budget", async () => {
  const f = fixture({ timeoutMs: 100 });
  const pending = f.machine.admit(f.member);
  await flush();
  f.clock.bootMs += 30; f.clock.wallMs += 30;
  await f.tick();
  assert.equal([...f.timers.values()][0].delay, 70);
  f.calls[0].resolve({ mode: "absent" });
  assert.equal(f.machine.validate(await pending), true);
});

test("lease is anchored before await and does not gain reconciliation time", async () => {
  const f = fixture();
  const value = f.active(100);
  const rejected = assert.rejects(f.machine.admit(f.member), code("invalid_generation"));
  await flush();
  f.clock.bootMs += 101; // Wall unchanged: anchoring at response would incorrectly renew.
  f.calls[0].resolve(value);
  await rejected;
  assert.equal(f.machine.status.state, "unreconciled");
});

test("invalid or excessive generation grants never authorize", async () => {
  for (const value of [null, {}, { mode: "absent", generation: "a".repeat(64) },
    { mode: "active", generation: "not-a-digest", expiresAtMs: 1000100 },
    { mode: "active", generation: "a".repeat(64), expiresAtMs: 999999 },
    { mode: "active", generation: "a".repeat(64), expiresAtMs: 5000000 }]) {
    const f = fixture();
    const rejected = assert.rejects(f.machine.admit(f.member), error =>
      code("reconcile_failed")(error) || code("invalid_generation")(error));
    await flush();
    f.calls[0].resolve(value);
    await rejected;
    assert.equal(f.machine.status.outstanding, 0);
  }
});

test("generation requires exactly 64 hex characters and rejects the zero digest", async () => {
  for (const generation of ["a".repeat(64) + "\n", "0".repeat(64)]) {
    const f = fixture();
    const rejected = assert.rejects(f.machine.admit(f.member), code("reconcile_failed"));
    await flush();
    f.calls[0].resolve(f.active(5000, generation));
    await rejected;
    assert.equal(f.machine.status.state, "unreconciled");
    assert.equal(f.machine.status.outstanding, 0);
  }
});

test("supplier rejection is sanitized and no automatic retry occurs", async () => {
  const f = fixture();
  const rejected = assert.rejects(f.machine.admit(f.member), code("reconcile_failed"));
  await flush(); f.calls[0].reject(new Error("private addon or broker payload"));
  await rejected; await flush();
  assert.equal(f.calls.length, 1);
  assert.equal(f.machine.status.state, "unreconciled");
});

test("invalidation during reconcile discards late completion until explicit fresh admission", async () => {
  const f = fixture();
  const rejected = assert.rejects(f.machine.admit(f.member), code("admission_invalidated"));
  await flush(); f.machine.invalidate(); await rejected;
  await assert.rejects(f.machine.admit(f.member), code("reconcile_busy"));
  assert.ok(f.calls[0].signal.aborted);
  f.calls[0].resolve({ mode: "absent" }); await flush();
  assert.equal(f.machine.status.state, "unreconciled");
  assert.equal(f.machine.validate(await ready(f)), true);
});

test("close rejects waiters, clears timers and rejects delayed or forged tickets", async () => {
  const f = fixture();
  const rejected = assert.rejects(f.machine.admit(f.member), code("admission_closed"));
  await flush(); f.machine.close(); await rejected;
  assert.equal(f.timers.size, 0);
  f.calls[0].resolve(f.active()); await flush();
  await assert.rejects(f.machine.admit(f.member), code("admission_closed"));
  assert.throws(() => f.machine.validate(Object.freeze({})), code("abort_required"));
  const other = fixture(); const ticket = await ready(other);
  assert.throws(() => f.machine.validate(ticket), code("abort_required"));
  assert.equal(other.machine.validate(ticket), true);
});

test("constructor rejects unbounded queues, waits and absent authority dependencies", () => {
  for (const config of [{ maxPending: 0 }, { maxPending: 257 }, { maxPending: true },
    { timeoutMs: 0 }, { timeoutMs: 60001 }, { clock: null }, { reconcile: null },
    { schedule: null }, { cancelTimer: null }, { membership: "uBlock0@raymondhill.net" }]) {
    assert.throws(() => fixture(config), code("invalid_config"));
  }
});

test("timer scheduling failure releases waiter without leaking raw exception", async () => {
  const f = fixture({ schedule: () => { throw new Error("private timer data"); } });
  await assert.rejects(f.machine.admit(f.member), code("reconcile_failed"));
  assert.equal(f.calls.length, 0);
  assert.equal(f.machine.status.waiting, 0);
});

test("timer cancellation failure closes coordinator and never authorizes a result", async () => {
  const f = fixture({ cancelTimer: () => { throw new Error("private timer failure"); } });
  const rejected = assert.rejects(f.machine.admit(f.member), code("reconcile_failed"));
  await flush(); f.calls[0].resolve({ mode: "absent" }); await rejected;
  assert.equal(f.machine.status.state, "closed");
  await f.tick();
  assert.equal(f.machine.status.outstanding, 0);
});

test("closing ready state invalidates all issued tickets, not stock settings", async () => {
  const f = fixture();
  const ticket = await ready(f, { mode: "absent" });
  f.machine.close();
  assert.throws(() => f.machine.validate(ticket), code("abort_required"));
  assert.deepEqual(f.machine.status, { state: "closed", waiting: 0, outstanding: 0 });
  assert.equal(f.calls.length, 1);
});
