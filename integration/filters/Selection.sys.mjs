// SPDX-License-Identifier: GPL-3.0-only
// Internal selection lifecycle seams, NOT a startup owner or an extension API.
// The owner binds one independently authorized immutable supplement key and the
// original signed uBO instance/context. No key is learned from a page/broker.
// Baselines stay child-local and in memory: neither receipts nor journals export
// custom-list URLs. This module creates no feed, transport, timer or storage API.

const CODES = new Set(["invalid_config", "invalid_selection", "selection_changed",
  "unowned_selection", "reload_unproved", "invalid_receipt", "invalid_journal",
  "busy_or_closed", "identity_lost", "authority_lost", "intent_changed", "uncertain", "selection_failed"]);
const MAX_LISTS = 512;
const MAX_KEY = 2048;
const MAX_CHARACTERS = 131072;
const MAX_RELOAD_EVENTS = 32;

export class FilterSelectionError extends Error {
  constructor(code) {
    const closed = CODES.has(code) ? code : "selection_failed";
    super(closed);
    this.code = closed;
  }
}
function requireValue(value, code = "invalid_config") {
  if (!value) throw new FilterSelectionError(code);
}
function exact(value, keys) {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).sort().join() === [...keys].sort().join();
}
function failure(error) {
  // Never format/read an unknown exception (which may contain private URLs).
  return error instanceof FilterSelectionError ? error : new FilterSelectionError("selection_failed");
}
function keys(value) {
  requireValue(Array.isArray(value) && value.length <= MAX_LISTS, "invalid_selection");
  let characters = 0;
  const result = [];
  for (const key of value) {
    requireValue(typeof key === "string" && key.length > 0 && key.length <= MAX_KEY
      && !/[\x00-\x20\x7f]/.test(key), "invalid_selection");
    characters += key.length;
    requireValue(characters <= MAX_CHARACTERS, "invalid_selection");
    result.push(key);
  }
  requireValue(new Set(result).size === result.length, "invalid_selection");
  return result;
}
function snapshot(value) {
  requireValue(exact(value, ["selectedFilterLists", "importedLists"]), "invalid_selection");
  const selected = keys(value.selectedFilterLists);
  const imported = keys(value.importedLists);
  // Strict subset of original 1.75 listKeysFromCustomFilterLists syntax. This
  // cannot prove dynamic badLists/stock migration stability; postchecks must.
  requireValue(imported.every(key => /^[a-z-]+:\/\/\S+$/.test(key)), "invalid_selection");
  return { selected, imported };
}
function same(a, b) {
  const wanted = new Set(b);
  return a.length === b.length && a.every(key => wanted.has(key));
}
function without(values, key) { return values.filter(value => value !== key); }
function bits(value, key) {
  return Object.freeze({ selected: value.selected.includes(key), imported: value.imported.includes(key) });
}
function preserved(before, after, key) {
  requireValue(same(without(before.selected, key), without(after.selected, key))
    && same(without(before.imported, key), without(after.imported, key)), "selection_changed");
}
function selectedAfter(before, key, desired) {
  return [...without(before.selected, key), ...(desired ? [key] : [])];
}
function generation(value) {
  requireValue(Number.isSafeInteger(value) && value >= 0, "reload_unproved");
  return value;
}

export function validateSelectionReceipt(value, operation) {
  requireValue(["observe", "add", "remove"].includes(operation), "invalid_receipt");
  requireValue(exact(value, operation === "observe" ? ["selected", "imported"]
    : ["selected", "imported", "preserved", "freshReload", "reloadEvents"])
    && typeof value.selected === "boolean" && typeof value.imported === "boolean", "invalid_receipt");
  if (operation !== "observe") {
    requireValue(value.selected === (operation === "add") && value.imported === value.selected
      && value.preserved === true && value.freshReload === true
      && Number.isSafeInteger(value.reloadEvents) && value.reloadEvents >= 2
      && value.reloadEvents <= MAX_RELOAD_EVENTS, "invalid_receipt");
  }
  return Object.freeze({ ...value });
}

