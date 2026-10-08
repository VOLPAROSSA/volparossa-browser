# SPDX-License-Identifier: GPL-3.0-only
"""Inert wrapper/closed evidence checks; never starts Firefox or an HTTP server."""
import ast
from contextlib import redirect_stdout
import io
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import smoke_filter_selection as smoke


def result(operation="enroll", choice="eligible", state="active", attempted=True):
    receipt = dict(selected=True, imported=True, preserved=True, freshReload=True, reloadEvents=2)
    if operation == "suspend": receipt.update(selected=False, imported=False)
    if operation == "observe": receipt = dict(selected=False, imported=False)
    value = dict(schema=1, choice=choice, state=state, attempted=attempted,
                 receipt=receipt if attempted or operation == "observe" else None)
    if operation in ("open", "close"): value = None
    return dict(schema=1, ok=True, phase=operation, invalidations=1,
                status=dict(schema=1, choice=choice, state=state, closed=operation == "close", failed=False), result=value)


class SelectionFixtureTests(unittest.TestCase):
    def setUp(self):
        self.guards = [patch.object(smoke.subprocess, name, side_effect=AssertionError("no process"))
                       for name in ("Popen", "run")]
        self.guards += [patch.object(smoke, "HTTPServer", side_effect=AssertionError("no server"))]
        self.guards += [patch.object(smoke.os, "killpg", return_value=None)]
        for guard in self.guards: guard.start(); self.addCleanup(guard.stop)

    def test_plan_and_unfrozen_execute_never_create_output(self):
        with patch.object(smoke, "PINS", None), redirect_stdout(io.StringIO()) as output:
            smoke.main([])
            value = json.loads(output.getvalue())
            self.assertIs(value["execute"], False)
            self.assertIs(value["source_pins_complete"], False)
            self.assertEqual(value["sessions"], ["enroll", "remove", "restart"])
            self.assertEqual(value["outer_seconds"], 480)
            with self.assertRaises(ValueError): smoke.main(["--execute", "--output", "/unapproved/path"])
            with self.assertRaises(ValueError): smoke.main(["--inside"])

    def test_fixed_source_set_excludes_old_owner_and_requires_every_pin(self):
        self.assertIn("integration/filters/Owner.sys.mjs", smoke.MODULES)
        self.assertIn("integration/filters/Journal.sys.mjs", smoke.MODULES)
        self.assertTrue(all("ubo-proof" not in name for name in smoke.MODULES))
        with patch.object(smoke, "PINS", {name: "a" * 64 for name in smoke.MODULES}):
            self.assertTrue(smoke.pinned())
            smoke.PINS.pop(next(iter(smoke.PINS)))
            self.assertFalse(smoke.pinned())

    def test_closed_owner_receipts_require_actual_fresh_preserved_mutations(self):
        good = result()
        self.assertEqual(smoke.owner_result(good, "enroll"), good)
        for key, value in (("freshReload", False), ("preserved", False), ("reloadEvents", 1),
                           ("reloadEvents", 33), ("reloadEvents", True), ("selected", False), ("imported", False)):
            bad = result(); bad["result"]["receipt"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): smoke.owner_result(bad, "enroll")
        for key, value in (("schema", True), ("ok", 1), ("invalidations", True), ("phase", "observe")):
            bad = result(); bad[key] = value
            with self.assertRaises(ValueError): smoke.owner_result(bad, "enroll")
        for bad in ({"schema": 1, "ok": False, "phase": "enroll"}, {**good, "private": "no export"}, None):
            with self.assertRaises(ValueError): smoke.owner_result(bad, "enroll")

    def test_sticky_refusal_and_observation_are_distinct_from_mutation(self):
        for operation in ("enroll", "observe", "open", "close"):
            good = result(operation, "opted-out", "suspended", False)
            self.assertEqual(smoke.owner_result(good, operation), good)
        bad = result("enroll", "opted-out", "suspended", False)
        bad["result"]["attempted"] = True
        with self.assertRaises(ValueError): smoke.owner_result(bad, "enroll")
        with self.assertRaises(ValueError): smoke.owner(None, "executeScript")

    def test_campaign_requires_actual_suspend_and_resume_not_status_only(self):
        for operation, state in (("enroll", "active"), ("suspend", "suspended"), ("resume", "active")):
            good = result(operation, "eligible", state, True)
            self.assertEqual(smoke.mutation(good, operation), good)
            declined = result(operation, "eligible", state, False)
            smoke.owner_result(declined, operation)  # generic valid no-op is not campaign proof
            with self.assertRaises(ValueError): smoke.mutation(declined, operation)
        with self.assertRaises(ValueError): smoke.owner_result(result("open", "opted-out", "active", False), "open")

    def test_http_fixture_only_serves_closed_public_paths_with_bounded_counts(self):
        state = smoke.FixtureState()
        host = "127.0.0.1:18765"
        self.assertEqual(state.response(host, "/unknown")[0], 404)
        for key in ("supplement", "custom-on", "custom-off"):
            status, data, kind = state.response(host, "/" + key + ".txt")
            self.assertEqual(status, 200); self.assertEqual(kind, "text/plain")
            if key == "supplement": self.assertIn(b"/probe^$xmlhttprequest", data)
            else: self.assertNotIn(b"/probe", data)
        for case in smoke.CASES:
            status, body, _ = state.response(host, "/page/" + case)
            self.assertEqual(status, 200); self.assertLess(len(body), 1024)
            self.assertIn(b"Promise.all", body)
        for _ in range(4): state.response(host, "/probe/baseline")
        with self.assertRaises(ValueError): state.response(host, "/probe/baseline")
        with self.assertRaises(ValueError): state.response("foreign.invalid", "/supplement.txt")
        with self.assertRaises(ValueError): state.response(host, "x" * 129)

    def test_clean_environment_does_not_inherit_injection_or_sandbox_disables(self):
        with patch.dict(smoke.os.environ, {"LD_PRELOAD": "untrusted", "PYTHONPATH": "untrusted",
                                         "MOZ_DISABLE_CONTENT_SANDBOX": "1", "HTTP_PROXY": "untrusted"}):
            env = smoke.clean_environment(ROOT / "build/never-launched")
        for key in ("LD_PRELOAD", "PYTHONPATH", "MOZ_DISABLE_CONTENT_SANDBOX", "HTTP_PROXY"):
            self.assertNotIn(key, env)
        self.assertEqual(env["MOZ_DISABLE_NONLOCAL_CONNECTIONS"], "1")

    def test_source_has_single_session_owner_real_exit_and_no_storage_writer(self):
        driver = (ROOT / "scripts/smoke_filter_selection.py").read_text(); ast.parse(driver)
        fixture = (ROOT / "tests/fixtures/filter_selection.sys.mjs").read_text()
        self.assertIn("process.returncode == 0", driver)
        self.assertIn("timeout=480", driver)
        self.assertIn('addon_page(client, UBLOCK, "about.html")', driver)
        self.assertIn("if (operation === \"open\")", fixture)
        self.assertIn("check(!opened)", fixture)
        for text in (driver, fixture):
            self.assertNotIn("storage.local.set", text)
            self.assertNotIn("toOverwrite", text)
            self.assertNotIn("uboProof.enabled", text)
        self.assertNotIn("getLists", smoke.BOOTSTRAP)
        for key in ("production_default_enrollment", "startup_resume_stale_filter_barrier",
                    "authorized_network_list", "native_admission_registered", "immutable_content_verified"):
            self.assertIs(smoke.SCOPE[key], False)

    def test_new_module_layout_is_exact_private_and_immutable_between_sessions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            profile = root / "profile"; profile.mkdir(mode=0o700)
            pins = {}
            for index, name in enumerate(smoke.MODULES):
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
                data = ("// inert fixture " + str(index)).encode()
                path.write_bytes(data); path.chmod(0o600)
                pins[name] = hashlib.sha256(data).hexdigest()
            with patch.object(smoke, "ROOT", root), patch.object(smoke, "PINS", pins):
                smoke.profile_modules(profile, create=True)
                smoke.profile_modules(profile)
                with self.assertRaises(ValueError): smoke.profile_modules(profile, create=True)
                target = profile / "chrome" / smoke.PROFILE_MODULES / "Fixture.sys.mjs"
                target.chmod(0o600)
                with self.assertRaises(ValueError): smoke.profile_modules(profile)
                target.chmod(0o400)
                target.unlink(); target.symlink_to(root / "tests/fixtures/filter_selection.sys.mjs")
                with self.assertRaises(ValueError): smoke.profile_modules(profile)

    def test_cleanup_removes_only_owned_profile_names_and_retains_original_logs(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            for name in ("profile", "config", "cache", "runtime", "tmp", "appdata"):
                (work / name).mkdir(mode=0o700)
                (work / name / "synthetic").write_text("temporary")
            for phase in smoke.SESSIONS: (work / (phase + ".log")).write_text("private synthetic log")
            (work / "unrelated").write_text("retain")
            self.assertTrue(smoke.cleanup(work))
            self.assertEqual({p.name for p in work.iterdir()}, {"unrelated", *(phase + ".log" for phase in smoke.SESSIONS)})
            self.assertEqual(set(smoke.log_receipts(work)), set(smoke.SESSIONS))
            self.assertTrue(smoke.cleanup(work))

    def test_private_log_drain_caps_only_pipe_bytes_and_rejects_overflow(self):
        class Process:
            pid = 123
            stdout = io.BytesIO(b"123456789")
        output = io.BytesIO()
        with patch.object(smoke, "MAX_LOG", 8), patch.object(smoke.os, "killpg") as kill:
            drain = smoke.PrivateLogDrain(Process(), output); drain.start()
            with self.assertRaises(ValueError): drain.finish()
            self.assertFalse(drain.thread.is_alive())
            self.assertEqual(output.getvalue(), b"12345678")
            kill.assert_called_once_with(123, smoke.signal.SIGTERM)

    def test_private_log_drain_joins_clean_eof_without_signalling(self):
        class Process:
            pid = 123
            stdout = io.BytesIO(b"bounded")
        output = io.BytesIO()
        with patch.object(smoke.os, "killpg") as kill:
            drain = smoke.PrivateLogDrain(Process(), output); drain.start(); drain.finish()
            self.assertFalse(drain.thread.is_alive()); self.assertEqual(output.getvalue(), b"bounded")
            kill.assert_not_called()

    def test_private_log_drain_read_failure_is_closed_and_stops_owned_group(self):
        class Pipe:
            def read1(self, _): raise OSError("private-error-canary")
            def close(self): pass
        class Process:
            pid = 456
            stdout = Pipe()
        output = io.BytesIO()
        with patch.object(smoke.os, "killpg") as kill:
            drain = smoke.PrivateLogDrain(Process(), output); drain.start()
            with self.assertRaisesRegex(ValueError, "selection_fixture_log_cleanup"): drain.finish()
            self.assertEqual(output.getvalue(), b""); self.assertFalse(drain.thread.is_alive())
            kill.assert_called_once_with(456, smoke.signal.SIGTERM)

    def test_unjoined_drain_forces_owned_group_kill_and_never_passes(self):
        class Thread:
            joins = 0
            def join(self, timeout): self.joins += 1
            def is_alive(self): return True
        class Process: pid = 789
        drain = smoke.PrivateLogDrain(Process(), io.BytesIO()); drain.thread = Thread()
        with patch.object(smoke.os, "killpg") as kill, self.assertRaises(ValueError): drain.finish()
        self.assertEqual(drain.thread.joins, 2)
        kill.assert_called_once_with(789, smoke.signal.SIGKILL)

    def test_process_cleanup_escalates_only_owned_group_and_joins(self):
        class Process:
            pid = 321
            calls = 0
            returncode = None
            def poll(self): return self.returncode
            def wait(self, timeout):
                self.calls += 1
                if self.calls == 1: raise subprocess.TimeoutExpired("synthetic", timeout)
                self.returncode = -9
        process = Process()
        with patch.object(smoke.os, "killpg") as kill:
            smoke.stop_process(process)
            self.assertEqual(kill.call_args_list, [unittest.mock.call(321, smoke.signal.SIGTERM),
                                                   unittest.mock.call(321, smoke.signal.SIGKILL)])
            self.assertEqual(process.returncode, -9)
            smoke.stop_process(process); self.assertEqual(kill.call_count, 2)

    def test_browser_acceptance_requires_joined_integer_exit_zero(self):
        class Process:
            def wait(self, timeout): self.joined = timeout
        for code in (1, -9, None, True, False):
            process = Process(); process.returncode = code
            with self.assertRaises(ValueError): smoke.joined_success(process)
        process = Process(); process.returncode = 0; smoke.joined_success(process)
        self.assertEqual(process.joined, 20)

    def test_socket_close_error_cannot_skip_process_drain_or_module_cleanup(self):
        calls = []
        class Socket:
            def close(self): calls.append("socket"); raise OSError("synthetic close failure")
        class Client: socket = Socket()
        class Drain:
            def finish(self): calls.append("drain")
        with patch.object(smoke, "stop_process", side_effect=lambda _: calls.append("process")), \
                patch.object(smoke, "profile_modules", side_effect=lambda _: calls.append("modules")), \
                self.assertRaises(OSError):
            smoke.close_session(Client(), object(), Drain(), ROOT / "build/never-launched/profile")
        self.assertEqual(calls, ["socket", "process", "drain", "modules"])

    def test_diagnostic_operation_is_closed_and_contains_no_raw_error_or_path(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder); report = {"phase": "remove"}
            smoke.write_operation(work, report, "owner_suspend")
            self.assertEqual(smoke.read_operation(work),
                             {"schema": 1, "phase": "remove", "operation": "owner_suspend"})
            with self.assertRaises(ValueError): smoke.write_operation(work, report, "private-canary-url")
            self.assertEqual(report["operation"], "owner_suspend")
            (work / "operation.json").write_text('{"schema":1,"phase":"remove","operation":"owner_suspend","private":"canary"}')
            with self.assertRaises(ValueError): smoke.read_operation(work)


if __name__ == "__main__": unittest.main()
