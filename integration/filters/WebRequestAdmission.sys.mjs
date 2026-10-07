// SPDX-License-Identifier: GPL-3.0-only
// Parent-only bridge. Registration is NOT addon signature or publication approval.
// Native code uses this only for blocking listeners and must discard EVERY
// operation in finally, including ones bypassed by another listener or DNR.
import { FilterAdmission, FilterAdmissionError } from "./Admission.sys.mjs";

const ID = "uBlock0@raymondhill.net";
const MAX_OPERATIONS = 256;
const natural = value => Number.isSafeInteger(value) && value >= 0;
const abort = () => new FilterAdmissionError("abort_required");

function rejected() {
  const result = Promise.reject(abort());
  result.catch(() => {}); // Native may cancel before awaiting this operation.
  return Object.freeze({ result, validate() { throw abort(); }, discard() {} });
}

export class WebRequestAdmissionRegistry {
  #policies = new WeakMap();
  #current = null;

  register(policy, { admission, membership, clock, schedule, cancelTimer, timeoutMs = 10_000 } = {}) {
    try {
      if (policy === null || typeof policy !== "object" || policy.id !== ID || policy.active !== true
          || !(admission instanceof FilterAdmission)
          || !(typeof membership === "symbol" || membership !== null && typeof membership === "object")
          || ![clock, schedule, cancelTimer].every(value => typeof value === "function")
          || !natural(timeoutMs) || timeoutMs < 1 || timeoutMs > 60_000) throw abort();
    } catch { throw new FilterAdmissionError("invalid_config"); }

    const previous = this.#current?.deref();
    previous?.retire(true);
    const record = { admission, membership, clock, schedule, cancelTimer, timeoutMs,
      epoch: 0, closed: false, last: null, operations: new Set() };
    record.retire = close => {
      if (record.closed) return;
      record.epoch += 1;
      record.closed = close;
      record.admission.invalidate();
      for (const operation of record.operations) operation.discard();
      if (close) {
        // Weak tombstone prevents a still-registered native listener from
        // falling through as unguarded, without retaining the owner machinery.
        record.admission = record.membership = record.clock = null;
        record.schedule = record.cancelTimer = null;
      }
    };
    this.#policies.set(policy, record);
    this.#current = new WeakRef(record);
    return Object.freeze({ close: () => record.retire(true), invalidate: () => record.retire(false) });
  }

  start(policy, callback, data) {
    const record = this.#policies.get(policy);
    if (!record) return null;
    // This branch may never throw into upstream's generic callback-error path.
    try {
      if (record.closed || typeof callback !== "function" || record.operations.size >= MAX_OPERATIONS) {
        return rejected();
      }
      return this.#operation(record, policy, callback, data);
    } catch { return rejected(); }
  }

  #operation(record, policy, callback, data) {
    const epoch = record.epoch;
    const { admission, membership, schedule, cancelTimer } = record;
    let ticket = null, timer = null, ended = false, completed = false;
    let invoke = callback, payload = data, resolve, reject;
    const result = new Promise((yes, no) => { resolve = yes; reject = no; });
    result.catch(() => {});

    const sample = () => {
      if (record.closed || record.epoch !== epoch || this.#policies.get(policy) !== record
          || policy.id !== ID || policy.active !== true) throw abort();
      const now = record.clock();
      if (!now || Object.keys(now).sort().join() !== "bootMs,wallMs"
          || !natural(now.bootMs) || !natural(now.wallMs)
          || record.last && (now.bootMs < record.last.bootMs || now.wallMs < record.last.wallMs)) {
        throw abort();
      }
      record.last = { bootMs: now.bootMs, wallMs: now.wallMs };
      return record.last;
    };
    let start;
    const check = () => {
      if (ended) throw abort();
      const now = sample();
      const elapsed = Math.max(now.bootMs - start.bootMs, now.wallMs - start.wallMs);
      if (elapsed >= record.timeoutMs) throw abort();
      return elapsed;
    };
    const cleanup = () => {
      ended = true;
      invoke = payload = null;
      record.operations.delete(operation);
      if (ticket !== null) {
        admission.discard(ticket);
        ticket = null;
      }
      if (timer !== null) {
        const current = timer;
        timer = null;
        cancelTimer(current);
      }
    };
    const discard = () => {
      if (ended) return;
      try { cleanup(); } catch { /* Still settle with a closed error. */ }
      reject(abort());
    };
    const operation = Object.freeze({ result, discard,
      validate: () => {
        try {
          check();
          if (!completed || ticket === null) throw abort();
          admission.validate(ticket);
          ticket = null;
          cleanup();
          return true;
        } catch {
          discard();
          throw abort();
        }
      },
    });
    const arm = elapsed => {
      let scheduling = true;
      timer = schedule(() => {
        if (scheduling) { discard(); return; }
        timer = null;
        if (ended) return;
        try { arm(check()); } catch { discard(); }
      }, Math.max(1, record.timeoutMs - elapsed));
      scheduling = false;
      // A broken synchronous scheduler must not retain an unowned timer.
      if (ended && timer !== null) {
        const current = timer;
        timer = null;
        cancelTimer(current);
      }
    };

    try {
      start = sample();
      record.operations.add(operation);
      arm(0);
      if (!ended) {
        // Capture the returned ticket even after cancellation, then release it.
        // All callback rejections, including late ones, are observed and closed.
        Promise.resolve(admission.admit(membership)).then(async received => {
          if (ended) { admission.discard(received); return; }
          ticket = received;
          check();
          const run = invoke, value = payload;
          invoke = payload = null;
          admission.assertCurrent(ticket);
          const output = await run(value);
          check();
          completed = true;
          resolve(output);
        }).catch(discard);
      }
    } catch { discard(); }
    return operation;
  }
}

// No registration, browser observer or addon-facing API is installed on import.
export const WebRequestAdmission = new WebRequestAdmissionRegistry();
