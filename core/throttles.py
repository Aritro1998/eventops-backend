from rest_framework.throttling import UserRateThrottle


class BookingThrottle(UserRateThrottle):
    scope = "booking"


class AuthThrottle(UserRateThrottle):
    scope = "auth"


class DefaultThrottle(UserRateThrottle):
    scope = "default"