const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../auth/static/js/app.js'), 'utf8');
const ok = () => ({status: 200, ok: true});
const unauthorized = () => ({status: 401, ok: false});

function context(fetch, extra = {}) {
    const elements = {'auth-csrf-token': {content: 'csrf-example'}};
    const scope = vm.createContext({
        document: {getElementById: id => elements[id] || null, querySelectorAll: () => []},
        window: {location: {}}, fetch, AbortController, setTimeout, clearTimeout, ...extra
    });
    vm.runInContext(source, scope);
    return scope;
}

(async () => {
    const requests = [];
    const csrf = context(async (url, options) => { requests.push({url, options}); return ok(); });
    await vm.runInContext("authFetch('/logout', {method:'POST'})", csrf);
    assert.equal(requests[0].options.headers['X-CSRF-Token'], 'csrf-example');
    assert.equal(requests[0].options.credentials, 'same-origin');

    let refreshed = false, refreshes = 0;
    const singleFlight = context(async (url) => {
        if (url === '/refresh') { refreshes += 1; refreshed = true; await new Promise(setImmediate); return ok(); }
        return refreshed ? ok() : unauthorized();
    });
    const concurrent = await vm.runInContext("Promise.all([authenticatedFetch('/a'),authenticatedFetch('/b')])", singleFlight);
    assert.ok(concurrent.every(response => response.ok));
    assert.equal(refreshes, 1, 'Concurrent requests in one tab must share refresh');

    refreshed = false; refreshes = 0;
    let lockQueue = Promise.resolve();
    const locks = {request(name, fn) {
        assert.equal(name, 'auth-session-refresh');
        const next = lockQueue.then(fn);
        lockQueue = next.catch(() => {});
        return next;
    }};
    const coordinated = context(async url => {
        if (url === '/refresh') { refreshes += 1; refreshed = true; return ok(); }
        return refreshed ? ok() : unauthorized();
    }, {navigator: {locks}});
    await vm.runInContext("Promise.all([authenticatedFetch('/a'),authenticatedFetch('/b')])", coordinated);
    assert.equal(refreshes, 1, 'The second lock holder should use the first refresh instead of replaying a token');

    const expired = context(async () => unauthorized());
    await vm.runInContext("authenticatedFetch('/protected')", expired);
    assert.equal(expired.window.location.href, '/login');

    const visitor = context(async () => unauthorized());
    await vm.runInContext("authenticatedFetch('/protected', {}, false)", visitor);
    assert.equal(visitor.window.location.href, undefined);

    for (const status of [401, 403, 429, 500, 503]) {
        const destinations = [];
        const recovery = context(async () => ({status, ok: false}), {
            window: {location: {replace: url => destinations.push(url)}}
        });
        recovery.message = {textContent: 'Please wait'};
        await vm.runInContext('recoverPageSession(message)', recovery);
        if ([401, 403].includes(status)) assert.deepEqual(destinations, ['/login']);
        else assert.match(recovery.message.textContent, /temporarily unavailable/);
    }

    let expire;
    const stalled = context(() => new Promise(() => {}), {
        setTimeout: callback => { expire = callback; return 1; }, clearTimeout() {}
    });
    stalled.message = {textContent: 'Please wait'};
    const pending = vm.runInContext('recoverPageSession(message)', stalled);
    expire();
    await pending;
    assert.match(stalled.message.textContent, /Reload to retry/);

    const destinations = [];
    const recovered = context(async () => ok(), {
        window: {location: {reload: () => destinations.push('reload')}}
    });
    recovered.message = {};
    await vm.runInContext('recoverPageSession(message)', recovered);
    assert.deepEqual(destinations, ['reload']);

    const recoveryCalls = [];
    const rotated = context(async url => {
        recoveryCalls.push(url);
        return url === '/refresh' ? ok() : unauthorized();
    }, {window: {location: {reload: () => recoveryCalls.push('reload')}}});
    rotated.message = {};
    await vm.runInContext('recoverPageSession(message)', rotated);
    assert.deepEqual(recoveryCalls, ['/auth/session', '/refresh', 'reload']);

    console.log('Auth browser checks passed: CSRF headers, single-flight refresh, cross-tab coordination, expiry redirects and public pricing.');
})().catch(error => { console.error(error); process.exitCode = 1; });
