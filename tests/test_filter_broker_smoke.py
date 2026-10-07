# SPDX-License-Identifier: GPL-3.0-only
"""Inert fixture/evidence tests; no Firefox, core process or namespace is started."""
import argparse
import ast
from contextlib import ExitStack
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import smoke_filter_broker as smoke


def success():
    return dict(schema=1, ok=True, capabilities_validated=True, status_unavailable=True,
                fetch_error="unavailable", separate_connection=True, authority_removed=True,
                revoked_error="revoked", closed_after_errors=True, positive_content_fetch=False)


def sources(root):
    directory = root / "integration/filters"
    directory.mkdir(parents=True)
    values = {}
    for name in smoke.MODULES:
        path = root / name
        path.write_text("// inert source for staging-only test\n")
        path.chmod(0o664)
        values[name] = smoke.digest(path)
    return values


class FilterBrokerSmokeTests(unittest.TestCase):
    def test_success_requires_every_actual_boundary_and_preserves_false_scope(self):
        self.assertEqual(smoke.validate_result(success()), success())
        for key, value in success().items():
            bad = dict(success())
            del bad[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                smoke.validate_result(bad)
            bad[key] = not value if type(value) is bool else "wrong"
            with self.subTest(key=key), self.assertRaises(ValueError):
                smoke.validate_result(bad)
        for bad in (dict(success(), extra="raw"), dict(success(), schema=True),
                    dict(success(), positive_content_fetch=0), None, [], True):
            with self.assertRaises(ValueError):
                smoke.validate_result(bad)
        for name in ("positive_content_fetch", "protected_peer_fetch", "publication_resolved",
                     "ubo_activation", "production_default_activation", "authority_expiry_proven",
                     "startup_resume_stale_filter_barrier", "native_source_hooks"):
            self.assertIs(smoke.SCOPE[name], False)

    def test_failure_envelope_is_closed_no_raw_errors_or_unrecognized_fields(self):
        value = dict(schema=1, ok=False, phase="status_revoked", code="revoked")
        with self.assertRaises(smoke.ProofFailure) as error:
            smoke.validate_result(value)
        self.assertEqual((error.exception.phase, error.exception.code), ("status_revoked", "revoked"))
        for bad in (dict(value, schema=True), dict(value, code="/private/name"),
                    dict(value, phase="arbitrary"), dict(value, stack="secret")):
            with self.assertRaises(ValueError) as error:
                smoke.validate_result(bad)
            self.assertNotIn("secret", str(error.exception))
            self.assertNotIn("/private/name", str(error.exception))

    def test_exact_real_gecko_and_broker_no_simulated_response_or_agent(self):
        ast.parse((ROOT / "scripts/smoke_filter_broker.py").read_text())
        script = smoke.SCRIPT
        self.assertIn('resource://volparossa-filter-proof/Gecko.sys.mjs', script)
        self.assertEqual(script.count("VolparossaFilters.connect(expected)"), 2)
        for fragment in ("await client.fetch()", "await IOUtils.remove(authority.path)",
                         'fetchCode !== "unavailable"', 'revokeCode !== "revoked"',
                         'authority.append("authority.json")', "client.session.closed"):
            self.assertIn(fragment, script)
        for forbidden in ("new FilterBrokerSession", "session.push", "setTimeout(", "toImport",
                          "applyFilterListSelection", "eval(", "sandbox.disabled", "permissions.default"):
            self.assertNotIn(forbidden, script)
        command = smoke.broker_command(Path("/exact/core"), smoke.SHORT_WORK)
        self.assertEqual(command[:5], ["/exact/core", "--control-socket",
                         "/tmp/vpf/profile/volparossa-filter-broker/absent-agent.sock", "content", "filter-serve"])
        self.assertEqual(command[-3:], ["--socket", "/tmp/vpf/profile/volparossa-filter-broker/filter.sock", "--execute"])
        with self.assertRaises(ValueError):
            smoke.broker_command(Path("/exact/core"), Path("/" + "a" * 90))

    def test_wrapper_preserves_isolation_and_only_aliases_new_work_under_tmp(self):
        args = argparse.Namespace(broker="/readonly/core", broker_sha256="a" * 64)
        with patch.object(smoke.os, "readlink", return_value="net:[fixture-parent]"):
            command = smoke.wrapper_command(args, Path("/readonly/stage"), Path("/owned/fresh"), {}, ["--ro-bind", "/owned/home", "/owned/home"])
        for item in ("--die-with-parent", "--unshare-user", "--unshare-net", "--unshare-pid"):
            self.assertIn(item, command)
        self.assertEqual(command[5:8], ["--ro-bind", "/", "/"])
        at = command.index("--tmpfs")
        self.assertEqual(command[at:at+5], ["--tmpfs", "/tmp", "--bind", "/owned/fresh", "/tmp/vpf"])
        self.assertEqual(command[command.index("--output")+1], "/tmp/vpf")
        self.assertNotIn("--uid", command)
        self.assertNotIn("--share-net", command)
        self.assertEqual(smoke.DEADLINE_SECONDS, 480)

    def test_binary_binding_rejects_tamper_symlink_permissions_and_bad_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "core"
            binary.write_bytes(b"inert-not-executed")
            binary.chmod(0o755)
            sha = smoke.digest(binary)
            self.assertEqual(smoke.validate_binary(binary, sha), sha)
            for bad in (sha + "\n", "0" * 64, True):
                with self.assertRaises(ValueError):
                    smoke.validate_binary(binary, bad)
            link = Path(directory) / "link"
            link.symlink_to(binary)
            with self.assertRaises(ValueError):
                smoke.validate_binary(link, sha)
            binary.chmod(0o777)
            with self.assertRaises(ValueError):
                smoke.validate_binary(binary, sha)
            binary.chmod(0o644)
            with self.assertRaises(ValueError):
                smoke.validate_binary(binary, sha)
            binary.chmod(0o755)
            binary.write_bytes(b"changed")
            with self.assertRaises(ValueError):
                smoke.validate_binary(binary, sha)

    def test_private_module_staging_exact_bytes_and_original_source_modes_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = sources(root)
            profile = root / "profile"
            profile.mkdir(mode=0o700)
            with patch.object(smoke, "ROOT", root):
                smoke.stage_modules(profile, manifest)
                smoke.verify_modules(profile, manifest)
                for name in smoke.MODULES:
                    target = profile / "chrome" / smoke.PROFILE_MODULES / Path(name).name
                    self.assertEqual(target.read_bytes(), (root / name).read_bytes())
                    self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o400)
                    self.assertEqual(stat.S_IMODE((root / name).stat().st_mode), 0o664)
                with self.assertRaises(ValueError):
                    smoke.stage_modules(profile, manifest)

    def test_staging_rejects_changed_hash_extra_file_or_writable_staged_module(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = sources(root)
            profile = root / "profile"
            profile.mkdir(mode=0o700)
            with patch.object(smoke, "ROOT", root):
                bad = dict(manifest)
                bad[smoke.MODULES[0]] = "f" * 64
                with self.assertRaises(ValueError):
                    smoke.stage_modules(profile, bad)
                self.assertFalse((profile / "chrome").exists())
                smoke.stage_modules(profile, manifest)
                target = profile / "chrome" / smoke.PROFILE_MODULES / Path(smoke.MODULES[0]).name
                target.chmod(0o440)
                with self.assertRaises(ValueError):
                    smoke.verify_modules(profile, manifest)
                target.chmod(0o400)
                extra = target.parent / "extra.sys.mjs"
                extra.write_text("// not admitted")
                with self.assertRaises(ValueError):
                    smoke.verify_modules(profile, manifest)

    def test_fixture_has_unresolved_authority_no_secret_and_no_existing_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = sources(root)
            work = root / "work"
            work.mkdir(mode=0o700)
            with patch.object(smoke, "ROOT", root):
                expected = smoke.prepare_fixture(work, manifest)
            parent = work / "profile/volparossa-filter-broker"
            grant = json.loads((parent / "authority.json").read_text())
            self.assertEqual(grant["manifest_id"], expected["manifest_id"])
            self.assertEqual(grant["publisher_key"], smoke.PUBLISHER)
            self.assertEqual(stat.S_IMODE((parent / "authority.json").stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o700)
            self.assertFalse((parent / "filter.sock").exists())
            self.assertFalse((parent / "absent-agent.sock").exists())
            self.assertEqual(set(grant), {"version", "enabled", "public_content", "authorize_filter_publisher",
                                         "publisher_key", "name", "manifest_id", "not_after_unix_seconds"})

    def test_marionette_timeout_scoped_and_restored_on_success_or_failure(self):
        client = Mock()
        client.socket.gettimeout.return_value = 20
        client.command.side_effect = [None, None, {"value": success()}]
        self.assertEqual(smoke.exercise(client, {}), success())
        self.assertEqual(client.socket.settimeout.call_args_list[0].args, (95,))
        self.assertEqual(client.socket.settimeout.call_args_list[-1].args, (20,))
        client.command.side_effect = [None, None, RuntimeError("raw private failure")]
        with self.assertRaises(RuntimeError):
            smoke.exercise(client, {})
        self.assertEqual(client.socket.settimeout.call_args_list[-1].args, (20,))

    def test_phase_trace_refuses_raw_extra_fields_oversize_and_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "profile").mkdir()
            path = work / "profile" / smoke.TRACE_NAME
            self.assertIsNone(smoke.phase_trace(work))
            path.write_text(json.dumps(dict(schema=1, phase="fetch_unavailable")))
            self.assertEqual(smoke.phase_trace(work), "fetch_unavailable")
            for value in (dict(schema=True, phase="complete"), dict(schema=1, phase="/private/path"),
                          dict(schema=1, phase="complete", stack="private"), "x" * 129):
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    smoke.phase_trace(work)
            path.unlink()
            path.symlink_to(work / "missing")
            with self.assertRaises(ValueError):
                smoke.phase_trace(work)

    def test_inside_refuses_cleanup_or_report_before_proven_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "profile").mkdir()
            (work / "profile/user-data").write_text("preserve")
            metadata = dict(version="140.16.0", source_stamp="exact", runtime_sha256={})
            args = argparse.Namespace(broker_sha256="a" * 64, host_netns="parent")
            with patch.object(smoke, "SHORT_WORK", work), patch.object(smoke, "validate_isolation", side_effect=ValueError()), \
                    patch.object(smoke, "cleanup") as clean, patch.object(smoke.subprocess, "Popen") as launch:
                with self.assertRaises(smoke.ProofFailure):
                    smoke.inside(args, Path("/readonly/stage"), work, metadata, {})
                clean.assert_not_called()
                launch.assert_not_called()
            self.assertEqual((work / "profile/user-data").read_text(), "preserve")
            self.assertFalse((work / "report.json").exists())

    def test_cleanup_only_exact_owned_fixture_names_preserves_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for name in ("profile", "cache", "config", "tmp", "runtime", "appdata"):
                (work / name).mkdir()
                (work / name / "synthetic").write_text("remove")
            (work / smoke.LOG_NAME).write_text("raw synthetic log")
            (work / "report.json").write_text("retained evidence")
            (work / "unrelated").write_text("preserve")
            self.assertTrue(smoke.cleanup(work))
            self.assertTrue(smoke.cleanup(work))
            self.assertEqual((work / "report.json").read_text(), "retained evidence")
            self.assertEqual((work / "unrelated").read_text(), "preserve")
            (work / "profile").symlink_to(work)
            with self.assertRaises(ValueError):
                smoke.cleanup(work)

    def test_outer_timeout_keeps_original_evidence_and_runs_owned_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            args = ["smoke_filter_broker", "--stage", str(work), "--broker", "/readonly/core",
                    "--broker-sha256", "a" * 64, "--output", str(work)]
            with patch.object(sys, "argv", args), patch.object(smoke, "validate_binary"), \
                    patch.object(smoke, "validate_stage", return_value={}), patch.object(smoke, "validate_retained_bundle"), \
                    patch.object(smoke, "validate_module_manifest"), patch.object(smoke, "build_path", return_value=work), \
                    patch.object(smoke, "prepare_output"), patch.object(smoke, "isolated_browser_home", return_value=[]), \
                    patch.object(smoke, "wrapper_command", return_value=["never-executed"]), \
                    patch.object(smoke.subprocess, "run", side_effect=subprocess.TimeoutExpired("inert", 480)) as run, \
                    patch.object(smoke, "cleanup", return_value=True) as cleanup:
                # Different stage avoids the intentional work/stage overlap rejection.
                sys.argv[2] = "/"
                with self.assertRaises(subprocess.TimeoutExpired):
                    smoke.main()
                cleanup.assert_called_once_with(work)
                self.assertEqual(run.call_args.kwargs["timeout"], 480)
            self.assertEqual(json.loads((work / "outer-cleanup.json").read_text()),
                             dict(schema=1, temporary_profiles_removed=True))

    def test_broker_shutdown_requires_exit_zero_and_forced_kill_never_counts_graceful(self):
        process = Mock(pid=123)
        process.poll.side_effect = [None, 0]
        process.wait.return_value = 0
        with patch.object(smoke.os, "killpg") as kill:
            self.assertEqual(smoke.stop_broker(process), dict(started=True, reaped=True, graceful=True))
            kill.assert_called_once_with(123, smoke.signal.SIGTERM)
        process = Mock(pid=123)
        process.poll.side_effect = [None, -9]
        process.wait.side_effect = [subprocess.TimeoutExpired("inert", 5), -9]
        with patch.object(smoke.os, "killpg") as kill:
            self.assertEqual(smoke.stop_broker(process), dict(started=True, reaped=True, graceful=False))
            self.assertEqual(kill.call_args_list[-1].args, (123, smoke.signal.SIGKILL))

    def inert_inside(self, root, *, graceful=True, failed=False, previous=False):
        manifest = sources(root)
        (root / "build").mkdir()
        work = root / "build/fresh"
        work.mkdir(mode=0o700)
        (work / "appdata").mkdir(mode=0o700)
        if previous:
            (work / "report.json").write_text("original retained report")
        args = argparse.Namespace(broker="/readonly/core", broker_sha256="a" * 64,
                                  host_netns="parent", original_work=str(work))
        metadata = dict(version="140.16.0", source_stamp="exact", runtime_sha256={})

        def browser(*_args, **_kwargs):
            if failed:
                raise smoke.ProofFailure("fetch_unavailable", "invalid_response")
            (work / "profile/volparossa-filter-broker/authority.json").unlink()
            return {"exercise": success()}

        with ExitStack() as stack:
            for name, value in (("ROOT", root), ("SHORT_WORK", work)):
                stack.enter_context(patch.object(smoke, name, value))
            for name in ("validate_isolation", "validate_binary", "validate_stage", "validate_retained_bundle", "wait_socket"):
                stack.enter_context(patch.object(smoke, name))
            stack.enter_context(patch.object(smoke, "build_path", side_effect=Path))
            stack.enter_context(patch.object(smoke, "broker_command", return_value=["inert-never-executed"]))
            stack.enter_context(patch.object(smoke.os, "statvfs", return_value=SimpleNamespace(f_flag=os.ST_RDONLY)))
            launch = stack.enter_context(patch.object(smoke.subprocess, "Popen"))
            stop = stack.enter_context(patch.object(smoke, "stop_broker",
                                       return_value=dict(started=True, reaped=True, graceful=graceful)))
            stack.enter_context(patch.object(smoke, "run_browser", side_effect=browser))
            if failed or not graceful or previous:
                with self.assertRaises(ValueError):
                    smoke.inside(args, Path("/readonly/stage"), work, metadata, manifest)
            else:
                smoke.inside(args, Path("/readonly/stage"), work, metadata, manifest)
            if previous:
                launch.assert_not_called()
                stop.assert_not_called()
                self.assertEqual((work / "report.json").read_text(), "original retained report")
                self.assertTrue((work / "appdata").exists())
                return None
            launch.assert_called_once()
            stop.assert_called_once()
        self.assertFalse((work / "profile").exists())
        return json.loads((work / "report.json").read_text())

    def test_failed_browser_retains_closed_original_failure_and_still_cleans(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.inert_inside(Path(directory), failed=True)
            self.assertIs(report["passed"], False)
            self.assertEqual((report["phase"], report["failure"]), ("fetch_unavailable", "invalid_response"))
            self.assertIs(report["temporary_profiles_removed"], True)
            self.assertIsNone(report["exercise"])
            self.assertIs(report["scope"]["positive_content_fetch"], False)

    def test_inert_success_cannot_pass_if_broker_cleanup_is_not_graceful(self):
        for graceful in (False, True):
            with self.subTest(graceful=graceful), tempfile.TemporaryDirectory() as directory:
                report = self.inert_inside(Path(directory), graceful=graceful)
                self.assertIs(report["passed"], graceful)
                self.assertEqual(report["phase"], "complete" if graceful else "cleanup")
                self.assertEqual(report["exercise"], success())
                self.assertIs(report["scope"]["positive_content_fetch"], False)

    def test_manual_inside_cannot_overwrite_or_cleanup_a_previous_run(self):
        with tempfile.TemporaryDirectory() as directory:
            self.inert_inside(Path(directory), previous=True)


if __name__ == "__main__":
    unittest.main()
