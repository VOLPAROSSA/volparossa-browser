#!/usr/bin/env python3
"""Real Gecko panel -> existing private-serve socket; never create a protocol peer.

This is the browser half of a combined disposable-KVM proof, not standalone proof
that a service really ran a model. The core runner must retain its actual pinned
model, input/mount/isolation, owner-control and process-lifetime checks. It starts
this script instead of its final Python Submit, waits for OUTPUT/admitted.json,
then runs its existing observer against SERVICE_PID. That observer writes the
exact current-job isolation JSON passed as --observer-file before the answer.
The original synthetic question/context below match the core v2 private fixture.

Only a fixed synthetic canary and bounded boolean/numeric evidence are exported;
neither the private prompt nor the model's answer is written to a browser report.
The receive boundary is decoded-result-before-panel, not first socket-frame byte.
Full Firefox 157/native provider-selector integration remains unproven here.

Minimal browser-source manifest (use one exact Git commit in the core runner):
  scripts/{smoke_compute_model,smoke_privacy,stage_firefox}.py
  integration/{VolparossaCompute,VolparossaComputePanel}.sys.mjs
  defaults/privacy.json
For staging, use --without-extensions; this proof does not retest addon bundles.
Guest tools: Python 3 stdlib, bubblewrap, iproute2, and Firefox's Debian Depends.
The runtime is explicit input; this script downloads/installs nothing. The
following Debian security pin came from installed signed apt metadata; verify
the entire .deb before workspace-only extraction/staging in the disposable VM.
"""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time

from smoke_privacy import Marionette
from stage_firefox import MARKER, ROOT, build_path, digest

RUNTIME = {
    "version": "140.16.0", "source_stamp": "d864999404b3032f682d74ccc60d1ce38c9ce609",
    "package": "firefox-esr_140.16.0esr-1~deb13u1_amd64.deb",
    "bytes": 71994716,
    "sha256": "e32aeabcab2e74fe112332fad10f7d9630e14cd6f4564a596a71073018d24508",
    "url": "https://security.debian.org/debian-security/pool/updates/main/f/firefox-esr/"
           "firefox-esr_140.16.0esr-1~deb13u1_amd64.deb",
}
RUNTIME_SHA256 = {
    "firefox-esr": "22bb2d84f2289622e65fbcde266a30952e6ac64d27a2a1af75cea7229f2e6b14",
    "libxul.so": "62cc326204668a2dae453c9e89d9c200e513c9e3ad0772b164091bf1e5333fb4",
    "omni.ja": "c0d453127f9c1310dad39cc3f6971acfe59cdf561fec6918b129ee0c780c6e70",
    "browser/omni.ja": "757f1f47bfbde5603e08cdb7ddc83bd27e9b26695bf843d640df7984842fffd7",
}
QUESTION = "What is the test identifier in the note? Answer with only the identifier."
CONTEXT = "Synthetic private test note: The test identifier is {}. This note contains no real secrets."
PROFILE = "smollm2-360m-v1"
SOURCE_FILES = (
    "scripts/smoke_compute_model.py", "scripts/smoke_privacy.py", "scripts/stage_firefox.py",
    "integration/VolparossaCompute.sys.mjs", "integration/VolparossaComputePanel.sys.mjs",
    "defaults/privacy.json",
)
SCOPE = {
    "firefox157_build_proven": False, "native_provider_selector_proven": False,
    "standalone_model_authenticity_proven": False, "general_answer_quality_proven": False,
    "public_peer_execution_proven": False, "private_prompt_exported": False,
    "raw_model_answer_exported": False,
}
STATUS_PHASES = frozenset((
    "wrapper-launch", "namespace-validation", "browser-start", "marionette-connect",
    "marionette-session", "script-start", "module-import", "sidebar-initialize",
    "sidebar-show", "sidebar-document", "panel-create", "broker-connect",
    "capabilities-received", "submit-admitted", "result-received", "result-cleanup-verified",
    "panel-render-check", "panel-cleanup", "script-complete", "result-validation",
    "browser-stop", "private-log-check", "report-write", "complete",
))
STATUS_ERRORS = frozenset((
    "CHECK_FAILED", "OS_ERROR", "SUBPROCESS_FAILED", "RUNTIME_FAILED", "INTERRUPTED",
    "SCRIPT_FAILED", "UNCLASSIFIED", "BROKER_BUSY", "BROKER_INVALID_REQUEST",
    "BROKER_HANDSHAKE_REQUIRED", "BROKER_NO_SUCH_TASK", "BROKER_CANCELLED",
    "BROKER_EXECUTION_FAILED", "BROKER_CLEANUP_UNCONFIRMED", "MODULE_UNAVAILABLE",
    "MODULE_NOT_CONFIGURED", "MODULE_INVALID_RESPONSE", "MODULE_INVALID_QUESTION",
    "MODULE_INVALID_CONTEXT", "MODULE_CLEANUP_UNCONFIRMED",
))
STATUS_NAME = "browser-status.json"


