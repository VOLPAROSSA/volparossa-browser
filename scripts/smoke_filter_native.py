#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Explicit, disposable parent-only native WebRequest resource-overlay proof.

No build, download, signed addon, publisher owner, broker or default enrollment.
Only --execute can launch the retained xpcshell. Do not use the generic Mozilla
runner: it disables the content sandbox and can start additional test servers.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import resource
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT.parents[0] / "codex-network-gateway/build/native-firefox-157"
STAGE = ROOT / "build/filter-native-source-02"
WEB = "toolkit/components/extensions/webrequest/WebRequest.sys.mjs"
MOZ_BUILD = "toolkit/components/extensions/webrequest/moz.build"
REVISION = "47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1"
RECEIPT_SHA = "4be952653fe30d9516dde25c4ccd1cf870a6ab87514f8ba6a0961a7f4d62485d"
PATCH_SHA = "a375f318ef7965bb4b7c3629ef366354c89b2630a359493af4406b5c50774cd2"
ORIGINAL = {
    WEB: "a31dacd540c7335d8f703539de34b4f5eb5531a0e2eccf3bcb748bdcf2136e35",
    MOZ_BUILD: "ae2e67c348a4cea54762a58db281da7b313881ed04bd90cb9215804f08dceaaa",
}
PATCHED = {
    WEB: "a8a4527629508fd3f1dff70d7361a5fa028d92eb67011d8e6ddfefdebae8532d",
    MOZ_BUILD: "921d8561ab389c4b81b2acede86bf4eddbd07b233ea08dfa4f9268ca0f9304cf",
}
MODULES = {
    "Admission.sys.mjs": "ea1a170d32d5ca04c1edd1eefc7e896c3faec5b8ab0917373652a914710f37f4",
    "WebRequestAdmission.sys.mjs": "9dffa0205d88a393c944ad4d6969c30b09a066577c19eb439cad2d8e6e6917bf",
}
BINARIES = {
    "obj/dist/bin/xpcshell": "43de7531b38ba73f64acdb07449e89ac500041ef883c1bc6a8a262adae822f19",
    "obj/dist/bin/firefox": "6c28d9bdb250f0ead4ebcb18fbe44f50aecfe4c3b4c60e972f925f929f54dfb1",
    "obj/dist/bin/libxul.so": "a49a8c7fc85b61d1f14e0c4107153f00af56a03317f3b4031db5da642bfb7972",
}
SUPPORT = {
    "source/testing/xpcshell/head.js": "7183adfdba72b4d8508208215ddddc6573f0105f1b5a6ebeb3bc5ad11a13b81e",
    "obj/mozinfo.json": "e49d3e9055833bf4beab36ccee7d5f29af2899c64d7b674f35741933c3cfdc01",
    "source/netwerk/test/httpserver/httpd.sys.mjs": "410c595cbdbd1c45b26988500bcd92ffd9ff0622157e9ee34b87f4d6813ad8e3",
}
FIXTURE_SHA = "90b5bcd49333e3a33f59969e1110125cc69b92be2feb6fc14a4c8b7133597219"
FIXTURE = ROOT / "tests/fixtures/filter_native.js"
RESOURCE_INVENTORY_SHA = "2101ff07791612d49e0157e5a68623af5bb3adf5a5d7d21125b6a4fdcef9e8ad"
TESTING_INVENTORY_SHA = "0fa59e0601b3219d7de98c53658eb2aad898d27cf8c1c0a0e034bddaa46edc41"
MAX_LOG = 4 * 1024 * 1024
NATIVE_SECONDS = 90
OUTER_SECONDS = 150
PREFIX = b"FILTER_NATIVE_RESULT:"
COMPLETE = b"FILTER_NATIVE_HARNESS_COMPLETE"
PHASE_PREFIX = b"FILTER_NATIVE_PHASE:"
PHASES = ("bootstrap_enter", "head_loaded", "test_execute")
SCOPE = dict(kind="parent-only-native-resource-overlay", compiled_admission_build=False,
             original_signed_ubo=False, publication_owner=False, broker_fetch=False,
             default_enrollment=False, real_os_suspend=False, content_process=False, socket_process=False,
             controlled_exception_tests_included=False)
CHECKS = ("http_baseline", "startup_waits", "valid_null_once", "expiry_aborts", "invalidation_aborts",
          "unregistered_unchanged", "cleanup_complete")


