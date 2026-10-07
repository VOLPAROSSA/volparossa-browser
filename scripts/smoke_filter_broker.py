#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Opt-in real Gecko -> core filter-broker IPC, without list activation or peers.

The exact retained ESR and an explicitly hash-pinned core CLI run only inside a
fresh read-only-host, loopback-only bubblewrap namespace. There is deliberately
no agent socket: fetch must fail closed. This proves no positive content fetch,
protected peer transfer, uBO enrollment, default activation or expiry barrier.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time

from smoke_browser_startup import remove_profile
from smoke_compute_model import validate_stage
from smoke_consent import require, validate_isolation
from smoke_privacy import run_browser
from smoke_ubo_actor import (
    module_bytes, prepare_output, private_directory, validate_retained_bundle,
)
from stage_firefox import ROOT, build_path, digest, isolated_browser_home

SHORT_WORK = Path("/tmp/vpf")
MODULE_NAMES = ("Frame", "Contract", "Clock", "Session", "Gecko")
MODULES = tuple(f"integration/filters/{name}.sys.mjs" for name in MODULE_NAMES)
PROFILE_MODULES = "volparossa-filter-proof"
PHASES = (
    "initial", "isolation", "prepare", "broker_start", "browser_start", "module_import",
    "capabilities", "status", "fetch_unavailable", "reconnect", "revoke", "status_revoked",
    "complete", "verify_inputs", "cleanup",
)
ERRORS = (
    "invalid_request", "duplicate_request", "handshake_required", "busy", "unavailable",
    "revoked", "clock_error", "expired", "invalid_snapshot", "invalid_response",
    "not_configured", "fixture_other",
)
SCOPE = {
    "real_gecko_unix_ipc": True, "real_core_filter_broker": True,
    "positive_content_fetch": False, "protected_peer_fetch": False,
    "publication_resolved": False, "ubo_activation": False,
    "production_default_activation": False, "authority_expiry_proven": False,
    "startup_resume_stale_filter_barrier": False, "native_source_hooks": False,
}
# Public RFC 8032 test-vector key only: no fixture signing key or real publication.
PUBLISHER = "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
MANIFEST = hashlib.sha256(b"unresolved-disposable-filter-publication-v1").hexdigest()
NAME = "disposable-filter-broker-ipc"
TRACE_NAME = "filter-broker-proof-phase.json"
LOG_NAME = "broker-browser.log"
DEADLINE_SECONDS = 480
SCRIPT_SECONDS = 90

# Fixture-only privileged entry point. The application exposes no script bridge;
# endpoint and module paths are derived from ProfD, never supplied by a web page.
SCRIPT = r"""
const [expected, done] = arguments;
let phase = "module_import", client = null;
const phases = ["module_import", "capabilities", "status", "fetch_unavailable", "reconnect",
  "revoke", "status_revoked", "complete"];
const codes = ["invalid_request", "duplicate_request", "handshake_required", "busy", "unavailable",
  "revoked", "clock_error", "expired", "invalid_snapshot", "invalid_response", "not_configured"];
const mark = async value => {
  if (!phases.includes(value)) throw new Error();
  phase = value;
  const file = Services.dirsvc.get("ProfD", Ci.nsIFile);
  file.append("filter-broker-proof-phase.json");
  await IOUtils.writeJSON(file.path, {schema: 1, phase});
};
(async () => {
  await mark("module_import");
  const directory = Services.dirsvc.get("ProfD", Ci.nsIFile);
  for (const name of ["chrome", "volparossa-filter-proof"]) {
    directory.append(name);
    if (directory.isSymlink() || !directory.isDirectory() || (directory.permissions & 0o777) !== 0o700)
      throw new Error();
  }
  Services.io.getProtocolHandler("resource").QueryInterface(Ci.nsIResProtocolHandler)
    .setSubstitution("volparossa-filter-proof", Services.io.newFileURI(directory));
  const {VolparossaFilters} = ChromeUtils.importESModule("resource://volparossa-filter-proof/Gecko.sys.mjs");
  const {FilterBrokerError} = ChromeUtils.importESModule("resource://volparossa-filter-proof/Frame.sys.mjs");
  await mark("capabilities");
  client = await VolparossaFilters.connect(expected);
  const caps = client.session.capabilities;
  if (!caps || caps.protocol !== "volparossa-filter" || !caps.same_uid_only || caps.browser_activation !== false)
    throw new Error();
  await mark("status");
  const status = await client.status();
  if (status.state !== "unavailable" || status.snapshot !== null) throw new Error();
  await mark("fetch_unavailable");
  let fetchCode = null;
  try { await client.fetch(); } catch (error) {
    if (error instanceof FilterBrokerError) fetchCode = error.code;
  }
  if (fetchCode !== "unavailable" || !client.closed || !client.session.closed) throw new Error();
  client.close();
  await mark("reconnect");
  client = await VolparossaFilters.connect(expected);
  const before = await client.status();
  if (before.state !== "unavailable" || before.snapshot !== null) throw new Error();
  await mark("revoke");
  const authority = Services.dirsvc.get("ProfD", Ci.nsIFile);
  authority.append("volparossa-filter-broker");
  authority.append("authority.json");
  if (!authority.isFile() || authority.isSymlink() || (authority.permissions & 0o777) !== 0o600)
    throw new Error();
  await IOUtils.remove(authority.path);
  await mark("status_revoked");
  let revokeCode = null;
  try { await client.status(); } catch (error) {
    if (error instanceof FilterBrokerError) revokeCode = error.code;
  }
  if (revokeCode !== "revoked" || !client.closed || !client.session.closed) throw new Error();
  client.close();
  await mark("complete");
  return {schema: 1, ok: true, capabilities_validated: true, status_unavailable: true,
    fetch_error: fetchCode, separate_connection: true, authority_removed: true,
    revoked_error: revokeCode, closed_after_errors: true, positive_content_fetch: false};
})().finally(() => { try { client?.close(); } catch {} })
  .then(done, error => done({schema: 1, ok: false, phase,
    code: codes.includes(error?.code) ? error.code : "fixture_other"}));
"""


