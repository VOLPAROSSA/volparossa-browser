# SPDX-License-Identifier: GPL-3.0-only
"""Inert driver tests: no xpcshell, Firefox, namespaces or network requests."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import smoke_filter_native as smoke


def success():
    return dict(version=1, kind="parent-only-native-webrequest", synthetic_policy=True,
                process_clock_simulation=True, original_ubo=False, default_enrollment=False,
                checks={name: True for name in smoke.CHECKS})


def log_bytes(value=None):
    phases = b"".join(smoke.PHASE_PREFIX + name.encode() + b"\n" for name in smoke.PHASES)
    return (phases + smoke.PREFIX + json.dumps(success() if value is None else value,
                                    separators=(",", ":")).encode() + b"\n" + smoke.COMPLETE + b"\n")


class NativeFilterDriverTests(unittest.TestCase):
    def setUp(self):
        # Unexpected process launch is a test failure, never an actual launch.
        self.process_guard = patch.object(smoke.subprocess, "Popen", side_effect=AssertionError("no process"))
        self.process_guard.start()
        self.addCleanup(self.process_guard.stop)

    def test_exact_fixed_runtime_and_source_pins(self):
        self.assertEqual(smoke.NATIVE, ROOT.parent / "codex-network-gateway/build/native-firefox-157")
        self.assertEqual(smoke.REVISION, "47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1")
        self.assertEqual(smoke.PATCHED[smoke.WEB], "a8a4527629508fd3f1dff70d7361a5fa028d92eb67011d8e6ddfefdebae8532d")
        for name, value in smoke.MODULES.items():
            self.assertEqual(smoke.digest(ROOT / "integration/filters" / name), value)
        self.assertEqual(smoke.digest(smoke.FIXTURE), smoke.FIXTURE_SHA)
        self.assertEqual(smoke.digest(ROOT / "patches/0003-filter-admission.patch"), smoke.PATCH_SHA)
        self.assertEqual(set(smoke.SCOPE), {"kind", "compiled_admission_build", "original_signed_ubo",
            "publication_owner", "broker_fetch", "default_enrollment", "real_os_suspend",
            "content_process", "socket_process", "controlled_exception_tests_included"})
        self.assertTrue(all(value is False for key, value in smoke.SCOPE.items() if key != "kind"))

    def test_no_execute_flag_never_creates_output_or_starts_process(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "absent"
            with self.assertRaises(ValueError):
                smoke.main(["--output", str(output)])
            self.assertFalse(output.exists())

    def test_closed_result_requires_exact_types_every_check_and_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "log"
            path.write_bytes(log_bytes())
            self.assertEqual(smoke.result_from_log(path), success())
            bad = []
            for name in success():
                value = success()
                del value[name]
                bad.append(log_bytes(value))
            for name in smoke.CHECKS:
                value = success()
                value["checks"][name] = False
                bad.append(log_bytes(value))
            for key, wrong in (("version", True), ("original_ubo", 0),
                               ("synthetic_policy", 1), ("kind", "compiled")):
                bad.append(log_bytes(dict(success(), **{key: wrong})))
            bad.extend([log_bytes(dict(success(), extra="raw")), log_bytes() + log_bytes(),
                        log_bytes().replace(smoke.COMPLETE + b"\n", b""),
                        smoke.COMPLETE + b"\n" + log_bytes(),
                        log_bytes().replace(b'"version":1', b'"version":1,"version":1'),
                        log_bytes().replace(b'"version":1', b'"version": 1'),
                        log_bytes() + b'{"action":"test_status","status":"FAIL"}\n',
                        log_bytes() + b'{"action":"log","level": "ERROR"}\n',
                        log_bytes() + b'TEST-UNEXPECTED-FAIL\n'])
            for data in bad:
                path.write_bytes(data)
                with self.subTest(data=data[:60]), self.assertRaises(ValueError):
                    smoke.result_from_log(path)

    def test_log_cannot_be_symlink_or_over_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "log"
            path.write_bytes(log_bytes())
            link = Path(directory) / "link"
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                smoke.result_from_log(link)
            with patch.object(smoke, "MAX_LOG", 8), self.assertRaises(ValueError):
                smoke.result_from_log(path)

    def test_bootstrap_progress_is_closed_ordered_and_never_claims_partial_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "log"
            self.assertEqual(smoke.progress_from_log(path), "not_observed")
            path.write_bytes(b"")
            self.assertEqual(smoke.progress_from_log(path), "not_observed")
            observed = b""
            for phase in smoke.PHASES:
                observed += smoke.PHASE_PREFIX + phase.encode() + b"\n"
                path.write_bytes(observed)
                self.assertEqual(smoke.progress_from_log(path), phase)
                with self.assertRaises(ValueError):
                    smoke.result_from_log(path)
            for invalid in (smoke.PHASE_PREFIX + b"head_loaded\n", observed + observed,
                            smoke.PHASE_PREFIX + b"/raw/path\n", smoke.COMPLETE + b"\n"):
                path.write_bytes(invalid)
                self.assertEqual(smoke.progress_from_log(path), "invalid_progress")
            path.write_bytes(log_bytes())
            self.assertEqual(smoke.progress_from_log(path), "harness_complete")

    def test_output_is_new_direct_child_and_never_symlink_or_existing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "build").mkdir()
            with patch.object(smoke, "ROOT", root):
                output = root / "build/fresh"
                self.assertEqual(smoke.checked_output(output, fresh=True), output)
                for bad in (Path("relative"), root, root / "build", root / "build/a/b"):
                    with self.assertRaises(ValueError):
                        smoke.checked_output(bad, fresh=True)
                output.mkdir(mode=0o700)
                self.assertEqual(smoke.checked_output(output, fresh=False), output)
                with self.assertRaises(ValueError):
                    smoke.checked_output(output, fresh=True)
                output.chmod(0o755)
                with self.assertRaises(ValueError):
                    smoke.checked_output(output, fresh=False)
                link = root / "build/link"
                link.symlink_to(output)
                with self.assertRaises(ValueError):
                    smoke.checked_output(link, fresh=False)

    def test_inventory_rejects_escaping_or_directory_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "modules"
            folder.mkdir()
            (folder / "file").write_bytes(b"original")
            (folder / "link").symlink_to(folder / "file")
            before = smoke.inventory(folder, (folder,))
            self.assertEqual(before["link"]["sha256"], smoke.digest(folder / "file"))
            (folder / "file").write_bytes(b"tamper")
            self.assertNotEqual(smoke.inventory_hash(before), smoke.inventory_hash(smoke.inventory(folder, (folder,))))
            (folder / "outside").symlink_to(root)
            with self.assertRaises(ValueError):
                smoke.inventory(folder, (folder,))
            (folder / "outside").unlink()
            (root / "foreign").write_bytes(b"unrelated")
            (folder / "outside").symlink_to(root / "foreign")
            with self.assertRaises(ValueError):
                smoke.inventory(folder, (folder,))

    def test_resource_overlay_replaces_exact_three_entries_not_original_symlink_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            native, stage, work = root / "native", root / "stage", root / "work"
            modules = native / "obj/dist/bin/modules"
            modules.mkdir(parents=True)
            work.mkdir()
            (native / "original").write_bytes(b"original WebRequest")
            (modules / "WebRequest.sys.mjs").symlink_to(native / "original")
            (modules / "keep").write_bytes(b"retained unchanged")
            patched = stage / "patched" / smoke.WEB
            patched.parent.mkdir(parents=True)
            patched.write_bytes(b"reviewed patch")
            own = root / "integration/filters"
            own.mkdir(parents=True)
            hashes = {}
            for name in smoke.MODULES:
                (own / name).write_bytes(name.encode())
                hashes[name] = smoke.digest(own / name)
            before = smoke.inventory(modules, (native,))
            with patch.multiple(smoke, ROOT=root, NATIVE=native, STAGE=stage, MODULES=hashes,
                                PATCHED={smoke.WEB: smoke.digest(patched)}):
                actual = smoke.stage_modules(work, before)
                self.assertEqual(actual, smoke.inventory_hash(smoke.inventory(work / "modules", (native, work))))
                self.assertEqual(smoke.inventory(modules, (native,)), before)
                self.assertEqual((native / "original").read_bytes(), b"original WebRequest")
                with self.assertRaises(ValueError):
                    smoke.stage_modules(work, before)

    def test_command_masks_home_readonly_host_and_binds_only_private_output_and_overlay(self):
        work = ROOT / "build/inert-example"
        namespaces = {key: key + ":parent" for key in ("net", "pid", "user")}
        command = smoke.sandbox_command(work, namespaces)
        for flag in ("--die-with-parent", "--new-session", "--unshare-user", "--unshare-pid", "--unshare-net"):
            self.assertIn(flag, command)
        at = command.index("--ro-bind")
        self.assertEqual(command[at:at+3], ["--ro-bind", "/", "/"])
        self.assertEqual(command.count("--bind"), 1)
        at = command.index("--bind")
        self.assertEqual(command[at:at+3], ["--bind", str(work), str(work)])
        self.assertIn(str(smoke.NATIVE / "obj/dist/bin/modules"), command)
        self.assertNotIn("--share-net", command)
        self.assertNotIn("--cap-add", command)
        self.assertNotIn("--uid", command)
        self.assertNotIn("firefox", command)
        self.assertIn("--inside", command)

    def test_environment_is_closed_no_sandbox_override_no_inherited_loader_or_proxy(self):
        with patch.dict(os.environ, {"MOZ_DISABLE_CONTENT_SANDBOX": "1", "LD_PRELOAD": "/foreign",
                                     "HTTP_PROXY": "foreign", "PYTHONPATH": "/foreign"}):
            work = Path("/private/work")
            env = smoke.clean_environment(work)
        for name in ("MOZ_DISABLE_CONTENT_SANDBOX", "MOZ_DISABLE_SOCKET_PROCESS_SANDBOX",
                     "MOZ_FORCE_USE_SOCKET_PROCESS", "LD_PRELOAD", "HTTP_PROXY", "PYTHONPATH"):
            self.assertNotIn(name, env)
        self.assertEqual(env["MOZ_DISABLE_NONLOCAL_CONNECTIONS"], "1")
        self.assertEqual(env["MOZ_DISABLE_SOCKET_PROCESS"], "1")
        self.assertEqual(env["MOZ_STARTUP_CACHE"], str(work / "cache/startupCache"))
        self.assertEqual(env["XDG_CACHE_HOME"], str(work / "cache"))
        self.assertEqual(env["XPCSHELL_TEST_PROFILE_DIR"], str(work / "profile"))

    def test_bootstrap_uses_only_standard_head_and_fixed_fixture(self):
        bootstrap = smoke.bootstrap(Path("/private/work")).decode()
        self.assertIn('const _HEAD_FILES = [];', bootstrap)
        # _TEST_CWD invokes an Android-only native global. Linux Popen and
        # bwrap already supply the exact private working directory.
        self.assertNotIn('_TEST_CWD', bootstrap)
        self.assertIn(str(smoke.FIXTURE), bootstrap)
        self.assertIn('load(_HEAD_JS_PATH);', bootstrap)
        self.assertLess(bootstrap.index('print("FILTER_NATIVE_PHASE:bootstrap_enter")'), bootstrap.index('load(_HEAD_JS_PATH)'))
        self.assertLess(bootstrap.index('load(_HEAD_JS_PATH)'), bootstrap.index('print("FILTER_NATIVE_PHASE:head_loaded")'))
        self.assertLess(bootstrap.index('print("FILTER_NATIVE_PHASE:test_execute")'), bootstrap.index('_execute_test();'))
        self.assertLess(bootstrap.index('_execute_test();'), bootstrap.index('print("FILTER_NATIVE_HARNESS_COMPLETE")'))
        self.assertNotIn("runxpcshelltests", bootstrap)
        self.assertNotIn("setBoolPref", bootstrap)
        self.assertNotIn("extensions/test/xpcshell/head", bootstrap)

    def test_isolation_refuses_shared_namespace_nonloopback_or_writable_host(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "build/one"
            work.mkdir(parents=True, mode=0o700)
            (root / "home").mkdir()
            namespaces = {key: key + ":parent" for key in ("net", "pid", "user")}
            with patch.object(smoke, "ROOT", root), \
                 patch.object(smoke.pwd, "getpwuid", return_value=SimpleNamespace(pw_dir=str(root / "home"))), \
                 patch.object(smoke.os, "readlink", return_value="child"), \
                 patch.object(smoke.socket, "if_nameindex", return_value=[(1, "lo")]), \
                 patch.object(smoke.os, "statvfs", side_effect=lambda path: SimpleNamespace(f_flag=0 if path == work else os.ST_RDONLY)):
                smoke.validate_isolation(work, namespaces)
                with patch.object(smoke.os, "readlink", return_value="net:parent"), self.assertRaises(ValueError):
                    smoke.validate_isolation(work, namespaces)
                with patch.object(smoke.socket, "if_nameindex", return_value=[(1, "lo"), (2, "eth0")]), self.assertRaises(ValueError):
                    smoke.validate_isolation(work, namespaces)
                with patch.object(smoke.os, "statvfs", return_value=SimpleNamespace(f_flag=0)), self.assertRaises(ValueError):
                    smoke.validate_isolation(work, namespaces)
                (root / "home/credential").write_text("inert")
                with self.assertRaises(ValueError):
                    smoke.validate_isolation(work, namespaces)

    def test_resource_bounds_are_local_child_limits(self):
        with patch.object(smoke.resource, "setrlimit") as limit:
            smoke.child_limits()
        self.assertEqual(len(limit.call_args_list), 5)
        values = {call.args[0]: call.args[1] for call in limit.call_args_list}
        self.assertEqual(values[smoke.resource.RLIMIT_CORE], (0, 0))
        self.assertEqual(values[smoke.resource.RLIMIT_FSIZE], (smoke.MAX_LOG, smoke.MAX_LOG))
        self.assertEqual(smoke.NATIVE_SECONDS, 90)
        self.assertEqual(smoke.OUTER_SECONDS, 150)

    def test_timeout_reaps_exact_owned_process_group_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = Mock(pid=1234567)
            process.poll.side_effect = [None, None, -9]
            process.wait.side_effect = [subprocess.TimeoutExpired("inert", 2), -9]
            with patch.object(smoke.subprocess, "Popen", return_value=process) as start, \
                 patch.object(smoke.time, "monotonic", side_effect=[100, 200]), \
                 patch.object(smoke.os, "killpg") as kill:
                with self.assertRaises(ValueError):
                    smoke.run_bounded(["inert-not-executed"], root / "log", root, 1)
            self.assertEqual(start.call_count, 1)
            self.assertTrue(start.call_args.kwargs["start_new_session"])
            self.assertEqual(kill.call_args_list[0].args, (1234567, signal.SIGTERM))
            self.assertEqual(kill.call_args_list[1].args, (1234567, signal.SIGKILL))

    def test_completed_process_is_not_signalled_after_pid_can_be_reaped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = Mock(pid=1234567, returncode=0)
            process.poll.return_value = 0
            with patch.object(smoke.subprocess, "Popen", return_value=process), \
                 patch.object(smoke.os, "killpg") as kill:
                self.assertEqual(smoke.run_bounded(["inert"], root / "log", root, 1), 0)
            kill.assert_not_called()

    def test_cleanup_removes_only_fixed_private_state_and_refuses_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "build/one"
            work.mkdir(parents=True, mode=0o700)
            for name in ("profile", "tmp", "cache", "retained-evidence"):
                (work / name).mkdir()
            (work / "report.json").write_text("original")
            with patch.object(smoke, "ROOT", root):
                smoke.remove_private_state(work)
                self.assertEqual(sorted(path.name for path in work.iterdir()), ["report.json", "retained-evidence"])
                (work / "profile").symlink_to(work / "retained-evidence")
                with self.assertRaises(ValueError):
                    smoke.remove_private_state(work)
            self.assertTrue((work / "retained-evidence").is_dir())

    def test_failed_native_execution_retains_logs_cleans_state_and_never_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "build").mkdir()
            work = root / "build/one"
            def failed_run(_command, log, _work, _seconds):
                smoke.write_new(log, b"original failure\n")
                return 1
            with ExitStack() as stack:
                for name, value in dict(ROOT=root).items():
                    stack.enter_context(patch.object(smoke, name, value))
                stack.enter_context(patch.object(smoke, "validate_inputs", return_value={"pinned": True}))
                stack.enter_context(patch.object(smoke, "inventory", return_value={}))
                stack.enter_context(patch.object(smoke, "stage_modules", return_value="a" * 64))
                stack.enter_context(patch.object(smoke, "host_snapshot", return_value={"unchanged": True}))
                stack.enter_context(patch.object(smoke, "sandbox_command", return_value=["not-run"]))
                stack.enter_context(patch.object(smoke, "run_bounded", side_effect=failed_run))
                self.assertEqual(smoke.execute(work), 1)
            report = json.loads((work / "report.json").read_text())
            self.assertFalse(report["passed"])
            self.assertFalse(report["native_process_completed"])
            self.assertTrue(report["cleanup"])
            self.assertEqual(report["bootstrap_phase"], "not_observed")
            self.assertEqual((work / "sandbox.log").read_bytes(), b"original failure\n")
            self.assertFalse((work / "profile").exists())
            self.assertEqual(stat.S_IMODE((work / "report.json").stat().st_mode), 0o600)

    def test_success_requires_cleanup_and_unchanged_host_not_only_fixture_line(self):
        for host_changed, cleanup_failed in ((False, False), (True, False), (False, True)):
            with self.subTest(host_changed=host_changed, cleanup_failed=cleanup_failed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "build").mkdir()
                work = root / "build/one"
                def completed_run(_command, log, _work, _seconds):
                    smoke.write_new(log, b"sandbox completed\n")
                    smoke.write_new(work / "xpcshell.log", log_bytes())
                    smoke.write_new(work / "native-result.json", smoke.encoded(success()))
                    return 0
                with ExitStack() as stack:
                    stack.enter_context(patch.object(smoke, "ROOT", root))
                    stack.enter_context(patch.object(smoke, "validate_inputs", return_value={"pinned": True}))
                    stack.enter_context(patch.object(smoke, "inventory", return_value={}))
                    stack.enter_context(patch.object(smoke, "stage_modules", return_value="a" * 64))
                    stack.enter_context(patch.object(smoke, "host_snapshot", side_effect=[{"state": 0}, {"state": int(host_changed)}]))
                    stack.enter_context(patch.object(smoke, "sandbox_command", return_value=["not-run"]))
                    stack.enter_context(patch.object(smoke, "run_bounded", side_effect=completed_run))
                    if cleanup_failed:
                        stack.enter_context(patch.object(smoke, "remove_private_state", side_effect=ValueError("fixed failure")))
                    expected = not (host_changed or cleanup_failed)
                    self.assertEqual(smoke.execute(work), 0 if expected else 1)
                report = json.loads((work / "report.json").read_text())
                self.assertEqual(report["passed"], expected)
                self.assertTrue(report["native_process_completed"])
                self.assertEqual(report["cleanup"], not cleanup_failed)
                self.assertEqual(report["host_snapshot_unchanged"], not host_changed)

    def test_fixture_is_real_channel_path_and_explicit_synthetic_scope(self):
        source = smoke.FIXTURE.read_text()
        for token in ('new WebExtensionPolicy(', 'new MatchPatternSet(', 'NetUtil.newChannel(',
                      'channel.asyncOpen(', 'WebRequest.onBeforeRequest.addListener(',
                      'server._start(-1, "127.0.0.1")', 'await cleanup();',
                      'expiredOwner.now.bootMs += 1000;', 'invalidatedOwner.handle.invalidate();'):
            self.assertIn(token, source)
        for forbidden in ('eval(', 'Cu.eval', 'setBoolPref(', 'loadExtension(', 'fetch(',
                          'ChromeUtils.importESModule("resource://gre/modules/VolparossaNetwork'):
            self.assertNotIn(forbidden, source)
        self.assertEqual(source.count('print("FILTER_NATIVE_RESULT:"'), 1)


if __name__ == "__main__":
    unittest.main()
