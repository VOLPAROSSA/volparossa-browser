#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Opt-in native delivery of synthetic bytes to the original signed uBO.

One actual retained ESR process, private fresh profile, read-only host/runtime,
disposable user/PID/network namespaces. No HTTP server, source build, download,
broker, signature/publication proof, engine-use claim or default activation.
Execution stays refused until the complete reviewed source set is frozen.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

from smoke_ubo_actor import module_bytes, private_directory, prepare_output, validate_retained_bundle
from smoke_filter_selection import clean_environment, limits, PrivateLogDrain, joined_success, stop_process
from smoke_privacy import Marionette, snapshot
from smoke_consent import all_addons_active, require, validate_isolation
from smoke_browser_startup import remove_profile
from smoke_compute_model import validate_stage
from stage_firefox import ROOT, build_path, digest, isolated_browser_home, load_defaults

BODY = b"[Adblock Plus 2.0]\n||ads.asset.invalid^\n||track.asset.invalid^\n"
ENVELOPE = b"VOLPAROSSA synthetic unsigned asset fixture v1"
MODULE_NAMES = ("AssetChannel", "AssetRedirect", "Actor", "ActorContract", "Selection", "VolparossaFilterSelectionParent",
                "VolparossaFilterSelectionChild", "Contract", "Frame")
MODULES = {"integration/filters/" + name + ".sys.mjs": name + ".sys.mjs" for name in MODULE_NAMES}
MODULES["tests/fixtures/filter_asset.sys.mjs"] = "Fixture.sys.mjs"
# Complete reviewed module set. Never learn execution pins from live files.
PINS = {
    "integration/filters/AssetChannel.sys.mjs": "a3cd3e50b604d428a6075d5cbb4c5bb73a7519bcb0cbc0aa8ceb9a15f6fa76b0",
    "integration/filters/AssetRedirect.sys.mjs": "3d71e1db996f0a217a6ed9efb35234801f587ee5a873575ca1c67c46c8b95e2b",
    "integration/filters/Actor.sys.mjs": "df38bd93bca44227649bd40f6a9e737a62852e0327a36c165424860175e088f4",
    "integration/filters/ActorContract.sys.mjs": "ce57d577358e2328e765d0a32d04f37d9f8b4ea36cc4d6accaa308ffe34e2b57",
    "integration/filters/Selection.sys.mjs": "1fc0f83e426ac5068a5d8c78ae9b286dd0a0d51321b8bdf7182ade1fba2ddb28",
    "integration/filters/VolparossaFilterSelectionParent.sys.mjs": "b04b38aee8678a184ec4ce953ef49491bf1834061e606ae467165924c140b75c",
    "integration/filters/VolparossaFilterSelectionChild.sys.mjs": "de1b0d151b0b15a4ff78115a5cef5ba68b111729abbf5beb0a666fc4a29c291b",
    "integration/filters/Contract.sys.mjs": "e4a631eae4590115d4e54a196529fa17ad4b7955bb184ae1edced919b69cd15b",
    "integration/filters/Frame.sys.mjs": "8f01606ca2f359b888948d13dd489d96cc42b567b88f9996716168d8500b4ca5",
    "tests/fixtures/filter_asset.sys.mjs": "644d6cba54e7b3d6e4080bbf8586dc33c6f03f47757551ae996f59dbf8423444",
}
PROFILE_MODULES = "volparossa-filter-asset"
MAX_LOG = 4 * 1024 * 1024
OUTER_SECONDS = 240
PHASES = ("start", "actor_open", "asset_open", "read_asset", "wrong_principal", "wrong_query",
          "asset_close", "late_request", "actor_close", "complete")
FAILURE_PHASES = ("bootstrap", "reply_validate", *PHASES)
ASSET_CHECKPOINTS = ("ready", "request_current", "request_uri", "request_method", "request_context",
                    "request_principals", "request_flags", "request_headers", "transfer", "transfer_identity",
                    "transfer_callbacks", "prepare", "intercept", "body_complete")
FAILURE_REASONS = (
    "asset_config", "asset_package", "asset_context", "asset_authority", "asset_expired", "asset_clock",
    "asset_busy", "asset_closed", "asset_failed", "asset_cleanup",
    "actor_invalid", "actor_closed", "actor_busy", "actor_context", "actor_package", "actor_deadline",
    "actor_clock", "actor_authority", "actor_reply", "actor_asset", "actor_readiness_changed",
    "actor_cleanup", "actor_other", "invalid_config", "invalid_selection", "selection_changed",
    "unowned_selection", "reload_unproved", "invalid_receipt", "identity_lost", "selection_failed",
    "fixture_failed", "fixture_negative_deadline", "fixture_cleanup", "fixture_other", "fixture_invalid_reply",
)
OPERATIONS = ("prepare", "browser_start", "browser_connect", "browser_identity", "exercise", "browser_quit", "complete")
SCOPE = dict(original_signed_ubo=True, actual_background_asset_request=True,
             synthetic_publication_grant=True, synthetic_manifest=True, browser_sessions=1,
             protected_peer_fetch=False, real_core_broker=False, publisher_signature_verified=False,
             actual_uBO_engine_bytes=False, native_admission_registered=False,
             production_default_enrollment=False, startup_resume_stale_filter_barrier=False,
             real_os_expiry=False, original_https_server=False)
