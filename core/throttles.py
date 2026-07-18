"""
Per-view rate limits. Each class here is just a `scope` label — the actual
requests-per-minute numbers live in core/settings/base.py's
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"], keyed by that same scope string.
Splitting scope (here) from rate (settings) means the numbers can differ
per environment (see dev.py, which loosens these drastically) without
touching this file.

To use one, set `throttle_classes = [BookingThrottle]` (or similar) on a
view — DRF's DEFAULT_THROTTLE_CLASSES only applies DefaultThrottle unless
a view overrides it.
"""

from rest_framework.throttling import UserRateThrottle


class BookingThrottle(UserRateThrottle):
    scope = "booking"


class AuthThrottle(UserRateThrottle):
    scope = "auth"


class DefaultThrottle(UserRateThrottle):
    scope = "default"