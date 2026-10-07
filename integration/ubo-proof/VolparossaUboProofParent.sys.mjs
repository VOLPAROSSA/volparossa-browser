// SPDX-License-Identifier: GPL-3.0-only
import { ACTOR, ID, ProofError, requireProof, step, validateCommand, validateReply } from "./Contract.sys.mjs";

const owners = new WeakMap();

// Only privileged browser code with the actual newly created browser can bind.
export function bindOwnedActor(actor, browser, documentURL) {
  requireProof(!owners.has(actor) && actor.browsingContext === browser.browsingContext);
  const manager = actor.manager;
  const binding = Object.freeze({
    owner: Services.uuid.generateUUID().toString(), documentURL,
    browserID: browser.browsingContext.id, innerID: manager.innerWindowId,
  });
  owners.set(actor, { browser, manager, binding });
  return operation => actor.fixedCommand({ ...binding, operation });
}

export class VolparossaUboProofParent extends JSWindowActorParent {
  receiveMessage() {
    throw new ProofError("ubo_proof_unsolicited_child_message");
  }

  async fixedCommand(data) {
    const operation = await step("parent_validate", () => {
      const owned = owners.get(this);
      requireProof(owned && this.manager === owned.manager
        && this.browsingContext === owned.browser.browsingContext
        && this.browsingContext.currentWindowGlobal === owned.manager
        && owned.manager.documentPrincipal.addonId === ID
        && owned.manager.documentURI.spec === owned.binding.documentURL,
      "ubo_proof_unowned_parent_context");
      return validateCommand(data, owned.binding);
    });
    const reply = await step("actor_delivery", () => this.sendQuery(ACTOR + ":fixed", data));
    // A failure or malformed envelope MUST throw before enrollment reads fields.
    return validateReply(reply, operation);
  }

  didDestroy() {
    owners.delete(this);
  }
}
