# SPDX-License-Identifier: GPL-3.0-only
"""Inert driver/evidence tests: no Firefox, subprocess, server or network."""
import ast
from contextlib import contextmanager, redirect_stdout
import copy
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import smoke_filter_asset as smoke


def result():
    negative = dict(stopped=True, aborted=True, bytes=0, no_network_flags=True)
    return dict(schema=1, ok=True, phase="complete", cleanup_complete=True,
                synthetic_manifest_sha256=hashlib.sha256(smoke.ENVELOPE).hexdigest(),
                positive=dict(status=dict(closed=False, accepted=1, completed=1, denied=0),
                              bytes=63, sha256=hashlib.sha256(smoke.BODY).hexdigest(),
                              exact_text=True, preserved=True, selected=False, imported=False),
                wrong_principal=dict(negative), wrong_query=dict(negative), after_close=dict(negative),
                final_status=dict(closed=True, accepted=1, completed=1, denied=2))


def diagnostic():
    return dict(checkpoint="request_context", status=dict(closed=False, accepted=0, completed=0, denied=1))


@contextmanager
def staged_sources():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        profile = root / "profile"
        profile.mkdir(mode=0o700)
        pins = {}
        for index, name in enumerate(smoke.MODULES):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            data = ("// inert fixture " + str(index) + "\n").encode()
            path.write_bytes(data)
            path.chmod(0o600)
            pins[name] = hashlib.sha256(data).hexdigest()
        with patch.object(smoke, "ROOT", root), patch.object(smoke, "PINS", pins):
            yield root, profile


