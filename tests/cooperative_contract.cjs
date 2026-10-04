// SPDX-License-Identifier: GPL-3.0-only
// Pure JS contracts with inert DOM/transport doubles, not Gecko or peer execution evidence.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const base = path.resolve(__dirname, "../integration");
function load(name, exports, additions = {}) {
  const scope = {TextEncoder, TextDecoder, setTimeout, clearTimeout,
    ChromeUtils: {generateQI: () => () => {}}, ...additions};
  vm.createContext(scope);
  const source = fs.readFileSync(path.join(base, name), "utf8")
    .replace(/^import .*;\n/gm, "").replace(/^export /gm, "");
  vm.runInContext(source + `\nglobalThis.loaded = {${exports}};`, scope);
  return scope.loaded;
}
const {VolparossaCooperativeCompute: Client} = load("VolparossaCooperativeCompute.sys.mjs", "VolparossaCooperativeCompute");
const caps = {visibility: "public_cooperative", network_access: true, private_data_supported: false,
  public_cache: true, training: false, cloud_fallback: false, max_question_bytes: 512, max_context_bytes: 4096,
  max_request_bytes: 32768, max_response_bytes: 65536, execution_slots: 1, max_connections: 8,
  retained_public_receipts: true, remote_erasure_guaranteed: false, model_execution_proven: false,
  quarantined: false, max_seconds: 600, max_task_seconds: 1800, model_profile: "smollm2-135m-v1"};
const result = {cleanup: {complete: true}, remote_cleanup_confirmed: true, retained_public_receipts: true,
  model_answer_correctness_proven: false, semantic_completeness_proven: false, output: {text: "<script>literal answer</script>"},
  answer_complete: true, execution_complete: true, answer_status: "complete", provider_keys: ["a".repeat(64), "b".repeat(64)],
  selected_provider_keys: ["a".repeat(64), "b".repeat(64)], joining: "hierarchical_peer_synthesis",
  package_count: 1, total_parts: 3, synthesis_levels: 1, source_manifest_id: "c".repeat(64)};
const id = "a".repeat(32);
function respond(event, value) {
  let resolved;
  const client = Object.assign(Object.create(Client.prototype), {pending: new Map(), active: null});
  client.pending.set(id, {type: event === "capabilities" ? "capabilities" : "submit", admitted: true,
    resolve: data => { resolved = data; }, reject: () => { throw new Error("unexpected rejection"); }});
  client._response({version: 1, id, event, [event === "capabilities" ? "capabilities" : "result"]: value});
  return resolved;
}
assert.equal(respond("capabilities", caps).visibility, "public_cooperative");
for (const change of [{visibility: "private_local"}, {network_access: false}, {training: true},
  {private_data_supported: true}, {max_task_seconds: 7201}, {remote_erasure_guaranteed: true},
  {max_context_bytes: 8192}, {model_execution_proven: true}]) {
  assert.throws(() => respond("capabilities", {...caps, ...change}), e => e.code === "invalid_response");
}
assert.equal(respond("result", result).output.text, result.output.text);
for (const change of [{cleanup: {complete: false}}, {remote_cleanup_confirmed: false},
  {provider_keys: ["a".repeat(64), "a".repeat(64)]}, {provider_keys: ["d".repeat(64)]},
  {model_answer_correctness_proven: true}, {semantic_completeness_proven: true},
  {output: {text: "\0"}}, {answer_status: "incomplete"}, {execution_complete: false},
  {joining: "hierarchical_peer_synthesis_incomplete"}]) {
  assert.throws(() => respond("result", {...result, ...change}), e => e.code === "invalid_response");
}
let requests = 0;
const client = Object.assign(Object.create(Client.prototype), {capabilities: caps, closed: false, active: null,
  _request: (operation, deadline) => { requests++; assert.equal(deadline, 1815000); return {id, operation}; }});
