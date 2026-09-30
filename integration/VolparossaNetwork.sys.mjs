// SPDX-License-Identifier: GPL-3.0-only
// Privileged, operator-authorized single-authority adapter. Not a global proxy.
import { setTimeout, clearTimeout } from "resource://gre/modules/Timer.sys.mjs";

const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });
const MAX_FRAME = 4096;
// Core admission prepares the signed route before publishing Ready. Keep this
// control-plane wait outside Firefox's ordinary CONNECT/origin-TLS deadline.
const BOOTSTRAP_TIMEOUT_MS = 95000;
const HEX = /^[0-9a-f]{64}$/;
const ATTACH_STAGES = new Set(["process-gate", "unix-transport", "constructor", "transport-timeout", "input-stream",
  "output-stream", "input-pump", "input-listen", "proxy-filter", "bootstrap-write", "bootstrap-wait",
  "bootstrap-reply", "bootstrap-read", "ready-validate", "ready-proxy", "bootstrap-eof", "bootstrap-timeout"]);
const attachments = new Set();
const channels = new WeakMap();
const networkDecisions = new WeakMap();
const proxyService = Cc["@mozilla.org/network/protocol-proxy-service;1"]
  .getService(Ci.nsIProtocolProxyService);

export class VolparossaNetworkError extends Error {
  constructor(code, stage = null, error = null) {
    super(code);
    this.code = code;
    const result = error?.result;
    // Closed execution facts only: no exception text, paths, capability or authority.
    this.diagnostic = ATTACH_STAGES.has(stage) ? Object.freeze({stage,
      nsresult: Number.isInteger(result) && result >= -2147483648 && result <= 4294967295
        ? result >>> 0 : null}) : null;
  }
}
/** Only the authenticated bootstrap decoder can supply a network decision. */
export function getNetworkDecision(error) { return networkDecisions.get(error) ?? null; }
// ESR 140 exposes the same process-monotonic clock through Cu; the pinned newer
// source moves it to ChromeUtils. Never substitute wall time for this bound.
export function networkMonotonicNow() {
  return typeof ChromeUtils.now === "function" ? ChromeUtils.now() : Cu.now();
}
function fail(code) { throw new VolparossaNetworkError(code); }
function keys(value, expected) {
  if (!value || typeof value !== "object" || Array.isArray(value) ||
      Object.keys(value).sort().join(",") !== expected.sort().join(",")) {
    fail("invalid_contract");
  }
}
function authority(hostname, port) {
  if (typeof hostname !== "string" || hostname.length > 253 ||
      !hostname.includes(".") || /^[0-9.]+$/.test(hostname) ||
      !hostname.split(".").every(label => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label)) ||
      !Number.isInteger(port) || port < 1 || port > 65535) {
    fail("invalid_scope");
  }
}

/** Pure schema validation; grant is supplied by privileged owner code, never a web page. */
export function validateNetworkGrant(grant, now = Date.now()) {
  keys(grant, ["version", "app_uid", "app_socket", "capability", "hostname", "port",
    "partition", "expires_at_ms", "overlay_only"]);
  authority(grant.hostname, grant.port);
  if (grant.version !== 1 || grant.overlay_only !== true ||
      !Number.isInteger(grant.app_uid) || grant.app_uid < 0 || grant.app_uid > 4294967295 ||
      typeof grant.app_socket !== "string" || !grant.app_socket.startsWith("/") ||
      grant.app_socket.includes("\0") || encoder.encode(grant.app_socket).length > 107 ||
      typeof grant.capability !== "string" || !HEX.test(grant.capability) ||
      typeof grant.partition !== "string" || !HEX.test(grant.partition) ||
      !Number.isSafeInteger(grant.expires_at_ms) || grant.expires_at_ms <= now ||
      grant.expires_at_ms - now > 300000) {
    fail("invalid_contract");
  }
  return Object.freeze({ ...grant });
}

export function validateNetworkReady(reply, grant, now = Date.now()) {
  keys(reply, ["version", "proxy_host", "proxy_port", "proxy_authorization", "hostname", "port",
    "partition", "expires_at_ms", "overlay_only"]);
  if (reply.version !== 1 || reply.proxy_host !== "127.0.0.1" ||
      !Number.isInteger(reply.proxy_port) || reply.proxy_port < 1 || reply.proxy_port > 65535 ||
      typeof reply.proxy_authorization !== "string" || !/^Bearer [0-9a-f]{64}$/.test(reply.proxy_authorization) ||
      reply.hostname !== grant.hostname || reply.port !== grant.port || reply.partition !== grant.partition ||
      reply.expires_at_ms !== grant.expires_at_ms || reply.expires_at_ms <= now || reply.overlay_only !== true) {
    fail("invalid_contract");
  }
  return Object.freeze({ ...reply });
}