BOOTSTRAP = r"""
const done = arguments[arguments.length - 1];
let failureEnvelope;
(async () => {
  const directory = Services.dirsvc.get("ProfD", Ci.nsIFile);
  for (const part of ["chrome", "volparossa-filter-asset"]) {
    directory.append(part);
    if (directory.isSymlink() || !directory.isDirectory() || (directory.permissions & 0o7777) !== 0o700)
      throw new Error("asset_fixture_directory");
  }
  Services.io.getProtocolHandler("resource").QueryInterface(Ci.nsIResProtocolHandler)
    .setSubstitution("volparossa-filter-asset", Services.io.newFileURI(directory));
  const fixture = ChromeUtils.importESModule("resource://volparossa-filter-asset/Fixture.sys.mjs");
  const {exercise} = fixture;
  failureEnvelope = fixture.failureEnvelope;
  return exercise();
})().then(done, error => done(failureEnvelope ? failureEnvelope(error, "bootstrap")
  : {schema: 1, ok: false, phase: "bootstrap", reason: "fixture_other"}));
"""


def asset_diagnostic(value):
    require(type(value) is dict and set(value) == {"checkpoint", "status"}
            and type(value["checkpoint"]) is str and value["checkpoint"] in ASSET_CHECKPOINTS)
    state = value["status"]
    require(type(state) is dict and set(state) == {"closed", "accepted", "completed", "denied"}
            and type(state["closed"]) is bool
            and all(type(state[key]) is int and 0 <= state[key] <= 65535 for key in ("accepted", "completed", "denied")))
    return {"checkpoint": value["checkpoint"], "status": dict(state)}


class AssetFailure(ValueError):
    """Only known enum strings may enter a retained public result."""
    def __init__(self, phase, reason, diagnostic=None):
        if type(phase) is not str or phase not in FAILURE_PHASES or type(reason) is not str or reason not in FAILURE_REASONS:
            phase, reason, diagnostic = "reply_validate", "fixture_invalid_reply", None
        if diagnostic is not None:
            try: diagnostic = asset_diagnostic(diagnostic)
            except (ValueError, KeyError, TypeError):
                phase, reason, diagnostic = "reply_validate", "fixture_invalid_reply", None
        super().__init__(reason)
        self.phase, self.reason = phase, reason
        if diagnostic is not None: self.asset_diagnostic = diagnostic


def pinned():
    return (type(PINS) is dict and set(PINS) == set(MODULES)
            and all(isinstance(value, str) and len(value) == 64
                    and all(char in "0123456789abcdef" for char in value) for value in PINS.values()))


def verify_sources():
    require(pinned(), "asset_fixture_unfrozen")
    return {name: module_bytes(ROOT / name, PINS[name]) for name in MODULES}


def profile_modules(profile, *, create=False):
    sources = verify_sources()
    private_directory(profile)
    chrome, target = profile / "chrome", profile / "chrome" / PROFILE_MODULES
    if create:
        require(not chrome.exists() and not chrome.is_symlink())
        chrome.mkdir(mode=0o700); target.mkdir(mode=0o700)
        for source, name in MODULES.items():
            with os.fdopen(os.open(target / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400), "wb") as stream:
                stream.write(sources[source])
    private_directory(chrome); private_directory(target)
    require({path.name for path in target.iterdir()} == set(MODULES.values()))
    for source, name in MODULES.items():
        require(module_bytes(target / name, PINS[source], staged=True) == sources[source])