class ProofFailure(ValueError):
    def __init__(self, phase, code="fixture_other"):
        self.phase = phase if phase in PHASES else "initial"
        self.code = code if code in ERRORS else "fixture_other"
        super().__init__(self.code)


def validate_result(value):
    expected = dict(schema=1, ok=True, capabilities_validated=True, status_unavailable=True,
                    fetch_error="unavailable", separate_connection=True, authority_removed=True,
                    revoked_error="revoked", closed_after_errors=True, positive_content_fetch=False)
    if (type(value) is dict and set(value) == {"schema", "ok", "phase", "code"}
            and type(value["schema"]) is int and value["schema"] == 1 and value["ok"] is False
            and value["phase"] in PHASES and value["code"] in ERRORS):
        raise ProofFailure(value["phase"], value["code"])
    require(type(value) is dict and set(value) == set(expected)
            and all(type(value[key]) is type(item) and value[key] == item for key, item in expected.items()))
    return value


def validate_binary(path, expected):
    require(isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected) is not None)
    require(path.is_absolute() and path.resolve(strict=True) == path)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and not info.st_mode & 0o002 and info.st_mode & 0o111
            and 0 < info.st_size <= 512 * 1024 * 1024)
    require(digest(path) == expected)
    return expected


def validate_module_manifest(expected):
    require(type(expected) is dict and set(expected) == set(MODULES)
            and all(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) for value in expected.values()))
    for name, sha in expected.items():
        module_bytes(ROOT / name, sha)


def verify_modules(profile, expected):
    validate_module_manifest(expected)
    directory = profile / "chrome" / PROFILE_MODULES
    for path in (profile, profile / "chrome", directory):
        private_directory(path)
    require({path.name for path in directory.iterdir()} == {Path(name).name for name in MODULES})
    for name, sha in expected.items():
        require(module_bytes(directory / Path(name).name, sha, staged=True) == module_bytes(ROOT / name, sha))


def stage_modules(profile, expected):
    validate_module_manifest(expected)
    private_directory(profile)
    chrome = profile / "chrome"
    require(not chrome.exists() and not chrome.is_symlink())
    chrome.mkdir(mode=0o700)
    directory = chrome / PROFILE_MODULES
    directory.mkdir(mode=0o700)
    for name, sha in expected.items():
        with (directory / Path(name).name).open("xb") as stream:
            stream.write(module_bytes(ROOT / name, sha))
        (directory / Path(name).name).chmod(0o400)
    verify_modules(profile, expected)


def prepare_fixture(work, modules):
    private_directory(work)
    for name in ("profile", "config", "cache", "runtime", "tmp"):
        (work / name).mkdir(mode=0o700)
    profile = work / "profile"
    (profile / "prefs.js").write_text('user_pref("remote.prefs.recommended", false);\n')
    stage_modules(profile, modules)
    parent = profile / "volparossa-filter-broker"
    parent.mkdir(mode=0o700)
    for name in ("agent-cache", "downloads"):
        (parent / name).mkdir(mode=0o700)
    expected = dict(publisher_key=PUBLISHER, name=NAME, manifest_id=MANIFEST,
                    authority_expires_unix_seconds=int(time.time()) + 600)
    grant = dict(version=1, enabled=True, public_content=True, authorize_filter_publisher=True,
                 publisher_key=PUBLISHER, name=NAME, manifest_id=MANIFEST,
                 not_after_unix_seconds=expected["authority_expires_unix_seconds"])
    with (parent / "authority.json").open("x") as stream:
        json.dump(grant, stream, separators=(",", ":"))
    (parent / "authority.json").chmod(0o600)
    return expected


