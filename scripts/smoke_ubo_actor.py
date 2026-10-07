#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Opt-in isolated proof of a browser-owned closed actor on the original signed uBO.

No browser starts unless this script is explicitly run. The retained ESR runtime
is verified and mounted read-only; two fresh disposable profiles are used in a
loopback-only namespace. Marionette bootstraps the module only as a test driver;
the actual module/actor accepts no script, URL, path or free-form uBO request.
This is NOT production default enrollment, authorized network-list fetching,
offline expiry or a startup/resume stale-filter barrier.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

from consent_fixture import PURPOSES, serve_fixture, values
from bundle_extensions import LOCK, load_lock, read_package
from smoke_browser_startup import remove_profile
from smoke_compute_model import validate_stage
from smoke_consent import (
    UBLOCK, all_addons_active, check_probe_removed, configure_consent,
    load_case, recorded_case, require, set_control_addons, ubo_selection,
    unblocked_probe, until, validate_isolation,
)
from smoke_privacy import run_browser, snapshot
from stage_firefox import ROOT, build_path, digest, isolated_browser_home, load_defaults

PORT = 18765
ORIGIN = f"http://127.0.0.1:{PORT}"
LIST_URL = ORIGIN + "/filters.txt"
STOCKS = sorted(["easylist", "easyprivacy", "plowe-0", "ublock-badware", "ublock-filters",
                 "ublock-privacy", "ublock-quick-fixes", "ublock-unbreak", "urlhaus-1", "user-filters"])
MODULES = tuple("integration/ubo-proof/" + name + ".sys.mjs" for name in (
    "Contract", "Controller", "VolparossaUboProofParent", "VolparossaUboProofChild"))
PROFILE_MODULES = "volparossa-ubo-proof"
MAX_MODULE_BYTES = 65536
TRACE_NAME = "volparossa-ubo-proof-phase.json"
TRACE_PHASES = ("import", "open", "enroll", "observe", "revoke", "complete")
# Kept identical to the closed actor contract by inert source tests.
FAILURE_PHASES = (
    "bootstrap", "read_journal", "observe", "pending_journal", "completed_journal", "opt_out_journal",
    "parent_validate", "actor_delivery", "reply_validate", "child_validate", "child_context",
    "child_api_ready", "child_dashboard_ready", "child_storage_read", "child_storage_ready",
    "child_broadcast", "child_cleanup", "mutate", "wait_stored", "reload_drain", "reload_drain_event",
    "reload_fresh", "reload_fresh_event", "verify_final",
)
FAILURE_REASONS = (
    "ubo_proof_other", "ubo_proof_invalid_reply", "ubo_proof_busy_or_closed", "ubo_proof_cannot_reset_enrollment",
    "ubo_proof_concurrent_selection_change", "ubo_proof_deadline", "ubo_proof_explicit_fixture_required",
    "ubo_proof_extension_replaced", "ubo_proof_invalid_journal", "ubo_proof_missing_attempt_marker",
    "ubo_proof_missing_storage", "ubo_proof_no_reload_evidence", "ubo_proof_not_fresh_stock_selection",
    "ubo_proof_page_timeout", "ubo_proof_private_journal_required", "ubo_proof_private_profile_required",
    "ubo_proof_refused", "ubo_proof_uncertain_previous_write", "ubo_proof_unknown_timeout", "ubo_proof_unowned_list",
    "ubo_proof_unowned_parent_context", "ubo_proof_unsolicited_child_message", "ubo_proof_wrong_addon",
    "ubo_proof_wrong_command", "ubo_proof_wrong_context", "ubo_proof_wrong_package", "ubo_proof_wrong_package_digest",
)
SCOPE = {
    "isolated_original_signed_ubo_only": True, "production_default_enrollment": False,
    "startup_resume_stale_filter_barrier": False, "offline_authority_expiry": False,
    "decentralized_signed_list_broker": False, "full_firefox_source_build": False,
}

