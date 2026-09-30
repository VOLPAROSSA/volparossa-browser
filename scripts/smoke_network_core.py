#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Guest-only real core gateway browser driver; external fixture owns policy/routes/captures."""

import argparse
import base64
import json
import os
from pathlib import Path
import re
import signal
import socket
import ssl
import stat
import struct
import subprocess
import sys
import time
from urllib.parse import urlsplit

from smoke_browser_startup import remove_profile
from smoke_compute_model import private_directory, validate_stage
from smoke_network import require
from smoke_privacy import Marionette
from stage_firefox import ROOT, build_path, digest, isolated_browser_home, validate_isolated_browser_home

GRANT_KEYS = {"version", "app_uid", "app_socket", "capability", "hostname", "port", "partition", "expires_at_ms", "overlay_only"}
STATUS_NAME = "driver-status.json"
STATUS_KIND = "real-gecko-core-gateway-driver-status"
STATUS_PHASES = frozenset((
    "wrapper-start", "runtime-validation", "isolated-home", "wrapper-launch", "child-validation",
    "grant-validation", "control-namespace", "socket-path", "socket-owner", "socket-access", "profile-init", "browser-start", "marionette-connect", "marionette-session",
    "script-start", "import", "attach-a", "attach-b", "wrong-scope", "request-a", "request-b",
    "detach-a", "finish-b", "result-validation", "browser-stop", "complete",
))
ATTACH_STAGES = frozenset(("process-gate", "unix-transport", "constructor", "transport-timeout", "input-stream",
    "output-stream", "input-pump", "input-listen", "proxy-filter", "bootstrap-write", "bootstrap-wait",
    "bootstrap-reply", "bootstrap-read", "ready-validate", "ready-proxy", "bootstrap-eof", "bootstrap-timeout"))


def attach_diagnostic(value):
    require(value is None or type(value) is dict and set(value) == {"stage", "nsresult"}
        and value["stage"] in ATTACH_STAGES and (value["nsresult"] is None
            or type(value["nsresult"]) is int and 0 <= value["nsresult"] <= 0xffffffff))
    return value
STATUS_ERRORS = frozenset((
    "OS_ERROR", "CHECK_FAILED", "SUBPROCESS_FAILED", "RUNTIME_FAILED", "SCRIPT_FAILED",
    "invalid_contract", "invalid_scope", "scope_unavailable", "invalid_channel",
    "unsupported_runtime", "unavailable", "request_failed", "detached",
))
CONTROL_DIRECTORY = Path("/run/volparossa/control")
APP_SOCKET_NAME = "agent.sock.apps"


def status_record(phase, error_code=None, errno=None, child_exit_code=None, attachment=None):
    require(phase in STATUS_PHASES and (error_code is None or error_code in STATUS_ERRORS))
    require(errno is None or type(errno) is int and 0 < errno < 4096)
    require(child_exit_code is None or type(child_exit_code) is int and -255 <= child_exit_code <= 255)
    return dict(version=1, kind=STATUS_KIND, phase=phase, error_code=error_code,
                errno=errno, child_exit_code=child_exit_code, attachment=attach_diagnostic(attachment))


def driver_status(work, phase, error=None):
    """Closed phase/codes survive pre-browser failures and outer fixture cancellation."""
    value = status_record(phase)
    if error is not None:
        # Keep the child's last bounded phase if the outer bwrap wait merely
        # relays its failure. Never serialize exceptions, argv or stderr.
        previous = work / STATUS_NAME
        if previous.is_file() and not previous.is_symlink() and previous.stat().st_size <= 2048:
            candidate = json.loads(previous.read_text())
            expected = status_record(candidate["phase"], candidate["error_code"], candidate["errno"], candidate["child_exit_code"],
                                     candidate.get("attachment"))
            require(candidate == expected)
            value = candidate
        if value["error_code"] is None:
            value["error_code"] = "OS_ERROR" if isinstance(error, OSError) else \
                "SUBPROCESS_FAILED" if isinstance(error, subprocess.SubprocessError) else \
                "CHECK_FAILED" if isinstance(error, (ValueError, KeyError, TypeError)) else "RUNTIME_FAILED"
            if isinstance(error, OSError) and type(error.errno) is int and 0 < error.errno < 4096:
                value["errno"] = error.errno
        if isinstance(error, subprocess.CalledProcessError) and -255 <= error.returncode <= 255:
            value["child_exit_code"] = error.returncode
    temporary = work / (STATUS_NAME + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True) + "\n")
    temporary.chmod(0o600)
    temporary.replace(work / STATUS_NAME)


