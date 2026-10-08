// SPDX-License-Identifier: GPL-3.0-only
// Runs the actual module and SQL against Node SQLite/files. The Gecko service
// adapters are fixtures: this is not a Firefox runtime or power-loss test.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";
import { DatabaseSync } from "node:sqlite";
import { selectionJournal, FilterSelectionLifecycle } from "../integration/filters/Selection.sys.mjs";

const EXPECTED = Object.freeze({ publisherKey: "1".repeat(64), name: "public-supplement", keyDigest: "2".repeat(64) });
const state = (choice, phase) => ({ schema: 1, choice, state: phase });
const fresh = () => state("eligible", "fresh");
const directory = fileURLToPath(new URL("../build/", import.meta.url));
const source = fs.readFileSync(new URL("../integration/filters/Journal.sys.mjs", import.meta.url), "utf8");
const closed = expected => error => error.code === expected && error.message === expected;
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
const defer = () => { let resolve; return { promise: new Promise(yes => { resolve = yes; }), resolve }; };

class File {
  constructor(location) { this.path = location; }
  clone() { return new File(this.path); }
  append(name) { this.path = path.join(this.path, name); }
  normalize() { this.path = fs.realpathSync(this.path); }
  QueryInterface() { return this; }
  get leafName() { return path.basename(this.path); }
  get directoryEntries() {
    const entries = fs.readdirSync(this.path); let index = 0, closed = false;
    return { QueryInterface() { return this; }, hasMoreElements() { assert.equal(closed, false); return index < entries.length; },
      getNext: () => new File(path.join(this.path, entries[index++])), close() { closed = true; } };
  }
  get parent() { const parent = path.dirname(this.path); return parent === this.path ? null : new File(parent); }
  exists() { return fs.existsSync(this.path); }
  isSymlink() { try { return fs.lstatSync(this.path).isSymbolicLink(); } catch (e) {
    if (e.code === "ENOENT") throw { result: "NS_ERROR_FILE_NOT_FOUND" }; throw e;
  } }
  isDirectory() { return fs.statSync(this.path).isDirectory(); }
  isFile() { return fs.statSync(this.path).isFile(); }
  get permissions() { return fs.statSync(this.path).mode & 0o7777; }
  get fileSize() { return fs.statSync(this.path).size; }
  create(type, mode) {
    if (type === "directory") fs.mkdirSync(this.path, { mode });
    else fs.closeSync(fs.openSync(this.path, "wx", mode));
  }
}

