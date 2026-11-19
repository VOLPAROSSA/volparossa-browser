// SPDX-License-Identifier: GPL-3.0-only
// Isolated proof only. These constants are not a production enrollment policy.
export const ID = "uBlock0@raymondhill.net";
export const VERSION = "1.75.0";
export const XPI_BYTES = 4650100;
export const XPI_SHA256 = "5b74415860456370644bd80f16125e865b0e6c356bb5dfcfb84069967eaa5287";
export const URL = "http://127.0.0.1:18765/filters.txt";
export const ACTOR = "VolparossaUboProof";
export const STOCKS = Object.freeze([
  "easylist", "easyprivacy", "plowe-0", "ublock-badware", "ublock-filters",
  "ublock-privacy", "ublock-quick-fixes", "ublock-unbreak", "urlhaus-1", "user-filters",
]);

export const PHASES = Object.freeze([
  "bootstrap", "read_journal", "observe", "pending_journal", "completed_journal", "opt_out_journal",
  "parent_validate", "actor_delivery", "reply_validate", "child_validate", "child_context",
  "child_api_ready", "child_dashboard_ready", "child_storage_read", "child_storage_ready",
  "child_broadcast", "child_cleanup", "mutate", "wait_stored", "reload_drain", "reload_drain_event",
  "reload_fresh", "reload_fresh_event", "verify_final",
]);
export const REASONS = Object.freeze([
  "ubo_proof_other", "ubo_proof_invalid_reply", "ubo_proof_busy_or_closed", "ubo_proof_cannot_reset_enrollment",
  "ubo_proof_concurrent_selection_change", "ubo_proof_deadline", "ubo_proof_explicit_fixture_required",
  "ubo_proof_extension_replaced", "ubo_proof_invalid_journal", "ubo_proof_missing_attempt_marker",
  "ubo_proof_missing_storage", "ubo_proof_no_reload_evidence", "ubo_proof_not_fresh_stock_selection",
  "ubo_proof_page_timeout", "ubo_proof_private_journal_required", "ubo_proof_private_profile_required",
  "ubo_proof_refused", "ubo_proof_uncertain_previous_write", "ubo_proof_unknown_timeout", "ubo_proof_unowned_list",
  "ubo_proof_unowned_parent_context", "ubo_proof_unsolicited_child_message", "ubo_proof_wrong_addon",
  "ubo_proof_wrong_command", "ubo_proof_wrong_context", "ubo_proof_wrong_package", "ubo_proof_wrong_package_digest",
]);

export class ProofError extends Error {
  constructor(reason) {
    const closed = REASONS.includes(reason) ? reason : "ubo_proof_other";
    super(closed);
    this.reason = closed;
  }
}

export class ProofFailure extends ProofError {
  constructor(phase, reason) {
    super(PHASES.includes(phase) ? reason : "ubo_proof_invalid_reply");
    this.phase = PHASES.includes(phase) ? phase : "reply_validate";
  }
}

// Never inspect/export unknown exception messages, names, stacks or properties.
export function failureEnvelope(error, fallback = "bootstrap") {
  return { schema: 1, ok: false,
    phase: error instanceof ProofFailure && PHASES.includes(error.phase) ? error.phase
      : PHASES.includes(fallback) ? fallback : "reply_validate",
    reason: error instanceof ProofError && REASONS.includes(error.reason) ? error.reason : "ubo_proof_other" };
}

export async function step(phase, action) {
  try { return await action(); }
  catch (error) {
    const failure = failureEnvelope(error, phase);
    throw new ProofFailure(failure.phase, failure.reason);
  }
}

export function successEnvelope(value) {
  return { schema: 1, ok: true, value };
}

export function validateReply(reply, operation) {
  const invalid = () => { throw new ProofFailure("reply_validate", "ubo_proof_invalid_reply"); };
  if (!reply || reply.schema !== 1 || typeof reply.ok !== "boolean") invalid();
  if (!reply.ok) {
    if (!exactKeys(reply, ["schema", "ok", "phase", "reason"])
      || !PHASES.includes(reply.phase) || !REASONS.includes(reply.reason)) invalid();
    throw new ProofFailure(reply.phase, reply.reason);
  }
  if (!exactKeys(reply, ["schema", "ok", "value"])) invalid();
  const value = reply.value;
  const keys = operation === "observe" ? ["selected", "imported", "stocks"]
    : ["selected", "imported", "stocks", "freshReload", "reloadEvents"];
  if (!["observe", "enroll", "revoke"].includes(operation) || !exactKeys(value, keys)
    || typeof value.selected !== "boolean" || typeof value.imported !== "boolean" || value.stocks !== 10) invalid();
  if (operation !== "observe" && (value.freshReload !== true
    || !Number.isSafeInteger(value.reloadEvents) || value.reloadEvents < 2 || value.reloadEvents > 32
    || value.selected !== (operation === "enroll") || value.imported !== value.selected)) invalid();
  return value;
}

