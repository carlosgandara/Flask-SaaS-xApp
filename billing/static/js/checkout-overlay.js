/* Presentation only: payment events never grant membership in the browser. */
(() => {
    let checkout = null;
    let confirming = false;

    function closeFrame() {
        try { window.LemonSqueezy?.Url?.Close?.(); } catch (error) { /* Vendor cleanup is best effort. */ }
        try { window.LemonSqueezy?.Loader?.Hide?.(); } catch (error) { /* Preserve hosted fallback. */ }
    }

    function cancelCheckout() {
        if (!checkout || confirming) return;
        const previous = checkout;
        checkout = null;
        closeFrame();
        document.getElementById('billing-checkout-fallback').hidden = true;
        previous.onClose();
        previous.button.focus();
    }

    async function confirmMembership() {
        if (!checkout || confirming) return;
        confirming = true;
        closeFrame();
        document.getElementById('billing-checkout-fallback').hidden = true;
        document.getElementById('billing-confirmation').hidden = false;
        const status = document.getElementById('billing-confirmation-status');
        status.textContent = 'Checkout completed. Confirming your membership…';
        const message = document.getElementById('billing-message');
        if (message) message.textContent = '';
        const planStatus = document.getElementById('billing-plan-status');
        if (planStatus) planStatus.textContent = 'Checkout completed. Waiting for membership confirmation. You do not need to pay again.';
        const root = document.getElementById('billing-root');
        root.dataset.checkoutCompleted = 'true';
        root.querySelectorAll('[data-checkout-plan]').forEach(button => {
            button.disabled = true;
            button.hidden = true;
        });
        // Bounded polling allows time for the signed webhook to reach the server.
        // Only the protected API's has_access result can confirm membership.
        for (let attempt = 0; attempt < 8; attempt += 1) {
            try {
                const response = await authenticatedFetch('/billing/api/membership', {
                    credentials: 'same-origin', cache: 'no-store'
                }, false);
                if (response.status === 401) {
                    window.location.assign('/billing/membership?checkout=returned');
                    return;
                }
                if (response.ok) {
                    const data = await response.json();
                    if (data.subscriptions.some(row => row.provider === 'lemonsqueezy' &&
                        row.plan === checkout.plan && row.has_access)) {
                        window.location.assign('/billing/membership');
                        return;
                    }
                }
            } catch (error) {
                // A network problem must not invite the customer to pay twice.
            }
            if (attempt < 7) await new Promise(resolve => setTimeout(resolve, 4000));
        }
        status.textContent = 'Membership confirmation is taking longer than expected. Check Membership shortly. You do not need to pay again.';
    }

    window.BillingCheckoutOverlay = {
        open(options) {
            const fallback = document.getElementById('billing-checkout-fallback');
            if (!fallback || checkout || !window.createLemonSqueezy) {
                console.info('billing.overlay_unavailable', !fallback ? 'page' : checkout ? 'already_open' : 'vendor_script');
                return false;
            }
            const target = new URL(options.url);
            if (target.protocol !== 'https:' || !target.hostname.endsWith('.lemonsqueezy.com') ||
                target.username || target.password || (target.port && target.port !== '443') ||
                !target.pathname.startsWith('/checkout')) return false;
            try {
                // Explicit initialization also works if the async vendor script
                // downloaded after the window load event.
                window.createLemonSqueezy();
                if (!window.LemonSqueezy?.Setup || !window.LemonSqueezy?.Url?.Open) return false;
                window.LemonSqueezy.Setup({eventHandler(event) {
                    if (event?.event === 'Checkout.Success') confirmMembership();
                    // Lemon.js forwards the frame's close string after verifying
                    // its origin and source. This only restores the UI, not access.
                    else if (event === 'close') cancelCheckout();
                }});
                checkout = options;
                document.getElementById('billing-checkout-link').href = target.href;
                fallback.hidden = false;
                window.LemonSqueezy.Url.Open(target.href);
                const frame = document.querySelector('body > iframe[src*=".lemonsqueezy.com/checkout"]');
                if (frame) {
                    frame.title = 'Secure membership checkout';
                    frame.focus();
                }
                return true;
            } catch (error) {
                console.warn('billing.overlay_failed', error.name);
                checkout = null;
                closeFrame();
                fallback.hidden = true;
                return false;
            }
        }
    };

    document.getElementById('billing-close-checkout')?.addEventListener('click', cancelCheckout);
})();
