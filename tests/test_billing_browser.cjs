// Exercise the real browser scripts without a network or customer credentials.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function runRecovery(responses) {
    const calls = [];
    const destinations = [];
    const retry = {hidden: true, addEventListener() {}};
    const recovery = {textContent: ''};
    const elements = {
        'auth-csrf-token': {content: 'example'},
        'billing-root': {dataset: {}, querySelectorAll: () => []},
        'billing-message': {textContent: ''},
        'billing-session-recovery': recovery,
        'billing-retry-session': retry
    };
    const context = vm.createContext({
        document: {getElementById: id => elements[id] || null, querySelectorAll: () => []},
        window: {location: {search: '?checkout=returned', replace: url => destinations.push(url)}},
        fetch: async (url, options) => {
            calls.push({url, options});
            const result = responses.shift();
            if (result instanceof Error) throw result;
            assert.ok(result, 'Unexpected extra request');
            return result;
        }
    });
    for (const filename of ['auth/static/js/app.js', 'billing/static/js/billing.js']) {
        vm.runInContext(fs.readFileSync(path.join(__dirname, '..', filename), 'utf8'), context);
    }
    await new Promise(setImmediate);
    return {calls, destinations, retry, recovery, location: context.window.location};
}

async function runPricing(responses) {
    const subscribe = {hidden: true, disabled: true, dataset: {checkoutPlan: 'monthly'}, addEventListener() {}};
    const marker = {hidden: true, dataset: {currentPlan: 'monthly'}};
    const status = {textContent: ''};
    const manage = {hidden: true, addEventListener() {}};
    const login = {hidden: true};
    const elements = {
        'auth-csrf-token': {content: 'example'},
        'billing-root': {dataset: {}, querySelectorAll: selector =>
            selector === '[data-checkout-plan]' ? [subscribe] : selector === '[data-current-plan]' ? [marker] : []},
        'billing-message': {textContent: ''}, 'billing-plan-status': status,
        'billing-manage-current': manage, 'billing-plan-login': login
    };
    const context = vm.createContext({
        document: {getElementById: id => elements[id] || null, querySelectorAll: () => []},
        window: {location: {}},
        fetch: async () => {
            const result = responses.shift();
            if (result instanceof Error) throw result;
            assert.ok(result, 'Unexpected extra pricing request');
            return result;
        }
    });
    for (const filename of ['auth/static/js/app.js', 'billing/static/js/billing.js']) {
        vm.runInContext(fs.readFileSync(path.join(__dirname, '..', filename), 'utf8'), context);
    }
    await new Promise(setImmediate);
    return {subscribe, marker, status, manage, login, location: context.window.location};
}

(async () => {
    const expired = await runRecovery([
        {status: 401, ok: false}, {status: 200, ok: true}, {status: 200, ok: true}
    ]);
    assert.deepEqual(expired.calls.map(call => call.url), [
        '/billing/api/membership', '/refresh', '/billing/api/membership'
    ]);
    assert.equal(expired.calls[1].options.method, 'POST');
    assert.deepEqual(expired.destinations, ['/billing/membership?checkout=returned']);

    const loggedOut = await runRecovery([{status: 401, ok: false}, {status: 401, ok: false}]);
    assert.equal(loggedOut.location.href, '/login');
    assert.deepEqual(loggedOut.destinations, ['/login']);

    const offline = await runRecovery([new Error('Offline')]);
    assert.equal(offline.retry.hidden, false);
    assert.match(offline.recovery.textContent, /do not need to pay again/);
    assert.deepEqual(offline.destinations, []);

    const subscriber = await runPricing([{status: 200, ok: true, json: async () => ({
        can_subscribe: false,
        current_subscription: {id: 1, plan: 'monthly', plan_name: 'Monthly membership', status: 'active'}
    })}]);
    assert.equal(subscriber.subscribe.hidden, true);
    assert.equal(subscriber.manage.hidden, false);
    assert.equal(subscriber.marker.hidden, false);
    assert.match(subscriber.status.textContent, /membership is active/);

    const newUser = await runPricing([{status: 200, ok: true, json: async () => ({can_subscribe: true})}]);
    assert.equal(newUser.subscribe.hidden, false);
    assert.equal(newUser.subscribe.disabled, false);
    assert.equal(newUser.manage.hidden, true);

    const anonymous = await runPricing([{status: 401, ok: false}, {status: 401, ok: false}]);
    assert.equal(anonymous.login.hidden, false);
    assert.equal(anonymous.location.href, undefined);
    assert.equal(anonymous.subscribe.hidden, true);

    const pricingOffline = await runPricing([new Error('Offline')]);
    assert.equal(pricingOffline.subscribe.hidden, true);
    assert.match(pricingOffline.status.textContent, /Unable to check/);

    console.log('Browser recovery passed: expired cookie, logged-out user, network failure.');
    console.log('Pricing states passed: subscriber, new account, anonymous, network failure.');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