def asset_result(value):
    if type(value) is not dict:
        raise AssetFailure("reply_validate", "fixture_invalid_reply")
    if value.get("ok") is not True:
        if (set(value) not in ({"schema", "ok", "phase", "reason"},
                              {"schema", "ok", "phase", "reason", "asset_diagnostic"})
                or type(value.get("schema")) is not int or value["schema"] != 1 or value.get("ok") is not False
                or type(value.get("phase")) is not str or value["phase"] not in FAILURE_PHASES
                or type(value.get("reason")) is not str or value["reason"] not in FAILURE_REASONS):
            raise AssetFailure("reply_validate", "fixture_invalid_reply")
        if "asset_diagnostic" in value:
            try: diagnostic = asset_diagnostic(value["asset_diagnostic"])
            except (ValueError, KeyError, TypeError):
                raise AssetFailure("reply_validate", "fixture_invalid_reply") from None
            raise AssetFailure(value["phase"], value["reason"], diagnostic)
        raise AssetFailure(value["phase"], value["reason"])
    require(type(value) is dict and set(value) == {"schema", "ok", "phase", "synthetic_manifest_sha256",
            "positive", "wrong_principal", "wrong_query", "after_close", "final_status", "cleanup_complete"}
            and type(value["schema"]) is int and value["schema"] == 1 and value["ok"] is True
            and value["phase"] == "complete" and value["cleanup_complete"] is True
            and value["synthetic_manifest_sha256"] == hashlib.sha256(ENVELOPE).hexdigest(), "asset_fixture_failed")
    positive = value["positive"]
    require(type(positive) is dict and set(positive) == {"status", "bytes", "sha256", "exact_text", "preserved", "selected", "imported"}
            and type(positive["bytes"]) is int and positive["bytes"] == len(BODY) == 63
            and positive["sha256"] == hashlib.sha256(BODY).hexdigest()
            and positive["exact_text"] is True and positive["preserved"] is True
            and positive["selected"] is False and positive["imported"] is False)
    for state, closed, denied in ((positive["status"], False, 0), (value["final_status"], True, 2)):
        require(type(state) is dict and set(state) == {"closed", "accepted", "completed", "denied"}
                and state["closed"] is closed and all(type(state[key]) is int for key in ("accepted", "completed", "denied"))
                and state["accepted"] == 1 and state["completed"] == 1 and state["denied"] == denied)
    for key in ("wrong_principal", "wrong_query", "after_close"):
        denied = value[key]
        require(type(denied) is dict and set(denied) == {"stopped", "aborted", "bytes", "no_network_flags"}
                and denied["stopped"] is True and denied["aborted"] is True and denied["no_network_flags"] is True
                and type(denied["bytes"]) is int and denied["bytes"] == 0)
    return value


def exercise(client):
    client.command("Marionette:SetContext", {"value": "chrome"})
    client.command("WebDriver:SetTimeouts", {"script": 100000})
    previous = client.socket.gettimeout(); client.socket.settimeout(105)
    try:
        return asset_result(client.command("WebDriver:ExecuteAsyncScript", {"script": BOOTSTRAP,
            "args": [], "newSandbox": True, "sandbox": "system"})["value"])
    finally: client.socket.settimeout(previous)


def write_operation(work, report, operation):
    require(operation in OPERATIONS)
    report["operation"] = operation
    path = work / "operation.json"
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600), "w") as stream:
        json.dump(dict(schema=1, operation=operation), stream, separators=(",", ":"))


def read_json(path, keys):
    if not path.exists(): return None
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 256)
    value = json.loads(path.read_text())
    require(type(value) is dict and set(value) == keys and type(value["schema"]) is int and value["schema"] == 1)
    return value


def read_operation(work):
    value = read_json(work / "operation.json", {"schema", "operation"})
    require(value is None or value["operation"] in OPERATIONS)
    return value


def read_phase(work):
    value = read_json(work / "profile/volparossa-filter-asset-phase.json", {"schema", "phase"})
    require(value is None or value["phase"] in PHASES)
    return value


def close_session(client, process, drain, profile):
    try:
        if client: client.socket.close()
    finally:
        try: stop_process(process)
        finally:
            try:
                if drain: drain.finish()
            finally: profile_modules(profile)


def browser(stage, work, report):
    profile_modules(work / "profile")
    process = client = drain = None
    mark = lambda operation: write_operation(work, report, operation)
    with (work / "asset.log").open("xb") as log:
        try:
            mark("browser_start")
            process = subprocess.Popen([str(stage / "firefox-esr"), "--headless", "--no-remote", "--new-instance",
                "--profile", str(work / "profile"), "--marionette", "--remote-allow-system-access", "about:blank"],
                env=clean_environment(work), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                start_new_session=True)
            drain = PrivateLogDrain(process, log); drain.start()
            mark("browser_connect")
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                require(process.poll() is None)
                try:
                    connection = socket.create_connection(("127.0.0.1", 2828), timeout=1)
                    connection.settimeout(20); client = Marionette(connection); break
                except (ConnectionRefusedError, TimeoutError): time.sleep(0.2)
            require(client is not None)
            client.command("WebDriver:NewSession", {"capabilities": {"alwaysMatch": {"acceptInsecureCerts": False}}})
            client.command("Marionette:SetContext", {"value": "chrome"}); mark("browser_identity")
            require(Path(client.script('return Services.dirsvc.get("GreD", Ci.nsIFile).path;')).resolve() == stage)
            preferences = snapshot(client, list(load_defaults()) + ["privacy.trackingprotection.enabled", "remote.prefs.recommended"])
            require(preferences["remote.prefs.recommended"]["effective"] is False)
            all_addons_active(client); mark("exercise")
            result = exercise(client)
            all_addons_active(client)
            require(snapshot(client, list(preferences)) == preferences)
            mark("browser_quit")
            client.command("Marionette:Quit", {"flags": ["eAttemptQuit"]}); joined_success(process)
            drain.finish()
            return dict(exit_status=0, result=result)
        finally: close_session(client, process, drain, work / "profile")


