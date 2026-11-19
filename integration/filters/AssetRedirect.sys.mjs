// SPDX-License-Identifier: GPL-3.0-only
// Explicit process-lifetime redirect denial for the virtual asset namespace.
// No Admission bypass, URL grant, server or network capability is created here.
// Firefox's REDIRECT_TRANSPARENT skips category sinks (native
// nsAsyncRedirectVerifyHelper::Run); this is NOT an all-redirect/no-DNS proof.
const HOST = "filters.volparossa.invalid";
const CATEGORY = "net-channel-event-sinks";
const CONTRACT = "@volparossa.org/filter-asset-redirect-guard;1";
const CID = "{8a88c6df-2a09-46d5-8b39-5d7f508479ec}";
let installed = null, poisoned = false;

export class FilterAssetRedirectError extends Error {
  constructor(code) {
    super(["redirect_config", "redirect_failed", "redirect_cleanup"].includes(code) ? code : "redirect_failed");
    this.code = this.message;
  }
}
function requireValue(value) { if (!value) throw new FilterAssetRedirectError("redirect_config"); }
function reserved(channel) {
  const uri = channel.URI;
  // Hostless file/data/about redirects are unrelated, not parse failures. Do
  // not inspect/log any path, query, headers or principal on unrelated traffic.
  return ["http", "https", "ws", "wss"].includes(uri.scheme) && uri.asciiHost === HOST;
}
function cancel(channel) {
  try {
    channel.loadFlags |= Ci.nsICachingChannel.LOAD_NO_NETWORK_IO
      | Ci.nsICachingChannel.LOAD_BYPASS_LOCAL_CACHE | Ci.nsIRequest.INHIBIT_CACHING;
  } catch { poisoned = true; }
  try { channel.cancel(Cr.NS_BINDING_ABORTED); } catch { poisoned = true; }
}

/** Call once from the parent asset owner; installation is not auto-startup.
 * transfer(oldChannel, newChannel) is synchronous and parent-private. It must
 * verify the existing owned native request, authority, principals, context and
 * channel ID, then record its one native replacement. It is called ONLY for an
 * exact-URI HTTPS internal redirect within this fixed namespace. true permits
 * that transfer, never a foreign redirect or a normal Admission operation.
 * newChannel.originalURI is not yet set at this native notification (IDL).
 */
export function installAssetRedirectGuard(options) {
  requireValue(options && typeof options === "object" && !Array.isArray(options)
    && Object.keys(options).join() === "transfer" && typeof options.transfer === "function"
    && Services.appinfo.processType === Ci.nsIXULRuntime.PROCESS_TYPE_DEFAULT && !poisoned);
  const { transfer } = options;
  if (installed) { requireValue(installed.transfer === transfer); return; }
  const registrar = Components.manager.QueryInterface(Ci.nsIComponentRegistrar);
  const cid = Components.ID(CID);
  requireValue(!registrar.isCIDRegistered(cid) && !registrar.isContractIDRegistered(CONTRACT));
  let found = false;
  try { Services.catMan.getCategoryEntry(CATEGORY, CONTRACT); found = true; }
  catch (error) {
    if (error.result !== Cr.NS_ERROR_NOT_AVAILABLE) throw new FilterAssetRedirectError("redirect_failed");
  }
  requireValue(!found);
  let factoryAttempted = false, categoryAttempted = false, observerAttempted = false;
  const state = { transfer, closed: false };
  const sink = {
    QueryInterface: ChromeUtils.generateQI(["nsIChannelEventSink", "nsIFactory"]),
    createInstance(iid) { return this.QueryInterface(iid); },
    asyncOnChannelRedirect(oldChannel, newChannel, flags, callback) {
      let allowed = false;
      try {
        const oldReserved = reserved(oldChannel), newReserved = reserved(newChannel);
        if (!oldReserved && !newReserved) allowed = true;
        else if (!state.closed && !poisoned && oldReserved && newReserved
            && flags === Ci.nsIChannelEventSink.REDIRECT_INTERNAL
            && oldChannel.URI.scheme === "https" && newChannel.URI.scheme === "https"
            && oldChannel.URI.spec === newChannel.URI.spec) {
          allowed = transfer(oldChannel, newChannel) === true;
        }
      } catch { /* malformed/native failure is a closed veto, never formatted */ }
      if (!allowed) {
        // Veto alone can keep the original 3xx response loading. Cancel both;
        // never reset interception or fall back to that response's body.
        cancel(oldChannel); cancel(newChannel);
      }
      // Native permits a synchronous callback. Never put it inside a retrying
      // catch or call it twice if it throws; the native helper owns propagation.
      try { callback.onRedirectVerifyCallback(allowed ? Cr.NS_OK : Cr.NS_BINDING_ABORTED); }
      catch { throw Cr.NS_BINDING_ABORTED; }
    },
  };
  const shutdown = {
    observe(_subject, topic) {
      if (topic !== "xpcom-shutdown" || state.closed) return;
      state.closed = true; poisoned = true;
      // This is process shutdown only, not a caller-visible way to drop the
      // permanent denial guard when an individual asset lease closes.
      try { Services.catMan.deleteCategoryEntry(CATEGORY, CONTRACT, false); } catch { /* already shutting down */ }
      try { registrar.unregisterFactory(cid, sink); } catch { /* already shutting down */ }
      try { Services.obs.removeObserver(shutdown, "xpcom-shutdown"); } catch { /* already shutting down */ }
      installed = null;
    },
  };
  try {
    // Native registration pattern: WebRequest.sys.mjs ChannelEventSink and
    // netwerk/test/unit/test_redirect_veto.js. Persist=false, replace=false.
    factoryAttempted = true;
    registrar.registerFactory(cid, "VOLPAROSSA fixed asset redirect guard", CONTRACT, sink);
    categoryAttempted = true;
    Services.catMan.addCategoryEntry(CATEGORY, CONTRACT, CONTRACT, false, false);
    observerAttempted = true;
    Services.obs.addObserver(shutdown, "xpcom-shutdown");
    installed = state;
  } catch {
    state.closed = true; poisoned = true; // uncertain installation never retries
    let failed = false;
    if (observerAttempted) try { Services.obs.removeObserver(shutdown, "xpcom-shutdown"); } catch { failed = true; }
    if (categoryAttempted) try { Services.catMan.deleteCategoryEntry(CATEGORY, CONTRACT, false); } catch { failed = true; }
    if (factoryAttempted) try { registrar.unregisterFactory(cid, sink); } catch { failed = true; }
    throw new FilterAssetRedirectError(failed ? "redirect_cleanup" : "redirect_failed");
  }
}