class AssetFixtureTests(unittest.TestCase):
    def setUp(self):
        # These guards apply even to failure branches and future test changes.
        for name in ("Popen", "run", "call", "check_call", "check_output"):
            guard = patch.object(smoke.subprocess, name, side_effect=AssertionError("no process"))
            guard.start()
            self.addCleanup(guard.stop)
        for name in ("socket", "create_connection"):
            guard = patch.object(smoke.socket, name, side_effect=AssertionError("no network"))
            guard.start()
            self.addCleanup(guard.stop)
        guard = patch.object(smoke.os, "killpg", side_effect=AssertionError("no process signal"))
        guard.start()
        self.addCleanup(guard.stop)

    def test_default_plan_is_inert_and_unfrozen_execute_fails_before_preparation(self):
        with patch.object(smoke, "PINS", None), \
                patch.object(smoke, "prepare_output") as prepare, \
                patch.object(smoke, "validate_stage") as stage, \
                patch.object(smoke, "inside") as inside, \
                patch.object(smoke.os, "umask") as umask, \
                patch.object(Path, "mkdir") as mkdir, redirect_stdout(io.StringIO()) as output:
            smoke.main([])
            self.assertEqual(json.loads(output.getvalue()), dict(execute=False,
                source_pins_complete=False, scope=smoke.SCOPE, outer_seconds=240, sessions=1))
            for args in (["--execute"], ["--execute", "--stage", "/never-runtime", "--output", "/never-output"],
                         ["--inside"], ["--host-netns", "net:[never]"]):
                with self.subTest(args=args), self.assertRaises(ValueError):
                    smoke.main(args)
            for action in (prepare, stage, inside, umask, mkdir):
                action.assert_not_called()

    def test_exact_module_dependency_set_has_no_owner_journal_or_old_proof(self):
        names = ("AssetChannel", "AssetRedirect", "Actor", "ActorContract", "Selection", "VolparossaFilterSelectionParent",
                 "VolparossaFilterSelectionChild", "Contract", "Frame")
        expected = {"integration/filters/" + name + ".sys.mjs": name + ".sys.mjs" for name in names}
        expected["tests/fixtures/filter_asset.sys.mjs"] = "Fixture.sys.mjs"
        self.assertEqual(smoke.MODULE_NAMES, names)
        self.assertEqual(smoke.MODULES, expected)
        self.assertEqual(len(set(smoke.MODULES.values())), len(expected))
        for name in smoke.MODULES:
            self.assertTrue((ROOT / name).is_file(), name)

    def test_complete_pins_require_exact_lowercase_sha256_per_dependency(self):
        valid = {name: "a" * 64 for name in smoke.MODULES}
        with patch.object(smoke, "PINS", valid):
            self.assertTrue(smoke.pinned())
        variants = [None, [], {}, {**valid, "unexpected": "a" * 64}]
        incomplete = dict(valid)
        incomplete.pop(next(iter(valid)))
        variants.append(incomplete)
        for value in (True, 64, b"a" * 64, "", "a" * 63, "a" * 65, "A" * 64, "g" * 64):
            variants.append({**valid, next(iter(valid)): value})
        for pins in variants:
            with self.subTest(pins=pins), patch.object(smoke, "PINS", pins), \
                    patch.object(smoke, "module_bytes") as read:
                self.assertFalse(smoke.pinned())
                with self.assertRaisesRegex(ValueError, "asset_fixture_unfrozen"):
                    smoke.verify_sources()
                read.assert_not_called()

    def test_source_verification_checks_actual_bytes_not_only_pin_shape(self):
        with staged_sources() as (root, _):
            sources = smoke.verify_sources()
            self.assertEqual(set(sources), set(smoke.MODULES))
            for name, data in sources.items():
                self.assertEqual(hashlib.sha256(data).hexdigest(), smoke.PINS[name])
            (root / "tests/fixtures/filter_asset.sys.mjs").write_bytes(b"changed source")
            with self.assertRaises(ValueError):
                smoke.verify_sources()

    def test_positive_result_is_exact_synthetic_body_and_closed_receipt(self):
        self.assertEqual(smoke.BODY, b"[Adblock Plus 2.0]\n||ads.asset.invalid^\n||track.asset.invalid^\n")
        self.assertEqual(len(smoke.BODY), 63)
        self.assertEqual(smoke.ENVELOPE, b"VOLPAROSSA synthetic unsigned asset fixture v1")
        good = result()
        self.assertIs(smoke.asset_result(good), good)
        for key, value in (("bytes", 62), ("bytes", 64), ("bytes", True), ("bytes", 63.0),
                           ("sha256", "a" * 64), ("sha256", hashlib.sha256(smoke.BODY + b"\n").hexdigest()),
                           ("exact_text", False), ("exact_text", 1), ("preserved", False),
                           ("preserved", 1), ("selected", True), ("selected", 0),
                           ("imported", True), ("imported", 0)):
            bad = result()
            bad["positive"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                smoke.asset_result(bad)

    def test_status_counts_are_exact_integers_and_close_has_two_denials(self):
        for where in ("positive", "final_status"):
            for key in ("accepted", "completed", "denied"):
                expected = (0 if where == "positive" else 2) if key == "denied" else 1
                for value in (True, False, float(expected), -1, expected + 1, str(expected)):
                    bad = result()
                    state = bad["positive"]["status"] if where == "positive" else bad[where]
                    state[key] = value
                    with self.subTest(where=where, key=key, value=value), self.assertRaises(ValueError):
                        smoke.asset_result(bad)
            for value in ((True, 0, None) if where == "positive" else (False, 1, None)):
                bad = result()
                state = bad["positive"]["status"] if where == "positive" else bad[where]
                state["closed"] = value
                with self.subTest(where=where, closed=value), self.assertRaises(ValueError):
                    smoke.asset_result(bad)

    def test_every_negative_requires_native_stop_abort_zero_bytes_and_flags(self):
        for name in ("wrong_principal", "wrong_query", "after_close"):
            for key, value in (("stopped", False), ("stopped", 1), ("aborted", False), ("aborted", 1),
                               ("bytes", 1), ("bytes", -1), ("bytes", False), ("bytes", 0.0),
                               ("no_network_flags", False), ("no_network_flags", 1)):
                bad = result()
                bad[name][key] = value
                with self.subTest(name=name, key=key, value=value), self.assertRaises(ValueError):
                    smoke.asset_result(bad)

    def test_result_schema_manifest_completion_and_every_nested_key_are_closed(self):
        for key, value in (("schema", True), ("schema", 1.0), ("schema", 2), ("ok", False), ("ok", 1),
                           ("phase", "read_asset"), ("cleanup_complete", False), ("cleanup_complete", 1),
                           ("synthetic_manifest_sha256", "a" * 64),
                           ("synthetic_manifest_sha256", hashlib.sha256(smoke.BODY).hexdigest())):
            bad = result()
            bad[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                smoke.asset_result(bad)
        paths = ((), ("positive",), ("positive", "status"), ("final_status",),
                 ("wrong_principal",), ("wrong_query",), ("after_close",))
        for path in paths:
            sample = result()
            target = sample
            for part in path:
                target = target[part]
            for missing in (*target.keys(), None):
                bad = copy.deepcopy(sample)
                target = bad
                for part in path:
                    target = target[part]
                if missing is None:
                    target["unreviewed"] = "private-canary"
                else:
                    del target[missing]
                with self.subTest(path=path, missing=missing), self.assertRaises(ValueError):
                    smoke.asset_result(bad)
        for value in (None, [], {"schema": 1, "ok": False, "phase": "start"}):
            with self.assertRaises(ValueError):
                smoke.asset_result(value)

    def test_scope_does_not_claim_signed_broker_engine_admission_or_default_use(self):
        self.assertEqual(smoke.SCOPE, dict(original_signed_ubo=True, actual_background_asset_request=True,
            synthetic_publication_grant=True, synthetic_manifest=True, browser_sessions=1,
            protected_peer_fetch=False, real_core_broker=False, publisher_signature_verified=False,
            actual_uBO_engine_bytes=False, native_admission_registered=False,
            production_default_enrollment=False, startup_resume_stale_filter_barrier=False,
            real_os_expiry=False, original_https_server=False))
        for key, value in smoke.SCOPE.items():
            self.assertIs(type(value), int if key == "browser_sessions" else bool)

    def test_every_known_failure_preserves_only_closed_phase_and_reason(self):
        for phase in smoke.FAILURE_PHASES:
            for reason in smoke.FAILURE_REASONS:
                value = dict(schema=1, ok=False, phase=phase, reason=reason)
                with self.subTest(phase=phase, reason=reason), self.assertRaises(smoke.AssetFailure) as caught:
                    smoke.asset_result(value)
                error = caught.exception
                self.assertEqual(vars(error), dict(phase=phase, reason=reason))
                self.assertEqual(error.args, (reason,))
                self.assertEqual(str(error), reason)

    def test_malformed_failures_collapse_to_fixed_invalid_reply_without_canary(self):
        canary = "private-canary-url-and-raw-error"
        good = dict(schema=1, ok=False, phase="read_asset", reason="actor_asset")
        variants = [None, [], canary, {**good, "raw_error": canary}]
        for key in good:
            missing = dict(good)
            del missing[key]
            variants.append(missing)
        for key, values in (
                ("schema", (True, 1.0, 0, "1", None)),
                ("ok", (0, 1, "false", None)),
                ("phase", (canary, "", None, True, 1, [], {})),
                ("reason", (canary, "", None, True, 1, [], {}))):
            variants.extend({**good, key: value} for value in values)
        class StringSubclass(str):
            pass
        variants.extend(({**good, "phase": StringSubclass("read_asset")},
                         {**good, "reason": StringSubclass("actor_asset")}))
        for value in variants:
            with self.subTest(value=value), self.assertRaises(smoke.AssetFailure) as caught:
                smoke.asset_result(value)
            error = caught.exception
            self.assertEqual(vars(error), dict(phase="reply_validate", reason="fixture_invalid_reply"))
            self.assertEqual(error.args, ("fixture_invalid_reply",))
            self.assertNotIn(canary, str(error))
            self.assertNotIn(canary, repr(error))
            self.assertNotIn(canary, json.dumps(vars(error)))

    def test_asset_failure_constructor_never_formats_unknown_inputs(self):
        class Unknown:
            def __str__(self):
                raise AssertionError("unknown error must not be formatted")
            def __repr__(self):
                raise AssertionError("unknown error must not be formatted")
            def __eq__(self, _):
                raise AssertionError("unknown value must not be compared")
        for phase, reason in ((Unknown(), "actor_asset"), ("read_asset", Unknown()),
                              ("private-canary", "actor_asset"), ("read_asset", "private-canary"),
                              (None, None), ([], {})):
            error = smoke.AssetFailure(phase, reason)
            self.assertEqual(vars(error), dict(phase="reply_validate", reason="fixture_invalid_reply"))
            self.assertEqual(str(error), "fixture_invalid_reply")
        class UnknownReply(dict):
            def get(self, *args):
                raise AssertionError("unknown reply must not be inspected")
        with self.assertRaises(smoke.AssetFailure) as caught:
            smoke.asset_result(UnknownReply())
        self.assertEqual(caught.exception.reason, "fixture_invalid_reply")

    def test_optional_asset_diagnostic_accepts_closed_checkpoints_and_integer_boundaries(self):
        for checkpoint in smoke.ASSET_CHECKPOINTS:
            for closed in (False, True):
                for count in (0, 65535):
                    value = dict(checkpoint=checkpoint,
                                 status=dict(closed=closed, accepted=count, completed=count, denied=count))
                    with self.subTest(checkpoint=checkpoint, closed=closed, count=count):
                        self.assertEqual(smoke.asset_diagnostic(value), value)
                        reply = dict(schema=1, ok=False, phase="read_asset", reason="asset_context",
                                     asset_diagnostic=value)
                        with self.assertRaises(smoke.AssetFailure) as caught:
                            smoke.asset_result(reply)
                        self.assertEqual(vars(caught.exception), dict(phase="read_asset", reason="asset_context",
                                                                      asset_diagnostic=value))

    def test_optional_asset_diagnostic_rejects_malformed_private_fields_and_types(self):
        canary = "private-canary-url-and-raw-error"
        variants = [None, [], canary, {}, {**diagnostic(), "private": canary}]
        for key in ("checkpoint", "status"):
            value = diagnostic()
            del value[key]
            variants.append(value)
        for checkpoint in (canary, "", None, True, 1, [], {}):
            variants.append({**diagnostic(), "checkpoint": checkpoint})
        for state in (None, [], canary, {}, {**diagnostic()["status"], "private": canary}):
            variants.append({**diagnostic(), "status": state})
        for key in diagnostic()["status"]:
            value = diagnostic()
            del value["status"][key]
            variants.append(value)
        for key in ("accepted", "completed", "denied"):
            for count in (-1, 65536, 2**53, True, False, 0.0, 1.0, "1", None):
                value = diagnostic()
                value["status"][key] = count
                variants.append(value)
        for closed in (0, 1, "false", None):
            value = diagnostic()
            value["status"]["closed"] = closed
            variants.append(value)
        class StringSubclass(str):
            pass
        variants.append({**diagnostic(), "checkpoint": StringSubclass("ready")})
        for value in variants:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    smoke.asset_diagnostic(value)
                reply = dict(schema=1, ok=False, phase="read_asset", reason="asset_context",
                             asset_diagnostic=value)
                with self.assertRaises(smoke.AssetFailure) as caught:
                    smoke.asset_result(reply)
                self.assertEqual(vars(caught.exception), dict(phase="reply_validate", reason="fixture_invalid_reply"))
                self.assertNotIn(canary, str(caught.exception))
                self.assertNotIn(canary, json.dumps(vars(caught.exception)))
                # None means "not supplied" only at the constructor, never in an envelope.
                if value is not None:
                    error = smoke.AssetFailure("read_asset", "asset_context", value)
                    self.assertEqual(vars(error), dict(phase="reply_validate", reason="fixture_invalid_reply"))
        reply = dict(schema=1, ok=False, phase="read_asset", reason="asset_context",
                     asset_diagnostic=diagnostic(), private=canary)
        with self.assertRaises(smoke.AssetFailure) as caught:
            smoke.asset_result(reply)
        self.assertFalse(hasattr(caught.exception, "asset_diagnostic"))

    def test_transfer_identity_and_callbacks_preserve_closed_failure_snapshot(self):
        for checkpoint in ("transfer_identity", "transfer_callbacks"):
            value = dict(checkpoint=checkpoint, status=dict(closed=False, accepted=1, completed=0, denied=0))
            reply = dict(schema=1, ok=False, phase="read_asset", reason="actor_asset", asset_diagnostic=value)
            with self.subTest(checkpoint=checkpoint), self.assertRaises(smoke.AssetFailure) as caught:
                smoke.asset_result(reply)
            self.assertEqual(vars(caught.exception), dict(phase="read_asset", reason="actor_asset",
                                                          asset_diagnostic=value))

    def test_asset_diagnostic_is_copied_and_cannot_preserve_invalid_primary_failure(self):
        source = diagnostic()
        expected = copy.deepcopy(source)
        checked = smoke.asset_diagnostic(source)
        error = smoke.AssetFailure("read_asset", "asset_context", source)
        reply = dict(schema=1, ok=False, phase="read_asset", reason="asset_context", asset_diagnostic=source)
        with self.assertRaises(smoke.AssetFailure) as caught:
            smoke.asset_result(reply)
        source["checkpoint"] = "private-canary"
        source["status"]["denied"] = 65536
        source["status"]["private"] = "private-canary"
        for saved in (checked, error.asset_diagnostic, caught.exception.asset_diagnostic):
            self.assertEqual(saved, expected)
            self.assertIsNot(saved, source)
            self.assertIsNot(saved["status"], source["status"])
        for phase, reason in (("private-canary", "asset_context"), ("read_asset", "private-canary")):
            rejected = smoke.AssetFailure(phase, reason, diagnostic())
            self.assertEqual(vars(rejected), dict(phase="reply_validate", reason="fixture_invalid_reply"))
        self.assertEqual(vars(smoke.AssetFailure("read_asset", "asset_context", None)),
                         dict(phase="read_asset", reason="asset_context"))

    def test_python_and_fixture_diagnostic_enums_remain_identical_and_closed(self):
        fixture = (ROOT / "tests/fixtures/filter_asset.sys.mjs").read_text()
        reasons = re.search(r"const FAILURE_REASONS = new Set\((\[.*?\])\);", fixture, re.S)
        self.assertIsNotNone(reasons)
        # JS accepts a trailing comma; these are reviewed string literals only.
        literal = re.sub(r",\s*\]$", "]", reasons.group(1))
        self.assertEqual(tuple(json.loads(literal)), smoke.FAILURE_REASONS)
        self.assertEqual(smoke.FAILURE_PHASES, ("bootstrap", "reply_validate", *smoke.PHASES))
        self.assertEqual(len(smoke.FAILURE_REASONS), len(set(smoke.FAILURE_REASONS)))
        self.assertEqual(len(smoke.FAILURE_PHASES), len(set(smoke.FAILURE_PHASES)))
        for value in (*smoke.FAILURE_PHASES, *smoke.FAILURE_REASONS):
            self.assertRegex(value, r"^[a-z_]+$")
        self.assertIn('Object.getOwnPropertyDescriptor(error, "code")', fixture)
        self.assertIn('Object.hasOwn(descriptor, "value")', fixture)
        self.assertIn('reason: "fixture_other"', smoke.BOOTSTRAP)

    def test_asset_checkpoint_vocabulary_matches_fixture_exactly(self):
        expected = ("ready", "request_current", "request_uri", "request_method", "request_context",
                    "request_principals", "request_flags", "request_headers", "transfer", "transfer_identity",
                    "transfer_callbacks", "prepare", "intercept", "body_complete")
        self.assertEqual(smoke.ASSET_CHECKPOINTS, expected)
        fixture = (ROOT / "tests/fixtures/filter_asset.sys.mjs").read_text()
        checkpoints = re.search(r"const ASSET_CHECKPOINTS = new Set\((\[.*?\])\);", fixture, re.S)
        self.assertIsNotNone(checkpoints)
        literal = re.sub(r",\s*\]$", "]", checkpoints.group(1))
        self.assertEqual(tuple(json.loads(literal)), expected)

    def test_inside_report_preserves_only_recognized_diagnostics_and_cleans_up(self):
        canary = "private-canary-url-and-raw-error"
        metadata = dict(version="synthetic", source_stamp="synthetic", runtime_sha256={},
                        extensions=dict(packages=[]))
        for error in (smoke.AssetFailure("read_asset", "actor_asset"),
                      smoke.AssetFailure("read_asset", "asset_context", diagnostic()),
                      smoke.AssetFailure("read_asset", "asset_context", {**diagnostic(), "private": canary}),
                      smoke.AssetFailure(canary, canary), ValueError(canary), KeyboardInterrupt(canary)):
            with tempfile.TemporaryDirectory() as folder:
                work = Path(folder)
                with patch.object(smoke, "validate_isolation"), patch.object(smoke, "verify_sources"), \
                        patch.object(smoke, "limits"), patch.object(smoke, "profile_modules"), \
                        patch.object(smoke, "browser", side_effect=error), self.assertRaises(ValueError):
                    smoke.inside(ROOT / "build/never-launched", work, metadata, "net:[synthetic]")
                text = (work / "report.json").read_text()
                report = json.loads(text)
                self.assertNotIn(canary, text)
                self.assertIs(report["passed"], False)
                self.assertEqual(report["failure"], "fixture_failed")
                self.assertIs(report["temporary_profiles_removed"], True)
                if isinstance(error, smoke.AssetFailure):
                    self.assertEqual(report["failure_phase"], error.phase)
                    self.assertEqual(report["closed_failure"], error.reason)
                else:
                    self.assertNotIn("failure_phase", report)
                    self.assertNotIn("closed_failure", report)
                if isinstance(error, smoke.AssetFailure) and hasattr(error, "asset_diagnostic"):
                    self.assertEqual(report["asset_diagnostic"], error.asset_diagnostic)
                else:
                    self.assertNotIn("asset_diagnostic", report)
                self.assertEqual({path.name for path in work.iterdir()}, {"report.json"})

    def test_fixture_matches_body_manifest_and_closed_phase_vocabulary(self):
        fixture = (ROOT / "tests/fixtures/filter_asset.sys.mjs").read_text()
        for key in ("BODY", "ENVELOPE"):
            literal = re.search(r"const " + key + r' = ("(?:[^"\\]|\\.)*");', fixture)
            self.assertIsNotNone(literal)
            self.assertEqual(json.loads(literal.group(1)).encode(), getattr(smoke, key))
        phases = re.search(r"const PHASES = new Set\((\[.*?\])\);", fixture, re.S)
        self.assertIsNotNone(phases)
        self.assertEqual(tuple(json.loads(phases.group(1))), smoke.PHASES)
        driver = (ROOT / "scripts/smoke_filter_asset.py").read_text()
        ast.parse(driver)
        for text in (driver, fixture):
            self.assertNotIn("storage.local.set", text)
            self.assertNotIn("toOverwrite", text)
            self.assertNotIn("acceptInsecureCerts\": True", text)
        self.assertIn('actor.command("readAsset")', fixture)
        self.assertIn("onStopRequest(_request, code)", fixture)
        self.assertIn("code === Cr.NS_BINDING_ABORTED", fixture)
        self.assertIn("LOAD_NO_NETWORK_IO", fixture)
        self.assertIn("LOAD_BYPASS_LOCAL_CACHE", fixture)
        self.assertIn("INHIBIT_CACHING", fixture)
        self.assertIn('acceptInsecureCerts": False', driver)

    def test_staged_module_tree_has_exact_private_modes_and_cannot_be_recreated(self):
        with staged_sources() as (_, profile):
            smoke.profile_modules(profile, create=True)
            smoke.profile_modules(profile)
            target = profile / "chrome" / smoke.PROFILE_MODULES
            for directory in (profile, profile / "chrome", target):
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            self.assertEqual({path.name for path in target.iterdir()}, set(smoke.MODULES.values()))
            for name in smoke.MODULES.values():
                self.assertEqual(stat.S_IMODE((target / name).stat().st_mode), 0o400)
            with self.assertRaises(ValueError):
                smoke.profile_modules(profile, create=True)

    def test_staged_module_permissions_bytes_and_symlinks_are_rejected(self):
        for change in ("mode", "bytes", "symlink", "extra", "directory_mode"):
            with self.subTest(change=change), staged_sources() as (root, profile):
                smoke.profile_modules(profile, create=True)
                folder = profile / "chrome" / smoke.PROFILE_MODULES
                target = folder / "Fixture.sys.mjs"
                if change == "mode":
                    target.chmod(0o600)
                elif change == "bytes":
                    target.chmod(0o600)
                    target.write_bytes(b"changed staged bytes")
                    target.chmod(0o400)
                elif change == "symlink":
                    target.unlink()
                    target.symlink_to(root / "tests/fixtures/filter_asset.sys.mjs")
                elif change == "extra":
                    (folder / "unreviewed.sys.mjs").write_text("// extra")
                else:
                    folder.chmod(0o755)
                with self.assertRaises(ValueError):
                    smoke.profile_modules(profile)

    def test_staging_refuses_existing_chrome_and_source_symlinks(self):
        for change in ("chrome_directory", "chrome_symlink", "source_symlink"):
            with self.subTest(change=change), staged_sources() as (root, profile):
                if change == "chrome_directory":
                    (profile / "chrome").mkdir(mode=0o700)
                elif change == "chrome_symlink":
                    (profile / "chrome").symlink_to(root, target_is_directory=True)
                else:
                    path = root / "tests/fixtures/filter_asset.sys.mjs"
                    saved = root / "retained-source"
                    path.rename(saved)
                    path.symlink_to(saved)
                with self.assertRaises(ValueError):
                    smoke.profile_modules(profile, create=True)

    def test_cleanup_is_idempotent_and_retains_logs_receipt_and_unrelated_data(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            for name in ("profile", "config", "cache", "runtime", "tmp", "appdata"):
                (work / name).mkdir(mode=0o700)
                (work / name / "synthetic").write_text("temporary")
            log = b"private bounded synthetic log\n"
            (work / "asset.log").write_bytes(log)
            (work / "report.json").write_text("{}")
            (work / "unrelated").write_text("retain")
            self.assertTrue(smoke.cleanup(work))
            self.assertEqual({path.name for path in work.iterdir()}, {"asset.log", "report.json", "unrelated"})
            self.assertEqual(smoke.log_receipts(work), {"asset": dict(bytes=len(log), sha256=hashlib.sha256(log).hexdigest())})
            self.assertTrue(smoke.cleanup(work))
            self.assertEqual((work / "asset.log").read_bytes(), log)

    def test_log_receipts_reject_oversize_or_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            self.assertEqual(smoke.log_receipts(work), {})
            log = work / "asset.log"
            log.write_bytes(b"12345")
            with patch.object(smoke, "MAX_LOG", 4), self.assertRaises(ValueError):
                smoke.log_receipts(work)
            retained = work / "retained"
            log.rename(retained)
            log.symlink_to(retained)
            with self.assertRaises(ValueError):
                smoke.log_receipts(work)

    def test_phase_and_operation_records_accept_only_closed_schemas(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            (work / "profile").mkdir(mode=0o700)
            phase_file = work / "profile/volparossa-filter-asset-phase.json"
            self.assertIsNone(smoke.read_phase(work))
            self.assertIsNone(smoke.read_operation(work))
            for phase in smoke.PHASES:
                value = dict(schema=1, phase=phase)
                phase_file.write_text(json.dumps(value))
                self.assertEqual(smoke.read_phase(work), value)
            report = {}
            for operation in smoke.OPERATIONS:
                smoke.write_operation(work, report, operation)
                self.assertEqual(smoke.read_operation(work), dict(schema=1, operation=operation))
            with self.assertRaises(ValueError):
                smoke.write_operation(work, report, "private-canary")
            self.assertEqual(report["operation"], "complete")
            for path, reader, key, good in ((phase_file, smoke.read_phase, "phase", "complete"),
                    (work / "operation.json", smoke.read_operation, "operation", "complete")):
                for value in ({"schema": True, key: good}, {"schema": 1.0, key: good},
                              {"schema": 1, key: "private-canary"}, {"schema": 1, key: good, "private": "canary"},
                              {"schema": 1}, {"schema": 1, key: []}):
                    path.write_text(json.dumps(value))
                    with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                        reader(work)
                path.write_text(" " * 257)
                with self.assertRaises(ValueError):
                    reader(work)
                path.write_text(json.dumps({"schema": 1, key: good}))
                retained = work / (key + "-retained.json")
                path.rename(retained)
                path.symlink_to(retained)
                with self.assertRaises(ValueError):
                    reader(work)

    def test_exercise_restores_socket_timeout_after_valid_or_rejected_receipt(self):
        for value in (result(), {"schema": 1, "ok": False, "phase": "read_asset"}):
            client = Mock()
            client.socket.gettimeout.return_value = 20
            client.command.return_value = {"value": value}
            if value["ok"]:
                self.assertEqual(smoke.exercise(client), value)
            else:
                with self.assertRaises(ValueError):
                    smoke.exercise(client)
            self.assertEqual(client.socket.settimeout.call_args_list,
                             [unittest.mock.call(105), unittest.mock.call(20)])
            client.command.assert_any_call("WebDriver:ExecuteAsyncScript", {"script": smoke.BOOTSTRAP,
                "args": [], "newSandbox": True, "sandbox": "system"})

    def test_socket_process_or_drain_failure_cannot_skip_later_cleanup(self):
        for failing in ("socket", "process", "drain"):
            calls = []
            def record(name):
                calls.append(name)
                if name == failing:
                    raise OSError("synthetic cleanup failure")
            client = SimpleNamespace(socket=SimpleNamespace(close=lambda: record("socket")))
            drain = SimpleNamespace(finish=lambda: record("drain"))
            with self.subTest(failing=failing), \
                    patch.object(smoke, "stop_process", side_effect=lambda _: record("process")), \
                    patch.object(smoke, "profile_modules", side_effect=lambda _: record("modules")), \
                    self.assertRaises(OSError):
                smoke.close_session(client, object(), drain, ROOT / "build/never-launched/profile")
            self.assertEqual(calls, ["socket", "process", "drain", "modules"])


if __name__ == "__main__":
    unittest.main()
