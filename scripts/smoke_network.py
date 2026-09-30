#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Real Gecko channel/Unix/TLS smoke with a synthetic gateway, never a core-overlay proof."""

import argparse
import base64
import json
import os
from pathlib import Path
import signal
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time

from smoke_browser_startup import remove_profile
from smoke_compute_model import private_directory, validate_stage
from smoke_privacy import Marionette
from stage_firefox import ROOT, build_path, digest, isolated_browser_home, validate_isolated_browser_home

AUTHORITY = "network-fixture.invalid"
ORIGIN_PORT = 443
BODY = b"VOLPAROSSA synthetic network fixture\n"


def require(value):
    if not value:
        raise ValueError("network_fixture_failed")


def read_exact(stream, length):
    data = bytearray()
    while len(data) < length:
        part = stream.recv(length - len(data))
        require(bool(part))
        data.extend(part)
    return bytes(data)


def receive(stream):
    size, = struct.unpack("!I", read_exact(stream, 4))
    require(0 < size <= 4096)
    return json.loads(read_exact(stream, size))


def send(stream, reply):
    payload = json.dumps(reply, separators=(",", ":")).encode()
    require(len(payload) <= 4096)
    frame = struct.pack("!I", len(payload)) + payload
    # Deliberately split both header and UTF-8 body across stream writes.
    for part in (frame[:2], frame[2:7], frame[7:]):
        stream.sendall(part)


def request_headers(stream):
    data = bytearray()
    while not data.endswith(b"\r\n\r\n"):
        require(len(data) < 8192)
        data.extend(read_exact(stream, 1))
    lines = data.decode("ascii").split("\r\n")
    headers = {}
    for line in lines[1:-2]:
        key, value = line.split(":", 1)
        key = key.lower()
        require(key not in headers)
        headers[key] = value.strip()
    return lines[0], headers


def certificates(work):
    def openssl(*args):
        subprocess.run(["/usr/bin/openssl", *args], check=True, timeout=20,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj",
            "/CN=VOLPAROSSA synthetic fixture CA", "-keyout", str(work / "ca.key"), "-out", str(work / "ca.pem"),
            "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    openssl("req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=" + AUTHORITY,
            "-keyout", str(work / "leaf.key"), "-out", str(work / "leaf.csr"))
    (work / "leaf.ext").write_text("basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\nsubjectAltName=DNS:" + AUTHORITY + "\n")
    openssl("x509", "-req", "-in", str(work / "leaf.csr"), "-CA", str(work / "ca.pem"),
            "-CAkey", str(work / "ca.key"), "-set_serial", "1", "-days", "1", "-extfile", str(work / "leaf.ext"),
            "-out", str(work / "leaf.pem"))
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_3
    tls.load_cert_chain(work / "leaf.pem", work / "leaf.key")
    der = ssl.PEM_cert_to_DER_cert((work / "ca.pem").read_text())
    return tls, base64.b64encode(der).decode()


