// SPDX-License-Identifier: GPL-3.0-only
// Actual adapter logic with a simulated Gecko transport/clock, not browser proof.
'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture(ttl = 300000) {
  const state = {now: 1000000, monotonic: 0, timers: new Map(), next: 0, closed: 0, writes: 0, proxies: 0,
    timeouts: [], filters: new Map(), adapters: []};
  const output = {QueryInterface() { return this; }, write(_data, size) { state.writes++; return size; },
    close() { state.closed++; }};
  const transport = {setTimeout(...args) { state.timeouts.push(args); },
    openInputStream() { return {close() { state.closed++; }}; }, openOutputStream() { return output; },
    close() { state.closed++; }};
  const proxy = {registerChannelFilter(filter, priority) { state.filters.set(priority, filter); },
    newProxyInfo(_type, _host, _port, proxyAuthorizationHeader) { state.proxies++; return {proxyAuthorizationHeader}; }};
  const services = {
    '@mozilla.org/network/protocol-proxy-service;1': {getService: () => proxy},
    '@mozilla.org/uuid-generator;1': {getService: () => ({generateUUID: () => '{12345678-1234-1234-1234-123456789012}'})},
    '@mozilla.org/network/input-stream-pump;1': {createInstance: () => ({init() {}, asyncRead(owner) { state.adapters.push(owner); }, cancel() {}})},
    '@mozilla.org/file/local;1': {createInstance: () => ({initWithPath() {}})},
    '@mozilla.org/network/socket-transport-service;1': {getService: () => ({createUnixDomainTransport: () => transport})},
  };
  const context = vm.createContext({TextEncoder, TextDecoder, Date: {now: () => state.now},
    setTimeout(fn, ms) { const id = ++state.next; state.timers.set(id, {fn, ms}); return id; },
    clearTimeout(id) { state.timers.delete(id); }, Cc: services,
    Ci: {nsISocketTransport: {TIMEOUT_CONNECT: 0}, nsIProxyInfo: {TRANSPARENT_PROXY_RESOLVES_HOST: 1, ALWAYS_TUNNEL_VIA_PROXY: 2},
      nsIXULRuntime: {PROCESS_TYPE_DEFAULT: 0}, nsIRequest: {LOAD_BYPASS_CACHE: 1, INHIBIT_CACHING: 2}},
    Cr: {NS_BINDING_ABORTED: 0x804b0002, NS_ERROR_ABORT: 0x80004004, NS_ERROR_NO_INTERFACE: 0x80004002},
    Components: {Exception: (code, result) => Object.assign(new Error(code), {result})},
    ChromeUtils: {generateQI: () => function () { return this; }, now: () => state.monotonic},
    Services: {tm: {currentThread: {}}, appinfo: {processType: 0}}});
  const raw = fs.readFileSync(path.join(__dirname, '../integration/VolparossaNetwork.sys.mjs'), 'utf8');
  const source = raw.replace(/^import .*Timer\.sys\.mjs";$/m, '').replace(/^export /gm, '');
  vm.runInContext(`globalThis.Network = (() => { ${source}\nreturn {VolparossaNetwork, VolparossaNetworkError,
    validateNetworkGrant, validateNetworkFailure, getNetworkDecision, networkMonotonicNow}; })(); globalThis.Adapter = Network.VolparossaNetwork;`, context);
  const controllerSource = fs.readFileSync(path.join(__dirname, '../integration/VolparossaBrowserNetwork.sys.mjs'), 'utf8')
    .replace(/^import[\s\S]*?from "\.\/VolparossaNetwork\.sys\.mjs";\n/m, '').replace(/^export /gm, '');
  vm.runInContext(`globalThis.Controller = (() => { const {VolparossaNetwork, VolparossaNetworkError,
    validateNetworkGrant, getNetworkDecision, networkMonotonicNow} = Network; ${controllerSource}\nreturn VolparossaBrowserNetwork; })();`, context);
  const grant = {version: 1, app_uid: 1234, app_socket: '/run/test.apps', capability: 'a'.repeat(64),
    hostname: 'fixture.invalid', port: 443, partition: 'b'.repeat(64),
    expires_at_ms: state.now + ttl, overlay_only: true};
  const adapter = new context.Adapter(transport, grant);
  const reply = {version: 1, proxy_host: '127.0.0.1', proxy_port: 32123,
    proxy_authorization: 'Bearer ' + 'c'.repeat(64), hostname: grant.hostname, port: grant.port,
    partition: grant.partition, expires_at_ms: grant.expires_at_ms, overlay_only: true};
  function ready(value = reply, target = adapter) {
    const body = Buffer.from(JSON.stringify(value));
    const frame = Buffer.alloc(body.length + 4); frame.writeUInt32BE(body.length); body.copy(frame, 4);
    target._consume(frame);
  }
  const failure = (status = 'unavailable') => ({version: 1, status, reason: status === 'unavailable' ? 'no_eligible_paths' : 'blocked',
    hostname: grant.hostname, port: grant.port, partition: grant.partition, expires_at_ms: grant.expires_at_ms,
    direct_until_ms: status === 'unavailable' ? state.now + 5000 : 0});
  function browser() {
    const browsingContext = {originAttributes: {userContextId: 3, privateBrowsingId: 0}};
    browsingContext.top = browsingContext;
    return {localName: 'browser', ownerGlobal: {gBrowser: {}}, browsingContext, isConnected: true};
  }
  function channel(tab, changes = {}) {
    const headers = new Map(); const calls = [];
    return {URI: {schemeIs: scheme => scheme === 'https', asciiHost: grant.hostname, port: grant.port, userPass: ''},
      loadInfo: {browsingContext: tab.browsingContext}, requestMethod: 'GET', loadFlags: 0,
      allowHttp3: true, allowAltSvc: true, allowSpdy: true, notificationCallbacks: null,
      QueryInterface() { return this; }, calls, headers,
      cancel(code) { calls.push(['cancel', code]); },
      getRequestHeader(key) { if (!headers.has(key)) { throw new Error('absent'); } return headers.get(key); },
      setRequestHeader(key, value) { if (value) { headers.set(key, value); } else { headers.delete(key); } },
      setNewListener(listener) { this.listener = listener; return {onStartRequest: () => calls.push(['start']),
        onDataAvailable: (...args) => calls.push(['data', ...args]), onStopRequest: () => calls.push(['stop'])}; }, ...changes};
  }
  function select(request, original = {original: true}) {
    let result = original;
    for (const [, filter] of [...state.filters].sort((a, b) => a[0] - b[0])) {
      filter.applyFilter(request, result, {onProxyFilterResult(value) { result = value; }});
    }
    return result;
  }
  return {state, adapter, ready, grant, failure, browser, channel, select, context};
}