def broker_command(binary, work):
    parent = work / "profile/volparossa-filter-broker"
    socket = parent / "filter.sock"
    require(len(os.fsencode(socket)) <= 100)
    # The absent control endpoint is deliberate. No real agent/helper starts and
    # no test backend or alternative positive-download path is added to the CLI.
    return [str(binary), "--control-socket", str(parent / "absent-agent.sock"),
            "content", "filter-serve", "--authority", str(parent / "authority.json"),
            "--cache", str(parent / "agent-cache"), "--work-parent", str(parent / "downloads"),
            "--socket", str(socket), "--execute"]


def wait_socket(process, work):
    path = work / "profile/volparossa-filter-broker/filter.sock"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        require(process.poll() is None)
        if path.exists() or path.is_symlink():
            info = path.lstat()
            require(stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid()
                    and stat.S_IMODE(info.st_mode) == 0o600)
            return
        time.sleep(0.05)
    raise ProofFailure("broker_start", "unavailable")


def exercise(client, expected):
    client.command("Marionette:SetContext", {"value": "chrome"})
    client.command("WebDriver:SetTimeouts", {"script": SCRIPT_SECONDS * 1000})
    previous = client.socket.gettimeout()
    client.socket.settimeout(SCRIPT_SECONDS + 5)
    try:
        value = client.command("WebDriver:ExecuteAsyncScript", {
            "script": SCRIPT, "args": [expected], "newSandbox": True, "sandbox": "system",
        })["value"]
    finally:
        client.socket.settimeout(previous)
    return validate_result(value)


def phase_trace(work):
    path = work / "profile" / TRACE_NAME
    if not path.exists() and not path.is_symlink():
        return None
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and info.st_nlink == 1 and 0 < info.st_size <= 128)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        raw = stream.read(129)
    require(len(raw) <= 128)
    value = json.loads(raw)
    require(type(value) is dict and set(value) == {"schema", "phase"}
            and type(value["schema"]) is int and value["schema"] == 1 and value["phase"] in PHASES)
    return value["phase"]


def stop_broker(process):
    if process is None:
        return {"started": False, "reaped": True, "graceful": False}
    graceful = False
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            graceful = process.wait(timeout=5) == 0
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
    return {"started": True, "reaped": process.poll() is not None, "graceful": graceful}


def cleanup(work):
    removed = remove_profile(work)
    path = work / LOG_NAME
    if path.exists() or path.is_symlink():
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1)
        path.unlink()
    return removed and not path.exists()


