# SPDX-License-Identifier: GPL-3.0-only
"""Inert journal-driver checks. No Firefox, namespace or network is started."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import smoke_filter_journal as smoke


def result_log(phase, value=None):
    result = smoke.expected_result(phase) if value is None else value
    return (b"\n".join(smoke.PROGRESS[:3]) + b"\n" + smoke.PREFIX
            + json.dumps(result, separators=(",", ":")).encode("ascii")
            + b"\n" + smoke.COMPLETE + b"\n")


class JournalDriverTests(unittest.TestCase):
    def setUp(self):
        self.guard = patch.object(smoke.N.subprocess, "Popen", side_effect=AssertionError("no native launch"))
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def test_plan_is_read_only_and_unfrozen_execute_refuses(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "absent"
            with redirect_stdout(io.StringIO()) as printed:
                self.assertEqual(smoke.main(["--output", str(output)]), 0)
            plan = json.loads(printed.getvalue())
            self.assertIs(plan["execute"], False)
            self.assertEqual(plan["combined_native_acceptance_seconds"], 90)
            self.assertEqual(plan["outer_execution_seconds"], 150)
            self.assertFalse(output.exists())
            with patch.object(smoke, "FIXTURE_SHA", None), self.assertRaises(ValueError):
                smoke.main(["--output", str(output), "--execute"])
            self.assertFalse(output.exists())
            with self.assertRaises(ValueError):
                smoke.main(["--inside"])

    def test_closed_phase_results_require_all_checks_and_harness_completion(self):
        for phase in smoke.PHASES:
            good = result_log(phase)
            self.assertEqual(smoke.parse_result(good, phase), smoke.expected_result(phase))
            bad = [good + good, good.replace(smoke.COMPLETE, b""),
                   smoke.COMPLETE + b"\n" + good,
                   good.replace(b'"version":1', b'"version":true'),
                   good.replace(b'"version":1', b'"version":1,"version":1'),
                   good.replace(b'"version":1', b'"version": 1'),
                   good + b'{"action":"test_status","status":"FAIL"}\n',
                   good + b'{"action":"log","level": "ERROR"}\n',
                   good + b'TEST-UNEXPECTED-FAIL\n',
                   result_log("reopen" if phase == "store" else "store")]
            for name in smoke.CHECKS[phase]:
                result = smoke.expected_result(phase)
                result["checks"][name] = False
                bad.append(result_log(phase, result))
            result = smoke.expected_result(phase)
            result["extra"] = "unexpected"
            bad.append(result_log(phase, result))
            for data in bad:
                with self.subTest(phase=phase, data=data[:80]), self.assertRaises(ValueError):
                    smoke.parse_result(data, phase)
        with self.assertRaises(ValueError):
            smoke.parse_result(result_log("store"), "arbitrary")
        with patch.object(smoke.N, "MAX_LOG", 8), self.assertRaises(ValueError):
            smoke.parse_result(result_log("store"), "store")

    def test_fixed_modules_and_helper_are_bound_without_runtime_validation(self):
        self.assertEqual(smoke.N.digest(smoke.HELPER), smoke.HELPER_SHA)
        self.assertEqual(set(smoke.MODULES), {"Journal.sys.mjs", "Selection.sys.mjs"})
        for name, digest in smoke.MODULES.items():
            self.assertEqual(smoke.N.digest(ROOT / "integration/filters" / name), digest)
        if smoke.FIXTURE_SHA is not None:
            self.assertEqual(smoke.N.digest(smoke.FIXTURE), smoke.FIXTURE_SHA)
        self.assertEqual(smoke.SCOPE["native_processes"], 2)
        for name in ("compiled_journal_build", "native_close_proven", "original_signed_ubo",
                     "publication_owner", "default_enrollment", "broker_fetch"):
            self.assertIs(smoke.SCOPE[name], False)

    def test_bootstraps_use_two_real_process_phases_not_module_reloading(self):
        work = ROOT / "build/filter-journal-test-only"
        for phase in smoke.PHASES:
            data = smoke.bootstrap(work, phase)
            self.assertIn(('const _FILTER_JOURNAL_PHASE = "' + phase + '";').encode(), data)
            self.assertIn(str(smoke.FIXTURE).encode(), data)
            self.assertIn(b"_execute_test();", data)
            self.assertIn(smoke.COMPLETE, data)
            self.assertNotIn(b"_TEST_CWD", data)
            self.assertNotIn(b"unload", data)
        with self.assertRaises(ValueError):
            smoke.bootstrap(work, "other")

    def test_sandbox_retains_reviewed_namespaces_readonly_mounts_and_private_environment(self):
        work = ROOT / "build/filter-journal-test-only"
        command = smoke.sandbox_command(work, {"net": "1", "pid": "2", "user": "3"})
        self.assertEqual(command[0], "/usr/bin/bwrap")
        for flag in ("--die-with-parent", "--new-session", "--unshare-user", "--unshare-pid", "--unshare-net"):
            self.assertEqual(command.count(flag), 1)
        self.assertIn(str(ROOT / "scripts/smoke_filter_journal.py"), command)
        self.assertNotIn(str(smoke.HELPER), command)
        self.assertIn(["--ro-bind", "/", "/"], [command[i:i + 3] for i in range(len(command) - 2)])
        environment = smoke.N.clean_environment(work)
        self.assertEqual(environment["XPCSHELL_TEST_PROFILE_DIR"], str(work / "profile"))
        self.assertEqual(environment["MOZ_DISABLE_NONLOCAL_CONNECTIONS"], "1")
        self.assertEqual(environment["MOZ_DISABLE_SOCKET_PROCESS"], "1")
        self.assertFalse(any("SANDBOX" in key for key in environment))

    def test_private_log_rejects_symlinks_and_oversize(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "log"
            log.write_bytes(result_log("store"))
            self.assertEqual(smoke.read_private(log), result_log("store"))
            with self.assertRaises(ValueError):
                smoke.read_private(log, 8)
            alias = Path(folder) / "alias"
            alias.symlink_to(log)
            with self.assertRaises(ValueError):
                smoke.read_private(alias)


if __name__ == "__main__":
    unittest.main()
