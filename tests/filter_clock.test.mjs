// SPDX-License-Identifier: GPL-3.0-only
import assert from "node:assert/strict";
import test from "node:test";
import { FilterClock } from "../integration/filters/Clock.sys.mjs";

test("suspend-inclusive time bounds a slow wall clock without extending authority", () => {
  let time = { bootMs: 5000, wallMs: 1_000_000 };
  const clock = new FilterClock(() => time);
  assert.equal(clock.nowSeconds(), 1000);
  time = { bootMs: 9000, wallMs: 1_001_000 };
  assert.equal(clock.nowSeconds(), 1004);
  time = { bootMs: 10000, wallMs: 1_020_000 };
  assert.equal(clock.nowSeconds(), 1020);
});

test("regression, unavailable clocks and invalid numbers terminally close", () => {
  for (const broken of [
    { bootMs: 4999, wallMs: 1_000_000 }, { bootMs: 5000, wallMs: 999999 },
    { bootMs: NaN, wallMs: 1_000_000 }, { bootMs: 5000.5, wallMs: 1_000_000 },
    { bootMs: 5000, wallMs: Infinity }, { bootMs: -1, wallMs: 1_000_000 }, null,
  ]) {
    let time = { bootMs: 5000, wallMs: 1_000_000 };
    const clock = new FilterClock(() => time);
    clock.nowSeconds(); time = broken;
    assert.throws(() => clock.nowSeconds(), error => error.message === "clock_error");
    time = { bootMs: 5001, wallMs: 1_000_001 };
    assert.throws(() => clock.nowSeconds(), error => error.message === "clock_error");
  }
  const clock = new FilterClock(() => { throw new Error("platform-private-canary"); });
  assert.throws(() => clock.nowSeconds(), error => error.message === "clock_error");
});

test("projection overflow cannot wrap to an earlier authorization time", () => {
  let time = { bootMs: 0, wallMs: Number.MAX_SAFE_INTEGER - 1 };
  const clock = new FilterClock(() => time); clock.nowSeconds();
  time = { bootMs: 3, wallMs: Number.MAX_SAFE_INTEGER - 1 };
  assert.throws(() => clock.nowSeconds());
});