const input = {question: "Public question", context: "Public context", license: "CC0-1.0", public_content: true, rights_confirmed: true};
for (const change of [{public_content: false}, {rights_confirmed: false}, {license: ""},
  {question: "€".repeat(171)}, {context: "x".repeat(4097)}, {context: " "}]) {
  assert.throws(() => client.submit({...input, ...change}));
}
assert.equal(requests, 0);
client.submit(input);
assert.equal(requests, 1);
assert.throws(() => client.submit(input), e => e.code === "busy");

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.dataset = {};
    this.value = ""; this.checked = false; this.disabled = false; this.textContent = ""; }
  append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
  setAttribute() {}
  addEventListener(name, callback) { (this.listeners[name] ??= []).push(callback); }
  async emit(name) { for (const callback of this.listeners[name] ?? []) await callback(); }
  remove() { this.parent.children = this.parent.children.filter(child => child !== this); }
}
function document() {
  const nodes = [];
  return {body: new Element("body"), createElement(tag) { const value = new Element(tag); nodes.push(value); return value; },
    get(name) { return nodes.find(node => node.id === "volparossa-public-" + name); }};
}
let connects = 0, submissions = 0, closes = 0, finish, reject, submitted;
const FakeClient = {async connect() {
  connects++;
  return {capabilities: caps, submit(input) {
    submissions++; submitted = input; input.onAdmitted();
    return {finished: new Promise((yes, no) => { finish = yes; reject = no; }),
      cancel: async () => { reject({code: "cancelled"}); }};
  }, close() { closes++; }};
}};
const {createVolparossaCooperativePanel: panelFor} = load("VolparossaCooperativePanel.sys.mjs", "createVolparossaCooperativePanel",
  {VolparossaCooperativeCompute: FakeClient});
async function approve(doc) {
  doc.get("license").value = "CC0-1.0"; await doc.get("license").emit("change");
  for (const name of ["rights", "consent"]) { doc.get(name).checked = true; await doc.get(name).emit("change"); }
}
(async () => {
  const doc = document(), panel = panelFor(doc, doc.body);
  await panel.ask("Public question", "Public context");
  assert.equal(connects, 0); assert.equal(submissions, 0); assert.equal(doc.get("submit").disabled, true);
  await doc.get("submit").emit("click"); // Even a synthetic forced click cannot bypass the gate.
  assert.equal(connects, 0);
  await approve(doc); assert.equal(doc.get("submit").disabled, false);
  await doc.get("context").emit("input");
  assert.equal(doc.get("consent").checked, false); assert.equal(doc.get("rights").checked, false);
  assert.equal(doc.get("submit").disabled, true);
  await approve(doc);
  const completed = doc.get("submit").emit("click");
  await new Promise(setImmediate);
  assert.equal(submitted.context, "Public context"); assert.equal(submitted.public_content, true);
  assert.equal(submitted.rights_confirmed, true); assert.equal(doc.get("context").disabled, true);
  finish(result); await completed;
  assert.equal(doc.get("answer").textContent, result.output.text);
  assert.equal(doc.get("answer").children.length, 0);
  assert.equal(panel.element.dataset.peerCount, "2"); assert.equal(panel.element.dataset.state, "complete");
  assert.equal(doc.get("consent").checked, false); assert.equal(doc.get("submit").disabled, true);
  await panel.ask("Second public question", "Second public context");
  assert.equal(submissions, 1); assert.equal(doc.get("license").value, "");
  await approve(doc);
  const cancelled = doc.get("submit").emit("click");
  await new Promise(setImmediate);
  await doc.get("cancel").emit("click"); await cancelled;
  assert.equal(panel.element.dataset.state, "cancelled"); assert.equal(doc.get("answer").textContent, "");
  assert.ok(doc.get("status").textContent.includes("may remain"));
  panel.destroy(); assert.equal(doc.body.children.length, 0);
  assert.equal(doc.get("context").value, ""); assert.equal(closes, 2);
  process.stdout.write("cooperative_transport_and_consent_contracts_passed\n");
})().catch(error => { console.error(error); process.exitCode = 1; });
