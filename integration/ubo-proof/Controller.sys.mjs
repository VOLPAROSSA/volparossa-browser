// SPDX-License-Identifier: GPL-3.0-only
// Explicitly bootstrapped disposable-profile experiment, not a product startup hook.
import { setTimeout, clearTimeout } from "resource://gre/modules/Timer.sys.mjs";
import {
  ACTOR, ID, VERSION, XPI_BYTES, XPI_SHA256, ProofError, enrollOnce, journal, requireProof, step,
} from "./Contract.sys.mjs";
import { bindOwnedActor } from "./VolparossaUboProofParent.sys.mjs";

const { AddonManager } = ChromeUtils.importESModule("resource://gre/modules/AddonManager.sys.mjs");
const { ExtensionParent } = ChromeUtils.importESModule("resource://gre/modules/ExtensionParent.sys.mjs");
let active = false;

function profileFile(name) {
  const directory = Services.dirsvc.get("ProfD", Ci.nsIFile);
  requireProof(!directory.isSymlink() && directory.isDirectory()
    && (directory.permissions & 0o777) === 0o700, "ubo_proof_private_profile_required");
  const file = directory.clone();
  file.append(name);
  if (file.exists()) requireProof(!file.isSymlink() && file.isFile()
    && (file.permissions & 0o777) === 0o600 && file.fileSize <= 256,
  "ubo_proof_private_journal_required");
  return file;
}

async function readJournal() {
  const state = profileFile("volparossa-ubo-proof.json");
  const attempted = profileFile("volparossa-ubo-proof.attempted");
  const temporary = profileFile("volparossa-ubo-proof.tmp");
  // A crash during a write is unknown, not permission to start enrollment again.
  if (temporary.exists()) return { schema: 1, state: "pending" };
  if (!state.exists()) return { schema: 1, state: attempted.exists() ? "pending" : "fresh" };
  requireProof(attempted.exists(), "ubo_proof_missing_attempt_marker");
  const value = journal(JSON.parse(new TextDecoder().decode(await IOUtils.read(state.path, { maxBytes: 256 }))));
  requireProof(value.state !== "fresh", "ubo_proof_cannot_reset_enrollment");
  return value;
}

async function writeJournal(value) {
  journal(value);
  requireProof(value.state !== "fresh", "ubo_proof_cannot_reset_enrollment");
  const attempted = profileFile("volparossa-ubo-proof.attempted");
  if (!attempted.exists()) {
    attempted.create(Ci.nsIFile.NORMAL_FILE_TYPE, 0o600);
    await IOUtils.writeUTF8(attempted.path, "attempted\n", { flush: true });
  }
  const state = profileFile("volparossa-ubo-proof.json");
  const temporary = profileFile("volparossa-ubo-proof.tmp");
  requireProof(!temporary.exists(), "ubo_proof_uncertain_previous_write");
  temporary.create(Ci.nsIFile.NORMAL_FILE_TYPE, 0o600);
  await IOUtils.writeJSON(state.path, value, { tmpPath: temporary.path, flush: true });
  requireProof(profileFile("volparossa-ubo-proof.json").exists());
}

async function exactAddon() {
  const addon = await AddonManager.getAddonByID(ID);
  requireProof(addon && addon.isActive && !addon.userDisabled && !addon.appDisabled
    && addon.version === VERSION && addon.signedState === AddonManager.SIGNEDSTATE_SIGNED
    && Services.prefs.getBoolPref("xpinstall.signatures.required"), "ubo_proof_wrong_addon");
  const packageURI = addon.getResourceURI("").QueryInterface(Ci.nsIJARURI).JARFile;
  const file = packageURI.QueryInterface(Ci.nsIFileURL).file;
  requireProof(!file.isSymlink() && file.isFile() && file.fileSize === XPI_BYTES,
    "ubo_proof_wrong_package");
  const bytes = await IOUtils.read(file.path, { maxBytes: XPI_BYTES + 1 });
  requireProof(bytes.length === XPI_BYTES);
  const hash = Cc["@mozilla.org/security/hash;1"].createInstance(Ci.nsICryptoHash);
  hash.init(hash.SHA256);
  hash.update(bytes, bytes.length);
  const actual = Array.from(hash.finish(false), byte => byte.charCodeAt(0).toString(16).padStart(2, "0")).join("");
  requireProof(actual === XPI_SHA256, "ubo_proof_wrong_package_digest");
  const extension = ExtensionParent.GlobalManager.getExtension(ID);
  requireProof(extension?.manifest?.version === VERSION && extension.policy.active === true);
  return extension;
}

