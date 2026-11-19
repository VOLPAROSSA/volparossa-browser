// SPDX-License-Identifier: GPL-3.0-only
import assert from "node:assert/strict";
import test from "node:test";
import { FilterBrokerSession } from "../integration/filters/Session.sys.mjs";
import { MAX_REQUEST, MAX_RESPONSE, MAX_REQUESTS } from "../integration/filters/Frame.sys.mjs";

const expected = { publisher_key: "a".repeat(64), name: "supplement-v1", manifest_id: "b".repeat(64),
  authority_expires_unix_seconds: 3000 };
function framed(value) {
  const body = new TextEncoder().encode(JSON.stringify(value));
  const frame = new Uint8Array(4 + body.length);
  new DataView(frame.buffer).setUint32(0, body.length); frame.set(body, 4);
  return frame;
}
function fixture(options = {}) {
  let sequence = 0, closes = 0;
  const written = [], timers = new Map();
  const io = { write: frame => written.push(JSON.parse(new TextDecoder().decode(frame.subarray(4)))),
    close: () => closes++ };
  const client = new FilterBrokerSession(expected, io, {
    nowSeconds: () => 1100, sha256: () => "c".repeat(64),
    randomID: () => (++sequence).toString(16).padStart(32, "0"),
    schedule: (fn, milliseconds) => { const key = timers.size + 1; timers.set(key, { fn, milliseconds }); return key; },
    cancelTimer: key => timers.delete(key), ...options,
  });
  const reply = value => client.push(framed({ version: 1, id: written.at(-1).id, ...value }));
  const handshake = async () => {
    const pending = client.negotiate();
    reply({ event: "capabilities", protocol: "volparossa-filter", same_uid_only: true,
      public_content_only: true, fixed_publication: true, browser_activation: false,
      max_request_bytes: MAX_REQUEST, max_response_bytes: MAX_RESPONSE, max_requests: MAX_REQUESTS,
      max_connections: 4, ...expected });
    return pending;
  };
  return { client, io, written, timers, reply, handshake, closes: () => closes };
}

test("handshake precedes fixed operations, with one bounded pending request", async () => {
  const f = fixture();
  assert.throws(() => f.client.fetch(), error => error.code === "handshake_required");
  await f.handshake();
  assert.equal(f.written[0].operation.type, "capabilities");
  assert.throws(() => f.client.negotiate());
  const pending = f.client.status();
  assert.throws(() => f.client.fetch(), error => error.code === "busy");
  f.reply({ event: "status", state: "unavailable", snapshot: null });
  assert.equal((await pending).state, "unavailable");
  assert.equal(f.timers.size, 0);
  f.client.close(); assert.equal(f.closes(), 1);
});

test("uncorrelated or duplicate responses close and never resolve a request", async () => {
  for (const duplicate of [false, true]) {
    const f = fixture(); await f.handshake();
    const pending = f.client.status();
    const response = { version: 1, id: duplicate ? f.written.at(-1).id : "f".repeat(32),
      event: "status", state: "unavailable", snapshot: null };
    f.client.push(framed(response));
    if (duplicate) f.client.push(framed(response));
    await assert.rejects(pending, error => error.code === "invalid_response");
    assert.equal(f.client.closed, true);
    assert.equal(f.closes(), 1);
  }
});

test("deadline closes transport, discards late frames and does not retry", async () => {
  const f = fixture(); await f.handshake();
  const pending = f.client.fetch();
  const timer = [...f.timers.values()][0];
  assert.equal(timer.milliseconds, 60000);
  timer.fn();
  await assert.rejects(pending, error => error.code === "unavailable");
  f.reply({ event: "status", state: "unavailable", snapshot: null });
  assert.equal(f.written.length, 2);
  assert.equal(f.client.closed, true);
  assert.equal(f.timers.size, 0);
});

test("write failure exports no error detail or automatic retry", async () => {
  const f = fixture();
  f.io.write = async () => { throw new Error("private-socket-canary"); };
  await assert.rejects(f.client.negotiate(), error => error.message === "unavailable");
  assert.equal(f.closes(), 1);
});

test("partial EOF, remote error and unknown parser failure remain failures", async () => {
  for (const operation of ["partial", "error", "unknown"]) {
    const f = fixture(); await f.handshake();
    const pending = f.client.status();
    if (operation === "partial") {
      f.client.push(Uint8Array.of(0, 0)); f.client.eof();
    } else f.reply({ event: "error", code: operation === "error" ? "revoked" : "private-canary" });
    await assert.rejects(pending, error => error.code === (operation === "error" ? "revoked" : "invalid_response"));
    assert.equal(f.client.closed, true);
  }
});

test("issued IDs and request count are bounded without reopening transport", async () => {
  const f = fixture(); await f.handshake();
  for (let n = 1; n < MAX_REQUESTS; n++) {
    const pending = f.client.status();
    f.reply({ event: "status", state: "unavailable", snapshot: null }); await pending;
  }
  assert.throws(() => f.client.status(), error => error.code === "unavailable");
  assert.equal(f.written.length, MAX_REQUESTS);
  assert.equal(f.closes(), 1);
  const repeated = fixture({ randomID: () => "1".repeat(32) }); await repeated.handshake();
  assert.throws(() => repeated.client.status());
  assert.equal(repeated.closes(), 1);
});

test("clock errors and foreign capabilities cannot establish readiness", async () => {
  const f = fixture({ nowSeconds: () => { throw new Error("clock-canary"); } });
  await assert.rejects(f.handshake(), error => error.message === "clock_error");
  assert.equal(f.client.capabilities, null);
  const other = fixture(); const pending = other.client.negotiate();
  other.reply({ event: "capabilities", ...expected, manifest_id: "c".repeat(64) });
  await assert.rejects(pending);
  assert.equal(other.client.closed, true);
});

test("delayed timer delivery cannot authorize an answer after its deadline", async () => {
  for (const afterResponse of [false, true]) {
    let now = 1100;
    const f = fixture({ nowSeconds: () => now }); await f.handshake();
    const pending = f.client.status();
    if (!afterResponse) now = 1105;
    f.reply({ event: "status", state: "unavailable", snapshot: null });
    now = 1105;
    await assert.rejects(pending, error => error.code === "unavailable");
    assert.equal(f.closes(), 1);
  }
});

test("clock regression and timer cancellation failure still settle and close", async () => {
  let now = 1100;
  const f = fixture({ nowSeconds: () => now }); await f.handshake();
  const pending = f.client.status(); now--;
  f.reply({ event: "status", state: "unavailable", snapshot: null });
  await assert.rejects(pending, error => error.code === "clock_error");
  assert.equal(f.closes(), 1);
  const broken = fixture({ cancelTimer: () => { throw new Error("private-timer-canary"); } });
  await assert.rejects(broken.handshake(), error => error.message === "invalid_response");
  assert.equal(broken.closes(), 1);
  assert.equal(broken.client.pending, null);
});

test("complete final frame survives graceful EOF but cannot reopen the session", async () => {
  const f = fixture(); await f.handshake();
  const pending = f.client.status();
  f.reply({ event: "status", state: "unavailable", snapshot: null });
  f.client.eof();
  assert.throws(() => f.client.status(), error => error.code === "unavailable");
  assert.equal((await pending).state, "unavailable");
  assert.equal(f.client.closed, true);
  assert.equal(f.closes(), 1);
});
