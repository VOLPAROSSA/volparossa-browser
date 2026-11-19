// SPDX-License-Identifier: GPL-3.0-only
// Browser-owned transport adapters provide the I/O, clock, RNG and digest functions.
// This session exposes only fixed filter operations; never import it into web content.
import { FrameDecoder, FilterBrokerError, MAX_REQUESTS, requestFrame, requireFilter } from "./Frame.sys.mjs";
import { validateExpected, validateResponse } from "./Contract.sys.mjs";

export class FilterBrokerSession {
  constructor(expected, io, {
    nowSeconds, sha256, randomID, schedule = setTimeout, cancelTimer = clearTimeout,
  }) {
    this.expected = validateExpected(expected);
    requireFilter(io && typeof io.write === "function" && typeof io.close === "function"
      && [nowSeconds, sha256, randomID, schedule, cancelTimer].every(value => typeof value === "function"),
    "not_configured");
    Object.assign(this, { io, nowSeconds, sha256, randomID, schedule, cancelTimer });
    this.closed = false;
    this.ended = false;
    this.lastSeconds = null;
    this.capabilities = null;
    this.pending = null;
    this.issued = new Set();
    this.decoder = new FrameDecoder(value => this.response(value));
  }

  negotiate() { return this.request("capabilities"); }
  status() { return this.request("status"); }
  fetch() { return this.request("fetch"); }

  sample() {
    try {
      const now = this.nowSeconds();
      requireFilter(Number.isSafeInteger(now) && now >= 0
        && (this.lastSeconds === null || now >= this.lastSeconds));
      this.lastSeconds = now;
      return now;
    } catch {
      this.close("clock_error");
      throw new FilterBrokerError("clock_error");
    }
  }

  withinDeadline(pending) {
    requireFilter(this.sample() < pending.deadline, "unavailable");
  }

  request(operation) {
    requireFilter(!this.closed && !this.ended, "unavailable");
    requireFilter(!this.pending, "busy");
    requireFilter(operation === "capabilities" ? this.capabilities === null : this.capabilities !== null,
      "handshake_required");
    if (this.issued.size >= MAX_REQUESTS) {
      this.close();
      throw new FilterBrokerError("unavailable");
    }
    let id, frame;
    try {
      id = this.randomID();
      requireFilter(!this.issued.has(id));
      frame = requestFrame(id, operation);
    } catch {
      this.close();
      throw new FilterBrokerError("unavailable");
    }
    this.issued.add(id);
    const duration = operation === "fetch" ? 60 : 5;
    const deadline = this.sample() + duration;
    if (!Number.isSafeInteger(deadline)) {
      this.close("clock_error");
      throw new FilterBrokerError("clock_error");
    }
    const finished = new Promise((resolve, reject) => {
      this.pending = { id, operation, resolve, reject, receiving: false, timer: null, deadline };
    });
    try {
      this.pending.timer = this.schedule(() => this.close(), duration * 1000);
      // The bounded transport writes only this frame; no retry or alternate socket.
      Promise.resolve(this.io.write(frame)).catch(() => this.close());
    } catch {
      this.close();
    }
    return finished;
  }

  push(bytes) {
    if (this.closed) return;
    try { this.decoder.push(bytes); }
    catch { this.close("invalid_response"); }
  }

  eof() {
    if (this.closed) return;
    try { this.decoder.finish(); }
    catch { this.close("invalid_response"); return; }
    this.ended = true;
    // A normal server EOF after its final COMPLETE frame may precede async
    // digest validation. Finish that frame under the original deadline; never
    // admit another request, infer success from EOF, or allow a partial frame.
    if (!this.pending?.receiving) this.close();
  }

  response(value) {
    const pending = this.pending;
    requireFilter(pending !== null && !pending.receiving);
    try { this.withinDeadline(pending); }
    catch (error) { this.close(error.code); return; }
    pending.receiving = true;
    validateResponse(value, {
      id: pending.id, operation: pending.operation, expected: this.expected,
      nowSeconds: () => this.sample(), sha256: this.sha256,
    }).then(result => {
      if (this.closed || this.pending !== pending) return;
      try { this.withinDeadline(pending); }
      catch (error) { this.close(error.code); return; }
      try { this.cancelTimer(pending.timer); }
      catch { this.close("invalid_response"); return; }
      this.pending = null;
      if (pending.operation === "capabilities") this.capabilities = result;
      pending.resolve(result);
      if (this.ended) this.close();
    }, error => {
      if (this.closed || this.pending !== pending) return;
      this.close(error instanceof FilterBrokerError ? error.code : "invalid_response");
    });
  }

  close(code = "unavailable") {
    if (this.closed) return;
    this.closed = true;
    this.decoder.close();
    const pending = this.pending;
    this.pending = null;
    this.capabilities = null;
    if (pending) {
      try { this.cancelTimer(pending.timer); } catch { /* Still settle and close the I/O. */ }
      pending.reject(new FilterBrokerError(code));
    }
    try { this.io.close(); } catch { /* No transport error or pathname escapes. */ }
  }
}
