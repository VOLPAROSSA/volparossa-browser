#!/usr/bin/env python3
"""Real privileged Gecko Unix transport against a synthetic protocol peer, not model inference."""

import argparse
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time

from smoke_privacy import Marionette
from stage_firefox import MARKER, ROOT, build_path, digest

CAPABILITIES = {
    "visibility": "private_local", "local_only": True, "model_profile": "protocol-fixture",
    "max_question_bytes": 512, "max_context_bytes": 4096,
    "max_request_bytes": 32768, "max_response_bytes": 65536,
    "execution_slots": 1, "max_connections": 8, "max_seconds": 30,
    "network_access": False, "public_cache": False, "training": False, "cloud_fallback": False,
}
CASES = ("complete", "cancel", "utf8", "oversize", "wrong_id", "cleanup", "bounds", "not_private", "panel")


def exact(connection, length):
    data = bytearray()
    while len(data) < length:
        chunk = connection.recv(length - len(data))
        if not chunk:
            raise RuntimeError("truncated fixture request")
        data.extend(chunk)
    return bytes(data)


def receive(connection):
    length = struct.unpack("!I", exact(connection, 4))[0]
    if not 0 < length <= 32768:
        raise RuntimeError("invalid request frame size")
    value = json.loads(exact(connection, length).decode("utf-8", "strict"))
    if value["version"] != 1 or len(value["id"]) != 32:
        raise RuntimeError("invalid request header")
    return value


def send(connection, value):
    payload = json.dumps(value).encode()
    frame = struct.pack("!I", len(payload)) + payload
    # Deliberately split headers/UTF-8 payloads across actual Unix socket writes.
    for offset in range(0, len(frame), 3):
        connection.sendall(frame[offset:offset + 3])


def protocol_fixture(listener, failures):
    try:
        for case in CASES:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(20)
                handshake = receive(connection)
                if handshake["operation"] != {"type": "capabilities"}:
                    raise RuntimeError("submit was sent before capability negotiation")
                if case == "utf8":
                    connection.sendall(struct.pack("!I", 1) + b"\xff")
                    continue
                if case == "oversize":
                    connection.sendall(struct.pack("!I", 65537))
                    continue
                caps = dict(CAPABILITIES)
                if case == "not_private":
                    caps["training"] = True
                send(connection, {"version": 1, "id": handshake["id"], "event": "capabilities", "capabilities": caps})
                if case in ("bounds", "not_private"):
                    if connection.recv(1):
                        raise RuntimeError("invalid/private-incompatible request reached the broker")
                    continue
                submit = receive(connection)
                if submit["operation"] != {"type": "submit", "question": "Fixture question", "context": "Explicit synthetic context"}:
                    raise RuntimeError("unexpected submit payload")
                if submit["id"] == handshake["id"]:
                    raise RuntimeError("request ID reused")
                submit_id = submit["id"]
                send(connection, {"version": 1, "id": "0" * 32 if case == "wrong_id" else submit_id, "event": "admitted"})
                if case == "wrong_id":
                    continue
                if case == "cancel":
                    cancel = receive(connection)
                    if cancel["operation"] != {"type": "cancel", "task_id": submit_id} or cancel["id"] in (handshake["id"], submit_id):
                        raise RuntimeError("cancel scope or correlation changed")
                    send(connection, {"version": 1, "id": cancel["id"], "event": "cancel_requested", "task_id": submit_id})
                    send(connection, {"version": 1, "id": submit_id, "event": "error", "code": "cancelled"})
                else:
                    send(connection, {"version": 1, "id": submit_id, "event": "result", "result": {
                        "output": {"text": "<script>fixture only</script> ✓"},
                        "answer_complete": True, "answer_status": "complete",
                        "cleanup": {"complete": case != "cleanup"},
                    }})
    except Exception as error:
        failures.append(str(error))
    finally:
        listener.close()


