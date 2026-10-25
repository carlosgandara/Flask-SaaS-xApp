"""Shared extension instances, independent of HTTP routes."""
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=["300 per minute", "3000 per hour"])