def require(value):
    if not value:
        raise ValueError("native_filter_fixture_invalid")


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for data in iter(lambda: source.read(1024 * 1024), b""):
            value.update(data)
    return value.hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def write_new(path, data):
    # Exclusive creation even inside the fresh private output directory.
    with path.open("xb") as target:
        os.chmod(path, 0o600)
        target.write(data)


def fixed_file(path, expected, *, root=None):
    require(path.is_file())
    if root is not None:
        require(path.resolve(strict=True).is_relative_to(root))
    else:
        require(not path.is_symlink())
    require(digest(path) == expected)


def inventory(folder, allowed):
    """Bounded full resource tree; retain symlinks, never follow directory links."""
    require(folder.is_dir() and not folder.is_symlink())
    entries = {}
    total = 0
    for current, directories, files in os.walk(folder, followlinks=False):
        for name in sorted(directories + files):
            path = Path(current) / name
            key = path.relative_to(folder).as_posix()
            mode = path.lstat().st_mode
            require(len(entries) < 4096 and len(key) <= 512)
            if stat.S_ISDIR(mode):
                entries[key] = dict(kind="directory")
                continue
            require(stat.S_ISREG(mode) or stat.S_ISLNK(mode))
            resolved = path.resolve(strict=True)
            require(resolved.is_file() and any(resolved.is_relative_to(root) for root in allowed))
            size = resolved.stat().st_size
            total += size
            require(size <= 8 * 1024 * 1024 and total <= 64 * 1024 * 1024)
            entries[key] = dict(kind="symlink" if path.is_symlink() else "file",
                                size=size, sha256=digest(path))
            if path.is_symlink():
                entries[key]["target"] = os.readlink(path)
    return entries


