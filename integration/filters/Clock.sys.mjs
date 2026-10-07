// SPDX-License-Identifier: GPL-3.0-only
import { FilterBrokerError } from "./Frame.sys.mjs";

// Process-local projection; not a trusted clock across restart. The platform
// supplier samples suspend-inclusive uptime BEFORE the wall clock.
export class FilterClock {
  #sample;
  #anchor = null;
  #last = null;
  #failed = false;

  constructor(sample) { this.#sample = sample; }

  nowSeconds() {
    try {
      if (this.#failed) throw new Error();
      const value = this.#sample();
      if (![value.bootMs, value.wallMs].every(n => Number.isSafeInteger(n) && n >= 0)
          || (this.#last && (value.bootMs < this.#last.bootMs || value.wallMs < this.#last.wallMs))) {
        throw new Error();
      }
      this.#anchor ??= { bootMs: value.bootMs, wallMs: value.wallMs };
      this.#last = { bootMs: value.bootMs, wallMs: value.wallMs };
      const projected = this.#anchor.wallMs + value.bootMs - this.#anchor.bootMs;
      if (!Number.isSafeInteger(projected)) throw new Error();
      return Math.floor(Math.max(value.wallMs, projected) / 1000);
    } catch {
      this.#failed = true;
      throw new FilterBrokerError("clock_error");
    }
  }
}
