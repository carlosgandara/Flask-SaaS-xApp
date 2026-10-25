"""Durable billing jobs. Run as a separate process beside the web service."""

from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import signal
import threading

import click
from flask import current_app
from flask.cli import with_appcontext
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import SQLAlchemyError

from auth.db import SessionLocal
from .errors import BillingError, InvalidWebhook, ProviderUnavailable, SyncBusy
from .inbox import claim, finish
from .leases import acquire, insert_for, release
from .models import CheckoutIntent, OperatorAction, Subscription, WebhookInbox, WorkerHeartbeat
from .providers.base import Event, identifier
from .service import process_event, provider_for, record_event


def process_one():
    job = claim()
    if job is None:
        return False
    try:
        if job['payload'].get('_internal_event'):
            data = job['payload']['_internal_event']
            event = Event(job['event_id'], 'reconcile', identifier(data['subscription_id']), data.get('checkout_ref'))
        else:
            event = provider_for(job['provider']).event_from_payload(job['payload'])
        if event is not None:
            process_event(job['provider'], event)
        else:
            with SessionLocal.begin() as db:
                record_event(db, job['provider'], Event(job['event_id'], 'ignored', 'ignored'))
        finish(job, 'done')
    except (ProviderUnavailable, SQLAlchemyError) as exc:
        retry = job['attempts'] < 8
        delay = min(3600, 5 * (2 ** min(job['attempts'], 10))) + secrets.randbelow(5)
        finish(job, 'retry' if retry else 'needs_review', type(exc).__name__, delay=delay)
        current_app.logger.warning('billing.job_failure job=%s state=%s type=%s',
                                   job['id'], 'retry' if retry else 'needs_review', type(exc).__name__)
    except (BillingError, KeyError, TypeError, ValueError, AttributeError) as exc:
        finish(job, 'needs_review', type(exc).__name__)
        current_app.logger.error('billing.job_needs_review job=%s type=%s', job['id'], type(exc).__name__)
    except Exception as exc:
        # An unexpected defect is reviewable, never silently discarded or logged
        # with a payload/SQL parameter dump. A human can replay after the fix.
        finish(job, 'needs_review', type(exc).__name__)
        current_app.logger.error('billing.job_unexpected job=%s type=%s', job['id'], type(exc).__name__)
    return True


def heartbeat(worker_id):
    with SessionLocal.begin() as db:
        now = datetime.utcnow()
        db.execute(insert_for(db, WorkerHeartbeat).values(id=worker_id, mode=current_app.config['BILLING_MODE'], last_seen=now)
                   .on_conflict_do_update(index_elements=['id'], set_={'last_seen': now}))


def reconcile_all():
    """Queue current subscriptions and discover unrecorded purchases without email matching."""
    mode = current_app.config['BILLING_MODE']
    leader = acquire('reconciliation:' + mode, seconds=300)
    queued = 0
    try:
        for name, adapter in current_app.extensions['billing_providers'].items():
            products = {plan['product_id'] for plan in current_app.config['BILLING_PLANS'].values() if plan['provider'] == name}
            cursor = None
            while True:
                records, next_cursor = adapter.list_subscriptions(cursor)
                for external in records:
                    if external['product_id'] not in products:
                        continue
                    with SessionLocal.begin() as db:
                        existing = db.execute(select(Subscription.id).where(Subscription.provider == name,
                            Subscription.mode == mode, Subscription.external_id == external['id'])).first()
                        reference = external.get('checkout_ref')
                        intent = db.get(CheckoutIntent, reference) if isinstance(reference, str) else None
                        linked = bool(intent and intent.provider == name and intent.mode == mode)
                        review = not existing and not linked
                        bucket = int(datetime.now(timezone.utc).timestamp()) // current_app.config['BILLING_RECONCILE_SECONDS']
                        identity = ('discovery:' if not existing else f'reconcile:{bucket}:') + external['id']
                        event_id = hashlib.sha256(identity.encode()).hexdigest()
                        result = db.execute(insert_for(db, WebhookInbox).values(
                            provider=name, mode=mode, event_id=event_id, event_type='reconcile',
                            payload={'_internal_event': {'subscription_id': external['id'], 'checkout_ref': reference}},
                            status='needs_review' if review else 'pending', attempts=0, available_at=datetime.utcnow(),
                            last_error='unmatched_external_subscription' if review else None, request_id='reconcile')
                            .on_conflict_do_nothing(index_elements=['provider', 'mode', 'event_id']))
                        queued += result.rowcount
                        if review and result.rowcount:
                            current_app.logger.error('billing.unmatched_purchase provider=%s subscription=%s', name, external['id'])
                if next_cursor is None:
                    break
                if next_cursor == cursor:
                    raise ProviderUnavailable('Provider returned an invalid pagination cursor')
                cursor = next_cursor
        # Existing subscriptions removed from the current catalog still need
        # cancellation/expiry reconciliation; do not silently stop maintaining them.
        last_id = 0
        while True:
            with SessionLocal() as db:
                records = db.execute(select(Subscription).where(Subscription.mode == mode,
                    Subscription.id > last_id).order_by(Subscription.id).limit(100)).scalars().all()
            if not records:
                break
            for row in records:
                last_id = row.id
                if row.provider not in current_app.extensions['billing_providers'] or row.product_id in {
                    plan['product_id'] for plan in current_app.config['BILLING_PLANS'].values() if plan['provider'] == row.provider}:
                    continue
                with SessionLocal.begin() as db:
                    event_id = secrets.token_hex(32)
                    db.add(WebhookInbox(provider=row.provider, mode=mode, event_id=event_id, event_type='reconcile',
                        payload={'_internal_event': {'subscription_id': row.external_id}}, status='pending', request_id='reconcile'))
                    queued += 1
        return queued
    finally:
        release('reconciliation:' + mode, leader)