def inventory_hash(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def validate_inputs(*, mounted=False):
    require(NATIVE.is_dir() and NATIVE.resolve() == NATIVE)
    require(STAGE.is_dir() and STAGE.resolve() == STAGE)
    fixed_file(NATIVE / "build-result.json", RECEIPT_SHA)
    receipt = json.loads((NATIVE / "build-result.json").read_text())
    require(receipt["source"]["revision"] == REVISION and receipt["outputs"] == BINARIES
            and receipt["native_build_proven"] is True and receipt["system_installation"] is False
            and receipt["build_network_disabled"] is True and receipt["output"] == str(NATIVE))
    for name, sha in {**BINARIES, **SUPPORT}.items():
        fixed_file(NATIVE / name, sha, root=NATIVE)
    for name, sha in receipt["overlay"]["files"].items():
        fixed_file(NATIVE / "source" / name, sha, root=NATIVE / "source")
    for name, sha in ORIGINAL.items():
        fixed_file(NATIVE / "source" / name, sha, root=NATIVE / "source")
    fixed_file(ROOT / "patches/0003-filter-admission.patch", PATCH_SHA)
    for name, sha in PATCHED.items():
        fixed_file(STAGE / "patched" / name, sha)
    for name, sha in MODULES.items():
        fixed_file(ROOT / "integration/filters" / name, sha)
        fixed_file(STAGE / "patched" / Path(WEB).parent / "volparossa" / name, sha)
    fixed_file(FIXTURE, FIXTURE_SHA)
    record = json.loads((STAGE / "source.json").read_text())
    require(record == dict(upstream_revision=REVISION, upstream_sha256=ORIGINAL,
                           kind="exact-source-overlay-not-a-firefox-build", patch_sha256=PATCH_SHA,
                           patched_sha256=PATCHED, module_sha256=MODULES,
                           native_build_proven=False, default_enrollment=False,
                           publication_owner_integrated=False))
    modules = NATIVE / "obj/dist/bin/modules"
    require(inventory_hash(inventory(NATIVE / "obj/_tests/modules", (NATIVE,))) == TESTING_INVENTORY_SHA)
    fixed_file(modules / "WebRequest.sys.mjs", PATCHED[WEB] if mounted else ORIGINAL[WEB],
               root=modules if mounted else NATIVE)
    if mounted:
        for name, sha in MODULES.items():
            fixed_file(modules / "volparossa" / name, sha)
    else:
        require(not (modules / "volparossa").exists())
        require(inventory_hash(inventory(modules, (NATIVE,))) == RESOURCE_INVENTORY_SHA)
    return dict(build_receipt_sha256=RECEIPT_SHA, upstream_revision=REVISION,
                runtime_sha256=BINARIES, support_sha256=SUPPORT, original_sha256=ORIGINAL,
                patched_sha256=PATCHED, module_sha256=MODULES, patch_sha256=PATCH_SHA,
                fixture_sha256=FIXTURE_SHA, driver_sha256=digest(Path(__file__)),
                original_modules_inventory_sha256=RESOURCE_INVENTORY_SHA,
                testing_modules_inventory_sha256=TESTING_INVENTORY_SHA)


def stage_modules(work, before):
    original = NATIVE / "obj/dist/bin/modules"
    target = work / "modules"
    require(not target.exists() and not target.is_symlink())
    shutil.copytree(original, target, symlinks=True)
    require(inventory(target, (NATIVE, target)) == before)
    # Remove only the copied symlink/entry, NEVER its original target.
    (target / "WebRequest.sys.mjs").unlink()
    write_new(target / "WebRequest.sys.mjs", (STAGE / "patched" / WEB).read_bytes())
    (target / "volparossa").mkdir(mode=0o700)
    for name in MODULES:
        write_new(target / "volparossa" / name, (ROOT / "integration/filters" / name).read_bytes())
    after = inventory(target, (NATIVE, target))
    expected = dict(before)
    expected["WebRequest.sys.mjs"] = dict(kind="file", size=(target / "WebRequest.sys.mjs").stat().st_size,
                                         sha256=PATCHED[WEB])
    expected["volparossa"] = dict(kind="directory")
    for name, sha in MODULES.items():
        expected["volparossa/" + name] = dict(kind="file", size=(target / "volparossa" / name).stat().st_size,
                                            sha256=sha)
    require(after == expected)
    return inventory_hash(after)


def result_from_log(path):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_LOG)
    data = path.read_bytes()
    matches = [line[len(PREFIX):] for line in data.splitlines() if line.startswith(PREFIX)]
    require(len(matches) == 1 and len(matches[0]) <= 4096)
    require(data.splitlines().count(COMPLETE) == 1
            and data.index(COMPLETE) > data.index(PREFIX))
    require(progress_from_log(path) == "harness_complete")
    value = json.loads(matches[0])
    expected = dict(version=1, kind="parent-only-native-webrequest", synthetic_policy=True,
                    process_clock_simulation=True, original_ubo=False, default_enrollment=False,
                    checks={name: True for name in CHECKS})
    # Canonical re-encoding enforces types (False is not integer 0), while
    # duplicate keys / alternate whitespace in the original line are rejected.
    require(encoded(value) == encoded(expected)
            and json.dumps(value, separators=(",", ":")).encode("ascii") == matches[0])
    # xpcshell may exit zero after an assertion failure. Never infer pass just
    # from the fixture line if the enclosing standard harness reports failure.
    require(b'"status":"FAIL"' not in data.replace(b" ", b"")
            and b'"status":"ERROR"' not in data.replace(b" ", b"")
            and b'"level":"ERROR"' not in data.replace(b" ", b"")
            and b"TEST-UNEXPECTED-" not in data)
    return value


def progress_from_log(path):
    """Closed bootstrap observations, not a diagnosis or a partial test pass."""
    if not path.exists():
        return "not_observed"
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_LOG)
    phase = "not_observed"
    expected = [PHASE_PREFIX + value.encode("ascii") for value in PHASES] + [COMPLETE]
    observed = 0
    for line in path.read_bytes().splitlines():
        if line.startswith(PHASE_PREFIX) or line == COMPLETE:
            if observed >= len(expected) or line != expected[observed]:
                return "invalid_progress"
            phase = PHASES[observed] if observed < len(PHASES) else "harness_complete"
            observed += 1
    return phase


def checked_output(path, *, fresh):
    require(path.is_absolute() and path.parent == ROOT / "build" and path.name not in ("", ".", ".."))
    require(path.parent.is_dir() and path.parent.resolve() == path.parent)
    require(path.resolve() == path and not path.is_symlink() and path != STAGE)
    if fresh:
        require(not path.exists())
    else:
        info = path.stat()
        require(path.is_dir() and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
    return path


def host_snapshot():
    return dict(netns=os.readlink("/proc/self/ns/net"),
                dns=digest(Path("/etc/resolv.conf")), routes=digest(Path("/proc/net/route")),
                routes6=digest(Path("/proc/net/ipv6_route")))


def clean_environment(work):
    # Match upstream xpcshell's parent-only Necko selection. This avoids a
    # separate socket process; it does NOT disable any process sandbox.
    return dict(PATH="/usr/bin:/bin", HOME=pwd.getpwuid(os.getuid()).pw_dir,
                LANG="C.UTF-8", LC_ALL="C.UTF-8", TMPDIR=str(work / "tmp"),
                XPCSHELL_TEST_PROFILE_DIR=str(work / "profile"),
                XPCSHELL_TEST_TEMP_DIR=str(work / "tmp"),
                MOZ_STARTUP_CACHE=str(work / "cache/startupCache"),
                XDG_CACHE_HOME=str(work / "cache"),
                MOZ_CRASHREPORTER_DISABLE="1", MOZ_CRASHREPORTER_NO_REPORT="1",
                MOZ_DISABLE_NONLOCAL_CONNECTIONS="1", MOZ_DISABLE_SOCKET_PROCESS="1",
                LD_LIBRARY_PATH=str(NATIVE / "obj/dist/bin"))


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
            "--output", str(work), "--execute", "--inside", "--parent-namespaces", encoded(namespaces).decode()]


