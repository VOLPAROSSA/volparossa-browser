// SPDX-License-Identifier: GPL-3.0-only
// Parent-process service only. No content/extension API, automatic launch,
// publication discovery, TCP fallback, or uBlock activation is installed here.
import { setTimeout, clearTimeout } from "resource://gre/modules/Timer.sys.mjs";
import { FilterBrokerError, requireFilter, MAX_REQUEST } from "./Frame.sys.mjs";
import { validateExpected } from "./Contract.sys.mjs";
import { FilterBrokerSession } from "./Session.sys.mjs";
import { FilterClock } from "./Clock.sys.mjs";

function privateSocket() {
  requireFilter(Services.appinfo.OS === "Linux"
    && Services.appinfo.processType === Ci.nsIXULRuntime.PROCESS_TYPE_DEFAULT, "unavailable");
  const profile = Services.dirsvc.get("ProfD", Ci.nsIFile);
  const parent = profile.clone();
  parent.append("volparossa-filter-broker");
  for (const directory of [profile, parent]) {
    requireFilter(directory.isDirectory() && !directory.isSymlink()
      && (directory.permissions & 0o7777) === 0o700
      && directory.isReadable() && directory.isWritable(), "not_configured");
  }
  const socket = parent.clone();
  socket.append("filter.sock");
  // No user-supplied endpoint. The non-root browser relies on its private profile
  // directory and normal local-account trust. This is not addon authentication
  // or a defense against the same account/root replacing browser-owned files.
  for (let part = socket; part; part = part.parent) {
    requireFilter(!part.isSymlink(), "not_configured");
    const canonical = part.clone();
    canonical.normalize();
    requireFilter(canonical.path === part.path, "not_configured");
  }
  requireFilter(socket.isSpecial() && (socket.permissions & 0o7777) === 0o600
    && new TextEncoder().encode(socket.path).length <= 100, "not_configured");
  return socket;
}

function sha256(bytes) {
  const hash = Cc["@mozilla.org/security/hash;1"].createInstance(Ci.nsICryptoHash);
  hash.init(hash.SHA256);
  hash.update(bytes, bytes.length);
  return Array.from(hash.finish(false), value => value.charCodeAt(0).toString(16).padStart(2, "0")).join("");
}

/** An explicitly opened, one-owner filter connection. Closing never reconnects. */
export class VolparossaFilters {
  static async connect(expected) {
    let client;
    try {
      const publication = validateExpected(expected);
      const socket = privateSocket();
      const service = Cc["@mozilla.org/network/socket-transport-service;1"]
        .getService(Ci.nsISocketTransportService);
      client = new VolparossaFilters(publication, service.createUnixDomainTransport(socket));
      await client.session.negotiate();
      requireFilter(privateSocket().path === socket.path, "not_configured");
      return client;
    } catch (error) {
      client?.close();
      throw error instanceof FilterBrokerError ? error : new FilterBrokerError("unavailable");
    }
  }

  constructor(expected, transport) {
    this.transport = transport;
    this.closed = false;
    this.outgoing = null;
    const clock = new FilterClock(() => ({
      // Local getter only: does not record or upload telemetry, and does not
      // require telemetry to be enabled. CLOCK_BOOTTIME includes system suspend.
      bootMs: Services.telemetry.msSinceProcessStartIncludingSuspend(),
      wallMs: Date.now(),
    }));
    this.session = new FilterBrokerSession(expected, {
      write: frame => this.write(frame), close: () => this.close(),
    }, {
      nowSeconds: () => clock.nowSeconds(), sha256,
      randomID: () => Cc["@mozilla.org/uuid-generator;1"].getService(Ci.nsIUUIDGenerator)
        .generateUUID().toString().replace(/[{}-]/g, ""),
      schedule: setTimeout, cancelTimer: clearTimeout,
    });
    try {
      transport.setTimeout(Ci.nsISocketTransport.TIMEOUT_CONNECT, 5);
      this.input = transport.openInputStream(0, 0, 0);
      this.output = transport.openOutputStream(0, 0, 0).QueryInterface(Ci.nsIAsyncOutputStream);
      this.pump = Cc["@mozilla.org/network/input-stream-pump;1"].createInstance(Ci.nsIInputStreamPump);
      this.pump.init(this.input, 0, 0, true);
      this.pump.asyncRead(this);
    } catch {
      this.close();
      throw new FilterBrokerError("unavailable");
    }
  }

  QueryInterface = ChromeUtils.generateQI(["nsIStreamListener", "nsIRequestObserver", "nsIOutputStreamCallback"]);

  status() { return this.session.status(); }
  fetch() { return this.session.fetch(); }

  write(frame) {
    requireFilter(!this.closed && this.outgoing === null && frame instanceof Uint8Array
      && frame.length <= MAX_REQUEST + 4, "unavailable");
    this.outgoing = { frame, offset: 0 };
    this.flush();
  }

  flush() {
    if (this.closed || !this.outgoing) return;
    const next = this.outgoing;
    const bytes = next.frame.subarray(next.offset);
    let written = 0;
    try { written = this.output.write(String.fromCharCode(...bytes), bytes.length); }
    catch (error) { if (error.result !== Cr.NS_BASE_STREAM_WOULD_BLOCK) throw error; }
    requireFilter(Number.isSafeInteger(written) && written >= 0 && written <= bytes.length, "unavailable");
    next.offset += written;
    if (next.offset === next.frame.length) this.outgoing = null;
    else this.output.asyncWait(this, 0, 0, Services.tm.currentThread);
  }

  onOutputStreamReady() {
    try { this.flush(); } catch { this.close(); }
  }

  onStartRequest() {}
  onStopRequest(_request, status) {
    if (Components.isSuccessCode(status)) this.session.eof();
    else this.close();
  }

  onDataAvailable(_request, input, _offset, count) {
    if (this.closed) return;
    try {
      requireFilter(Number.isSafeInteger(count) && count > 0);
      const binary = Cc["@mozilla.org/binaryinputstream;1"].createInstance(Ci.nsIBinaryInputStream);
      binary.setInputStream(input);
      while (count && !this.closed) {
        const length = Math.min(count, 8192);
        const bytes = binary.readByteArray(length);
        requireFilter(bytes.length === length);
        this.session.push(new Uint8Array(bytes));
        count -= length;
      }
    } catch { this.session.close("invalid_response"); }
  }

  close() {
    if (this.closed) return;
    this.closed = true;
    this.outgoing = null;
    this.session.close();
    try { this.pump?.cancel(Cr.NS_BINDING_ABORTED); } catch {}
    try { this.input?.close(); } catch {}
    try { this.output?.close(); } catch {}
    try { this.transport.close(Cr.NS_BINDING_ABORTED); } catch {}
  }
}
