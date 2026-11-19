// SPDX-License-Identifier: GPL-3.0-only
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture() {
  const source = fs.readFileSync(path.join(__dirname, '../integration/VolparossaTabRoutes.sys.mjs'), 'utf8')
    .replace(/^import .*;$/gm, '').replace('export const VolparossaTabRoutes', 'this.ui');
  const events = [];
  let pick = 0, consent = true, canRead = true, denied = false, delay;
  const grant = { hostname: 'scope.example', port: 443, expires_at_ms: 130000 };
  const reads = [];
  const controls = new Map();
  const doc = {
    getElementById(id) { if (!controls.has(id)) controls.set(id, {}); return controls.get(id); },
    l10n: {
      setAttributes(element, id) { element.id = id; },
      async formatValue(id) { return id; },
      async formatValues(messages) { return [messages[0].id, messages[1].args.authority]; },
    },
  };
  function makeTab(key = {}) {
    const win = new EventTarget();
    const tab = Object.assign(new EventTarget(), { isConnected: true, closing: false, ownerGlobal: win,
      permanentKey: key, linkedBrowser: {}, ownerDocument: { ...doc, defaultView: win } });
    win.TabContextMenu = { contextTab: tab };
    win.gBrowser = { getTabForBrowser: b => b === tab.linkedBrowser ? tab : null };
    return tab;
  }
  const scope = {
    TextDecoder, Date: { now: () => 100000 }, networkMonotonicNow: () => 200,
    Ci: { nsIFilePicker: { modeOpen: 1, returnOK: 0, filterAll: 0 } },
    Cc: { '@mozilla.org/filepicker;1': { createInstance() { return {
      init() { events.push('picker'); }, appendFilters() {}, file: { path: '/private/grant' },
      open(done) { assert.equal(this.addToRecentDocs, false); done(pick); },
    }; } } },
    IOUtils: { async readVolparossaGrant() {
      if (delay) await delay;
      if (!canRead) throw Error('unreadable');
      const bytes = new TextEncoder().encode(JSON.stringify(grant));
      reads.push(bytes); return bytes;
    } },
    Services: { appinfo: { OS: 'Linux' },
      prompt: { confirm(win, title, authority) { events.push(['confirm', win, authority]); return consent; } },
      tm: { dispatchToMainThread: fn => fn() } },
    VolparossaBrowserNetwork: { bind(browser) {
      events.push(['bind', browser]);
      return { status: { state: 'preparing' },
        async authorize(g) { events.push('authorize'); this.status.state = denied ? 'blocked' : 'overlay_ready';
          if (denied) throw Error('refused'); },
        close() { events.push('close'); },
        release(options) { events.push(options?.allowOrdinaryInternet ? 'allow ordinary' : 'destroyed'); },
      };
    } },
  };
  // The returned JSON belongs to the VM's object realm; compare primitive fields here.
  scope.validateNetworkGrant = g => {
    assert.equal(g.hostname, grant.hostname); assert.equal(g.port, grant.port);
    assert.equal(g.expires_at_ms, grant.expires_at_ms); return g;
  };
  vm.runInNewContext(source, scope);
  return { ui: scope.ui, tab: makeTab(), makeTab, events, reads, controls, scope, grant,
    setPick: x => pick = x, setConsent: x => consent = x, setRead: x => canRead = x,
    deny: () => denied = true, setDelay: x => delay = x,
    status: () => controls.get('volparossa-tab-route-status')?.id };
}
test('opening the native menu does not attach or read; multiselection is hidden', async () => {
  const f = fixture(); f.ui.update(f.tab);
  assert.equal(f.status(), 'volparossa-tab-route-ordinary'); assert.deepEqual(f.events, []);
  f.tab.multiselected = true; f.ui.update(f.tab);
  assert.equal(f.controls.get('context_volparossaTabRoute').hidden, true);
  await f.ui.connect(f.tab); assert.deepEqual(f.events, []);
});
test('picker cancel, refused file and cancelled confirmation never bind a tab', async () => {
  const f = fixture(); f.setPick(1); await f.ui.connect(f.tab);
  assert.equal(f.status(), 'volparossa-tab-route-ordinary'); assert.equal(f.reads.length, 0);
  f.setPick(0); f.setRead(false); await f.ui.connect(f.tab);
  assert.equal(f.status(), 'volparossa-tab-route-invalid');
  f.setRead(true); f.setConsent(false); await f.ui.connect(f.tab);
  assert.equal(f.status(), 'volparossa-tab-route-ordinary');
  assert.equal(f.events.at(-1)[2], 'scope.example:443');
  assert.equal(f.reads[0].every(byte => byte === 0), true);
  assert.equal(f.events.includes('authorize'), false);
});
test('explicit grant affects only its original tab; release requires its separate command', async () => {
  const f = fixture(); const other = f.makeTab(); await f.ui.connect(f.tab);
  assert.equal(f.status(), 'volparossa-tab-route-ready');
  assert.deepEqual(f.events.find(e => e[0] === 'bind'), ['bind', f.tab.linkedBrowser]);
  f.ui.update(other); assert.equal(f.status(), 'volparossa-tab-route-ordinary');
  f.ui.release(other); assert.equal(f.events.includes('allow ordinary'), false);
  await f.ui.connect(f.tab); assert.equal(f.events.filter(e => e === 'authorize').length, 1);
  f.ui.release(f.tab); assert.equal(f.events.at(-1), 'allow ordinary');
  assert.equal(f.status(), 'volparossa-tab-route-ordinary');
});
test('tab moved or closed during selection cannot consume the grant', async () => {
  const f = fixture(); let finish; f.setDelay(new Promise(resolve => finish = resolve));
  const waiting = f.ui.connect(f.tab); await new Promise(resolve => setImmediate(resolve));
  f.tab.permanentKey = {}; finish(); await waiting;
  assert.equal(f.events.includes('authorize'), false);
});
test('authorization refusal remains guarded until owner explicitly allows ordinary networking', async () => {
  const f = fixture(); f.deny(); await f.ui.connect(f.tab);
  assert.equal(f.status(), 'volparossa-tab-route-blocked');
  assert.equal(f.controls.get('volparossa-tab-route-connect').disabled, true);
  assert.equal(f.controls.get('volparossa-tab-route-release').disabled, false);
  f.ui.release(f.tab); assert.equal(f.events.at(-1), 'allow ordinary');
});
test('expiry turns the status back to blocked without direct fallback or renewal', async () => {
  const f = fixture(); await f.ui.connect(f.tab);
  f.scope.Date.now = () => f.grant.expires_at_ms; f.ui.update(f.tab);
  assert.equal(f.status(), 'volparossa-tab-route-blocked');
  assert.equal(f.events.includes('allow ordinary'), false);
  assert.equal(f.events.filter(e => e === 'authorize').length, 1);
});
test('adopting the logical tab retains its guard, and final tab close tears it down', async () => {
  const f = fixture(); await f.ui.connect(f.tab);
  const moved = f.makeTab(f.tab.permanentKey);
  const close = Object.assign(new Event('TabClose'), { detail: { adoptedBy: moved } });
  f.tab.dispatchEvent(close); f.tab.ownerGlobal.dispatchEvent(new Event('unload'));
  assert.equal(f.events.includes('close'), false);
  f.ui.update(moved); assert.equal(f.status(), 'volparossa-tab-route-ready');
  moved.closing = true; moved.dispatchEvent(new Event('TabClose'));
  assert.deepEqual(f.events.slice(-2), ['close', 'destroyed']);
  assert.equal(f.events.includes('allow ordinary'), false);
});
