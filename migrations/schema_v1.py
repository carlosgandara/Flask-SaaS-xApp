"""Frozen schema baseline for both new installs and existing starter databases."""
import sqlalchemy as sa

metadata = sa.MetaData()

sa.Table('billing_operator_actions', metadata,
    sa.Column('id', sa.Integer(), nullable=False, primary_key=True),
    sa.Column('actor', sa.String(length=128), nullable=False),
    sa.Column('action', sa.String(length=32), nullable=False),
    sa.Column('target_id', sa.Integer(), nullable=False),
    sa.Column('reason', sa.String(length=256), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
)


sa.Table('billing_sync_leases', metadata,
    sa.Column('key', sa.String(length=200), nullable=False, primary_key=True),
    sa.Column('token', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
)


sa.Table('billing_webhook_inbox', metadata,
    sa.Column('id', sa.Integer(), nullable=False, primary_key=True),
    sa.Column('provider', sa.String(length=24), nullable=False),
    sa.Column('mode', sa.String(length=8), nullable=False),
    sa.Column('event_id', sa.String(length=128), nullable=False),
    sa.Column('event_type', sa.String(length=96), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=True),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('available_at', sa.DateTime(), nullable=False),
    sa.Column('lease_token', sa.String(length=64), nullable=True),
    sa.Column('lease_until', sa.DateTime(), nullable=True),
    sa.Column('last_error', sa.String(length=80), nullable=True),
    sa.Column('request_id', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.UniqueConstraint('provider', 'mode', 'event_id', name='uq_billing_inbox_event'),
)

sa.Index('ix_billing_inbox_due', metadata.tables['billing_webhook_inbox'].c['mode'], metadata.tables['billing_webhook_inbox'].c['status'], metadata.tables['billing_webhook_inbox'].c['available_at'], unique=False)

sa.Table('billing_webhook_receipts', metadata,
    sa.Column('id', sa.Integer(), nullable=False, primary_key=True),
    sa.Column('provider', sa.String(length=24), nullable=False),
    sa.Column('mode', sa.String(length=8), nullable=False),
    sa.Column('event_id', sa.String(length=128), nullable=False),
    sa.Column('event_type', sa.String(length=96), nullable=False),
    sa.Column('processed_at', sa.DateTime(), nullable=False),
    sa.UniqueConstraint('provider', 'mode', 'event_id', name='uq_billing_webhook'),
)


sa.Table('billing_worker_heartbeats', metadata,
    sa.Column('id', sa.String(length=64), nullable=False, primary_key=True),
    sa.Column('mode', sa.String(length=8), nullable=False),
    sa.Column('last_seen', sa.DateTime(), nullable=False),
)


sa.Table('users', metadata,
    sa.Column('id', sa.Integer(), nullable=False, primary_key=True),
    sa.Column('email', sa.String(), nullable=False, unique=True),
    sa.Column('password', sa.String(), nullable=False),
    sa.Column('verified', sa.Boolean(), nullable=True),
    sa.Column('verification_token', sa.String(), nullable=True),
    sa.Column('verification_expiry', sa.DateTime(), nullable=True),
    sa.Column('reset_token', sa.String(), nullable=True),
    sa.Column('reset_expiry', sa.DateTime(), nullable=True),
    sa.Column('failed_login_attempts', sa.Integer(), nullable=True),
    sa.Column('locked_until', sa.DateTime(), nullable=True),
)

sa.Index('ix_users_email', metadata.tables['users'].c['email'], unique=True)
sa.Index('ix_users_id', metadata.tables['users'].c['id'], unique=False)

sa.Table('auth_action_tokens', metadata,
    sa.Column('id', sa.Integer(), nullable=False, primary_key=True),
    sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', name='fk_auth_action_tokens_user_id'), nullable=False),
    sa.Column('purpose', sa.String(length=16), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False, unique=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('consumed_at', sa.DateTime(), nullable=True),
)

sa.Index('ix_auth_action_tokens_user_id', metadata.tables['auth_action_tokens'].c['user_id'], unique=False)

sa.Table('auth_sessions', metadata,
    sa.Column('id', sa.String(length=64), nullable=False, primary_key=True),
    sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', name='fk_auth_sessions_user_id'), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
)

sa.Index('ix_auth_sessions_user_id', metadata.tables['auth_sessions'].c['user_id'], unique=False)

sa.Table('billing_checkout_intents', metadata,
    sa.Column('id', sa.String(length=64), nullable=False, primary_key=True),
    sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', name='fk_billing_checkout_intents_user_id'), nullable=False),
    sa.Column('plan_key', sa.String(length=64), nullable=False),
    sa.Column('provider', sa.String(length=24), nullable=False),
    sa.Column('product_id', sa.String(length=128), nullable=False),
    sa.Column('mode', sa.String(length=8), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('checkout_url', sa.String(length=2048), nullable=True),
    sa.Column('subscription_id', sa.String(length=128), nullable=True),
    sa.Column('state', sa.String(length=16), nullable=False),
)

sa.Index('ix_billing_checkout_intents_user_id', metadata.tables['billing_checkout_intents'].c['user_id'], unique=False)

sa.Table('billing_subscriptions', metadata,
    sa.Column('id', sa.Integer(), nullable=False, primary_key=True),
    sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', name='fk_billing_subscriptions_user_id'), nullable=False),
    sa.Column('provider', sa.String(length=24), nullable=False),
    sa.Column('mode', sa.String(length=8), nullable=False),
    sa.Column('external_id', sa.String(length=128), nullable=False),
    sa.Column('customer_id', sa.String(length=128), nullable=False),
    sa.Column('plan_key', sa.String(length=64), nullable=True),
    sa.Column('product_id', sa.String(length=128), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('access_until', sa.DateTime(), nullable=True),
    sa.Column('cancel_at_period_end', sa.Boolean(), nullable=False),
    sa.Column('blocked', sa.Boolean(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.UniqueConstraint('provider', 'mode', 'external_id', name='uq_billing_subscription'),
)

sa.Index('ix_billing_subscriptions_user_id', metadata.tables['billing_subscriptions'].c['user_id'], unique=False)

sa.Table('refresh_tokens', metadata,
    sa.Column('id', sa.Integer(), nullable=False, primary_key=True),
    sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', name='fk_refresh_tokens_user_id'), nullable=False),
    sa.Column('session_id', sa.String(length=64), sa.ForeignKey('auth_sessions.id', name='fk_refresh_tokens_session_id'), nullable=True),
    sa.Column('token_hash_sha256', sa.String(length=64), nullable=False),
    sa.Column('token_hash_bcrypt', sa.String(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
)

sa.Index('ix_refresh_tokens_token_hash_sha256', metadata.tables['refresh_tokens'].c['token_hash_sha256'], unique=False)
sa.Index('ix_refresh_tokens_session_id', metadata.tables['refresh_tokens'].c['session_id'], unique=False)
sa.Index('ix_refresh_tokens_id', metadata.tables['refresh_tokens'].c['id'], unique=False)
