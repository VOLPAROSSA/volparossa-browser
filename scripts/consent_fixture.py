#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Bounded synthetic CMP and blocking probes; not a real-site consent test."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlsplit

PURPOSES = ("A", "B", "D", "E", "F", "X")
CASES = ("baseline", "refusal", "unsupported", "hidden", "preference",
         "list-removed", "list-removal-restart")
FIXTURE = Path(__file__).resolve().parents[1] / "tests/fixtures/consent/cmp.html"


def rules():
    selector = '#volparossa-cmp[data-supported="true"]'
    return {"VOLPAROSSA synthetic fixture": {
        "detectors": [{
            "presentMatcher": [{"type": "css", "target": {"selector": selector}}],
            "showingMatcher": [{"type": "css", "target": {
                "selector": selector, "displayFilter": True}}],
        }],
        "methods": [
            {"name": "HIDE_CMP"}, {"name": "OPEN_OPTIONS"},
            {"name": "DO_CONSENT", "action": {"type": "consent", "consents": [
                {"type": key, "matcher": {"type": "checkbox", "target": {
                    "selector": "#purpose-" + key}},
                 "toggleAction": {"type": "click", "target": {"selector": "#purpose-" + key}}}
                for key in PURPOSES]}},
            {"name": "SAVE_CONSENT", "action": {"type": "click", "target": {
                "selector": "#save-consent"}}},
        ],
    }}


def values(accepted=()):
    return {key: key in accepted for key in PURPOSES}


def validate_decision(decision):
    if (type(decision) is not dict or set(decision) != set(PURPOSES)
            or any(type(value) is not bool for value in decision.values())):
        raise ValueError("invalid_synthetic_consent")


def validate_recorded_decision(page, network, expected, blocked):
    """Visibility alone is deliberately insufficient, including for refusals."""
    validate_decision(expected)
    validate_decision(page.get("stored"))
    decisions = network.get("decisions")
    if type(decisions) is not list or len(decisions) != 1:
        raise ValueError("missing_or_duplicate_recorded_consent")
    validate_decision(decisions[0])
    if any(type(network.get(key)) is not int for key in ("essential", "optional", "ad-probe")):
        raise ValueError("invalid_synthetic_request_counters")
    if (page.get("stored") != expected or page.get("saves") != "1"
            or network.get("decisions") != [expected]
            or network.get("essential") != 1
            or page.get("essential") != "loaded"
            or network.get("optional") != int(any(expected.values()))
            or network.get("ad-probe") != (0 if blocked else 1)
            or page.get("adProbe") != ("blocked" if blocked else "loaded")):
        raise ValueError("synthetic_consent_or_filter_check_failed")


class FixtureState:
    def __init__(self):
        self.lock = threading.Lock()
        self.records = {case: {"essential": 0, "ad-probe": 0, "optional": 0,
                               "decisions": []} for case in CASES}
        self.rule_reads = 0
        self.filter_reads = 0

    def observe(self, case, event, decision=None):
        with self.lock:
            if case not in CASES or event not in self.records[case]:
                raise ValueError("unknown_synthetic_event")
            row = self.records[case]
            if event == "decisions":
                validate_decision(decision)
                if row[event]:
                    raise ValueError("duplicate_synthetic_decision")
                row[event].append(dict(decision))
            else:
                if row[event] >= 8:
                    raise ValueError("synthetic_request_bound")
                row[event] += 1

    def snapshot(self, case):
        with self.lock:
            return json.loads(json.dumps(self.records[case]))


@contextmanager
def serve_fixture():
    state = FixtureState()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(3)

        def answer(self, status, data=b"", content_type="text/plain"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)

        def route(self):
            if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                raise ValueError("wrong_fixture_host")
            target = urlsplit(self.path)
            query = parse_qs(target.query, max_num_fields=3)
            case = query.get("case", [None])
            if len(case) != 1:
                raise ValueError("ambiguous_fixture_case")
            return target.path, case[0]

        def do_GET(self):
            try:
                path, case = self.route()
                if path == "/rules.json":
                    state.rule_reads += 1
                    self.answer(200, json.dumps(rules()).encode(), "application/json")
                elif path == "/filters.txt":
                    state.filter_reads += 1
                    origin = f"http://127.0.0.1:{self.server.server_port}"
                    data = ("[Adblock Plus 2.0]\n! Title: VOLPAROSSA synthetic probe only\n"
                            f"|{origin}/ad-probe^$xmlhttprequest\n").encode()
                    self.answer(200, data)
                elif path == "/cmp.html" and case in CASES:
                    self.answer(200, FIXTURE.read_bytes(), "text/html; charset=utf-8")
                elif path in ("/essential", "/ad-probe", "/optional") and case in CASES:
                    state.observe(case, path[1:])
                    self.answer(200, b"synthetic resource")
                else:
                    self.answer(404)
            except (ValueError, KeyError):
                self.answer(400)

        def do_POST(self):
            try:
                path, case = self.route()
                size = int(self.headers.get("Content-Length", "0"))
                if (path != "/decision" or case not in CASES or not 0 < size <= 512
                        or self.headers.get("Content-Type") != "application/json"):
                    raise ValueError("invalid_fixture_post")
                decision = json.loads(self.rfile.read(size))
                state.observe(case, "decisions", decision)
                self.answer(204)
            except (ValueError, KeyError):
                self.answer(400)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("fixture_server_cleanup_failed")
