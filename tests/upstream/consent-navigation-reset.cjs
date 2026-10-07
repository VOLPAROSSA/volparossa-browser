// SPDX-License-Identifier: GPL-3.0-only
// Exercise the listener registered by the actual upstream background.js.
// No dependencies, browser installation, network or signed-XPI modification.
// Usage: node consent-navigation-reset.cjs SOURCE [ONE_LINE_PATCH]
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const vm = require('node:vm');

function sha256(value) {
    return crypto.createHash('sha256').update(value).digest('hex');
}

async function main() {
    assert.ok(process.argv.length === 3 || process.argv.length === 4, 'expected source and optional patch');
    const original = fs.readFileSync(process.argv[2], 'utf8');
    assert.ok(Buffer.byteLength(original) <= 65536, 'bounded background source');
    let source = original;
    if (process.argv[3]) {
        const lines = fs.readFileSync(process.argv[3], 'utf8').split('\n');
        const removed = lines.filter(line => line.startsWith('-') && !line.startsWith('---'));
        const added = lines.filter(line => line.startsWith('+') && !line.startsWith('+++'));
        assert.equal(removed.length, 1, 'exactly one removed line');
        assert.equal(added.length, 1, 'exactly one added line');
        assert.equal(removed[0].slice(1).replace('"Loading"', '"loading"'), added[0].slice(1));
        assert.equal(source.split(removed[0].slice(1)).length, 2, 'unique original patch line');
        source = source.replace(removed[0].slice(1), added[0].slice(1));
    }
    const imported = "import GDPRConfig from './GDPRConfig.js';";
    assert.ok(source.startsWith(imported + '\n'), 'expected sole configuration import');
    assert.equal(source.split(imported).length, 2);
    const callbacks = [], messages = [], badges = [], colors = [];
    const context = vm.createContext({
        GDPRConfig: {getDebugValues: () => Promise.resolve({autoOpenOptionsTab: false})},
        chrome: {
            runtime: {onMessage: {addListener: callback => messages.push(callback)}},
            tabs: {onUpdated: {addListener: callback => callbacks.push(callback)}},
            browserAction: {
                setBadgeText: value => badges.push({...value}),
                setBadgeBackgroundColor: value => colors.push({...value}),
            },
        },
    });
    // Substitute only the unrelated configuration import. The real STATUS,
    // tabStatusMap, setBadgeCheckmark and registered listener run unchanged.
    new vm.Script(source.slice(imported.length), {filename: 'Extension/background.js'})
        .runInContext(context, {timeout: 1000});
    await Promise.resolve();
    assert.equal(messages.length, 1);
    assert.equal(callbacks.length, 1);
    assert.equal(typeof callbacks[0], 'function');
    const cases = [
        ['loading', {status: 'loading'}, true],
        ['complete', {status: 'complete'}, false],
        ['missing status', {title: 'Synthetic title'}, false],
        ['empty update', {}, false],
        ['null status', {status: null}, false],
    ];
    for (const [name, change, resets] of cases) {
        vm.runInContext('tabStatusMap.clear(); tabStatusMap.set(7, STATUS.HANDLED); tabStatusMap.set(8, STATUS.HANDLED);', context);
        badges.length = colors.length = 0;
        callbacks[0](7, change, {});
        assert.equal(vm.runInContext('tabStatusMap.get(7)', context),
            vm.runInContext(resets ? 'STATUS.INIT' : 'STATUS.HANDLED', context), name + ': target status');
        assert.equal(vm.runInContext('tabStatusMap.get(8)', context),
            vm.runInContext('STATUS.HANDLED', context), name + ': other tab unchanged');
        assert.deepEqual(badges, resets ? [{text: '', tabId: 7}] : [], name + ': target badge');
        assert.deepEqual(colors, resets ? [{color: 'white', tabId: 7}] : [], name + ': target badge color');
    }
    console.log(JSON.stringify({passed: true, cases: cases.map(([name]) => name),
        source_sha256: sha256(original), executed_sha256: sha256(source),
        actual_source_registered_listener: true, other_tab_isolation: true,
        patch_applied_in_memory: Boolean(process.argv[3]),
        signed_extension_modified: false, browser_integration_proven: false}));
}

main().catch(error => {
    console.error(JSON.stringify({passed: false, assertion: error.message}));
    process.exitCode = 1;
});
