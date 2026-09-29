// SPDX-License-Identifier: GPL-3.0-only
// Privileged, operator-authorized single-authority adapter. Not a global proxy.
import { setTimeout, clearTimeout } from "resource://gre/modules/Timer.sys.mjs";

const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });
const MAX_FRAME = 4096;
const HEX = /^[0-9a-f]{64}$/;
const attachments = new Set();
const channels = new WeakMap();
const proxyService = Cc["@mozilla.org/network/protocol-proxy-service;1"]
  .getService(Ci.nsIProtocolProxyService);

export class VolparossaNetworkError extends Error {
  constructor(code) { super(code); this.code = code; }
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
      fail("unavailable");
    }
    const grant = validateNetworkGrant(input);
    const file = Cc["@mozilla.org/file/local;1"].createInstance(Ci.nsIFile);
    file.initWithPath(grant.app_socket);
    let attachment;
    try {
      const service = Cc["@mozilla.org/network/socket-transport-service;1"].getService(Ci.nsISocketTransportService);
      attachment = new VolparossaNetwork(service.createUnixDomainTransport(file), grant);
      await attachment.ready;
      return attachment;
    } catch (error) {
      attachment?.close();
      throw error instanceof VolparossaNetworkError ? error : new VolparossaNetworkError("unavailable");
    }
  }

  constructor(transport, grant) {
    this._grant = grant;
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
    this._timer = setTimeout(() => this.close("unavailable"), Math.min(10000, grant.expires_at_ms - Date.now()));
    transport.setTimeout(Ci.nsISocketTransport.TIMEOUT_CONNECT, 5);
    this._input = transport.openInputStream(0, 0, 0);
    this._output = transport.openOutputStream(0, 0, 0).QueryInterface(Ci.nsIAsyncOutputStream);
    this._pump = Cc["@mozilla.org/network/input-stream-pump;1"].createInstance(Ci.nsIInputStreamPump);
    this._pump.init(this._input, 0, 0, true);
    this._pump.asyncRead(this);
    const payload = encoder.encode(JSON.stringify({ version: 1, capability: grant.capability, partition: grant.partition }));
    this._frame = new Uint8Array(4 + payload.length);
    new DataView(this._frame.buffer).setUint32(0, payload.length);
    this._frame.set(payload, 4);
    this._offset = 0;
    if (!attachments.size) { proxyService.registerChannelFilter(filter, 0xffffffff); }
    attachments.add(this);
    try { this._flush(); } catch { this.close("unavailable"); }
  }

  QueryInterface = ChromeUtils.generateQI(["nsIStreamListener", "nsIRequestObserver", "nsIOutputStreamCallback"]);

  get active() { return this._ready && !this._closed; }

  _checkChannel(channel) {
    const uri = channel.URI;
    if (!this.active || Date.now() >= this._grant.expires_at_ms || !uri.schemeIs("https") ||
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

  close(code = "detached") {
    if (this._closed) { return; }
    this._closed = true;
    clearTimeout(this._timer);
    this._reject(new VolparossaNetworkError(code));
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
    const bytes = this._frame.subarray(this._offset);
    let written;
    try { written = this._output.write(String.fromCharCode(...bytes), bytes.length); }
    catch (error) {
      if (error.result !== Cr.NS_BASE_STREAM_WOULD_BLOCK) { throw error; }
      written = 0;
    }
    this._offset += written;
    if (this._offset === this._frame.length) { this._frame = null; }
    else { this._output.asyncWait(this, 0, 0, Services.tm.currentThread); }
  }
  onOutputStreamReady() { try { this._flush(); } catch { this.close("unavailable"); } }
  onStartRequest() {}
  onStopRequest() { this.close("unavailable"); }
  onDataAvailable(_request, input, _offset, count) {
    const binary = Cc["@mozilla.org/binaryinputstream;1"].createInstance(Ci.nsIBinaryInputStream);
    binary.setInputStream(input);
    try {
      while (count && !this._closed) {
        const length = Math.min(count, 4096);
        this._consume(new Uint8Array(binary.readByteArray(length)));
        count -= length;
      }
    } catch { this.close("invalid_contract"); }
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
        const reply = validateNetworkReady(JSON.parse(decoder.decode(this._body)), this._grant);
        this._body = null;
        this._proxy = proxyService.newProxyInfo("http", reply.proxy_host, reply.proxy_port,
          reply.proxy_authorization, this._isolation,
          Ci.nsIProxyInfo.TRANSPARENT_PROXY_RESOLVES_HOST | Ci.nsIProxyInfo.ALWAYS_TUNNEL_VIA_PROXY,
          0, null);
        this._ready = true;
        this._grant = { ...this._grant, capability: "" };
        clearTimeout(this._timer);
        this._timer = setTimeout(() => this.close("expired"), reply.expires_at_ms - Date.now());
        this._resolve();
      }
    }
  }
}
