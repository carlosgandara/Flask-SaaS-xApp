from .lemonsqueezy import LemonSqueezyProvider
from .stripe import StripeProvider


def build_providers(config):
    classes = {"stripe": StripeProvider, "lemonsqueezy": LemonSqueezyProvider}
    names = {plan["provider"] for plan in config["BILLING_PLANS"].values()}
    return {name: classes[name](config) for name in names}
