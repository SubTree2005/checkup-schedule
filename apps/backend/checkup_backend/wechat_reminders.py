from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .hospital_time import hospital_timezone
from .models import ExamPlan, HospitalInfo, PackageInfo, WechatReminder, utcnow

MAX_WECHAT_RESPONSE_BYTES = 1024 * 1024


class WechatConfigurationError(RuntimeError):
    pass


class WechatAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class WechatReminderConfig:
    app_id: str
    app_secret: str
    template_id: str
    data_template: dict[str, Any]
    page: str
    miniprogram_state: str
    lang: str
    trust_cloudbase_identity: bool


_token_lock = threading.Lock()
_token_value = ""
_token_expires_at = datetime.min
_token_app_id = ""


def _env_bool(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def load_wechat_reminder_config(*, require_secret: bool = True) -> WechatReminderConfig:
    values = {
        "WECHAT_APP_ID": os.getenv("WECHAT_APP_ID", "").strip(),
        "WECHAT_APP_SECRET": os.getenv("WECHAT_APP_SECRET", "").strip(),
        "WECHAT_REMINDER_TEMPLATE_ID": os.getenv("WECHAT_REMINDER_TEMPLATE_ID", "").strip(),
        "WECHAT_REMINDER_DATA_TEMPLATE": os.getenv("WECHAT_REMINDER_DATA_TEMPLATE", "").strip(),
    }
    required = ["WECHAT_APP_ID", "WECHAT_REMINDER_TEMPLATE_ID", "WECHAT_REMINDER_DATA_TEMPLATE"]
    if require_secret:
        required.append("WECHAT_APP_SECRET")
    missing = [name for name in required if not values[name]]
    if missing:
        raise WechatConfigurationError(f"缺少配置：{', '.join(missing)}")
    try:
        data_template = json.loads(values["WECHAT_REMINDER_DATA_TEMPLATE"])
    except json.JSONDecodeError as exc:
        raise WechatConfigurationError("WECHAT_REMINDER_DATA_TEMPLATE 不是有效 JSON") from exc
    if not isinstance(data_template, dict) or not data_template:
        raise WechatConfigurationError("WECHAT_REMINDER_DATA_TEMPLATE 必须是非空 JSON 对象")
    state = os.getenv("WECHAT_MINIPROGRAM_STATE", "formal").strip() or "formal"
    if state not in {"developer", "trial", "formal"}:
        raise WechatConfigurationError("WECHAT_MINIPROGRAM_STATE 必须是 developer、trial 或 formal")
    return WechatReminderConfig(
        app_id=values["WECHAT_APP_ID"],
        app_secret=values["WECHAT_APP_SECRET"],
        template_id=values["WECHAT_REMINDER_TEMPLATE_ID"],
        data_template=data_template,
        page=os.getenv("WECHAT_REMINDER_PAGE", "pages/record/record").strip() or "pages/record/record",
        miniprogram_state=state,
        lang=os.getenv("WECHAT_REMINDER_LANG", "zh_CN").strip() or "zh_CN",
        trust_cloudbase_identity=_env_bool("WECHAT_TRUST_CLOUDBASE_IDENTITY"),
    )


def public_reminder_config() -> dict[str, Any]:
    try:
        config = load_wechat_reminder_config()
    except WechatConfigurationError as exc:
        return {"available": False, "templateIDs": [], "reason": str(exc)}
    if not config.trust_cloudbase_identity:
        return {
            "available": False,
            "templateIDs": [],
            "reason": "云托管身份透传尚未启用",
        }
    return {
        "available": True,
        "templateIDs": [config.template_id],
        "schedule": "预约前一天 20:00；临近预约时改为提前 1 小时",
    }


def trusted_cloudbase_identity(headers: Any, config: WechatReminderConfig) -> tuple[str, str]:
    if not config.trust_cloudbase_identity:
        raise WechatConfigurationError("云托管身份透传尚未启用")
    open_id = str(headers.get("x-wx-openid") or "").strip()
    app_id = str(headers.get("x-wx-appid") or headers.get("x-wx-from-appid") or "").strip()
    if not open_id or not app_id:
        raise WechatConfigurationError("当前请求不是带微信身份的云托管调用")
    if app_id != config.app_id:
        raise WechatConfigurationError("云托管请求的 AppID 与服务配置不一致")
    return open_id, app_id


def reminder_time(appointment_at: datetime, *, now: datetime | None = None) -> datetime:
    now_utc = now or utcnow()
    local_zone = hospital_timezone()
    local_appointment = appointment_at.replace(tzinfo=timezone.utc).astimezone(local_zone)
    candidate_local = datetime.combine(
        local_appointment.date() - timedelta(days=1), time(20, 0), tzinfo=local_zone
    )
    candidate = candidate_local.astimezone(timezone.utc).replace(tzinfo=None)
    if candidate <= now_utc:
        candidate = appointment_at - timedelta(hours=1)
    return max(candidate, now_utc)


class _FormatValues(dict):
    def __missing__(self, key: str) -> str:
        raise WechatConfigurationError(f"提醒模板引用了未知变量：{key}")


def _render_value(value: Any, values: dict[str, str]) -> Any:
    if isinstance(value, str):
        return value.format_map(_FormatValues(values))
    if isinstance(value, dict):
        return {key: _render_value(child, values) for key, child in value.items()}
    if isinstance(value, list):
        return [_render_value(child, values) for child in value]
    return value


def create_plan_reminder(
    db: Session,
    *,
    plan: ExamPlan,
    hospital: HospitalInfo,
    package: PackageInfo | None,
    appointment_at: datetime,
    template_id: str,
    headers: Any,
) -> WechatReminder:
    config = load_wechat_reminder_config()
    if template_id != config.template_id:
        raise WechatConfigurationError("订阅模板与服务端配置不一致")
    open_id, app_id = trusted_cloudbase_identity(headers, config)
    local_appointment = appointment_at.replace(tzinfo=timezone.utc).astimezone(hospital_timezone())
    values = {
        "hospital": hospital.hospital_name,
        "appointment": local_appointment.strftime("%Y年%m月%d日 %H:%M"),
        "package": package.package_name if package else "自选项目",
        "preparation": "请按预约要求完成体检前准备",
    }
    data = _render_value(config.data_template, values)
    scheduled_at = reminder_time(appointment_at)
    reminder = WechatReminder(
        plan_id=plan.plan_id,
        user_id=plan.user_id,
        open_id=open_id,
        app_id=app_id,
        template_id=config.template_id,
        scheduled_at=scheduled_at,
        next_attempt_at=scheduled_at,
        message_data=data,
        page=config.page,
    )
    db.add(reminder)
    return reminder


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            declared = response.headers.get("Content-Length")
            if declared:
                try:
                    declared_size = int(declared)
                except (TypeError, ValueError):
                    declared_size = None
                if declared_size is not None and declared_size > MAX_WECHAT_RESPONSE_BYTES:
                    raise WechatAPIError("微信接口响应过大")
            raw = response.read(MAX_WECHAT_RESPONSE_BYTES + 1)
            if len(raw) > MAX_WECHAT_RESPONSE_BYTES:
                raise WechatAPIError("微信接口响应过大")
            result = json.loads(raw.decode("utf-8"))
    except WechatAPIError:
        raise
    except (urllib.error.URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WechatAPIError(f"微信接口请求失败：{exc}") from exc
    if not isinstance(result, dict):
        raise WechatAPIError("微信接口返回了无效响应")
    return result


def get_access_token(config: WechatReminderConfig) -> str:
    global _token_app_id, _token_expires_at, _token_value
    now = utcnow()
    with _token_lock:
        if (
            _token_value
            and _token_app_id == config.app_id
            and _token_expires_at > now + timedelta(minutes=5)
        ):
            return _token_value
        result = _post_json(
            "https://api.weixin.qq.com/cgi-bin/stable_token",
            {
                "grant_type": "client_credential",
                "appid": config.app_id,
                "secret": config.app_secret,
                "force_refresh": False,
            },
        )
        token = str(result.get("access_token") or "")
        if not token:
            raise WechatAPIError(
                f"获取 access_token 失败（{result.get('errcode', 'unknown')}）：{result.get('errmsg', '未知错误')}"
            )
        _token_value = token
        _token_app_id = config.app_id
        _token_expires_at = now + timedelta(seconds=max(int(result.get("expires_in", 7200)), 600))
        return token


def send_subscription_message(reminder: WechatReminder) -> None:
    config = load_wechat_reminder_config()
    if reminder.app_id != config.app_id or reminder.template_id != config.template_id:
        raise WechatConfigurationError("提醒记录与当前微信配置不一致")
    token = get_access_token(config)
    url = "https://api.weixin.qq.com/cgi-bin/message/subscribe/send?" + urllib.parse.urlencode(
        {"access_token": token}
    )
    result = _post_json(
        url,
        {
            "touser": reminder.open_id,
            "template_id": reminder.template_id,
            "page": reminder.page,
            "miniprogram_state": config.miniprogram_state,
            "lang": config.lang,
            "data": reminder.message_data,
        },
    )
    if int(result.get("errcode", 0)) != 0:
        raise WechatAPIError(
            f"发送失败（{result.get('errcode', 'unknown')}）：{result.get('errmsg', '未知错误')}"
        )


def dispatch_due_reminders(db: Session, *, limit: int = 50, now: datetime | None = None) -> dict[str, int]:
    current = now or utcnow()
    retry_cutoff = current - timedelta(minutes=10)
    stuck = list(
        db.scalars(
            select(WechatReminder).where(
                WechatReminder.status == "processing",
                WechatReminder.last_attempt_at <= retry_cutoff,
                WechatReminder.attempt_count < 3,
            )
            .order_by(WechatReminder.last_attempt_at, WechatReminder.create_time)
            .limit(limit)
        )
    )
    for reminder in stuck:
        reminder.status = "failed"
        reminder.next_attempt_at = current
        reminder.last_error = "上次派发未完成，已自动重试"
    if stuck:
        db.commit()

    rows = list(
        db.scalars(
            select(WechatReminder)
            .where(
                WechatReminder.status.in_(("pending", "failed")),
                WechatReminder.next_attempt_at <= current,
                WechatReminder.attempt_count < 3,
            )
            .order_by(WechatReminder.next_attempt_at, WechatReminder.create_time)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    for reminder in rows:
        reminder.status = "processing"
        reminder.attempt_count += 1
        reminder.last_attempt_at = current
    db.commit()

    sent = failed = 0
    for reminder in rows:
        try:
            send_subscription_message(reminder)
            reminder.status = "sent"
            reminder.sent_at = utcnow()
            reminder.last_error = None
            sent += 1
        except (WechatAPIError, WechatConfigurationError) as exc:
            reminder.status = "failed"
            reminder.last_error = str(exc)[:1000]
            delays = (1, 5, 30)
            reminder.next_attempt_at = utcnow() + timedelta(minutes=delays[min(reminder.attempt_count - 1, 2)])
            failed += 1
        db.commit()
    return {"claimed": len(rows), "sent": sent, "failed": failed}
