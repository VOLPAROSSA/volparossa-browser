#!/usr/bin/env python3
"""Read effective preferences in real Firefox, isolated from host writes and Internet access."""

import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time

from stage_firefox import DEFAULTS, MARKER, ROOT, build_path, digest, load_defaults

MAX_MESSAGE = 2 * 1024 * 1024


class Marionette:
    def __init__(self, connection):
        self.socket = connection
        self.next_id = 0
        self.receive()  # Protocol greeting.

    def receive(self):
        length = bytearray()
        while True:
            char = self.socket.recv(1)
            if not char:
                raise RuntimeError("Marionette closed its socket")
            if char == b":":
                break
            if not char.isdigit() or len(length) >= 8:
                raise RuntimeError("invalid Marionette frame length")
            length.extend(char)
        size = int(length)
        if size > MAX_MESSAGE:
            raise RuntimeError("oversized Marionette response")
        payload = bytearray()
        while len(payload) < size:
            chunk = self.socket.recv(size - len(payload))
            if not chunk:
                raise RuntimeError("truncated Marionette response")
            payload.extend(chunk)
        return json.loads(payload)

    def command(self, command, parameters):
        self.next_id += 1
        message = json.dumps([0, self.next_id, command, parameters]).encode()
        self.socket.sendall(str(len(message)).encode() + b":" + message)
        response = self.receive()
        if response[:2] != [1, self.next_id] or response[2] is not None:
            raise RuntimeError(f"{command} failed: {response}")
        return response[3]

    def script(self, source, args=()):
        result = self.command("WebDriver:ExecuteScript", {
            "script": source, "args": list(args), "newSandbox": True, "sandbox": "system",
        })
        return result["value"]


def snapshot(client, names):
    return client.script("""
        const prefs = Services.prefs;
        const defaults = prefs.getDefaultBranch("");
        const read = (branch, name) => {
          switch (branch.getPrefType(name)) {
            case prefs.PREF_BOOL: return branch.getBoolPref(name);
            case prefs.PREF_INT: return branch.getIntPref(name);
            case prefs.PREF_STRING: return branch.getStringPref(name);
            default: return null;
          }
        };
        return Object.fromEntries(arguments[0].map(name => [name, {
          effective: read(prefs, name), default: read(defaults, name),
          locked: prefs.prefIsLocked(name), user: prefs.prefHasUserValue(name)
        }]));
    """, [names])