SCRIPT = """
const done = arguments[arguments.length - 1];
(async () => {
  const root = Cc["@mozilla.org/file/local;1"].createInstance(Ci.nsIFile);
  root.initWithPath(arguments[0]);
  Services.io.getProtocolHandler("resource").QueryInterface(Ci.nsIResProtocolHandler)
    .setSubstitution("volparossa-compute-test", Services.io.newFileURI(root));
  const { VolparossaCompute } = ChromeUtils.importESModule(
    "resource://volparossa-compute-test/VolparossaCompute.sys.mjs"
  );
  const outcomes = {};
  for (const name of arguments[2]) {
    let client;
    try {
      if (name === "panel") {
        const { createVolparossaComputePanel } = ChromeUtils.importESModule(
          "resource://volparossa-compute-test/VolparossaComputePanel.sys.mjs"
        );
        Services.prefs.setStringPref("browser.volparossa.compute.socket", arguments[1]);
        const doc = Services.wm.getMostRecentWindow("navigator:browser").document
          .implementation.createHTMLDocument("protocol fixture");
        const panel = createVolparossaComputePanel(doc, doc.body);
        await panel.ask("Fixture question", "Explicit synthetic context");
        const rendered = panel.element.querySelector("pre").textContent;
        const executable = panel.element.querySelectorAll("script").length;
        panel.destroy();
        outcomes[name] = { rendered, executable, removed: !doc.body.hasChildNodes() };
        continue;
      }
      client = await VolparossaCompute.connect({ socketPath: arguments[1] });
      if (name === "bounds") {
        const invalid = [];
        for (const input of [
          { question: "€".repeat(171), context: "x" },
          { question: "x", context: "x".repeat(4097) },
          { question: " ", context: "x" },
        ]) {
          try { client.submit(input); invalid.push("accepted"); }
          catch (error) { invalid.push(error.code); }
        }
        outcomes[name] = invalid;
      } else {
        let cancel;
        const task = client.submit({
          question: "Fixture question", context: "Explicit synthetic context",
          onAdmitted: () => { if (name === "cancel") { cancel = task.cancel(); } },
        });
        try {
          const result = await task.finished;
          outcomes[name] = result.output.text;
        } finally {
          await cancel;
        }
      }
    } catch (error) {
      outcomes[name] = error.code || "unexpected_error";
    } finally {
      client?.close();
    }
  }
  done(outcomes);
})().catch(() => done({ fatal: "privileged_module_failed" }));
"""


