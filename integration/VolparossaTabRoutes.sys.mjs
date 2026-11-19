// SPDX-License-Identifier: GPL-3.0-only
import { VolparossaBrowserNetwork } from "./VolparossaBrowserNetwork.sys.mjs";
import { validateNetworkGrant, networkMonotonicNow } from "./VolparossaNetwork.sys.mjs";

// permanentKey follows the same native tab across window moves and process swaps.
const sessions = new WeakMap();
const states = new Map([
  ["preparing", "preparing"], ["overlay_ready", "ready"], ["overlay", "ready"],
  ["ordinary_fallback_authorized", "fallback"], ["ordinary_internet", "fallback"],
]);

function chosenTab(tab) {
  return tab?.isConnected && !tab.closing && !tab.multiselected &&
    tab.ownerDocument.defaultView?.gBrowser?.getTabForBrowser(tab.linkedBrowser) === tab;
}

function updateMenu(tab) {
  const doc = tab.ownerDocument;
  const entry = sessions.get(tab.permanentKey);
  const expired = entry?.owner && (Date.now() >= entry.expires ||
    networkMonotonicNow() >= entry.deadline);
  const state = expired ? "blocked" : entry?.owner?.status.state;
  const label = state ? states.get(state) ?? "blocked" : entry?.state ?? "ordinary";
  doc.l10n.setAttributes(doc.getElementById("volparossa-tab-route-status"),
    "volparossa-tab-route-" + label);
  doc.getElementById("volparossa-tab-route-connect").disabled =
    !chosenTab(tab) || !!entry?.owner || entry?.state === "choosing";
  doc.getElementById("volparossa-tab-route-release").disabled =
    !chosenTab(tab) || !entry?.owner;
}

function refresh(tab) {
  if (tab.isConnected && tab.ownerDocument.defaultView.TabContextMenu?.contextTab === tab) {
    updateMenu(tab);
  }
}

function watchTab(tab, key, entry) {
  const win = tab.ownerDocument.defaultView;
  const stop = () => {
    tab.removeEventListener("TabClose", onClose);
    win.removeEventListener("unload", onUnload);
  };
  const retire = () => {
    if (sessions.get(key) !== entry) return;
    entry.owner.close();
    entry.owner.release();
    sessions.delete(key);
  };
  const onClose = event => {
    stop();
    if (event.detail?.adoptedBy) {
      watchTab(event.detail.adoptedBy, key, entry);
    } else {
      retire();
    }
  };
  const onUnload = () => {
    stop();
    entry.owner.close();
    Services.tm.dispatchToMainThread(retire);
  };
  tab.addEventListener("TabClose", onClose);
  win.addEventListener("unload", onUnload);
  entry.stop = stop;
}

async function connect(tab) {
  if (!chosenTab(tab)) return;
  const key = tab.permanentKey;
  const old = sessions.get(key);
  if (!key || old?.owner || old?.state === "choosing") return;
  const entry = { state: "choosing", owner: null };
  sessions.set(key, entry);
  const browser = tab.linkedBrowser;
  const current = () => chosenTab(tab) && tab.linkedBrowser === browser &&
    tab.permanentKey === key && sessions.get(key) === entry;
  try {
    const title = await tab.ownerDocument.l10n.formatValue("volparossa-tab-route-picker");
    if (!current()) return;
    const picker = Cc["@mozilla.org/filepicker;1"].createInstance(Ci.nsIFilePicker);
    picker.init(tab.ownerDocument.defaultView.browsingContext, title, Ci.nsIFilePicker.modeOpen);
    picker.addToRecentDocs = false;
    picker.appendFilters(Ci.nsIFilePicker.filterAll);
    if (await new Promise(resolve => picker.open(resolve)) !== Ci.nsIFilePicker.returnOK || !current()) return;
    const bytes = await IOUtils.readVolparossaGrant(picker.file.path);
    let grant;
    try {
      grant = validateNetworkGrant(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)));
    } finally {
      bytes.fill(0);
    }
    if (!current()) return;
    const [heading, message] = await tab.ownerDocument.l10n.formatValues([
      { id: "volparossa-tab-route-confirm-title" },
      { id: "volparossa-tab-route-confirm", args: { authority: grant.hostname + ":" + grant.port } },
    ]);
    if (!current() || !Services.prompt.confirm(tab.ownerDocument.defaultView, heading, message) || !current()) return;
    entry.owner = VolparossaBrowserNetwork.bind(browser);
    entry.expires = grant.expires_at_ms;
    entry.deadline = networkMonotonicNow() + grant.expires_at_ms - Date.now();
    watchTab(tab, key, entry);
    await entry.owner.authorize(grant);
  } catch {
    // Only fixed public status is exposed. An authorization refusal keeps its
    // controller and guard until the user explicitly releases this tab.
    entry.state = "invalid";
  } finally {
    if (!entry.owner && entry.state === "choosing" && sessions.get(key) === entry) sessions.delete(key);
    refresh(tab);
  }
}

export const VolparossaTabRoutes = {
  update(tab) {
    const menu = tab.ownerDocument.getElementById("context_volparossaTabRoute");
    menu.hidden = !chosenTab(tab) || Services.appinfo.OS !== "Linux";
    if (!menu.hidden) updateMenu(tab);
  },
  connect,
  release(tab) {
    if (!chosenTab(tab)) return;
    const entry = sessions.get(tab.permanentKey);
    if (!entry?.owner) return;
    entry.owner.release({ allowOrdinaryInternet: true });
    entry.stop();
    sessions.delete(tab.permanentKey);
    refresh(tab);
  },
};
