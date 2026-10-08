// SPDX-License-Identifier: GPL-3.0-only
// Explicit parent-owned delivery of one prefetched public asset. No listener
// admission, startup enrollment, server, origin fetch or engine-byte witness.
import { validateExpected, validateResponse } from "./Contract.sys.mjs";
import { ID, VERSION, XPI_BYTES, XPI_SHA256 } from "./ActorContract.sys.mjs";
import { installAssetRedirectGuard } from "./AssetRedirect.sys.mjs";

const { AddonManager } = ChromeUtils.importESModule("resource://gre/modules/AddonManager.sys.mjs");
const { ExtensionParent } = ChromeUtils.importESModule("resource://gre/modules/ExtensionParent.sys.mjs");
const { setTimeout, clearTimeout } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
const HOST = "filters.volparossa.invalid";
const MAX_MS = 40_000;
let owner = null, opening = false, installed = false, poisoned = false;
const CODES = new Set(["asset_config", "asset_package", "asset_context", "asset_authority",
  "asset_expired", "asset_clock", "asset_busy", "asset_closed", "asset_failed", "asset_cleanup"]);
export class FilterAssetError extends Error {
  constructor(code) { super(CODES.has(code) ? code : "asset_failed"); this.code = this.message; }
}
function requireValue(value, code = "asset_config") { if (!value) throw new FilterAssetError(code); }
const exact = (value, keys) => value && typeof value === "object" && !Array.isArray(value)
  && Object.keys(value).sort().join() === [...keys].sort().join();
const natural = value => Number.isSafeInteger(value) && value >= 0;
function sha256(bytes) {
  const hash = Cc["@mozilla.org/security/hash;1"].createInstance(Ci.nsICryptoHash);
  hash.init(hash.SHA256); hash.update(bytes, bytes.length);
  return Array.from(hash.finish(false), byte => byte.charCodeAt(0).toString(16).padStart(2, "0")).join("");
}
export function assetKey(expected) {
  return `https://${HOST}/v1/${validateExpected(expected).manifest_id}.txt`;
}
function flags() {
  return Ci.nsICachingChannel.LOAD_NO_NETWORK_IO | Ci.nsICachingChannel.LOAD_BYPASS_LOCAL_CACHE
    | Ci.nsIRequest.INHIBIT_CACHING;
}
function abort(channel) {
  // Retain no-network flags even if a native cancel operation fails.
  try { channel.loadFlags |= flags(); } catch { poisoned = true; }
  try { channel.cancel(Cr.NS_BINDING_ABORTED); } catch { poisoned = true; }
}
function signed(addon) {
  requireValue(addon && addon.id === ID && addon.version === VERSION && addon.isActive
    && !addon.userDisabled && !addon.appDisabled && addon.signedState === AddonManager.SIGNEDSTATE_SIGNED
    && Services.prefs.getBoolPref("xpinstall.signatures.required"), "asset_package");
}
async function packageIdentity() {
  const addon = await AddonManager.getAddonByID(ID);
  signed(addon);
  const extension = ExtensionParent.GlobalManager.getExtension(ID);
  requireValue(extension?.manifest?.version === VERSION && extension.policy?.active === true
    && WebExtensionPolicy.getByID(ID) === extension.policy, "asset_package");
  const file = addon.getResourceURI("").QueryInterface(Ci.nsIJARURI).JARFile.QueryInterface(Ci.nsIFileURL).file;
  requireValue(!file.isSymlink() && file.isFile() && file.fileSize === XPI_BYTES, "asset_package");
  const bytes = await IOUtils.read(file.path, { maxBytes: XPI_BYTES + 1 });
  requireValue(bytes.length === XPI_BYTES && sha256(bytes) === XPI_SHA256, "asset_package");
  return { addon, extension };
}

