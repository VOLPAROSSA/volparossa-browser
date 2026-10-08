// SPDX-License-Identifier: GPL-3.0-only
// Disposable-profile fixture only: synthetic envelope/authority, NOT a signed
// publication, broker fetch, uBO engine witness or default enrollment.
import { FilterAssetError, openAssetChannel } from "./AssetChannel.sys.mjs";
import { openSelectionActor } from "./Actor.sys.mjs";
import { FilterActorError } from "./ActorContract.sys.mjs";

const { NetUtil } = ChromeUtils.importESModule("resource://gre/modules/NetUtil.sys.mjs");
const { setTimeout, clearTimeout } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
const BODY = "[Adblock Plus 2.0]\n||ads.asset.invalid^\n||track.asset.invalid^\n";
const ENVELOPE = "VOLPAROSSA synthetic unsigned asset fixture v1";
const PHASES = new Set(["start", "actor_open", "asset_open", "read_asset", "wrong_principal", "wrong_query",
  "asset_close", "late_request", "actor_close", "complete"]);
const ASSET_CHECKPOINTS = new Set(["ready", "request_current", "request_uri", "request_method", "request_context",
  "request_principals", "request_flags", "request_headers", "transfer", "transfer_identity", "transfer_callbacks",
  "prepare", "intercept", "body_complete"]);
const FAILURE_REASONS = new Set([
  "asset_config", "asset_package", "asset_context", "asset_authority", "asset_expired", "asset_clock",
  "asset_busy", "asset_closed", "asset_failed", "asset_cleanup",
  "actor_invalid", "actor_closed", "actor_busy", "actor_context", "actor_package", "actor_deadline",
  "actor_clock", "actor_authority", "actor_reply", "actor_asset", "actor_readiness_changed",
  "actor_cleanup", "actor_other", "invalid_config", "invalid_selection", "selection_changed",
  "unowned_selection", "reload_unproved", "invalid_receipt", "identity_lost", "selection_failed",
  "fixture_failed", "fixture_negative_deadline", "fixture_cleanup", "fixture_other", "fixture_invalid_reply",
]);
const encoder = new TextEncoder();
let used = false;

class AssetFixtureError extends Error {
  constructor(code) { super("asset_fixture_failed"); this.code = FAILURE_REASONS.has(code) ? code : "fixture_other"; }
}
function assetDiagnostic(asset) {
  if (!asset) return null;
  try {
    const diagnostic = asset.diagnostic, value = asset.status;
    if (!diagnostic || Object.keys(diagnostic).sort().join() !== "checkpoint"
      || !ASSET_CHECKPOINTS.has(diagnostic.checkpoint) || !value
      || Object.keys(value).sort().join() !== "accepted,closed,completed,denied"
      || typeof value.closed !== "boolean"
      || ![value.accepted, value.completed, value.denied].every(n => Number.isSafeInteger(n) && n >= 0 && n <= 65535)) {
      return null;
    }
    return { checkpoint: diagnostic.checkpoint, status: { closed: value.closed,
      accepted: value.accepted, completed: value.completed, denied: value.denied } };
  } catch { return null; } // Diagnostic failures never override the primary failure.
}
export function failureEnvelope(error, phase, diagnostic = null) {
  let reason = "fixture_other";
  try {
    if (error instanceof FilterAssetError || error instanceof FilterActorError || error instanceof AssetFixtureError) {
      // Never format an exception or evaluate an arbitrary .code/.message getter.
      // Only a known class's own data property can select an allowlisted enum.
      const descriptor = Object.getOwnPropertyDescriptor(error, "code");
      if (descriptor && Object.hasOwn(descriptor, "value") && FAILURE_REASONS.has(descriptor.value)) {
        reason = descriptor.value;
      }
    }
  } catch { /* An unexpected/proxy exception remains the fixed generic reason. */ }
  const envelope = { schema: 1, ok: false, phase: PHASES.has(phase) ? phase : "bootstrap", reason };
  // Only assetDiagnostic's closed copy is supplied by this private fixture.
  if (diagnostic !== null) envelope.asset_diagnostic = diagnostic;
  return envelope;
}
function check(value) { if (!value) throw new AssetFixtureError("fixture_failed"); }
function sha256(bytes) {
  const hash = Cc["@mozilla.org/security/hash;1"].createInstance(Ci.nsICryptoHash);
  hash.init(hash.SHA256); hash.update(bytes, bytes.length);
  return Array.from(hash.finish(false), byte => byte.charCodeAt(0).toString(16).padStart(2, "0")).join("");
}
function status(channel, closed, denied) {
  const value = channel.status;
  check(value && Object.keys(value).sort().join() === "accepted,closed,completed,denied"
    && value.closed === closed && value.accepted === 1 && value.completed === 1 && value.denied === denied);
  return { ...value };
}

