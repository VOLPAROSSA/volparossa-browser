"""Fixture/checker regressions only; these tests do not execute Firefox or add-ons."""

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import consent_fixture as fixture
import smoke_consent as smoke


class ConsentFixtureTests(unittest.TestCase):
    def evidence(self, accepted=(), blocked=True):
        decision = fixture.values(accepted)
        page = {"stored": decision, "saves": "1", "essential": "loaded",
                "adProbe": "blocked" if blocked else "loaded", "visible": False}
        network = {"decisions": [decision], "essential": 1,
                   "optional": int(bool(accepted)), "ad-probe": 0 if blocked else 1}
        return page, network, decision

    def test_recorded_refusal_requires_both_storage_and_server_observation(self):
        page, network, expected = self.evidence()
        fixture.validate_recorded_decision(page, network, expected, True)
        for name, replacement in (("stored", None), ("saves", "0"),
                                  ("stored", dict(expected, A=0)),
                                  ("essential", "failed"), ("adProbe", "loaded")):
            corrupted = dict(page, **{name: replacement})
            with self.subTest(name=name), self.assertRaises(ValueError):
                fixture.validate_recorded_decision(corrupted, network, expected, True)
        for name, replacement in (("decisions", []), ("decisions", [expected, expected]),
                                  ("decisions", [dict(expected, A=0)]), ("essential", True),
                                  ("optional", 1), ("essential", 0), ("ad-probe", 1)):
            corrupted = dict(network, **{name: replacement})
            with self.subTest(name=name), self.assertRaises(ValueError):
                fixture.validate_recorded_decision(page, corrupted, expected, True)

    def test_hidden_only_banner_cannot_count_as_refusal(self):
        page = {"stored": None, "saves": "0", "visible": False,
                "essential": "loaded", "adProbe": "blocked"}
        network = {"decisions": [], "essential": 1, "optional": 0, "ad-probe": 0}
        with self.assertRaises(ValueError):
            fixture.validate_recorded_decision(page, network, fixture.values(), True)

    def test_positive_probe_and_user_preference_controls(self):
        for accepted, blocked in ((fixture.PURPOSES, False), (("F",), True)):
            page, network, expected = self.evidence(accepted, blocked)
            fixture.validate_recorded_decision(page, network, expected, blocked)
        page, network, _ = self.evidence(("F",), True)
        with self.assertRaises(ValueError):
            fixture.validate_recorded_decision(page, network, fixture.values(), True)

    def test_decisions_are_exact_bounded_boolean_purpose_maps(self):
        for decision in (None, [], {}, {"A": False}, dict(fixture.values(), A=0),
                         dict(fixture.values(), X="false"), dict(fixture.values(), unknown=False)):
            with self.subTest(decision=decision), self.assertRaises(ValueError):
                fixture.validate_decision(decision)
        fixture.validate_decision(fixture.values())

    def test_state_rejects_duplicate_decisions_and_unbounded_requests(self):
        state = fixture.FixtureState()
        state.observe("refusal", "decisions", fixture.values())
        with self.assertRaises(ValueError):
            state.observe("refusal", "decisions", fixture.values())
        for _ in range(8):
            state.observe("baseline", "essential")
        with self.assertRaises(ValueError):
            state.observe("baseline", "essential")
        with self.assertRaises(ValueError):
            state.observe("arbitrary-host", "essential")
        copy_of_record = state.snapshot("refusal")
        copy_of_record["decisions"].clear()
        self.assertEqual(state.snapshot("refusal")["decisions"], [fixture.values()])

    def test_fixture_rules_cover_six_purposes_without_hide_or_remote_references(self):
        rule = fixture.rules()["VOLPAROSSA synthetic fixture"]
        methods = {method["name"]: method for method in rule["methods"]}
        self.assertNotIn("action", methods["HIDE_CMP"])
        self.assertEqual([entry["type"] for entry in methods["DO_CONSENT"]["action"]["consents"]],
                         list(fixture.PURPOSES))
        self.assertEqual(methods["SAVE_CONSENT"]["action"]["target"]["selector"], "#save-consent")
        self.assertNotIn("references", json.dumps(rule))
        self.assertNotIn("http", json.dumps(rule))
        self.assertIn('data-supported="true"', rule["detectors"][0]["presentMatcher"][0]["target"]["selector"])

    def test_html_starts_optional_purposes_on_and_persists_only_on_save(self):
        html = fixture.FIXTURE.read_text()
        for key in fixture.PURPOSES:
            self.assertIn(f'id="purpose-{key}" checked', html)
        self.assertEqual(html.count("localStorage.setItem"), 1)
        self.assertGreater(html.index("localStorage.setItem"), html.index('addEventListener("click"'))
        self.assertIn('fetch("/decision"', html)
        self.assertIn('if (Object.values(values).some(Boolean))', html)
        self.assertNotIn("https://", html)

    def test_host_network_is_rejected_before_mount_or_browser_work(self):
        with patch.object(smoke.os, "readlink", return_value="same-network"):
            with patch.object(smoke, "validate_isolated_browser_home") as home_check:
                with patch.object(smoke, "run_browser") as browser:
                    with self.assertRaisesRegex(ValueError, "disposable_network"):
                        smoke.inside(ROOT, ROOT / "build", {}, "same-network")
                    home_check.assert_not_called()
                    browser.assert_not_called()

    def test_writable_host_is_rejected_before_browser_work(self):
        class Writable:
            f_flag = 0
        with patch.object(smoke.os, "readlink", return_value="new-network"):
            with patch.object(smoke.os, "statvfs", return_value=Writable()):
                with patch.object(smoke, "run_browser") as browser:
                    with self.assertRaisesRegex(ValueError, "read_only_host"):
                        smoke.inside(ROOT, ROOT / "build", {}, "host-network")
                    browser.assert_not_called()

    def test_runtime_scenario_is_local_ui_only_and_keeps_signature_checks(self):
        source = (ROOT / "scripts/smoke_consent.py").read_text()
        for required in ('"--unshare-user", "--unshare-net"', '"--ro-bind", "/", "/"',
                         "validate_isolated_browser_home(work)", "validate_bundle(stage, metadata)",
                         "signatureEnforcement", "selected_before", "hidden_banner_falsely_counted_as_refusal"):
            self.assertIn(required, source)
        for forbidden in ("toOverwrite", "storage.sync.set", "force_installed",
                          "MOZ_DISABLE_CONTENT_SANDBOX", "security.sandbox.content.level"):
            self.assertNotIn(forbidden, source)

    def test_upstream_patch_is_separate_and_exactly_one_navigation_case_change(self):
        source = (ROOT / "patches/consent-o-matic-navigation-reset.patch").read_text()
        removed = [line for line in source.splitlines() if line.startswith("-") and not line.startswith("---")]
        added = [line for line in source.splitlines() if line.startswith("+") and not line.startswith("+++")]
        self.assertEqual(len(removed), 1)
        self.assertEqual(len(added), 1)
        self.assertEqual(removed[0][1:].replace('"Loading"', '"loading"'), added[0][1:])
        regression = (ROOT / "tests/fixtures/consent/navigation-reset.js").read_text()
        self.assertIn('upstreamCallback(7, {status: "loading"}', regression)
        self.assertIn('proposedCallback(7, {status: "loading"}', regression)
        self.assertIn("unrelatedPreserved", regression)
        # Source-shape checks only. Firefox must execute this regression before
        # claiming the original callback's runtime failure and candidate repair.


if __name__ == "__main__":
    unittest.main()
