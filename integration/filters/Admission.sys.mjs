// SPDX-License-Identifier: GPL-3.0-only
// Browser-owned NEW-callback admission, not addon identity verification or a
// native WebRequest hook. The caller verifies the exact signed uBO membership,
// owns both clocks/timers (timers must schedule asynchronously), and proves
// supplementary-only removal + fresh reload
// before returning "absent". Never give these capabilities to an extension/page.
// No URLs, callback replay, stock-list disabling or past-side-effect revocation.

const CODES = new Set([
  "invalid_config", "admission_identity", "admission_capacity", "admission_closed",
  "admission_invalidated", "clock_invalid", "reconcile_busy", "reconcile_failed",
  "reconcile_timeout", "invalid_generation", "abort_required",
]);
const MAX_PENDING = 256;
const MAX_TIMEOUT_MS = 60_000;
const MAX_LEASE_MS = 3_600_000;

export class FilterAdmissionError extends Error {
  constructor(code) {
    const closed = CODES.has(code) ? code : "invalid_config";
    super(closed);
    this.code = closed;
  }
}

function exact(value, keys) {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).sort().join() === [...keys].sort().join();
}
function natural(value) {
  return Number.isSafeInteger(value) && value >= 0;
}
function demand(condition) {
  if (!condition) throw new FilterAdmissionError("invalid_config");
}

export class FilterAdmission {
  #membership;
  #clock;
  #reconcile;
  #schedule;
  #cancelTimer;
  #maximum;
  #timeout;
  #last = null;
  #ready = null;
  #flight = null;
  #waiters = [];
  #tickets = new Map();
  #epoch = 0;
  #closed = false;

  constructor({ membership, clock, reconcile, schedule, cancelTimer,
                maxPending = 32, timeoutMs = 10_000 }) {
    demand((typeof membership === "symbol" || (membership !== null && typeof membership === "object"))
      && [clock, reconcile, schedule, cancelTimer].every(value => typeof value === "function")
      && natural(maxPending) && maxPending >= 1 && maxPending <= MAX_PENDING
      && natural(timeoutMs) && timeoutMs >= 1 && timeoutMs <= MAX_TIMEOUT_MS);
    this.#membership = membership;
    this.#clock = clock;
    this.#reconcile = reconcile;
    this.#schedule = schedule;
    this.#cancelTimer = cancelTimer;
    this.#maximum = maxPending;
    this.#timeout = timeoutMs;
  }

  get status() {
    // Cached diagnostic only; neither a current-clock check nor authorization.
    return Object.freeze({ state: this.#closed ? "closed" : this.#ready ? "ready"
      : this.#flight ? "reconciling" : "unreconciled",
    waiting: this.#waiters.length, outstanding: this.#tickets.size });
  }

  #sample() {
    try {
      const now = this.#clock();
      if (!exact(now, ["bootMs", "wallMs"]) || !natural(now.bootMs) || !natural(now.wallMs)
          || (this.#last && (now.bootMs < this.#last.bootMs || now.wallMs < this.#last.wallMs))) {
        throw new Error();
      }
      this.#last = { bootMs: now.bootMs, wallMs: now.wallMs };
      return this.#last;
    } catch {
      this.#stop("clock_invalid");
      throw new FilterAdmissionError("clock_invalid");
    }
  }

  #expired(now, ready = this.#ready) {
    return ready?.mode === "active"
      && (now.bootMs >= ready.bootDeadline || now.wallMs >= ready.expiresAtMs);
  }

  #ticket() {
    const ticket = Object.freeze({});
    this.#tickets.set(ticket, this.#epoch);
    return ticket;
  }