class SyntheticGateway:
    """Test peer only: no policy, core, WireGuard or MPTCP implementation substitute."""
    def __init__(self, path, tls):
        self.path, self.tls = path, tls
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(str(path))
        os.chmod(path, 0o600)
        self.listener.listen(4)
        self.listener.settimeout(1)
        self.stop = threading.Event()
        self.connections, self.threads, self.errors = [], [], []
        self.completed, self.detached = 0, 0
        self.connect_headers = set()
        self.connect_shape = []
        self.capabilities = [os.urandom(32).hex() for _ in range(4)]
        self.partition = os.urandom(32).hex()
        self.expiry = int(time.time() * 1000) + 120000
        self.thread = threading.Thread(target=self.accept, daemon=True)
        self.thread.start()

    def accept(self):
        while not self.stop.is_set():
            try:
                connection, _ = self.listener.accept()
                connection.settimeout(50)
                self.connections.append(connection)
                thread = threading.Thread(target=self.attach, args=(connection,), daemon=True)
                self.threads.append(thread)
                thread.start()
            except socket.timeout:
                continue
            except OSError:
                break

    def attach(self, connection):
        proxy, thread = None, None
        closed = threading.Event()
        try:
            request = receive(connection)
            require(set(request) == {"version", "capability", "partition"}
                    and request["version"] == 1 and request["partition"] == self.partition)
            index = self.capabilities.index(request["capability"])
            if index == 3:  # Fixed denial/EOF, never an availability-to-direct permission.
                return
            proxy = socket.socket()
            proxy.bind(("127.0.0.1", 0))
            proxy.listen(4)
            proxy.settimeout(.2)
            bearer = "Bearer " + os.urandom(32).hex()
            send(connection, dict(version=1, proxy_host="127.0.0.1", proxy_port=proxy.getsockname()[1],
                proxy_authorization=bearer, hostname=AUTHORITY if index != 2 else "wrong.invalid", port=ORIGIN_PORT,
                partition=self.partition, expires_at_ms=self.expiry, overlay_only=True))
            if index != 2:
                thread = threading.Thread(target=self.proxy, args=(proxy, bearer, closed), daemon=True)
                thread.start()
            require(connection.recv(1) == b"")
            self.detached += 1
        except (OSError, ValueError):
            if not self.stop.is_set():
                self.errors.append("synthetic_attach_failed")
        finally:
            closed.set()
            if proxy is not None:
                proxy.close()
            if thread is not None:
                thread.join(timeout=3)
                if thread.is_alive():
                    self.errors.append("synthetic_proxy_not_reaped")
            connection.close()

    def proxy(self, listener, bearer, closed):
        while not closed.is_set():
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                connection.settimeout(15)
                line, headers = request_headers(connection)
                self.connect_headers.update(headers)
                if len(self.connect_shape) < 12:
                    self.connect_shape.append(dict(method=line.startswith("CONNECT "),
                        exact_line=line == f"CONNECT {AUTHORITY}:{ORIGIN_PORT} HTTP/1.1",
                        authorization_present="proxy-authorization" in headers,
                        authorization_matches=headers.get("proxy-authorization") == bearer,
                        host_matches=headers.get("host", f"{AUTHORITY}:{ORIGIN_PORT}") == f"{AUTHORITY}:{ORIGIN_PORT}"))
                require(line == f"CONNECT {AUTHORITY}:{ORIGIN_PORT} HTTP/1.1")
                require(headers.get("proxy-authorization") == bearer)
                require(headers.get("host", f"{AUTHORITY}:{ORIGIN_PORT}") == f"{AUTHORITY}:{ORIGIN_PORT}")
                self.connect_headers.update(headers)
                require(set(headers) <= {"host", "proxy-authorization", "user-agent", "proxy-connection", "connection"})
                connection.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                with self.tls.wrap_socket(connection, server_side=True) as secure:
                    request, origin_headers = request_headers(secure)
                    require(request == "GET /fixture HTTP/1.1" and "proxy-authorization" not in origin_headers)
                    require(all(bearer not in value for value in origin_headers.values()))
                    require(secure.version() == "TLSv1.3")
                    secure.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\nContent-Length: "
                                   + str(len(BODY)).encode() + b"\r\n\r\n" + BODY)
                    self.completed += 1
            except (OSError, ValueError):
                self.errors.append("synthetic_proxy_failed")
            finally:
                connection.close()

    def close(self):
        self.stop.set()
        self.listener.close()
        for connection in self.connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        self.thread.join(timeout=3)
        for thread in self.threads:
            thread.join(timeout=4)
        require(not self.thread.is_alive() and not any(thread.is_alive() for thread in self.threads))
        self.path.unlink()