// Child-local transport, to be bound by the existing exact-principal actor:
// read: ONLY the two storage keys above; never browser.storage.local.set.
// send: original dashboard channel, ONLY the fixed messages emitted below.
// waitStored(desired): bounded wait for both target bits; no acknowledgement-only
// success. waitLoaded(after): bounded staticFilteringDataChanged observation,
// { generation, listKeys }; generation() is its monotone per-command counter.
// assertCurrent is synchronous exact-context/operation-lease checking, including
// before every dashboard send. The transport supplies the existing actor deadline
// and teardown. A never-settling transport retains the busy slot, never overlaps.
// Do NOT use getLists as a read-only readiness barrier: uBO can mutate there.
export function createSelectionCommand({ key, transport, assertCurrent }) {
  requireValue(typeof key === "string" && key.length <= MAX_KEY
    && /^https?:\/\/[^\s\x00-\x20\x7f]+$/.test(key)
    && typeof assertCurrent === "function"
    && transport && ["read", "send", "waitStored", "generation", "waitLoaded"]
      .every(name => typeof transport[name] === "function"));
  let busy = false;
  const check = () => requireValue(assertCurrent() === true, "identity_lost");
  const call = async action => {
    check();
    const value = await action();
    check();
    return value;
  };
  return async operation => {
    requireValue(!busy, "busy_or_closed");
    requireValue(["observe", "add", "remove"].includes(operation));
    busy = true;
    try {
      const before = snapshot(await call(() => transport.read()));
      if (operation === "observe") return bits(before, key);
      const desired = operation === "add";
      if (desired) {
        const own = bits(before, key);
        requireValue(!own.selected && !own.imported, "unowned_selection");
        requireValue(before.selected.length < MAX_LISTS && before.imported.length < MAX_LISTS,
          "invalid_selection");
        snapshot({ selectedFilterLists: [...before.selected, key], importedLists: [...before.imported, key] });
      }
      const verifyStored = async () => {
        const after = snapshot(await call(() => transport.read()));
        preserved(before, after, key);
        const own = bits(after, key);
        requireValue(own.selected === desired && own.imported === desired, "selection_changed");
      };
      // Delta only. Never toOverwrite, toSelect (even merge), DB edits or baseline
      // restoration. Native uBO may reorder imports: equality is semantic sets.
      await call(() => transport.send(desired
        ? { what: "applyFilterListSelection", toImport: key }
        : { what: "applyFilterListSelection", toRemove: [key] }));
      await call(() => transport.waitStored(desired));
      await verifyStored();
      const start = generation(transport.generation());
      let last = start;
      for (let pass = 0; pass < 2; pass++) {
        check();
        const previous = generation(transport.generation());
        requireValue(previous >= last && previous - start <= MAX_RELOAD_EVENTS, "reload_unproved");
        // First drain may join a pre-mutation load. Second call starts/joins only
        // after it completed; require a different observed event and fresh keys.
        await call(() => transport.send({ what: "reloadAllFilters" }));
        const event = await call(() => transport.waitLoaded(previous));
        requireValue(exact(event, ["generation", "listKeys"]), "reload_unproved");
        last = generation(event.generation);
        requireValue(last > previous && last - start <= MAX_RELOAD_EVENTS
          && generation(transport.generation()) === last, "reload_unproved");
        const loaded = keys(event.listKeys);
        requireValue(same(without(loaded, key), without(before.selected, key)), "selection_changed");
        if (pass === 1) requireValue(same(loaded, selectedAfter(before, key, desired)), "reload_unproved");
        await verifyStored();
        requireValue(generation(transport.generation()) === last, "reload_unproved");
      }
      // These receipts prove membership/reload ordering, NOT filter contents:
      // original uBO broadcasts keys even for empty/failed assets, and can reuse
      // compiled caches. Immutable source/content/expiry proof remains separate.
      return validateSelectionReceipt({ selected: desired, imported: desired,
        preserved: true, freshReload: true, reloadEvents: last - start }, operation);
    } catch (error) { throw failure(error); }
    finally { busy = false; }
  };
}