def check_status(value):
    require(type(value) is dict and set(value) == {"version", "phase", "failure"}
            and type(value["version"]) is int and value["version"] == 1
            and value["phase"] in STATUS_PHASES
            and (value["failure"] is None or value["failure"] in STATUS_ERRORS))
    return value


def status(work, phase, failure=None):
    value = check_status(dict(version=1, phase=phase, failure=failure))
    private_directory(work)
    fd = os.open(work / STATUS_NAME, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as target:
        json.dump(value, target, separators=(",", ":"))
        target.write("\n")


def failed_status(work, error):
    # Preserve the innermost fixed phase, including a JS/module/broker failure.
    with (work / STATUS_NAME).open("rb") as source:
        data = source.read(4097)
    require(len(data) <= 4096)
    value = check_status(json.loads(data))
    if value["failure"] is None:
        code = ("INTERRUPTED" if isinstance(error, KeyboardInterrupt) else
                "SUBPROCESS_FAILED" if isinstance(error, subprocess.SubprocessError) else
                "OS_ERROR" if isinstance(error, OSError) else
                "CHECK_FAILED" if isinstance(error, (ValueError, KeyError, TypeError)) else
                "RUNTIME_FAILED" if isinstance(error, RuntimeError) else "UNCLASSIFIED")
        status(work, value["phase"], code)


def require(condition):
    if not condition:
        raise ValueError("combined_browser_smoke_check_failed")


def private_directory(path):
    require(path.is_absolute() and path.resolve() == path)
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o700)


def validate_endpoint(path):
    require(path.is_absolute() and path.resolve() == path and len(os.fsencode(path)) <= 107)
    private_directory(path.parent)
    info = path.lstat()
    require(stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o600)
    return info.st_dev, info.st_ino


def validate_stage(stage):
    metadata = json.loads((stage / MARKER).read_text())
    require(metadata["schema"] == 1 and metadata["version"] == RUNTIME["version"]
            and metadata["source_stamp"] == RUNTIME["source_stamp"]
            and metadata["kind"] == "installed-runtime-privacy-overlay-not-a-firefox-source-build"
            and metadata["executable"] == "firefox-esr")
    require(metadata["runtime_sha256"] == RUNTIME_SHA256)
    for name, expected in metadata["runtime_sha256"].items():
        path = stage / name
        require(path.resolve().is_relative_to(stage) and digest(path) == expected)
    require(digest(ROOT / "defaults/privacy.json") == metadata["defaults_sha256"]
            and digest(stage / "volparossa.cfg") == metadata["autoconfig_sha256"])
    return metadata


def check_result(value, canary):
    """Exact sanitized result shape; a synthetic dict test is never inference proof."""
    require(type(value) is dict and set(value) == {
        "capabilities", "admitted", "answer", "boundary", "panel", "removed",
    })
    require(value["capabilities"] == {"visibility": "private_local", "local_only": True,
        "model_profile": PROFILE, "network_access": False, "public_cache": False,
        "training": False, "cloud_fallback": False})
    require(type(value["admitted"]) is int and value["admitted"] == 1 and value["removed"] is True)
    answer = value["answer"]
    require(type(answer) is dict and set(answer) == {"answer_status", "complete", "canary_present", "generated_tokens"}
            and answer["answer_status"] == "eos" and answer["complete"] is True
            and answer["canary_present"] is True and type(answer["generated_tokens"]) is int
            and 1 <= answer["generated_tokens"] <= 256)
    require(value["boundary"] == {"point": "decoded_result_before_panel_render",
        "ephemeral_children": 0, "observed_worker_lifetimes_ended": True})
    require(value["panel"] == {"actual_sidebar_document": True, "connected": True,
        "canary_rendered": True, "text_only": True, "eos_status_visible": True})
    require(re.fullmatch(r"CANARY[0-9]{8}", canary) is not None)