# This privileged bootstrap is fixture-only; no code string traverses the actor.
BOOTSTRAP = r"""
const [operation, done] = arguments;
let failureEnvelope;
(async () => {
  if (!["enroll", "observe", "revoke"].includes(operation)) throw new Error("fixed_operation_required");
  const trace = Services.dirsvc.get("ProfD", Ci.nsIFile);
  trace.append("volparossa-ubo-proof-phase.json");
  const mark = phase => {
    if (!["import", "open", "enroll", "observe", "revoke", "complete"].includes(phase))
      throw new Error("fixed_phase_required");
    return IOUtils.writeJSON(trace.path, {schema: 1, phase});
  };
  const directory = Services.dirsvc.get("ProfD", Ci.nsIFile);
  for (const name of ["chrome", "volparossa-ubo-proof"]) {
    directory.append(name);
    if (directory.isSymlink() || !directory.isDirectory() || (directory.permissions & 0o777) !== 0o700)
      throw new Error("fixed_profile_modules_required");
  }
  Services.io.getProtocolHandler("resource").QueryInterface(Ci.nsIResProtocolHandler)
    .setSubstitution("volparossa-ubo-proof", Services.io.newFileURI(directory));
  await mark("import");
  ({failureEnvelope} = ChromeUtils.importESModule("resource://volparossa-ubo-proof/Contract.sys.mjs"));
  const {openProof} = ChromeUtils.importESModule("resource://volparossa-ubo-proof/Controller.sys.mjs");
  await mark("open");
  const proof = await openProof();
  let result;
  try { await mark(operation); result = await proof[operation](); } finally { proof.shutdown(); }
  await mark("complete");
  return result;
})().then(done, error => done({failed: true, diagnostic: failureEnvelope
  ? failureEnvelope(error) : {schema: 1, ok: false, phase: "bootstrap", reason: "ubo_proof_other"}}));
"""


class ActorFailure(ValueError):
    """Only validated fixed enum fields may enter the retained report."""
    def __init__(self, phase, reason):
        if phase not in FAILURE_PHASES or reason not in FAILURE_REASONS:
            phase, reason = "reply_validate", "ubo_proof_invalid_reply"
        super().__init__(reason)
        self.phase, self.reason = phase, reason


def actor_result(result):
    if type(result) is not dict:
        raise ActorFailure("reply_validate", "ubo_proof_invalid_reply")
    if "failed" in result:
        diagnostic = result.get("diagnostic")
        if (set(result) != {"failed", "diagnostic"} or result["failed"] is not True
                or type(diagnostic) is not dict or set(diagnostic) != {"schema", "ok", "phase", "reason"}
                or type(diagnostic["schema"]) is not int or diagnostic["schema"] != 1 or diagnostic["ok"] is not False
                or diagnostic["phase"] not in FAILURE_PHASES or diagnostic["reason"] not in FAILURE_REASONS):
            raise ActorFailure("reply_validate", "ubo_proof_invalid_reply")
        raise ActorFailure(diagnostic["phase"], diagnostic["reason"])
    return result


def validate_retained_bundle(stage, metadata):
    """Reuse exact read-only package checks for a retained sibling-worktree stage.

    The ordinary bundle verify() also confines *outputs* to its checkout build/.
    Here no output is written to the existing stage, and no install/fetch is used.
    """
    cache = stage / "distribution/extensions"
    require(cache.resolve() == cache and cache.is_relative_to(stage))
    require(metadata.get("extensions", {}).get("lock_sha256") == digest(LOCK))
    packages = [read_package(cache, entry)[1] for entry in load_lock()["extensions"]]
    require(packages == metadata["extensions"]["packages"], "ubo_actor_stale_extension_packages")