def validate_isolation(work, namespaces):
    require(set(namespaces) == {"net", "pid", "user"})
    require(all(os.readlink("/proc/self/ns/" + key) != value for key, value in namespaces.items()))
    require(sorted(name for _, name in socket.if_nameindex()) == ["lo"])
    for path in (Path("/"), ROOT, NATIVE, Path(pwd.getpwuid(os.getuid()).pw_dir),
                 work / "modules", NATIVE / "obj/dist/bin/modules"):
        require(os.statvfs(path).f_flag & os.ST_RDONLY)
    require(not (os.statvfs(work).f_flag & os.ST_RDONLY))
    checked_output(work, fresh=False)
    require(not any(Path(pwd.getpwuid(os.getuid()).pw_dir).iterdir()))


def bootstrap(work):
    definitions = dict(_HEAD_JS_PATH=str(NATIVE / "source/testing/xpcshell/head.js"),
                       _MOZINFO_JS_PATH=str(NATIVE / "obj/mozinfo.json"),
                       _PREFS_FILE=str(work / "user.js"),
                       _TESTING_MODULES_DIR=str(NATIVE / "obj/_tests/modules") + "/",
                       _HEAD_FILES=[], _JSDEBUGGER_PORT=0, _TEST_FILE=[str(FIXTURE)],
                       _TEST_NAME="filter-native-parent-only", _EXPECTED="pass")
    source = "\n".join("const " + key + " = " + json.dumps(value) + ";" for key, value in definitions.items())
    # Standard upstream head only: no extension test head or broad pref changes.
    return ('print("FILTER_NATIVE_PHASE:bootstrap_enter");\n' + source
            + '\nload(_HEAD_JS_PATH);\nprint("FILTER_NATIVE_PHASE:head_loaded");\n'
            'print("FILTER_NATIVE_PHASE:test_execute");\n_execute_test();\n'
            'print("FILTER_NATIVE_HARNESS_COMPLETE");\nquit(0);\n').encode("ascii")


def child_limits():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_LOG, MAX_LOG))
    resource.setrlimit(resource.RLIMIT_NOFILE, (512, 512))
    resource.setrlimit(resource.RLIMIT_AS, (16 * 1024**3, 16 * 1024**3))
    resource.setrlimit(resource.RLIMIT_CPU, (100, 105))


def run_bounded(command, log, work, seconds):
    process = None
    with log.open("xb") as output:
        os.chmod(log, 0o600)
        try:
            process = subprocess.Popen(command, cwd=work, env=clean_environment(work),
                                       stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                       start_new_session=True, preexec_fn=child_limits)
            deadline = time.monotonic() + seconds
            while process.poll() is None:
                require(time.monotonic() < deadline and log.stat().st_size <= MAX_LOG)
                time.sleep(0.05)
            return process.returncode
        finally:
            if process is not None and process.poll() is None:
                # A PID namespace reaps any grandchildren on bwrap termination.
                # The separate group also covers an unfinished direct xpcshell.
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        os.killpg(process.pid, sig)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        continue
                require(process.poll() is not None)


def remove_private_state(work):
    checked_output(work, fresh=False)
    # Only these freshly created child directories, never caller-selected paths.
    for name in ("profile", "tmp", "cache"):
        path = work / name
        require(not path.is_symlink() and path.parent == work)
        if path.exists():
            require(path.is_dir() and path.stat().st_uid == os.getuid())
            shutil.rmtree(path)