// Once activated, reserve this virtual namespace for the entire process. Closing
// a lease leaves a denial guard, so late uBO requests cannot fall back to DNS.
const guard = {
  observe(subject, topic) {
    if (topic === "xpcom-shutdown") {
      try { owner?.close(); } catch { poisoned = true; }
      Services.obs.removeObserver(guard, "http-on-opening-request");
      Services.obs.removeObserver(guard, "xpcom-shutdown");
      installed = false; poisoned = true;
      return;
    }
    let channel;
    try {
      channel = subject.QueryInterface(Ci.nsIHttpChannel);
      if (channel.URI.asciiHost !== HOST) return;
    } catch { return; }
    try {
      channel.loadFlags |= flags();
      requireValue(!poisoned && owner !== null, "asset_closed");
      owner.accept(channel);
    } catch {
      if (owner) owner.denied();
      abort(channel);
    }
  },
};
function install() {
  if (installed) return;
  installAssetRedirectGuard({ transfer: transferOwned });
  Services.obs.addObserver(guard, "http-on-opening-request");
  try { Services.obs.addObserver(guard, "xpcom-shutdown"); }
  catch (error) {
    Services.obs.removeObserver(guard, "http-on-opening-request");
    throw error;
  }
  installed = true;
}
function transferOwned(oldChannel, newChannel) {
  return !poisoned && owner !== null && owner.transfer(oldChannel, newChannel) === true;
}

/** expected is independent browser configuration; fetched is the completed
 * Gecko broker fetch result, revalidated here before observing any request.
 * The two authority callbacks belong to the parent owner, never addon content.
 * This is one public-byte delivery lease, not proof that uBO loaded its engine.
 */
