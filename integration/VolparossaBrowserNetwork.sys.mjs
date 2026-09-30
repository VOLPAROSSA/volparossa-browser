// SPDX-License-Identifier: GPL-3.0-only
// Privileged ordinary-channel integration, not an OS or complete browser kill switch.
import { VolparossaNetwork, VolparossaNetworkError, validateNetworkGrant, getNetworkDecision, networkMonotonicNow }
  from "./VolparossaNetwork.sys.mjs";

const controllers = new Set();
const owned = new WeakMap();
const proxyService = Cc["@mozilla.org/network/protocol-proxy-service;1"]
  .getService(Ci.nsIProtocolProxyService);
let registered = false;

function blocked(channel) {
  channel.cancel(Cr.NS_ERROR_ABORT);
  throw Components.Exception("VOLPAROSSA_BROWSER_REQUEST_BLOCKED", Cr.NS_ERROR_ABORT);
}
function requireThat(condition, code) {
  if (!condition) { throw new VolparossaNetworkError(code); }
}
function authority(channel) {
  const uri = channel.URI;
  requireThat(uri.schemeIs("https") && !uri.userPass, "scope_unavailable");
  const hostname = uri.asciiHost;
  const port = uri.port === -1 ? 443 : uri.port;
  requireThat(typeof hostname === "string" && hostname.includes(".") && !/^[0-9.]+$/.test(hostname)
    && Number.isInteger(port) && port >= 1 && port <= 65535, "scope_unavailable");
  return `${hostname}:${port}`;
}
function contextFor(channel) {
  const info = channel.loadInfo;
  const context = info.browsingContext ?? info.associatedBrowsingContext;
  return context?.top ?? null;
}
function attributes(context) {
  // Container/private boundaries are native parent-owned BrowsingContext facts,
  // never identifiers accepted from a content script or page request header.
  const attrs = context.originAttributes;
  const privateBrowsingId = attrs.privateBrowsingId ?? 0;
  const userContextId = attrs.userContextId ?? 0;
  requireThat(Number.isInteger(privateBrowsingId) && [0, 1].includes(privateBrowsingId)
    && Number.isInteger(userContextId) && userContextId >= 0, "invalid_browser_context");
  return { privateBrowsingId, userContextId };
}

const filter = {
  QueryInterface: ChromeUtils.generateQI(["nsIProtocolProxyChannelFilter"]),
  applyFilter(channel, original, callback) {
    let controller = owned.get(channel);
    if (!controller) {
      let context;
      try { context = contextFor(channel); } catch { context = null; }
      if (context) {
        for (const candidate of controllers) {
          if (candidate._matches(context)) { controller = candidate; break; }
        }
      }
    }
    if (!controller) { callback.onProxyFilterResult(original); return; }
    try {
      const selected = controller._select(channel, original);
      callback.onProxyFilterResult(selected);
    } catch { blocked(channel); }
  },
};

/** Explicit owner-managed routing for one browser element's ordinary HTTPS loads.
 * Defaults to inactive + killSwitch off, preserving normal Firefox networking.
 * No grant file is auto-loaded; no privilege or capability is exposed to page JS.
 */
export class VolparossaBrowserNetwork {
  static bind(browser, { killSwitch = false } = {}) {
    requireThat(Services.appinfo.processType === Ci.nsIXULRuntime.PROCESS_TYPE_DEFAULT
      && browser?.localName === "browser" && browser.ownerGlobal?.gBrowser
      && browser.browsingContext?.top === browser.browsingContext
      && typeof killSwitch === "boolean" && controllers.size < 32, "invalid_browser_context");
    requireThat(![...controllers].some(controller => controller._browser === browser), "browser_already_bound");
    const controller = new VolparossaBrowserNetwork(browser, killSwitch);
    if (!registered) {
      // Immediately before the attachment's final highest-priority scope check.
      proxyService.registerChannelFilter(filter, 0xfffffffe);
      registered = true;
    }
    controllers.add(controller);
    return controller;
  }

  constructor(browser, killSwitch) {
    this._browser = browser;
    this._attributes = attributes(browser.browsingContext);
    this._killSwitch = killSwitch;
    this._enabled = false;
    this._closed = false;
    this._routes = new Map();
    this._lastState = "ordinary_internet";
    this._partition = null;
  }

  get status() {
    return Object.freeze({ version: 1, active: this._enabled, kill_switch: this._killSwitch,
      state: this._lastState, scope: "bound_browser_https_channels",
      full_browser_kill_switch: false, automatic_grant_renewal: false });
  }

