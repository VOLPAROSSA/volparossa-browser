#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Empty-profile Firefox startup only, before any model or private-service work.

Use the combined proof's exact staged runtime, privacy defaults and bubblewrap
isolation. No broker, prompt, model, module injection or arbitrary URL is accepted.
Only this empty about:blank run may retain a bounded actual browser log. Never
point these diagnostics at the combined private session's profile or log.
"""

import argparse
import hashlib
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

from smoke_compute_model import private_directory, validate_stage
from smoke_privacy import Marionette
from stage_firefox import ROOT, build_path, digest

LOG_LIMIT = 16384
CONNECT_SECONDS = 40
LOG_NAME = "startup-only.log"
RAW_LOG_NAME = "empty-profile-startup.raw"
KIND = "empty-profile-about-blank-startup-only"
SCOPE = dict(private_input_used=False, broker_connected=False, model_executed=False)
PROFILE_NAMES = ("profile", "config", "cache", "runtime", "tmp")


def require(condition):
    if not condition:
        raise ValueError("browser_startup_preflight_failed")


def process_snapshot(pid, proc=Path("/proc")):
    """Only kernel process facts, never argv/environment or arbitrary log text."""
    value = dict(pid=pid, start_ticks=None, state=None, wchan=None, readable=False)
    try:
        with (proc / str(pid) / "stat").open() as stream:
            raw = stream.read(4097)
        require(len(raw) <= 4096 and ") " in raw)
        fields = raw.rsplit(") ", 1)[1].split()
        require(len(fields) >= 20 and re.fullmatch(r"[RSDZTWtXxKIP]", fields[0])
                and fields[19].isdigit())
        value.update(start_ticks=int(fields[19]), state=fields[0], readable=True)
        with (proc / str(pid) / "wchan").open() as stream:
            wchan = stream.read(81).strip()
        if re.fullmatch(r"[a-zA-Z0-9_]{1,80}", wchan):
            value["wchan"] = wchan
    except (OSError, ValueError):
        pass
    return value


def port_listener(raw):
    """Read only the fixed Marionette listen state, not unrelated connections."""
    require(len(raw) <= 65536)
    for line in raw.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 4 and fields[1].rsplit(":", 1)[-1].upper() == "0B0C" and fields[3] == "0A":
            return True
    return False


def network_snapshot(links, proc=Path("/proc/net")):
    require(type(links) is list and [item["ifname"] for item in links] == ["lo"])
    result = dict(interfaces=["lo"], loopback_up="UP" in links[0].get("flags", []),
                  marionette_port=2828, ipv4_listener=None, ipv6_listener=None)
    for name, field in (("tcp", "ipv4_listener"), ("tcp6", "ipv6_listener")):
        try:
            with (proc / name).open() as stream:
                result[field] = port_listener(stream.read(65537))
        except (OSError, ValueError):
            pass
    return result


def retain_empty_startup_log(work):
    """Hardcoded new preflight log only: no caller-supplied/private-session path."""
    fd = os.open(work / RAW_LOG_NAME, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1)
        source.seek(max(0, info.st_size - LOG_LIMIT))
        raw = source.read(LOG_LIMIT)
    with (work / LOG_NAME).open("xb") as target:
        target.write(raw)
    (work / LOG_NAME).chmod(0o600)
    (work / RAW_LOG_NAME).unlink()
    return dict(name=LOG_NAME, bytes=len(raw), observed_bytes=info.st_size,
                truncated=info.st_size > LOG_LIMIT, sha256=hashlib.sha256(raw).hexdigest())


def remove_profile(work):
    for name in PROFILE_NAMES:
        path = work / name
        if path.exists():
            require(not path.is_symlink() and path.is_dir())
            shutil.rmtree(path)
    return all(not (work / name).exists() for name in PROFILE_NAMES)


def inside(args, stage, work, metadata):
    require(args.host_netns and os.readlink("/proc/self/ns/net") != args.host_netns)
    require(all(os.statvfs(path).f_flag & os.ST_RDONLY for path in (Path("/"), ROOT, stage)))
    links = json.loads(subprocess.check_output(["/usr/bin/ip", "-j", "link", "show"], text=True))
    network = network_snapshot(links)
    for name in PROFILE_NAMES:
        (work / name).mkdir(mode=0o700)
    # Exactly the same preference as the combined proof. Keep privacy/sandbox on.
    (work / "profile/prefs.js").write_text('user_pref("remote.prefs.recommended", false);\n')
    environment = dict(os.environ)
    environment.update(MOZ_NO_REMOTE="1", MOZ_CRASHREPORTER_DISABLE="1",
        XDG_CONFIG_HOME=str(work / "config"), XDG_CACHE_HOME=str(work / "cache"),
        XDG_RUNTIME_DIR=str(work / "runtime"), TMPDIR=str(work / "tmp"))
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "DBUS_SYSTEM_BUS_ADDRESS"):
        environment.pop(key, None)
    report = dict(version=1, kind=KIND, passed=False, scope=SCOPE, outcome="not_started",
        connect_seconds=CONNECT_SECONDS, elapsed_ms=0, firefox=None, initial_firefox=None,
        firefox_exit_code=None, firefox_final_exit_code=None, network=network, host_read_only=True, startup_log=None,
        runtime_version=metadata["version"], runtime_source_stamp=metadata["source_stamp"],
        runtime_sha256=metadata["runtime_sha256"], defaults_sha256=metadata["defaults_sha256"],
        script_sha256=digest(Path(__file__).resolve()), cleanup=None)
    browser, connection, client, forced = None, None, None, False
    started = time.monotonic()
    try:
        with (work / RAW_LOG_NAME).open("xb") as log:
            browser = subprocess.Popen([str(stage / metadata["executable"]), "--headless", "--no-remote",
                "--new-instance", "--profile", str(work / "profile"), "--marionette",
                "--remote-allow-system-access", "about:blank"], env=environment,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            report["initial_firefox"] = process_snapshot(browser.pid)
            deadline = time.monotonic() + CONNECT_SECONDS
            report["outcome"] = "timeout"
            while time.monotonic() < deadline:
                if browser.poll() is not None:
                    report["outcome"] = "exited"
                    break
                try:
                    connection = socket.create_connection(("127.0.0.1", 2828),
                                                          timeout=min(1, max(0.001, deadline - time.monotonic())))
                    connection.settimeout(max(0.001, deadline - time.monotonic()))
                    client = Marionette(connection)
                    client.command("WebDriver:NewSession", {"capabilities": {"alwaysMatch": {}}})
                    report["outcome"] = "connected"
                    break
                except (ConnectionRefusedError, TimeoutError):
                    if connection is not None:
                        connection.close()
                        connection = None
                    time.sleep(min(0.2, max(0, deadline - time.monotonic())))
            report["firefox"] = process_snapshot(browser.pid)
            report["network"] = network_snapshot(links)
            report["elapsed_ms"] = int((time.monotonic() - started) * 1000)
            if report["outcome"] == "connected":
                shutdown_deadline = time.monotonic() + 20
                connection.settimeout(20)
                client.command("Marionette:Quit", {"flags": ["eAttemptQuit"]})
                report["passed"] = browser.wait(timeout=max(0.001, shutdown_deadline - time.monotonic())) == 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt):
        report["outcome"] = "startup_error"
        report["passed"] = False
    finally:
        if report["firefox"] is None and browser is not None:
            report["firefox"] = process_snapshot(browser.pid)
            report["network"] = network_snapshot(links)
        if report["elapsed_ms"] == 0:
            report["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        if connection is not None:
            connection.close()
        report["firefox_exit_code"] = browser.poll() if browser is not None else None
        if browser is not None and browser.poll() is None:
            forced = True
            os.killpg(browser.pid, signal.SIGTERM)
            try:
                browser.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(browser.pid, signal.SIGKILL)
                browser.wait(timeout=5)
        report["firefox_final_exit_code"] = browser.poll() if browser is not None else None
        report["startup_log"] = retain_empty_startup_log(work)
        report["cleanup"] = dict(browser_exited=browser is None or browser.poll() is not None,
                                 temporary_profile_removed=remove_profile(work), forced_termination=forced)
        report["passed"] = report["passed"] and not forced and all(report["cleanup"][field]
            for field in ("browser_exited", "temporary_profile_removed"))
        with (work / "report.json").open("x") as target:
            json.dump(report, target, indent=2)
            target.write("\n")
    require(report["passed"])


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--host-netns", help=argparse.SUPPRESS)
    args = parser.parse_args()
    stage, work = build_path(args.stage), build_path(args.output)
    require(not stage.is_relative_to(work) and not work.is_relative_to(stage))
    metadata = validate_stage(stage)
    if args.inside:
        private_directory(work)
        inside(args, stage, work, metadata)
        return
    require(not work.exists() and not work.is_symlink())
    work.mkdir(mode=0o700)
    completed = subprocess.run([
        "/usr/bin/bwrap", "--die-with-parent", "--unshare-user", "--unshare-net",
        "--ro-bind", "/", "/", "--bind", str(work), str(work),
        "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev", "--",
        sys.executable, "-B", str(Path(__file__).resolve()), *sys.argv[1:], "--inside",
        "--host-netns", os.readlink("/proc/self/ns/net"),
    ], timeout=90, check=False)
    require(completed.returncode == 0)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt):
        print("empty-profile Firefox startup preflight failed", file=sys.stderr)
        raise SystemExit(1) from None
