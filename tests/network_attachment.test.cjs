// SPDX-License-Identifier: GPL-3.0-only
// Actual adapter logic with a simulated Gecko transport/clock, not browser proof.
'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture(ttl = 300000) {
  const state = {now: 1000000, timers: new Map(), next: 0, closed: 0, writes: 0, proxies: 0, timeouts: []};
  const output = {QueryInterface() { return this; }, write(_data, size) { state.writes++; return size; },
    close() { state.closed++; }};
  const transport = {setTimeout(...args) { state.timeouts.push(args); },
    openInputStream() { return {close() { state.closed++; }}; }, openOutputStream() { return output; },
    close() { state.closed++; }};
  const proxy = {registerChannelFilter() {}, newProxyInfo() { state.proxies++; return {}; }};
  const services = {
    '@mozilla.org/network/protocol-proxy-service;1': {getService: () => proxy},
    '@mozilla.org/uuid-generator;1': {getService: () => ({generateUUID: () => '{12345678-1234-1234-1234-123456789012}'})},
    '@mozilla.org/network/input-stream-pump;1': {createInstance: () => ({init() {}, asyncRead() {}, cancel() {}})},
  };
  const context = vm.createContext({TextEncoder, TextDecoder, Date: {now: () => state.now},
    setTimeout(fn, ms) { const id = ++state.next; state.timers.set(id, {fn, ms}); return id; },
    clearTimeout(id) { state.timers.delete(id); }, Cc: services,
    Ci: {nsISocketTransport: {TIMEOUT_CONNECT: 0}, nsIProxyInfo: {TRANSPARENT_PROXY_RESOLVES_HOST: 1, ALWAYS_TUNNEL_VIA_PROXY: 2}},
    Cr: {NS_BINDING_ABORTED: 0x804b0002}, ChromeUtils: {generateQI: () => function () { return this; }},
    Services: {tm: {currentThread: {}}}});
  const raw = fs.readFileSync(path.join(__dirname, '../integration/VolparossaNetwork.sys.mjs'), 'utf8');
  const source = raw.replace(/^import .*Timer\.sys\.mjs";$/m, '').replace(/^export /gm, '');
  vm.runInContext(source + '\nglobalThis.Adapter = VolparossaNetwork;', context);
  const grant = {version: 1, app_uid: 1234, app_socket: '/run/test.apps', capability: 'a'.repeat(64),
    hostname: 'fixture.invalid', port: 443, partition: 'b'.repeat(64),
    expires_at_ms: state.now + ttl, overlay_only: true};
  const adapter = new context.Adapter(transport, grant);
  const reply = {version: 1, proxy_host: '127.0.0.1', proxy_port: 32123,
    proxy_authorization: 'Bearer ' + 'c'.repeat(64), hostname: grant.hostname, port: grant.port,
    partition: grant.partition, expires_at_ms: grant.expires_at_ms, overlay_only: true};
  function ready(value = reply) {
    const body = Buffer.from(JSON.stringify(value));
    const frame = Buffer.alloc(body.length + 4); frame.writeUInt32BE(body.length); body.copy(frame, 4);
    adapter._consume(frame);
  }
  return {state, adapter, ready, grant};
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