  setKillSwitch(value) {
    requireThat(typeof value === "boolean" && !this._closed, "invalid_contract");
    this._killSwitch = value;
    this._lastState = value ? "new_unprotected_requests_blocked" : "fallback_requires_authority";
    // Does not migrate existing flows or claim that WebRTC/background traffic is stopped.
  }

  async authorize(input) {
    requireThat(!this._closed, "scope_unavailable");
    this._enabled = true;
    this._lastState = "preparing";
    const grant = validateNetworkGrant(input);
    requireThat(this._partition === null || this._partition === grant.partition, "partition_mismatch");
    const key = `${grant.hostname}:${grant.port}`;
    requireThat(!this._routes.has(key) && this._routes.size < 8, "scope_already_bound");
    this._partition = grant.partition;
    const entry = { type: "pending", grant, attachment: null, decision: null,
      deadline: networkMonotonicNow() + grant.expires_at_ms - Date.now() };
    this._routes.set(key, entry);
    try {
      const attachment = await VolparossaNetwork.attach(grant);
      if (this._closed) { attachment.close(); throw new VolparossaNetworkError("scope_unavailable"); }
      entry.type = "overlay";
      entry.attachment = attachment;
      this._lastState = "overlay_ready";
    } catch (error) {
      entry.type = "blocked";
      entry.decision = getNetworkDecision(error);
      this._lastState = "blocked";
      // A trusted bounded core refusal may permit a new ordinary request; EOF,
      // timeout, malformed messages and arbitrary unavailable exceptions cannot.
      if (!this._closed && entry.decision?.status === "unavailable" &&
          entry.decision.reason === "no_eligible_paths") {
        entry.type = "fallback";
        this._lastState = this._killSwitch ? "blocked" : "ordinary_fallback_authorized";
      }
      if (entry.type !== "fallback") { throw error; }
    }
    return this.status;
  }

  _matches(context) {
    // The browser element survives process switches; compare its CURRENT top context.
    return context === this._browser.browsingContext?.top;
  }

  _select(channel, original) {
    if (this._closed) { throw new VolparossaNetworkError("scope_unavailable"); }
    const context = contextFor(channel);
    requireThat(context && this._matches(context), "scope_unavailable");
    const attrs = attributes(context);
    requireThat(attrs.privateBrowsingId === this._attributes.privateBrowsingId
      && attrs.userContextId === this._attributes.userContextId, "scope_unavailable");
    if (!this._enabled && !this._killSwitch) { return original; }
    const key = authority(channel);
    const entry = this._routes.get(key);
    requireThat(entry && Date.now() < entry.grant.expires_at_ms && networkMonotonicNow() < entry.deadline, "scope_unavailable");
    if (entry.type === "overlay") {
      // Once this channel has an overlay owner, it can never become a direct retry.
      if (owned.has(channel)) { entry.attachment._checkChannel(channel); return entry.attachment._proxy; }
      const proxy = entry.attachment.adoptChannel(channel);
      owned.set(channel, this);
      this._lastState = "overlay";
      return proxy;
    }
    requireThat(!owned.has(channel) && !this._killSwitch && entry.type === "fallback"
      && entry.decision?.status === "unavailable" && Date.now() < entry.decision.direct_until_ms
      && networkMonotonicNow() < entry.decision.monotonic_until_ms,
      "scope_unavailable");
    const http = channel.QueryInterface(Ci.nsIHttpChannel);
    requireThat(["GET", "HEAD"].includes(http.requestMethod), "fallback_requires_new_safe_request");
    try { http.getRequestHeader("Proxy-Authorization"); throw new VolparossaNetworkError("invalid_channel"); }
    catch (error) { if (error instanceof VolparossaNetworkError) { throw error; } }
    this._lastState = "ordinary_internet";
    return original; // Respect the owner's existing Firefox proxy, not an implicit DIRECT chain.
  }

  close() {
    this._closed = true;
    this._enabled = true;
    this._lastState = "blocked";
    for (const entry of this._routes.values()) { entry.attachment?.close(); }
    // Retain this guard for the still-live browser: closing a socket is NOT consent
    // to ordinary networking. Owner code may explicitly release the binding below.
  }

  release({ allowOrdinaryInternet = false } = {}) {
    requireThat(allowOrdinaryInternet === true || !this._browser.isConnected, "ordinary_internet_ack_required");
    this.close();
    controllers.delete(this);
  }
}
