#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Explicit two-process native SQLite journal proof; default is read-only plan.

No addon, admission overlay, network fetch, publication grant or default owner.
Uses the unchanged reviewed native wrapper's isolation/resource/cleanup helpers.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import signal
import sys
import time
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/smoke_filter_native.py"
HELPER_SHA = "f8bcfedcd1c8b75d34e91d95c3927b67862e84306283de4818530ddfea7c4ec0"
MODULES = {
    "Journal.sys.mjs": "2b90fa020c21c4644180e0346c23c5c7dc2cd876eb6e3dc6ad59fbe92ad95dd2",
    "Selection.sys.mjs": "1fc0f83e426ac5068a5d8c78ae9b286dd0a0d51321b8bdf7182ade1fba2ddb28",
}
FIXTURE = ROOT / "tests/fixtures/filter_journal.js"
FIXTURE_SHA = "3ad035c13514f6b376ac046db95cbed69064c26c90c15fbf9719e53ab55b14f6"
PHASES = ("store", "reopen")
CHECKS = {
    "store": ("fresh", "committed_refusal", "wrapper_close", "process_lease"),
    "reopen": ("persisted_refusal", "reset_rejected", "sticky_enrollment_refused",
               "no_actor_or_grant", "wrapper_close", "process_lease"),
}
PREFIX = b"FILTER_JOURNAL_RESULT:"
COMPLETE = b"FILTER_JOURNAL_HARNESS_COMPLETE"
PROGRESS = (b"FILTER_JOURNAL_PHASE:bootstrap_enter", b"FILTER_JOURNAL_PHASE:head_loaded",
            b"FILTER_JOURNAL_PHASE:test_execute", COMPLETE)
SCOPE = dict(kind="two-parent-process-native-sqlite-journal-resource-overlay",
             native_processes=2, shared_disposable_profile=True, synthetic_binding=True,
             compiled_journal_build=False, native_close_proven=False, original_signed_ubo=False,
             publication_owner=False, default_enrollment=False, broker_fetch=False,
             content_process=False, socket_process=False)


def require(value):
    if not value:
        raise ValueError("native_filter_journal_invalid")


def reviewed_helper():
    require(HELPER.is_file() and not HELPER.is_symlink())
    source = HELPER.read_bytes()
    require(hashlib.sha256(source).hexdigest() == HELPER_SHA)
    # Execute only these verified source bytes, never a timestamp-valid pyc;
    # even plan mode must not create bytecode or trust stale cached code.
    module = ModuleType("reviewed_filter_native")
    module.__file__ = str(HELPER)
    exec(compile(source, str(HELPER), "exec"), module.__dict__)
    return module


N = reviewed_helper()
NATIVE = N.NATIVE


def pinned():
    return all(isinstance(value, str) and len(value) == 64
               and all(char in "0123456789abcdef" for char in value)
               for value in [FIXTURE_SHA, *MODULES.values()])


