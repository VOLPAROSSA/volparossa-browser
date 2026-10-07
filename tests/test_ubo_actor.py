# SPDX-License-Identifier: GPL-3.0-only
"""Inert wrapper/evidence checks; no browser, extension, socket or namespace starts."""
import ast
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import smoke_ubo_actor as smoke
import consent_fixture


class UboActorFixtureTests(unittest.TestCase):
    def test_fresh_change_requires_reload_not_selection_or_ack(self):
        value = {"freshReload": True, "selected": True, "imported": True,
                 "stocks": 10, "reloadEvents": 2}
        smoke.fresh_change(value, True)
        for key, replacement in (("freshReload", False), ("selected", False),
                                  ("imported", False), ("stocks", 9), ("stocks", True),
                                  ("reloadEvents", 1), ("reloadEvents", True), ("reloadEvents", 33)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                smoke.fresh_change(dict(value, **{key: replacement}), True)
        with self.assertRaises(ValueError):
            smoke.fresh_change({"accepted": True}, True)

    def test_decline_must_explicitly_record_nonattempt_after_optout(self):
        smoke.declined({"state": "opted-out", "attempted": False})
        for value in ({"state": "enrolled", "attempted": False},
                      {"state": "opted-out", "attempted": True},
                      {"state": "opted-out"}, {}):
            with self.assertRaises(ValueError):
                smoke.declined(value)

    def test_fixture_url_fixed_and_no_arbitrary_browser_command(self):
        self.assertEqual(smoke.LIST_URL, "http://127.0.0.1:18765/filters.txt")
        contract = (ROOT / smoke.MODULES[0]).read_text()
        self.assertIn('export const URL = "' + smoke.LIST_URL + '"', contract)
        with self.assertRaises(ValueError):
            smoke.actor(None, "eval")
        for port in (True, -1, 65536, "18765"):
            with self.assertRaises(ValueError), consent_fixture.serve_fixture(port=port):
                self.fail("invalid fixture port reached server")

    def test_no_production_or_expiry_claims(self):
        self.assertEqual(smoke.SCOPE, {
            "isolated_original_signed_ubo_only": True,
            "production_default_enrollment": False,
            "startup_resume_stale_filter_barrier": False,
            "offline_authority_expiry": False,
            "decentralized_signed_list_broker": False,
            "full_firefox_source_build": False,
        })

    def test_failure_report_and_both_profile_cleanups(self):
        # Exercise actual orchestration/finally without launching a campaign.
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for name in ("user", "authority"):
                (work / name).mkdir()
                (work / name / "enroll.log").write_text("synthetic log")
            calls = []
            with patch.object(smoke, "validate_isolation"), patch.object(smoke, "campaign", side_effect=smoke.ActorFailure("reload_fresh_event", "ubo_proof_deadline")), \
                    patch.object(smoke, "remove_profile", side_effect=lambda p: calls.append(p.name) or True):
                with self.assertRaises(ValueError):
                    smoke.inside(Path("/unused"), work, {"runtime_sha256": {}}, "unused")
            import json
            report = json.loads((work / "report.json").read_text())
            self.assertFalse(report["passed"])
            self.assertEqual(report["closed_failure"], "ubo_proof_deadline")
            self.assertEqual(report["failure_phase"], "reload_fresh_event")
            self.assertEqual(calls, ["user", "authority", work.name])
            self.assertTrue(report["temporary_profiles_removed"])
            self.assertFalse((work / "user/enroll.log").exists())
            self.assertFalse((work / "authority/enroll.log").exists())

    def test_all_owned_sources_parse_and_bootstrap_is_not_actor_payload(self):
        ast.parse((ROOT / "scripts/smoke_ubo_actor.py").read_text())
        parent = (ROOT / smoke.MODULES[2]).read_text()
        child = (ROOT / smoke.MODULES[3]).read_text()
        # Names in MODULES are explicit; only the fixture harness uses Marionette.
        for name in smoke.MODULES:
            source = (ROOT / name).read_text()
            for forbidden in ("eval(", "executeScript", "Debugger", "toOverwrite", "toSelect:", "Subprocess"):
                self.assertNotIn(forbidden, source, name)
        self.assertNotIn("BOOTSTRAP", parent + child)
        controller = (ROOT / smoke.MODULES[1]).read_text()
        self.assertIn('"browser.volparossa.uboProof.enabled", false', controller)
        self.assertIn('value.state !== "fresh"', controller)
        self.assertIn('messageManagerGroups: ["webext-browsers"]', controller)

    def test_timeout_still_cleans_exact_fixture_paths_without_success_claim(self):
        import tempfile
        import json
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory) / "stage"
            stage.mkdir()
            work = Path(directory) / "build/run"
            def timeout(*args, **kwargs):
                profile = work / "user/profile"
                profile.mkdir(parents=True)
                (profile / "synthetic").write_text("test data")
                (work / "user/enroll.log").write_text("synthetic log")
                raise subprocess.TimeoutExpired("fixture", 480)
            with patch.object(sys, "argv", ["smoke", "--stage", str(stage), "--output", str(work)]), \
                    patch.object(smoke, "ROOT", Path(directory)), \
                    patch.object(smoke, "build_path", side_effect=Path), \
                    patch.object(smoke, "validate_stage", return_value={}), \
                    patch.object(smoke, "validate_retained_bundle"), \
                    patch.object(smoke, "isolated_browser_home", return_value=[]), \
                    patch.object(smoke.subprocess, "run", side_effect=timeout):
                with self.assertRaises(subprocess.TimeoutExpired):
                    smoke.main()
            self.assertFalse((work / "user/profile").exists())
            self.assertFalse((work / "user/enroll.log").exists())
            self.assertFalse((work / "report.json").exists())
            self.assertEqual(json.loads((work / "outer-cleanup.json").read_text()),
                             {"schema": 1, "temporary_profiles_removed": True})

    def test_retained_bundle_keeps_full_original_package_checks(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            (stage / "distribution/extensions").mkdir(parents=True)
            metadata = {"extensions": {"lock_sha256": smoke.digest(smoke.LOCK), "packages": ["expected"]}}
            with patch.object(smoke, "load_lock", return_value={"extensions": [{"id": "fixed"}]}), \
                    patch.object(smoke, "read_package", return_value=(b"", "expected")) as checked:
                smoke.validate_retained_bundle(stage, metadata)
                checked.assert_called_once_with(stage / "distribution/extensions", {"id": "fixed"})
            with patch.object(smoke, "read_package", side_effect=ValueError("digest mismatch")):
                with self.assertRaises(ValueError):
                    smoke.validate_retained_bundle(stage, metadata)

    def test_preparation_creates_only_absent_build_parent_and_fresh_child(self):
        import tempfile
        import stat
        with tempfile.TemporaryDirectory() as directory, patch.object(smoke, "ROOT", Path(directory)):
            base = Path(directory) / "build"
            target = base / "proof-02"
            self.assertFalse(base.exists())
            smoke.prepare_output(target)
            self.assertEqual(stat.S_IMODE(base.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
            with self.assertRaises(ValueError):
                smoke.prepare_output(target)
            self.assertTrue(target.is_dir())
            with self.assertRaises(ValueError):
                smoke.prepare_output(base / "nested/run")
            self.assertFalse((base / "nested").exists())

    def test_preparation_refuses_parent_or_child_symlink_and_wrong_owner(self):
        import tempfile
        import os
        with tempfile.TemporaryDirectory() as directory, patch.object(smoke, "ROOT", Path(directory)):
            root = Path(directory)
            other = root / "other"
            other.mkdir(mode=0o700)
            base = root / "build"
            base.symlink_to(other, target_is_directory=True)
            with self.assertRaises(ValueError):
                smoke.prepare_output(base / "run")
            self.assertEqual(list(other.iterdir()), [])
            base.unlink()
            base.mkdir(mode=0o700)
            target = base / "run"
            target.symlink_to(other, target_is_directory=True)
            with self.assertRaises(ValueError):
                smoke.prepare_output(target)
            self.assertTrue(target.is_symlink())
            owner = os.getuid()
            with patch.object(smoke.os, "getuid", return_value=owner + 1), self.assertRaises(ValueError):
                smoke.prepare_output(base / "new")
            self.assertFalse((base / "new").exists())

    def test_actor_socket_timeout_is_scoped_and_restored_on_success_and_error(self):
        class Socket:
            timeout = 20
            def gettimeout(self):
                return self.timeout
            def settimeout(self, value):
                self.timeout = value

        class Client:
            def __init__(self, outcome):
                self.socket = Socket()
                self.outcome = outcome
                self.commands = []
            def command(self, name, arguments):
                self.commands.append((name, arguments))
                if name == "WebDriver:ExecuteAsyncScript":
                    self.assert_timeout()
                    if isinstance(self.outcome, Exception):
                        raise self.outcome
                    return {"value": self.outcome}
                return {}
            def assert_timeout(self):
                assert self.socket.timeout == 60

        success = {"state": "opted-out", "attempted": False}
        for outcome in (success, TimeoutError(), {"failed": True, "code": "ubo_proof_deadline"}):
            client = Client(outcome)
            if outcome is success:
                self.assertEqual(smoke.actor(client, "enroll"), success)
            else:
                with self.assertRaises((TimeoutError, ValueError)):
                    smoke.actor(client, "enroll")
            self.assertEqual(client.socket.timeout, 20)
            self.assertIn(("WebDriver:SetTimeouts", {"script": 55000}), client.commands)
            executed = [args for name, args in client.commands if name == "WebDriver:ExecuteAsyncScript"]
            self.assertEqual(executed[0]["args"], ["enroll"])

    def test_phase_evidence_is_closed_and_bounded_not_raw_browser_output(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            profile = work / "user/profile"
            profile.mkdir(parents=True)
            path = profile / smoke.TRACE_NAME
            for phase in smoke.TRACE_PHASES:
                path.write_text(json.dumps({"schema": 1, "phase": phase}))
                self.assertEqual(smoke.phase_trace(work), {"user": phase})
            for value in ({"schema": 1, "phase": "https://private.invalid"},
                          {"schema": 1, "phase": "open", "content": "not allowed"},
                          {"schema": True, "phase": "open"}):
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    smoke.phase_trace(work)
            path.write_text("x" * 129)
            with self.assertRaises(ValueError):
                smoke.phase_trace(work)

    def test_failure_envelope_is_strict_and_matches_javascript_fixed_enums(self):
        import json
        import re
        contract = (ROOT / smoke.MODULES[0]).read_text()
        for name, expected in (("PHASES", smoke.FAILURE_PHASES), ("REASONS", smoke.FAILURE_REASONS)):
            body = re.search(r"export const " + name + r" = Object\.freeze\(\[([\s\S]*?)\]\);", contract)[1]
            self.assertEqual(tuple(re.findall(r'"([a-z_]+)"', body)), expected)
        diagnostic = {"schema": 1, "ok": False, "phase": "child_dashboard_ready", "reason": "ubo_proof_other"}
        with self.assertRaises(smoke.ActorFailure) as raised:
            smoke.actor_result({"failed": True, "diagnostic": diagnostic})
        self.assertEqual((raised.exception.phase, raised.exception.reason),
                         ("child_dashboard_ready", "ubo_proof_other"))
        invalid = [None, [], "PRIVATE", {"failed": True}, {"failed": False, "diagnostic": diagnostic},
                   {"failed": True, "diagnostic": diagnostic, "extra": "PRIVATE"}]
        for change in ({"phase": "PRIVATE"}, {"reason": "ubo_proof_PRIVATE"}, {"schema": True},
                       {"ok": 0}, {"extra": "PRIVATE"}, {"phase": ["child_dashboard_ready"]}):
            invalid.append({"failed": True, "diagnostic": dict(diagnostic, **change)})
        for value in invalid:
            with self.subTest(value=json.dumps(value)), self.assertRaises(smoke.ActorFailure) as rejected:
                smoke.actor_result(value)
            self.assertEqual((rejected.exception.phase, rejected.exception.reason),
                             ("reply_validate", "ubo_proof_invalid_reply"))
        self.assertEqual(smoke.actor_result({"state": "opted-out", "attempted": False}),
                         {"state": "opted-out", "attempted": False})

    def test_unknown_exception_cannot_export_a_raw_message_as_diagnostic(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            with patch.object(smoke, "validate_isolation"), \
                    patch.object(smoke, "campaign", side_effect=ValueError("ubo_proof_PRIVATE_secret")), \
                    patch.object(smoke, "remove_profile", return_value=True):
                with self.assertRaises(ValueError):
                    smoke.inside(Path("/unused"), work, {"runtime_sha256": {}}, "unused")
            encoded = (work / "report.json").read_text()
            report = json.loads(encoded)
            self.assertEqual(report["failure"], "ValueError")
            self.assertFalse(report["passed"])
            self.assertNotIn("closed_failure", report)
            self.assertNotIn("failure_phase", report)
            self.assertNotIn("PRIVATE", encoded)

    def module_source(self, root):
        source = root / "integration/ubo-proof"
        source.mkdir(parents=True)
        for name in smoke.MODULES:
            path = root / name
            path.write_bytes((ROOT / name).read_bytes())
            path.chmod(0o600)
        return {name: smoke.digest(root / name) for name in smoke.MODULES}

    def test_module_layout_uses_fixed_profile_chrome_and_exact_readonly_bytes(self):
        import tempfile
        import stat
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = self.module_source(root)
            for name in smoke.MODULES:
                (root / name).chmod(0o664)  # Ordinary owned checkout mode is preserved.
            profile = root / "profile"
            profile.mkdir(mode=0o700)
            with patch.object(smoke, "ROOT", root):
                self.assertEqual(smoke.stage_profile_modules(profile, expected), expected)
                self.assertEqual(smoke.verify_profile_modules(profile, expected), expected)
                with self.assertRaises(ValueError):
                    smoke.stage_profile_modules(profile, expected)
            staged = profile / "chrome/volparossa-ubo-proof"
            self.assertEqual({p.name for p in staged.iterdir()}, {Path(p).name for p in smoke.MODULES})
            for name in smoke.MODULES:
                copied = staged / Path(name).name
                self.assertEqual(copied.read_bytes(), (root / name).read_bytes())
                self.assertEqual(stat.S_IMODE(copied.stat().st_mode), 0o400)
                self.assertEqual(stat.S_IMODE((root / name).stat().st_mode), 0o664)
        self.assertNotIn("moduleDirectory", smoke.BOOTSTRAP)
        self.assertIn('Services.dirsvc.get("ProfD", Ci.nsIFile)', smoke.BOOTSTRAP)
        self.assertIn('["chrome", "volparossa-ubo-proof"]', smoke.BOOTSTRAP)
        self.assertNotIn("security.sandbox", (ROOT / "scripts/smoke_ubo_actor.py").read_text())

    def test_module_staging_refuses_preexisting_symlink_wrong_owner_or_mode(self):
        import tempfile
        import os
        for kind in ("existing", "chrome-symlink", "profile-symlink", "owner", "mode", "source-symlink", "source-mode"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                expected = self.module_source(root)
                profile = root / "profile"
                profile.mkdir(mode=0o700)
                other = root / "other"
                other.mkdir(mode=0o700)
                if kind == "existing":
                    (profile / "chrome").mkdir(mode=0o700)
                elif kind == "chrome-symlink":
                    (profile / "chrome").symlink_to(other, target_is_directory=True)
                elif kind == "profile-symlink":
                    profile = root / "linked-profile"
                    profile.symlink_to(other, target_is_directory=True)
                elif kind == "mode":
                    profile.chmod(0o755)
                elif kind == "source-symlink":
                    source = root / smoke.MODULES[0]
                    moved = root / "original.mjs"
                    source.rename(moved)
                    source.symlink_to(moved)
                elif kind == "source-mode":
                    (root / smoke.MODULES[0]).chmod(0o666)
                actual_uid = os.getuid()
                with patch.object(smoke, "ROOT", root), \
                        patch.object(smoke.os, "getuid", return_value=actual_uid + int(kind == "owner")), \
                        self.assertRaises(ValueError):
                    smoke.stage_profile_modules(profile, expected)
                self.assertEqual(list(other.iterdir()), [])
                self.assertFalse((profile / "chrome/volparossa-ubo-proof").exists())

    def test_module_binding_refuses_manifest_tamper_extra_names_and_changed_files(self):
        import tempfile
        for kind in ("manifest-key", "manifest-digest", "extra", "missing", "content", "source", "mode", "group-write", "symlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                expected = self.module_source(root)
                profile = root / "profile"
                profile.mkdir(mode=0o700)
                with patch.object(smoke, "ROOT", root):
                    smoke.stage_profile_modules(profile, expected)
                    staged = profile / "chrome/volparossa-ubo-proof"
                    target = staged / Path(smoke.MODULES[0]).name
                    if kind == "manifest-key":
                        expected["other.sys.mjs"] = "0" * 64
                    elif kind == "manifest-digest":
                        expected[smoke.MODULES[0]] = "0" * 64
                    elif kind == "extra":
                        (staged / "extra.sys.mjs").write_text("not authorized")
                    elif kind == "missing":
                        target.unlink()
                    elif kind == "content":
                        target.chmod(0o600)
                        target.write_text("changed")
                        target.chmod(0o400)
                    elif kind == "source":
                        (root / smoke.MODULES[0]).write_text("changed original")
                    elif kind == "mode":
                        target.chmod(0o600)
                    elif kind == "group-write":
                        target.chmod(0o440 | 0o020)
                    elif kind == "symlink":
                        target.unlink()
                        target.symlink_to(root / smoke.MODULES[0])
                    with self.assertRaises((ValueError, FileNotFoundError)):
                        smoke.verify_profile_modules(profile, expected)

    def test_module_staging_precedes_launch_and_verification_runs_after_failed_browser(self):
        import tempfile
        from contextlib import nullcontext
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "user"
            expected = {name: smoke.digest(ROOT / name) for name in smoke.MODULES}
            report = {"modules_sha256": expected}
            def failed_browser(stage, actual_work, profile, phase, exercise):
                self.assertEqual(actual_work, work)
                self.assertEqual(phase, "enroll")
                smoke.verify_profile_modules(profile, expected)
                raise RuntimeError("inert browser failure")
            with patch.object(smoke, "serve_fixture", return_value=nullcontext((smoke.ORIGIN, None))), \
                    patch.object(smoke, "run_browser", side_effect=failed_browser) as launched, \
                    patch.object(smoke, "verify_profile_modules", wraps=smoke.verify_profile_modules) as verified:
                with self.assertRaises(RuntimeError):
                    smoke.campaign(Path("/unused"), work, "user", report)
                launched.assert_called_once()
                self.assertEqual(verified.call_count, 4)  # staging, before, inert check, finally
            self.assertEqual(report["modules_sha256"], expected)
            self.assertEqual(report["staged_modules_sha256"], {"user": expected})


if __name__ == "__main__":
    unittest.main()