export function selectionJournal(value) {
  requireValue(exact(value, ["schema", "choice", "state"]) && value.schema === 1
    && ["eligible", "opted-out"].includes(value.choice)
    && ["fresh", "pending", "active", "suspended"].includes(value.state)
    && (value.choice !== "opted-out" || value.state !== "active"), "invalid_journal");
  return Object.freeze({ ...value });
}

// Parent seam. The durable journal is subscription-scoped (publisher + name),
// NOT manifest-scoped. Its owner must preserve an attempted marker / atomic
// pending write, treat missing-after-attempt/corrupt data as pending, and never
// recreate eligible/fresh on restart. fresh + opted-out means never owned, not
// permission to delete a preexisting user key. save must be durable on resolution.
// No storage implementation/approval is invented here. Only a proved receipt can
// feed owner reconciliation; state/observe alone NEVER authorize Admission.
// Schedule mutations behind the owner's closed barrier BEFORE supplying a ready
// result; do not recursively invalidate the flight awaiting its own mutation.
export class FilterSelectionLifecycle {
  #record;
  #save;
  #command;
  #invalidate;
  #assertCurrent;
  #authorize;
  #busy = false;
  #closed = false;
  #epoch = 0;
  #operationEpoch = null;
  #saveFlight = null;
  #optOutWrite = null;