def prepare_output(work):
    """Create only this checkout's owned build directory and one fresh child."""
    base = ROOT / "build"
    require(work.parent == base, "ubo_actor_output_must_be_direct_build_child")
    require(not work.exists() and not work.is_symlink(), "ubo_actor_output_already_exists")
    if not base.exists() and not base.is_symlink():
        base.mkdir(mode=0o700)
    info = base.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and base.resolve() == base and not info.st_mode & 0o022,
            "ubo_actor_build_must_be_owned_directory")
    work.mkdir(mode=0o700)


def private_directory(path):
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and path.resolve() == path
            and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,
            "ubo_actor_module_directory_refused")


def module_bytes(path, expected, *, staged=False):
    """Read one bounded owned regular file without following a final symlink."""
    require(path.resolve() == path, "ubo_actor_module_symlink_refused")
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and info.st_nlink == 1 and 0 < info.st_size <= MAX_MODULE_BYTES
            # Original owned checkout files may use ordinary group-write 0664;
            # inside() verifies their source mount is read-only. Never execute
            # there: only the hash-bound 0400 copies use the Firefox read path.
            and (stat.S_IMODE(info.st_mode) == 0o400 if staged else not info.st_mode & 0o7113),
            "ubo_actor_module_file_refused")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        opened = os.fstat(stream.fileno())
        require((opened.st_dev, opened.st_ino) == (info.st_dev, info.st_ino),
                "ubo_actor_module_changed")
        data = stream.read(MAX_MODULE_BYTES + 1)
    require(len(data) == info.st_size and hashlib.sha256(data).hexdigest() == expected,
            "ubo_actor_module_digest_mismatch")
    return data


def verify_profile_modules(profile, expected):
    require(type(expected) is dict and set(expected) == set(MODULES), "ubo_actor_module_manifest_refused")
    directory = profile / "chrome" / PROFILE_MODULES
    for path in (profile, profile / "chrome", directory):
        private_directory(path)
    require({path.name for path in directory.iterdir()} == {Path(name).name for name in MODULES},
            "ubo_actor_module_names_refused")
    verified = {}
    for name in MODULES:
        source = module_bytes(ROOT / name, expected[name])
        staged = module_bytes(directory / Path(name).name, expected[name], staged=True)
        require(staged == source,
                "ubo_actor_module_copy_mismatch")
        verified[name] = hashlib.sha256(staged).hexdigest()
    return verified


def stage_profile_modules(profile, expected):
    """Use Firefox's existing profile/chrome read boundary, never relax its sandbox."""
    require(type(expected) is dict and set(expected) == set(MODULES), "ubo_actor_module_manifest_refused")
    private_directory(profile)
    chrome = profile / "chrome"
    require(not chrome.exists() and not chrome.is_symlink(), "ubo_actor_module_staging_not_fresh")
    # Validate all original bytes before creating a new fixed private tree.
    sources = {name: module_bytes(ROOT / name, expected[name]) for name in MODULES}
    chrome.mkdir(mode=0o700)
    directory = chrome / PROFILE_MODULES
    directory.mkdir(mode=0o700)
    for name, data in sources.items():
        path = directory / Path(name).name
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400), "wb") as stream:
            stream.write(data)
    return verify_profile_modules(profile, expected)


def actor(client, operation):
    require(operation in ("enroll", "observe", "revoke"))
    client.command("Marionette:SetContext", {"value": "chrome"})
    client.command("WebDriver:SetTimeouts", {"script": 55000})
    previous_timeout = client.socket.gettimeout()
    client.socket.settimeout(60)
    try:
        result = client.command("WebDriver:ExecuteAsyncScript", {
            "script": BOOTSTRAP, "args": [operation],
            "newSandbox": True, "sandbox": "system",
        })["value"]
    finally:
        client.socket.settimeout(previous_timeout)
    return actor_result(result)


