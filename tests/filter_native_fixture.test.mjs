// SPDX-License-Identifier: GPL-3.0-only
// Inert checks of owned fixture diagnostics and control ordering, not Gecko.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("./fixtures/filter_native.js", import.meta.url), "utf8");
let tasks = 0;
const context = vm.createContext({
  Cr: Object.freeze({ NS_OK: 0, NS_ERROR_ABORT: 0x80004004 }),
  add_task: callback => { assert.equal(typeof callback, "function"); tasks++; },
}, { codeGeneration: { strings: false, wasm: false } });
// Only register the task; never invoke its async body or provide Gecko APIs.
vm.runInContext(source, context, { timeout: 1000, filename: "owned-filter-fixture" });
const observe = (...args) => JSON.parse(JSON.stringify(context.nativeHttpObservation(...args)));

test("owned fixture registers one unexecuted native task", () => {
  assert.equal(tasks, 1);
  assert.equal(context.Services, undefined);
  assert.equal(context.ChromeUtils, undefined);
});

test("HTTP errors cannot masquerade as successful transport or export response bytes", () => {
  const observation = observe("baseline", { httpStatus: 400, body: "PRIVATE_BODY_CANARY", fault: false },
    { status: 0 }, 0);
  assert.deepEqual(observation, { phase: "baseline", transport: "ok", http_status: 400,
    body: "other", origin_hits: 0 });
  assert.equal(JSON.stringify(observation).includes("PRIVATE_BODY_CANARY"), false);
  assert.deepEqual(observe("startup", { httpStatus: 200, body: "ok", fault: false }, { status: 0 }, 1),
    { phase: "startup", transport: "ok", http_status: 200, body: "ok", origin_hits: 1 });
});

test("diagnostics contain only closed phases categories and bounded numbers", () => {
  assert.deepEqual(observe("expired", { httpStatus: 1000, body: "raw", fault: true },
    { status: 0x80004004 }, 9000), { phase: "expired", transport: "aborted", http_status: null,
    body: "over_bound_or_read_error", origin_hits: 2 });
  assert.deepEqual(observe("ordinary", { httpStatus: "secret", body: "", fault: false }, null, NaN),
    { phase: "ordinary", transport: "other", http_status: null, body: "empty", origin_hits: null });
  assert.throws(() => observe("PRIVATE_PHASE_CANARY", {}, null, 0), /invalid_http_phase/);
});

test("literal socket binding has an explicit matching HTTP identity before requests", () => {
  const bind = source.indexOf('server._start(-1, "127.0.0.1")');
  const identity = source.indexOf('server.identity.setPrimary("http", "127.0.0.1", port)');
  const accepted = source.indexOf('server.identity.has("http", "127.0.0.1", port)');
  const baseline = source.indexOf('const baseline = open("baseline")');
  assert.ok(bind >= 0 && bind < identity && identity < accepted && accepted < baseline);
  assert.equal(source.match(/server\._start\(/g).length, 1);
  assert.equal(source.match(/server\.identity\.setPrimary\(/g).length, 1);
  assert.equal(source.includes('"0.0.0.0"'), false);
});

test("unfiltered baseline precedes owner and listener registration and requires real HTTP success", () => {
  const baseline = source.indexOf('const baseline = open("baseline")');
  const verified = source.indexOf('checks.http_baseline = true');
  assert.ok(baseline > 0 && verified > baseline);
  assert.ok(verified < source.indexOf('const startupOwner = owner('));
  assert.ok(verified < source.indexOf('const removeStartup = listen('));
  for (const check of ['Assert.equal(listeners.size, 0', 'Assert.equal(owners.length, 0',
    'Assert.equal(baselineResult.status, Cr.NS_OK', 'Assert.equal(baseline.httpStatus, 200',
    'Assert.equal(hits.baseline, 1', 'Assert.equal(baseline.body, "ok"', 'Assert.ok(!baseline.fault']) {
    assert.ok(source.includes(check), check);
  }
});

test("HTTP status observations precede success assertions without weakening guarded cancellation", () => {
  for (const phase of ["startup", "ordinary"]) {
    assert.ok(source.indexOf(`observeHTTP("${phase}",`) < source.indexOf(`Assert.equal(${phase}.httpStatus, 200`));
  }
  assert.ok(source.includes('Assert.equal(result.status, Cr.NS_ERROR_ABORT'));
  assert.ok(source.includes('Assert.equal(hits[path], 0'));
  assert.ok(source.includes('Assert.equal(hits.redirect, 0'));
  assert.ok(source.includes('if (count > 64 || request.body.length + count > 64)'));
  assert.ok(source.includes('await cleanup();'));
  assert.equal(source.includes('Services.prefs.set'), false);
});