test('route preparation may outlast old bootstrap bound without opening a browser channel', async () => {
  const {state, adapter, ready} = fixture();
  assert.equal(state.timers.get(1).ms, 95000);
  assert.deepEqual(state.timeouts, [[0, 5]]); // Unix connection, not TLS timeout.
  state.now += 35000;
  assert.equal(adapter.active, false); assert.equal(state.proxies, 0);
  assert.throws(() => adapter.openChannel({URI: {schemeIs: () => true, asciiHost: 'fixture.invalid', port: 443}}, {}),
    error => error.code === 'scope_unavailable');
  ready(); await adapter.ready;
  assert.equal(adapter.active, true); assert.equal(state.proxies, 1);
  assert.equal(state.timers.size, 1); assert.equal([...state.timers.values()][0].ms, 265000);
  adapter.close(); assert.equal(adapter.active, false); assert.equal(state.timers.size, 0);
});

test('absolute grant expiry caps preparation; timeout closes transport and leaves no ready proxy', async () => {
  for (const ttl of [2500, 300000]) {
    const {state, adapter, ready} = fixture(ttl);
    const rejected = assert.rejects(adapter.ready, error => error.code === 'unavailable' &&
      error.diagnostic.stage === 'bootstrap-timeout');
    const timer = state.timers.get(1);
    assert.equal(timer.ms, Math.min(ttl, 95000));
    state.now += timer.ms; timer.fn(); await rejected;
    assert.equal(adapter.active, false); assert.equal(state.closed, 3);
    assert.equal(state.proxies, 0); assert.equal(state.timers.size, 0);
    ready(); assert.equal(adapter.active, false); assert.equal(state.proxies, 0);
  }
});