/** Parsing alone does not confer authority: only a reply received on attach() does. */
export function validateNetworkFailure(reply, grant, now = Date.now()) {
  keys(reply, ["version", "status", "reason", "hostname", "port", "partition", "expires_at_ms", "direct_until_ms"]);
  if (reply.version !== 1 || reply.hostname !== grant.hostname || reply.port !== grant.port ||
      reply.partition !== grant.partition || reply.expires_at_ms !== grant.expires_at_ms ||
      !Number.isSafeInteger(reply.expires_at_ms) || reply.expires_at_ms <= now ||
      !Number.isSafeInteger(reply.direct_until_ms) ||
      !((reply.status === "denied" && reply.reason === "blocked" && reply.direct_until_ms === 0) ||
        (reply.status === "unavailable" && reply.reason === "no_eligible_paths" &&
         reply.direct_until_ms > now && reply.direct_until_ms <= now + 5000 &&
         reply.direct_until_ms <= grant.expires_at_ms))) {
    fail("invalid_contract");
  }
  return Object.freeze({ ...reply });
}

const filter = {
  QueryInterface: ChromeUtils.generateQI(["nsIProtocolProxyChannelFilter"]),
  applyFilter(channel, original, callback) {
    const owner = channels.get(channel);
    if (!owner) {
      callback.onProxyFilterResult(original);
      return;
    }
    try {
      owner._checkChannel(channel);
      callback.onProxyFilterResult(owner._proxy);
    } catch {
      channel.cancel(Cr.NS_ERROR_ABORT);
      // Never return null (= DIRECT) for an owned request, including after EOF.
      throw Components.Exception("VOLPAROSSA_SCOPED_REQUEST_BLOCKED", Cr.NS_ERROR_ABORT);
    }
  },
};

/** A held Unix connection owns this attachment and its exact-authority channels. */
export class VolparossaNetwork {
  static async attach(input) {
    if (Services.appinfo.processType !== Ci.nsIXULRuntime.PROCESS_TYPE_DEFAULT || attachments.size >= 8) {
      throw new VolparossaNetworkError("unavailable", "process-gate");
    }
    const grant = validateNetworkGrant(input);
    const file = Cc["@mozilla.org/file/local;1"].createInstance(Ci.nsIFile);
    file.initWithPath(grant.app_socket);
    let attachment, stage = "unix-transport";
    try {
      const service = Cc["@mozilla.org/network/socket-transport-service;1"].getService(Ci.nsISocketTransportService);
      const transport = service.createUnixDomainTransport(file);
      stage = "constructor";
      attachment = new VolparossaNetwork(transport, grant);
      await attachment.ready;
      return attachment;
    } catch (error) {
      attachment?.close();
      throw error instanceof VolparossaNetworkError ? error : new VolparossaNetworkError("unavailable", stage, error);
    }
  }

