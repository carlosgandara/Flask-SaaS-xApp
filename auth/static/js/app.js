/* Shared browser transport: CSRF for mutations and coordinated session refresh. */
let csrfValue = document.getElementById('auth-csrf-token')?.content || '';
let refreshInFlight = null;

async function authFetch(url, options = {}) {
    const method = (options.method || 'GET').toUpperCase();
    const headers = {...(options.headers || {})};
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
        if (!csrfValue) {
            const response = await fetch('/auth/csrf', {credentials: 'same-origin', cache: 'no-store'});
            if (!response.ok) throw new Error('Unable to prepare a secure request. Reload this page.');
            csrfValue = (await response.json()).csrf_token;
        }
        headers['X-CSRF-Token'] = csrfValue;
    }
    return fetch(url, {...options, credentials: 'same-origin', headers});
}

async function refreshSession() {
    if (!refreshInFlight) {
        refreshInFlight = authFetch('/refresh', {method: 'POST'}).finally(() => { refreshInFlight = null; });
    }
    return refreshInFlight;
}

async function authenticatedFetch(url, options = {}, redirectOnFailure = true) {
    let response = await authFetch(url, options);
    if (response.status !== 401) return response;
    async function restore() {
        // Web Locks coordinate tabs. Probe before refreshing because another tab
        // may already have rotated the shared cookies while this one waited.
        if (typeof navigator !== 'undefined' && navigator.locks) {
            return navigator.locks.request('auth-session-refresh', async () => {
                const probe = await authFetch('/auth/session', {cache: 'no-store'});
                return probe.ok || (await refreshSession()).ok;
            });
        }
        return (await refreshSession()).ok;
    }
    if (await restore()) response = await authFetch(url, options);
    if (response.status === 401 && redirectOnFailure) window.location.href = '/login';
    return response;
}

async function logout() {
    try {
        const response = await authFetch('/logout', {method: 'POST'});
        if (!response.ok) throw new Error('Could not sign out. Please try again.');
        window.location.href = '/login';
    } catch (error) {
        const message = document.getElementById('auth-message');
        if (message) message.textContent = error.message;
    }
}

const forms = [
    ['login-form', '/login', () => ({email: document.getElementById('email').value, password: document.getElementById('password').value})],
    ['register-form', '/register', () => ({email: document.getElementById('email').value, password: document.getElementById('password').value})],
    ['forgot-password-form', '/forgot-password', () => ({email: document.getElementById('email').value})],
    ['resend-verification-form', '/resend-verification', () => ({email: document.getElementById('email').value})],
    ['reset-password-form', '/reset-password', () => ({token: new URLSearchParams(window.location.search).get('token'), new_password: document.getElementById('new_password').value})]
];
for (const [id, endpoint, payload] of forms) {
    const form = document.getElementById(id);
    if (!form) continue;
    let submitting = false;
    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (submitting) return;
        submitting = true;
        const button = form.querySelector('button[type="submit"]');
        const message = document.getElementById('msg');
        if (button) button.disabled = true;
        message.textContent = 'Please wait…';
        message.className = '';
        try {
            const response = await authFetch(endpoint, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload())});
            const data = await response.json();
            if (!response.ok) throw new Error((data.error || 'Request failed.') + (data.reference ? ` Reference: ${data.reference}` : ''));
            message.className = 'success';
            message.textContent = data.message;
            if (endpoint === '/login') window.location.href = '/dashboard';
        } catch (error) {
            message.className = 'error';
            message.textContent = error.message || 'Unable to connect. Try again.';
        } finally {
            submitting = false;
            if (button) button.disabled = false;
        }
    });
}

async function fetchProtectedData() {
    const output = document.getElementById('api-response');
    if (!output) return;
    output.textContent = 'Loading…';
    try {
        const response = await authenticatedFetch('/protected');
        const data = await response.json();
        output.textContent = response.ok ? data.message : (data.error || 'Unable to load data');
    } catch (error) { output.textContent = 'Unable to connect. Try again.'; }
}
if (document.getElementById('api-response')) {
    document.getElementById('refresh-data')?.addEventListener('click', fetchProtectedData);
    fetchProtectedData();
}
if (document.getElementById('user-email')) {
    authenticatedFetch('/protected').then(async response => {
        const data = await response.json();
        document.getElementById('user-email').textContent = response.ok ? data.message : 'Please sign in again.';
    }).catch(() => { document.getElementById('user-email').textContent = 'Unable to load your profile.'; });
}
document.querySelectorAll('[data-action="logout"]').forEach(button => button.addEventListener('click', logout));

async function recoverPageSession(message) {
    const controller = new AbortController();
    let timer;
    try {
        // Bound the entire recovery, including waiting for another tab's lock.
        const timeout = new Promise((_, reject) => {
            timer = setTimeout(() => {
                controller.abort();
                reject(new Error('Session recovery timed out'));
            }, 10000);
        });
        const response = await Promise.race([
            restorePageCookies(controller.signal),
            timeout,
        ]);
        if (response.ok) {
            window.location.reload();
        } else if (response.status === 401 || response.status === 403) {
            window.location.replace('/login');
        } else {
            message.textContent = 'Session recovery is temporarily unavailable. Reload to retry or sign in again.';
        }
    } catch (_) {
        message.textContent = 'Unable to restore your session. Reload to retry or sign in again.';
    } finally {
        clearTimeout(timer);
    }
}

async function restorePageCookies(signal) {
    async function restore() {
        if (signal.aborted) throw new Error('Session recovery cancelled');
        // Another tab may already have restored the shared cookies.
        const probe = await authFetch('/auth/session', {cache: 'no-store', signal});
        if (probe.status !== 401) return probe;
        return authFetch('/refresh', {method: 'POST', signal});
    }
    if (typeof navigator !== 'undefined' && navigator.locks) {
        return navigator.locks.request('auth-session-refresh', {signal}, restore);
    }
    return restore();
}

if (document.getElementById('auth-session-recovery')) {
    recoverPageSession(document.getElementById('auth-session-recovery'));
}

// Learning clients share the same CSRF and session recovery transport.
window.learningFetch = authenticatedFetch;
