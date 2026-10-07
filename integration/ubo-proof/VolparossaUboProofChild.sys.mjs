// SPDX-License-Identifier: GPL-3.0-only
import {
  ACTOR, ID, VERSION, changeSelection, failureEnvelope, loadedSelection, requireProof,
  selection, step, successEnvelope, validateCommand, validateContext,
} from "./Contract.sys.mjs";

const { ExtensionPageChild } = ChromeUtils.importESModule(
  "resource://gre/modules/ExtensionPageChild.sys.mjs");

export class VolparossaUboProofChild extends JSWindowActorChild {
  owner = null;
  busy = false;
  destroyed = false;

  check() {
    const policy = WebExtensionPolicy.getByID(ID);
    const context = ExtensionPageChild.extensionContexts.get(this.manager.innerWindowId);
    validateContext({
      id: context?.extension?.id, version: context?.extension?.manifest?.version,
      principalID: this.document.nodePrincipal.addonId,
      documentURL: this.document.documentURI, policyURL: policy?.getURL("3p-filters.html"),
      topLevel: this.browsingContext.parent === null, active: policy?.active === true,
      extensionContext: context?.active === true && context?.viewType === "tab",
      browserID: this.browsingContext.id, innerID: this.manager.innerWindowId,
    }, this.owner);
    requireProof(!this.destroyed && context.extension.manifest.version === VERSION);
  }

  async receiveMessage(message) {
    let phase = "child_validate";
    let acquired = false;
    let reply;
    let channel;
    try {
      requireProof(message.name === ACTOR + ":fixed" && !this.busy && !this.destroyed);
      // First parent query binds this one actor instance; content has no actor API.
      if (this.owner === null) this.owner = Object.freeze({ ...message.data });
      const operation = validateCommand(message.data, this.owner);
      phase = "child_context";
      this.check();
      this.busy = true;
      acquired = true;
      const window = Cu.waiveXrays(this.contentWindow);
      let generation = 0;
      let loaded = null;
      const wait = async predicate => {
        for (let index = 0; index < 240; index++) {
          await step("child_context", () => this.check());
          if (await predicate()) return;
          await new Promise(resolve => this.contentWindow.setTimeout(resolve, 50));
        }
        requireProof(false, "ubo_proof_deadline");
      };
      phase = "child_api_ready";
      await wait(() => typeof window.vAPI?.messaging?.send === "function"
        && typeof window.browser?.storage?.local?.get === "function");
      // The original dashboard handler awaits uBO.isReadyPromise. Discard the
      // returned list metadata; nothing from it is exported to the browser.
      phase = "child_dashboard_ready";
      await window.vAPI.messaging.send("dashboard", Cu.cloneInto({ what: "getLists" }, this.contentWindow));
      const read = () => step("child_storage_read", async () => {
        await step("child_context", () => this.check());
        // Only two specific read-only public storage keys; never edit uBO's DB.
        return window.browser.storage.local.get(Cu.cloneInto(
          { selectedFilterLists: null, importedLists: [] }, this.contentWindow));
      });
      phase = "child_storage_ready";
      await wait(async () => Array.isArray((await read()).selectedFilterLists));
      if (operation === "observe") {
        phase = "observe";
        reply = successEnvelope(selection(await read()));
      } else {
        phase = "child_broadcast";
        channel = new this.contentWindow.BroadcastChannel("uBO");
        channel.onmessage = event => {
          if (event.data?.what !== "staticFilteringDataChanged") return;
          // Keep bounded closed fixture keys only; don't return arbitrary URLs.
          const keys = event.data.listKeys;
          if (!loadedSelection(keys, false) && !loadedSelection(keys, true)) return;
          generation += 1;
          loaded = Array.from(keys);
        };
        phase = "mutate";
        reply = successEnvelope(await changeSelection(operation, {
          read,
          send: async request => {
            await step("child_context", () => this.check());
            await window.vAPI.messaging.send("dashboard", Cu.cloneInto(request, this.contentWindow));
            await step("child_context", () => this.check());
          },
          waitStored: desired => wait(async () => {
            const observed = selection(await read());
            return observed.selected === desired && observed.imported === desired;
          }),
          generation: () => generation,
          waitLoaded: (previous, desired) => wait(() => generation > previous
            && (desired === null || loadedSelection(loaded, desired))),
        }));
      }
    } catch (error) {
      reply = failureEnvelope(error, phase);
    } finally {
      try { channel?.close(); }
      catch (error) { if (reply?.ok !== false) reply = failureEnvelope(error, "child_cleanup"); }
      if (acquired) this.busy = false;
    }
    return reply;
  }

  didDestroy() {
    this.destroyed = true;
  }
}