  constructor(transport, grant) {
    this._grant = grant;
    this._grantDeadline = networkMonotonicNow() + Math.max(0, grant.expires_at_ms - Date.now());
    this._transport = transport;
    this._closed = false;
    this._ready = false;
    this._active = new Set();
    this._header = new Uint8Array(4);
    this._headerUsed = 0;
    this._body = null;
    this._bodyUsed = 0;
    this._proxy = null;
    const nonce = Cc["@mozilla.org/uuid-generator;1"].getService(Ci.nsIUUIDGenerator)
      .generateUUID().toString().replace(/[{}-]/g, "");
    this._isolation = "volparossa:" + grant.partition + ":" + nonce;
    this.ready = new Promise((resolve, reject) => { this._resolve = resolve; this._reject = reject; });
    this._timer = setTimeout(() => this.close("unavailable", "bootstrap-timeout"),
      Math.min(BOOTSTRAP_TIMEOUT_MS, grant.expires_at_ms - Date.now()));
    try {
      this._attachStage = "transport-timeout";
      transport.setTimeout(Ci.nsISocketTransport.TIMEOUT_CONNECT, 5);
      this._attachStage = "input-stream";
      this._input = transport.openInputStream(0, 0, 0);
      this._attachStage = "output-stream";
      this._output = transport.openOutputStream(0, 0, 0).QueryInterface(Ci.nsIAsyncOutputStream);
      this._attachStage = "input-pump";
      this._pump = Cc["@mozilla.org/network/input-stream-pump;1"].createInstance(Ci.nsIInputStreamPump);
      this._pump.init(this._input, 0, 0, true);
      this._attachStage = "input-listen";
      this._pump.asyncRead(this);
      const payload = encoder.encode(JSON.stringify({ version: 1, capability: grant.capability, partition: grant.partition }));
      this._frame = new Uint8Array(4 + payload.length);
      new DataView(this._frame.buffer).setUint32(0, payload.length);
      this._frame.set(payload, 4);
      this._offset = 0;
      this._attachStage = "proxy-filter";
      if (!attachments.size) { proxyService.registerChannelFilter(filter, 0xffffffff); }
      attachments.add(this);
      this._flush();
    } catch (error) {
      // Let attach() observe the same ready rejection even if setup failed synchronously.
      this.close("unavailable", this._attachStage, error);
    }
  }

  QueryInterface = ChromeUtils.generateQI(["nsIStreamListener", "nsIRequestObserver", "nsIOutputStreamCallback"]);

  get active() { return this._ready && !this._closed; }

  _checkChannel(channel) {
    const uri = channel.URI;
    if (!this.active || Date.now() >= this._grant.expires_at_ms || networkMonotonicNow() >= this._grantDeadline || !uri.schemeIs("https") ||
        uri.asciiHost !== this._grant.hostname || (uri.port === -1 ? 443 : uri.port) !== this._grant.port ||
        uri.userPass) {
      fail("scope_unavailable");
    }
  }

  /** Open one fresh privileged channel; redirects are deliberately outside this first slice. */
  openChannel(channel, listener) {
    this._checkChannel(channel);
    if (channels.has(channel) || channel.isPending() || channel.notificationCallbacks || this._active.size >= 8) {
      fail("invalid_channel");
    }
    const internal = channel.QueryInterface(Ci.nsIHttpChannelInternal);
    if (!("allowHttp3" in internal) || !("allowAltSvc" in internal)) { fail("unsupported_runtime"); }
    internal.allowHttp3 = false;
    internal.allowAltSvc = false;
    internal.allowSpdy = false;
    internal.beConservative = false; // Firefox conservative proxy-failover must never become DIRECT.
    internal.bypassProxy = false;
    internal.blockAuthPrompt = true;
    channel.loadFlags |= Ci.nsIRequest.LOAD_BYPASS_CACHE | Ci.nsIRequest.INHIBIT_CACHING;
    // Firefox ignores nsIProxyInfo.proxyAuthorizationHeader for an HTTP proxy
    // (it consumes that field only for HTTPS/MASQUE proxies). Its native
    // CONNECT builder copies this proxy header and RequestHeaderBuf then prunes
    // Proxy-Authorization from the HTTPS origin request. Require that exact
    // HTTPS+CONNECT path; redirects, direct fallback and HTTP are forbidden.
    const http = channel.QueryInterface(Ci.nsIHttpChannel);
    try { http.getRequestHeader("Proxy-Authorization"); fail("invalid_channel"); }
    catch (error) { if (error instanceof VolparossaNetworkError) { throw error; } }
    http.setRequestHeader("Proxy-Authorization", this._proxy.proxyAuthorizationHeader, false);
    const owner = this;
    const callbacks = {
      QueryInterface: ChromeUtils.generateQI(["nsIInterfaceRequestor", "nsIChannelEventSink"]),
      getInterface(iid) { return this.QueryInterface(iid); },
      asyncOnChannelRedirect(_old, _next, _flags, callback) {
        callback.onRedirectVerifyCallback(Cr.NS_ERROR_ABORT);
      },
    };
    channel.notificationCallbacks = callbacks;
    const wrapper = {
      QueryInterface: ChromeUtils.generateQI(["nsIStreamListener", "nsIRequestObserver"]),
      onStartRequest(request) { listener.onStartRequest(request); },
      onDataAvailable(...args) { listener.onDataAvailable(...args); },
      onStopRequest(request, status) {
        owner._active.delete(channel);
        channels.delete(channel);
        try { http.setRequestHeader("Proxy-Authorization", "", false); } catch {}
        listener.onStopRequest(request, status);
      },
    };
    channels.set(channel, this);
    this._active.add(channel);
    try { channel.asyncOpen(wrapper); }
    catch (error) {
      this._active.delete(channel);
      channels.delete(channel);
      channel.cancel(Cr.NS_ERROR_ABORT);
      throw new VolparossaNetworkError("request_failed");
    }
  }

