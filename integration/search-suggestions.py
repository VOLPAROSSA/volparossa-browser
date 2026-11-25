#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Exercise a selected OpenSearch suggestion provider in two real Firefox sessions."""
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from smoke_privacy import run_browser


def require(ok, message):
    if not ok:
        raise ValueError(message)


def browser_script(client, source, *args):
    # Never return search text or include sampled URLs in the receipt or test logs.
    result = client.command('WebDriver:ExecuteAsyncScript', {
        'script': 'const finish = arguments[arguments.length - 1]; (async () => {' + source +
                  '})().then(finish, error => finish({error: error.name}));',
        'args': list(args), 'newSandbox': True, 'sandbox': 'system',
    })['value']
    require(result == 'ok', 'browser search exercise failed: ' + str(result))


def main(stage, work):
    require(os.geteuid() != 0 and [n for _, n in socket.if_nameindex()] == ['lo']
            and os.statvfs(stage).f_flag & os.ST_RDONLY
            and work.is_dir() and work.stat().st_uid == os.geteuid()
            and work.stat().st_mode & 0o777 == 0o700, 'isolated, unprivileged Firefox runner required')
    fresh, opted = ('probe ' + secrets.token_hex(8) for _ in range(2))
    counts = Counter()

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            parts = urlsplit(self.path)
            if parts.path == '/engine.xml' and not parts.query:
                counts['manifest'] += 1
                data = f'''<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">
                  <ShortName>VOLPAROSSA fixture</ShortName><Description>Disposable search provider</Description>
                  <Url type="text/html" template="{origin}/submitted?q={{searchTerms}}"/>
                  <Url type="application/x-suggestions+json" template="{origin}/suggest?q={{searchTerms}}"/>
                </OpenSearchDescription>'''.encode()
                content_type = 'application/opensearchdescription+xml'
            elif parts.path == '/suggest':
                value = parse_qs(parts.query).get('q', [''])[0]
                counts['fresh' if value == fresh else 'opted' if value == opted else 'other'] += 1
                data = json.dumps([value, [value + ' result']]).encode()
                content_type = 'application/x-suggestions+json'
            else:
                counts['submission' if parts.path == '/submitted' else 'unexpected'] += 1
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

    with ThreadingHTTPServer(('127.0.0.1', 0), Provider) as server:
        origin = f'http://127.0.0.1:{server.server_port}'
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            profile = work / 'profile'
            for part in ('profile', 'config', 'cache', 'runtime', 'tmp'):
                (work / part).mkdir(mode=0o700)
            (profile / 'prefs.js').write_text('user_pref("remote.prefs.recommended", false);\n')

            def exercise(client, marker, initial):
                browser_script(client, '''
                    await Services.search.init();
                    if (arguments[2]) {
                      const engine = await Services.search.addOpenSearchEngine(arguments[0] + "/engine.xml");
                      await Services.search.setDefault(engine, Services.search.CHANGE_REASON.USER);
                    }
                    if ((await Services.search.getDefault()).name !== "VOLPAROSSA fixture" ||
                        Services.prefs.getBoolPref("browser.search.suggest.enabled") === arguments[2]) {
                      return "unexpected provider or preference";
                    }
                    const bar = Services.wm.getMostRecentWindow("navigator:browser").gURLBar;
                    bar.search(arguments[1]);
                    const context = await bar.lastQueryContextPromise;
                    if (bar.value !== arguments[1] || context.searchString !== arguments[1]) {
                      return "address bar did not complete the query";
                    }
                    if (arguments[2]) {
                      Services.prefs.setBoolPref("browser.search.suggest.enabled", true);
                      Services.prefs.savePrefFile(null);
                    }
                    return "ok";
                ''', origin, marker, initial)

            run_browser(stage, work, profile, 'fresh', exercise=lambda c: exercise(c, fresh, True))
            require(counts == {'manifest': 1}, 'fresh address bar contacted its suggestion or result recipient')
            run_browser(stage, work, profile, 'opted', exercise=lambda c: exercise(c, opted, False))
            require(counts['manifest'] == 1 and counts['opted'] > 0
                    and set(counts) == {'manifest', 'opted'}, 'enabled provider did not receive only the new marker')
        finally:
            server.shutdown()
            t.join(timeout=5)
    result = dict(firefox='157.0.1', source_stamp='737ec77d798755890b062aab541a90d6c75afd19',
                  provider_preserved_after_restart=True, fresh_suggestion_requests=0,
                  enabled_suggestion_requests=counts['opted'], submitted_searches=0)
    (work / 'suggestions.json').write_text(json.dumps(result) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main(*(Path(p).resolve(strict=True) for p in sys.argv[1:]))