def cleanup(work):
    return remove_profile(work)


def log_receipts(work):
    path = work / "asset.log"
    if not path.exists(): return {}
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_LOG)
    return {"asset": dict(bytes=path.stat().st_size, sha256=digest(path))}


def inside(stage, work, metadata, host_netns):
    validate_isolation(stage, work, host_netns); verify_sources(); limits()
    report = dict(schema=1, passed=False, scope=SCOPE, operation="prepare", session=None,
                  module_sha256=PINS, driver_sha256=digest(Path(__file__)),
                  runtime_version=metadata["version"], runtime_source_stamp=metadata["source_stamp"],
                  runtime_sha256=metadata["runtime_sha256"], extension_packages=metadata["extensions"]["packages"],
                  failure="fixture_failed")
    try:
        for name in ("profile", "config", "cache", "runtime", "tmp"):
            (work / name).mkdir(mode=0o700)
        profile_modules(work / "profile", create=True)
        with (work / "profile/prefs.js").open("x") as stream:
            stream.write('user_pref("remote.prefs.recommended", false);\n')
        report["session"] = browser(stage, work, report)
        verify_sources(); validate_retained_bundle(stage, metadata); require(validate_stage(stage) == metadata)
        report["passed"] = True; write_operation(work, report, "complete")
    except (Exception, KeyboardInterrupt) as error:
        if isinstance(error, AssetFailure):
            report["failure_phase"] = error.phase
            report["closed_failure"] = error.reason
            if hasattr(error, "asset_diagnostic"): report["asset_diagnostic"] = error.asset_diagnostic
    finally:
        try: report["fixture_phase"] = read_phase(work)
        except (Exception, KeyboardInterrupt): report["fixture_phase"] = None; report["passed"] = False
        report["temporary_profiles_removed"] = cleanup(work)
        report["passed"] = report["passed"] and report["temporary_profiles_removed"]
        report["last_operation"] = read_operation(work); report["logs"] = log_receipts(work)
        if report["passed"]: report["failure"] = None
        with (work / "report.json").open("x") as stream: json.dump(report, stream, sort_keys=True)
    require(report["passed"], "asset_fixture_failed")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path); parser.add_argument("--output", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--host-netns", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.execute:
        require(not args.inside and args.host_netns is None)
        print(json.dumps(dict(execute=False, source_pins_complete=pinned(), scope=SCOPE,
                              outer_seconds=OUTER_SECONDS, sessions=1), sort_keys=True)); return
    require(pinned() and args.stage is not None and args.output is not None, "asset_fixture_unfrozen")
    os.umask(0o077)
    stage, work = args.stage.resolve(strict=True), build_path(args.output)
    require(not stage.is_relative_to(work) and not work.is_relative_to(stage))
    metadata = validate_stage(stage); validate_retained_bundle(stage, metadata); verify_sources()
    if args.inside: return inside(stage, work, metadata, args.host_netns)
    require(args.host_netns is None); prepare_output(work)
    try:
        mounts = isolated_browser_home(work)
        subprocess.run(["/usr/bin/bwrap", "--die-with-parent", "--unshare-user", "--unshare-net", "--unshare-pid",
            "--ro-bind", "/", "/", *mounts, "--bind", str(work), str(work), "--tmpfs", "/tmp",
            "--proc", "/proc", "--dev", "/dev", "--", sys.executable, "-B", str(Path(__file__).resolve()),
            "--stage", str(stage), "--output", str(work), "--execute", "--inside", "--host-netns",
            os.readlink("/proc/self/ns/net")], check=True, timeout=OUTER_SECONDS, env=clean_environment(work))
    finally:
        removed = cleanup(work)
        with (work / "outer-cleanup.json").open("x") as stream:
            json.dump(dict(schema=1, temporary_profiles_removed=removed,
                           last_operation=read_operation(work), logs=log_receipts(work)), stream)
        require(removed)


if __name__ == "__main__":
    try: main()
    except (Exception, KeyboardInterrupt):
        print("filter_asset_fixture_failed", file=sys.stderr); raise SystemExit(1) from None