  /** Adopt an ordinary, already-open Gecko channel during native proxy resolution.
   * The browser still owns its listener, load group and non-redirect callbacks.
   * This does not create another request or replay an existing request body.
   */
  adoptChannel(channel) {
    this._checkChannel(channel);
    if (channels.has(channel) || this._active.size >= 8) { fail("invalid_channel"); }
    const internal = channel.QueryInterface(Ci.nsIHttpChannelInternal);
    if (!("allowHttp3" in internal) || !("allowAltSvc" in internal)) { fail("unsupported_runtime"); }
    internal.allowHttp3 = false;
    internal.allowAltSvc = false;
    internal.allowSpdy = false;
    internal.beConservative = false;
    internal.bypassProxy = false;
    internal.blockAuthPrompt = true;
    channel.loadFlags |= Ci.nsIRequest.LOAD_BYPASS_CACHE | Ci.nsIRequest.INHIBIT_CACHING;
    const http = channel.QueryInterface(Ci.nsIHttpChannel);
    try { http.getRequestHeader("Proxy-Authorization"); fail("invalid_channel"); }
    catch (error) { if (error instanceof VolparossaNetworkError) { throw error; } }
    http.setRequestHeader("Proxy-Authorization", this._proxy.proxyAuthorizationHeader, false);
    const previousCallbacks = channel.notificationCallbacks;
    const callbacks = {
      QueryInterface: ChromeUtils.generateQI(["nsIInterfaceRequestor", "nsIChannelEventSink"]),
      getInterface(iid) {
        if (iid.equals(Ci.nsIChannelEventSink)) { return this; }
        if (previousCallbacks) { return previousCallbacks.getInterface(iid); }
        throw Components.Exception("VOLPAROSSA_NO_INTERFACE", Cr.NS_ERROR_NO_INTERFACE);
      },
      asyncOnChannelRedirect(_old, _next, _flags, callback) {
        // No inherited proxy secret may escape to an unbound redirected request.
        callback.onRedirectVerifyCallback(Cr.NS_ERROR_ABORT);
      },
    };
    channel.notificationCallbacks = callbacks;
    const owner = this;
    let listener;
    const wrapper = {
      QueryInterface: ChromeUtils.generateQI(["nsIStreamListener", "nsIRequestObserver"]),
      onStartRequest(request) { listener.onStartRequest(request); },
      onDataAvailable(...args) { listener.onDataAvailable(...args); },
      onStopRequest(request, status) {
        owner._active.delete(channel);
        channels.delete(channel);
        try { http.setRequestHeader("Proxy-Authorization", "", false); } catch {}
        try {
          if (channel.notificationCallbacks === callbacks) { channel.notificationCallbacks = previousCallbacks; }
        } catch {}
        listener.onStopRequest(request, status);
      },
    };
    try {
      listener = channel.QueryInterface(Ci.nsITraceableChannel).setNewListener(wrapper);
      if (!listener) { fail("invalid_channel"); }
      channels.set(channel, this);
      this._active.add(channel);
      return this._proxy;
    } catch {
      channel.cancel(Cr.NS_ERROR_ABORT);
      throw new VolparossaNetworkError("invalid_channel");
    }
  }

  close(code = "detached", stage = null, error = null) {
    if (this._closed) { return; }
    this._closed = true;
    clearTimeout(this._timer);
    this._reject(new VolparossaNetworkError(code, stage, error));
    for (const channel of this._active) {
      try { channel.cancel(Cr.NS_ERROR_ABORT); } catch {}
    }
    this._active.clear();
    this._frame = null;
    this._body = null;
    this._proxy = null;
    this._grant = { ...this._grant, capability: "" };
    try { this._pump.cancel(Cr.NS_BINDING_ABORTED); } catch {}
    try { this._input.close(); } catch {}
    try { this._output.close(); } catch {}
    try { this._transport.close(Cr.NS_BINDING_ABORTED); } catch {}
    attachments.delete(this);
    // Keep the shared filter installed while cancelled channels finish: their
    // existing WeakMap ownership must still fail closed on late proxy resolution.
  }

