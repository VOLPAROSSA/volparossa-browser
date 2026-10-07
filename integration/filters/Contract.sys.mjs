// SPDX-License-Identifier: GPL-3.0-only
// Closed contract for an independently configured, same-owner public-filter broker.
// Signature/publisher policy verification belongs to the trusted core; this module
// additionally binds exact original content/envelope bytes, not an arbitrary URL.
import {
  VERSION, MAX_REQUEST, MAX_RESPONSE, MAX_REQUESTS, FilterBrokerError, requireFilter, exactKeys,
} from "./Frame.sys.mjs";

export const ERROR_CODES = Object.freeze([
  "invalid_request", "duplicate_request", "handshake_required", "busy", "unavailable",
  "revoked", "clock_error", "expired", "invalid_snapshot",
]);
const MAX_TEXT = 1024 * 1024;
const encoder = new TextEncoder();
const hex = value => typeof value === "string" && value.length === 64 && /^[0-9a-f]{64}$/.test(value) && !/^0+$/.test(value);
const natural = value => Number.isSafeInteger(value) && value >= 0;
const printableName = value => typeof value === "string" && value.length > 0 && value.length <= 128
  && !/[^\x20-\x7e]/.test(value);

export function validateExpected(value) {
  requireFilter(exactKeys(value, ["publisher_key", "name", "manifest_id", "authority_expires_unix_seconds"])
    && hex(value.publisher_key) && hex(value.manifest_id) && printableName(value.name)
    && natural(value.authority_expires_unix_seconds) && value.authority_expires_unix_seconds > 0,
  "not_configured");
  return Object.freeze({ ...value });
}

function stillCurrent(expected, snapshot, nowSeconds) {
  const now = nowSeconds();
  requireFilter(natural(now) && expected.authority_expires_unix_seconds > now, "expired");
  if (snapshot) requireFilter(snapshot.authorization_expires_unix_seconds > now
    && snapshot.verified_at_unix_seconds <= now, "expired");
}

function metadata(value, expected) {
  requireFilter(exactKeys(value, ["generation", "publisher_key", "name", "revision", "sha256",
    "bytes", "rules", "grammar", "verified_at_unix_seconds", "expires_unix_seconds",
    "authorization_expires_unix_seconds"])
    && value.generation === expected.manifest_id && value.publisher_key === expected.publisher_key
    && value.name === expected.name && natural(value.revision) && hex(value.sha256)
    && natural(value.bytes) && value.bytes > 0 && value.bytes <= MAX_TEXT
    && natural(value.rules) && value.rules > 0 && value.rules <= 4096
    && value.grammar === "ubo-domain-block-v1" && natural(value.verified_at_unix_seconds)
    && natural(value.expires_unix_seconds) && natural(value.authorization_expires_unix_seconds)
    && value.verified_at_unix_seconds < value.expires_unix_seconds
    && value.authorization_expires_unix_seconds === Math.min(value.expires_unix_seconds,
      expected.authority_expires_unix_seconds));
  return Object.freeze({ ...value });
}

export function ruleCount(text) {
  requireFilter(typeof text === "string" && text.length > 0 && text.length <= MAX_TEXT
    && /^[\x00-\x7f]*$/.test(text));
  const lines = text.split("\n");
  requireFilter(lines.length <= 8192);
  let count = 0;
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index].endsWith("\r") ? lines[index].slice(0, -1) : lines[index];
    if (!line || (index === 0 && line === "[Adblock Plus 2.0]")) continue;
    requireFilter(line.length <= 257 && line.startsWith("||") && line.endsWith("^"));
    const domain = line.slice(2, -1);
    const labels = domain.split(".");
    requireFilter(domain.length <= 253 && labels.length >= 2
      && labels.every(label => label.length >= 1 && label.length <= 63
        && /^[a-z0-9-]+$/.test(label) && !label.startsWith("-") && !label.endsWith("-"))
      && /^[a-z]{2,}$/.test(labels.at(-1)));
    count++;
    requireFilter(count <= 4096);
  }
  requireFilter(count > 0);
  return count;
}

export async function validateResponse(value, { id, operation, expected, nowSeconds, sha256 }) {
  requireFilter(value?.version === VERSION && (value.id === id
    || (value.id === null && value.event === "error")));
  if (value.event === "error") {
    requireFilter(exactKeys(value, ["version", "id", "event", "code"]) && ERROR_CODES.includes(value.code));
    throw new FilterBrokerError(value.code);
  }
  stillCurrent(expected, null, nowSeconds);
  if (operation === "capabilities") {
    requireFilter(exactKeys(value, ["version", "id", "event", "protocol", "same_uid_only",
      "public_content_only", "fixed_publication", "browser_activation", "max_request_bytes",
      "max_response_bytes", "max_requests", "max_connections", "publisher_key", "name", "manifest_id",
      "authority_expires_unix_seconds"])
      && value.event === "capabilities" && value.protocol === "volparossa-filter"
      && value.same_uid_only === true && value.public_content_only === true
      && value.fixed_publication === true && value.browser_activation === false
      && value.max_request_bytes === MAX_REQUEST && value.max_response_bytes === MAX_RESPONSE
      && value.max_requests === MAX_REQUESTS && value.max_connections === 4
      && value.publisher_key === expected.publisher_key && value.name === expected.name
      && value.manifest_id === expected.manifest_id
      && value.authority_expires_unix_seconds === expected.authority_expires_unix_seconds);
    return Object.freeze({ ...value });
  }
  if (operation === "status") {
    requireFilter(exactKeys(value, ["version", "id", "event", "state", "snapshot"]) && value.event === "status"
      && ["unavailable", "ready"].includes(value.state));
    if (value.state === "unavailable") {
      requireFilter(value.snapshot === null);
      return Object.freeze({ state: "unavailable", snapshot: null });
    }
    const snapshot = metadata(value.snapshot, expected);
    stillCurrent(expected, snapshot, nowSeconds);
    return Object.freeze({ state: "ready", snapshot });
  }
  requireFilter(operation === "fetch" && exactKeys(value, ["version", "id", "event", "snapshot", "filters", "manifest_hex"])
    && value.event === "snapshot");
  const snapshot = metadata(value.snapshot, expected);
  stillCurrent(expected, snapshot, nowSeconds);
  requireFilter(typeof value.manifest_hex === "string" && value.manifest_hex.length > 0
    && value.manifest_hex.length <= 2 * 65536 && value.manifest_hex.length % 2 === 0
    && !/[^0-9a-f]/.test(value.manifest_hex)
    && ruleCount(value.filters) === snapshot.rules);
  const text = encoder.encode(value.filters);
  requireFilter(text.length === snapshot.bytes);
  const manifest = Uint8Array.from(value.manifest_hex.match(/../g), byte => Number.parseInt(byte, 16));
  // SHA-256 of the canonical signed envelope is its immutable manifest ID.
  requireFilter(await sha256(text) === snapshot.sha256 && await sha256(manifest) === snapshot.generation);
  stillCurrent(expected, snapshot, nowSeconds);
  return Object.freeze({ snapshot, filters: value.filters, manifest_hex: value.manifest_hex });
}