SCRIPT = r"""
const [moduleRoot, socketPath, capability, partition, expiry, uid, certificate, scratch, originPort, done] = arguments;
let phase = "import";
(async () => {
  const directory = Cc["@mozilla.org/file/local;1"].createInstance(Ci.nsIFile);
  directory.initWithPath(moduleRoot);
  Services.io.getProtocolHandler("resource").QueryInterface(Ci.nsIResProtocolHandler)
    .setSubstitution("volparossa-network-test", Services.io.newFileURI(directory));
  const {VolparossaNetwork, validateNetworkGrant, validateNetworkReady} = ChromeUtils.importESModule(
    "resource://volparossa-network-test/VolparossaNetwork.sys.mjs");
  phase = "certificate";
  Cc["@mozilla.org/security/x509certdb;1"].getService(Ci.nsIX509CertDB).addCertFromBase64(certificate, "C,,");
  const grant = i => ({version:1, app_uid:uid, app_socket:socketPath, capability:capability[i],
    hostname:"network-fixture.invalid", port:originPort, partition, expires_at_ms:expiry, overlay_only:true});
  const principal = Services.scriptSecurityManager.getSystemPrincipal();
  const channel = url => Services.io.newChannelFromURI(Services.io.newURI(url), null, principal, null,
    Ci.nsILoadInfo.SEC_ALLOW_CROSS_ORIGIN_SEC_CONTEXT_IS_NULL, Ci.nsIContentPolicy.TYPE_OTHER);
  const proxyResponses=[];
  const fetch = (owner, url=`https://network-fixture.invalid:${originPort}/fixture`) => new Promise((resolve,reject) => {
    let body="";
    try {
      owner.openChannel(channel(url), {
        onStartRequest(request) {
          try { proxyResponses.push(request.QueryInterface(Ci.nsIProxiedChannel).httpProxyConnectResponseCode); }
          catch(error) { reject(error); request.cancel(Cr.NS_ERROR_ABORT); }
        },
        onDataAvailable(_request, input, _offset, count) {
          if (body.length + count > 4096) throw new Error("fixture_body_bound");
          const reader=Cc["@mozilla.org/binaryinputstream;1"].createInstance(Ci.nsIBinaryInputStream);
          reader.setInputStream(input); body += reader.readBytes(count);
        },
        onStopRequest(_request,status) { if (Components.isSuccessCode(status)) resolve(body); else reject(new Error("channel_failed")); }
      });
    } catch(error) { reject(error); }
  });
  const denied = async operation => { try { await operation(); return false; } catch { return true; } };
  const result = {};
  phase = "driver-write";
  // Exercise the actual APIs used by the real-core driver's bounded observer
  // markers and incremental hashing, without a synthetic model or core.
  await IOUtils.writeJSON(scratch + "/observer.tmp", {version:1,detach:true}, {mode:"create"});
  await IOUtils.move(scratch + "/observer.tmp", scratch + "/observer.json", {noOverwrite:true});
  phase = "driver-read";
  const marker=JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(
    await IOUtils.read(scratch + "/observer.json", {maxBytes:128})));
  result.observer_api=await IOUtils.exists(scratch + "/observer.json") && marker.version === 1 && marker.detach === true;
  await IOUtils.remove(scratch + "/observer.json");
  phase = "driver-stream";
  const stream=Cc["@mozilla.org/io/string-input-stream;1"].createInstance(Ci.nsIStringInputStream);
  stream.setByteStringData("abc");
  phase = "driver-hash";
  const hash=Cc["@mozilla.org/security/hash;1"].createInstance(Ci.nsICryptoHash);
  hash.init(Ci.nsICryptoHash.SHA256); hash.updateFromStream(stream,3);
  result.streaming_hash_api=Array.from(hash.finish(false), c => c.charCodeAt(0).toString(16).padStart(2,"0")).join("")
    === "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
  let first, second;
  try {
    phase = "attach-a";
    first = await VolparossaNetwork.attach(grant(0));
    phase = "attach-b";
    second = await VolparossaNetwork.attach(grant(1));
    result.two_live = first.active && second.active && first._isolation !== second._isolation;
    phase = "request-a";
    result.first_body = await fetch(first) === "VOLPAROSSA synthetic network fixture\n";
    phase = "request-b";
    result.second_body = await fetch(second) === "VOLPAROSSA synthetic network fixture\n";
    result.scope_blocked = await denied(() => fetch(first, "https://other.invalid/fixture"));
    result.http_blocked = await denied(() => fetch(first, "http://network-fixture.invalid/fixture"));
    first.close();
    result.detached_blocked = await denied(() => fetch(first));
    result.other_survives = second.active && await fetch(second) === "VOLPAROSSA synthetic network fixture\n";
    result.proxy_status_api = proxyResponses.length === 3 && proxyResponses.every(value => value === 200);
    result.wrong_ready_blocked = await denied(() => VolparossaNetwork.attach(grant(2)));
    result.denial_blocked = await denied(() => VolparossaNetwork.attach(grant(3)));
    result.grant_schema = await denied(() => validateNetworkGrant({...grant(0), unknown:true}));
    result.expired_blocked = await denied(() => validateNetworkGrant({...grant(0), expires_at_ms:1}));
    result.raw_ip_blocked = await denied(() => validateNetworkGrant({...grant(0), hostname:"127.0.0.1"}));
  } finally { first?.close(); second?.close(); }
  done(result);
})().catch(error => done({fatal:"network_fixture_failed", phase,
  code:["invalid_contract","unavailable","invalid_channel","scope_unavailable","unsupported_runtime","request_failed"].includes(error.code)
    ? error.code : "unexpected", result:Number.isInteger(error.result) ? error.result : null,
  error_type:["ReferenceError","TypeError","DOMException"].includes(error.name) ? error.name : "other"}));
"""