def fresh_change(result, desired):
    require(type(result) is dict and result.get("freshReload") is True
            and result.get("selected") is desired and result.get("imported") is desired
            and type(result.get("stocks")) is int and result["stocks"] == 10
            and type(result.get("reloadEvents")) is int and 2 <= result["reloadEvents"] <= 32,
            "ubo_actor_no_fresh_reload_evidence")


def declined(result):
    require(result == {"state": "opted-out", "attempted": False}, "ubo_actor_reenrolled_after_optout")


def enroll(client, state, mark):
    mark("signed-addons")
    all_addons_active(client)
    mark("initial-stock-selection")
    require(ubo_selection(client) == STOCKS, "ubo_actor_initial_stock_selection")
    mark("configure-consent")
    configure_consent(client, ORIGIN)
    # Independent positive endpoint control before adding a supplementary list.
    mark("positive-baseline")
    set_control_addons(client, False)
    load_case(client, ORIGIN, "baseline")
    client.script("document.querySelector('#save-consent').click(); return true;")
    baseline = recorded_case(client, state, "baseline", values(PURPOSES), False)
    mark("reenable-addons")
    set_control_addons(client, True)
    all_addons_active(client)
    mark("closed-actor-enroll")
    changed = actor(client, "enroll")
    fresh_change(changed, True)
    require(changed["state"] == "enrolled" and changed["attempted"] is True)
    mark("verify-enrolled-selection")
    require(ubo_selection(client, LIST_URL) == sorted(STOCKS + [LIST_URL]))
    mark("recorded-refusal")
    load_case(client, ORIGIN, "refusal")
    refusal = recorded_case(client, state, "refusal", values(), True)
    require(state.filter_reads > 0, "ubo_actor_probe_not_fetched")
    return {"baseline": baseline, "enrollment": changed, "refusal": refusal,
            "fixed_filter_reads": state.filter_reads}


def remove_through_ui(client):
    require(ubo_selection(client, LIST_URL) == sorted(STOCKS + [LIST_URL]))
    require(client.script("""
      const entry = Array.from(document.querySelectorAll('#lists .listEntry[data-role="leaf"]'))
        .find(entry => entry.dataset.key === arguments[0]);
      if (!entry || !entry.classList.contains('external')) return false;
      entry.querySelector('.remove').click(); return entry.classList.contains('toRemove');
    """, [LIST_URL]))
    until(client, "return !document.querySelector('#buttonApply').classList.contains('disabled');")
    client.script("document.querySelector('#buttonApply').click(); return true;")
    until(client, """
      return document.querySelector('#buttonApply').classList.contains('disabled')
        && !Array.from(document.querySelectorAll('#lists .listEntry[data-role="leaf"]'))
          .some(entry => entry.dataset.key === arguments[0]);
    """, [LIST_URL])
    check_probe_removed(client, ORIGIN, STOCKS)


def reinstall_original(client, stage):
    client.command("Marionette:SetContext", {"value": "chrome"})
    result = client.command("WebDriver:ExecuteAsyncScript", {
        "script": r"""
        const [packagePath, done] = arguments;
        (async () => {
          const {AddonManager} = ChromeUtils.importESModule("resource://gre/modules/AddonManager.sys.mjs");
          const id = "uBlock0@raymondhill.net";
          const addon = await AddonManager.getAddonByID(id);
          await addon.uninstall();
          if (await AddonManager.getAddonByID(id)) throw new Error("uninstall_failed");
          const file = Cc["@mozilla.org/file/local;1"].createInstance(Ci.nsIFile);
          file.initWithPath(packagePath);
          const install = await AddonManager.getInstallForFile(file);
          if (install.addon.id !== id || install.addon.version !== "1.75.0"
              || install.addon.signedState !== AddonManager.SIGNEDSTATE_SIGNED) throw new Error("wrong_package");
          await install.install();
          return true;
        })().then(done, () => done(false));
        """, "args": [str(stage / "distribution/extensions" / (UBLOCK + ".xpi"))],
        "newSandbox": True, "sandbox": "system",
    })["value"]
    require(result is True, "ubo_actor_original_reinstall_failed")