# Instrument only observation around the real module methods. No frames, model
# results or private-serve implementation are supplied by this browser harness.
SCRIPT = r"""
const [moduleRoot, socketPath, workParent, markerPath, observerPath, servicePid,
       question, context, canary, statusPath, done] = arguments;
let phase = "module-import";
let failure = null;
const status = (next, code = null) => {
  if (failure === null) phase = next;
  failure ??= code;
  const file = Cc["@mozilla.org/file/local;1"].createInstance(Ci.nsIFile);
  file.initWithPath(statusPath);
  if (file.isSymlink()) throw new Error("status_symlink");
  const stream = Cc["@mozilla.org/network/file-output-stream;1"].createInstance(Ci.nsIFileOutputStream);
  stream.init(file, 0x02 | 0x08 | 0x20, 0o600, 0);
  const data = JSON.stringify({version: 1, phase, failure}) + "\n";
  try { if (stream.write(data, data.length) !== data.length) throw new Error("status_write"); }
  finally { stream.close(); }
};
(async () => {
  status("module-import");
  const file = path => {
    const value = Cc["@mozilla.org/file/local;1"].createInstance(Ci.nsIFile);
    value.initWithPath(path); return value;
  };
  const read = (path, limit) => {
    const stream = Cc["@mozilla.org/network/file-input-stream;1"].createInstance(Ci.nsIFileInputStream);
    stream.init(file(path), 0x01, 0, 0);
    const reader = Cc["@mozilla.org/intl/converter-input-stream;1"].createInstance(Ci.nsIConverterInputStream);
    reader.init(stream, "UTF-8", 4096, 0);
    try {
      let data = "", part = {};
      while (data.length <= limit && reader.readString(limit + 1 - data.length, part)) data += part.value;
      if (data.length > limit) throw new Error("observation_bound");
      return data;
    } finally { reader.close(); }
  };
  const writeAdmission = () => {
    const stream = Cc["@mozilla.org/network/file-output-stream;1"].createInstance(Ci.nsIFileOutputStream);
    stream.init(file(markerPath), 0x02 | 0x08 | 0x80, 0o600, 0);
    const data = '{"version":1,"event":"real_core_job_admitted"}\n';
    try { if (stream.write(data, data.length) !== data.length) throw new Error("marker_write"); }
    finally { stream.close(); }
  };
  const ended = member => {
    if (!Number.isSafeInteger(member.pid) || member.pid < 1 ||
        !Number.isSafeInteger(member.start_ticks) || member.start_ticks < 1) throw new Error("observer_identity");
    const path = `/proc/${member.pid}/stat`;
    if (!file(path).exists()) return true;
    const raw = read(path, 8192);
    const fields = raw.slice(raw.lastIndexOf(") ") + 2).trim().split(/\s+/);
    if (fields.length < 20) throw new Error("observer_stat");
    return Number(fields[19]) !== member.start_ticks;
  };
  const observeBoundary = () => {
    const source = file(observerPath);
    if (source.isSymlink() || !source.isFile()) throw new Error("observer_missing");
    const observation = JSON.parse(read(observerPath, 65536));
    if (observation.observed !== true || observation.cli?.pid !== servicePid ||
        !Array.isArray(observation.owned_processes) || observation.owned_processes.length > 64 ||
        !observation.owned_processes.some(p => p.pid === observation.worker?.pid &&
          p.start_ticks === observation.worker?.start_ticks && p.pid !== servicePid)) {
      throw new Error("observer_unbound");
    }
    const members = observation.owned_processes.filter(p => p.pid !== servicePid);
    if (ended(observation.cli) || !members.length || !members.every(ended) ||
        file(workParent).directoryEntries.hasMoreElements()) {
      throw new Error("cleanup_incomplete");
    }
    return {point: "decoded_result_before_panel_render", ephemeral_children: 0,
      observed_worker_lifetimes_ended: true};
  };
  Services.io.getProtocolHandler("resource").QueryInterface(Ci.nsIResProtocolHandler)
    .setSubstitution("volparossa-compute-model-test", Services.io.newFileURI(file(moduleRoot)));
  const {VolparossaCompute} = ChromeUtils.importESModule(
    "resource://volparossa-compute-model-test/VolparossaCompute.sys.mjs");
  const {createVolparossaComputePanel} = ChromeUtils.importESModule(
    "resource://volparossa-compute-model-test/VolparossaComputePanel.sys.mjs");
  const observed = {admitted: 0};
  const connect = VolparossaCompute.connect;
  VolparossaCompute.connect = async function(...args) {
    status("broker-connect");
    try { return await connect.apply(this, args); }
    catch (error) {
      const codes = {unavailable: "MODULE_UNAVAILABLE", not_configured: "MODULE_NOT_CONFIGURED",
        invalid_response: "MODULE_INVALID_RESPONSE", invalid_question: "MODULE_INVALID_QUESTION",
        invalid_context: "MODULE_INVALID_CONTEXT", cleanup_unconfirmed: "MODULE_CLEANUP_UNCONFIRMED"};
      status(phase, codes[error?.code] || "SCRIPT_FAILED");
      throw error;
    }
  };
  const original = VolparossaCompute.prototype._response;
  VolparossaCompute.prototype._response = function(response) {
    if (response.event === "capabilities") {
      status("capabilities-received");
      const caps = response.capabilities;
      observed.capabilities = Object.fromEntries(["visibility", "local_only", "model_profile",
        "network_access", "public_cache", "training", "cloud_fallback"].map(k => [k, caps[k]]));
    } else if (response.event === "admitted") {
      status("submit-admitted");
      observed.admitted++;
      writeAdmission();
    } else if (response.event === "result") {
      status("result-received");
      observed.boundary = observeBoundary();
      status("result-cleanup-verified");
      const answer = response.result;
      const output = answer?.output;
      if (answer?.version !== 1 || answer.operation !== "compute_private_task" ||
          answer.model_profile !== "smollm2-360m-v1" || answer.local_only !== true ||
          answer.execution_complete !== true || answer.answer_complete !== true || answer.complete !== true ||
          answer.private_data_supported !== true || answer.distributed_execution_claimed !== false ||
          answer.private_training_claimed !== false || answer.semantic_completeness_proven !== false ||
          answer.model_answer_correctness_proven !== false || answer.cleanup?.complete !== true ||
          answer.cleanup.retained_input !== false || answer.cleanup.retained_report !== false ||
          output?.sample_index !== 0 || output.text_truncated !== false ||
          output.generation?.version !== 1 || output.generation.stop_reason !== "eos" ||
          output.generation.model_profile !== "smollm2-360m-v1" || output.generation.max_new_tokens !== 256) {
        throw new Error("model_completion_invalid");
      }
      observed.answer = {answer_status: answer.answer_status, complete: answer.answer_complete,
        canary_present: output.text.includes(canary), generated_tokens: output.generated_tokens};
    } else if (response.event === "error") {
      const codes = {busy: "BROKER_BUSY", invalid_request: "BROKER_INVALID_REQUEST",
        handshake_required: "BROKER_HANDSHAKE_REQUIRED", no_such_task: "BROKER_NO_SUCH_TASK",
        cancelled: "BROKER_CANCELLED", execution_failed: "BROKER_EXECUTION_FAILED",
        cleanup_unconfirmed: "BROKER_CLEANUP_UNCONFIRMED"};
      status(phase, codes[response.code] || "SCRIPT_FAILED");
    }
    return original.call(this, response);
  };
  const win = Services.wm.getMostRecentWindow("navigator:browser");
  status("sidebar-initialize");
  Services.prefs.setStringPref("browser.volparossa.compute.socket", socketPath);
  Services.prefs.setBoolPref("browser.ml.chat.enabled", true);
  Services.prefs.setStringPref("browser.ml.chat.provider", "");
  await win.SidebarController.promiseInitialized;
  status("sidebar-show");
  if (!(await win.SidebarController.show("viewGenaiChatSidebar"))) throw new Error("sidebar_missing");
  const doc = win.document.getElementById("sidebar").contentDocument;
  status("sidebar-document");
  if (doc.documentURI !== "chrome://browser/content/genai/chat.html") throw new Error("sidebar_document");
  status("panel-create");
  const panel = createVolparossaComputePanel(doc, doc.body);
  try {
    await panel.ask(question, context);
    status("panel-render-check");
    observed.panel = {actual_sidebar_document: true, connected: panel.element.isConnected,
      canary_rendered: panel.element.querySelector("pre").textContent.includes(canary),
      text_only: panel.element.querySelector("pre").childElementCount === 0 &&
        panel.element.querySelectorAll("script").length === 0,
      eos_status_visible: panel.element.querySelector('[role="status"]').textContent.includes("Status: eos.")};
  } finally {
    status("panel-cleanup");
    panel.destroy();
    observed.removed = !doc.getElementById("volparossa-private-compute");
    VolparossaCompute.prototype._response = original;
    VolparossaCompute.connect = connect;
    win.SidebarController.hide();
  }
  status("script-complete");
  done(observed);
})().catch(() => {
  try { status(phase, "SCRIPT_FAILED"); } catch {}
  done({failure: "combined_browser_smoke_failed"});
});
"""


