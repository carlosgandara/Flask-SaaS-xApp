/* No payment secrets, user IDs or access decisions belong in browser code. */
(() => {
    const root = document.getElementById('billing-root');
    if (!root) return;
    const message = document.getElementById('billing-message');
    let busy = false;

    const recovery = document.getElementById('billing-session-recovery');
    if (recovery) {
        const retry = document.getElementById('billing-retry-session');
        async function restoreSession() {
            retry.hidden = true;
            recovery.textContent = 'Restoring your session…';
            try {
                // The protected API returns 401, allowing authenticatedFetch to
                // refresh cookies with POST /refresh and retry. Never grant access
                // from a checkout query parameter or refresh during a GET request.
                const response = await authenticatedFetch('/billing/api/membership', {
                    credentials: 'same-origin', cache: 'no-store'
                });
                if (response.status === 401) {
                    window.location.replace('/login');
                    return;
                }
                if (!response.ok) throw new Error('Session recovery failed');
                window.location.replace('/billing/membership' + window.location.search);
            } catch (error) {
                recovery.textContent = 'Unable to restore your session. Try again or log in. You do not need to pay again.';
                retry.hidden = false;
            }
        }
        retry.addEventListener('click', restoreSession);
        restoreSession();
        return;
    }

    async function submit(button, path, payload) {
        if (busy) return;
        busy = true;
        button.disabled = true;
        message.textContent = 'Please wait…';
        try {
            const response = await authenticatedFetch(path, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {'Content-Type': 'application/json', 'X-CSRF-Token': root.dataset.csrf},
                body: JSON.stringify(payload)
            });
            if (response.status === 401) {
                window.location.assign('/login');
                return;
            }
            const data = await response.json();
            if (!response.ok) {
                const reference = response.status >= 500 ? response.headers.get('X-Request-ID') : null;
                throw new Error((data.error || 'Billing is unavailable. Try again later.') +
                    (reference ? ` Reference: ${reference}` : ''));
            }
            const target = new URL(data.url);
            if (target.protocol !== 'https:') throw new Error('Invalid payment destination.');
            if (path === '/billing/checkout' && (target.username || target.password ||
                (target.port && target.port !== '443') ||
                (data.provider === 'lemonsqueezy' ? !target.hostname.endsWith('.lemonsqueezy.com') :
                    data.provider !== 'stripe' || target.hostname !== 'checkout.stripe.com'))) {
                throw new Error('Invalid payment destination.');
            }
            if (path === '/billing/checkout' && data.provider === 'lemonsqueezy' &&
                window.BillingCheckoutOverlay?.open({
                    url: target.href, plan: payload.plan, button,
                    onClose() {
                        busy = false;
                        button.disabled = false;
                        message.textContent = 'Checkout closed. You can reopen it when ready.';
                    }
                })) {
                message.textContent = 'Complete your subscription in the secure checkout window.';
                return;
            }
            // Stripe, unavailable vendor scripts and overlay failures use hosted checkout.
            window.location.assign(target.href);
        } catch (error) {
            message.textContent = error.message || 'Unable to connect. Try again later.';
            button.disabled = false;
            busy = false;
        }
    }

    root.querySelectorAll('[data-checkout-plan]').forEach(button => {
        button.addEventListener('click', () => submit(button, '/billing/checkout', {plan: button.dataset.checkoutPlan}));
    });
    root.querySelectorAll('[data-portal-subscription]').forEach(button => {
        button.addEventListener('click', () => submit(button, '/billing/portal', {
            subscription_id: Number(button.dataset.portalSubscription)
        }));
    });

    const planStatus = document.getElementById('billing-plan-status');
    if (planStatus) {
        async function loadPlanStatus() {
            try {
                // Pricing remains public. Recover an existing session if possible,
                // but do not redirect anonymous visitors away from the catalog.
                const response = await authenticatedFetch('/billing/api/membership', {
                    credentials: 'same-origin', cache: 'no-store'
                }, false);
                if (response.status === 401) {
                    planStatus.textContent = 'Log in with your verified account to view your membership or subscribe.';
                    document.getElementById('billing-plan-login').hidden = false;
                    return;
                }
                if (!response.ok) throw new Error('Membership check failed');
                const data = await response.json();
                if (root.dataset.checkoutCompleted === 'true') return;
                if (data.can_subscribe) {
                    planStatus.textContent = 'You have no current subscription. Choose a plan to subscribe.';
                    root.querySelectorAll('[data-checkout-plan]').forEach(button => {
                        button.hidden = false;
                        button.disabled = false;
                    });
                    return;
                }
                const current = data.current_subscription;
                if (!current) throw new Error('Membership check failed');
                planStatus.textContent = `Your ${current.plan_name} membership is ${current.status}. Manage your existing subscription below.`;
                root.querySelectorAll('[data-current-plan]').forEach(label => {
                    label.hidden = label.dataset.currentPlan !== current.plan;
                });
                const manage = document.getElementById('billing-manage-current');
                manage.hidden = false;
                manage.addEventListener('click', () => submit(manage, '/billing/portal', {
                    subscription_id: current.id
                }));
            } catch (error) {
                planStatus.textContent = 'Unable to check your membership. Refresh this page to try again.';
            }
        }
        loadPlanStatus();
    }
})();