export function requireProof(condition, code = "ubo_proof_refused") {
  if (!condition) throw new ProofError(code);
}

function exactKeys(value, keys) {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).sort().join() === [...keys].sort().join();
}

export function validateContext(value, expected) {
  requireProof(value.id === ID && value.version === VERSION && value.principalID === ID
    && value.documentURL === expected.documentURL && value.policyURL === expected.documentURL
    && value.topLevel === true && value.active === true && value.extensionContext === true
    && value.browserID === expected.browserID && value.innerID === expected.innerID
    && Number.isSafeInteger(value.browserID) && value.browserID > 0
    && Number.isSafeInteger(value.innerID) && value.innerID > 0,
  "ubo_proof_wrong_context");
}

export function validateCommand(data, owner) {
  requireProof(exactKeys(data, ["operation", "owner", "browserID", "innerID", "documentURL"])
    && ["observe", "enroll", "revoke"].includes(data.operation)
    && typeof data.owner === "string" && /^\{[0-9a-f-]{36}\}$/.test(data.owner)
    && data.owner === owner.owner && data.browserID === owner.browserID
    && data.innerID === owner.innerID && data.documentURL === owner.documentURL,
  "ubo_proof_wrong_command");
  return data.operation;
}

export function selection(value) {
  requireProof(value && Array.isArray(value.selectedFilterLists)
    && Array.isArray(value.importedLists), "ubo_proof_missing_storage");
  const selected = value.selectedFilterLists;
  const imported = value.importedLists;
  requireProof(selected.length <= 11 && imported.length <= 1
    && new Set(selected).size === selected.length && new Set(imported).size === imported.length
    && STOCKS.every(key => selected.includes(key))
    && selected.every(key => STOCKS.includes(key) || key === URL)
    && imported.every(key => key === URL), "ubo_proof_not_fresh_stock_selection");
  return { selected: selected.includes(URL), imported: imported.includes(URL), stocks: 10 };
}

export function loadedSelection(keys, desired) {
  return Array.isArray(keys) && keys.length === 10 + Number(desired)
    && new Set(keys).size === keys.length && STOCKS.every(key => keys.includes(key))
    && keys.includes(URL) === desired && keys.every(key => STOCKS.includes(key) || key === URL);
}

export function journal(value) {
  requireProof(exactKeys(value, ["schema", "state"]) && value.schema === 1
    && ["fresh", "pending", "enrolled", "opted-out"].includes(value.state),
  "ubo_proof_invalid_journal");
  return value;
}

// Adapter parameters are internal browser-owned functions, never an IPC API.
// Persist pending BEFORE sending any mutation. Never retry an unknown outcome.
export async function enrollOnce(record, save, command) {
  journal(record);
  if (record.state !== "fresh") return { state: record.state, attempted: false };
  const before = await step("observe", () => command("observe"));
  requireProof(!before.selected && !before.imported, "ubo_proof_unowned_list");
  await step("pending_journal", () => save({ schema: 1, state: "pending" }));
  const after = await step("mutate", () => command("enroll"));
  await step("verify_final", () => requireProof(after.selected && after.imported && after.freshReload === true
    && after.stocks === 10 && after.reloadEvents >= 2, "ubo_proof_no_reload_evidence"));
  await step("completed_journal", () => save({ schema: 1, state: "enrolled" }));
  return { state: "enrolled", attempted: true, ...after };
}

// Two sequential reload calls matter: the first can join a pre-mutation load.
// The second can only join/start a load after that first one has completed.
export async function changeSelection(operation, transport) {
  requireProof(["enroll", "revoke"].includes(operation));
  const desired = operation === "enroll";
  const before = await step("child_storage_read", () => transport.read());
  selection(before);
  await step("mutate", () => transport.send(desired
    ? { what: "applyFilterListSelection", toImport: URL }
    : { what: "applyFilterListSelection", toRemove: [URL] }));
  await step("wait_stored", () => transport.waitStored(desired));
  let generation = transport.generation();
  await step("reload_drain", () => transport.send({ what: "reloadAllFilters" }));
  await step("reload_drain_event", () => transport.waitLoaded(generation, null));
  generation = transport.generation();
  await step("reload_fresh", () => transport.send({ what: "reloadAllFilters" }));
  await step("reload_fresh_event", () => transport.waitLoaded(generation, desired));
  const after = await step("verify_final", async () => {
    const observed = selection(await transport.read());
    requireProof(observed.selected === desired && observed.imported === desired,
      "ubo_proof_concurrent_selection_change");
    return observed;
  });
  return { ...after, freshReload: true, reloadEvents: transport.generation() };
}