def campaign(stage, work, kind, report):
    work.mkdir(mode=0o700)
    for name in ("profile", "config", "cache", "runtime", "tmp"):
        (work / name).mkdir(mode=0o700)
    report["phase"] = kind + "-module-staging"
    staged_hashes = stage_profile_modules(work / "profile", report["modules_sha256"])
    report.setdefault("staged_modules_sha256", {})[kind] = staged_hashes
    (work / "profile/prefs.js").write_text(
        'user_pref("remote.prefs.recommended", false);\n'
        'user_pref("browser.volparossa.uboProof.enabled", true);\n')
    result = {}
    with serve_fixture(port=PORT) as (origin, state):
        require(origin == ORIGIN)

        def mark(phase):
            require(phase in (
                "signed-addons", "initial-stock-selection", "configure-consent", "positive-baseline",
                "reenable-addons", "closed-actor-enroll", "verify-enrolled-selection", "recorded-refusal",
                "observe-reopened-enrollment", "user-remove", "closed-actor-revoke", "declined-reenrollment",
                "unblocked-probe", "restarted-optout", "original-reinstall", "reinstalled-optout",
            ))
            report["phase"] = kind + "-" + phase

        def checked(phase, callback):
            report["phase"] = kind + "-" + phase
            verify_profile_modules(work / "profile", report["modules_sha256"])
            def exercise(client):
                client.command("Marionette:SetContext", {"value": "chrome"})
                initial = snapshot(client, list(load_defaults()) + ["privacy.trackingprotection.enabled"])
                value = callback(client)
                all_addons_active(client)
                require(snapshot(client, list(initial)) == initial, "ubo_actor_changed_privacy_defaults")
                return value
            try:
                return run_browser(stage, work, work / "profile", phase, exercise=exercise)["exercise"]
            finally:
                verify_profile_modules(work / "profile", report["modules_sha256"])

        result["initial"] = checked("enroll", lambda client: enroll(client, state, mark))

        def remove(client):
            mark("observe-reopened-enrollment")
            require(actor(client, "observe") == {
                "state": "enrolled", "selected": True, "imported": True, "stocks": 10,
            }, "ubo_actor_enrollment_not_persisted")
            if kind == "user":
                mark("user-remove")
                remove_through_ui(client)
                changed = actor(client, "observe")
                require(changed == {"state": "opted-out", "selected": False, "imported": False, "stocks": 10})
            else:
                mark("closed-actor-revoke")
                changed = actor(client, "revoke")
                fresh_change(changed, False)
                require(changed["state"] == "opted-out")
            mark("declined-reenrollment")
            declined(actor(client, "enroll"))
            check_probe_removed(client, ORIGIN, STOCKS)
            mark("unblocked-probe")
            return {"removal": changed, "probe": unblocked_probe(client, ORIGIN, state, "list-removed")}

        result["remove"] = checked("remove", remove)

        def restarted(client):
            mark("restarted-optout")
            declined(actor(client, "enroll"))
            check_probe_removed(client, ORIGIN, STOCKS)
            probe = unblocked_probe(client, ORIGIN, state, "list-removal-restart")
            if kind == "user":
                mark("original-reinstall")
                reinstall_original(client, stage)
                all_addons_active(client)
                mark("reinstalled-optout")
                declined(actor(client, "enroll"))
                check_probe_removed(client, ORIGIN, STOCKS)
            return {"probe": probe, "optout_survived_restart": True,
                    "optout_survived_original_reinstall": kind == "user"}

        result["restarted"] = checked("restart", restarted)
    return result


