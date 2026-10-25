const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const checkoutUrl = 'https://store.lemonsqueezy.com/checkout/custom/test?signature=example';
const response = data => ({status: 200, ok: true, json: async () => data});
const settle = () => new Promise(setImmediate);

function harness(responses, {sdk = true, openThrows = false} = {}) {
    const calls = [], destinations = [], opened = [];
    const callbacks = {};
    let handler;
    let focused = false;
    const button = {dataset: {checkoutPlan: 'monthly'}, disabled: false,
        addEventListener: (name, callback) => { callbacks.subscribe = callback; }, focus() { focused = true; }};
    const elements = {
        'auth-csrf-token': {content: 'example'},
        'billing-root': {dataset: {csrf: 'example'}, querySelectorAll: selector =>
            selector === '[data-checkout-plan]' ? [button] : []},
        'billing-message': {textContent: ''},
        'billing-checkout-fallback': {hidden: true},
        'billing-checkout-link': {},
        'billing-confirmation': {hidden: true},
        'billing-confirmation-status': {textContent: ''},
        'billing-close-checkout': {addEventListener: (name, callback) => { callbacks.close = callback; }}
    };
    const vendor = {
        Setup: options => { handler = options.eventHandler; },
        Url: {Open: url => { if (openThrows) throw new Error('Vendor unavailable'); opened.push(url); }, Close() {}},
        Loader: {Hide() {}}
    };
    const window = {location: {assign: url => destinations.push(url)}};
    if (sdk) {
        window.createLemonSqueezy = () => { window.LemonSqueezy = vendor; };
    }
    const context = vm.createContext({
        window, URL, setTimeout: callback => callback(),
        document: {getElementById: id => elements[id] || null, querySelectorAll: () => [],
            querySelector: () => ({title: '', focus() {}})},
        fetch: async (url, options) => {
            calls.push({url, options});
            const next = responses.shift();
            assert.ok(next, 'Unexpected request');
            if (next instanceof Error) throw next;
            return next;
        }
    });
    for (const file of ['auth/static/js/app.js', 'billing/static/js/checkout-overlay.js', 'billing/static/js/billing.js']) {
        vm.runInContext(fs.readFileSync(path.join(__dirname, '..', file), 'utf8'), context);
    }
    return {calls, destinations, opened, elements, button,
        click: async () => { await callbacks.subscribe(); await settle(); },
        event: async event => { handler(event); await settle(); },
        close: () => callbacks.close(), focused: () => focused};
}

(async () => {
    const success = harness([
        response({provider: 'lemonsqueezy', url: checkoutUrl}),
        response({subscriptions: []}),
        response({subscriptions: [{provider: 'lemonsqueezy', plan: 'monthly', has_access: true}]})
    ]);
    await success.click();
    assert.deepEqual(success.opened, [checkoutUrl]);
    assert.deepEqual(success.destinations, []);
    assert.equal(success.elements['billing-checkout-link'].href, checkoutUrl);
    assert.equal(success.elements['billing-checkout-fallback'].hidden, false);
    await success.event({event: 'Checkout.Success', data: {untrusted: 'ignored'}});
    assert.equal(success.calls.filter(call => call.url === '/billing/api/membership').length, 2);
    assert.deepEqual(success.destinations, ['/billing/membership']);

    const pending = harness([response({provider: 'lemonsqueezy', url: checkoutUrl}),
        ...Array.from({length: 8}, () => response({subscriptions: []}))]);
    await pending.click();
    await pending.event({event: 'Checkout.Success', data: {status: 'active', has_access: true}});
    assert.deepEqual(pending.destinations, []);
    assert.equal(pending.button.disabled, true);
    assert.equal(pending.button.hidden, true);
    assert.equal(pending.elements['billing-message'].textContent, '');
    assert.match(pending.elements['billing-confirmation-status'].textContent, /do not need to pay again/);
    const count = pending.calls.length;
    await pending.event({event: 'Checkout.Success'});
    assert.equal(pending.calls.length, count, 'Repeated events must not start another polling loop');

    const cancelled = harness([response({provider: 'lemonsqueezy', url: checkoutUrl})]);
    await cancelled.click();
    await cancelled.event('close');
    assert.equal(cancelled.button.disabled, false);
    assert.equal(cancelled.focused(), true);
    assert.deepEqual(cancelled.destinations, []);
    assert.equal(cancelled.elements['billing-checkout-fallback'].hidden, true);

    for (const options of [{sdk: false}, {openThrows: true}]) {
        const fallback = harness([response({provider: 'lemonsqueezy', url: checkoutUrl})], options);
        await fallback.click();
        assert.deepEqual(fallback.destinations, [checkoutUrl]);
        assert.equal(fallback.calls.length, 1, 'Fallback must reuse the existing checkout');
    }

    const stripe = harness([response({provider: 'stripe', url: 'https://checkout.stripe.com/test'})]);
    await stripe.click();
    assert.deepEqual(stripe.opened, []);
    assert.deepEqual(stripe.destinations, ['https://checkout.stripe.com/test']);

    const malicious = harness([response({provider: 'lemonsqueezy', url: 'https://evil.invalid/checkout'})]);
    await malicious.click();
    assert.deepEqual(malicious.opened, []);
    assert.deepEqual(malicious.destinations, []);
    assert.match(malicious.elements['billing-message'].textContent, /Invalid payment destination/);

    console.log('Overlay checks passed: open, server-confirmed success, delayed webhook, duplicate events, cancellation, hosted fallbacks, Stripe, unsafe URL rejection.');
})().catch(error => { console.error(error); process.exitCode = 1; });