export async function openAssetChannel(options) {
  requireValue(exact(options, ["expected", "fetched", "assertCurrent", "authorize", "clock"])
    && [options.assertCurrent, options.authorize, options.clock].every(v => typeof v === "function")
    && Services.appinfo.processType === Ci.nsIXULRuntime.PROCESS_TYPE_DEFAULT);
  requireValue(!owner && !opening && !poisoned, "asset_busy");
  opening = true;
  const { assertCurrent, authorize, clock } = options;
  let identity = null, background = null, context = null, global = null;
  let last = null, start = null, lifetime = MAX_MS, timer = null, openingTimer = null, body = null;
  let closed = false, accepted = 0, completed = 0, denied = 0, record = null;
  // Parent-only progress enum, never request attributes or exception text.
  // It locates a failed native seam without making a delivery proof.
  let checkpoint = "ready";
  const sample = () => {
    let value;
    try { value = clock(); } catch { throw new FilterAssetError("asset_clock"); }
    requireValue(exact(value, ["bootMs", "wallMs"]) && natural(value.bootMs) && natural(value.wallMs)
      && (!last || value.bootMs >= last.bootMs && value.wallMs >= last.wallMs), "asset_clock");
    last = { ...value }; return last;
  };
  const current = () => {
    requireValue(!closed && !poisoned, "asset_closed");
    requireValue(assertCurrent() === true, "asset_context");
    requireValue(authorize() === true, "asset_authority");
    const now = sample();
    if (start) requireValue(Math.max(now.bootMs - start.bootMs, now.wallMs - start.wallMs) < lifetime,
      "asset_expired");
    if (identity) {
      signed(identity.addon);
      const ext = identity.extension;
      requireValue(ExtensionParent.GlobalManager.getExtension(ID) === ext && ext.policy.active
        && WebExtensionPolicy.getByID(ID) === ext.policy && ext.backgroundContext === background
        && background?.active === true && background.browsingContext === context
        // ESR140's parent proxy leaves innerWindowID at zero. Bind its native
        // actor instead; neither a copied ID nor a replacement global suffices.
        && context.currentWindowGlobal === global && background.actor?.manager === global
        && background.actor.browsingContext === context
        && global.documentPrincipal.addonPolicy === ext.policy, "asset_context");
    }
    return true;
  };
  const close = () => {
    if (closed) return;
    closed = true; body = null;
    if (timer !== null) { clearTimeout(timer); timer = null; }
    if (record) {
      record.done = true;
      for (const channel of record.channels) abort(channel);
      try { record.stream?.close(); } catch { poisoned = true; }
      record = null;
    }
    if (owner === lease) owner = null;
    if (poisoned) throw new FilterAssetError("asset_cleanup");
  };
  const validRequest = (channel, replacement = false) => {
    checkpoint = "request_current";
    current();
    const uri = channel.URI;
    const query = uri.spec.slice(key.length);
    checkpoint = "request_uri";
    requireValue(uri.spec === key || uri.spec.startsWith(key + "?_")
      && /^\?_=(?:0|[1-9][0-9]{0,4})$/.test(query) && Number(query.slice(3)) < 86413, "asset_context");
    const info = channel.loadInfo;
    checkpoint = "request_method";
    requireValue(channel.requestMethod === "GET" && (replacement || channel.originalURI.spec === uri.spec)
      && info.externalContentPolicyType === Ci.nsIContentPolicy.TYPE_XMLHTTPREQUEST, "asset_context");
    checkpoint = "request_context";
    requireValue(info.browsingContextID === context.id && info.innerWindowID === global.innerWindowId,
      "asset_context");
    checkpoint = "request_principals";
    requireValue([info.loadingPrincipal, info.triggeringPrincipal].every(principal => principal
        && !principal.isSystemPrincipal && principal.addonPolicy === identity.extension.policy
        && principal.equals(global.documentPrincipal)), "asset_context");
    checkpoint = "request_flags";
    requireValue((channel.loadFlags & (Ci.nsIChannel.LOAD_DOCUMENT_URI
      | Ci.nsIChannel.LOAD_BYPASS_SERVICE_WORKER | Ci.nsIRequest.LOAD_FROM_CACHE)) === 0
      && (channel.loadFlags & flags()) === flags()
      && Services.prefs.getBoolPref("dom.serviceWorkers.enabled"), "asset_context");
    let count = 0, bytes = 0;
    checkpoint = "request_headers";
    channel.visitRequestHeaders({ visitHeader(name, value) {
      count++; bytes += name.length + value.length;
      requireValue(count <= 32 && bytes <= 8192 && !/^(?:authorization|proxy-authorization|cookie|range|if-)/i.test(name),
        "asset_context");
    } });
  };
  let key;
  const lease = {
    close,
    denied() { denied = Math.min(denied + 1, 65535); },
    transfer(oldChannel, newChannel) {
      const old = oldChannel.QueryInterface(Ci.nsIHttpChannel);
      const next = newChannel.QueryInterface(Ci.nsIHttpChannel);
      requireValue(record && !record.done && record.prepared && !record.started
        && record.channels.has(old) && record.channels.size === 1, "asset_context");
      validRequest(old); validRequest(next, true);
      checkpoint = "transfer_identity";
      requireValue(next !== old && next.channelId === record.id && old.URI.spec === next.URI.spec,
        "asset_context");
      checkpoint = "transfer_callbacks";
      requireValue(next.notificationCallbacks === record.nativeRequestor
        && next.notificationCallbacks?.getInterface(Ci.nsINetworkInterceptController) === record.nativeController,
        "asset_context");
      checkpoint = "transfer";
      record.channels.add(next);
      return true;
    },
    accept(channel) {
      validRequest(channel);
      requireValue(accepted === 0 && record === null, "asset_busy");
      const prior = channel.notificationCallbacks;
      const request = { channels: new Set([channel]), id: channel.channelId, prepared: false,
        started: false, done: false, stream: null };
      requireValue(natural(request.id) && request.id > 0, "asset_context");
      const fail = () => { try { close(); } catch { /* no fallback */ } };
      const check = next => {
        requireValue(record === request && !request.done && owner === lease, "asset_closed");
        validRequest(next);
        requireValue(request.channels.has(next) && next.channelId === request.id
          && request.channels.size <= 2, "asset_context");
      };
      const callbacks = {
        QueryInterface: ChromeUtils.generateQI(["nsIInterfaceRequestor", "nsINetworkInterceptController"]),
        getInterface(iid) {
          if (iid.equals(Ci.nsINetworkInterceptController)) return this;
          if (prior) return prior.getInterface(iid);
          throw Cr.NS_ERROR_NO_INTERFACE;
        },
        shouldPrepareForIntercept(uri, next) {
          try {
            check(next);
            checkpoint = "prepare";
            requireValue(next === channel && !request.prepared && uri.spec === channel.URI.spec, "asset_context");
            request.prepared = true; return true;
          } catch { fail(); return false; }
        },
        channelIntercepted(intercepted) {
          let next;
          try {
            next = intercepted.channel.QueryInterface(Ci.nsIHttpChannel);
            check(next);
            checkpoint = "intercept";
            requireValue(request.prepared && !request.started, "asset_context");
            request.started = true;
            const stream = Cc["@mozilla.org/io/string-input-stream;1"].createInstance(Ci.nsIStringInputStream);
            stream.setByteStringData(body); request.stream = stream;
            intercepted.synthesizeStatus(200, "OK");
            intercepted.synthesizeHeader("Content-Type", "text/plain; charset=utf-8");
            intercepted.synthesizeHeader("Content-Length", String(body.length));
            intercepted.synthesizeHeader("Cache-Control", "no-store");
            intercepted.synthesizeHeader("X-Content-Type-Options", "nosniff");
            intercepted.startSynthesizedResponse(stream, {
              QueryInterface: ChromeUtils.generateQI(["nsIInterceptedBodyCallback"]),
              bodyComplete(status) {
                try {
                  check(next); checkpoint = "body_complete";
                  requireValue(Components.isSuccessCode(status), "asset_failed");
                  request.done = true; request.stream.close(); record = null; completed++;
                } catch { abort(next); fail(); }
              },
            }, null, "", false);
            intercepted.finishSynthesizedResponse();
          } catch {
            if (next) abort(next);
            try { intercepted.cancelInterception(Cr.NS_BINDING_ABORTED); } catch { /* already canceled below */ }
            fail();
          }
        },
      };
      record = request; accepted++;
      try {
        channel.notificationCallbacks = callbacks;
        // XPConnect returns native wrappers, not the assigned JS object. Bind
        // their exact identities before any native replacement can be admitted.
        request.nativeRequestor = channel.notificationCallbacks;
        request.nativeController = request.nativeRequestor?.getInterface(Ci.nsINetworkInterceptController);
        requireValue(request.nativeRequestor && request.nativeController
          && channel.notificationCallbacks === request.nativeRequestor, "asset_context");
      }
      catch (error) { fail(); throw error; }
    },
  };
  try {
    current(); start = last;
    const timedOut = new Promise((_, reject) => {
      openingTimer = setTimeout(() => {
        // An unresolved native package lookup/read has no join/cancel API.
        // Poison ownership; a late result must never install a delivery lease.
        poisoned = true;
        try { close(); } catch { /* denial remains permanent */ }
        reject(new FilterAssetError("asset_expired"));
      }, MAX_MS);
    });
    timedOut.catch(() => {});
    const expected = validateExpected(options.expected);
    requireValue(exact(options.fetched, ["snapshot", "filters", "manifest_hex"]));
    // Reuse the same hash/grammar/lifetime validation as the broker consumer;
    // this wrapper adapts its returned value, not a fabricated network receipt.
    const fetched = await Promise.race([timedOut,
      validateResponse({ version: 1, id: "asset", event: "snapshot", ...options.fetched },
        { id: "asset", operation: "fetch", expected, nowSeconds: () => Math.floor(sample().wallMs / 1000), sha256 })]);
    lifetime = Math.min(MAX_MS, fetched.snapshot.authorization_expires_unix_seconds * 1000 - start.wallMs);
    requireValue(Number.isSafeInteger(lifetime) && lifetime > 0, "asset_expired");
    body = fetched.filters; key = assetKey(expected);
    identity = await Promise.race([timedOut, packageIdentity()]);
    background = identity.extension.backgroundContext;
    context = background?.browsingContext; global = context?.currentWindowGlobal;
    requireValue(background?.isBackgroundContext === true && context?.parent === null && global
      && natural(context.id) && context.id > 0 && natural(global.innerWindowId) && global.innerWindowId > 0,
    "asset_context");
    current(); install(); owner = lease;
    const poll = () => {
      timer = null;
      try { current(); timer = setTimeout(poll, Math.min(250, lifetime - Math.max(
        last.bootMs - start.bootMs, last.wallMs - start.wallMs))); }
      catch { try { close(); } catch { /* poisoned guard continues to deny */ } }
    };
    poll(); requireValue(!closed, "asset_closed");
    return Object.freeze({ key, get status() { return Object.freeze({ closed, accepted, completed, denied }); },
      get diagnostic() { return Object.freeze({ checkpoint }); }, close });
  } catch (error) {
    try { close(); } catch { throw new FilterAssetError("asset_cleanup"); }
    throw error instanceof FilterAssetError ? error : new FilterAssetError("asset_failed");
  } finally {
    if (openingTimer !== null) clearTimeout(openingTimer);
    opening = false;
  }
}