def purge_completed_payloads():
    cutoff = datetime.utcnow() - timedelta(days=current_app.config['BILLING_EVENT_RETENTION_DAYS'])
    with SessionLocal.begin() as db:
        db.execute(update(WebhookInbox).where(WebhookInbox.status == 'done', WebhookInbox.completed_at < cutoff,
            WebhookInbox.payload.is_not(None)).values(payload=None))
        db.execute(delete(WorkerHeartbeat).where(WorkerHeartbeat.last_seen < datetime.utcnow() - timedelta(days=7)))


@click.command('billing-worker')
@click.option('--once', is_flag=True, help='Process at most one queued job, then exit (no scheduled reconciliation).')
@with_appcontext
def worker_command(once):
    if not current_app.config['BILLING_ENABLED']:
        raise click.ClickException('Enable and configure billing first')
    click.echo('Starting billing worker (' + current_app.config['BILLING_MODE'] + ' mode). Checking database schema...')
    from diagnostics import schema_problems
    try:
        if not current_app.testing and schema_problems():
            raise click.ClickException('Run db-upgrade before starting the worker')
    except SQLAlchemyError as exc:
        raise click.ClickException('Worker could not connect to/check the database (' + type(exc).__name__ + '). Check database connectivity and retry.') from None
    worker_id = secrets.token_hex(16)
    if once:
        heartbeat(worker_id)
        click.echo('Processed one job.' if process_one() else 'No pending jobs.')
        return
    stop = threading.Event()
    previous = {}
    for name in ('SIGINT', 'SIGTERM'):
        number = getattr(signal, name)
        previous[number] = signal.signal(number, lambda *_: stop.set())
    next_heartbeat = next_reconcile = datetime.min
    idle = current_app.config['BILLING_POLL_SECONDS']
    try:
        while not stop.is_set():
            try:
                now = datetime.utcnow()
                if now >= next_heartbeat:
                    heartbeat(worker_id)
                    if next_heartbeat == datetime.min:
                        click.echo('Billing worker online. Waiting for jobs; keep this terminal open. Press Ctrl+C to stop.')
                    next_heartbeat = now + timedelta(seconds=30)
                if now >= next_reconcile:
                    try:
                        reconcile_all()
                        purge_completed_payloads()
                    except SyncBusy:
                        pass
                    except Exception as exc:
                        current_app.logger.error('billing.reconciliation_failed type=%s', type(exc).__name__)
                    next_reconcile = now + timedelta(seconds=current_app.config['BILLING_RECONCILE_SECONDS'])
                if process_one():
                    idle = current_app.config['BILLING_POLL_SECONDS']
                    continue
                stop.wait(idle)
                idle = min(10, idle * 2)
            except SQLAlchemyError as exc:
                current_app.logger.error('billing.worker_database_unavailable type=%s', type(exc).__name__)
                stop.wait(5)
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
        click.echo('Billing worker stopped.')