export async function openProof() {
  requireProof(!active && Services.appinfo.processType === Ci.nsIXULRuntime.PROCESS_TYPE_DEFAULT
    && Services.appinfo.version === "140.16.0" && Services.appinfo.appBuildID === "20260908152208"
    && Services.prefs.getBoolPref("browser.volparossa.uboProof.enabled", false),
  "ubo_proof_explicit_fixture_required");
  active = true;
  let page;
  let registered = false;
  let closed = false;
  let busy = false;
  const shutdown = () => {
    if (closed) return;
    closed = true;
    try { if (page && !page.unloaded) page.shutdown(); }
    finally {
      if (registered) ChromeUtils.unregisterWindowActor(ACTOR);
      active = false;
    }
  };
  try {
    const extension = await exactAddon();
    const documentURL = extension.policy.getURL("3p-filters.html");
    // Registration matches only the one original extension document, no frames,
    // web pages, general extension scheme or content-supplied script messages.
    ChromeUtils.registerWindowActor(ACTOR, {
      parent: { esModuleURI: new URL("./VolparossaUboProofParent.sys.mjs", import.meta.url).href },
      child: { esModuleURI: new URL("./VolparossaUboProofChild.sys.mjs", import.meta.url).href },
      matches: [documentURL], allFrames: false, messageManagerGroups: ["webext-browsers"],
    });
    registered = true;
    page = new ExtensionParent.HiddenExtensionPage(extension, "tab");
    const browser = await page.createBrowserElement();
    ExtensionParent.apiManager.emit("extension-browser-inserted", browser);
    browser.fixupAndLoadURIString(documentURL, { triggeringPrincipal: extension.principal });
    let manager;
    for (let count = 0; count < 240; count++) {
      manager = browser.browsingContext.currentWindowGlobal;
      if (manager?.documentURI.spec === documentURL && manager.documentPrincipal.addonId === ID
        && !browser.webProgress.isLoadingDocument) break;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    requireProof(manager?.documentURI.spec === documentURL && !browser.webProgress.isLoadingDocument,
      "ubo_proof_page_timeout");
    const send = bindOwnedActor(manager.getActor(ACTOR), browser, documentURL);
    const command = async operation => {
      requireProof(!closed);
      requireProof(await exactAddon() === extension, "ubo_proof_extension_replaced");
      let timer;
      try {
        return await Promise.race([
          send(operation), new Promise((_, reject) => {
            timer = setTimeout(() => {
              shutdown();
              reject(new ProofError("ubo_proof_unknown_timeout"));
            }, 40000);
          }),
        ]);
      } finally { clearTimeout(timer); }
    };
    const serial = action => async () => {
      requireProof(!busy && !closed, "ubo_proof_busy_or_closed");
      busy = true;
      try { return await action(); } finally { busy = false; }
    };
    return Object.freeze({
      enroll: serial(async () => enrollOnce(await step("read_journal", readJournal), writeJournal, command)),
      observe: serial(async () => {
        let record = await step("read_journal", readJournal);
        const observed = await step("observe", () => command("observe"));
        if (record.state === "enrolled" && (!observed.selected || !observed.imported)) {
          record = { schema: 1, state: "opted-out" };
          await step("opt_out_journal", () => writeJournal(record));
        }
        return { state: record.state, ...observed };
      }),
      revoke: serial(async () => {
        // Record the owner's removal first; failed removal never authorizes re-add.
        await step("opt_out_journal", () => writeJournal({ schema: 1, state: "opted-out" }));
        const observed = await step("mutate", () => command("revoke"));
        requireProof(!observed.selected && !observed.imported && observed.freshReload);
        return { state: "opted-out", ...observed };
      }),
      shutdown,
    });
  } catch (error) {
    shutdown();
    throw error;
  }
}
