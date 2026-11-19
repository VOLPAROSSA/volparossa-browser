// SPDX-License-Identifier: GPL-3.0-only
// Pure framing for the scoped local public-filter service. No endpoint or authority.
export const VERSION = 1;
export const MAX_REQUEST = 512;
export const MAX_RESPONSE = 2 * 1024 * 1024;
export const MAX_REQUESTS = 32;
const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });

export class FilterBrokerError extends Error {
  constructor(code = "invalid_response") {
    super(code);
    this.code = code;
  }
}

export function requireFilter(condition, code = "invalid_response") {
  if (!condition) throw new FilterBrokerError(code);
}

export function exactKeys(value, keys) {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).sort().join() === [...keys].sort().join();
}

export function requestFrame(id, operation) {
  requireFilter(typeof id === "string" && id.length === 32 && /^[0-9a-f]{32}$/.test(id)
    && ["capabilities", "status", "fetch"].includes(operation), "invalid_request");
  // No caller-supplied selector, path, URL, command or authority can enter the wire.
  const body = encoder.encode(JSON.stringify({ version: VERSION, id, operation: { type: operation } }));
  requireFilter(body.length > 0 && body.length <= MAX_REQUEST, "invalid_request");
  const frame = new Uint8Array(body.length + 4);
  new DataView(frame.buffer).setUint32(0, body.length);
  frame.set(body, 4);
  return frame;
}

export class FrameDecoder {
  constructor(onMessage) {
    requireFilter(typeof onMessage === "function");
    this.onMessage = onMessage;
    this.header = new Uint8Array(4);
    this.headerUsed = 0;
    this.body = null;
    this.bodyUsed = 0;
    this.closed = false;
    this.reading = false;
  }

  close() {
    this.closed = true;
    this.body = null;
    this.headerUsed = 0;
    this.bodyUsed = 0;
  }

  finish() {
    const complete = this.headerUsed === 0 && this.body === null;
    this.close();
    requireFilter(complete);
  }

  push(bytes) {
    requireFilter(!this.closed && !this.reading && bytes instanceof Uint8Array);
    this.reading = true;
    try {
      this.consume(bytes);
    } catch {
      this.close();
      // Never propagate peer JSON, UTF-8 errors or consumer exception messages.
      throw new FilterBrokerError();
    } finally {
      this.reading = false;
    }
  }

  consume(bytes) {
    let offset = 0;
    while (offset < bytes.length && !this.closed) {
      if (!this.body) {
        const count = Math.min(4 - this.headerUsed, bytes.length - offset);
        this.header.set(bytes.subarray(offset, offset + count), this.headerUsed);
        this.headerUsed += count;
        offset += count;
        if (this.headerUsed !== 4) continue;
        const length = new DataView(this.header.buffer).getUint32(0);
        requireFilter(length > 0 && length <= MAX_RESPONSE);
        this.body = new Uint8Array(length);
        this.bodyUsed = 0;
      }
      const count = Math.min(this.body.length - this.bodyUsed, bytes.length - offset);
      this.body.set(bytes.subarray(offset, offset + count), this.bodyUsed);
      this.bodyUsed += count;
      offset += count;
      if (this.bodyUsed === this.body.length) {
        const text = decoder.decode(this.body);
        const value = JSON.parse(text);
        // The broker emits compact JSON with safe integer fields. Require the
        // exact scalar representation, rejecting duplicate keys and lossy numbers.
        requireFilter(value !== null && typeof value === "object" && !Array.isArray(value)
          && JSON.stringify(value) === text);
        this.body = null;
        this.bodyUsed = 0;
        this.headerUsed = 0;
        this.onMessage(value);
      }
    }
    requireFilter(offset === bytes.length);
  }
}
