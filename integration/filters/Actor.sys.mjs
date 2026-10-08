// SPDX-License-Identifier: GPL-3.0-only
// Explicit parent-owned adapter, NOT a startup owner, publication approval,
// immutable-key rotation or a WebExtension API. Never exposes a page/browser.
import { ACTOR, ID, VERSION, XPI_BYTES, XPI_SHA256, DOCUMENT, DEADLINE_MS,
  demand, closedError, FilterActorError, operation, validateKey, makeDeadline } from "./ActorContract.sys.mjs";
import { bindSelectionActor } from "./VolparossaFilterSelectionParent.sys.mjs";

const { setTimeout, clearTimeout } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
const { AddonManager } = ChromeUtils.importESModule("resource://gre/modules/AddonManager.sys.mjs");
const { ExtensionParent } = ChromeUtils.importESModule("resource://gre/modules/ExtensionParent.sys.mjs");
let active = null;

function signed(addon) {
  demand(addon && addon.id === ID && addon.isActive && !addon.userDisabled && !addon.appDisabled
    && addon.version === VERSION && addon.signedState === AddonManager.SIGNEDSTATE_SIGNED
    && Services.prefs.getBoolPref("xpinstall.signatures.required"), "actor_package");
}
async function exactAddon() {
  const addon = await AddonManager.getAddonByID(ID);
  signed(addon);
  const extension = ExtensionParent.GlobalManager.getExtension(ID);
  demand(extension?.manifest?.version === VERSION && extension.policy?.active === true
    && WebExtensionPolicy.getByID(ID) === extension.policy, "actor_package");
  const file = addon.getResourceURI("").QueryInterface(Ci.nsIJARURI).JARFile.QueryInterface(Ci.nsIFileURL).file;
  demand(!file.isSymlink() && file.isFile() && file.fileSize === XPI_BYTES, "actor_package");
  const bytes = await IOUtils.read(file.path, { maxBytes: XPI_BYTES + 1 });
  demand(bytes.length === XPI_BYTES, "actor_package");
  const hash = Cc["@mozilla.org/security/hash;1"].createInstance(Ci.nsICryptoHash);
  hash.init(hash.SHA256);
  hash.update(bytes, bytes.length);
  const digest = Array.from(hash.finish(false), byte => byte.charCodeAt(0).toString(16).padStart(2, "0")).join("");
  demand(digest === XPI_SHA256, "actor_package");
  signed(addon);
  demand(ExtensionParent.GlobalManager.getExtension(ID) === extension && extension.policy.active
    && WebExtensionPolicy.getByID(ID) === extension.policy, "actor_package");
  return extension;
}

