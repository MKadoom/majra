"""Alert channel check (owner-only)."""
from fastapi import APIRouter, Depends

from app.api.auth import require_owner
from app.models import User
from app.services import alerts

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.post("/test")
def send_test_alert(user: User = Depends(require_owner)):
    """Send one test alert to every configured channel and return what each
    answered: per WhatsApp recipient the HTTP status and Meta's error code and
    message, if any. A 2xx means WhatsApp accepted the message; delivery is
    confirmed by it arriving on the phone."""
    return alerts.send_test()
