// SPDX-License-Identifier: GPL-3.0-only
import { createSelectionCommand } from "./Selection.sys.mjs";
import { ACTOR, ID, DOCUMENT, demand, closedError, FilterActorError, failed, reply, commandBinding, validateCommand,
  validateContext, boundedKeys, baseline, sameBaseline, makeDeadline } from "./ActorContract.sys.mjs";

const { ExtensionPageChild } = ChromeUtils.importESModule("resource://gre/modules/ExtensionPageChild.sys.mjs");

export class VolparossaFilterSelectionChild extends JSWindowActorChild {
  owner = null;
  busy = false;
  destroyed = false;
  ready = false;
  sequence = 0;
  channel = null;
  policy = null;
  context = null;
  extension = null;

  check() {
    demand(!this.destroyed && this.owner, "actor_closed");
    const policy = WebExtensionPolicy.getByID(ID);
    const context = ExtensionPageChild.extensionContexts.get(this.manager.innerWindowId);
    validateContext({ id: context?.extension?.id, version: context?.extension?.manifest?.version,
      principalID: this.document.nodePrincipal.addonId, documentURL: this.document.documentURI,
      policyURL: policy?.getURL(DOCUMENT), topLevel: this.browsingContext.parent === null,
      active: policy?.active === true, extensionContext: context?.active === true && context?.viewType === "tab",
      browserID: this.browsingContext.id, innerID: this.manager.innerWindowId }, this.owner);
    if (this.policy === null) {
      this.policy = policy; this.context = context; this.extension = context.extension;
    }
    demand(policy === this.policy && context === this.context && context.extension === this.extension, "actor_context");
    return true;
  }

  async receiveMessage(message) {
    let acquired = false;
    let requestID = null;
    let output;
    let readinessChanged = false;
    try {
      demand(message.name === ACTOR + ":fixed" && !this.busy && !this.destroyed, "actor_busy");
      const bound = this.owner ?? commandBinding(message.data);
      const op = validateCommand(message.data, bound);
      requestID = message.data.requestID;
      demand(requestID === this.sequence + 1);
      this.owner = bound;
      this.check();
      this.sequence = requestID;
      this.busy = true;
      acquired = true;
      const deadline = makeDeadline(() => ({ bootMs: Services.telemetry.msSinceProcessStartIncludingSuspend(),
        wallMs: Date.now() }), message.data.deadlineWallMs);
      const check = () => { this.check(); deadline.check(); return true; };
      const window = Cu.waiveXrays(this.contentWindow);
      const wait = async predicate => {
        for (let count = 0; count < 240; count++) {
          check();
          const matched = await predicate();
          check();
          if (matched) return;
          await new Promise(resolve => this.contentWindow.setTimeout(resolve, 50));
        }
        demand(false, "actor_deadline");
      };
      await wait(() => typeof window.vAPI?.messaging?.send === "function"
        && typeof window.browser?.storage?.local?.get === "function");
      const read = async () => {
        check();
        const value = await window.browser.storage.local.get(Cu.cloneInto(
          { selectedFilterLists: null, importedLists: [] }, this.contentWindow));
        check();
        return value;
      };
      await wait(async () => Array.isArray((await read()).selectedFilterLists));
      const send = async request => {
        check();
        await window.vAPI.messaging.send("dashboard", Cu.cloneInto(request, this.contentWindow));
        check();
      };
      let readinessBaseline = null;
      if (!this.ready) {
        // Original about.html does not auto-request lists. Unlike 3p-filters,
        // it lets us capture BEFORE getLists' isReadyPromise/getAvailableLists.
        // That call CAN normalize/migrate user lists. Detect, do not hide it.
        const before = baseline(await read());
        readinessBaseline = before;
        await send({ what: "getLists" }); // discard all returned list metadata
        const after = baseline(await read());
        demand(sameBaseline(before, after), "actor_readiness_changed");
        this.ready = true;
      }
      let firstSelectionRead = true;
      const selectionRead = async () => {
        const value = await read();
        if (firstSelectionRead && readinessBaseline !== null) {
          // Native getLists does not await its storage writes. Do not silently
          // adopt a delayed readiness migration as the transaction's baseline.
          readinessChanged = !sameBaseline(readinessBaseline, baseline(value));
          demand(!readinessChanged, "actor_readiness_changed");
        }
        firstSelectionRead = false;
        return value;
      };
      let generation = 0;
      let loaded = null;
      let eventError = null;
      if (op !== "observe") {
        this.channel = new this.contentWindow.BroadcastChannel("uBO");
        this.channel.onmessage = event => {
          if (event.data?.what !== "staticFilteringDataChanged") return;
          try {
            check();
            demand(generation < 32, "reload_unproved");
            loaded = boundedKeys(event.data.listKeys);
            generation++;
          } catch (error) { eventError = closedError(error); }
        };
      }
      const eventCheck = () => { check(); if (eventError) throw eventError; return true; };
      const command = createSelectionCommand({ key: this.owner.key, assertCurrent: eventCheck, transport: {
        read: selectionRead, send, generation: () => { eventCheck(); return generation; },
        waitStored: desired => wait(async () => {
          eventCheck();
          const value = baseline(await read());
          return value.selectedFilterLists.includes(this.owner.key) === desired
            && value.importedLists.includes(this.owner.key) === desired;
        }),
        waitLoaded: async previous => {
          await wait(() => { eventCheck(); return generation > previous; });
          return { generation, listKeys: loaded };
        },
      } });
      const value = await command(op);
      eventCheck();
      output = reply(value, requestID);
    } catch (error) {
      if (acquired) this.destroyed = true; // unknown outcome cannot reopen this child
      output = failed(readinessChanged ? new FilterActorError("actor_readiness_changed") : error, requestID);
    } finally {
      if (acquired) {
        try { this.channel?.close(); }
        catch { this.destroyed = true; output = failed(new FilterActorError("actor_cleanup"), requestID); }
        this.channel = null;
        this.busy = false;
      }
    }
    return output;
  }
  didDestroy() {
    this.destroyed = true;
    try { this.channel?.close(); } catch { /* destroyed: never issue a success receipt */ }
    finally { this.channel = null; }
  }
}