def remove_browser_files(work):
    for name in ("profile", "config", "cache", "runtime", "tmp"):
        path = work / name
        if path.exists():
            private_directory(path)
            shutil.rmtree(path)
    log = work / "firefox.log"
    if log.exists():
        info = log.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1)
        log.unlink()


def inside(args, stage, work, metadata):
    status(work, "namespace-validation")
    require(args.host_netns and os.readlink("/proc/self/ns/net") != args.host_netns)
    require(all(os.statvfs(path).f_flag & os.ST_RDONLY for path in (Path("/"), ROOT, stage, args.work_parent)))
    links = json.loads(subprocess.check_output(["/usr/bin/ip", "-j", "link", "show"], text=True))
    require([link["ifname"] for link in links] == ["lo"])
    endpoint = validate_endpoint(args.socket)
    profile = work / "profile"
    for name in ("profile", "config", "cache", "runtime", "tmp"):
        (work / name).mkdir(mode=0o700)
    (profile / "prefs.js").write_text('user_pref("remote.prefs.recommended", false);\n')
    environment = dict(os.environ)
    environment.update({"MOZ_NO_REMOTE": "1", "MOZ_CRASHREPORTER_DISABLE": "1",
        "XDG_CONFIG_HOME": str(work / "config"), "XDG_CACHE_HOME": str(work / "cache"),
        "XDG_RUNTIME_DIR": str(work / "runtime"), "TMPDIR": str(work / "tmp")})
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "DBUS_SYSTEM_BUS_ADDRESS"):
        environment.pop(key, None)
    browser, client, report = None, None, None
    try:
        with (work / "firefox.log").open("xb") as log:
            status(work, "browser-start")
            browser = subprocess.Popen([str(stage / metadata["executable"]), "--headless", "--no-remote",
                "--new-instance", "--profile", str(profile), "--marionette", "--remote-allow-system-access",
                "about:blank"], env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            deadline = time.monotonic() + 40
            status(work, "marionette-connect")
            while time.monotonic() < deadline:
                require(browser.poll() is None)
                try:
                    connection = socket.create_connection(("127.0.0.1", 2828), timeout=1)
                    connection.settimeout(640)
                    client = Marionette(connection)
                    break
                except (ConnectionRefusedError, TimeoutError):
                    time.sleep(0.2)
            require(client is not None)
            status(work, "marionette-session")
            client.command("WebDriver:NewSession", {"capabilities": {"alwaysMatch": {}}})
            client.command("Marionette:SetContext", {"value": "chrome"})
            client.command("WebDriver:SetTimeouts", {"script": 630000})
            status(work, "script-start")
            observed = client.command("WebDriver:ExecuteAsyncScript", {
                "script": SCRIPT, "args": [str(ROOT / "integration"), str(args.socket), str(args.work_parent),
                    str(work / "admitted.json"), str(args.observer_file), args.service_pid,
                    QUESTION, CONTEXT.format(args.canary), args.canary, str(work / STATUS_NAME)],
                "newSandbox": True, "sandbox": "system",
            })["value"]
            # Do not erase a broker/module error emitted before the panel swallowed it.
            with (work / STATUS_NAME).open("rb") as source:
                current = check_status(json.loads(source.read(4096)))
            require(current["failure"] is None)
            status(work, "result-validation")
            check_result(observed, args.canary)
            require(validate_endpoint(args.socket) == endpoint and not list(args.work_parent.iterdir()))
            report = {"passed": True, "kind": "real-gecko-panel-existing-private-core-service",
                "version": 1, "scope": SCOPE, "core_revision": args.core_revision,
                "synthetic_canary": args.canary, "observed": observed,
                "core_observer_sha256": digest(args.observer_file),
                "runtime_version": metadata["version"], "runtime_source_stamp": metadata["source_stamp"],
                "runtime_sha256": metadata["runtime_sha256"], "host_read_only": True, "interfaces": ["lo"],
                "same_owner_socket_mode": 0o600,
                "browser_source_sha256": {name: digest(ROOT / name) for name in SOURCE_FILES}}
            status(work, "browser-stop")
            client.command("Marionette:Quit", {"flags": ["eAttemptQuit"]})
            require(browser.wait(timeout=20) == 0)
        # Logs are private temporary data, not an artifact export surface.
        status(work, "private-log-check")
        with (work / "firefox.log").open("rb") as log:
            raw = log.read(1048577)
        require(len(raw) <= 1048576 and all(text.encode() not in raw for text in
                (QUESTION, CONTEXT.format(args.canary), args.canary)))
        report["private_prompt_absent_from_browser_log"] = True
    except BaseException as error:
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
    status(work, "report-write")
    (work / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    status(work, "complete")


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("stage", "output", "socket", "work-parent", "observer-file"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--core-revision", required=True)
    parser.add_argument("--service-pid", type=int, required=True)
    parser.add_argument("--canary", required=True)
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--host-netns", help=argparse.SUPPRESS)
    args = parser.parse_args()
    require(re.fullmatch(r"[0-9a-f]{40}", args.core_revision) and args.service_pid > 1
            and re.fullmatch(r"CANARY[0-9]{8}", args.canary))
    stage, work = build_path(args.stage), build_path(args.output)
    require(not stage.is_relative_to(work) and not work.is_relative_to(stage))
    private_directory(args.work_parent)
    validate_endpoint(args.socket)
    require(args.observer_file.is_absolute() and args.observer_file.resolve() == args.observer_file
            and args.observer_file.parent.is_dir())
    metadata = validate_stage(stage)
    if args.inside:
        private_directory(work)
        inside(args, stage, work, metadata)
        return
    require(not work.exists() and not work.is_symlink() and not args.observer_file.exists()
            and not args.observer_file.is_symlink() and not list(args.work_parent.iterdir()))
    work.mkdir(mode=0o700)
    status(work, "wrapper-launch")
    print("Fresh browser profile and real core IPC; existing model assets only; no downloads.", flush=True)
    completed = subprocess.run([
        "/usr/bin/bwrap", "--die-with-parent", "--unshare-user", "--unshare-net",
        "--ro-bind", "/", "/", "--bind", str(work), str(work),
        "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev", "--",
        sys.executable, str(Path(__file__).resolve()), *sys.argv[1:], "--inside",
        "--host-netns", os.readlink("/proc/self/ns/net"),
    ], timeout=710, check=False)
    if completed.returncode != 0:
        failed_status(work, subprocess.CalledProcessError(completed.returncode, "browser-wrapper"))
    require(completed.returncode == 0 and (work / "report.json").is_file())
    print(json.dumps({"passed": True, "report": str(work / "report.json")}))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt):
        # No exception text, JS response, private prompt or traceback is exported.
        print("combined browser/model smoke failed; private diagnostics not exported", file=sys.stderr)
        raise SystemExit(1) from None