  _flush() {
    if (this._closed || !this._frame) { return; }
    this._attachStage = "bootstrap-write";
    const bytes = this._frame.subarray(this._offset);
    let written;
    try { written = this._output.write(String.fromCharCode(...bytes), bytes.length); }
    catch (error) {
      if (error.result !== Cr.NS_BASE_STREAM_WOULD_BLOCK) { throw error; }
      written = 0;
    }
    this._offset += written;
    if (this._offset === this._frame.length) {
      this._frame = null;
      this._attachStage = "bootstrap-reply";
    } else {
      this._attachStage = "bootstrap-wait";
      this._output.asyncWait(this, 0, 0, Services.tm.currentThread);
    }
  }
  onOutputStreamReady() { try { this._flush(); } catch (error) { this.close("unavailable", this._attachStage, error); } }
  onStartRequest() {}
  onStopRequest(_request, status) { this.close("unavailable", "bootstrap-eof", {result: status}); }
  onDataAvailable(_request, input, _offset, count) {
    try {
      this._attachStage = "bootstrap-read";
      const binary = Cc["@mozilla.org/binaryinputstream;1"].createInstance(Ci.nsIBinaryInputStream);
      binary.setInputStream(input);
      while (count && !this._closed) {
        const length = Math.min(count, 4096);
        this._consume(new Uint8Array(binary.readByteArray(length)));
        count -= length;
      }
    } catch (error) { this.close("invalid_contract", this._attachStage, error); }
  }
  _consume(bytes) {
    let offset = 0;
    while (offset < bytes.length && !this._closed) {
      if (this._ready) { fail("invalid_contract"); }
      if (!this._body) {
        const length = Math.min(4 - this._headerUsed, bytes.length - offset);
        this._header.set(bytes.subarray(offset, offset + length), this._headerUsed);
        this._headerUsed += length;
        offset += length;
        if (this._headerUsed !== 4) { continue; }
        const size = new DataView(this._header.buffer).getUint32(0);
        if (!size || size > MAX_FRAME) { fail("invalid_contract"); }
        this._body = new Uint8Array(size);
      }
      const length = Math.min(this._body.length - this._bodyUsed, bytes.length - offset);
      this._body.set(bytes.subarray(offset, offset + length), this._bodyUsed);
      this._bodyUsed += length;
      offset += length;
      if (this._bodyUsed === this._body.length) {
        this._attachStage = "ready-validate";
        const parsed = JSON.parse(decoder.decode(this._body));
        if (parsed && Object.hasOwn(parsed, "status")) {
          // No buffered second frame/trailing bytes can authorize ordinary egress.
          if (offset !== bytes.length || networkMonotonicNow() >= this._grantDeadline) { fail("invalid_contract"); }
          const reply = validateNetworkFailure(parsed, this._grant);
          const error = new VolparossaNetworkError(reply.status === "unavailable" ? "route_unavailable" : "denied");
          const remaining = reply.status === "unavailable" ? Math.min(5000,
            reply.direct_until_ms - Date.now(), this._grantDeadline - networkMonotonicNow()) : 0;
          networkDecisions.set(error, Object.freeze({ ...reply,
            monotonic_until_ms: networkMonotonicNow() + Math.max(0, remaining) }));
          this._reject(error);
          this.close(error.code);
          return;
        }
        const reply = validateNetworkReady(parsed, this._grant);
        if (networkMonotonicNow() >= this._grantDeadline) { fail("invalid_contract"); }
        this._body = null;
        this._attachStage = "ready-proxy";
        this._proxy = proxyService.newProxyInfo("http", reply.proxy_host, reply.proxy_port,
          reply.proxy_authorization, this._isolation,
          Ci.nsIProxyInfo.TRANSPARENT_PROXY_RESOLVES_HOST | Ci.nsIProxyInfo.ALWAYS_TUNNEL_VIA_PROXY,
          0, null);
        // Ready now means core route preparation succeeded, not that an origin
        // connection or payload has succeeded. Only now can openChannel proceed.
        this._ready = true;
        this._grant = { ...this._grant, capability: "" };
        clearTimeout(this._timer);
        this._timer = setTimeout(() => this.close("expired"),
          Math.min(reply.expires_at_ms - Date.now(), this._grantDeadline - networkMonotonicNow()));
        this._resolve();
      }
    }
  }
}
