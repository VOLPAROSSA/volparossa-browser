// SPDX-License-Identifier: GPL-3.0-only
import { ACTOR, ID, DOCUMENT, MAX_COMMANDS, demand, closedError, operation,
  validateBinding, validateCommand, validateReply } from "./ActorContract.sys.mjs";

const owners = new WeakMap();

export function bindSelectionActor(actor, browser, { key, documentURL, policy, current }) {
  demand(!owners.has(actor) && actor.browsingContext === browser.browsingContext
    && typeof current === "function");
  const manager = actor.manager;
  const binding = validateBinding({ owner: Services.uuid.generateUUID().toString(), key, documentURL,
    browserID: browser.browsingContext.id, innerID: manager.innerWindowId });
  const owned = { browser, manager, binding, policy, current, next: 0, busy: false };
  owners.set(actor, owned);
  const check = () => {
    demand(owners.get(actor) === owned && current() === true && policy.active === true
      && WebExtensionPolicy.getByID(ID) === policy && policy.getURL(DOCUMENT) === documentURL
      && actor.manager === manager && actor.browsingContext === browser.browsingContext
      && browser.browsingContext.currentWindowGlobal === manager && browser.browsingContext.parent === null
      && manager.documentPrincipal.addonId === ID && manager.documentURI.spec === documentURL, "actor_context");
    return true;
  };
  owned.check = check;
  return Object.freeze({ current: check, close: () => owners.delete(actor),
    command(op, deadlineWallMs) {
      operation(op);
      demand(owned.next < MAX_COMMANDS);
      return actor.fixedCommand({ ...binding, schema: 1, operation: op, requestID: ++owned.next, deadlineWallMs });
    } });
}

export class VolparossaFilterSelectionParent extends JSWindowActorParent {
  receiveMessage() { demand(false, "actor_invalid"); }
  async fixedCommand(data) {
    const owned = owners.get(this);
    let acquired = false;
    try {
      demand(owned && !owned.busy, "actor_busy");
      const op = validateCommand(data, owned.binding);
      owned.check();
      owned.busy = true;
      acquired = true;
      const value = await this.sendQuery(ACTOR + ":fixed", data);
      owned.check(); // a valid-looking reply cannot survive navigation/replacement
      return validateReply(value, data.requestID, op);
    } catch (error) { throw closedError(error); }
    finally { if (acquired) owned.busy = false; }
  }
  didDestroy() { owners.delete(this); }
}