def validate_inputs(*, mounted=False):
    require(pinned())
    N.fixed_file(HELPER, HELPER_SHA)
    require(NATIVE.is_dir() and NATIVE.resolve() == NATIVE)
    N.fixed_file(NATIVE / "build-result.json", N.RECEIPT_SHA)
    receipt = json.loads((NATIVE / "build-result.json").read_text())
    require(receipt["source"]["revision"] == N.REVISION and receipt["outputs"] == N.BINARIES
            and receipt["native_build_proven"] is True and receipt["system_installation"] is False
            and receipt["build_network_disabled"] is True and receipt["output"] == str(NATIVE))
    for name, sha in {**N.BINARIES, **N.SUPPORT}.items():
        N.fixed_file(NATIVE / name, sha, root=NATIVE)
    for name, sha in receipt["overlay"]["files"].items():
        N.fixed_file(NATIVE / "source" / name, sha, root=NATIVE / "source")
    for name, sha in N.ORIGINAL.items():
        N.fixed_file(NATIVE / "source" / name, sha, root=NATIVE / "source")
    for name, sha in MODULES.items():
        N.fixed_file(ROOT / "integration/filters" / name, sha)
    N.fixed_file(FIXTURE, FIXTURE_SHA)
    require(N.inventory_hash(N.inventory(NATIVE / "obj/_tests/modules", (NATIVE,)))
            == N.TESTING_INVENTORY_SHA)
    modules = NATIVE / "obj/dist/bin/modules"
    # Original WebRequest remains untouched; this is not admission-hook proof.
    N.fixed_file(modules / "WebRequest.sys.mjs", N.ORIGINAL[N.WEB], root=NATIVE)
    if mounted:
        for name, sha in MODULES.items():
            N.fixed_file(modules / "volparossa-journal" / name, sha)
    else:
        require(not (modules / "volparossa-journal").exists())
        require(N.inventory_hash(N.inventory(modules, (NATIVE,))) == N.RESOURCE_INVENTORY_SHA)
    return dict(build_receipt_sha256=N.RECEIPT_SHA, upstream_revision=N.REVISION,
                runtime_sha256=N.BINARIES, support_sha256=N.SUPPORT, original_sha256=N.ORIGINAL,
                module_sha256=MODULES, fixture_sha256=FIXTURE_SHA, helper_sha256=HELPER_SHA,
                driver_sha256=N.digest(Path(__file__)),
                original_modules_inventory_sha256=N.RESOURCE_INVENTORY_SHA,
                testing_modules_inventory_sha256=N.TESTING_INVENTORY_SHA)


def stage_modules(work, before):
    target = work / "modules"
    require(not target.exists() and not target.is_symlink())
    shutil.copytree(NATIVE / "obj/dist/bin/modules", target, symlinks=True)
    require(N.inventory(target, (NATIVE, target)) == before)
    (target / "volparossa-journal").mkdir(mode=0o700)
    expected = dict(before)
    expected["volparossa-journal"] = dict(kind="directory")
    for name, sha in MODULES.items():
        path = target / "volparossa-journal" / name
        N.write_new(path, (ROOT / "integration/filters" / name).read_bytes())
        expected["volparossa-journal/" + name] = dict(kind="file", size=path.stat().st_size, sha256=sha)
    after = N.inventory(target, (NATIVE, target))
    require(after == expected)
    return N.inventory_hash(after)


def expected_result(phase):
    require(phase in PHASES)
    return dict(version=1, kind="parent-only-native-sqlite-journal", phase=phase,
                synthetic_binding=True, original_ubo=False, native_close_proven=False,
                default_enrollment=False, checks={key: True for key in CHECKS[phase]})


def parse_result(data, phase):
    require(isinstance(data, bytes) and len(data) <= N.MAX_LOG)
    lines = data.splitlines()
    matches = [line[len(PREFIX):] for line in lines if line.startswith(PREFIX)]
    require(len(matches) == 1 and len(matches[0]) <= 4096)
    require([line for line in lines if line.startswith(b"FILTER_JOURNAL_PHASE:")
             or line == COMPLETE] == list(PROGRESS))
    require(lines.index(PROGRESS[2]) < next(index for index, line in enumerate(lines)
            if line.startswith(PREFIX)) < lines.index(COMPLETE))
    value = json.loads(matches[0])
    require(N.encoded(value) == N.encoded(expected_result(phase))
            and json.dumps(value, separators=(",", ":")).encode("ascii") == matches[0])
    compact = data.replace(b" ", b"")
    require(all(tag not in compact for tag in (b'"status":"FAIL"', b'"status":"ERROR"',
            b'"level":"ERROR"', b"TEST-UNEXPECTED-")))
    return value


def read_private(path, limit=N.MAX_LOG):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= limit)
    return path.read_bytes()