export async function exercise() {
  check(!used); used = true;
  let phase = "start", actor = null, asset = null, current = true, outcome = null, diagnostic = null;
  const pending = new Set();
  const clock = () => ({ bootMs: Services.telemetry.msSinceProcessStartIncludingSuspend(), wallMs: Date.now() });
  const trace = Services.dirsvc.get("ProfD", Ci.nsIFile);
  trace.append("volparossa-filter-asset-phase.json");
  const mark = async value => {
    check(PHASES.has(value)); phase = value;
    await IOUtils.writeJSON(trace.path, { schema: 1, phase });
  };
  // These are separate negative parent channels, never substitutes for the
  // positive background-page XHR. Only native onStopRequest proves completion.
  async function deniedRequest(uri) {
    const channel = NetUtil.newChannel({ uri, loadingPrincipal: Services.scriptSecurityManager.getSystemPrincipal(),
      contentPolicyType: Ci.nsIContentPolicy.TYPE_XMLHTTPREQUEST,
      securityFlags: Ci.nsILoadInfo.SEC_ALLOW_CROSS_ORIGIN_SEC_CONTEXT_IS_NULL }).QueryInterface(Ci.nsIHttpChannel);
    let resolve, timer, bytes = 0, stopped = false;
    const done = new Promise(finish => { resolve = finish; });
    const request = { channel, done }; pending.add(request);
    try {
      const timeout = new Promise((_, reject) => {
        timer = setTimeout(() => {
          try { channel.cancel(Cr.NS_BINDING_ABORTED); } catch { /* process cleanup still owns this channel */ }
          reject(new AssetFixtureError("fixture_negative_deadline"));
        }, 5000);
      });
      channel.asyncOpen({
        QueryInterface: ChromeUtils.generateQI(["nsIStreamListener"]),
        onStartRequest() {},
        onDataAvailable(_request, stream, _offset, count) {
          bytes += count;
          // No negative request may expose any synthesized content. Drain only
          // a bounded chunk so cancellation does not leave an unread callback.
          if (count <= 1024) NetUtil.readInputStreamToString(stream, count);
          channel.cancel(Cr.NS_BINDING_ABORTED);
        },
        onStopRequest(_request, code) { stopped = true; pending.delete(request); resolve(code); },
      });
      const code = await Promise.race([done, timeout]);
      const flags = Ci.nsICachingChannel.LOAD_NO_NETWORK_IO | Ci.nsICachingChannel.LOAD_BYPASS_LOCAL_CACHE
        | Ci.nsIRequest.INHIBIT_CACHING;
      check(stopped && code === Cr.NS_BINDING_ABORTED && bytes === 0 && (channel.loadFlags & flags) === flags);
      return { stopped: true, aborted: true, bytes: 0, no_network_flags: true };
    } finally { clearTimeout(timer); }
  }
  try {
    await mark("start");
    const body = encoder.encode(BODY), manifest = encoder.encode(ENVELOPE);
    check(body.length === 63);
    const expected = Object.freeze({ publisher_key: "a".repeat(64), name: "synthetic-native-asset-fixture",
      manifest_id: sha256(manifest), authority_expires_unix_seconds: 4102444800 });
    const fetched = { filters: BODY, manifest_hex: Array.from(manifest, v => v.toString(16).padStart(2, "0")).join(""),
      snapshot: { generation: expected.manifest_id, publisher_key: expected.publisher_key, name: expected.name,
        revision: 1, sha256: sha256(body), bytes: body.length, rules: 2, grammar: "ubo-domain-block-v1",
        verified_at_unix_seconds: Math.floor(Date.now() / 1000), expires_unix_seconds: 4102444800,
        authorization_expires_unix_seconds: 4102444800 } };
    const key = `https://filters.volparossa.invalid/v1/${expected.manifest_id}.txt`;
    // Opening the fixed about.html actor first avoids spending the short asset
    // lease on the separate actor/package startup. It performs no asset read.
    await mark("actor_open");
    actor = await openSelectionActor({ key, assertCurrent: () => current,
      authorize: operation => current && ["observe", "readAsset"].includes(operation), clock });
    await mark("asset_open");
    asset = await openAssetChannel({ expected, fetched, assertCurrent: () => current,
      authorize: () => current, clock });
    check(asset.key === key);
    await mark("read_asset");
    const received = await actor.command("readAsset");
    // Original fetchFilterList normalizes trimEnd()+LF. This fixed body already
    // has exactly that form; no arbitrary whitespace/content repair is allowed.
    check(received.schema === 1 && received.operation === "readAsset" && received.text === BODY
      && received.selected === false && received.imported === false && received.preserved === true);
    const positive = { status: status(asset, false, 0), bytes: encoder.encode(received.text).length,
      sha256: sha256(encoder.encode(received.text)), exact_text: true, preserved: true,
      selected: false, imported: false };
    await mark("wrong_principal");
    const wrongPrincipal = await deniedRequest(key);
    status(asset, false, 1);
    await mark("wrong_query");
    const wrongQuery = await deniedRequest(key + "?unexpected=1");
    status(asset, false, 2);
    await mark("asset_close"); asset.close();
    const closed = status(asset, true, 2);
    await mark("late_request");
    const late = await deniedRequest(key);
    status(asset, true, 2);
    await mark("actor_close"); actor.close(); actor = null; current = false;
    check(pending.size === 0);
    await mark("complete");
    outcome = { schema: 1, ok: true, phase, synthetic_manifest_sha256: expected.manifest_id,
      positive, wrong_principal: wrongPrincipal, wrong_query: wrongQuery, after_close: late,
      final_status: closed, cleanup_complete: true };
  } catch (error) {
    diagnostic = assetDiagnostic(asset); // snapshot before finally closes the owner
    outcome = failureEnvelope(error, phase, diagnostic);
  } finally {
    if (diagnostic === null) diagnostic = assetDiagnostic(asset);
    current = false;
    let failed = false;
    try { asset?.close(); } catch { failed = true; }
    try { actor?.close(); } catch { failed = true; }
    for (const request of pending) {
      try { request.channel.cancel(Cr.NS_BINDING_ABORTED); } catch { failed = true; }
    }
    if (pending.size !== 0) failed = true;
    // Cleanup uncertainty still fails even after positive checks. Report the
    // closed cleanup reason instead of losing it in the bootstrap rejection.
    if (failed) outcome = failureEnvelope(new AssetFixtureError("fixture_cleanup"), phase, diagnostic);
  }
  return outcome;
}
