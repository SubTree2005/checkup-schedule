from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import Cookie, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .database import get_db
from .models import HospitalAdmin, UserInfo, UserSession, utcnow

PASSWORD_ITERATIONS = 310_000
SESSION_DAYS = 7
SESSION_COOKIE = "checkup_admin_session"


@dataclass(frozen=True)
class AdminContext:
    user_id: str
    hospital_id: str
    name: str
    phone: str
    is_owner: bool


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PASSWORD_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode(),
        base64.urlsafe_b64encode(digest).decode(),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode())
        expected = base64.urlsafe_b64decode(digest_text.encode())
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def issue_session(db: Session, user_id: str, login_ip: str | None) -> str:
    raw_token = secrets.token_urlsafe(32)
    db.add(
        UserSession(
            session_id=session_digest(raw_token),
            user_id=user_id,
            login_ip=login_ip,
            expires_at=utcnow() + timedelta(days=SESSION_DAYS),
        )
    )
    return raw_token


def session_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_DAYS * 24 * 3600,
        httponly=True,
        secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def get_current_admin(
    request: Request,
    db: Session = Depends(get_db),
    cookie_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    authorization: str | None = Header(default=None),
) -> AdminContext:
    token = cookie_token
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")

    row = db.execute(
        select(UserSession, UserInfo, HospitalAdmin)
        .join(UserInfo, UserInfo.user_id == UserSession.user_id)
        .join(HospitalAdmin, HospitalAdmin.user_id == UserInfo.user_id)
        .where(UserSession.session_id == session_digest(token))
    ).one_or_none()
    if row is None or row.UserSession.expires_at <= utcnow():
        if row is not None:
            db.delete(row.UserSession)
            db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期")
    return AdminContext(
        user_id=row.UserInfo.user_id,
        hospital_id=row.HospitalAdmin.hospital_id,
        name=row.UserInfo.name,
        phone=row.UserInfo.phone,
        is_owner=row.HospitalAdmin.is_owner,
    )


def revoke_session(db: Session, token: str | None) -> None:
    if token:
        db.execute(delete(UserSession).where(UserSession.session_id == session_digest(token)))