test('EOF during route preparation rejects attachment and cancels its timer', async () => {
  const {state, adapter} = fixture();
  const rejected = assert.rejects(adapter.ready, error => error.diagnostic.stage === 'bootstrap-eof');
  adapter.onStopRequest(null, 0); await rejected;
  assert.equal(adapter.active, false); assert.equal(state.closed, 3); assert.equal(state.timers.size, 0);
});

test('both pinned Gecko clocks are monotonic and wall-clock rollback cannot extend a grant', async () => {
  const f = fixture();
  f.context.ChromeUtils.now = undefined;
  f.context.Cu = {now: () => f.state.monotonic};
  f.ready(); await f.adapter.ready;
  assert.equal(f.context.Network.networkMonotonicNow(), 0);
  f.state.now -= 60000; f.state.monotonic = 300000;
  assert.throws(() => f.adapter._checkChannel(f.channel(f.browser())), error => error.code === 'scope_unavailable');
  f.adapter.close();
});

test('only an exact decoded terminal failure carries direct-network authority', async () => {
  for (const status of ['unavailable', 'denied']) {
    const {adapter, ready, failure, state, context} = fixture();
    const rejected = adapter.ready.catch(error => error);
    ready(failure(status));
    const error = await rejected;
    const decision = context.Network.getNetworkDecision(error);
    assert.equal(decision.status, status); assert.equal(Object.isFrozen(decision), true);
    assert.equal(decision.monotonic_until_ms, status === 'unavailable' ? 5000 : 0);
    assert.equal(adapter.active, false); assert.equal(state.proxies, 0); assert.equal(state.closed, 3);
    assert.equal(context.Network.getNetworkDecision({code: 'route_unavailable', ...decision}), null);
    assert.equal(context.Network.getNetworkDecision(new context.Network.VolparossaNetworkError('route_unavailable')), null);
  }
});

test('failure parser rejects wrong authority, extra fields, expiry and mixed denial semantics', async () => {
  const {context, grant, failure, ready, adapter, state} = fixture();
  const valid = failure();
  for (const change of [{version: 2}, {hostname: 'other.invalid'}, {port: 444}, {partition: 'c'.repeat(64)},
    {expires_at_ms: grant.expires_at_ms + 1}, {direct_until_ms: state.now + 5001},
    {direct_until_ms: state.now}, {direct_until_ms: true}, {reason: 'blocked'}, {status: 'denied'},
    {fallback_allowed: true}, {status: 'error'}]) {
    assert.throws(() => context.Network.validateNetworkFailure({...valid, ...change}, grant, state.now),
      error => error.code === 'invalid_contract');
  }
  assert.throws(() => context.Network.validateNetworkFailure({...failure('denied'), direct_until_ms: 1}, grant, state.now));
  const rejected = adapter.ready.catch(error => error);
  state.monotonic = 300000;
  assert.throws(() => ready(valid), error => error.code === 'invalid_contract');
  adapter.close(); assert.equal(context.Network.getNetworkDecision(await rejected), null);
});

test('trailing bytes on a failure frame cannot create a trusted fallback decision', async () => {
  const f = fixture(); const rejected = f.adapter.ready.catch(error => error);
  const body = Buffer.from(JSON.stringify(f.failure()));
  const frame = Buffer.alloc(body.length + 5); frame.writeUInt32BE(body.length); body.copy(frame, 4);
  assert.throws(() => f.adapter._consume(frame), error => error.code === 'invalid_contract');
  f.adapter.close(); assert.equal(f.context.Network.getNetworkDecision(await rejected), null);
});

test('ordinary browser defaults preserve existing network; activated unknown scope and kill switch block', async () => {
  const f = fixture(); const tab = f.browser(); const owner = f.context.Controller.bind(tab);
  const original = {ownerProxy: true}; const request = f.channel(tab);
  assert.equal(f.select(request, original), original); assert.equal(owner.status.kill_switch, false);
  owner.setKillSwitch(true); assert.throws(() => f.select(request, original));
  assert.equal(request.calls.at(-1)[0], 'cancel');
  const other = f.channel(f.browser()); assert.equal(f.select(other, original), original);
  owner.release({allowOrdinaryInternet: true});
  f.adapter.ready.catch(() => {}); f.adapter.close();
});

