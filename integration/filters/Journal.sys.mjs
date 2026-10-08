// SPDX-License-Identifier: GPL-3.0-only
// Parent-only profile storage. Opening does not enroll a list or grant admission.
import { selectionJournal } from "./Selection.sys.mjs";

const { Sqlite } = ChromeUtils.importESModule("resource://gre/modules/Sqlite.sys.mjs");
const leases = new Set();
const MAX_DATABASE = 1024 * 1024;
const MAX_SUBSCRIPTIONS = 32;
const CODES = new Set(["journal_config", "journal_private_profile", "journal_private_file",
  "journal_incomplete", "journal_binding", "journal_schema", "journal_capacity",
  "journal_changed", "journal_transition", "journal_busy", "journal_closed", "journal_failed"]);
const HEX = /^[0-9a-f]{64}$/;
const FRESH = Object.freeze({ schema: 1, choice: "eligible", state: "fresh" });
const SELECT = "SELECT key_digest, choice, phase, revision FROM selection_state WHERE subscription = :subscription";
const CREATE = `CREATE TABLE selection_state (
  subscription TEXT PRIMARY KEY NOT NULL,
  key_digest TEXT NOT NULL,
  choice TEXT NOT NULL CHECK(choice IN ('eligible','opted-out')),
  phase TEXT NOT NULL CHECK(phase IN ('fresh','pending','active','suspended')),
  revision INTEGER NOT NULL CHECK(revision >= 0),
  CHECK(choice != 'opted-out' OR phase != 'active')
)`;

export class FilterJournalError extends Error {
  constructor(code) {
    const closed = CODES.has(code) ? code : "journal_failed";
    super(closed);
    this.code = closed;
  }
}
function check(value, code) { if (!value) throw new FilterJournalError(code); }
function failure(error) {
  return error instanceof FilterJournalError ? error : new FilterJournalError("journal_failed");
}
function exact(value, keys) {
  return value && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).sort().join() === [...keys].sort().join();
}
function hash(bytes) {
  const digest = Cc["@mozilla.org/security/hash;1"].createInstance(Ci.nsICryptoHash);
  digest.init(digest.SHA256);
  digest.update(bytes, bytes.length);
  return Array.from(digest.finish(false), byte => byte.charCodeAt(0).toString(16).padStart(2, "0")).join("");
}
function binding(value) {
  check(exact(value, ["publisherKey", "name", "keyDigest"])
    && typeof value.publisherKey === "string" && HEX.test(value.publisherKey) && !/^0+$/.test(value.publisherKey)
    && typeof value.name === "string" && value.name.length > 0 && value.name.length <= 128
    && !/[^\x20-\x7e]/.test(value.name)
    && typeof value.keyDigest === "string" && HEX.test(value.keyDigest) && !/^0+$/.test(value.keyDigest), "journal_config");
  // Manifest/authority rollover must not reset user choice. Only this public
  // publisher/name digest is the subscription namespace; never custom URLs.
  return Object.freeze({ subscription: hash(new TextEncoder().encode(JSON.stringify(
    ["volparossa-filter-selection-v1", value.publisherKey, value.name]))), keyDigest: value.keyDigest });
}
function child(parent, name) {
  const file = parent.clone(); file.append(name); return file;
}
function directory(file) {
  check(file.exists() && file.isDirectory() && !file.isSymlink()
    && (file.permissions & 0o7777) === 0o700, "journal_private_profile");
}
function safeFile(file, limit = MAX_DATABASE) {
  // nsIFile.isSymlink uses lstat on Linux: a genuinely absent file throws,
  // whereas a dangling symlink still returns true and must be rejected.
  let link;
  try { link = file.isSymlink(); }
  catch (error) {
    check(error.result === Cr.NS_ERROR_FILE_NOT_FOUND, "journal_private_file");
    return;
  }
  check(!link, "journal_private_file");
  if (!file.exists()) return;
  check(!file.isSymlink() && file.isFile() && (file.permissions & 0o7777) === 0o600
    && file.fileSize <= limit, "journal_private_file");
}
function paths(create = false) {
  check(Services.appinfo.processType === Ci.nsIXULRuntime.PROCESS_TYPE_DEFAULT, "journal_config");
  const profile = Services.dirsvc.get("ProfD", Ci.nsIFile);
  directory(profile);
  for (let part = profile; part; part = part.parent) {
    const normalized = part.clone(); normalized.normalize();
    check(!part.isSymlink() && normalized.path === part.path, "journal_private_profile");
  }
  const root = child(profile, "volparossa-filter-state");
  if (!root.exists() && create) root.create(Ci.nsIFile.DIRECTORY_TYPE, 0o700);
  directory(root);
  const db = child(root, "selection.sqlite");
  const marker = child(root, "attempted");
  safeFile(db); safeFile(marker, 16);
  // We use DELETE journaling. A surviving hot journal is SQLite's to recover;
  // never delete it, a database or a marker to manufacture fresh state.
  safeFile(child(root, "selection.sqlite-journal"));
  safeFile(child(root, "selection.sqlite-wal"));
  safeFile(child(root, "selection.sqlite-shm"));
  check(!child(root, "selection.sqlite-wal").exists()
    && !child(root, "selection.sqlite-shm").exists(), "journal_incomplete");
  return { profile, root, db, marker };
}
function recheck(files) {
  const current = paths();
  check(current.profile.path === files.profile.path && current.root.path === files.root.path
    && current.db.path === files.db.path && current.db.exists() && current.marker.exists(), "journal_changed");
}
function scopeCount(root) {
  const entries = root.directoryEntries.QueryInterface(Ci.nsIDirectoryEnumerator);
  let count = 0, total = 0;
  try {
    while (entries.hasMoreElements()) {
      check(++total <= MAX_SUBSCRIPTIONS + 5, "journal_capacity");
      const entry = entries.getNext().QueryInterface(Ci.nsIFile);
      if (/^[0-9a-f]{64}\.attempted$/.test(entry.leafName)) {
        safeFile(entry, 80);
        count++;
      } else check(["attempted", "selection.sqlite", "selection.sqlite-journal",
        "selection.sqlite-wal", "selection.sqlite-shm"].includes(entry.leafName), "journal_incomplete");
    }
  } finally { entries.close(); }
  check(count <= MAX_SUBSCRIPTIONS, "journal_capacity");
  return count;
}
function record(row, expected) {
  check(row && row.getResultByName("key_digest") === expected.keyDigest, "journal_binding");
  const revision = row.getResultByName("revision");
  check(Number.isSafeInteger(revision) && revision >= 0, "journal_schema");
  let value;
  try { value = selectionJournal({ schema: 1, choice: row.getResultByName("choice"), state: row.getResultByName("phase") }); }
  catch { throw new FilterJournalError("journal_schema"); }
  return { value, revision };
}
async function read(connection, expected) {
  const rows = await connection.execute(SELECT, { subscription: expected.subscription });
  check(rows.length === 1, "journal_changed");
  return record(rows[0], expected);
}

