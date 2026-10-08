// SPDX-License-Identifier: GPL-3.0-only
// Internal, browser-owned actor contract. No extension/page-facing API.
import { FilterSelectionError, validateSelectionReceipt } from "./Selection.sys.mjs";

export const ACTOR = "VolparossaFilterSelection";
export const ID = "uBlock0@raymondhill.net";
export const VERSION = "1.75.0";
export const XPI_BYTES = 4650100;
export const XPI_SHA256 = "5b74415860456370644bd80f16125e865b0e6c356bb5dfcfb84069967eaa5287";
export const DOCUMENT = "about.html";
export const DEADLINE_MS = 40_000;
export const MAX_COMMANDS = 32;
const CODES = new Set(["actor_invalid", "actor_closed", "actor_busy", "actor_context",
  "actor_package", "actor_deadline", "actor_clock", "actor_authority", "actor_reply",
  "actor_readiness_changed", "actor_cleanup", "actor_other", "invalid_config",
  "invalid_selection", "selection_changed", "unowned_selection", "reload_unproved",
  "invalid_receipt", "identity_lost", "selection_failed"]);

export class FilterActorError extends Error {
  constructor(code) {
    const closed = CODES.has(code) ? code : "actor_other";
    super(closed);
    this.code = closed;
  }
}
export function demand(value, code = "actor_invalid") {
  if (!value) throw new FilterActorError(code);
}
export function closedError(error) {
  return error instanceof FilterActorError || error instanceof FilterSelectionError
    ? new FilterActorError(error.code) : new FilterActorError("actor_other");
}
export function exact(value, names) {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).sort().join() === [...names].sort().join();
}
export function operation(value) {
  demand(["observe", "add", "remove"].includes(value));
  return value;
}
export function validateKey(value) {
  demand(typeof value === "string" && value.length <= 2048 && !/[\x00-\x20\x7f]/.test(value));
  let url;
  try { url = new URL(value); } catch { throw new FilterActorError("actor_invalid"); }
  demand(["http:", "https:"].includes(url.protocol) && url.hostname !== ""
    && url.username === "" && url.password === "" && url.hash === "" && url.href === value);
  return value; // syntax only; independent caller authority remains mandatory
}
const BINDING = ["owner", "browserID", "innerID", "documentURL", "key"];
export function validateBinding(value) {
  demand(exact(value, BINDING) && typeof value.owner === "string"
    && /^\{[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\}$/.test(value.owner)
    && [value.browserID, value.innerID].every(n => Number.isSafeInteger(n) && n > 0)
    && typeof value.documentURL === "string" && value.documentURL.length <= 256
    && /^moz-extension:\/\/[0-9a-f-]+\/about\.html$/.test(value.documentURL));
  validateKey(value.key);
  return Object.freeze({ ...value });
}
export function validateCommand(value, binding) {
  demand(exact(value, [...BINDING, "schema", "operation", "requestID", "deadlineWallMs"])
    && value.schema === 1 && Number.isSafeInteger(value.requestID)
    && value.requestID > 0 && value.requestID <= MAX_COMMANDS
    && Number.isSafeInteger(value.deadlineWallMs) && value.deadlineWallMs > 0);
  validateBinding(binding);
  demand(BINDING.every(key => value[key] === binding[key]), "actor_context");
  return operation(value.operation);
}
export function commandBinding(value) {
  demand(value !== null && typeof value === "object");
  return validateBinding(Object.fromEntries(BINDING.map(key => [key, value[key]])));
}
export function validateContext(value, binding) {
  demand(value.id === ID && value.version === VERSION && value.principalID === ID
    && value.documentURL === binding.documentURL && value.policyURL === binding.documentURL
    && value.topLevel === true && value.active === true && value.extensionContext === true
    && value.browserID === binding.browserID && value.innerID === binding.innerID, "actor_context");
}
export function reply(value, requestID) {
  return { schema: 1, requestID, ok: true, value };
}
export function failed(error, requestID) {
  return { schema: 1, requestID: Number.isSafeInteger(requestID) && requestID > 0
    && requestID <= MAX_COMMANDS ? requestID : null, ok: false, code: closedError(error).code };
}
export function validateReply(value, requestID, op) {
  demand(value && value.schema === 1 && value.requestID === requestID && typeof value.ok === "boolean", "actor_reply");
  if (!value.ok) {
    demand(exact(value, ["schema", "requestID", "ok", "code"]) && CODES.has(value.code), "actor_reply");
    throw new FilterActorError(value.code);
  }
  demand(exact(value, ["schema", "requestID", "ok", "value"]), "actor_reply");
  try { return validateSelectionReceipt(value.value, op); }
  catch { throw new FilterActorError("actor_reply"); }
}

// Copies stay child-local. These bounds cover early readiness/broadcast parsing;
// Selection still performs canonical baseline and mutation-postcondition checks.
export function boundedKeys(value) {
  demand(Array.isArray(value) && value.length <= 512, "invalid_selection");
  let total = 0;
  const copy = [];
  for (const key of value) {
    demand(typeof key === "string" && key.length > 0 && key.length <= 2048
      && !/[\x00-\x20\x7f]/.test(key), "invalid_selection");
    total += key.length;
    demand(total <= 131072, "invalid_selection");
    copy.push(key);
  }
  demand(new Set(copy).size === copy.length, "invalid_selection");
  return copy;
}
export function baseline(value) {
  demand(exact(value, ["selectedFilterLists", "importedLists"]), "invalid_selection");
  const importedLists = boundedKeys(value.importedLists);
  demand(importedLists.every(key => /^[a-z-]+:\/\/\S+$/.test(key)), "invalid_selection");
  return { selectedFilterLists: boundedKeys(value.selectedFilterLists), importedLists };
}
export function sameBaseline(a, b) {
  return ["selectedFilterLists", "importedLists"].every(name => {
    const wanted = new Set(b[name]);
    return a[name].length === wanted.size && a[name].every(key => wanted.has(key));
  });
}

export function makeDeadline(clock, wallDeadline = null) {
  let last = null;
  const sample = () => {
    let value;
    try { value = clock(); } catch { throw new FilterActorError("actor_clock"); }
    demand(exact(value, ["bootMs", "wallMs"]) && [value.bootMs, value.wallMs]
      .every(n => Number.isSafeInteger(n) && n >= 0)
      && (!last || (value.bootMs >= last.bootMs && value.wallMs >= last.wallMs)), "actor_clock");
    last = { ...value };
    return last;
  };
  const start = sample();
  const until = wallDeadline ?? start.wallMs + DEADLINE_MS;
  demand(Number.isSafeInteger(until) && until > start.wallMs && until <= start.wallMs + DEADLINE_MS, "actor_deadline");
  return Object.freeze({ wallDeadline: until, check() {
    const now = sample();
    demand(now.wallMs < until && now.bootMs - start.bootMs < DEADLINE_MS, "actor_deadline");
    return true;
  } });
}