  async admit(membership) {
    if (membership !== this.#membership) throw new FilterAdmissionError("admission_identity");
    if (this.#closed) throw new FilterAdmissionError("admission_closed");
    const now = this.#sample();
    if (this.#expired(now)) this.invalidate();
    // Resume/new-event ordering must not depend on delivery of an expired timer.
    if (this.#flight?.live && this.#timedOut(this.#flight, now)) {
      this.#rejectWaiters("reconcile_timeout");
      this.#retireFlight();
    }
    if (this.#flight && !this.#flight.live) throw new FilterAdmissionError("reconcile_busy");
    if (this.#waiters.length + this.#tickets.size >= this.#maximum) {
      throw new FilterAdmissionError("admission_capacity");
    }
    if (this.#ready) return this.#ticket();
    return new Promise((resolve, reject) => {
      this.#waiters.push({ resolve, reject });
      if (!this.#flight) this.#start(now);
    });
  }

  // Single-use, synchronous final check immediately before applying a result.
  // Any failure requires the caller to abort the affected channel, not treat the
  // old combined uBO result as harmless or invoke the listener a second time.
  validate(ticket) {
    const epoch = this.#tickets.get(ticket);
    this.#tickets.delete(ticket);
    try {
      if (this.#closed) throw new Error();
      const now = this.#sample();
      if (this.#expired(now)) this.invalidate();
      if (epoch === undefined || epoch !== this.#epoch || !this.#ready) throw new Error();
      return true;
    } catch {
      throw new FilterAdmissionError("abort_required");
    }
  }

  // Release a cancelled/completed request without authorizing any result.
  discard(ticket) {
    this.#tickets.delete(ticket);
  }

  invalidate() {
    // Owner must call before every list/authority mutation, addon restart or
    // membership change. This component cannot observe those external events.
    this.#epoch += 1;
    this.#ready = null;
    this.#tickets.clear();
    this.#rejectWaiters("admission_invalidated");
    this.#retireFlight();
  }

  close() {
    this.#stop("admission_closed");
  }

  #rejectWaiters(code) {
    const waiters = this.#waiters;
    this.#waiters = [];
    for (const waiter of waiters) waiter.reject(new FilterAdmissionError(code));
  }

  #clearTimer(flight) {
    if (flight.timer === null) return;
    const timer = flight.timer;
    flight.timer = null;
    try {
      this.#cancelTimer(timer);
    } catch {
      this.#closed = true;
      throw new FilterAdmissionError("reconcile_failed");
    }
  }

  #retireFlight() {
    const flight = this.#flight;
    if (!flight) return;
    flight.live = false;
    flight.controller.abort();
    try { this.#clearTimer(flight); } catch { this.#closed = true; }
    // A supplier may ignore AbortSignal. Keep its slot until it really settles:
    // no overlapping reconciliation or late authorization after timeout/close.
  }

  #stop(code) {
    this.#closed = true;
    this.#ready = null;
    this.#tickets.clear();
    this.#rejectWaiters(code);
    this.#retireFlight();
  }

  #timedOut(flight, now) {
    return now.bootMs - flight.start.bootMs >= this.#timeout
      || now.wallMs - flight.start.wallMs >= this.#timeout;
  }

  #arm(flight, now) {
    const elapsed = Math.max(now.bootMs - flight.start.bootMs, now.wallMs - flight.start.wallMs);
    flight.timer = this.#schedule(() => {
      flight.timer = null;
      if (this.#flight !== flight || !flight.live) return;
      try {
        const current = this.#sample();
        if (this.#timedOut(flight, current)) {
          this.#rejectWaiters("reconcile_timeout");
          this.#retireFlight();
        } else {
          this.#arm(flight, current);
        }
      } catch {
        this.#stop("clock_invalid");
      }
    }, Math.max(1, this.#timeout - elapsed));
  }

  #start(now) {
    const flight = { start: now, epoch: this.#epoch, live: true, timer: null,
      controller: new AbortController() };
    this.#flight = flight;
    try {
      this.#arm(flight, now);
    } catch {
      this.#rejectWaiters("reconcile_failed");
      this.#retireFlight();
      this.#flight = null;
      return;
    }
    Promise.resolve().then(() => {
      if (!flight.live) return null;
      return this.#reconcile(Object.freeze({ signal: flight.controller.signal }));
    }).then(value => this.#finish(flight, value), () => this.#finish(flight, null));
  }

  #finish(flight, value) {
    if (this.#flight !== flight) return;
    try {
      this.#clearTimer(flight);
      if (!flight.live || this.#closed || flight.epoch !== this.#epoch) return;
      const now = this.#sample();
      if (this.#timedOut(flight, now)) throw new FilterAdmissionError("reconcile_timeout");
      let ready;
      if (exact(value, ["mode"]) && value.mode === "absent") {
        ready = { mode: "absent" };
      } else if (exact(value, ["mode", "generation", "expiresAtMs"]) && value.mode === "active"
          && typeof value.generation === "string" && value.generation.length === 64
          && /^[0-9a-f]{64}$/.test(value.generation) && value.generation !== "0".repeat(64)
          && natural(value.expiresAtMs)) {
        // Anchor the monotonic lease to reconciliation START, never extend it
        // by time spent awaiting a response or by wall-clock rollback.
        const lifetime = value.expiresAtMs - flight.start.wallMs;
        const deadline = flight.start.bootMs + lifetime;
        if (lifetime <= 0 || lifetime > MAX_LEASE_MS || !natural(deadline)) {
          throw new FilterAdmissionError("invalid_generation");
        }
        ready = { mode: "active", generation: value.generation,
          expiresAtMs: value.expiresAtMs, bootDeadline: deadline };
        if (this.#expired(now, ready)) throw new FilterAdmissionError("invalid_generation");
      } else {
        throw new FilterAdmissionError("reconcile_failed");
      }
      this.#ready = ready;
      const waiters = this.#waiters;
      this.#waiters = [];
      for (const waiter of waiters) waiter.resolve(this.#ticket());
    } catch (error) {
      this.#ready = null;
      this.#rejectWaiters(error instanceof FilterAdmissionError ? error.code : "reconcile_failed");
    } finally {
      flight.live = false;
      flight.controller.abort();
      this.#flight = null;
    }
  }
}