test('ordinary native channel is adopted without replay and listener/callbacks survive', async () => {
  const f = fixture(); const tab = f.browser(); const owner = f.context.Controller.bind(tab);
  const pending = owner.authorize(f.grant); const attachment = f.state.adapters.at(-1);
  assert.throws(() => f.select(f.channel(tab))); // Pending preparation never means DIRECT.
  f.ready(undefined, attachment); await pending;
  const callbacks = {getInterface: () => 'original-interface'};
  const request = f.channel(tab, {notificationCallbacks: callbacks});
  const proxy = f.select(request); assert.equal(proxy, attachment._proxy);
  assert.equal(request.headers.get('Proxy-Authorization'), proxy.proxyAuthorizationHeader);
  assert.equal(request.allowHttp3, false); assert.equal(request.allowAltSvc, false); assert.equal(request.allowSpdy, false);
  assert.equal(request.beConservative, false); assert.equal(request.bypassProxy, false);
  assert.equal(request.notificationCallbacks.getInterface({equals: () => false}), 'original-interface');
  let redirect; request.notificationCallbacks.asyncOnChannelRedirect(null, null, 0, {onRedirectVerifyCallback: code => redirect = code});
  assert.equal(redirect, 0x80004004);
  request.listener.onStartRequest(request); request.listener.onDataAvailable(request, 'buffer', 0, 3);
  request.listener.onStopRequest(request, 0);
  assert.deepEqual(request.calls, [['start'], ['data', request, 'buffer', 0, 3], ['stop']]);
  assert.equal(request.notificationCallbacks, callbacks); assert.equal(request.headers.has('Proxy-Authorization'), false);
  owner.close(); assert.throws(() => f.select(request));
  assert.throws(() => owner.release(), error => error.code === 'ordinary_internet_ack_required');
  owner.release({allowOrdinaryInternet: true});
  f.adapter.ready.catch(() => {}); f.adapter.close();
});

test('only decoded unavailability admits new safe requests, bounded by monotonic lifetime', async () => {
  const f = fixture(); const tab = f.browser(); const owner = f.context.Controller.bind(tab);
  const pending = owner.authorize(f.grant);
  f.ready(f.failure(), f.state.adapters.at(-1)); await pending;
  const original = {ownerProxy: true}; assert.equal(f.select(f.channel(tab), original), original);
  assert.throws(() => f.select(f.channel(tab, {requestMethod: 'POST'})));
  owner.setKillSwitch(true); assert.throws(() => f.select(f.channel(tab)));
  owner.setKillSwitch(false);
  const alien = f.channel(tab); alien.URI.asciiHost = 'different.invalid'; assert.throws(() => f.select(alien));
  const header = f.channel(tab); header.headers.set('Proxy-Authorization', 'private'); assert.throws(() => f.select(header));
  f.state.now -= 60000; f.state.monotonic += 5001;
  assert.throws(() => f.select(f.channel(tab))); // Wall-clock rollback cannot prolong authority.
  owner.release({allowOrdinaryInternet: true});
  f.adapter.ready.catch(() => {}); f.adapter.close();
});

test('denial and EOF never allow fallback, and changed container attributes block', async () => {
  for (const denied of [true, false]) {
    const f = fixture(); const tab = f.browser(); const owner = f.context.Controller.bind(tab);
    const pending = owner.authorize(f.grant); const rejected = assert.rejects(pending);
    const attachment = f.state.adapters.at(-1);
    if (denied) { f.ready(f.failure('denied'), attachment); } else { attachment.onStopRequest(null, 0); }
    await rejected; assert.throws(() => f.select(f.channel(tab)));
    owner.release({allowOrdinaryInternet: true}); f.adapter.ready.catch(() => {}); f.adapter.close();
  }
  const f = fixture(); const tab = f.browser(); const owner = f.context.Controller.bind(tab);
  tab.browsingContext.originAttributes.userContextId = 99;
  assert.throws(() => f.select(f.channel(tab)));
  owner.release({allowOrdinaryInternet: true}); f.adapter.ready.catch(() => {}); f.adapter.close();
});
