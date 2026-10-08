// SPDX-License-Identifier: GPL-3.0-only
// Disposable-profile fixture ONLY. Synthetic grants are not publisher approval,
// verified filter bytes, a native request barrier or production default policy.
import { openSelectionOwner } from "./Owner.sys.mjs";

const KEY = "http://127.0.0.1:18765/supplement.txt";
const EXPECTED = Object.freeze({ publisher_key: "a".repeat(64), name: "synthetic-selection-fixture",
  manifest_id: "b".repeat(64), authority_expires_unix_seconds: 4102444800 });
const OPERATIONS = new Set(["open", "observe", "enroll", "suspend", "resume", "close"]);
let owner = null, opened = false, current = true, grant = true, busy = false;
let invalidations = 0;

function check(value) { if (!value) throw new Error("selection_fixture_failed"); }
function status() {
  const value = owner.status;
  check(value && Object.keys(value).sort().join() === "choice,closed,failed,schema,state"
    && value.schema === 1 && ["eligible", "opted-out"].includes(value.choice)
    && ["fresh", "pending", "active", "suspended"].includes(value.state)
    && typeof value.closed === "boolean" && typeof value.failed === "boolean");
  return { ...value };
}

// One module instance/owner per actual process. Do not close/reopen the journal
// between commands or append module-cache query strings to evade its lease.
export async function exercise(operation) {
  if (!OPERATIONS.has(operation) || busy) return { schema: 1, ok: false, phase: "command" };
  busy = true;
  let result = null;
  try {
    if (operation === "open") {
      check(!opened);
      opened = true;
      owner = await openSelectionOwner({ expected: EXPECTED, key: KEY,
        assertCurrent: () => current, authorize: () => grant,
        invalidate: () => { check(invalidations < 32); invalidations++; },
        clock: () => ({ bootMs: Services.telemetry.msSinceProcessStartIncludingSuspend(), wallMs: Date.now() }),
      });
    } else {
      check(owner && current);
      if (operation === "close") { await owner.close(); current = false; }
      else if (operation === "suspend") { grant = false; result = await owner.suspend("revoked"); }
      else if (operation === "resume") { grant = true; result = await owner.enroll(); }
      else result = await owner[operation]();
    }
    return { schema: 1, ok: true, phase: operation, status: status(), result, invalidations };
  } catch {
    // Never read/format unknown exception properties or export storage/URLs.
    current = false;
    try { if (owner) await owner.close(); } catch { /* original operation failed */ }
    return { schema: 1, ok: false, phase: operation };
  } finally { busy = false; }
}
