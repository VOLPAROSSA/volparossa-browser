// SPDX-License-Identifier: GPL-3.0-only
import assert from "node:assert/strict";
import test from "node:test";
import { FrameDecoder, FilterBrokerError, MAX_REQUEST, MAX_RESPONSE, requestFrame }
  from "../integration/filters/Frame.sys.mjs";

const id = "a".repeat(32);
const body = { version: 1, id, event: "status" };
function frame(text) {
  const bytes = new TextEncoder().encode(text);
  const value = new Uint8Array(bytes.length + 4);
  new DataView(value.buffer).setUint32(0, bytes.length);
  value.set(bytes, 4);
  return value;
}
function rejected(text) {
  let calls = 0;
  const input = new FrameDecoder(() => calls++);
  assert.throws(() => input.push(frame(text)), error =>
    error instanceof FilterBrokerError && error.message === "invalid_response");
  assert.equal(calls, 0);
  assert.equal(input.closed, true);
  assert.equal(input.body, null);
}

test("only fixed operations and correlated IDs enter request frames", () => {
  for (const operation of ["capabilities", "status", "fetch"]) {
    const value = requestFrame(id, operation);
    const length = new DataView(value.buffer).getUint32(0);
    assert.equal(length, value.length - 4);
    assert.ok(length <= MAX_REQUEST);
    assert.deepEqual(JSON.parse(new TextDecoder().decode(value.subarray(4))),
      { version: 1, id, operation: { type: operation } });
  }
  for (const operation of ["publish", "http://private.invalid", {}, { type: "fetch" }, null]) {
    assert.throws(() => requestFrame(id, operation), FilterBrokerError);
  }
  for (const bad of [null, "", "a".repeat(31), "A".repeat(32), "g".repeat(32), id + "\n"]) {
    assert.throws(() => requestFrame(bad, "fetch"), FilterBrokerError);
  }
});

test("all single split positions and byte-wise delivery preserve one frame", () => {
  const value = frame(JSON.stringify(body));
  for (let split = 0; split <= value.length; split++) {
    const seen = [];
    const input = new FrameDecoder(value => seen.push(value));
    input.push(value.subarray(0, split));
    input.push(value.subarray(split));
    input.finish();
    assert.deepEqual(seen, [body]);
  }
  const seen = [];
  const input = new FrameDecoder(value => seen.push(value));
  for (const byte of value) input.push(Uint8Array.of(byte));
  input.finish();
  assert.deepEqual(seen, [body]);
});

test("concatenated responses preserve order without leftover state", () => {
  const one = frame(JSON.stringify(body));
  const two = frame(JSON.stringify({ ...body, id: "b".repeat(32) }));
  const bytes = new Uint8Array(one.length + two.length);
  bytes.set(one); bytes.set(two, one.length);
  const seen = [];
  const input = new FrameDecoder(value => seen.push(value.id));
  input.push(bytes); input.finish();
  assert.deepEqual(seen, [id, "b".repeat(32)]);
});

test("empty and oversized declared frames fail before allocation", () => {
  for (const size of [0, MAX_RESPONSE + 1, 0xffffffff]) {
    const header = new Uint8Array(4);
    new DataView(header.buffer).setUint32(0, size);
    const input = new FrameDecoder(() => assert.fail("unexpected callback"));
    assert.throws(() => input.push(header), FilterBrokerError);
    assert.equal(input.body, null);
    assert.equal(input.closed, true);
  }
});

test("every nonempty truncation fails at EOF", () => {
  const value = frame(JSON.stringify(body));
  for (let length = 1; length < value.length; length++) {
    const input = new FrameDecoder(() => assert.fail("unexpected callback"));
    input.push(value.subarray(0, length));
    assert.throws(() => input.finish(), FilterBrokerError);
    assert.equal(input.closed, true);
    assert.equal(input.body, null);
  }
});

test("duplicate or noncanonical JSON is rejected, including nested keys", () => {
  for (const text of ['{"id":1,"id":2}', '{"x":{"a":1,"a":1}}', '{ "x":1}',
    '{"x":-0}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e0}', '{"x":9007199254740993}',
    '{"x":"\\u0061"}', 'null', '[]', 'true', '"private-data-canary"', '{"bad":']) rejected(text);
});

test("invalid UTF-8 fails closed without exporting peer bytes", () => {
  const value = frame('{"x":"a"}');
  value[value.length - 3] = 0xff;
  const input = new FrameDecoder(() => assert.fail("unexpected callback"));
  assert.throws(() => input.push(value), error => error.message === "invalid_response");
  assert.equal(input.closed, true);
});

test("callback exceptions are closed errors and prohibit more frames", () => {
  const input = new FrameDecoder(() => { throw new Error("private-context-canary"); });
  assert.throws(() => input.push(frame(JSON.stringify(body))), error => error.message === "invalid_response");
  assert.equal(input.closed, true);
  assert.throws(() => input.push(frame(JSON.stringify(body))), FilterBrokerError);
});

test("reentrant delivery cannot interleave decoder state", () => {
  const value = frame(JSON.stringify(body));
  const input = new FrameDecoder(() => input.push(value));
  assert.throws(() => input.push(value), FilterBrokerError);
  assert.equal(input.closed, true);
});

test("explicit close stops trailing frames", () => {
  let calls = 0;
  const input = new FrameDecoder(() => { calls++; input.close(); });
  const one = frame(JSON.stringify(body));
  const bytes = new Uint8Array(one.length * 2);
  bytes.set(one); bytes.set(one, one.length);
  assert.throws(() => input.push(bytes), FilterBrokerError);
  assert.equal(calls, 1);
  assert.equal(input.closed, true);
});
