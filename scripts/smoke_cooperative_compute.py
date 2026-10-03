#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Real Gecko -> existing cooperative core; exact public fixture, no fake peer or download.

The independent core observer authorizes Submit only after it has verified that
prefilling the actual panel created no public work. It verifies the first task's
real two-peer execution before authorizing a second task's scoped cancellation.
Only hashes and closed result metadata leave the browser. The core separately
proves protected transport, actual workers, original receipts and cleanup.
"""

import argparse
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import sys
import time

from smoke_compute_model import (RUNTIME, RUNTIME_SHA256, private_directory, require,
    remove_browser_files, startup_observation, validate_endpoint, validate_stage)
from smoke_privacy import Marionette
from stage_firefox import ROOT, build_path, digest, isolated_browser_home, validate_isolated_browser_home

SOURCE_FILES = ("scripts/smoke_cooperative_compute.py", "scripts/smoke_compute_model.py",
    "scripts/smoke_privacy.py", "scripts/stage_firefox.py", "defaults/privacy.json",
    "integration/VolparossaCooperativeCompute.sys.mjs", "integration/VolparossaCooperativePanel.sys.mjs")
PHASES = frozenset(("wrapper-launch", "namespace-validation", "browser-start", "marionette-connect",
    "sidebar", "prefill", "awaiting-consent", "first-task", "result-received", "result-verified",
    "cancel-task", "cancel-admitted", "cancel-authorized", "cancelled", "panel-cleanup",
    "result-validation", "browser-stop", "complete"))
ERRORS = frozenset(("CHECK_FAILED", "OS_ERROR", "SUBPROCESS_FAILED", "INTERRUPTED", "SCRIPT_FAILED",
    "busy", "invalid_request", "handshake_required", "no_such_task", "execution_failed",
    "cleanup_unconfirmed", "unavailable", "not_configured", "invalid_response", "invalid_question",
    "invalid_context", "public_consent_required", "invalid_license", "deadline_exceeded", "storage_bound"))
MARKERS = {"pre-consent.json": "prefill_without_dispatch", "authorize.json": "allow_explicit_public_submit",
    "result-received.json": "public_result_received", "result-verified.json": "public_result_verified",
    "cancel-admitted.json": "public_cancel_target_admitted", "cancel-authorize.json": "allow_scoped_public_cancel"}


def public_input(path):
    require(path.is_absolute() and path.resolve() == path)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid in (0, os.getuid())
            and not info.st_mode & 0o022 and info.st_nlink == 1 and info.st_size <= 16384)
    value = json.loads(path.read_bytes())
    require(type(value) is dict and set(value) == {"question", "context", "license"})
    for name, maximum in (("question", 512), ("context", 4096)):
        text = value[name]
        require(type(text) is str and text.strip() and "\0" not in text and len(text.encode()) <= maximum)
    require(value["license"] in ("GPL-3.0-only", "CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0"))
    return value


def status(work, phase, failure=None):
    require(phase in PHASES and (failure is None or failure in ERRORS))
    private_directory(work)
    fd = os.open(work / "browser-status.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as target:
        json.dump(dict(version=1, phase=phase, failure=failure), target)


def failed_status(work, error):
    value = json.loads((work / "browser-status.json").read_bytes())
    require(set(value) == {"version", "phase", "failure"} and value["version"] == 1
            and value["phase"] in PHASES and (value["failure"] is None or value["failure"] in ERRORS))
    if value["failure"] is None:
        code = "INTERRUPTED" if isinstance(error, KeyboardInterrupt) else "OS_ERROR" if isinstance(error, OSError) else \
            "SUBPROCESS_FAILED" if isinstance(error, subprocess.SubprocessError) else "CHECK_FAILED"
        status(work, value["phase"], code)


def check_result(value):
    require(type(value) is dict and set(value) == {"prefill_no_dispatch", "explicit_consent", "text_only",
        "scoped_cancel_confirmed", "first_task_id", "cancel_task_id", "first_result", "removed"})
    require(all(value[key] is True for key in ("prefill_no_dispatch", "explicit_consent", "text_only",
                                              "scoped_cancel_confirmed", "removed")))
    require(all(re.fullmatch(r"[0-9a-f]{32}", value[key]) for key in ("first_task_id", "cancel_task_id"))
            and value["first_task_id"] != value["cancel_task_id"])
    answer = value["first_result"]
    require(type(answer) is dict and set(answer) == {"source_manifest_id", "package_count", "total_parts",
        "synthesis_levels", "provider_keys", "selected_provider_keys", "output_sha256", "joining",
        "answer_complete", "execution_complete", "remote_cleanup_confirmed", "retained_public_receipts"})
    require(all(answer[key] is True for key in ("answer_complete", "execution_complete",
        "remote_cleanup_confirmed", "retained_public_receipts")))
    require(answer["joining"] == "hierarchical_peer_synthesis"
            and all(type(answer[key]) is int and answer[key] >= minimum for key, minimum in
                    (("package_count", 1), ("total_parts", 2), ("synthesis_levels", 1))))
    for name in ("provider_keys", "selected_provider_keys"):
        keys = answer[name]
        require(type(keys) is list and 2 <= len(keys) <= 4 and len(set(keys)) == len(keys)
                and all(type(key) is str and re.fullmatch(r"[0-9a-f]{64}", key) and key != "0"*64 for key in keys))
    require(set(answer["provider_keys"]) <= set(answer["selected_provider_keys"]))
    require(all(type(answer[key]) is str and re.fullmatch(r"[0-9a-f]{64}", answer[key])
                for key in ("source_manifest_id", "output_sha256")))


SCRIPT = r"""
const [moduleRoot, socketPath, work, input, done] = arguments;
let phase = "sidebar";
let failure = null;
const file = path => {
  const f = Cc["@mozilla.org/file/local;1"].createInstance(Ci.nsIFile);
  f.initWithPath(path); return f;
};
const write = (name, value, exclusive = true) => {
  const f = file(`${work}/${name}`);
  // Gecko's IsSymlink uses lstat and throws for an absent path. A new marker
  // needs atomic PR_CREATE_FILE|PR_EXCL, which also refuses dangling symlinks.
  // Only the already-created, private status file may be replaced.
  if (!exclusive && (!f.exists() || f.isSymlink() || !f.isFile() || f.permissions !== 0o600)) {
    throw new Error("owned_file");
  }
  const stream = Cc["@mozilla.org/network/file-output-stream;1"].createInstance(Ci.nsIFileOutputStream);
  stream.init(f, 0x02 | 0x08 | (exclusive ? 0x80 : 0x20), 0o600, 0);
  const data = JSON.stringify(value) + "\n";
  try { if (stream.write(data, data.length) !== data.length) throw new Error("write_failed"); }
  finally { stream.close(); }
};
const status = (next, code = null) => {
  if (failure === null) phase = next;
  failure ??= code;
  write("browser-status.json", {version: 1, phase, failure}, false);
};
const marker = (name, event) => write(name, {version: 1, event});
const check = ok => { if (!ok) throw new Error("browser_cooperative_check"); };
(async () => {
  const {setTimeout} = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const readMarker = (name, expected) => {
    const f = file(`${work}/${name}`);
    if (!f.exists()) return false;
    check(!f.isSymlink() && f.isFile() && f.fileSize <= 512);
    const stream = Cc["@mozilla.org/network/file-input-stream;1"].createInstance(Ci.nsIFileInputStream);
    stream.init(f, 0x01, 0, 0);
    const reader = Cc["@mozilla.org/intl/converter-input-stream;1"].createInstance(Ci.nsIConverterInputStream);
    reader.init(stream, "UTF-8", 512, 0);
    let part = {}, data = "";
    try { while (reader.readString(513 - data.length, part)) { data += part.value; check(data.length <= 512); } }
    finally { reader.close(); }
    const parsed = JSON.parse(data);
    check(Object.keys(parsed).sort().join() === "event,version" && parsed.version === 1 && parsed.event === expected);
    return true;
  };
  const waitFor = async (condition, seconds) => {
    const end = Date.now() + seconds * 1000;
    while (!condition()) { check(Date.now() < end); await sleep(50); }
  };
  const waitMarker = (name, event, seconds = 120) => waitFor(() => readMarker(name, event), seconds);
  const sha256 = text => {
    const bytes = new TextEncoder().encode(text);
    const hash = Cc["@mozilla.org/security/hash;1"].createInstance(Ci.nsICryptoHash);
    hash.init(Ci.nsICryptoHash.SHA256); hash.update(bytes, bytes.length);
    return Array.from(hash.finish(false), ch => ch.charCodeAt(0).toString(16).padStart(2, "0")).join("");
  };
  Services.io.getProtocolHandler("resource").QueryInterface(Ci.nsIResProtocolHandler)
    .setSubstitution("volparossa-cooperative-proof", Services.io.newFileURI(file(moduleRoot)));
  const {VolparossaCooperativeCompute} = ChromeUtils.importESModule(
    "resource://volparossa-cooperative-proof/VolparossaCooperativeCompute.sys.mjs");
  const {createVolparossaCooperativePanel} = ChromeUtils.importESModule(
    "resource://volparossa-cooperative-proof/VolparossaCooperativePanel.sys.mjs");
  const connect = VolparossaCooperativeCompute.connect;
  const submit = VolparossaCooperativeCompute.prototype.submit;
  let connections = 0, submissions = 0, admitted = 0, firstResult = null, firstTextHash = null, cancelled = false;
  const ids = [];
  VolparossaCooperativeCompute.connect = async function(...args) {
    connections++; return connect.apply(this, args);
  };
  VolparossaCooperativeCompute.prototype.submit = function(input) {
    check(input.public_content === true && input.rights_confirmed === true);
    submissions++;
    const count = submissions;
    const onAdmitted = input.onAdmitted;
    const task = submit.call(this, {...input, onAdmitted: () => {
      admitted++; onAdmitted();
      if (count === 2) {
        status("cancel-admitted"); marker("cancel-admitted.json", "public_cancel_target_admitted");
      }
    }});
    ids.push(task.id);
    task.finished.then(result => {
      if (count === 1) {
        firstTextHash = sha256(result.output.text);
        firstResult = Object.fromEntries(["source_manifest_id", "package_count", "total_parts", "synthesis_levels",
          "provider_keys", "selected_provider_keys", "joining", "answer_complete", "execution_complete",
          "remote_cleanup_confirmed", "retained_public_receipts"].map(key => [key, result[key]]));
        firstResult.output_sha256 = firstTextHash;
      }
    }, error => {
      if (count === 2 && error.code === "cancelled") cancelled = true;
      else {
        const known = new Set(["busy", "invalid_request", "handshake_required", "no_such_task", "execution_failed",
          "cleanup_unconfirmed", "unavailable", "not_configured", "invalid_response", "invalid_question",
          "invalid_context", "public_consent_required", "invalid_license", "deadline_exceeded", "storage_bound"]);
        status(phase, known.has(error?.code) ? error.code : "SCRIPT_FAILED");
      }
    });
    return task;
  };
  const win = Services.wm.getMostRecentWindow("navigator:browser");
  Services.prefs.setStringPref("browser.volparossa.compute.public_socket", socketPath);
  Services.prefs.setBoolPref("browser.ml.chat.enabled", true);
  Services.prefs.setStringPref("browser.ml.chat.provider", "");
  await win.SidebarController.promiseInitialized;
  check(await win.SidebarController.show("viewGenaiChatSidebar"));
  const doc = win.document.getElementById("sidebar").contentDocument;
  check(doc.documentURI === "chrome://browser/content/genai/chat.html");
  const panel = createVolparossaCooperativePanel(doc, doc.body);
  const node = name => doc.getElementById(`volparossa-public-${name}`);
  let observed;
  const approve = () => {
    node("license").value = input.license;
    node("license").dispatchEvent(new doc.defaultView.Event("change"));
    for (const name of ["rights", "consent"]) {
      node(name).checked = true; node(name).dispatchEvent(new doc.defaultView.Event("change"));
    }
    check(!node("submit").disabled); node("submit").click();
  };
  try {
    status("prefill");
    await panel.ask(input.question, input.context);
    check(connections === 0 && submissions === 0 && node("submit").disabled &&
      !node("rights").checked && !node("consent").checked && node("license").value === "");
    marker("pre-consent.json", "prefill_without_dispatch");
    status("awaiting-consent");
    await waitMarker("authorize.json", "allow_explicit_public_submit");
    check(connections === 0 && submissions === 0);
    status("first-task"); approve();
    await waitFor(() => ["complete", "incomplete", "error"].includes(panel.element.dataset.state), 1800);
    check(panel.element.dataset.state === "complete" && firstResult !== null && submissions === 1 && admitted === 1);
    check(firstResult.provider_keys.length >= 2 && firstResult.total_parts >= 2 &&
      firstResult.synthesis_levels >= 1 && firstResult.joining === "hierarchical_peer_synthesis");
    const rendered = node("answer");
    check(sha256(rendered.textContent) === firstTextHash && rendered.childElementCount === 0 &&
      panel.element.querySelectorAll("script").length === 0 && panel.element.isConnected);
    observed = {prefill_no_dispatch: true, explicit_consent: true, text_only: true,
      first_task_id: ids[0], first_result: firstResult};
    status("result-received"); marker("result-received.json", "public_result_received");
    await waitMarker("result-verified.json", "public_result_verified");
    status("result-verified");
    await panel.ask(input.question, input.context);
    check(submissions === 1 && node("submit").disabled && !node("rights").checked && !node("consent").checked);
    status("cancel-task"); approve();
    await waitMarker("cancel-authorize.json", "allow_scoped_public_cancel", 600);
    check(submissions === 2 && admitted === 2 && !node("cancel").disabled);
    status("cancel-authorized"); node("cancel").click();
    await waitFor(() => ["cancelled", "error", "complete", "incomplete"].includes(panel.element.dataset.state), 120);
    check(cancelled && panel.element.dataset.state === "cancelled" && node("answer").textContent === "");
    observed.cancel_task_id = ids[1]; observed.scoped_cancel_confirmed = true;
    status("cancelled");
  } catch (error) {
    // Record the original stage before cleanup; finally must not erase it.
    status(phase, "SCRIPT_FAILED");
    throw error;
  } finally {
    status("panel-cleanup"); panel.destroy();
    VolparossaCooperativeCompute.connect = connect;
    VolparossaCooperativeCompute.prototype.submit = submit;
    win.SidebarController.hide();
  }
  observed.removed = !doc.getElementById("volparossa-cooperative-compute");
  done(observed);
})().catch(() => { try { status(phase, "SCRIPT_FAILED"); } catch {} done({failed: true}); });
"""


def inside(args, stage, work, metadata, fixture):
    status(work, "namespace-validation")
    require(args.host_netns and os.readlink("/proc/self/ns/net") != args.host_netns)
    require(all(os.statvfs(path).f_flag & os.ST_RDONLY for path in (Path("/"), ROOT, stage, args.input)))
    validate_isolated_browser_home(work)
    links = json.loads(subprocess.check_output(["/usr/bin/ip", "-j", "link", "show"], text=True))
    require([link["ifname"] for link in links] == ["lo"])
    endpoint = validate_endpoint(args.socket)
    for name in ("profile", "config", "cache", "runtime", "tmp"):
        (work / name).mkdir(mode=0o700)
    profile = work / "profile"
    (profile / "prefs.js").write_text('user_pref("remote.prefs.recommended", false);\n')
    environment = dict(os.environ)
    environment.update(MOZ_NO_REMOTE="1", MOZ_CRASHREPORTER_DISABLE="1", XDG_CONFIG_HOME=str(work / "config"),
        XDG_CACHE_HOME=str(work / "cache"), XDG_RUNTIME_DIR=str(work / "runtime"), TMPDIR=str(work / "tmp"))
    for name in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "DBUS_SYSTEM_BUS_ADDRESS"):
        environment.pop(name, None)
    browser, client, report = None, None, None
    started = time.monotonic()
    deadline = started + 40
    startup_saved = False
    try:
        with (work / "firefox.log").open("xb") as log:
            status(work, "browser-start")
            browser = subprocess.Popen([str(stage / metadata["executable"]), "--headless", "--no-remote",
                "--new-instance", "--profile", str(profile), "--marionette", "--remote-allow-system-access",
                "about:blank"], cwd=work, env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            status(work, "marionette-connect")
            while time.monotonic() < deadline:
                require(browser.poll() is None)
                try:
                    connection = socket.create_connection(("127.0.0.1", 2828), timeout=1)
                    connection.settimeout(2470)
                    client = Marionette(connection)
                    break
                except (ConnectionRefusedError, TimeoutError):
                    time.sleep(0.2)
            require(client is not None)
            startup_observation(work, browser, started, deadline, True)
            startup_saved = True
            client.command("WebDriver:NewSession", {"capabilities": {"alwaysMatch": {}}})
            client.command("Marionette:SetContext", {"value": "chrome"})
            client.command("WebDriver:SetTimeouts", {"script": 2400000})
            observed = client.command("WebDriver:ExecuteAsyncScript", {"script": SCRIPT,
                "args": [str(ROOT / "integration"), str(args.socket), str(work), fixture],
                "newSandbox": True, "sandbox": "system"})["value"]
            require(json.loads((work / "browser-status.json").read_bytes())["failure"] is None)
            status(work, "result-validation")
            check_result(observed)
            require(validate_endpoint(args.socket) == endpoint)
            report = dict(version=1, kind="real-gecko-cooperative-public-peers", passed=True,
                core_revision=args.core_revision, observed=observed, fixture_sha256=digest(args.input),
                runtime_version=metadata["version"], runtime_source_stamp=metadata["source_stamp"],
                runtime_sha256=metadata["runtime_sha256"], interfaces=["lo"], host_read_only=True,
                same_owner_socket_mode=0o600,
                browser_source_sha256={name: digest(ROOT / name) for name in SOURCE_FILES},
                scope=dict(native_provider_selector_proven=False, firefox157_build_proven=False,
                    standalone_model_authenticity_proven=False, general_answer_quality_proven=False,
                    confidential_peer_inference_proven=False, raw_context_in_report=False, raw_answer_in_report=False))
            status(work, "browser-stop")
            client.command("Marionette:Quit", {"flags": ["eAttemptQuit"]})
            require(browser.wait(timeout=20) == 0)
        require((work / "firefox.log").stat().st_size <= 1048576)
    except BaseException as error:
        if not startup_saved:
            startup_observation(work, browser, started, deadline, client is not None)
        failed_status(work, error)
        raise
    finally:
        if client:
            client.socket.close()
        if browser is not None and browser.poll() is None:
            os.killpg(browser.pid, signal.SIGTERM)
            try:
                browser.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(browser.pid, signal.SIGKILL)
                browser.wait(timeout=5)
        remove_browser_files(work)
    require(report is not None)
    report["temporary_browser_data_removed"] = True
    (work / "report.json").write_text(json.dumps(report, sort_keys=True) + "\n")
    status(work, "complete")


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("stage", "output", "socket", "input"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--barrier-directory", type=Path)
    parser.add_argument("--core-revision", required=True)
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--host-netns", help=argparse.SUPPRESS)
    args = parser.parse_args()
    require(re.fullmatch(r"[0-9a-f]{40}", args.core_revision))
    stage, work = build_path(args.stage), build_path(args.output)
    require(not stage.is_relative_to(work) and not work.is_relative_to(stage)
            and (args.barrier_directory is None or args.barrier_directory == work)
            and not args.socket.is_relative_to(work) and not args.input.is_relative_to(work))
    fixture = public_input(args.input)
    validate_endpoint(args.socket)
    metadata = validate_stage(stage)
    if args.inside:
        private_directory(work)
        inside(args, stage, work, metadata, fixture)
        return
    require(not work.exists() and not work.is_symlink())
    work.mkdir(mode=0o700)
    status(work, "wrapper-launch")
    try:
        mounts = isolated_browser_home(work)
        completed = subprocess.run(["/usr/bin/bwrap", "--die-with-parent", "--unshare-user", "--unshare-net",
            "--ro-bind", "/", "/", *mounts, "--bind", str(work), str(work), "--chdir", str(work),
            "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev", "--", sys.executable,
            str(Path(__file__).resolve()), *sys.argv[1:], "--inside", "--host-netns", os.readlink("/proc/self/ns/net")],
            cwd=work, timeout=2520, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        require(completed.returncode == 0 and (work / "report.json").is_file())
    except BaseException as error:
        failed_status(work, error)
        raise
    finally:
        remove_browser_files(work)
    print("COOPERATIVE_BROWSER_PROOF_COMPLETE")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt):
        print("COOPERATIVE_BROWSER_PROOF_FAILED", file=sys.stderr)
        raise SystemExit(1) from None