def inside(work, namespaces):
    validate_isolation(work, namespaces)
    inputs = validate_inputs(mounted=True)
    require(json.loads((work / "inputs.json").read_text()) == inputs)
    mounted = inventory(NATIVE / "obj/dist/bin/modules", (NATIVE,))
    require(inventory_hash(mounted) == (work / "overlay.sha256").read_text().strip())
    require((work / "bootstrap.js").read_bytes() == bootstrap(work)
            and (work / "user.js").read_bytes() == b"// No security preference overrides.\n")
    binary = NATIVE / "obj/dist/bin"
    command = [str(binary / "xpcshell"), "-g", str(binary), "-a", str(binary / "browser"),
               "-m", "-f", str(work / "bootstrap.js")]
    code = run_bounded(command, work / "xpcshell.log", work, NATIVE_SECONDS)
    require(code == 0)
    result = result_from_log(work / "xpcshell.log")
    require(validate_inputs(mounted=True) == inputs)
    validate_isolation(work, namespaces)
    require(inventory(NATIVE / "obj/dist/bin/modules", (NATIVE,)) == mounted)
    write_new(work / "native-result.json", encoded(result) + b"\n")
    return 0


def execute(work):
    inputs = validate_inputs()
    before = inventory(NATIVE / "obj/dist/bin/modules", (NATIVE,))
    snapshot = host_snapshot()
    namespaces = {key: os.readlink("/proc/self/ns/" + key) for key in ("net", "pid", "user")}
    checked_output(work, fresh=True).mkdir(mode=0o700)
    report = dict(schema=1, passed=False, scope=SCOPE, inputs=inputs,
                  source_modules_inventory_sha256=inventory_hash(before), native_process_completed=False,
                  cleanup=False, original_runtime_unchanged=False, host_snapshot_unchanged=False,
                  bootstrap_phase="not_observed", failure="fixture_failed")
    try:
        for name in ("profile", "tmp", "cache"):
            (work / name).mkdir(mode=0o700)
        overlay = stage_modules(work, before)
        report["overlay_modules_inventory_sha256"] = overlay
        write_new(work / "overlay.sha256", (overlay + "\n").encode())
        write_new(work / "inputs.json", encoded(inputs) + b"\n")
        write_new(work / "bootstrap.js", bootstrap(work))
        write_new(work / "user.js", b"// No security preference overrides.\n")
        code = run_bounded(sandbox_command(work, namespaces), work / "sandbox.log", work, OUTER_SECONDS)
        require(code == 0)
        report["result"] = result_from_log(work / "xpcshell.log")
        require(json.loads((work / "native-result.json").read_text()) == report["result"])
        report["native_process_completed"] = True
    except (Exception, KeyboardInterrupt):
        # Original bounded logs remain private evidence; no raw exception export.
        pass
    finally:
        try:
            remove_private_state(work)
            report["cleanup"] = True
        except (Exception, KeyboardInterrupt):
            pass
        try:
            report["original_runtime_unchanged"] = (validate_inputs() == inputs
                and inventory(NATIVE / "obj/dist/bin/modules", (NATIVE,)) == before)
            report["host_snapshot_unchanged"] = host_snapshot() == snapshot
        except (Exception, KeyboardInterrupt):
            pass
        try:
            report["bootstrap_phase"] = progress_from_log(work / "xpcshell.log")
        except (Exception, KeyboardInterrupt):
            report["bootstrap_phase"] = "invalid_progress"
        report["passed"] = (all(report[key] is True for key in
            ("native_process_completed", "cleanup", "original_runtime_unchanged", "host_snapshot_unchanged"))
            and report["bootstrap_phase"] == "harness_complete")
        report["failure"] = None if report["passed"] else "fixture_failed"
        report["logs"] = {name: dict(sha256=digest(work / name), bytes=(work / name).stat().st_size)
                          for name in ("sandbox.log", "xpcshell.log") if (work / name).is_file()}
        write_new(work / "report.json", encoded(report) + b"\n")
    return 0 if report["passed"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--execute", action="store_true", help="Explicitly run the isolated native fixture.")
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--parent-namespaces", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    os.umask(0o077)
    require(args.execute)
    def interrupted(_signum, _frame):
        raise InterruptedError("fixture_interrupted")
    signal.signal(signal.SIGTERM, interrupted)
    if args.inside:
        return inside(checked_output(args.output, fresh=False), json.loads(args.parent_namespaces))
    require(args.parent_namespaces is None)
    return execute(args.output)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt):
        print("native_filter_fixture_failed", file=sys.stderr)
        sys.exit(1)