def cleanup(work):
    """Idempotent parent/child cleanup of only this fresh fixture's exact names."""
    removed = True
    for kind in ("user", "authority"):
        directory = work / kind
        if not directory.exists():
            continue
        require(not directory.is_symlink() and directory.is_dir())
        removed = remove_profile(directory) and removed
        for name in ("enroll.log", "remove.log", "restart.log"):
            path = directory / name
            if path.exists():
                require(not path.is_symlink() and path.is_file())
                path.unlink()
    return remove_profile(work) and removed


def phase_trace(work):
    result = {}
    for kind in ("user", "authority"):
        path = work / kind / "profile" / TRACE_NAME
        if not path.exists():
            continue
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and 0 < info.st_size <= 128, "ubo_actor_invalid_phase_file")
        value = json.loads(path.read_text())
        require(type(value) is dict and set(value) == {"schema", "phase"}
                and type(value["schema"]) is int and value["schema"] == 1
                and value["phase"] in TRACE_PHASES, "ubo_actor_invalid_phase_file")
        result[kind] = value["phase"]
    return result


def inside(stage, work, metadata, host_netns):
    validate_isolation(stage, work, host_netns)
    report = {"schema": 1, "passed": False, "scope": SCOPE, "phase": "initial",
              "runtime_sha256": metadata["runtime_sha256"], "modules_sha256": {name: digest(ROOT / name) for name in MODULES},
              "module_layout": "profile/chrome/volparossa-ubo-proof", "staged_modules_sha256": {},
              "host_read_only": True, "interfaces": ["lo"], "campaigns": {}}
    try:
        for kind in ("user", "authority"):
            report["campaigns"][kind] = campaign(stage, work / kind, kind, report)
        validate_retained_bundle(stage, metadata)  # Signed files must still be identical.
        report["passed"] = True
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        report["failure"] = type(error).__name__
        if isinstance(error, ActorFailure):
            report["failure"] = "ValueError"
            report["failure_phase"] = error.phase
            report["closed_failure"] = error.reason
    finally:
        try:
            report["actor_phase"] = phase_trace(work)
        except (OSError, ValueError, KeyError):
            report["actor_phase"] = {"invalid": True}
            report["passed"] = False
        removed = cleanup(work)
        report["temporary_profiles_removed"] = removed
        report["passed"] = report["passed"] and removed
        with (work / "report.json").open("x") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
    require(report["passed"], "ubo_actor_smoke_failed_see_report")


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, help="Existing explicitly pinned read-only staged Firefox; never copied or downloaded")
    parser.add_argument("--output", required=True)
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--host-netns", help=argparse.SUPPRESS)
    args = parser.parse_args()
    stage, work = Path(args.stage).resolve(strict=True), build_path(args.output)
    require(not stage.is_relative_to(work) and not work.is_relative_to(stage))
    metadata = validate_stage(stage)
    validate_retained_bundle(stage, metadata)
    if args.inside:
        inside(stage, work, metadata, args.host_netns)
        return
    prepare_output(work)
    mounts = isolated_browser_home(work)
    print(f"uBO actor proof changes only {work}; original runtime read-only; new loopback-only network.", flush=True)
    try:
        subprocess.run([
            "/usr/bin/bwrap", "--die-with-parent", "--unshare-user", "--unshare-net", "--unshare-pid",
            "--ro-bind", "/", "/", *mounts, "--bind", str(work), str(work),
            "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev", "--",
            sys.executable, "-B", str(Path(__file__).resolve()), "--stage", str(stage),
            "--output", str(work), "--inside", "--host-netns", os.readlink("/proc/self/ns/net"),
        ], check=True, timeout=480)
    finally:
        # Timeout kills/waits for bwrap; its disposable PID namespace kills its
        # descendants. Do not leave profiles/raw logs when the child cannot report.
        removed = cleanup(work)
        with (work / "outer-cleanup.json").open("x") as stream:
            json.dump({"schema": 1, "temporary_profiles_removed": removed}, stream)
            stream.write("\n")
        require(removed, "ubo_actor_cleanup_incomplete")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        print(f"uBO actor smoke failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
