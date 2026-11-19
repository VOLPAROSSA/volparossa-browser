import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import smoke_cooperative_compute as SMOKE


class CooperativeCompute(unittest.TestCase):
    def test_actual_javascript_transport_and_review_gate_contracts(self):
        node = os.environ.get("VOLPAROSSA_TEST_NODE") or shutil.which("node")
        self.assertIsNotNone(node, "A Node executable is needed for the pure JS contracts; no browser is launched")
        result = subprocess.run([node, str(ROOT / "tests/cooperative_contract.cjs")],
                                capture_output=True, text=True, timeout=15, check=True)
        self.assertEqual(result.stdout.strip(), "cooperative_transport_and_consent_contracts_passed")

    def test_fixture_rejects_private_shape_oversize_or_implicit_license(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.json"
            value = dict(question="Question", context="Public text", license="CC0-1.0")
            source.write_text(json.dumps(value)); source.chmod(0o600)
            self.assertEqual(SMOKE.public_input(source), value)
            for change in (dict(context="x"*4097), dict(question="€"*171), dict(license=""), dict(private=True)):
                source.write_text(json.dumps(dict(value, **change)))
                with self.assertRaises(ValueError): SMOKE.public_input(source)

    def test_closed_combined_result_refuses_fake_multipeer_and_incomplete_scope(self):
        result = dict(prefill_no_dispatch=True, explicit_consent=True, text_only=True, scoped_cancel_confirmed=True,
            first_task_id="a"*32, cancel_task_id="b"*32, removed=True,
            first_result=dict(source_manifest_id="c"*64, package_count=2, total_parts=5, synthesis_levels=2,
                provider_keys=["a"*64,"b"*64], selected_provider_keys=["a"*64,"b"*64], output_sha256="d"*64,
                joining="hierarchical_peer_synthesis", answer_complete=True, execution_complete=True,
                remote_cleanup_confirmed=True, retained_public_receipts=True))
        SMOKE.check_result(result)
        for change in (dict(provider_keys=["a"*64]), dict(joining="single_source_answer"),
                       dict(synthesis_levels=0), dict(remote_cleanup_confirmed=False), dict(text="raw answer")):
            altered = dict(result, first_result=dict(result["first_result"], **change))
            with self.assertRaises(ValueError): SMOKE.check_result(altered)
        with self.assertRaises(ValueError): SMOKE.check_result(dict(result, prefill_no_dispatch=False))

    def test_owner_task_window_reaches_the_negotiated_deadline_and_reserves_separate_cancel_time(self):
        self.assertEqual(SMOKE.observation_budgets(None), (1800, 2400))
        for seconds in (1, 1800, 2400, 7200):
            first, script = SMOKE.observation_budgets(seconds)
            self.assertGreater(first, seconds + 15)
            self.assertGreater(script - first, 120 + 120 + 600 + 120)
        for bad in (0, 7201, True, 2400.0):
            with self.assertRaises(ValueError):
                SMOKE.observation_budgets(bad)
        self.assertIn("this.capabilities.max_task_seconds === ownerTaskSeconds", SMOKE.SCRIPT)
        self.assertIn('firstTaskWait, "FIRST_TASK_WAIT_EXPIRED"', SMOKE.SCRIPT)

    def test_driver_contains_real_panel_and_external_consent_cancel_barriers(self):
        self.assertIn("await panel.ask(input.question, input.context)", SMOKE.SCRIPT)
        self.assertIn("connections === 0 && submissions === 0", SMOKE.SCRIPT)
        for name, event in SMOKE.MARKERS.items():
            self.assertIn('"' + name + '"', SMOKE.SCRIPT)
            self.assertIn('"' + event + '"', SMOKE.SCRIPT)
        self.assertIn('node("cancel").click()', SMOKE.SCRIPT)
        self.assertIn("sha256(rendered.textContent) === firstTextHash", SMOKE.SCRIPT)
        self.assertNotIn("protocol_fixture", SMOKE.SCRIPT)
        panel = (ROOT / "integration/VolparossaCooperativePanel.sys.mjs").read_text()
        ask = panel.split("async ask(prompt, selectedContext)",1)[1].split("destroy()",1)[0]
        self.assertNotIn("connect(", ask); self.assertNotIn("run(", ask); self.assertNotIn("submit(", ask)
        for forbidden in ("innerHTML", "eval(", "fetch(", "XMLHttpRequest"):
            self.assertNotIn(forbidden, panel)

    def test_new_markers_use_atomic_create_and_cleanup_preserves_original_failure_phase(self):
        node = os.environ.get("VOLPAROSSA_TEST_NODE") or shutil.which("node")
        self.assertIsNotNone(node)
        # Exercise the exact production helper with the pinned Gecko IsSymlink
        # behavior: metadata access on a nonexistent path raises, PR_EXCL does not.
        helper = SMOKE.SCRIPT.split("const write =", 1)[1].split("const marker =", 1)[0]
        javascript = r'''
const assert = require("node:assert/strict");
let phase = "prefill", failure = null;
const work = "/owned";
const objects = new Map([["/owned/browser-status.json", {kind:"file", permissions:0o600}]]);
const file = path => ({path,
  exists() { return objects.has(path) && objects.get(path).kind !== "dangling"; },
  isSymlink() { if (!objects.has(path)) throw Error("ENOENT"); return objects.get(path).kind !== "file"; },
  isFile() { return objects.get(path)?.kind === "file"; },
  get permissions() { return objects.get(path)?.permissions; }
});
const Ci = {nsIFileOutputStream: {}};
const Cc = {"@mozilla.org/network/file-output-stream;1": {createInstance() {
  let target;
  return {init(f, flags, mode) {
    if ((flags & 0x80) && objects.has(f.path)) throw Error("EEXIST");
    assert.equal(flags & 0x08, 0x08); assert.equal(mode, 0o600);
    objects.set(f.path, target = {kind:"file", permissions:mode});
  }, write(data, length) { target.data = data; return length; }, close() {}};
}}};
''' + "const write =" + helper + r'''
write("pre-consent.json", {version:1,event:"prefill_without_dispatch"});
assert.equal(JSON.parse(objects.get("/owned/pre-consent.json").data).event,"prefill_without_dispatch");
assert.throws(() => write("pre-consent.json", {}), /EEXIST/);
objects.set("/owned/dangling.json", {kind:"dangling"});
assert.throws(() => write("dangling.json", {}), /EEXIST/);
assert.throws(() => write("missing-status.json", {}, false), /owned_file/);
status("prefill");
try { throw Error("synthetic original failure"); }
catch (error) { status(phase, "SCRIPT_FAILED"); }
finally { status("panel-cleanup"); }
assert.deepEqual(JSON.parse(objects.get("/owned/browser-status.json").data),
  {version:1,phase:"prefill",failure:"SCRIPT_FAILED"});
status("complete");
assert.equal(JSON.parse(objects.get("/owned/browser-status.json").data).phase,"prefill");
console.log("exclusive_markers_and_failure_phase_passed");
'''
        result = subprocess.run([node, "-e", javascript], capture_output=True, text=True,
                                timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "exclusive_markers_and_failure_phase_passed")
        self.assertIn('status(phase, timeout ? error.message : "SCRIPT_FAILED");\n    throw error;\n  } finally {', SMOKE.SCRIPT)

    def test_panel_observer_handles_early_late_and_absent_terminal_events(self):
        helper = SMOKE.SCRIPT.split("  const waitForPanel =", 1)[1].split("  const sha256", 1)[0]
        node = os.environ.get("VOLPAROSSA_TEST_NODE") or shutil.which("node")
        program = r'''const assert = require("node:assert/strict");
let live = null, timer = null, duration = 0;
class MutationObserver {
  constructor(callback) { this.callback = callback; }
  observe(element, options) {
    assert.deepEqual(options, {attributes: true, attributeFilter: ["data-state"]});
    assert.equal(live, null); live = this;
  }
  disconnect() { assert.equal(live, this); live = null; }
}
const element = {ownerDocument:{defaultView:{MutationObserver}}, dataset:{state:"complete"}};
const setTimeout = (callback, millis) => { timer = callback; duration = millis; return 17; };
const clearTimeout = id => { assert.equal(id, 17); timer = null; };
''' + "const waitForPanel =" + helper + r'''
(async () => {
  await waitForPanel(element, ["complete"], 1800, "FIRST_TASK_WAIT_EXPIRED");
  assert.equal(timer, null); assert.equal(live, null);
  element.dataset.state = "working";
  const active = waitForPanel(element, ["cancelled"], 120, "CANCEL_WAIT_EXPIRED");
  assert.equal(duration, 120000);
  live.callback(); assert.ok(live);
  element.dataset.state = "cancelled"; live.callback(); await active;
  assert.equal(timer, null); assert.equal(live, null);
  const missing = waitForPanel(element, ["complete"], 1800, "FIRST_TASK_WAIT_EXPIRED");
  assert.equal(duration, 1800000); timer();
  await assert.rejects(missing, /FIRST_TASK_WAIT_EXPIRED/); assert.equal(live, null);
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
        subprocess.run([node, "-e", program], check=True, timeout=5)
        self.assertIn("FIRST_TASK_WAIT_EXPIRED", SMOKE.ERRORS)
        self.assertIn("CANCEL_WAIT_EXPIRED", SMOKE.ERRORS)


if __name__ == "__main__":
    unittest.main()
