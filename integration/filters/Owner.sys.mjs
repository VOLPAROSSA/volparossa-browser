// SPDX-License-Identifier: GPL-3.0-only
// Explicit parent-only selection owner. No startup hook, feed server, discovery,
// publication approval or native request-admission authority is supplied here.
import { validateExpected } from "./Contract.sys.mjs";
import { validateKey } from "./ActorContract.sys.mjs";
import { openSelectionActor } from "./Actor.sys.mjs";
import { openSelectionJournal } from "./Journal.sys.mjs";
import { FilterSelectionLifecycle } from "./Selection.sys.mjs";

const CODES = new Set(["owner_config", "owner_context", "owner_clock", "owner_busy",
  "owner_closed", "owner_failed", "owner_cleanup"]);
export class FilterOwnerError extends Error {
  constructor(code) { super(CODES.has(code) ? code : "owner_failed"); this.code = this.message; }
}
function requireValue(value, code = "owner_config") {
  if (!value) throw new FilterOwnerError(code);
}
function exact(value, keys) {
  return value && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).sort().join() === [...keys].sort().join();
}
function failure(error) {
  return error instanceof FilterOwnerError ? error : new FilterOwnerError("owner_failed");
}
function digest(value) {
  const bytes = new TextEncoder().encode(value);
  const hash = Cc["@mozilla.org/security/hash;1"].createInstance(Ci.nsICryptoHash);
  hash.init(hash.SHA256); hash.update(bytes, bytes.length);
  return Array.from(hash.finish(false), byte => byte.charCodeAt(0).toString(16).padStart(2, "0")).join("");
}

/** One explicit subscription owner for this profile/process lifetime.
 * expected/key come from independent browser-owned configuration, not a broker
 * reply or page. authorize() is an independently checked publication grant.
 * invalidate() must synchronously close existing native admission before edits.
 * A selection receipt proves membership/reloads, NOT filter bytes or approval.
 */
export async function openSelectionOwner(options) {
  let journal = null, actor = null, lifecycle = null;
  let closed = false, failed = false, openingActor = null, closing = null;
  let primary = null, urgent = null, refusal = null;
  let expected, key, assertCurrent, authorize, invalidate, clock;
  let last = null, bootDeadline, wallDeadline;
  const sample = () => {
    let value;
    try { value = clock(); } catch { throw new FilterOwnerError("owner_clock"); }
    requireValue(exact(value, ["bootMs", "wallMs"])
      && [value.bootMs, value.wallMs].every(n => Number.isSafeInteger(n) && n >= 0)
      && (!last || (value.bootMs >= last.bootMs && value.wallMs >= last.wallMs)), "owner_clock");
    last = { ...value };
    return last;
  };
  const current = () => {
    requireValue(!closed, "owner_closed");
    requireValue(assertCurrent() === true, "owner_context");
    sample();
    return true;
  };
  const grant = () => {
    current();
    return !failed && lifecycle?.status.choice !== "opted-out"
      && last.wallMs < wallDeadline && last.bootMs < bootDeadline && authorize() === true;
  };
  const barrier = () => requireValue(invalidate() === undefined);
  const command = async operation => {
    current();
    if (!actor) {
      requireValue(!openingActor, "owner_busy");
      openingActor = openSelectionActor({ key, assertCurrent: current,
        authorize: op => op === "add" ? grant() : current(), clock: sample });
      try { actor = await openingActor; }
      finally { openingActor = null; }
      if (closed) { actor.close(); throw new FilterOwnerError("owner_closed"); }
    }
    current();
    return actor.command(operation);
  };
  const run = (method, argument, slot = "primary") => {
    try {
      requireValue(!closed, "owner_closed");
      requireValue(!failed || method === "optOut", "owner_failed");
      requireValue(slot === "refusal" ? !refusal : slot === "urgent" ? !urgent
        : !primary && !urgent && !refusal, "owner_busy");
    } catch (error) { return Promise.reject(failure(error)); }
    // Invoke immediately: opt-out/revocation establish their synchronous barrier
    // and epoch before an earlier actor or journal promise can deliver a result.
    let action;
    try { action = lifecycle[method](argument); }
    catch (error) { action = Promise.reject(error); }
    const flight = Promise.resolve(action).catch(error => {
      failed = true;
      try { barrier(); } catch { /* still refuse subsequent ordinary work */ }
      throw failure(error);
    });
    if (slot === "refusal") refusal = flight;
    else if (slot === "urgent") urgent = flight;
    else primary = flight;
    return flight.finally(() => {
      if (slot === "refusal" && refusal === flight) refusal = null;
      if (slot === "urgent" && urgent === flight) urgent = null;
      if (slot === "primary" && primary === flight) primary = null;
    });
  };
  const close = () => {
    if (closing) return closing;
    closed = true;
    closing = (async () => {
      let error = false;
      try { lifecycle?.close(); } catch { error = true; }
      try { actor?.close(); } catch { error = true; }
      // Join lifecycle writes before Journal.close: busy opt-out may have queued
      // its sticky write behind an earlier save. Never close underneath it.
      await Promise.allSettled([primary, urgent, refusal].filter(Boolean));
      try { actor?.close(); } catch { error = true; }
      try { await journal?.close(); } catch { error = true; }
      if (error) { failed = true; throw new FilterOwnerError("owner_cleanup"); }
    })();
    return closing;
  };
  try {
    requireValue(exact(options, ["expected", "key", "assertCurrent", "authorize", "invalidate", "clock"])
      && Services.appinfo.processType === Ci.nsIXULRuntime.PROCESS_TYPE_DEFAULT
      && [options.assertCurrent, options.authorize, options.invalidate, options.clock]
        .every(value => typeof value === "function"));
    ({ assertCurrent, authorize, invalidate, clock } = options);
    try { expected = validateExpected(options.expected); key = validateKey(options.key); }
    catch { throw new FilterOwnerError("owner_config"); }
    wallDeadline = expected.authority_expires_unix_seconds * 1000;
    requireValue(Number.isSafeInteger(wallDeadline), "owner_config");
    const start = sample();
    bootDeadline = start.bootMs + Math.max(0, wallDeadline - start.wallMs);
    requireValue(Number.isSafeInteger(bootDeadline), "owner_config");
    current(); barrier();
    journal = await openSelectionJournal({ publisherKey: expected.publisher_key, name: expected.name,
      keyDigest: digest(JSON.stringify(["volparossa-filter-selection-key-v1", expected.manifest_id, key])) });
    current();
    lifecycle = new FilterSelectionLifecycle({ record: journal.record,
      save: value => journal.save(value), command, invalidate: barrier,
      assertCurrent: current, authorize: grant });
    return Object.freeze({
      get status() { return Object.freeze({ ...lifecycle.status, closed, failed }); },
      observe: () => run("observe"),
      enroll: () => run("enroll"),
      suspend(reason) {
        if (!["expired", "revoked"].includes(reason)) return Promise.reject(new FilterOwnerError("owner_config"));
        return run("suspend", reason, "urgent");
      },
      optOut: () => run("optOut", undefined, "refusal"),
      close,
    });
  } catch (error) {
    try { await close(); } catch { throw new FilterOwnerError("owner_cleanup"); }
    throw failure(error);
  }
}