def bootstrap(work, phase):
    require(phase in PHASES)
    definitions = dict(_HEAD_JS_PATH=str(NATIVE / "source/testing/xpcshell/head.js"),
                       _MOZINFO_JS_PATH=str(NATIVE / "obj/mozinfo.json"),
                       _PREFS_FILE=str(work / "user.js"),
                       _TESTING_MODULES_DIR=str(NATIVE / "obj/_tests/modules") + "/",
                       _HEAD_FILES=[], _JSDEBUGGER_PORT=0, _TEST_FILE=[str(FIXTURE)],
                       _TEST_NAME="filter-journal-" + phase, _EXPECTED="pass",
                       _FILTER_JOURNAL_PHASE=phase)
    source = "\n".join("const " + key + " = " + json.dumps(value) + ";" for key, value in definitions.items())
    return ('print("FILTER_JOURNAL_PHASE:bootstrap_enter");\n' + source
        + '\nload(_HEAD_JS_PATH);\nprint("FILTER_JOURNAL_PHASE:head_loaded");\n'
        'print("FILTER_JOURNAL_PHASE:test_execute");\n_execute_test();\n'
        'print("FILTER_JOURNAL_HARNESS_COMPLETE");\nquit(0);\n').encode("ascii")


def sandbox_command(work, namespaces):
    task_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    require(task_home.parent == Path("/home") and task_home.resolve() == task_home)
    return ["/usr/bin/bwrap", "--die-with-parent", "--new-session", "--unshare-user",
            "--unshare-pid", "--unshare-net", "--ro-bind", "/", "/",
            "--tmpfs", str(task_home), "--remount-ro", str(task_home),
            "--bind", str(work), str(work), "--ro-bind", str(work / "modules"), str(work / "modules"),
            "--ro-bind", str(work / "modules"), str(NATIVE / "obj/dist/bin/modules"),
            "--tmpfs", "/tmp", "--tmpfs", "/run", "--proc", "/proc", "--dev", "/dev",
            "--chdir", str(work), "--", "/usr/bin/python3", "-B", str(Path(__file__).resolve()),
            "--output", str(work), "--execute", "--inside", "--parent-namespaces", N.encoded(namespaces).decode()]


def inside(work, namespaces):
    N.validate_isolation(work, namespaces)
    inputs = validate_inputs(mounted=True)
    require(json.loads(read_private(work / "inputs.json", 16384)) == inputs)
    mounted = N.inventory(NATIVE / "obj/dist/bin/modules", (NATIVE,))
    require(N.inventory_hash(mounted) == read_private(work / "overlay.sha256", 65).decode().strip())
    require(read_private(work / "user.js", 128) == b"// No security preference overrides.\n")
    results = {}
    # One 90s acceptance deadline across both launches and joins. The unchanged
    # helper's individual execution timer begins after Popen; do not interrupt
    # its constructor with signals or claim a hard launch-time kill deadline.
    # The unchanged outer helper supplies its separate 150s execution budget.
    deadline = time.monotonic() + N.NATIVE_SECONDS
    for phase in PHASES:
        script = work / ("bootstrap-" + phase + ".js")
        require(read_private(script, 8192) == bootstrap(work, phase))
        binary = NATIVE / "obj/dist/bin"
        command = [str(binary / "xpcshell"), "-g", str(binary), "-a", str(binary / "browser"),
                   "-m", "-f", str(script)]
        remaining = deadline - time.monotonic()
        require(remaining > 0)
        code = N.run_bounded(command, work / (phase + ".log"), work, remaining)
        N.write_new(work / (phase + "-exit.json"), N.encoded(dict(phase=phase, exit_status=code)) + b"\n")
        require(code == 0 and time.monotonic() < deadline)
        results[phase] = parse_result(read_private(work / (phase + ".log")), phase)
    require(time.monotonic() < deadline)
    require(validate_inputs(mounted=True) == inputs)
    N.validate_isolation(work, namespaces)
    require(N.inventory(NATIVE / "obj/dist/bin/modules", (NATIVE,)) == mounted)
    N.write_new(work / "native-result.json", N.encoded(results) + b"\n")
    return 0