def inside(args, stage, work, metadata, modules):
    report = dict(schema=1, passed=False, scope=SCOPE, phase="initial", failure=None,
                  runtime_version=metadata["version"], runtime_source_stamp=metadata["source_stamp"],
                  runtime_sha256=metadata["runtime_sha256"], broker_sha256=args.broker_sha256,
                  script_sha256=digest(Path(__file__).resolve()), modules_sha256=modules,
                  staged_modules_sha256=None, exercise=None, actor_phase=None,
                  host_read_only=False, interfaces=None, broker_cleanup=None,
                  broker_socket_removed=False, temporary_profiles_removed=False)
    broker = None
    scoped = False
    try:
        report["phase"] = "isolation"
        require(work == SHORT_WORK and os.getuid() != 0)
        validate_isolation(stage, work, args.host_netns)
        original = build_path(args.original_work)
        require(str(original) == args.original_work and original.parent == ROOT / "build")
        require((original.stat().st_dev, original.stat().st_ino) == (work.stat().st_dev, work.stat().st_ino))
        require({path.name for path in work.iterdir()} == {"appdata"})
        require(os.statvfs(args.broker).f_flag & os.ST_RDONLY)
        validate_binary(Path(args.broker), args.broker_sha256)
        validate_module_manifest(modules)
        report.update(host_read_only=True, interfaces=["lo"])
        scoped = True
        report["phase"] = "prepare"
        expected = prepare_fixture(work, modules)
        report["staged_modules_sha256"] = dict(modules)
        report["phase"] = "broker_start"
        broker = subprocess.Popen(broker_command(Path(args.broker), work),
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, start_new_session=True)
        wait_socket(broker, work)
        report["phase"] = "browser_start"
        result = run_browser(stage, work, work / "profile", "broker-browser",
                             exercise=lambda client: exercise(client, expected))
        report["exercise"] = validate_result(result["exercise"])
        report["phase"] = "verify_inputs"
        verify_modules(work / "profile", modules)
        require(not (work / "profile/volparossa-filter-broker/authority.json").exists())
        require(not (work / "profile/volparossa-filter-broker/absent-agent.sock").exists())
        validate_binary(Path(args.broker), args.broker_sha256)
        validate_stage(stage)
        validate_retained_bundle(stage, metadata)
        report["phase"] = "complete"
        report["passed"] = True
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        report["failure"] = "fixture_other"
        if isinstance(error, ProofFailure):
            report["phase"], report["failure"] = error.phase, error.code
    finally:
        # A manually invoked --inside is not proof of isolation. Never inspect,
        # remove or write a profile/report until the owned bind and namespace
        # were actually established. The outer owner handles its own fresh work.
        if not scoped:
            raise ProofFailure("isolation")
        try:
            report["broker_cleanup"] = stop_broker(broker)
            report["broker_socket_removed"] = not (work / "profile/volparossa-filter-broker/filter.sock").exists()
            report["actor_phase"] = phase_trace(work)
            if report["staged_modules_sha256"] is not None:
                verify_modules(work / "profile", modules)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            report["passed"] = False
            report["failure"] = "fixture_other"
        try:
            report["temporary_profiles_removed"] = cleanup(work)
        except (OSError, ValueError):
            report["temporary_profiles_removed"] = False
        cleaned = bool(report["broker_cleanup"] and report["broker_cleanup"]["reaped"]
                       and report["broker_cleanup"]["graceful"] and report["broker_socket_removed"]
                       and report["temporary_profiles_removed"])
        if report["passed"] and not cleaned:
            report["phase"], report["failure"] = "cleanup", "fixture_other"
        report["passed"] = bool(report["passed"] and cleaned)
        with (work / "report.json").open("x") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
    require(report["passed"], "filter_broker_smoke_failed_see_report")


def wrapper_command(args, stage, work, modules, mounts):
    return ["/usr/bin/bwrap", "--die-with-parent", "--unshare-user", "--unshare-net", "--unshare-pid",
            "--ro-bind", "/", "/", *mounts, "--bind", str(work), str(work),
            "--tmpfs", "/tmp", "--bind", str(work), str(SHORT_WORK),
            "--proc", "/proc", "--dev", "/dev", "--", sys.executable, "-B", str(Path(__file__).resolve()),
            "--stage", str(stage), "--output", str(SHORT_WORK), "--inside", "--original-work", str(work),
            "--host-netns", os.readlink("/proc/self/ns/net"), "--broker", str(args.broker),
            "--broker-sha256", args.broker_sha256, "--module-hashes", json.dumps(modules, separators=(",", ":"))]


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, help="Exact retained read-only ESR140 stage; no downloads")
    parser.add_argument("--broker", required=True, help="Existing reviewed core volparossa CLI executable")
    parser.add_argument("--broker-sha256", required=True, help="Independent exact CLI build hash")
    parser.add_argument("--output", required=True)
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--original-work", help=argparse.SUPPRESS)
    parser.add_argument("--host-netns", help=argparse.SUPPRESS)
    parser.add_argument("--module-hashes", help=argparse.SUPPRESS)
    args = parser.parse_args()
    stage = Path(args.stage).resolve(strict=True)
    binary = Path(args.broker)
    validate_binary(binary, args.broker_sha256)
    metadata = validate_stage(stage)
    validate_retained_bundle(stage, metadata)
    if args.inside:
        require(args.output == str(SHORT_WORK) and args.module_hashes is not None)
        inside(args, stage, SHORT_WORK, metadata, json.loads(args.module_hashes))
        return
    require(args.original_work is None and args.host_netns is None and args.module_hashes is None)
    modules = {name: digest(ROOT / name) for name in MODULES}
    validate_module_manifest(modules)
    work = build_path(args.output)
    require(not stage.is_relative_to(work) and not binary.is_relative_to(work))
    prepare_output(work)
    print("Filter IPC proof: fresh private workspace only; read-only host/runtime; loopback-only namespace; no list activation.", flush=True)
    try:
        mounts = isolated_browser_home(work)
        subprocess.run(wrapper_command(args, stage, work, modules, mounts), check=True, timeout=DEADLINE_SECONDS)
    finally:
        removed = cleanup(work)
        with (work / "outer-cleanup.json").open("x") as stream:
            json.dump({"schema": 1, "temporary_profiles_removed": removed}, stream)
            stream.write("\n")
        require(removed, "filter_broker_cleanup_incomplete")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt):
        print("Filter IPC proof failed; inspect the closed fixture report if available.", file=sys.stderr)
        raise SystemExit(1) from None
