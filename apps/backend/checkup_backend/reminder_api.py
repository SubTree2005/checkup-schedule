from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_db
from .models import WechatReminder
from .patient_api import PatientContext, get_current_patient
from .serializers import iso
from .wechat_reminders import dispatch_due_reminders, public_reminder_config

patient_reminder_router = APIRouter(prefix="/api/patient/reminders", tags=["patient-reminders"])
internal_reminder_router = APIRouter(prefix="/api/internal/reminders", tags=["internal-reminders"])


@patient_reminder_router.get("/config")
def reminder_config(_patient: PatientContext = Depends(get_current_patient)) -> dict:
    return public_reminder_config()


@patient_reminder_router.get("")
def list_reminders(
    patient: PatientContext = Depends(get_current_patient),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.scalars(
        select(WechatReminder)
        .where(WechatReminder.user_id == patient.user_id)
        .order_by(WechatReminder.create_time.desc())
    )
    return [
        {
            "reminderID": row.reminder_id,
            "planID": row.plan_id,
            "scheduledAt": iso(row.scheduled_at),
            "status": row.status,
            "attemptCount": row.attempt_count,
            "lastError": row.last_error,
            "sentAt": iso(row.sent_at),
        }
        for row in rows
    ]


@internal_reminder_router.post("/dispatch")
def dispatch_reminders(
    limit: int = Query(default=50, ge=1, le=200),
    dispatch_token: str | None = Header(default=None, alias="X-Reminder-Dispatch-Token"),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    expected = os.getenv("REMINDER_DISPATCH_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="提醒派发令牌尚未配置")
    if not dispatch_token or not hmac.compare_digest(dispatch_token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="派发令牌无效")
    return dispatch_due_reminders(db, limit=limit)