// assertCurrent(): synchronous owner/generation identity, NOT caller-supplied ID.
// authorize(operation): synchronous independent grant for this bound key. Removal
// needs retained key ownership, not an unexpired publication grant. The owner must
// close on revocation/replacement; checks cannot make cross-process side effects
// atomic or undo an already-sent original-uBO mutation. clock() samples bootMs
// (suspend-inclusive) then wallMs. Command receipts are not content-byte proofs.
export async function openSelectionActor({ key, assertCurrent, authorize, clock }) {
  validateKey(key);
  demand(!active && [assertCurrent, authorize, clock].every(value => typeof value === "function")
    && Services.appinfo.processType === Ci.nsIXULRuntime.PROCESS_TYPE_DEFAULT);
  const owner = Object.freeze({});
  active = owner;
  let extension = null;
  let page = null;
  let bound = null;
  let registered = false;
  let creatingBrowser = false;
  let cleanupFailed = false;
  let closed = false;
  let busy = false;
  let pendingReject = null;
  let deadline = null;
  let commandOperation = "observe";
  let last = null;
  const sample = () => {
    const value = clock();
    demand(value && [value.bootMs, value.wallMs].every(n => Number.isSafeInteger(n) && n >= 0)
      && (!last || (value.bootMs >= last.bootMs && value.wallMs >= last.wallMs)), "actor_clock");
    last = { bootMs: value.bootMs, wallMs: value.wallMs };
    return last;
  };
  const stop = () => {
    closed = true;
    const reject = pendingReject;
    pendingReject = null;
    reject?.(new FilterActorError("actor_closed"));
    // HiddenExtensionPage.shutdown() marks an in-progress creation unloaded,
    // but cannot join/release its SharedWindow acquisition until creation
    // settles. Keep the singleton poisoned rather than permit overlapping
    // owners while that native cleanup is still unknown.
    let failed = creatingBrowser;
    try { bound?.close(); } catch { failed = true; }
    try { if (page && !page.unloaded) page.shutdown(); } catch { failed = true; }
    try {
      if (registered) ChromeUtils.unregisterWindowActor(ACTOR);
      registered = false;
    } catch { failed = true; }
    cleanupFailed ||= failed;
    if (!cleanupFailed && active === owner) active = null;
    if (cleanupFailed) throw new FilterActorError("actor_cleanup");
  };
  const guard = () => {
    demand(!closed, "actor_closed");
    demand(assertCurrent() === true, "actor_context");
    if (busy) demand(authorize(commandOperation) === true, "actor_authority");
    if (extension) demand(extension.policy.active && ExtensionParent.GlobalManager.getExtension(ID) === extension
      && WebExtensionPolicy.getByID(ID) === extension.policy, "actor_context");
    deadline?.check();
    return true;
  };
  const limited = async action => {
    demand(!busy && !closed, "actor_busy");
    busy = true;
    let timer;
    try {
      deadline = makeDeadline(sample);
      guard();
      const cancellation = new Promise((_, reject) => { pendingReject = reject; });
      cancellation.catch(() => {}); // also handled if scheduling itself fails
      // Register the timeout before any I/O, including addon lookup/hash/open.
      timer = setTimeout(() => {
        const reject = pendingReject;
        pendingReject = null;
        reject?.(new FilterActorError("actor_deadline"));
        try { stop(); } catch { /* operation is already closed */ }
      }, DEADLINE_MS);
      const work = Promise.resolve().then(action);
      const value = await Promise.race([work, cancellation]);
      guard();
      return value;
    } catch (error) {
      try { stop(); } catch { throw new FilterActorError("actor_cleanup"); }
      throw closedError(error);
    } finally {
      pendingReject = null;
      clearTimeout(timer);
      deadline = null;
      busy = false;
    }
  };
  try {
    await limited(async () => {
      extension = await exactAddon();
      guard();
      const documentURL = extension.policy.getURL(DOCUMENT);
      ChromeUtils.registerWindowActor(ACTOR, {
        parent: { esModuleURI: new URL("./VolparossaFilterSelectionParent.sys.mjs", import.meta.url).href },
        child: { esModuleURI: new URL("./VolparossaFilterSelectionChild.sys.mjs", import.meta.url).href },
        matches: [documentURL], allFrames: false, messageManagerGroups: ["webext-browsers"],
      });
      registered = true;
      page = new ExtensionParent.HiddenExtensionPage(extension, "tab");
      let browser;
      creatingBrowser = true;
      try { browser = await page.createBrowserElement(); }
      finally { creatingBrowser = false; }
      if (closed) { if (!page.unloaded) page.shutdown(); demand(false, "actor_closed"); }
      guard();
      ExtensionParent.apiManager.emit("extension-browser-inserted", browser);
      browser.fixupAndLoadURIString(documentURL, { triggeringPrincipal: extension.principal });
      let manager;
      for (let count = 0; count < 240; count++) {
        guard();
        manager = browser.browsingContext.currentWindowGlobal;
        if (manager?.documentURI.spec === documentURL && manager.documentPrincipal.addonId === ID
          && !browser.webProgress.isLoadingDocument) break;
        await new Promise(resolve => setTimeout(resolve, 50));
      }
      guard();
      demand(manager?.documentURI.spec === documentURL && !browser.webProgress.isLoadingDocument, "actor_deadline");
      bound = bindSelectionActor(manager.getActor(ACTOR), browser, { key, documentURL, policy: extension.policy, current: guard });
      bound.current();
    });
    return Object.freeze({
      async command(op) {
        operation(op); // reject unknown operations before any addon or actor I/O
        demand(!busy && !closed, "actor_busy");
        commandOperation = op;
        return limited(async () => {
          demand(await exactAddon() === extension, "actor_package");
          guard();
          const value = await bound.command(op, deadline.wallDeadline);
          guard();
          demand(await exactAddon() === extension, "actor_package");
          guard();
          bound.current();
          return value;
        });
      },
      current() {
        // Idle identity is not a renewed grant for the previous operation. This
        // permits independently authorized removal after add authority expires.
        try { guard(); return bound.current(); }
        catch { try { stop(); } catch { /* false still refuses authority */ } return false; }
      },
      close: stop,
    });
  } catch (error) {
    try { stop(); } catch { throw new FilterActorError("actor_cleanup"); }
    throw closedError(error);
  }
}