/** Explicit profile owner. A changed fixed key requires separate old-key recovery.
 * One open attempt per database per module/process lifetime. Sqlite.sys.mjs can
 * resolve close after swallowing a native asyncClose error, and a rejected open
 * can already have started an unjoined native close. Neither permits safe reopen.
 * Keep the owner alive for its normal lifetime; closing requires browser restart
 * before a new owner can use this journal. This is not a native-close proof.
 */
export async function openSelectionJournal(expected) {
  let connection, lease, ownedLease = false, openAttempted = false;
  try {
    const owner = binding(expected);
    const files = paths(true);
    // One database owner, not only one scope owner: no initialization/PRAGMA
    // races or unbounded database connections. A future multi-feed owner shares
    // one coordinated journal rather than opening this repeatedly in parallel.
    lease = files.db.path;
    check(!leases.has(lease), "journal_busy");
    leases.add(lease);
    ownedLease = true;
    const isNew = !files.db.exists() && !files.marker.exists();
    check(isNew || (files.db.exists() && files.marker.exists()), "journal_incomplete");
    if (isNew) {
      files.marker.create(Ci.nsIFile.NORMAL_FILE_TYPE, 0o600);
      await IOUtils.writeUTF8(files.marker.path, "attempted\n", { flush: true });
      files.db.create(Ci.nsIFile.NORMAL_FILE_TYPE, 0o600);
    } else {
      check(await IOUtils.readUTF8(files.marker.path, { maxBytes: 16 }) === "attempted\n", "journal_incomplete");
    }
    recheck(files);
    scopeCount(files.root);
    openAttempted = true;
    connection = await Sqlite.openConnection({ path: files.db.path, sharedMemoryCache: false,
      defaultTransactionType: "IMMEDIATE" });
    const scopeMarker = child(files.root, owner.subscription + ".attempted");
    safeFile(scopeMarker, 80);
    const scopeAttempted = scopeMarker.exists();
    const scopeBytes = owner.keyDigest + "\n";
    if (scopeAttempted) check(await IOUtils.readUTF8(scopeMarker.path, { maxBytes: 80 }) === scopeBytes, "journal_binding");
    const checkScope = async () => {
      safeFile(scopeMarker, 80);
      check(scopeMarker.exists() && await IOUtils.readUTF8(scopeMarker.path, { maxBytes: 80 }) === scopeBytes, "journal_changed");
    };
    // EXTRA adds directory synchronization to rollback-journal deletion. Check
    // the effective settings, not merely whether the PRAGMA call resolved.
    const mode = await connection.execute("PRAGMA journal_mode=DELETE");
    check(mode.length === 1 && mode[0].getResultByName("journal_mode") === "delete", "journal_schema");
    await connection.execute("PRAGMA synchronous=EXTRA");
    const sync = await connection.execute("PRAGMA synchronous");
    check(sync.length === 1 && sync[0].getResultByName("synchronous") === 3, "journal_schema");
    await connection.execute("PRAGMA busy_timeout=1000");
    await connection.execute("PRAGMA trusted_schema=OFF");
    await connection.execute("PRAGMA temp_store=MEMORY");
    let stored;
    await connection.executeTransaction(async () => {
      if (isNew) {
        await connection.execute(CREATE);
        await connection.execute("PRAGMA user_version=1");
      }
      const version = await connection.execute("PRAGMA user_version");
      check(version.length === 1 && version[0].getResultByName("user_version") === 1, "journal_schema");
      const rows = await connection.execute(SELECT, { subscription: owner.subscription });
      check(rows.length <= 1, "journal_schema");
      if (rows.length === 0) {
        check(!scopeAttempted, "journal_changed");
        check(scopeCount(files.root) < MAX_SUBSCRIPTIONS, "journal_capacity");
        const count = await connection.execute("SELECT COUNT(*) AS n FROM selection_state");
        check(count.length === 1 && Number.isSafeInteger(count[0].getResultByName("n"))
          && count[0].getResultByName("n") < MAX_SUBSCRIPTIONS, "journal_capacity");
        // Separate attempted evidence makes a missing row distinguishable from
        // a new subscription. A crash/rollback after this write remains unknown,
        // never permission to recreate eligible/fresh on the next open.
        scopeMarker.create(Ci.nsIFile.NORMAL_FILE_TYPE, 0o600);
        await IOUtils.writeUTF8(scopeMarker.path, scopeBytes, { flush: true });
        await connection.execute("INSERT INTO selection_state (subscription,key_digest,choice,phase,revision) VALUES (:subscription,:key_digest,'eligible','fresh',0)",
          { subscription: owner.subscription, key_digest: owner.keyDigest });
      } else check(scopeAttempted, "journal_changed");
      stored = await read(connection, owner);
      await checkScope();
      recheck(files);
    });
    let closed = false, poisoned = false, flight = null, closing = null;
    const ensureOpen = () => check(!closed && !poisoned, "journal_closed");
    const result = Object.freeze({
      get record() { ensureOpen(); return stored.value; },
      save(value) {
        let next;
        try {
          ensureOpen(); check(!flight, "journal_busy"); next = selectionJournal(value);
          check(!(next.state === "fresh" && next.choice === "eligible")
            && !(stored.value.choice === "opted-out" && next.choice !== "opted-out")
            && (next.state === "pending" || stored.value.state === "pending"), "journal_transition");
        } catch (error) { return Promise.reject(failure(error)); }
        const task = (async () => {
          try {
            recheck(files);
            await checkScope();
            let nextStored;
            await connection.executeTransaction(async () => {
              const actual = await read(connection, owner);
              check(actual.revision === stored.revision && actual.value.choice === stored.value.choice
                && actual.value.state === stored.value.state && actual.revision < Number.MAX_SAFE_INTEGER, "journal_changed");
              await connection.execute("UPDATE selection_state SET choice=:choice, phase=:phase, revision=:revision WHERE subscription=:subscription",
                { choice: next.choice, phase: next.state, revision: actual.revision + 1, subscription: owner.subscription });
              nextStored = await read(connection, owner);
              check(nextStored.value.choice === next.choice && nextStored.value.state === next.state
                && nextStored.revision === actual.revision + 1, "journal_changed");
              recheck(files);
              await checkScope();
            });
            // Success follows COMMIT under EXTRA, not a statement ACK.
            recheck(files); await checkScope(); stored = nextStored;
          } catch (error) { poisoned = true; throw failure(error); }
        })();
        flight = task;
        return task.finally(() => { if (flight === task) flight = null; });
      },
      close() {
        if (closing) return closing;
        closed = true;
        closing = (async () => {
          try {
            if (flight) await flight.catch(() => {});
            await connection.close();
            // A resolved wrapper close is not evidence that the underlying
            // native close succeeded. The process-lifetime tombstone remains.
          }
          catch (error) { throw failure(error); }
        })();
        return closing;
      },
    });
    return result;
  } catch (error) {
    try { if (connection) await connection.close(); } catch { /* retain lease */ }
    if (ownedLease && !openAttempted) leases.delete(lease);
    throw failure(error);
  }
}