def execute(work):
    inputs = validate_inputs()
    before = N.inventory(NATIVE / "obj/dist/bin/modules", (NATIVE,))
    snapshot = N.host_snapshot()
    namespaces = {key: os.readlink("/proc/self/ns/" + key) for key in ("net", "pid", "user")}
    N.checked_output(work, fresh=True).mkdir(mode=0o700)
    report = dict(schema=1, passed=False, scope=SCOPE, inputs=inputs, phases={},
                  native_processes_completed=False, cleanup=False, original_runtime_unchanged=False,
                  host_snapshot_unchanged=False, failure="fixture_failed")
    try:
        for name in ("profile", "tmp", "cache"):
            (work / name).mkdir(mode=0o700)
        overlay = stage_modules(work, before)
        report["overlay_modules_inventory_sha256"] = overlay
        N.write_new(work / "overlay.sha256", (overlay + "\n").encode())
        N.write_new(work / "inputs.json", N.encoded(inputs) + b"\n")
        for phase in PHASES:
            N.write_new(work / ("bootstrap-" + phase + ".js"), bootstrap(work, phase))
        N.write_new(work / "user.js", b"// No security preference overrides.\n")
        code = N.run_bounded(sandbox_command(work, namespaces), work / "sandbox.log", work, N.OUTER_SECONDS)
        require(code == 0)
        results = {}
        for phase in PHASES:
            results[phase] = parse_result(read_private(work / (phase + ".log")), phase)
            require(read_private(work / (phase + "-exit.json"), 128)
                    == N.encoded(dict(phase=phase, exit_status=0)) + b"\n")
        require(read_private(work / "native-result.json", 8192) == N.encoded(results) + b"\n")
        report["result"] = results
        report["native_processes_completed"] = True
    except (Exception, KeyboardInterrupt):
        pass  # Original private logs retained; no raw exception/path export.
    finally:
        try:
            N.remove_private_state(work)
            report["cleanup"] = True
        except (Exception, KeyboardInterrupt):
            pass
        try:
            report["original_runtime_unchanged"] = (validate_inputs() == inputs
                and N.inventory(NATIVE / "obj/dist/bin/modules", (NATIVE,)) == before)
            report["host_snapshot_unchanged"] = N.host_snapshot() == snapshot
        except (Exception, KeyboardInterrupt):
            pass
        for phase in PHASES:
            observed = dict(exit_status=None, semantic_result=False)
            try:
                value = json.loads(read_private(work / (phase + "-exit.json"), 128))
                require(set(value) == {"phase", "exit_status"} and value["phase"] == phase
                        and type(value["exit_status"]) is int and -128 <= value["exit_status"] <= 255)
                observed["exit_status"] = value["exit_status"]
                parse_result(read_private(work / (phase + ".log")), phase)
                observed["semantic_result"] = True
            except (Exception, KeyboardInterrupt):
                pass
            report["phases"][phase] = observed
        report["passed"] = all(report[key] is True for key in
            ("native_processes_completed", "cleanup", "original_runtime_unchanged", "host_snapshot_unchanged"))
        report["failure"] = None if report["passed"] else "fixture_failed"
        report["logs"] = {name: dict(sha256=N.digest(work / name), bytes=(work / name).stat().st_size)
                          for name in ("sandbox.log", "store.log", "reopen.log")
                          if (work / name).is_file() and not (work / name).is_symlink()}
        N.write_new(work / "report.json", N.encoded(report) + b"\n")
    return 0 if report["passed"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--parent-namespaces", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.execute:
        require(not args.inside and args.parent_namespaces is None)
        print(N.encoded(dict(schema=1, execute=False, source_pins_complete=pinned(), scope=SCOPE,
                            combined_native_acceptance_seconds=N.NATIVE_SECONDS,
                            outer_execution_seconds=N.OUTER_SECONDS)).decode())
        return 0
    require(pinned() and args.output is not None)
    os.umask(0o077)
    def interrupted(_signum, _frame):
        raise InterruptedError("journal_fixture_interrupted")
    signal.signal(signal.SIGTERM, interrupted)
    if args.inside:
        return inside(N.checked_output(args.output, fresh=False), json.loads(args.parent_namespaces))
    require(args.parent_namespaces is None)
    return execute(args.output)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt):
        print("native_filter_journal_failed", file=sys.stderr)
        sys.exit(1)