function fixture(t, options = {}) {
  fs.mkdirSync(directory, { recursive: true });
  const root = fs.mkdtempSync(path.join(directory, "filter-journal-test-"));
  const profile = path.join(root, "profile");
  fs.mkdirSync(profile, { mode: 0o700 });
  const connections = new Set();
  const events = [];
  let commits = 0;
  let opens = 0;
  const sqlite = { async openConnection(config) {
    assert.equal(config.sharedMemoryCache, false);
    assert.equal(config.defaultTransactionType, "IMMEDIATE");
    opens++;
    const db = new DatabaseSync(config.path);
    connections.add(db);
    if (options.open) await options.open();
    return {
      async execute(sql, parameters) {
        events.push(sql);
        if (options.sql) await options.sql(sql, parameters, db);
        const statement = db.prepare(sql);
        statement.setReadBigInts(true);
        const rows = parameters === undefined ? statement.all() : statement.all(parameters);
        // mozIStorageRow exposes numeric columns as JS numbers. Preserve that
        // behavior so the production safe-integer guard, not Node's default
        // out-of-range exception, is exercised for corrupt revision counters.
        return rows.map(value => ({ getResultByName: key =>
          typeof value[key] === "bigint" ? Number(value[key]) : value[key] }));
      },
      async executeTransaction(action) {
        db.exec("BEGIN IMMEDIATE");
        try {
          const value = await action();
          if (options.beforeCommit) await options.beforeCommit(commits + 1);
          db.exec("COMMIT"); commits++;
          if (options.afterCommit) await options.afterCommit(commits);
          return value;
        } catch (error) { if (db.isTransaction) db.exec("ROLLBACK"); throw error; }
      },
      async close() {
        if (options.close) await options.close();
        if (options.swallowClose) return;
        db.close(); connections.delete(db);
      },
    };
  } };
  const environment = {
    selectionJournal, TextEncoder, console,
    ChromeUtils: { importESModule: url => {
      assert.equal(url, "resource://gre/modules/Sqlite.sys.mjs"); return { Sqlite: sqlite };
    } },
    Services: { appinfo: { processType: 0 }, dirsvc: { get: name => {
      assert.equal(name, "ProfD"); return new File(profile);
    } } },
    Ci: { nsIXULRuntime: { PROCESS_TYPE_DEFAULT: 0 }, nsIFile: { DIRECTORY_TYPE: "directory", NORMAL_FILE_TYPE: "file" } },
    Cr: { NS_ERROR_FILE_NOT_FOUND: "NS_ERROR_FILE_NOT_FOUND" },
    Cc: { "@mozilla.org/security/hash;1": { createInstance: () => {
      let hash; return { SHA256: "sha256", init: () => { hash = createHash("sha256"); },
        update: bytes => hash.update(bytes), finish: () => hash.digest("latin1") };
    } } },
    IOUtils: {
      async writeUTF8(location, text, config) {
        assert.equal(config.flush, true);
        const fd = fs.openSync(location, "r+");
        try { fs.writeFileSync(fd, text); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
      },
      async readUTF8(location, { maxBytes }) {
        const bytes = fs.readFileSync(location); assert.ok(bytes.length <= maxBytes); return bytes.toString("utf8");
      },
    },
  };
  const importLine = 'import { selectionJournal } from "./Selection.sys.mjs";';
  assert.equal(source.split(importLine).length, 2);
  const load = () => {
    const context = vm.createContext({ ...environment });
    vm.runInContext(source.replace(importLine, "").replaceAll("export class ", "class ")
      .replaceAll("export async function ", "async function ")
      + "\nglobalThis.open = openSelectionJournal;", context);
    return context;
  };
  let context = load();
  t.after(() => {
    for (const db of connections) db.close();
    assert.ok(root.startsWith(path.join(directory, "filter-journal-test-")));
    fs.rmSync(root, { recursive: true });
  });
  const dbPath = path.join(profile, "volparossa-filter-state", "selection.sqlite");
  return { profile, root, dbPath, events, open: (expected = EXPECTED) => context.open(expected),
    // Simulates a NEW process/module owner only after fixture connections have
    // actually closed. This is not proof of native Gecko close or process exit.
    restart: () => { assert.equal(connections.size, 0); context = load(); },
    commits: () => commits, opens: () => opens, raw: () => new DatabaseSync(dbPath) };
}

test("real SQLite persists pending, active and sticky opt-out across simulated process restart", async t => {
  const f = fixture(t);
  let journal = await f.open();
  assert.deepEqual(journal.record, fresh());
  await journal.save(state("eligible", "pending"));
  await journal.close();
  f.restart();
  journal = await f.open();
  assert.deepEqual(journal.record, state("eligible", "pending"));
  await journal.save(state("eligible", "active"));
  await journal.save(state("opted-out", "pending"));
  await journal.save(state("opted-out", "suspended"));
  await journal.close();
  f.restart();
  journal = await f.open();
  assert.deepEqual(journal.record, state("opted-out", "suspended"));
  await assert.rejects(journal.save(state("eligible", "pending")), closed("journal_transition"));
  await journal.close();
  assert.equal(fs.statSync(f.dbPath).mode & 0o7777, 0o600);
  assert.equal(fs.statSync(path.dirname(f.dbPath)).mode & 0o7777, 0o700);
  assert.ok(f.events.includes("PRAGMA synchronous=EXTRA"));
  assert.ok(f.events.includes("PRAGMA journal_mode=DELETE"));
  const bytes = fs.readFileSync(f.dbPath);
  assert.equal(bytes.includes(Buffer.from(EXPECTED.name)), false);
  assert.equal(bytes.includes(Buffer.from("https://")), false);
});

test("new immutable key cannot reset a subscription, publisher/name are isolated", async t => {
  const f = fixture(t); let journal = await f.open();
  await journal.save(state("opted-out", "pending"));
  await journal.save(state("opted-out", "fresh")); await journal.close();
  f.restart();
  await assert.rejects(f.open({ ...EXPECTED, keyDigest: "3".repeat(64) }), closed("journal_binding"));
  f.restart();
  journal = await f.open({ ...EXPECTED, name: "another-public-feed" });
  assert.deepEqual(journal.record, fresh()); await journal.close();
  f.restart();
  journal = await f.open(); assert.deepEqual(journal.record, state("opted-out", "fresh")); await journal.close();
});

test("duplicate open cannot release another owner's database lease", async t => {
  const f = fixture(t); const journal = await f.open();
  for (let i = 0; i < 3; i++) await assert.rejects(f.open(), closed("journal_busy"));
  assert.equal(f.opens(), 1);
  await journal.close();
  await assert.rejects(f.open(), closed("journal_busy")); assert.equal(f.opens(), 1);
  f.restart(); const next = await f.open(); await next.close();
});

test("no success before commit; close joins current write and forbids new work", async t => {
  const gate = defer(); const f = fixture(t, { beforeCommit: count => count === 2 ? gate.promise : undefined });
  const journal = await f.open();
  let saved = false, closedDone = false;
  const write = journal.save(state("eligible", "pending")).then(() => { saved = true; });
  await flush(); assert.equal(saved, false);
  await assert.rejects(journal.save(state("eligible", "pending")), closed("journal_busy"));
  const closing = journal.close().then(() => { closedDone = true; });
  await flush(); assert.equal(closedDone, false);
  await assert.rejects(journal.save(state("eligible", "active")), closed("journal_closed"));
  gate.resolve(); await write; await closing;
  f.restart();
  const reopened = await f.open(); assert.deepEqual(reopened.record, state("eligible", "pending")); await reopened.close();
});

test("failed transaction rolls back, poisons writer and preserves original record", async t => {
  let failed = false;
  const f = fixture(t, { beforeCommit: count => { if (count === 2 && !failed) {
    failed = true; throw { get message() { throw Error("private getter"); } };
  } } });
  const journal = await f.open();
  await assert.rejects(journal.save(state("eligible", "pending")), closed("journal_failed"));
  await assert.rejects(journal.save(state("eligible", "pending")), closed("journal_closed"));
  await journal.close(); f.restart(); const next = await f.open(); assert.deepEqual(next.record, fresh()); await next.close();
});

test("lost post-commit acknowledgement retains pending after reopen, not fresh", async t => {
  const f = fixture(t, { afterCommit: count => { if (count === 2) throw Error("private-raw-canary"); } });
  const journal = await f.open();
  await assert.rejects(journal.save(state("eligible", "pending")), closed("journal_failed"));
  await journal.close(); f.restart(); const next = await f.open();
  assert.deepEqual(next.record, state("eligible", "pending")); await next.close();
});

test("concurrent external record change is detected before update", async t => {
  const f = fixture(t); const journal = await f.open();
  const raw = f.raw(); raw.exec("UPDATE selection_state SET choice='opted-out',revision=revision+1"); raw.close();
  await assert.rejects(journal.save(state("eligible", "pending")), closed("journal_changed"));
  await journal.close(); f.restart(); const next = await f.open(); assert.equal(next.record.choice, "opted-out"); await next.close();
});

test("missing subscription row or its attempted marker cannot recreate eligible/fresh", async t => {
  for (const remove of ["row", "marker"]) {
    const f = fixture(t); const journal = await f.open();
    await journal.save(state("opted-out", "pending"));
    await journal.save(state("opted-out", "suspended")); await journal.close();
    if (remove === "row") { const raw = f.raw(); raw.exec("DELETE FROM selection_state"); raw.close(); }
    else {
      const names = fs.readdirSync(path.dirname(f.dbPath)).filter(name => name.endsWith(".attempted"));
      assert.equal(names.length, 1); fs.unlinkSync(path.join(path.dirname(f.dbPath), names[0]));
    }
    f.restart(); await assert.rejects(f.open(), closed("journal_changed"));
  }
});

test("failed initial subscription commit retains attempted evidence instead of resetting on restart", async t => {
  let failed = false;
  const f = fixture(t, { beforeCommit: () => { if (!failed) { failed = true; throw Error("lost first commit"); } } });
  await assert.rejects(f.open(), closed("journal_failed"));
  f.restart();
  await assert.rejects(f.open(), closed("journal_schema"));
  assert.equal(fs.readdirSync(path.dirname(f.dbPath)).filter(name => name.endsWith(".attempted")).length, 1);
});

test("missing database, missing marker, malformed marker and incomplete initialization refuse", async t => {
  for (const alter of [
    f => fs.unlinkSync(f.dbPath),
    f => fs.unlinkSync(path.join(path.dirname(f.dbPath), "attempted")),
    f => fs.writeFileSync(path.join(path.dirname(f.dbPath), "attempted"), "different\n"),
    f => fs.truncateSync(f.dbPath, 0),
  ]) {
    const f = fixture(t); const journal = await f.open(); await journal.close(); alter(f); f.restart();
    await assert.rejects(f.open(), error => ["journal_incomplete", "journal_schema"].includes(error.code));
  }
});

test("wrong schema and resource bounds refuse without resetting data", async t => {
  for (const sql of ["PRAGMA user_version=2", "UPDATE selection_state SET revision=9007199254740992"] ) {
    const f = fixture(t); const journal = await f.open(); await journal.close();
    const raw = f.raw(); raw.exec(sql); raw.close();
    f.restart();
    await assert.rejects(f.open(), closed("journal_schema"));
  }
  const f = fixture(t);
  for (let i = 0; i < 32; i++) {
    const journal = await f.open({ ...EXPECTED, name: `feed-${i}` }); await journal.close(); f.restart();
  }
  await assert.rejects(f.open({ ...EXPECTED, name: "one-too-many" }), closed("journal_capacity"));
});

test("private profile, database, sidecars and symlinks are checked before open", async t => {
  for (const alter of [
    f => fs.chmodSync(f.profile, 0o755),
    f => fs.chmodSync(f.dbPath, 0o644),
    f => { fs.unlinkSync(f.dbPath); fs.symlinkSync(path.join(f.root, "missing"), f.dbPath); },
    f => fs.writeFileSync(f.dbPath + "-wal", "", { mode: 0o600 }),
    f => fs.symlinkSync(path.join(f.root, "missing"), f.dbPath + "-wal"),
    f => fs.symlinkSync(path.join(f.root, "missing"), f.dbPath + "-shm"),
    f => fs.writeFileSync(f.dbPath + "-journal", "", { mode: 0o644 }),
  ]) {
    const f = fixture(t); const journal = await f.open(); await journal.close(); alter(f); f.restart();
    await assert.rejects(f.open(), error => ["journal_private_profile", "journal_private_file", "journal_incomplete"].includes(error.code));
    assert.equal(f.opens(), 1);
  }
});

test("unknown close retains the owner lease and never opens a second connection", async t => {
  const f = fixture(t, { close: () => { throw Error("private-close-canary"); } });
  const journal = await f.open(); await assert.rejects(journal.close(), closed("journal_failed"));
  await assert.rejects(f.open(), closed("journal_busy")); assert.equal(f.opens(), 1);
});

test("resolved but unproved native close and rejected async open both retain the lease", async t => {
  const closing = fixture(t, { swallowClose: true });
  const journal = await closing.open(); await journal.close();
  await assert.rejects(closing.open(), closed("journal_busy")); assert.equal(closing.opens(), 1);
  const opening = fixture(t, { open: () => { throw Error("native open then unjoined close"); } });
  await assert.rejects(opening.open(), closed("journal_failed"));
  await assert.rejects(opening.open(), closed("journal_busy")); assert.equal(opening.opens(), 1);
});

test("failed row attempts also consume the bounded scope-marker budget", async t => {
  let reject = false;
  const f = fixture(t, { beforeCommit: () => { if (reject) throw Error("row rollback"); } });
  const journal = await f.open(); await journal.close(); f.restart(); reject = true;
  for (let i = 1; i < 32; i++) {
    await assert.rejects(f.open({ ...EXPECTED, name: `failed-feed-${i}` }), closed("journal_failed"));
    f.restart();
  }
  await assert.rejects(f.open({ ...EXPECTED, name: "one-too-many" }), closed("journal_capacity"));
  assert.equal(fs.readdirSync(path.dirname(f.dbPath)).filter(name => name.endsWith(".attempted")).length, 32);
});

test("lifecycle commits intent before actor mutation and preserves sticky refusal on restart", async t => {
  const f = fixture(t); let journal = await f.open(); let present = false;
  const command = async operation => {
    if (operation !== "observe") {
      assert.equal(journal.record.state, "pending");
      present = operation === "add";
    }
    return { selected: present, imported: present, ...(operation === "observe" ? {} :
      { preserved: true, freshReload: true, reloadEvents: 2 }) };
  };
  const owner = new FilterSelectionLifecycle({ record: journal.record, save: value => journal.save(value),
    command, invalidate: () => {}, assertCurrent: () => true, authorize: () => true });
  assert.equal((await owner.enroll()).state, "active");
  await owner.optOut(); owner.close(); await journal.close();
  f.restart();
  journal = await f.open();
  const restarted = new FilterSelectionLifecycle({ record: journal.record, save: value => journal.save(value),
    command: () => { throw Error("must not re-enroll"); }, invalidate: () => {}, assertCurrent: () => true, authorize: () => true });
  assert.equal((await restarted.enroll()).attempted, false); restarted.close(); await journal.close();
});

test("binding rejects arbitrary paths, URLs and malformed fields before creating storage", async t => {
  const f = fixture(t);
  for (const value of [null, {}, { ...EXPECTED, path: "/tmp/arbitrary" }, { ...EXPECTED, name: "bad\nname" },
    { ...EXPECTED, publisherKey: "0".repeat(64) }, { ...EXPECTED, keyDigest: "https://unexpected.invalid/" }]) {
    await assert.rejects(f.open(value), closed("journal_config"));
  }
  assert.equal(f.opens(), 0);
  assert.equal(fs.existsSync(path.dirname(f.dbPath)), false);
});