def inside(args, stage, work, metadata):
    require(args.host_netns and os.readlink("/proc/self/ns/net") != args.host_netns)
    require(all(os.statvfs(path).f_flag & os.ST_RDONLY for path in (Path("/"), ROOT, stage)))
    validate_isolated_browser_home(work)
    links = json.loads(subprocess.check_output(["/usr/bin/ip", "-j", "link", "show"], text=True))
    require([link["ifname"] for link in links] == ["lo"] and "UP" in links[0]["flags"])
    for name in ("profile", "config", "cache", "runtime", "tmp"):
        (work / name).mkdir(mode=0o700)
    (work / "profile/prefs.js").write_text('user_pref("remote.prefs.recommended", false);\n')
    tls, certificate = certificates(work)
    # /tmp is a fresh tmpfs in this bwrap; bounded Unix paths even in long worktrees.
    socket_dir = Path(tempfile.mkdtemp(prefix="volparossa-network-", dir="/tmp"))
    gateway = SyntheticGateway(socket_dir / "app.sock", tls)
    environment = dict(os.environ, MOZ_NO_REMOTE="1", MOZ_CRASHREPORTER_DISABLE="1",
        XDG_CONFIG_HOME=str(work / "config"), XDG_CACHE_HOME=str(work / "cache"),
        XDG_RUNTIME_DIR=str(work / "runtime"), TMPDIR=str(work / "tmp"))
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "DBUS_SYSTEM_BUS_ADDRESS",
                "MOZ_LOG", "MOZ_LOG_FILE", "NSPR_LOG_MODULES", "NSPR_LOG_FILE"):
        environment.pop(key, None)
    browser, client, result = None, None, None
    report = dict(version=1, kind="real-gecko-scoped-https-with-synthetic-gateway", passed=False,
        core_overlay_proven=False, full_browser_killswitch=False, firefox157_build_proven=False,
        runtime_version=metadata["version"], runtime_source_stamp=metadata["source_stamp"],
        module_sha256=digest(ROOT / "integration/VolparossaNetwork.sys.mjs"),
        host_read_only=True, interfaces=["lo"], origin_port=ORIGIN_PORT)
    try:
        browser = subprocess.Popen([str(stage / metadata["executable"]), "--headless", "--no-remote", "--new-instance",
            "--profile", str(work / "profile"), "--marionette", "--remote-allow-system-access", "about:blank"],
            env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            require(browser.poll() is None)
            try:
                connection = socket.create_connection(("127.0.0.1", 2828), timeout=1)
                connection.settimeout(80)
                client = Marionette(connection)
                break
            except (ConnectionRefusedError, TimeoutError):
                time.sleep(.2)
        require(client is not None)
        client.command("WebDriver:NewSession", {"capabilities":{"alwaysMatch":{}}})
        client.command("Marionette:SetContext", {"value":"chrome"})
        client.command("WebDriver:SetTimeouts", {"script":70000})
        result = client.command("WebDriver:ExecuteAsyncScript", {"script":SCRIPT,
            "args":[str(ROOT / "integration"), str(gateway.path), gateway.capabilities, gateway.partition,
                    gateway.expiry, os.getuid(), certificate, str(work / "tmp"), ORIGIN_PORT], "newSandbox":True, "sandbox":"system"})["value"]
        expected = {name: True for name in ("two_live", "first_body", "second_body", "scope_blocked", "http_blocked",
            "detached_blocked", "other_survives", "wrong_ready_blocked", "denial_blocked", "grant_schema",
            "expired_blocked", "raw_ip_blocked", "observer_api", "streaming_hash_api", "proxy_status_api")}
        report["cases"] = result
        require(result == expected)
        deadline = time.monotonic() + 5
        while gateway.detached != 3 and time.monotonic() < deadline:
            time.sleep(.05)
        require(gateway.completed == 3 and gateway.detached == 3 and not gateway.errors)
        report.update(origin_capability_absent=True, actual_tls13_responses=3,
                      connect_header_names=sorted(gateway.connect_headers), independent_detach=True)
        client.command("Marionette:Quit", {"flags":["eAttemptQuit"]})
        require(browser.wait(timeout=20) == 0)
        report["passed"] = True
    finally:
        report["gateway_errors"] = list(gateway.errors)
        report["completed_responses"] = gateway.completed
        report["observed_connect_headers"] = sorted(gateway.connect_headers)
        report["connect_shape"] = gateway.connect_shape
        if client is not None:
            client.socket.close()
        if browser is not None and browser.poll() is None:
            os.killpg(browser.pid, signal.SIGTERM)
            try:
                browser.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(browser.pid, signal.SIGKILL)
                browser.wait(timeout=5)
        gateway.close()
        socket_dir.rmdir()
        for name in ("ca.key", "ca.pem", "leaf.key", "leaf.csr", "leaf.ext", "leaf.pem"):
            (work / name).unlink(missing_ok=True)
        report["cleanup"] = dict(browser_exited=browser is None or browser.poll() is not None,
                                 profile_removed=remove_profile(work), sockets_reaped=not gateway.path.exists())
        report["passed"] = report["passed"] and all(report["cleanup"].values())
        (work / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    require(report["passed"])


def main():
    global ORIGIN_PORT
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--origin-port", type=int, choices=(443, 18443), default=443,
                        help="Explicit synthetic CONNECT destination port; no host listener is opened.")
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--host-netns", help=argparse.SUPPRESS)
    args = parser.parse_args()
    ORIGIN_PORT = args.origin_port
    stage = args.stage.resolve(strict=True)
    work = build_path(args.output)
    require(not stage.is_relative_to(work) and not work.is_relative_to(stage))
    metadata = validate_stage(stage)
    if args.inside:
        private_directory(work)
        inside(args, stage, work, metadata)
        return
    require(not work.exists() and not work.is_symlink())
    work.parent.mkdir(parents=True, exist_ok=True)
    work.mkdir(mode=0o700)
    home_mounts = isolated_browser_home(work)
    subprocess.run(["/usr/bin/bwrap", "--die-with-parent", "--unshare-user", "--unshare-net", "--ro-bind", "/", "/",
        *home_mounts, "--bind", str(work), str(work), "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev", "--",
        sys.executable, "-B", str(Path(__file__).resolve()), *sys.argv[1:], "--inside", "--host-netns",
        os.readlink("/proc/self/ns/net")], check=True, timeout=150)
    print(json.dumps({"passed":True,"report":str(work / "report.json")}))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
        print("network gateway fixture failed", file=sys.stderr)
        raise SystemExit(1) from None