def run_browser(stage, work, profile, phase, change_choices=False):
    metadata = json.loads((stage / MARKER).read_text())
    log = work / f"{phase}.log"
    environment = dict(os.environ)
    environment.update({
        "MOZ_NO_REMOTE": "1", "MOZ_CRASHREPORTER_DISABLE": "1",
        "XDG_CONFIG_HOME": str(work / "config"), "XDG_CACHE_HOME": str(work / "cache"),
        "XDG_RUNTIME_DIR": str(work / "runtime"), "TMPDIR": str(work / "tmp"),
    })
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "DBUS_SYSTEM_BUS_ADDRESS"):
        environment.pop(key, None)
    with log.open("wb") as output:
        browser = subprocess.Popen([
            str(stage / metadata["executable"]), "--headless", "--no-remote", "--new-instance",
            "--profile", str(profile), "--marionette", "--remote-allow-system-access", "about:blank",
        ], env=environment, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        client = None
        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if browser.poll() is not None:
                    raise RuntimeError(f"Firefox exited early: {log}")
                try:
                    connection = socket.create_connection(("127.0.0.1", 2828), timeout=1)
                    connection.settimeout(20)
                    client = Marionette(connection)
                    break
                except (ConnectionRefusedError, TimeoutError):
                    time.sleep(0.2)
            if client is None:
                raise RuntimeError(f"Marionette startup timed out: {log}")
            session = client.command("WebDriver:NewSession", {"capabilities": {"alwaysMatch": {"acceptInsecureCerts": False}}})
            client.command("Marionette:SetContext", {"value": "chrome"})
            names = list(load_defaults()) + ["privacy.trackingprotection.enabled", "network.cookie.cookieBehavior", "remote.prefs.recommended"]
            values = snapshot(client, names)
            if values["remote.prefs.recommended"]["effective"] is not False:
                raise RuntimeError("automation recommendations would mask actual product defaults")
            actual_gre = client.script('return Services.dirsvc.get("GreD", Ci.nsIFile).path;')
            if Path(actual_gre).resolve() != stage:
                raise RuntimeError(f"wrong runtime GRE directory: {actual_gre}")
            if change_choices:
                client.script("""
                    Services.prefs.setBoolPref("identity.fxaccounts.enabled", true);
                    Services.prefs.setBoolPref("browser.newtabpage.activity-stream.showSponsoredTopSites", true);
                    Services.prefs.setStringPref("browser.contentblocking.category", "standard");
                    Services.prefs.savePrefFile(null);
                """)
            client.command("Marionette:Quit", {"flags": ["eAttemptQuit"]})
            browser.wait(timeout=20)
            return {"preferences": values, "gre": actual_gre, "session": session}
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


def inside(stage, work, host_netns):
    if not host_netns or os.readlink("/proc/self/ns/net") == host_netns:
        raise RuntimeError("refusing browser smoke in the host network namespace")
    # --inside is an internal entry point, but do not trust its name as evidence that
    # the caller applied the read-only wrapper. Check before creating a profile or
    # launching Firefox; a manual namespace-only invocation must fail closed too.
    for path in (Path("/"), Path.home(), ROOT, stage):
        if not os.statvfs(path).f_flag & os.ST_RDONLY:
            raise RuntimeError(f"smoke requires a read-only host/runtime mount: {path}")
    if os.statvfs(work).f_flag & os.ST_RDONLY:
        raise RuntimeError("smoke work directory must be the writable workspace bind mount")
    links = json.loads(subprocess.check_output(["/usr/bin/ip", "-j", "link", "show"], text=True))
    if [link["ifname"] for link in links] != ["lo"]:
        raise RuntimeError("smoke requires a disposable loopback-only network namespace")
    profile = work / "profile"
    profile.mkdir()
    for name in ("config", "cache", "runtime", "tmp"):
        (work / name).mkdir(mode=0o700)
    # Marionette would otherwise replace several features under test (including tracking
    # protection) with automation recommendations. Disable that behavior only. No user.js
    # and no test override of any feature under test: read the staged product defaults.
    (profile / "prefs.js").write_text('user_pref("remote.prefs.recommended", false);\n')
    fresh = run_browser(stage, work, profile, "fresh", change_choices=True)
    expected = load_defaults()
    for name, value in expected.items():
        actual = fresh["preferences"][name]
        # Mozilla itself channel-locks this legacy pref in ESR/release builds. We don't
        # introduce or remove that lock; its actual value must nevertheless be false.
        unexpected_lock = actual["locked"] and name != "toolkit.telemetry.enabled"
        if actual["effective"] != value or actual["default"] != value or unexpected_lock:
            raise RuntimeError(f"fresh default mismatch: {name}: {actual}")
    if fresh["preferences"]["privacy.trackingprotection.enabled"]["effective"] is not True:
        raise RuntimeError("strict label did not activate tracking protection")
    restored = run_browser(stage, work, profile, "user-choice")
    for name, value in {
        "identity.fxaccounts.enabled": True,
        "browser.newtabpage.activity-stream.showSponsoredTopSites": True,
        "browser.contentblocking.category": "standard",
    }.items():
        actual = restored["preferences"][name]
        if actual["effective"] != value or actual["locked"] or not actual["user"]:
            raise RuntimeError(f"user override did not survive restart: {name}: {actual}")
    if restored["preferences"]["privacy.trackingprotection.enabled"]["effective"] is not False:
        raise RuntimeError("Standard user choice did not restore native tracking-protection defaults")
    report = {
        "schema": 1, "passed": True,
        "scope": "real installed Firefox privacy defaults and persistent user choices; not a source build, network integration or kill switch proof",
        "network_namespace": os.readlink("/proc/self/ns/net"), "interfaces": ["lo"],
        "host_filesystem_read_only": True, "profile": str(profile),
        "fresh": fresh, "restart_with_user_choices": restored,
        "staging": json.loads((stage / MARKER).read_text()),
    }
    (work / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"passed": True, "report": str(work / "report.json")}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--work", help=argparse.SUPPRESS)
    parser.add_argument("--host-netns", help=argparse.SUPPRESS)
    args = parser.parse_args()
    stage = build_path(args.stage)
    metadata = json.loads((stage / MARKER).read_text())
    if digest(DEFAULTS) != metadata["defaults_sha256"]:
        raise ValueError("stage is stale relative to project defaults; create a fresh stage")
    if digest(stage / "volparossa.cfg") != metadata["autoconfig_sha256"]:
        raise ValueError("staged AutoConfig hash mismatch")
    for name, expected in metadata["runtime_sha256"].items():
        if digest(stage / name) != expected:
            raise ValueError(f"staged runtime hash mismatch: {name}")
    if args.inside:
        inside(stage, build_path(args.work), args.host_netns)
        return
    work = Path(tempfile.mkdtemp(prefix="privacy-smoke-", dir=ROOT / "build"))
    print(f"Smoke changes only {work}; host filesystem read-only, new loopback-only netns.", flush=True)
    command = [
        "/usr/bin/bwrap", "--die-with-parent", "--unshare-user", "--unshare-net",
        "--ro-bind", "/", "/", "--bind", str(work), str(work),
        "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev",
        "--", sys.executable, str(Path(__file__).resolve()), "--stage", str(stage),
        "--inside", "--work", str(work), "--host-netns", os.readlink("/proc/self/ns/net"),
    ]
    subprocess.run(command, check=True, timeout=150)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"privacy smoke failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