def inside(stage, work, host_netns):
    if not host_netns or os.readlink("/proc/self/ns/net") == host_netns:
        raise RuntimeError("refusing host network namespace")
    for path in (Path("/"), Path.home(), ROOT, stage):
        if not os.statvfs(path).f_flag & os.ST_RDONLY:
            raise RuntimeError("host and browser runtime must be read-only")
    links = json.loads(subprocess.check_output(["/usr/bin/ip", "-j", "link", "show"], text=True))
    if [link["ifname"] for link in links] != ["lo"]:
        raise RuntimeError("expected loopback-only disposable namespace")
    profile = work / "profile"
    profile.mkdir(mode=0o700)
    (profile / "prefs.js").write_text('user_pref("remote.prefs.recommended", false);\n')
    environment = dict(os.environ)
    for name in ("config", "cache", "runtime", "tmp"):
        (work / name).mkdir(mode=0o700)
    environment.update({
        "MOZ_NO_REMOTE": "1", "MOZ_CRASHREPORTER_DISABLE": "1",
        "XDG_CONFIG_HOME": str(work / "config"), "XDG_CACHE_HOME": str(work / "cache"),
        "XDG_RUNTIME_DIR": str(work / "runtime"), "TMPDIR": str(work / "tmp"),
    })
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "DBUS_SYSTEM_BUS_ADDRESS"):
        environment.pop(key, None)
    socket_path = work / "broker.sock"
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(socket_path))
    os.chmod(socket_path, 0o600)
    listener.listen(1)
    listener.settimeout(40)
    failures = []
    thread = threading.Thread(target=protocol_fixture, args=(listener, failures), daemon=True)
    thread.start()
    metadata = json.loads((stage / MARKER).read_text())
    client = None
    with (work / "firefox.log").open("wb") as log:
        browser = subprocess.Popen([
            str(stage / metadata["executable"]), "--headless", "--no-remote", "--new-instance",
            "--profile", str(profile), "--marionette", "--remote-allow-system-access", "about:blank",
        ], env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                if browser.poll() is not None:
                    raise RuntimeError("isolated browser exited early")
                try:
                    connection = socket.create_connection(("127.0.0.1", 2828), timeout=1)
                    connection.settimeout(60)
                    client = Marionette(connection)
                    break
                except (ConnectionRefusedError, TimeoutError):
                    time.sleep(0.2)
            if client is None:
                raise RuntimeError("isolated browser did not start")
            client.command("WebDriver:NewSession", {"capabilities": {"alwaysMatch": {}}})
            client.command("Marionette:SetContext", {"value": "chrome"})
            client.command("WebDriver:SetTimeouts", {"script": 60000})
            result = client.command("WebDriver:ExecuteAsyncScript", {
                "script": SCRIPT, "args": [str(ROOT / "integration"), str(socket_path), list(CASES)],
                "newSandbox": True, "sandbox": "system",
            })["value"]
            expected = {
                "complete": "<script>fixture only</script> ✓", "cancel": "cancelled",
                "utf8": "invalid_response", "oversize": "invalid_response", "wrong_id": "invalid_response",
                "cleanup": "invalid_response", "bounds": ["invalid_question", "invalid_context", "invalid_question"],
                "not_private": "invalid_response",
                "panel": {"rendered": "<script>fixture only</script> ✓", "executable": 0, "removed": True},
            }
            if result != expected:
                raise RuntimeError(f"unexpected bounded protocol outcomes: {result}")
            thread.join(timeout=5)
            if thread.is_alive() or failures:
                raise RuntimeError(f"protocol fixture failed: {failures}")
            report = {
                "passed": True, "kind": "real-gecko-unix-transport-with-synthetic-protocol-peer",
                "model_inference_proven": False, "firefox157_build_proven": False,
                "runtime_version": metadata["version"], "runtime_source_stamp": metadata["source_stamp"],
                "cases": list(CASES), "host_read_only": True, "interfaces": ["lo"],
                "module_sha256": digest(ROOT / "integration/VolparossaCompute.sys.mjs"),
            }
            (work / "report.json").write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps({"passed": True, "report": str(work / "report.json")}))
            client.command("Marionette:Quit", {"flags": ["eAttemptQuit"]})
            browser.wait(timeout=20)
        finally:
            if client:
                client.socket.close()
            if browser.poll() is None:
                os.killpg(browser.pid, signal.SIGTERM)
                try:
                    browser.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(browser.pid, signal.SIGKILL)
                    browser.wait(timeout=5)
            listener.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--work", help=argparse.SUPPRESS)
    parser.add_argument("--host-netns", help=argparse.SUPPRESS)
    args = parser.parse_args()
    stage = build_path(args.stage)
    metadata = json.loads((stage / MARKER).read_text())
    for name, expected in metadata["runtime_sha256"].items():
        if digest(stage / name) != expected:
            raise RuntimeError("staged Firefox runtime hash mismatch")
    if args.inside:
        inside(stage, build_path(args.work), args.host_netns)
        return
    work = Path(tempfile.mkdtemp(prefix="csm-", dir=ROOT / "build"))
    print(f"Changes only {work}; synthetic Unix peer and browser in disposable loopback netns; host read-only.", flush=True)
    subprocess.run([
        "/usr/bin/bwrap", "--die-with-parent", "--unshare-user", "--unshare-net",
        "--ro-bind", "/", "/", "--bind", str(work), str(work),
        "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev", "--",
        sys.executable, str(Path(__file__).resolve()), "--stage", str(stage),
        "--inside", "--work", str(work), "--host-netns", os.readlink("/proc/self/ns/net"),
    ], check=True, timeout=150)


if __name__ == "__main__":
    main()
