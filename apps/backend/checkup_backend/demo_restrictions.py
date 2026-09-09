from datetime import datetime

from .models import HospitalSettings, utcnow
from .serializers import iso


def demo_unrestricted(settings: HospitalSettings | None, now: datetime | None = None) -> bool:
    until = settings.demo_unrestricted_until if settings else None
    return until is not None and until > (now or utcnow())


def demo_restriction_status(settings: HospitalSettings | None) -> dict:
    enabled = demo_unrestricted(settings)
    return {"enabled": enabled, "expiresAt": iso(settings.demo_unrestricted_until) if enabled else None}