def queue_status():
    now = datetime.utcnow()
    mode = current_app.config['BILLING_MODE']
    with SessionLocal() as db:
        counts = dict(db.execute(select(WebhookInbox.status, func.count()).where(WebhookInbox.mode == mode)
                                .group_by(WebhookInbox.status)).all())
        oldest = db.execute(select(func.min(WebhookInbox.created_at)).where(WebhookInbox.mode == mode,
            WebhookInbox.status.in_(['pending', 'retry', 'processing']))).scalar()
        alive = db.execute(select(func.count()).select_from(WorkerHeartbeat).where(
            WorkerHeartbeat.mode == mode, WorkerHeartbeat.last_seen > now - timedelta(minutes=2))).scalar()
    lag = max(0, int((now - oldest).total_seconds())) if oldest else 0
    return counts, alive, lag


@click.command('billing-status')
@click.option('--check', is_flag=True, help='Exit nonzero when the worker is missing, backlog is old, or review is needed.')
@with_appcontext
def status_command(check):
    counts, alive, lag = queue_status()
    click.echo(f'Workers online: {alive}; oldest waiting event: {lag}s; event states: {counts}')
    if check and (not alive or lag > 300 or counts.get('needs_review', 0)):
        raise click.ClickException('Billing recovery needs attention; inspect worker logs and review jobs')


@click.command('billing-jobs')
@click.option('--status', type=click.Choice(['pending', 'processing', 'retry', 'needs_review', 'done']), default='needs_review')
@click.option('--limit', type=click.IntRange(1, 100), default=25)
@with_appcontext
def jobs_command(status, limit):
    """List safe job diagnostics in the current billing mode; never print payloads."""
    with SessionLocal() as db:
        rows = db.execute(select(WebhookInbox).where(WebhookInbox.mode == current_app.config['BILLING_MODE'],
            WebhookInbox.status == status).order_by(WebhookInbox.id).limit(limit)).scalars().all()
        for row in rows:
            click.echo(f'{row.id}: {row.provider} {row.event_type} state={row.status} attempts={row.attempts} error={row.last_error or "none"} request={row.request_id}')
        if not rows:
            click.echo('No matching jobs.')


@click.command('billing-retry')
@click.argument('job_id', type=int)
@click.option('--actor', required=True, help='Operator identity label for the audit trail.')
@click.option('--reason', required=True, help='Why replay is safe after reviewing provider state.')
@click.option('--checkout-ref', default=None, help='Explicitly link an unmatched purchase to a reviewed app checkout.')
@with_appcontext
def retry_command(job_id, actor, reason, checkout_ref):
    if not actor.strip() or len(actor) > 128 or not reason.strip() or len(reason) > 256:
        raise click.ClickException('Supply an operator label (<=128 characters) and reason (<=256 characters)')
    with SessionLocal.begin() as db:
        row = db.execute(select(WebhookInbox).where(WebhookInbox.id == job_id,
            WebhookInbox.mode == current_app.config['BILLING_MODE']).with_for_update()).scalar_one_or_none()
        if row is None or row.status not in {'needs_review', 'retry'} or row.payload is None:
            raise click.ClickException('Only a retained retry/review job in this billing mode can be replayed')
        if checkout_ref:
            intent = db.get(CheckoutIntent, checkout_ref)
            if intent is None or intent.provider != row.provider or intent.mode != row.mode:
                raise click.ClickException('Checkout reference does not belong to this provider and mode')
            import copy
            payload = copy.deepcopy(row.payload)
            if '_internal_event' in payload:
                payload['_internal_event']['checkout_ref'] = checkout_ref
            elif row.provider == 'stripe':
                payload['data']['object'].setdefault('metadata', {})['checkout_ref'] = checkout_ref
            else:
                payload['meta'].setdefault('custom_data', {})['checkout_ref'] = checkout_ref
            row.payload = payload
        row.status, row.attempts, row.available_at, row.last_error = 'pending', 0, datetime.utcnow(), None
        db.add(OperatorAction(actor=actor, action='link-and-retry' if checkout_ref else 'retry', target_id=job_id, reason=reason))
    click.echo('Reviewed job queued for retry. Subscription ownership will be verified during processing.')


@click.command('billing-reconcile')
@with_appcontext
def reconcile_command():
    if not current_app.config['BILLING_ENABLED']:
        raise click.ClickException('Enable billing first')
    try:
        click.echo(f'Queued {reconcile_all()} reconciliation/review jobs. Run billing-worker to process them.')
    except BillingError as exc:
        raise click.ClickException(str(exc)) from None


def init_worker_cli(app):
    for command in (worker_command, status_command, jobs_command, retry_command, reconcile_command):
        app.cli.add_command(command)
