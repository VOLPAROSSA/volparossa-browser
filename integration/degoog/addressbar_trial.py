# SPDX-License-Identifier: GPL-3.0-only
"""Submit two searches from real Firefox to the local-only, enrolled DeGoog reader."""
from collections import Counter
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
import secrets
import subprocess
import threading
import time
from urllib.parse import parse_qsl

from query import query

KNOWN = "Network attachment and kill switch"
EXPECTED_URL = ("https://github.com/VOLPAROSSA/volparossa-browser/blob/"
                "8878ed9c21e647184309c360d89289891b275216/docs/CORE_INTEGRATION.md")
SNAPSHOT_SHA = "7ec92c63b697c290d7634027be4e41e2eaab237459100782910183dd62fe12e3"


def exercise_submission(client, browser_script, bun, upstream, checkpoint):
    counts = Counter()
    capability = "/" + secrets.token_hex(24)

    class LocalEngine(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def answer(self, status, mime, body=b""):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'; base-uri 'none'; form-action 'none'")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def matches(self, tail):
            return self.path == capability + tail and self.headers.get_all("Host") == [authority]

        def do_GET(self):
            if self.matches("/engine.xml"):
                counts["description"] += 1
                self.answer(200, "application/opensearchdescription+xml", f"""<OpenSearchDescription
                xmlns="http://a9.com/-/spec/opensearch/1.1/"><ShortName>Local DeGoog</ShortName>
                <Description>Search the enrolled public index</Description><InputEncoding>UTF-8</InputEncoding>
                <Url type="text/html" method="POST" template="{origin}{capability}/results">
                <Param name="q" value="{{searchTerms}}"/></Url></OpenSearchDescription>""".encode())
            elif self.path == "/favicon.ico" and self.headers.get_all("Host") == [authority]:
                self.answer(204, "image/x-icon")
            else:
                counts["unexpected"] += 1
                self.answer(404, "text/plain")

        def do_POST(self):
            try:
                if (not self.matches("/results") or self.headers.get("Transfer-Encoding") is not None
                        or self.headers.get_content_type() != "application/x-www-form-urlencoded"
                        or len(self.headers.get_all("Content-Length", [])) != 1):
                    raise ValueError
                size = int(self.headers["Content-Length"])
                if not 0 < size <= 4096:
                    raise ValueError
                self.connection.settimeout(3)
                submitted = parse_qsl(self.rfile.read(size).decode("ascii"), keep_blank_values=True,
                                      strict_parsing=True, max_num_fields=1, errors="strict")
                if len(submitted) != 1 or submitted[0][0] != "q":
                    raise ValueError
                results = query(bun, upstream, checkpoint, SNAPSHOT_SHA, submitted[0][1].encode())["results"]
                items = "".join(f'<li><a href="{escape(row["url"], quote=True)}">{escape(row["title"])}</a>'
                                f'<p>{escape(row["snippet"])}</p></li>' for row in results)
                page = f'<!doctype html><meta charset="utf-8"><main><h1>Local DeGoog</h1><ol>{items}</ol></main>'
                counts["submissions"] += 1
                self.answer(200, "text/html; charset=utf-8", page.encode())
            except (ValueError, OSError, subprocess.SubprocessError):
                counts["rejected"] += 1
                self.answer(400, "text/plain")

    with HTTPServer(("127.0.0.1", 0), LocalEngine) as server:
        authority = f"127.0.0.1:{server.server_port}"
        origin = "http://" + authority
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            browser_script(client, """
                await Services.search.init();
                const engine = await Services.search.addOpenSearchEngine(arguments[0]);
                await Services.search.setDefault(engine, Services.search.CHANGE_REASON.USER);
                return (await Services.search.getDefault()).name === "Local DeGoog" ? "ok" : "selection failed";
            """, origin + capability + "/engine.xml")
            for text, expected_count in [(KNOWN, 1), ("private search " + secrets.token_hex(12), 0)]:
                client.command("Marionette:SetContext", {"value": "chrome"})
                browser_script(client, """
                    const bar = Services.wm.getMostRecentWindow("navigator:browser").gURLBar;
                    bar.search(arguments[0]);
                    const ctx = await bar.lastQueryContextPromise;
                    if (ctx.searchString !== arguments[0]) return "query did not complete";
                    bar.handleCommand();
                    return "ok";
                """, text)
                client.command("Marionette:SetContext", {"value": "content"})
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if client.script("""
                        if (document.location.href !== arguments[0] ||
                            document.querySelector("main h1")?.textContent !== "Local DeGoog") return false;
                        const rows = document.querySelectorAll("main ol li");
                        return rows.length === arguments[1] && (!rows.length ||
                            (rows[0].querySelector("a").href === arguments[2] &&
                             rows[0].querySelector("p").textContent === arguments[3]));
                    """, [origin + capability + "/results", expected_count, EXPECTED_URL, KNOWN]):
                        break
                    time.sleep(0.1)
                else:
                    raise ValueError("Firefox did not render the expected public search result")
            if counts != {"description": 1, "submissions": 2}:
                raise ValueError("unexpected local search recipient or request count")
        finally:
            server.shutdown()
            thread.join(5)
    client.command("Marionette:SetContext", {"value": "chrome"})
    return dict(selected_via="user", submitted_post_requests=2, known_links=1,
                unmatched_links=0, index_sha256=SNAPSHOT_SHA, query_in_result_url=False)
