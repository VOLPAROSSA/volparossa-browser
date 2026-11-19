// SPDX-License-Identifier: GPL-3.0-only
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { validateExpected, validateResponse, ruleCount, ERROR_CODES } from "../integration/filters/Contract.sys.mjs";
import { MAX_REQUEST, MAX_RESPONSE, MAX_REQUESTS } from "../integration/filters/Frame.sys.mjs";

const id = "1".repeat(32);
const sha256 = bytes => createHash("sha256").update(bytes).digest("hex");
const filters = "[Adblock Plus 2.0]\n||ads.example^\n";
// Parser inputs only, not a signed core fixture or runtime evidence.
const envelope = Buffer.from("inert-canonical-envelope");
const expected = validateExpected({ publisher_key: "a".repeat(64), name: "supplement-v1",
  manifest_id: sha256(envelope), authority_expires_unix_seconds: 3000 });
const snapshot = { generation: expected.manifest_id, publisher_key: expected.publisher_key, name: expected.name,
  revision: 1, sha256: sha256(filters), bytes: filters.length, rules: 1, grammar: "ubo-domain-block-v1",
  verified_at_unix_seconds: 1000, expires_unix_seconds: 2000, authorization_expires_unix_seconds: 2000 };
const response = { version: 1, id, event: "snapshot", snapshot, filters, manifest_hex: envelope.toString("hex") };
const options = { id, operation: "fetch", expected, nowSeconds: () => 1100, sha256 };
const caps = { version: 1, id, event: "capabilities", protocol: "volparossa-filter", same_uid_only: true,
  public_content_only: true, fixed_publication: true, browser_activation: false, max_request_bytes: MAX_REQUEST,
  max_response_bytes: MAX_RESPONSE, max_requests: MAX_REQUESTS, max_connections: 4, ...expected };

test("exact independent authority and fixed capabilities are required", async () => {
  assert.ok(Object.isFrozen(expected));
  assert.ok(Object.isFrozen(await validateResponse(caps, { ...options, operation: "capabilities" })));
  for (const update of [{ publisher_key: "b".repeat(64) }, { manifest_id: "b".repeat(64) }, { name: "other" },
    { browser_activation: true }, { public_content_only: false }, { same_uid_only: false },
    { max_request_bytes: 999999 }, { max_response_bytes: MAX_RESPONSE + 1 }, { extra: true }]) {
    await assert.rejects(validateResponse({ ...caps, ...update }, { ...options, operation: "capabilities" }));
  }
  for (const update of [{ publisher_key: "0".repeat(64) }, { publisher_key: expected.publisher_key + "\n" },
    { name: "private\nname" }, { name: "name\n" }, { name: "é" },
    { authority_expires_unix_seconds: Infinity }, { authority_expires_unix_seconds: 0 }, { hidden: true }]) {
    assert.throws(() => validateExpected({ ...expected, ...update }));
  }
});

test("original bytes and envelope digest bind a snapshot", async () => {
  const result = await validateResponse(response, options);
  assert.equal(result.filters, filters);
  assert.deepEqual(result.snapshot, snapshot);
  assert.ok(Object.isFrozen(result) && Object.isFrozen(result.snapshot));
  for (const update of [{ filters: filters + "\n" }, { manifest_hex: "aa" }, { manifest_hex: "a" },
    { manifest_hex: "AA" }, { manifest_hex: "aaa\n" }, { manifest_hex: "aa".repeat(65537) }, { filters: "||different.example^\n" }]) {
    await assert.rejects(validateResponse({ ...response, ...update }, options));
  }
});

test("foreign metadata, unsafe numbers and changed expiry are rejected", async () => {
  for (const update of [{ generation: "b".repeat(64) }, { publisher_key: "b".repeat(64) }, { name: "other" },
    { revision: Number.MAX_SAFE_INTEGER + 1 }, { revision: -1 }, { bytes: 0 }, { rules: 4097 },
    { grammar: "ubo-anything" }, { authorization_expires_unix_seconds: 3001 },
    { verified_at_unix_seconds: 2000 }, { expires_unix_seconds: 1999 }, { extra: true }]) {
    await assert.rejects(validateResponse({ ...response, snapshot: { ...snapshot, ...update } }, options));
  }
});

test("expiry is checked again after asynchronous integrity verification", async () => {
  let now = 1999;
  await assert.rejects(validateResponse(response, { ...options, nowSeconds: () => now,
    sha256: async bytes => { now = 2000; return sha256(bytes); } }), error => error.code === "expired");
  await assert.rejects(validateResponse(response, { ...options, nowSeconds: () => 3000 }));
  await assert.rejects(validateResponse(response, { ...options, nowSeconds: () => 999 }));
});

test("status cannot pretend missing or expired content is ready", async () => {
  const ready = { version: 1, id, event: "status", state: "ready", snapshot };
  assert.equal((await validateResponse(ready, { ...options, operation: "status" })).state, "ready");
  assert.equal((await validateResponse({ ...ready, state: "unavailable", snapshot: null },
    { ...options, operation: "status" })).state, "unavailable");
  for (const update of [{ state: "unavailable" }, { snapshot: null }, { state: "maybe" }, { extra: true }]) {
    await assert.rejects(validateResponse({ ...ready, ...update }, { ...options, operation: "status" }));
  }
});

test("only correlated closed events and errors are accepted", async () => {
  for (const update of [{ id: "2".repeat(32) }, { id: null }, { version: 2 }, { event: "status" }, { extra: "secret" }]) {
    await assert.rejects(validateResponse({ ...response, ...update }, options));
  }
  for (const code of ERROR_CODES) {
    for (const value of [id, null]) {
      await assert.rejects(validateResponse({ version: 1, id: value, event: "error", code }, options),
        error => error.code === code);
    }
  }
  await assert.rejects(validateResponse({ version: 1, id, event: "error", code: "private-canary" }, options),
    error => error.message === "invalid_response");
});

test("only the core domain-block grammar passes without transformations", () => {
  assert.equal(ruleCount(filters), 1);
  assert.equal(ruleCount("\r\n||a.example^\r\n\n||b.example^\n"), 2);
  for (const bad of ["", "[Adblock Plus 2.0]\n", "!comment\n||a.example^", "||A.example^", "||a.123^",
    "||127.0.0.1^", "@@||a.example^", "||a.example^$important", "a.example##.ad", "||-a.example^",
    "||a-.example^", "||a..example^", "||a.example^\0", "||a.example^\r\r", "||é.example^",
    "\n[Adblock Plus 2.0]\n||a.example^", "||" + "a".repeat(64) + ".example^",
    "||a.example^\n".repeat(4097), "\n".repeat(8192) + "||a.example^", "x".repeat(1024 * 1024 + 1)]) {
    assert.throws(() => ruleCount(bad));
  }
});
