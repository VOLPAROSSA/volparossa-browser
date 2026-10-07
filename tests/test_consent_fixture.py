"""Fixture/checker regressions only; these tests do not execute Firefox or add-ons."""

import json
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import consent_fixture as fixture
import smoke_consent as smoke


class ConsentFixtureTests(unittest.TestCase):
    def addon_observation(self, state):
        return {"signatureEnforcement": True, "addons": {
            entry["id"]: None if state == "removed" else {
                "version": entry["version"], "signatureAccepted": True, "appDisabled": False,
                "active": state == "active", "userDisabled": state == "disabled",
                "canDisable": state == "active", "canUninstall": True,
            } for entry in smoke.load_lock()["extensions"]}}

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
        for case in fixture.CASES:
            self.assertIn('"' + case + '"', html)
        for key in fixture.PURPOSES:
            self.assertIn(f'id="purpose-{key}" checked', html)
        self.assertEqual(html.count("localStorage.setItem"), 1)
        self.assertGreater(html.index("localStorage.setItem"), html.index('addEventListener("click"'))
        self.assertIn('fetch("/decision"', html)
        self.assertIn('if (Object.values(values).some(Boolean))', html)
        self.assertNotIn("https://", html)

    def test_all_four_addon_user_states_require_complete_signed_observations(self):
        self.assertEqual(len(smoke.load_lock()["extensions"]), 4)
        for state in ("active", "disabled", "removed"):
            original = self.addon_observation(state)
            smoke.validate_addon_state(original, state)
            for mutate in (
                lambda value: value.update(signatureEnforcement=False),
                lambda value: value["addons"].pop(smoke.CONSENT),
                lambda value: value["addons"].update(unexpected=None),
            ):
                changed = copy.deepcopy(original)
                mutate(changed)
                with self.subTest(state=state), self.assertRaises(ValueError):
                    smoke.validate_addon_state(changed, state)
        for state in ("active", "disabled"):
            for field, value in (("version", "wrong"), ("signatureAccepted", False),
                                 ("appDisabled", True), ("canUninstall", False),
                                 ("active", state != "active"), ("userDisabled", state != "disabled")):
                changed = self.addon_observation(state)
                changed["addons"][smoke.CONSENT][field] = value
                with self.subTest(state=state, field=field), self.assertRaises(ValueError):
                    smoke.validate_addon_state(changed, state)

    def test_disabled_or_reinstalled_addon_is_not_removal(self):
        for state in ("active", "disabled"):
            with self.assertRaisesRegex(ValueError, "reinstalled"):
                smoke.validate_addon_state(self.addon_observation(state), "removed")
        with self.assertRaises(ValueError):
            smoke.validate_addon_state(self.addon_observation("removed"), "disabled")

    def test_removed_filter_requires_real_unblocked_probe_without_consent(self):
        page = {"stored": None, "saves": "0", "visible": True,
                "essential": "loaded", "adProbe": "loaded"}
        network = {"essential": 1, "ad-probe": 1, "optional": 0, "decisions": []}
        smoke.validate_unanswered_probe(page, network)
        for key, value in (("ad-probe", 0), ("essential", True), ("optional", 1),
                           ("decisions", [fixture.values()])):
            with self.subTest(key=key), self.assertRaises(ValueError):
                smoke.validate_unanswered_probe(page, dict(network, **{key: value}))
        for key, value in (("adProbe", "blocked"), ("stored", fixture.values()),
                           ("visible", False), ("saves", "1")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                smoke.validate_unanswered_probe(dict(page, **{key: value}), network)

    def test_user_control_cycle_restarts_and_checks_disabled_then_absent(self):
        outputs = [{"exercise": {"removed": True}}, {"exercise": {"persisted": True}},
                   {"extensions": self.addon_observation("disabled")},
                   {"extensions": self.addon_observation("removed")}]
        controls, progress = {}, {}
        with patch.object(smoke, "run_browser", side_effect=outputs) as browser:
            smoke.user_control_cycle(ROOT, ROOT / "build", "http://127.0.0.1:1",
                                     fixture.FixtureState(), {}, progress, controls)
        self.assertEqual([call.args[3] for call in browser.call_args_list],
                         ["list-remove", "list-removal-restart", "addons-disabled", "addons-removed"])
        self.assertEqual(browser.call_args_list[2].kwargs["addon_action"], "uninstall")
        self.assertIn("disable_survived_restart", controls)
        self.assertIn("removal_survived_restart", controls)
        self.assertEqual(progress["phase"], "addons-removed")

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
