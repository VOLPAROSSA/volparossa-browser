#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Opt-in signed-uBO selection/SQLite-owner proof; default prints a plan only.

Three real ESR processes share ONE disposable profile. Only loopback synthetic
lists and public probes are served. No broker, signed publication, production
startup/resume barrier, addon repacking, model, download or default activation.
"""
import argparse
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import resource
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlencode

from smoke_ubo_actor import module_bytes, private_directory, prepare_output, validate_retained_bundle
from smoke_privacy import Marionette, snapshot
from smoke_consent import UBLOCK, addon_page, all_addons_active, navigate, require, until, validate_isolation
from smoke_browser_startup import remove_profile
from smoke_compute_model import validate_stage
from stage_firefox import ROOT, build_path, digest, isolated_browser_home, load_defaults

ORIGIN = "http://127.0.0.1:18765"
KEY = ORIGIN + "/supplement.txt"
CUSTOM_ON = ORIGIN + "/custom-on.txt"
CUSTOM_OFF = ORIGIN + "/custom-off.txt"
STOCKS = ("easylist", "easyprivacy", "plowe-0", "ublock-badware", "ublock-filters",
          "ublock-privacy", "ublock-quick-fixes", "ublock-unbreak", "urlhaus-1", "user-filters")
SESSIONS = ("enroll", "remove", "restart")
OPERATIONS = ("open", "observe", "enroll", "suspend", "resume", "close")
CASES = ("baseline", "enrolled", "suspended", "resumed", "removed", "restarted")
TRACE_OPERATIONS = ("prepare", "browser_start", "browser_connect", "browser_identity", "browser_quit",
                    "custom_subscribe_on", "custom_subscribe_off", "custom_deselect", "user_remove",
                    "selection_present", "selection_absent", "complete",
                    *("owner_" + op for op in OPERATIONS), *("probe_" + case for case in CASES))
MODULE_NAMES = ("Owner", "Journal", "Selection", "Actor", "ActorContract",
                "VolparossaFilterSelectionParent", "VolparossaFilterSelectionChild", "Contract", "Frame")
MODULES = {"integration/filters/" + name + ".sys.mjs": name + ".sys.mjs" for name in MODULE_NAMES}
MODULES["tests/fixtures/filter_selection.sys.mjs"] = "Fixture.sys.mjs"
# Complete reviewed dependency set. Never learn execution pins from live files.
PINS = {
    "integration/filters/Owner.sys.mjs": "0460e77f1ba4907b89f03608a9e6c948d92b875a88818cafa35872d7fa93a928",
    "integration/filters/Journal.sys.mjs": "2b90fa020c21c4644180e0346c23c5c7dc2cd876eb6e3dc6ad59fbe92ad95dd2",
    "integration/filters/Selection.sys.mjs": "1fc0f83e426ac5068a5d8c78ae9b286dd0a0d51321b8bdf7182ade1fba2ddb28",
    "integration/filters/Actor.sys.mjs": "df38bd93bca44227649bd40f6a9e737a62852e0327a36c165424860175e088f4",
    "integration/filters/ActorContract.sys.mjs": "23694e3ed3d85d38586b98eb472ed4483ebcc98c6eb192881106d9ddf9594047",
    "integration/filters/VolparossaFilterSelectionParent.sys.mjs": "b04b38aee8678a184ec4ce953ef49491bf1834061e606ae467165924c140b75c",
    "integration/filters/VolparossaFilterSelectionChild.sys.mjs": "1d3a7b50792c8add79f936223a1c8b28ca5643179cf9081bcfc33e4301af99aa",
    "integration/filters/Contract.sys.mjs": "e4a631eae4590115d4e54a196529fa17ad4b7955bb184ae1edced919b69cd15b",
    "integration/filters/Frame.sys.mjs": "8f01606ca2f359b888948d13dd489d96cc42b567b88f9996716168d8500b4ca5",
    "tests/fixtures/filter_selection.sys.mjs": "b4215691d5b977751789ee992a0877efe9baa3760bc7bd79da61d6db66c78b22",
}
PROFILE_MODULES = "volparossa-filter-selection"
MAX_LOG = 4 * 1024 * 1024
SCOPE = dict(original_signed_ubo=True, actual_actor_and_sqlite_owner=True,
             synthetic_publication_grant=True, browser_sessions=3, custom_imports=2,
             original_stock_lists=10, production_default_enrollment=False,
             startup_resume_stale_filter_barrier=False, authorized_network_list=False,
             native_admission_registered=False, immutable_content_verified=False,
             real_os_expiry=False, universal_custom_list_support=False)

BOOTSTRAP = r"""
const [operation, done] = arguments;
(async () => {
  if (!["open", "observe", "enroll", "suspend", "resume", "close"].includes(operation))
    throw new Error("selection_fixture_operation");
  const directory = Services.dirsvc.get("ProfD", Ci.nsIFile);
  for (const part of ["chrome", "volparossa-filter-selection"]) {
    directory.append(part);
    if (directory.isSymlink() || !directory.isDirectory() || (directory.permissions & 0o7777) !== 0o700)
      throw new Error("selection_fixture_directory");
  }
  Services.io.getProtocolHandler("resource").QueryInterface(Ci.nsIResProtocolHandler)
    .setSubstitution("volparossa-filter-selection", Services.io.newFileURI(directory));
  const {exercise} = ChromeUtils.importESModule("resource://volparossa-filter-selection/Fixture.sys.mjs");
  return exercise(operation);
})().then(done, () => done({schema: 1, ok: false, phase: "bootstrap"}));
"""


def pinned():
    return (type(PINS) is dict and set(PINS) == set(MODULES)
            and all(isinstance(value, str) and len(value) == 64
                    and all(char in "0123456789abcdef" for char in value) for value in PINS.values()))


def verify_sources():
    require(pinned(), "selection_fixture_unfrozen")
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


def owner_result(value, operation):
    require(operation in OPERATIONS)
    require(type(value) is dict and set(value) == {"schema", "ok", "phase", "status", "result", "invalidations"}
            and type(value["schema"]) is int and value["schema"] == 1 and value["ok"] is True
            and value["phase"] == operation and type(value["invalidations"]) is int
            and 0 <= value["invalidations"] <= 32, "selection_fixture_failed")
    status = value["status"]
    require(type(status) is dict and set(status) == {"schema", "choice", "state", "closed", "failed"}
            and type(status["schema"]) is int and status["schema"] == 1
            and status["choice"] in ("eligible", "opted-out") and status["state"] in ("fresh", "pending", "active", "suspended")
            and not (status["choice"] == "opted-out" and status["state"] == "active")
            and status["closed"] is (operation == "close") and status["failed"] is False)
    result = value["result"]
    if operation in ("open", "close"):
        require(result is None)
        return value
    require(type(result) is dict and set(result) == {"schema", "choice", "state", "attempted", "receipt"}
            and type(result["schema"]) is int and result["schema"] == 1
            and all(result[key] == status[key] for key in ("choice", "state")) and type(result["attempted"]) is bool)
    receipt = result["receipt"]
    if receipt is None:
        require(result["attempted"] is False)
    else:
        require(type(receipt) is dict and type(receipt.get("selected")) is bool and type(receipt.get("imported")) is bool)
        if operation == "observe":
            require(set(receipt) == {"selected", "imported"} and result["attempted"] is False)
        else:
            require(set(receipt) == {"selected", "imported", "preserved", "freshReload", "reloadEvents"}
                    and result["attempted"] is True and receipt["preserved"] is True and receipt["freshReload"] is True
                    and type(receipt["reloadEvents"]) is int and 2 <= receipt["reloadEvents"] <= 32
                    and receipt["selected"] is (operation != "suspend") and receipt["imported"] is receipt["selected"])
    return value


def mutation(value, operation):
    require(operation in ("enroll", "suspend", "resume"))
    value = owner_result(value, operation)
    require(value["result"]["attempted"] is True, "selection_fixture_mutation_unproved")
    return value


def owner(client, operation):
    require(operation in OPERATIONS)
    client.command("Marionette:SetContext", {"value": "chrome"})
    client.command("WebDriver:SetTimeouts", {"script": 55000})
    previous = client.socket.gettimeout(); client.socket.settimeout(60)
    try:
        result = client.command("WebDriver:ExecuteAsyncScript", {"script": BOOTSTRAP,
            "args": [operation], "newSandbox": True, "sandbox": "system"})["value"]
        return owner_result(result, operation)
    finally:
        client.socket.settimeout(previous)


def selection(client, supplement):
    # Unlike 3p-filters.html, original about.html does not request getLists.
    # This fixture-only query exports no URL or other user-storage value.
    addon_page(client, UBLOCK, "about.html")
    result = client.command("WebDriver:ExecuteAsyncScript", {"script": r"""
      const [stocks, on, off, own, present, done] = arguments;
      const page = window.wrappedJSObject;
      const same = (a, b) => Array.isArray(a) && a.length === b.length
        && new Set(a).size === a.length && a.every(key => b.includes(key));
      (async () => {
        const value = await page.browser.storage.local.get(page.Array.of("selectedFilterLists", "importedLists"));
        return {selected_preserved: same(value.selectedFilterLists, [...stocks, on, ...(present ? [own] : [])]),
          imports_preserved: same(value.importedLists, [on, off, ...(present ? [own] : [])])};
      })().then(done, () => done(null));
    """, "args": [list(STOCKS), CUSTOM_ON, CUSTOM_OFF, KEY, supplement],
        "newSandbox": True, "sandbox": "system"})["value"]
    require(type(result) is dict and set(result) == {"selected_preserved", "imports_preserved"}
            and all(value is True for value in result.values()), "selection_fixture_baseline_changed")
    return result


def subscribe(client, key):
    require(key in (CUSTOM_ON, CUSTOM_OFF))
    addon_page(client, UBLOCK, "asset-viewer.html?" + urlencode({"url": key, "subscribe": "1"}))
    until(client, "return document.querySelector('#subscribeButton') && !document.body.classList.contains('loading');")
    client.script("document.querySelector('#subscribeButton').click(); return true;")
    until(client, "return document.querySelector('#subscribe').classList.contains('hide');")


def user_change(client, key, remove=False):
    require(key == (KEY if remove else CUSTOM_OFF))
    addon_page(client, UBLOCK, "3p-filters.html")
    until(client, "return Array.from(document.querySelectorAll('#lists .listEntry[data-role=leaf]')).some(e=>e.dataset.key===arguments[0]);", [key])
    require(client.script("""
      const entry=Array.from(document.querySelectorAll('#lists .listEntry[data-role=leaf]')).find(e=>e.dataset.key===arguments[0]);
      if (!entry || !entry.classList.contains('external')) return false;
      if (arguments[1]) entry.querySelector('.remove').click();
      else {const box=entry.querySelector('input[type=checkbox]'); if (!box.checked) return false; box.click();}
      return true;
    """, [key, remove]))
    until(client, "return !document.querySelector('#buttonApply').classList.contains('disabled');")
    client.script("document.querySelector('#buttonApply').click(); return true;")
    until(client, "return document.querySelector('#buttonApply').classList.contains('disabled') && !document.body.classList.contains('working');")
    # Do not leave the dashboard listening to actor-triggered reload broadcasts.
    navigate(client, "about:blank")


class FixtureState:
    def __init__(self):
        self.counters = {case: {"probe": 0, "essential": 0} for case in CASES}
        self.reads = {name: 0 for name in ("supplement", "custom-on", "custom-off")}

    def response(self, host, path):
        require(host == "127.0.0.1:18765" and isinstance(path, str) and len(path) <= 128)
        if path in ("/supplement.txt", "/custom-on.txt", "/custom-off.txt"):
            name = path[1:-4]
            require(self.reads[name] < 32); self.reads[name] += 1
            rule = f"|{ORIGIN}/probe^$xmlhttprequest" if name == "supplement" else "||selection-fixture-unused.invalid^"
            return 200, ("[Adblock Plus 2.0]\n" + rule + "\n").encode(), "text/plain"
        for case in CASES:
            if path == "/page/" + case:
                body = ("<!doctype html><meta charset=utf-8><script>"
                    "Promise.all(['probe','essential'].map(async k=>{try{const r=await fetch('/'+k+'/" + case
                    + "');document.documentElement.dataset[k]=r.ok?'loaded':'other';}catch{document.documentElement.dataset[k]='blocked';}}));"
                    "</script>").encode()
                return 200, body, "text/html"
            for kind in ("probe", "essential"):
                if path == "/" + kind + "/" + case:
                    require(self.counters[case][kind] < 4); self.counters[case][kind] += 1
                    return 200, b"synthetic", "text/plain"
        return 404, b"", "text/plain"


@contextmanager
def serve():
    state = FixtureState()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def setup(self):
            super().setup(); self.connection.settimeout(3)
        def do_GET(self):
            try: status, body, mime = state.response(self.headers.get("Host"), self.path)
            except ValueError: status, body, mime = 400, b"", "text/plain"
            self.send_response(status); self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close"); self.end_headers(); self.wfile.write(body)
    server = HTTPServer(("127.0.0.1", 18765), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try: yield state.counters, state.reads
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
        require(not thread.is_alive())


def probe(client, counters, case, blocked):
    require(case in CASES)
    navigate(client, ORIGIN + "/page/" + case)
    until(client, "return document.documentElement.dataset.probe && document.documentElement.dataset.essential;")
    value = client.script("return {probe:document.documentElement.dataset.probe, essential:document.documentElement.dataset.essential};")
    require(value == {"probe": "blocked" if blocked else "loaded", "essential": "loaded"}
            and counters[case] == {"probe": 0 if blocked else 1, "essential": 1}, "selection_fixture_probe_failed")
    return dict(counters[case])


def clean_environment(work):
    return dict(PATH="/usr/bin:/bin", HOME=str(Path.home()), LANG="C.UTF-8", LC_ALL="C.UTF-8",
                PYTHONDONTWRITEBYTECODE="1", MOZ_NO_REMOTE="1", MOZ_CRASHREPORTER_DISABLE="1",
                MOZ_CRASHREPORTER_NO_REPORT="1", MOZ_DISABLE_NONLOCAL_CONNECTIONS="1",
                XDG_CONFIG_HOME=str(work / "config"), XDG_CACHE_HOME=str(work / "cache"),
                XDG_RUNTIME_DIR=str(work / "runtime"), TMPDIR=str(work / "tmp"))


def limits():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def stop_process(process):
    if process is None or process.poll() is not None: return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try: os.killpg(process.pid, sig)
        except ProcessLookupError: pass
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: continue
        return
    require(process.poll() is not None, "selection_fixture_process_cleanup")


class PrivateLogDrain:
    """Drain one owned process pipe, never cap its SQLite/cache file sizes."""
    def __init__(self, process, output):
        self.process, self.output = process, output
        self.failed = threading.Event()
        self.thread = threading.Thread(target=self._read, daemon=True)

    def start(self): self.thread.start()

    def _read(self):
        total = 0
        try:
            while True:
                data = self.process.stdout.read1(min(4096, MAX_LOG - total + 1))
                if not data: break
                accepted = data[:MAX_LOG - total]
                self.output.write(accepted); self.output.flush(); total += len(accepted)
                require(len(accepted) == len(data), "selection_fixture_log_limit")
        except Exception:
            self.failed.set()
            try: os.killpg(self.process.pid, signal.SIGTERM)
            except OSError: pass
        finally:
            try: self.process.stdout.close()
            except Exception: self.failed.set()

    def finish(self):
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            self.failed.set()
            # A surviving descendant may still hold stdout after the parent
            # exits. This is the group's owned PID, never a caller-supplied ID.
            try: os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            self.thread.join(timeout=5)
        require(not self.thread.is_alive() and not self.failed.is_set(), "selection_fixture_log_cleanup")


def joined_success(process):
    process.wait(timeout=20)
    require(type(process.returncode) is int and process.returncode == 0, "selection_fixture_browser_exit")


def close_session(client, process, drain, profile):
    try:
        if client: client.socket.close()
    finally:
        try: stop_process(process)
        finally:
            try:
                if drain: drain.finish()
            finally: profile_modules(profile)


def browser(stage, work, phase, callback, mark):
    require(phase in SESSIONS)
    profile_modules(work / "profile")
    process = None; client = None; drain = None
    with (work / (phase + ".log")).open("xb") as log:
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
            client.command("Marionette:SetContext", {"value": "chrome"})
            mark("browser_identity")
            require(Path(client.script('return Services.dirsvc.get("GreD", Ci.nsIFile).path;')).resolve() == stage)
            preferences = snapshot(client, list(load_defaults()) + ["privacy.trackingprotection.enabled", "remote.prefs.recommended"])
            require(preferences["remote.prefs.recommended"]["effective"] is False)
            all_addons_active(client)
            value = callback(client)
            mark("owner_close"); owner(client, "close")
            all_addons_active(client)
            require(snapshot(client, list(preferences)) == preferences)
            mark("browser_quit")
            client.command("Marionette:Quit", {"flags": ["eAttemptQuit"]}); joined_success(process)
            drain.finish()
            return dict(exit_status=0, result=value)
        finally:
            close_session(client, process, drain, work / "profile")


def write_operation(work, report, operation):
    require(operation in TRACE_OPERATIONS and report["phase"] in ("prepare", *SESSIONS, "complete"))
    report["operation"] = operation
    value = dict(schema=1, phase=report["phase"], operation=operation)
    path = work / "operation.json"
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600), "w") as stream:
        json.dump(value, stream, separators=(",", ":"))


def read_operation(work):
    path = work / "operation.json"
    if not path.exists(): return None
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 256)
    value = json.loads(path.read_text())
    require(type(value) is dict and set(value) == {"schema", "phase", "operation"}
            and type(value["schema"]) is int and value["schema"] == 1
            and value["phase"] in ("prepare", *SESSIONS, "complete") and value["operation"] in TRACE_OPERATIONS)
    return value


def campaign(stage, work, report):
    mark = lambda operation: write_operation(work, report, operation)
    def checked_owner(client, operation):
        mark("owner_" + operation); return owner(client, operation)
    def checked_selection(client, present):
        mark("selection_present" if present else "selection_absent"); return selection(client, present)
    def checked_probe(client, counters, case, blocked):
        mark("probe_" + case); return probe(client, counters, case, blocked)
    for name in ("profile", "config", "cache", "runtime", "tmp"):
        (work / name).mkdir(mode=0o700)
    profile_modules(work / "profile", create=True)
    (work / "profile/prefs.js").write_text('user_pref("remote.prefs.recommended", false);\n')
    with serve() as (counters, reads):
        def enroll(client):
            mark("custom_subscribe_on"); subscribe(client, CUSTOM_ON)
            mark("custom_subscribe_off"); subscribe(client, CUSTOM_OFF)
            mark("custom_deselect"); user_change(client, CUSTOM_OFF)
            checked_selection(client, False); checked_probe(client, counters, "baseline", False)
            checked_owner(client, "open")
            changed = mutation(checked_owner(client, "enroll"), "enroll")
            require(changed["status"]["state"] == "active" and changed["result"]["attempted"] is True)
            checked_selection(client, True)
            return dict(enrollment=changed, probe=checked_probe(client, counters, "enrolled", True))
        def remove(client):
            opened = checked_owner(client, "open"); require(opened["status"]["state"] == "active")
            checked_selection(client, True)
            suspended = mutation(checked_owner(client, "suspend"), "suspend")
            require(suspended["status"]["choice"] == "eligible" and suspended["status"]["state"] == "suspended")
            checked_selection(client, False); checked_probe(client, counters, "suspended", False)
            resumed = mutation(checked_owner(client, "resume"), "resume"); require(resumed["status"]["state"] == "active")
            checked_selection(client, True); checked_probe(client, counters, "resumed", True)
            mark("user_remove"); user_change(client, KEY, remove=True)
            observed = checked_owner(client, "observe")
            require(observed["status"]["choice"] == "opted-out" and observed["status"]["state"] == "suspended")
            declined = checked_owner(client, "enroll"); require(declined["result"]["attempted"] is False)
            checked_selection(client, False)
            return dict(suspended=suspended, resumed=resumed, removal=observed, declined=declined,
                        probe=checked_probe(client, counters, "removed", False))
        def restart(client):
            opened = checked_owner(client, "open")
            require(opened["status"]["choice"] == "opted-out" and opened["status"]["state"] == "suspended")
            declined = checked_owner(client, "enroll"); require(declined["result"]["attempted"] is False)
            checked_selection(client, False)
            return dict(declined=declined, probe=checked_probe(client, counters, "restarted", False))
        for phase, action in zip(SESSIONS, (enroll, remove, restart)):
            report["phase"] = phase
            report["sessions"][phase] = browser(stage, work, phase, action, mark)
        require(reads["supplement"] > 0 and reads["custom-on"] > 0 and reads["custom-off"] > 0)
        report["synthetic_list_reads"] = dict(reads)
    require(all(counters[case] == {"probe": 0 if case in ("enrolled", "resumed") else 1, "essential": 1}
                for case in CASES))
    report["probes"] = {case: dict(counters[case]) for case in CASES}
    report["selected_and_deselected_custom_lists_preserved"] = True


def cleanup(work):
    # Retain original bounded private logs, including failures. Never remove
    # runtime evidence along with disposable profile/cache/database state.
    return remove_profile(work)


def log_receipts(work):
    receipts = {}
    for phase in SESSIONS:
        log = work / (phase + ".log")
        if log.exists():
            require(log.is_file() and not log.is_symlink() and log.stat().st_size <= MAX_LOG)
            receipts[phase] = dict(bytes=log.stat().st_size, sha256=digest(log))
    return receipts


def inside(stage, work, metadata, host_netns):
    validate_isolation(stage, work, host_netns); verify_sources()
    # Set once before the HTTP/log threads exist; never run Python preexec code
    # in a forked child of this threaded fixture. Firefox inherits no-core mode.
    limits()
    report = dict(schema=1, passed=False, scope=SCOPE, phase="prepare", sessions={},
                  module_sha256=PINS, driver_sha256=digest(Path(__file__)),
                  runtime_version=metadata["version"], runtime_source_stamp=metadata["source_stamp"],
                  runtime_sha256=metadata["runtime_sha256"], extension_packages=metadata["extensions"]["packages"],
                  failure="fixture_failed")
    try:
        campaign(stage, work, report)
        verify_sources(); validate_retained_bundle(stage, metadata)
        require(validate_stage(stage) == metadata)
        report["passed"] = True; report["phase"] = "complete"; write_operation(work, report, "complete")
    except (Exception, KeyboardInterrupt): pass
    finally:
        report["temporary_profiles_removed"] = cleanup(work)
        report["passed"] = report["passed"] and report["temporary_profiles_removed"]
        report["last_operation"] = read_operation(work); report["logs"] = log_receipts(work)
        if report["passed"]: report["failure"] = None
        with (work / "report.json").open("x") as stream: json.dump(report, stream, sort_keys=True)
    require(report["passed"], "selection_fixture_failed")


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
                              outer_seconds=480, sessions=list(SESSIONS)), sort_keys=True)); return
    require(pinned() and args.stage is not None and args.output is not None, "selection_fixture_unfrozen")
    os.umask(0o077)
    stage, work = args.stage.resolve(strict=True), build_path(args.output)
    require(not stage.is_relative_to(work) and not work.is_relative_to(stage))
    metadata = validate_stage(stage); validate_retained_bundle(stage, metadata); verify_sources()
    if args.inside: return inside(stage, work, metadata, args.host_netns)
    require(args.host_netns is None)
    prepare_output(work)
    try:
        mounts = isolated_browser_home(work)
        subprocess.run(["/usr/bin/bwrap", "--die-with-parent", "--unshare-user", "--unshare-net", "--unshare-pid",
            "--ro-bind", "/", "/", *mounts, "--bind", str(work), str(work), "--tmpfs", "/tmp",
            "--proc", "/proc", "--dev", "/dev", "--", sys.executable, "-B", str(Path(__file__).resolve()),
            "--stage", str(stage), "--output", str(work), "--execute", "--inside", "--host-netns",
            os.readlink("/proc/self/ns/net")], check=True, timeout=480, env=clean_environment(work))
    finally:
        removed = cleanup(work)
        with (work / "outer-cleanup.json").open("x") as stream:
            json.dump(dict(schema=1, temporary_profiles_removed=removed,
                           last_operation=read_operation(work), logs=log_receipts(work)), stream)
        require(removed)


if __name__ == "__main__":
    try: main()
    except (Exception, KeyboardInterrupt):
        print("filter_selection_fixture_failed", file=sys.stderr); raise SystemExit(1) from None