  constructor({ record, save, command, invalidate, assertCurrent, authorize }) {
    requireValue([save, command, invalidate, assertCurrent, authorize].every(value => typeof value === "function"));
    this.#record = selectionJournal(record);
    this.#save = save;
    this.#command = command;
    this.#invalidate = invalidate;
    this.#assertCurrent = assertCurrent;
    this.#authorize = authorize;
  }
  get status() { return this.#record; }
  #check(granted = false) {
    requireValue(!this.#closed, "busy_or_closed");
    requireValue(this.#operationEpoch === null || this.#operationEpoch === this.#epoch, "intent_changed");
    requireValue(this.#assertCurrent() === true, "identity_lost");
    if (granted) requireValue(this.#authorize() === true, "authority_lost");
  }
  async #call(operation, granted = false) {
    this.#check(granted);
    const value = await this.#command(operation);
    this.#check(granted);
    return validateSelectionReceipt(value, operation);
  }
  async #store(value, granted = false) {
    this.#check(granted);
    await this.#write(selectionJournal(value));
    this.#check(granted);
    this.#record = selectionJournal(value);
  }
  async #write(value) {
    const flight = Promise.resolve().then(() => this.#save(value));
    this.#saveFlight = flight;
    try { await flight; }
    finally { if (this.#saveFlight === flight) this.#saveFlight = null; }
  }
  #invalidateNow() {
    // Synchronous capability, as with Admission.invalidate(). An asynchronous
    // substitute must not let a mutation run before its barrier is established.
    requireValue(this.#invalidate() === undefined);
  }
  #intent() {
    requireValue(!this.#closed, "busy_or_closed");
    this.#epoch += 1;
    this.#invalidateNow();
  }
  #persistBusyOptOut() {
    if (this.#optOutWrite) return this.#optOutWrite;
    const previous = this.#saveFlight;
    // ONE sticky write slot; wait only for earlier journal I/O, never for the
    // actor operation. The epoch forbids that old operation's next write/receipt.
    // Even a failed earlier save must not erase the explicit refusal. A failed
    // refusal save stays pending in memory; later calls do not retry that write.
    this.#optOutWrite = (async () => {
      try { await previous; } catch { /* still persist the user's refusal */ }
      await this.#write(selectionJournal({ schema: 1, choice: "opted-out", state: "pending" }));
    })();
    return this.#optOutWrite;
  }
  #result(attempted, receipt = null) {
    return Object.freeze({ ...this.#record, attempted, receipt });
  }
  async #serial(action) {
    requireValue(!this.#busy && !this.#closed, "busy_or_closed");
    requireValue(this.#record.state !== "pending", "uncertain");
    this.#busy = true;
    this.#operationEpoch = this.#epoch;
    try {
      this.#check();
      const result = await action();
      // The final durable commit also yields. A revocation in its delivery
      // microtask must not turn the old receipt into a successful new result.
      this.#check(result.receipt?.freshReload === true && result.receipt.selected === true);
      return result;
    }
    catch (error) { throw failure(error); }
    finally { this.#busy = false; this.#operationEpoch = null; }
  }
  async #change(operation, choice) {
    const granted = operation === "add";
    this.#check(granted);
    this.#invalidateNow(); // invalidate every old native admission first
    this.#record = selectionJournal({ schema: 1, choice, state: "pending" });
    // On ANY uncertainty, including a failed final durable write, keep pending.
    await this.#store(this.#record, granted);
    const receipt = await this.#call(operation, granted);
    await this.#store({ schema: 1, choice, state: granted ? "active" : "suspended" }, granted);
    this.#check(granted);
    return this.#result(true, receipt);
  }
  async #observe() {
    const observed = await this.#call("observe");
    if (this.#record.state === "active" && (!observed.selected || !observed.imported)) {
      // User deselection/removal differs from the owner's recorded temporary
      // removal. Persist sticky intent; still no compiled-absence proof here.
      this.#invalidateNow();
      this.#record = selectionJournal({ schema: 1, choice: "opted-out", state: "pending" });
      await this.#store(this.#record);
      await this.#store({ schema: 1, choice: "opted-out", state: "suspended" });
    }
    return observed;
  }
  observe() {
    return this.#serial(async () => this.#result(false, await this.#observe()));
  }
  enroll() {
    return this.#serial(async () => {
      if (this.#record.choice === "opted-out") return this.#result(false);
      this.#check(true);
      const before = await this.#observe();
      this.#check(true);
      if (this.#record.choice === "opted-out" || this.#record.state === "active") return this.#result(false);
      requireValue(!before.selected && !before.imported, "unowned_selection");
      return this.#change("add", "eligible");
    });
  }
  async suspend(reason) {
    try {
      requireValue(["expired", "revoked"].includes(reason));
      this.#intent(); // even a busy/uncertain actor cannot delay this barrier
    } catch (error) { throw failure(error); }
    return this.#serial(async () => {
      const before = await this.#observe();
      if (this.#record.state === "fresh") {
        requireValue(!before.selected && !before.imported, "unowned_selection");
        return this.#result(false); // never owned; not an engine-absence receipt
      }
      return this.#change("remove", this.#record.choice);
    });
  }
  async optOut() {
    const busy = this.#busy;
    const uncertain = this.#record.state === "pending";
    if (!this.#closed && (busy || uncertain)) {
      this.#record = selectionJournal({ schema: 1, choice: "opted-out", state: "pending" });
      let barrierError = null;
      try { this.#intent(); } catch (error) { barrierError = failure(error); }
      try { await this.#persistBusyOptOut(); } catch (error) { throw failure(error); }
      if (barrierError) throw barrierError;
      // Choice is durable, but no removal/retry/recovery was scheduled.
      throw new FilterSelectionError(busy ? "busy_or_closed" : "uncertain");
    }
    try { this.#intent(); } catch (error) { throw failure(error); }
    return this.#serial(async () => {
      if (this.#record.state === "fresh") {
        // User refusal remains sticky even if this key was already user-owned.
        // Do not remove/adopt it, and do not return an engine-absence receipt.
        this.#invalidateNow();
        this.#record = selectionJournal({ schema: 1, choice: "opted-out", state: "pending" });
        await this.#store(this.#record);
        await this.#store({ schema: 1, choice: "opted-out", state: "fresh" });
        return this.#result(false);
      }
      // Sticky intent and pending must reach disk before even the child's read;
      // a failed/hung observe must not leave active admission or eligible choice.
      return this.#change("remove", "opted-out");
    });
  }
  close() {
    if (this.#closed) return;
    this.#closed = true;
    try { this.#invalidateNow(); } catch (error) { throw failure(error); }
  }
}
