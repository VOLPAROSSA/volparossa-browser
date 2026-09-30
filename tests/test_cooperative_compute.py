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


if __name__ == "__main__":
    unittest.main()