def grant_file(path):
    require(path.is_absolute() and path.resolve() == path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 4096)
        raw = source.read(4097)
    require(len(raw) <= 4096)
    grant = json.loads(raw)
    require(set(grant) == GRANT_KEYS and grant["version"] == 1 and grant["app_uid"] == os.getuid()
            and grant["overlay_only"] is True)
    for field in ("capability", "partition"):
        require(type(grant[field]) is str and re.fullmatch(r"[0-9a-f]{64}", grant[field]) is not None)
    require(type(grant["expires_at_ms"]) is int and int(time.time() * 1000) < grant["expires_at_ms"]
            <= int(time.time() * 1000) + 300000)
    return grant


def probe_app_socket(work, grants):
    """Connect and close without sending bytes: no capability or delegation is consumed."""
    driver_status(work, "socket-path")
    paths = {grant["app_socket"] for grant in grants}
    require(len(paths) == 1)
    path = Path(next(iter(paths)))
    require(path.is_absolute() and len(os.fsencode(path)) <= 107 and path.resolve(strict=True) == path)
    info, parent = path.lstat(), path.parent.lstat()
    require(stat.S_ISSOCK(info.st_mode) and stat.S_ISDIR(parent.st_mode))
    driver_status(work, "socket-owner")
    require(stat.S_IMODE(info.st_mode) == 0o660 and parent.st_mode & 0o022 == 0
            and info.st_uid == parent.st_uid and info.st_gid == parent.st_gid
            and (info.st_uid == os.geteuid() or info.st_gid in {os.getegid(), *os.getgroups()}))
    driver_status(work, "socket-access")
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(2)
        connection.connect(str(path))
        _, peer_uid, _ = struct.unpack("iII", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        require(peer_uid == info.st_uid)
    return dict(path_type_verified=True, socket_parent_owner_group_match=True,
        peer_uid_matches_socket=True, unix_connect_verified=True, capability_sent=False)


def control_namespace_mounts(directory):
    """Recreate only the fixture agent's control publication in the app namespace.

    The agent binds its runtime below /run/volparossa and issues that unchanged
    app-socket path. Binding the exact control directory retains its owner/group
    checks; binding a socket below a freshly app-owned parent would not. Neither
    the remaining runtime nor agent state is published below the private /run.
    The directory was already accessible to the same application UID/group.
    """
    require(directory.is_absolute() and directory.resolve(strict=True) == directory
            and directory.parts[-2:] == ("runtime-client", "control"))
    parent, endpoint = directory.lstat(), (directory / APP_SOCKET_NAME).lstat()
    require(stat.S_ISDIR(parent.st_mode) and parent.st_mode & 0o022 == 0
            and stat.S_ISSOCK(endpoint.st_mode) and stat.S_IMODE(endpoint.st_mode) == 0o660
            and (endpoint.st_uid, endpoint.st_gid) == (parent.st_uid, parent.st_gid))
    return ["--tmpfs", "/run", "--ro-bind", str(directory), str(CONTROL_DIRECTORY),
            "--remount-ro", "/run"]


def validate_control_namespace(directory, grants):
    require({grant["app_socket"] for grant in grants} == {str(CONTROL_DIRECTORY / APP_SOCKET_NAME)})
    control_namespace_mounts(directory)  # Validate the original source again inside the child.
    for name in ("", APP_SOCKET_NAME):
        original, mounted = (directory / name).lstat(), (CONTROL_DIRECTORY / name).lstat()
        require((original.st_dev, original.st_ino, original.st_mode, original.st_uid, original.st_gid)
                == (mounted.st_dev, mounted.st_ino, mounted.st_mode, mounted.st_uid, mounted.st_gid))
    require(all(os.statvfs(path).f_flag & os.ST_RDONLY for path in (Path("/run"), CONTROL_DIRECTORY))
            and set(CONTROL_DIRECTORY.parent.iterdir()) == {CONTROL_DIRECTORY})
    return dict(scope="client-control-directory", original_socket_inode_preserved=True,
        original_parent_inode_preserved=True, read_only=True, grant_unmodified=True)


def pinned_url(url, grant):
    parsed = urlsplit(url)
    require(parsed.scheme == "https" and parsed.hostname == grant["hostname"]
            and (parsed.port or 443) == grant["port"] and not parsed.username and not parsed.password
            and not parsed.fragment and len(url) <= 512)


SCRIPT = r"""
const [moduleRoot, grants, urls, expectedSha, expectedBytes, certificate, output, done] = arguments;
let phase = "import";
const checkpoint = async (next, errorCode = null, attachment = null) => {
  phase = next;
  await IOUtils.writeJSON(output + "/driver-status.json", {
    version:1, kind:"real-gecko-core-gateway-driver-status", phase,
    error_code:errorCode, errno:null, child_exit_code:null, attachment,
  }, {tmpPath:output + "/driver-status.json.tmp"});
};
const status = async (name, value) => {
  await IOUtils.writeJSON(output + "/" + name + ".tmp", value, {mode:"create"});
  await IOUtils.move(output + "/" + name + ".tmp", output + "/" + name, {noOverwrite:true});
};
(async () => {
  await checkpoint("import");
  const {setTimeout} = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const directory = Cc["@mozilla.org/file/local;1"].createInstance(Ci.nsIFile);
  directory.initWithPath(moduleRoot);
  Services.io.getProtocolHandler("resource").QueryInterface(Ci.nsIResProtocolHandler)
    .setSubstitution("volparossa-network-core-test", Services.io.newFileURI(directory));
  const {VolparossaNetwork} = ChromeUtils.importESModule("resource://volparossa-network-core-test/VolparossaNetwork.sys.mjs");
  Cc["@mozilla.org/security/x509certdb;1"].getService(Ci.nsIX509CertDB).addCertFromBase64(certificate, "C,,");
  const principal = Services.scriptSecurityManager.getSystemPrincipal();
  const channel = url => Services.io.newChannelFromURI(Services.io.newURI(url), null, principal, null,
    Ci.nsILoadInfo.SEC_ALLOW_CROSS_ORIGIN_SEC_CONTEXT_IS_NULL, Ci.nsIContentPolicy.TYPE_OTHER);
  const download = (owner, url) => new Promise((resolve,reject) => {
    const hash=Cc["@mozilla.org/security/hash;1"].createInstance(Ci.nsICryptoHash);
    hash.init(Ci.nsICryptoHash.SHA256);
    let bytes=0;
    const request=channel(url);
    try {
      owner.openChannel(request, {
        onStartRequest(response) {
          if (response.QueryInterface(Ci.nsIHttpChannel).responseStatus !== 200) response.cancel(Cr.NS_ERROR_ABORT);
        },
        onDataAvailable(_request, input, _offset, count) {
          bytes += count;
          if (bytes > expectedBytes) { request.cancel(Cr.NS_ERROR_ABORT); return; }
          hash.updateFromStream(input, count);
        },
        onStopRequest(_request, status) {
          const sha=Array.from(hash.finish(false), c => c.charCodeAt(0).toString(16).padStart(2,"0")).join("");
          if (Components.isSuccessCode(status) && bytes === expectedBytes && sha === expectedSha) {
            resolve({bytes, sha256_verified:true});
          } else { reject(new Error("download_failed")); }
        }
      });
    } catch(error) { reject(error); }
  });
  let a, b;
  const result={};
  try {
    await checkpoint("attach-a"); a=await VolparossaNetwork.attach(grants[0]);
    await checkpoint("attach-b"); b=await VolparossaNetwork.attach(grants[1]);
    result.independent_attachments=a.active && b.active && a._isolation !== b._isolation;
    await checkpoint("wrong-scope");
    try { await download(a,"https://outside-authority.invalid/denied"); result.wrong_scope_blocked=false; }
    catch { result.wrong_scope_blocked=true; }
    if (!result.wrong_scope_blocked) throw new Error("scope_failure");
    await checkpoint("request-a");
    result.a=await download(a,urls[0]);
    await status("a-complete.json", {version:1,complete:true});
    await checkpoint("request-b");
    const pending=download(b,urls[1]);
    // Consume rejection immediately while the fixture observes actual kernel paths.
    let bFailure=false;
    pending.catch(() => { bFailure=true; });
    const deadline=Date.now()+75000;
    while (!(await IOUtils.exists(output+"/detach-a"))) {
      if (bFailure || Date.now() >= deadline) throw new Error("detach_marker_unavailable");
      await sleep(50);
    }
    const marker=await IOUtils.read(output+"/detach-a", {maxBytes:128});
    const command=JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(marker));
    if (Object.keys(command).sort().join(",") !== "detach,version" || command.version !== 1 || command.detach !== true) {
      throw new Error("detach_marker_invalid");
    }
    await checkpoint("detach-a"); a.close();
    await status("a-detached.json", {version:1,detached:true});
    result.a_detached=!a.active;
    await checkpoint("finish-b");
    result.b=await pending;
    result.b_survives_a_detach=b.active;
  } finally { a?.close(); b?.close(); }
  done(result);
})().catch(async error => {
  const allowed=["invalid_contract","invalid_scope","scope_unavailable","invalid_channel",
    "unsupported_runtime","unavailable","request_failed","detached"];
  const code=allowed.includes(error?.code) ? error.code : "SCRIPT_FAILED";
  const attachment = error?.diagnostic ?? null;
  try { await checkpoint(phase,code,attachment); } catch {}
  done({fatal:"network_core_driver_failed",phase,code,attachment});
});
"""


def check_result(result, expected_bytes):
    require(type(result) is dict and set(result) == {"independent_attachments", "wrong_scope_blocked",
        "a", "b", "a_detached", "b_survives_a_detach"})
    for field in ("independent_attachments", "wrong_scope_blocked", "a_detached", "b_survives_a_detach"):
        require(result[field] is True)
    for field in ("a", "b"):
        require(result[field] == {"bytes":expected_bytes, "sha256_verified":True})


def guest_guard(args):
    require(socket.gethostname() == "volparossa-alpha" and os.getuid() != 0
            and re.fullmatch(r"net:\[[0-9]+\]", args.parent_netns) is not None
            and os.readlink("/proc/self/ns/net") != args.parent_netns)
    fields = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    require(int(fields["CapEff"].strip(), 16) == 0)


def isolated_runtime_parent(stage, work):
    """Mount only the pinned runtime below a child-owned, read-only parent shell.

    The application can traverse the provisioning user's search-only home, but
    bubblewrap's destination-parent construction also encounters its unmapped
    owner. Do not relax that home's permissions or expose its other entries.
    The runtime and private work retain their exact original inodes and modes.
    """
    require(ROOT == Path("/home/vpci/browser-network-runtime")
            and stage.is_relative_to(ROOT / "build") and work.is_relative_to(ROOT / "build")
            and not Path.home().is_relative_to(ROOT.parent))
    return ["--tmpfs", str(ROOT.parent), "--ro-bind", str(ROOT), str(ROOT),
            "--remount-ro", str(ROOT.parent)]


def inside(args, stage, work, metadata):
    driver_status(work, "child-validation")
    guest_guard(args)
    require(all(os.statvfs(path).f_flag & os.ST_RDONLY for path in (Path("/"), ROOT.parent, ROOT, stage)))
    validate_isolated_browser_home(work)
    driver_status(work, "grant-validation")
    grants = [grant_file(path) for path in (args.grant_a, args.grant_b)]
    require(grants[0]["capability"] != grants[1]["capability"])
    driver_status(work, "control-namespace")
    control_namespace = validate_control_namespace(args.control_directory, grants)
    socket_access = probe_app_socket(work, grants)
    for url, grant in zip((args.url_a, args.url_b), grants):
        pinned_url(url, grant)
    require(args.test_ca.stat().st_size <= 16384 and not args.test_ca.is_symlink())
    certificate = base64.b64encode(ssl.PEM_cert_to_DER_cert(args.test_ca.read_text())).decode()
    driver_status(work, "profile-init")
    for name in ("profile", "config", "cache", "runtime", "tmp"):
        (work / name).mkdir(mode=0o700)
    (work / "profile/prefs.js").write_text('user_pref("remote.prefs.recommended", false);\n')
    environment = dict(os.environ, MOZ_NO_REMOTE="1", MOZ_CRASHREPORTER_DISABLE="1",
        XDG_CONFIG_HOME=str(work / "config"), XDG_CACHE_HOME=str(work / "cache"),
        XDG_RUNTIME_DIR=str(work / "runtime"), TMPDIR=str(work / "tmp"))
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "DBUS_SYSTEM_BUS_ADDRESS",
                "MOZ_LOG", "MOZ_LOG_FILE", "NSPR_LOG_MODULES", "NSPR_LOG_FILE"):
        environment.pop(key, None)
    report = dict(version=1, kind="real-gecko-core-gateway-driver", passed=False,
        core_revision=args.core_revision, runtime_version=metadata["version"], runtime_source_stamp=metadata["source_stamp"],
        module_sha256=digest(ROOT / "integration/VolparossaNetwork.sys.mjs"), script_sha256=digest(Path(__file__).resolve()),
        expected_bytes=args.expected_bytes, expected_sha256=args.expected_sha256, overlay_kernel_proof_external=True,
        full_browser_killswitch=False, firefox157_build_proven=False, namespace=os.readlink("/proc/self/ns/net"),
        socket_access=socket_access, control_namespace=control_namespace)
    browser, client = None, None
    try:
        driver_status(work, "browser-start")
        browser = subprocess.Popen([str(stage / metadata["executable"]), "--headless", "--no-remote", "--new-instance",
            "--profile", str(work / "profile"), "--marionette", "--remote-allow-system-access", "about:blank"],
            env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + 40
        driver_status(work, "marionette-connect")
        while time.monotonic() < deadline:
            require(browser.poll() is None)
            try:
                connection = socket.create_connection(("127.0.0.1", 2828), timeout=1)
                connection.settimeout(240)
                client = Marionette(connection)
                break
            except (ConnectionRefusedError, TimeoutError):
                time.sleep(.2)
        require(client is not None)
        driver_status(work, "marionette-session")
        client.command("WebDriver:NewSession", {"capabilities":{"alwaysMatch":{}}})
        client.command("Marionette:SetContext", {"value":"chrome"})
        client.command("WebDriver:SetTimeouts", {"script":230000})
        driver_status(work, "script-start")
        result = client.command("WebDriver:ExecuteAsyncScript", {"script":SCRIPT,
            "args":[str(ROOT / "integration"), grants, [args.url_a,args.url_b], args.expected_sha256,
                    args.expected_bytes, certificate, str(work)], "newSandbox":True, "sandbox":"system"})["value"]
        report["result"] = result
        # Preserve a closed script failure phase rather than overwrite it with
        # the surrounding result validator's generic failure.
        if type(result) is dict and "fatal" not in result:
            driver_status(work, "result-validation")
        check_result(result, args.expected_bytes)
        driver_status(work, "browser-stop")
        client.command("Marionette:Quit", {"flags":["eAttemptQuit"]})
        require(browser.wait(timeout=20) == 0)
        report["passed"] = True
    finally:
        if client is not None:
            client.socket.close()
        if browser is not None and browser.poll() is None:
            os.killpg(browser.pid, signal.SIGTERM)
            try:
                browser.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(browser.pid, signal.SIGKILL)
                browser.wait(timeout=5)
        report["cleanup"] = dict(browser_exited=browser is None or browser.poll() is not None,
                                 profile_removed=remove_profile(work))
        report["passed"] = report["passed"] and all(report["cleanup"].values())
        (work / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    require(report["passed"])
    driver_status(work, "complete")


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("stage", "output", "grant-a", "grant-b", "test-ca", "control-directory"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("url-a", "url-b", "expected-sha256", "core-revision", "parent-netns"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--expected-bytes", type=int, required=True)
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    guest_guard(args)
    require(re.fullmatch(r"[0-9a-f]{40}", args.core_revision) is not None
            and re.fullmatch(r"[0-9a-f]{64}", args.expected_sha256) is not None
            and args.expected_bytes == 33554432)
    stage, work = args.stage.resolve(strict=True), build_path(args.output)
    require(not stage.is_relative_to(work) and not work.is_relative_to(stage))
    if args.inside:
        private_directory(work)
        try:
            driver_status(work, "runtime-validation")
            inside(args, stage, work, validate_stage(stage))
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
            driver_status(work, "runtime-validation", error)
            raise
        return
    require(not work.exists() and not work.is_symlink())
    work.parent.mkdir(parents=True, exist_ok=True)
    work.mkdir(mode=0o700)
    driver_status(work, "wrapper-start")
    try:
        driver_status(work, "runtime-validation")
        validate_stage(stage)
        driver_status(work, "isolated-home")
        home_mounts = isolated_browser_home(work)
        runtime_mounts = isolated_runtime_parent(stage, work)
        driver_status(work, "control-namespace")
        control_mounts = control_namespace_mounts(args.control_directory)
        # Retain the fixture's CLIENT netns: the real app proxy is loopback there.
        # This entrypoint refuses the VM root/host namespace and effective capabilities.
        driver_status(work, "wrapper-launch")
        # The root-owned topology runner inherits vpci's private source cwd.
        # The application has no reason to enter that checkout: use its own
        # fresh work directory both before and after the mount namespace switch.
        subprocess.run(["/usr/bin/bwrap", "--die-with-parent", "--unshare-user", "--ro-bind", "/", "/",
            *runtime_mounts, *home_mounts, *control_mounts, "--bind", str(work), str(work),
            "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev",
            "--chdir", str(work), "--", sys.executable, "-B", str(Path(__file__).resolve()),
            *sys.argv[1:], "--inside"], cwd=work, check=True, timeout=295)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        driver_status(work, "wrapper-launch", error)
        raise


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
        print("core network browser driver failed", file=sys.stderr)
        raise SystemExit(1) from None
