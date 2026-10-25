"""Safe errors for clients; never expose provider responses or credentials."""


class BillingError(Exception):
    status_code = 400


class ProviderUnavailable(BillingError):
    status_code = 503


class InvalidWebhook(BillingError):
    pass


class BillingConflict(BillingError):
    status_code = 409


class CheckoutNotCreated(ProviderUnavailable):
    """A preflight GET failed before a payment POST could have been sent."""


class SyncBusy(ProviderUnavailable):
    pass


class UnmatchedCheckout(ProviderUnavailable):
    pass
