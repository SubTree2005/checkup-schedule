from __future__ import annotations

import json
import os
from collections import OrderedDict, deque
from dataclasses import dataclass
from ipaddress import ip_address
from math import ceil
from threading import Lock
from time import monotonic
from typing import Any


DEFAULT_MAX_REQUEST_BODY_BYTES = 8 * 1024 * 1024
MAX_TRACKED_RATE_LIMIT_KEYS = 10_000


def _positive_env_int(name: str, default: int, *, maximum: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not 1 <= value <= maximum:
        raise RuntimeError(f"{name} must be between 1 and {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class _RateRule:
    limit: int
    window_seconds: int


class _SlidingWindowLimiter:
    """Small, process-local abuse guard with bounded bookkeeping memory."""

    def __init__(self, *, max_keys: int = MAX_TRACKED_RATE_LIMIT_KEYS) -> None:
        self._max_keys = max_keys
        self._requests: OrderedDict[tuple[str, str], deque[float]] = OrderedDict()
        self._lock = Lock()

    def check(self, key: tuple[str, str], rule: _RateRule, now: float) -> int | None:
        cutoff = now - rule.window_seconds
        with self._lock:
            timestamps = self._requests.pop(key, deque())
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()
            if len(timestamps) >= rule.limit:
                self._requests[key] = timestamps
                return max(1, ceil(timestamps[0] + rule.window_seconds - now))
            timestamps.append(now)
            self._requests[key] = timestamps
            while len(self._requests) > self._max_keys:
                self._requests.popitem(last=False)
        return None


class SecurityBoundaryMiddleware:
    """Bound request cost and add browser security/privacy response headers."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.max_body_bytes = _positive_env_int(
            "MAX_REQUEST_BODY_BYTES",
            DEFAULT_MAX_REQUEST_BODY_BYTES,
            maximum=64 * 1024 * 1024,
        )
        window = _positive_env_int("RATE_LIMIT_WINDOW_SECONDS", 60, maximum=3600)
        self.auth_rule = _RateRule(
            _positive_env_int("AUTH_RATE_LIMIT_ATTEMPTS", 10, maximum=10_000),
            window,
        )
        self.ai_rule = _RateRule(
            _positive_env_int("AI_RATE_LIMIT_REQUESTS", 20, maximum=10_000),
            window,
        )
        self.scheduler_rule = _RateRule(
            _positive_env_int("SCHEDULER_RATE_LIMIT_REQUESTS", 20, maximum=10_000),
            window,
        )
        self.trust_proxy_headers = os.getenv("TRUST_PROXY_HEADERS", "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self._limiter = _SlidingWindowLimiter()

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        method = str(scope.get("method") or "GET").upper()
        rule = self._rate_rule(path, method)
        if rule is not None:
            retry_after = self._limiter.check((path, self._client_ip(scope)), rule, monotonic())
            if retry_after is not None:
                await self._json_error(scope, send, 429, "请求过于频繁，请稍后重试", retry_after=retry_after)
                return

        headers = {key.lower(): value for key, value in scope.get("headers", ())}
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                await self._json_error(scope, send, 400, "Content-Length 无效")
                return
            if declared_length < 0:
                await self._json_error(scope, send, 400, "Content-Length 无效")
                return
            if declared_length > self.max_body_bytes:
                await self._json_error(scope, send, 413, "请求体过大")
                return

        bounded_receive = receive
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            body = bytearray()
            while True:
                message = await receive()
                if message.get("type") == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > self.max_body_bytes:
                    await self._json_error(scope, send, 413, "请求体过大")
                    return
                if not message.get("more_body", False):
                    break
            delivered = False

            async def replay_receive() -> dict:
                nonlocal delivered
                if delivered:
                    return {"type": "http.disconnect"}
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}

            bounded_receive = replay_receive

        async def secure_send(message: dict) -> None:
            if message.get("type") == "http.response.start":
                self._add_security_headers(scope, message)
            await send(message)

        await self.app(scope, bounded_receive, secure_send)

    def _rate_rule(self, path: str, method: str) -> _RateRule | None:
        if method != "POST" and not (method == "DELETE" and path == "/api/patient/account"):
            return None
        if path in {
            "/api/auth/login",
            "/api/auth/register",
            "/api/patient/auth/login",
            "/api/patient/auth/register",
            "/api/patient/account",
        }:
            return self.auth_rule
        if path == "/api/patient/agent/chat":
            return self.ai_rule
        if path == "/api/patient/plans" or (
            path.startswith("/api/patient/plans/")
            and (path.endswith("/replan") or path.endswith("/resume"))
        ):
            return self.scheduler_rule
        return None

    def _client_ip(self, scope: dict) -> str:
        client = scope.get("client")
        direct = str(client[0]) if client else "unknown"
        if not self.trust_proxy_headers:
            return direct
        for key, value in scope.get("headers", ()):
            if key.lower() != b"x-forwarded-for":
                continue
            candidate = value.decode("latin-1").split(",", 1)[0].strip()
            try:
                return str(ip_address(candidate))
            except ValueError:
                return direct
        return direct

    async def _json_error(
        self,
        scope: dict,
        send: Any,
        status_code: int,
        detail: str,
        *,
        retry_after: int | None = None,
    ) -> None:
        body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
        message = {
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
        if retry_after is not None:
            message["headers"].append((b"retry-after", str(retry_after).encode("ascii")))
        self._add_security_headers(scope, message)
        await send(message)
        await send({"type": "http.response.body", "body": body})

    def _add_security_headers(self, scope: dict, message: dict) -> None:
        existing = {key.lower() for key, _value in message.get("headers", ())}
        additions = [
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", b"DENY"),
            (b"referrer-policy", b"no-referrer"),
            (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
        ]
        path = str(scope.get("path") or "")
        if path.startswith("/api/"):
            additions.append((b"cache-control", b"no-store"))
        if path == "/" or path.startswith("/assets/"):
            additions.append(
                (
                    b"content-security-policy",
                    b"default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; "
                    b"form-action 'self'; script-src 'self'; style-src 'self'; "
                    b"img-src 'self' data: https:; connect-src 'self'",
                )
            )
        if os.getenv("COOKIE_SECURE", "false").strip().lower() == "true":
            additions.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
        headers = message.setdefault("headers", [])
        headers.extend((key, value) for key, value in additions if key not in existing)
