// SPDX-License-Identifier: GPL-3.0-only
// Stage beside src/ in the pinned DeGoog 1.2.0 checkout, in an isolated data directory.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { constants, copyFile, mkdir, open, readFile } from "node:fs/promises";
import { join } from "node:path";
import DegoogEngine from "./src/server/extensions/engines/builtins/degoog/index";
import { setInstanceSettings } from "./src/server/utils/settings/server-settings";
import { getAdapter } from "./src/server/indexer/db/factory";
import { recordResults } from "./src/server/indexer/store/record";
import { flushQueue } from "./src/server/indexer/queue/queue";
import { buildSqliteExportFile } from "./src/server/indexer/export/builder";
import { importFromBuffer } from "./src/server/indexer/import/importer";

const revision = "8878ed9c21e647184309c360d89289891b275216";
const documents = [
  { file: "BUNDLED_EXTENSIONS.md", section: "Consent handling and compatibility",
    sha256: "8da073666b33259396ec4cd87af67965340ec5d19d7b53306ddd65d54dbfca9b" },
  { file: "CORE_INTEGRATION.md", section: "Network attachment and kill switch",
    sha256: "b91bf5947f54d69cb906373ceb73d581afdf9e829af6d3ac941b98c66952deb6" },
  { file: "FIREFOX_PROVENANCE.md", section: "Upstream source integration target",
    sha256: "b5dc9d753ab24ded3d00d5801c90ab47e1db7b458885be061ce14547561888ca" },
];
const digest = (data: Uint8Array) => createHash("sha256").update(data).digest("hex");
const address = (file: string) => "https://github.com/VOLPAROSSA/volparossa-browser/blob/" + revision + "/docs/" + file;

async function build(docs: string, output: string) {
  // Admit the complete corpus before placing any result in the upstream queue.
  const results = await Promise.all(documents.map(async doc => {
    const bytes = await readFile(join(docs, doc.file));
    assert.equal(digest(bytes), doc.sha256, "public document revision differs");
    const lines = bytes.toString("utf8").split("\n");
    assert.ok(lines[0].startsWith("# ") && lines.includes("## " + doc.section));
    return { url: address(doc.file), title: lines[0].slice(2),
      snippet: doc.section, source: "VOLPAROSSA Browser documentation" };
  }));
  for (const result of results) {
    // Only an enrolled public heading becomes an index key.
    await recordResults(result.snippet, "web", [result]);
  }
  await flushQueue();
  const snapshot = await buildSqliteExportFile("web");
  await copyFile(snapshot, output, constants.COPYFILE_EXCL);
  process.stdout.write(JSON.stringify({ documents: results.length, sha256: digest(await readFile(snapshot)) }) + "\n");
}

async function restoreAndSearch(snapshot: string, expected: string) {
  assert.match(expected, /^[a-f0-9]{64}$/);
  const input = await open(snapshot, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  let bytes: Uint8Array;
  try {
    const info = await input.stat();
    assert.ok(info.isFile() && info.size > 0 && info.size <= 4 * 1024 * 1024, "unexpected snapshot size");
    bytes = new Uint8Array(info.size);
    const reader = input.createReadStream({ autoClose: false, start: 0, end: info.size });
    let offset = 0;
    for await (const block of reader) {
      assert.ok(block.length <= bytes.length - offset, "snapshot grew during admission");
      bytes.set(block, offset);
      offset += block.length;
    }
    assert.equal(offset, info.size, "incomplete snapshot");
  } finally {
    await input.close();
  }
  assert.equal(digest(bytes), expected, "public index digest differs");
  const restored = await importFromBuffer(bytes.buffer as ArrayBuffer, "web");
  assert.deepEqual(restored, { urls: documents.length, hits: documents.length });
  const engine = new DegoogEngine();
  let externalRequests = 0;
  const context = { searchType: "web", fetch: async () => {
    externalRequests += 1;
    throw new Error("local index attempted an external fetch");
  } };
  for (const doc of documents) {
    const results = await engine.executeSearch(doc.section, 1, undefined, context);
    assert.deepEqual(results.map(row => row.url), [address(doc.file)]);
  }
  // Exercise a submitted query outside the corpus without passing it to the upstream recorder.
  const marker = (await Bun.stdin.text()).trim();
  assert.match(marker, /^private-probe-[a-f0-9]{32}$/);
  assert.deepEqual(await engine.executeSearch(marker, 1, undefined, context), []);
  assert.equal(externalRequests, 0);
  process.stdout.write(JSON.stringify({ restored, known_queries: documents.length, unmatched_queries: 1, external_requests: externalRequests }) + "\n");
}

const [mode, first, second, ...extra] = process.argv.slice(2);
assert.ok((mode === "build" || mode === "restore") && first && second && extra.length === 0);
const data = process.env.DEGOOG_DATA_DIR;
assert.ok(data && data.startsWith("/"), "a fresh absolute DeGoog data directory is required");
await mkdir(data, { mode: 0o700 });
try {
  await setInstanceSettings({ degoogIndexerEnabled: "true", degoogIndexerFuzzyEnabled: "false" });
  if (mode === "build") await build(first, second);
  else await restoreAndSearch(first, second);
} finally {
  await getAdapter().close();
}
