from __future__ import annotations

import heapq
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from checkup_scheduler import (
    BatchPlannerConfig,
    DepartmentState,
    Exam,
    MedicalEligibilityRule,
    MedicalRuleContext,
    PatientState,
    TimeWindow,
    TravelTimeMatrix,
    build_batch_schedule,
)

from .api import client_ip, get_hospital_settings
from .database import get_db
from .exam_constraints import prerequisite_item_ids, validate_exam_selection
from .hospital_time import (
    daily_intersections_utc,
    hospital_timezone,
    next_daily_windows_utc,
    parse_open_time_ranges,
)
from .models import (
    AnomalyReport,
    DepartmentDistance,
    DepartmentInfo,
    ExamInfo,
    ExamPlan,
    HospitalAdmin,
    HospitalGIS,
    HospitalInfo,
    HospitalSettings,
    PackageInfo,
    PlanExecutionDetail,
    UserConsent,
    UserInfo,
    UserMobilityProfile,
    UserSession,
    UserStatusInfo,
    utcnow,
)
from .queue_state import ItemQueueState, system_department_queues
from .schemas import LoginRequest, PatientAccountDelete, PatientPlanCreate, PatientProfileUpdate, PatientRegister
from .security import (
    hash_password,
    issue_session,
    revoke_session,
    session_digest,
    verify_login_password,
    verify_password,
)
from .serializers import hospital_dict, iso
from .wechat_reminders import WechatConfigurationError, create_plan_reminder

router = APIRouter(prefix="/api/patient", tags=["patient-miniprogram"])
PATIENT_SESSION_COOKIE = "checkup_patient_session"
PRIVACY_POLICY_VERSION = "v0.3.1-2026-08-31"
HOSPITAL_CAMPUS_PATTERN = re.compile(r"^(.*?)[（(]([^）)]+)[）)]$")


@dataclass(frozen=True)
class PatientContext:
    user_id: str
    name: str
    phone: str


@dataclass(frozen=True)
class PatientPreparationRule:
    prerequisites_by_item: dict[str, dict]
    profile: dict
    reference_at: datetime | None = None
    allow_future_fasting: bool = False
    enforce_current_bladder: bool = True

    def rejection_for(self, item_id: str, *, proposed_start: datetime | None = None) -> str | None:
        prerequisites = self.prerequisites_by_item.get(item_id) or {}
        reasons: list[str] = []
        required_fasting = prerequisites.get("fastingHours", 0)
        if (
            isinstance(required_fasting, bool)
            or not isinstance(required_fasting, (int, float))
            or not math.isfinite(required_fasting)
            or required_fasting < 0
        ):
            return "检查项目的空腹要求配置无效"
        fasting_confirmed = self.profile.get("fasting") == "yes"
        future_preparation_hours = 0.0
        if self.allow_future_fasting and self.reference_at is not None and proposed_start is not None:
            future_preparation_hours = max(0.0, (proposed_start - self.reference_at).total_seconds() / 3600)
        if not fasting_confirmed and required_fasting > future_preparation_hours:
            reasons.append(f"未确认已满足 {required_fasting:g} 小时空腹要求")
        if self.enforce_current_bladder and (
            prerequisites.get("bladderReady")
            or prerequisites.get("bladderRequired")
            or prerequisites.get("fullBladder")
        ) and self.profile.get("bladder") != "normal":
            reasons.append("未确认已完成饮水憋尿准备")
        return "、".join(reasons) or None

    def evaluate(self, context: MedicalRuleContext) -> str | None:
        return self.rejection_for(context.exam.id, proposed_start=context.proposed_start)


def _current_preparation_rule(
    db: Session,
    user_id: str,
    exam_rows: list[tuple[ExamInfo, DepartmentInfo]],
) -> PatientPreparationRule:
    status_record = _latest_status(db, user_id)
    profile = dict(status_record.profile_data or {}) if status_record else {}
    if status_record:
        profile["fasting"] = "yes" if status_record.fasting_hours >= 8 else "no"
        profile["bladder"] = "normal" if status_record.is_bladder_ready else "recentUrination"
    return PatientPreparationRule(
        {exam.item_id: dict(exam.prerequisites or {}) for exam, _department in exam_rows},
        profile,
    )


def _bearer_token(authorization: str | None, cookie_token: str | None = None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return cookie_token


def get_current_patient(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    cookie_token: str | None = Cookie(default=None, alias=PATIENT_SESSION_COOKIE),
) -> PatientContext:
    token = _bearer_token(authorization, cookie_token)
    if not token:
        raise HTTPException(status_code=401, detail="请先登录")
    row = db.execute(
        select(UserSession, UserInfo)
        .join(UserInfo, UserInfo.user_id == UserSession.user_id)
        .where(UserSession.session_id == session_digest(token))
    ).one_or_none()
    if row is None or row.UserSession.expires_at <= utcnow():
        if row is not None:
            db.delete(row.UserSession)
            db.commit()
        raise HTTPException(status_code=401, detail="登录已过期")
    if row.UserInfo.role == "演示患者":
        db.delete(row.UserSession)
        db.commit()
        raise HTTPException(status_code=403, detail="演示患者账号不能登录患者端")
    is_admin = db.scalar(select(HospitalAdmin.user_id).where(HospitalAdmin.user_id == row.UserInfo.user_id))
    if is_admin or row.UserInfo.role != "普通用户":
        raise HTTPException(status_code=403, detail="医院管理员请使用 Web 管理端")
    return PatientContext(row.UserInfo.user_id, row.UserInfo.name, row.UserInfo.phone)


def _set_patient_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        PATIENT_SESSION_COOKIE,
        token,
        max_age=7 * 24 * 3600,
        httponly=True,
        secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        samesite="lax",
        path="/",
    )


def _age_from_birth_date(value: str | None) -> int | None:
    if not value:
        return None
    try:
        birth = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
    today = utcnow().date()
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def _birth_date_from_age(age: int | None) -> str | None:
    return f"{utcnow().year - age}-01-01" if age else None


def _latest_status(db: Session, user_id: str) -> UserStatusInfo | None:
    return db.scalar(
        select(UserStatusInfo)
        .where(UserStatusInfo.user_id == user_id)
        .order_by(UserStatusInfo.create_time.desc())
    )


def _profile_dict(db: Session, user: UserInfo) -> dict:
    current = _latest_status(db, user.user_id)
    health = {
        "fasting": "yes" if current and current.fasting_hours >= 8 else "no",
        "bladder": "normal" if current and current.is_bladder_ready else "recentUrination",
        "drinkingWater": "adequate",
        "specialNeed": "none",
        "booked": "yes",
        "medicalHistory": "-",
        "allergens": "-",
    }
    if current:
        health.update(current.profile_data or {})
    return {
        "userID": user.user_id,
        "name": user.name,
        "phone": user.phone,
        "gender": user.gender or "",
        "age": _age_from_birth_date(user.birth_date),
        "avatarUrl": user.avatar_url,
        "role": "user",
        "profile": health,
    }


@router.post("/auth/register", status_code=status.HTTP_201_CREATED)
def register_patient(
    payload: PatientRegister,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    if db.scalar(select(UserInfo.user_id).where(UserInfo.phone == payload.phone)):
        raise HTTPException(status_code=409, detail="该手机号已注册")
    user = UserInfo(
        phone=payload.phone,
        password=hash_password(payload.password),
        name=payload.name,
        gender=payload.gender,
        birth_date=_birth_date_from_age(payload.age),
        role="普通用户",
    )
    db.add(user)
    try:
        db.flush()
        db.add(
            UserConsent(
                user_id=user.user_id,
                policy_version=PRIVACY_POLICY_VERSION,
                accepted_ip=client_ip(request),
            )
        )
        db.add(
            UserStatusInfo(
                user_id=user.user_id,
                profile_data={
                    "medicalHistory": payload.medicalHistory.strip() or "-",
                    "allergens": payload.allergens.strip() or "-",
                },
            )
        )
        token = issue_session(db, user.user_id, client_ip(request))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="该手机号已注册") from exc
    _set_patient_cookie(response, token)
    return {"token": token, "user": _profile_dict(db, user)}


@router.post("/auth/login")
def login_patient(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    user = db.scalar(select(UserInfo).where(UserInfo.phone == payload.phone))
    is_admin = user and db.scalar(select(HospitalAdmin.user_id).where(HospitalAdmin.user_id == user.user_id))
    eligible_password = user.password if user is not None and not is_admin and user.role == "普通用户" else None
    if not verify_login_password(payload.password, eligible_password):
        raise HTTPException(status_code=401, detail="手机号或密码错误")
    token = issue_session(db, user.user_id, client_ip(request))
    db.commit()
    _set_patient_cookie(response, token)
    return {"token": token, "user": _profile_dict(db, user)}


@router.get("/auth/me")
def patient_me(patient: PatientContext = Depends(get_current_patient), db: Session = Depends(get_db)) -> dict:
    return _profile_dict(db, db.get(UserInfo, patient.user_id))


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout_patient(
    response: Response,
    authorization: str | None = Header(default=None),
    cookie_token: str | None = Cookie(default=None, alias=PATIENT_SESSION_COOKIE),
    db: Session = Depends(get_db),
) -> Response:
    revoke_session(db, _bearer_token(authorization, cookie_token))
    db.commit()
    response.delete_cookie(PATIENT_SESSION_COOKIE, path="/")
    response.status_code = 204
    return response


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
def delete_patient_account(
    payload: PatientAccountDelete,
    response: Response,
    patient: PatientContext = Depends(get_current_patient),
    db: Session = Depends(get_db),
) -> Response:
    user = db.get(UserInfo, patient.user_id)
    if user is None or not verify_password(payload.password, user.password):
        raise HTTPException(status_code=401, detail="密码错误，无法注销账号")

    plan_ids = list(db.scalars(select(ExamPlan.plan_id).where(ExamPlan.user_id == patient.user_id)))
    if plan_ids:
        db.execute(delete(PlanExecutionDetail).where(PlanExecutionDetail.plan_id.in_(plan_ids)))
        db.execute(delete(ExamPlan).where(ExamPlan.plan_id.in_(plan_ids)))
    db.execute(delete(AnomalyReport).where(AnomalyReport.reporter_id == patient.user_id))
    db.execute(delete(UserMobilityProfile).where(UserMobilityProfile.user_id == patient.user_id))
    db.execute(delete(UserStatusInfo).where(UserStatusInfo.user_id == patient.user_id))
    db.execute(delete(UserSession).where(UserSession.user_id == patient.user_id))
    db.execute(delete(UserConsent).where(UserConsent.user_id == patient.user_id))
    db.delete(user)
    db.commit()
    response.delete_cookie(PATIENT_SESSION_COOKIE, path="/")
    response.status_code = 204
    return response


@router.patch("/profile")
def update_profile(
    payload: PatientProfileUpdate,
    patient: PatientContext = Depends(get_current_patient),
    db: Session = Depends(get_db),
) -> dict:
    user = db.get(UserInfo, patient.user_id)
    data = payload.model_dump(exclude_unset=True)
    if data.get("name", "") is None or data.get("phone", "") is None:
        raise HTTPException(status_code=422, detail="姓名和手机号不能设为空值")
    field_mapping = {"name": "name", "gender": "gender", "phone": "phone", "avatarUrl": "avatar_url"}
    for field, attribute in field_mapping.items():
        if field in data:
            setattr(user, attribute, data[field])
    if "age" in data:
        user.birth_date = _birth_date_from_age(data["age"])
    health_fields = {
        "fasting", "bladder", "drinkingWater", "specialNeed", "booked", "medicalHistory", "allergens"
    }
    if health_fields.intersection(data):
        previous = _latest_status(db, patient.user_id)
        db.add(
            UserStatusInfo(
                user_id=patient.user_id,
                fasting_hours=(8 if data.get("fasting", "yes" if previous and previous.fasting_hours >= 8 else "no") == "yes" else 0),
                is_bladder_ready=(data.get("bladder", "normal" if previous and previous.is_bladder_ready else "recentUrination") == "normal"),
                blood_pressure=previous.blood_pressure if previous else None,
                profile_data={
                    **((previous.profile_data or {}) if previous else {}),
                    **{key: data[key] for key in health_fields if key in data},
                },
            )
        )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="个人资料更新冲突") from exc
    return _profile_dict(db, user)


def _hospital_name_parts(value: str) -> tuple[str, str]:
    match = HOSPITAL_CAMPUS_PATTERN.fullmatch(value.strip())
    return (match.group(1).strip(), match.group(2).strip()) if match else (value.strip(), "本院区")


def _parse_appointment(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _appointment_counts(db: Session, hospital_id: str) -> dict[tuple[str, str], int]:
    appointments = db.scalars(
        select(ExamPlan.appointment_at)
        .where(
            ExamPlan.hospital_id == hospital_id,
            ExamPlan.plan_status.notin_(["已完成", "已结束"]),
            ExamPlan.appointment_at.is_not(None),
        )
    ).all()
    counts: dict[tuple[str, str], int] = {}
    timezone_local = hospital_timezone()
    for appointment in appointments:
        local = appointment.replace(tzinfo=timezone.utc).astimezone(timezone_local)
        key = (local.strftime("%Y-%m-%d"), local.strftime("%H:%M"))
        counts[key] = counts.get(key, 0) + 1
    return counts


def _appointment_dates(
    db: Session,
    hospital: HospitalInfo,
    settings: HospitalSettings | None,
) -> list[dict]:
    slot_minutes = settings.appointment_slot_minutes if settings else 30
    slot_capacity = settings.appointment_slot_capacity if settings else 20
    days_ahead = settings.appointment_days_ahead if settings else 7
    is_available = settings.is_available if settings else True
    try:
        ranges = parse_open_time_ranges(hospital.open_time)
    except ValueError:
        ranges = (("08:00", "17:00"),)
    timezone_local = hospital_timezone()
    now_local = utcnow().replace(tzinfo=timezone.utc).astimezone(timezone_local)
    counts = _appointment_counts(db, hospital.hospital_id)
    dates = []
    weekdays_only = "工作日" in (hospital.open_time or "")
    for offset in range(days_ahead):
        local_day = now_local.date() + timedelta(days=offset)
        if weekdays_only and local_day.weekday() >= 5:
            continue
        slots = []
        for start_text, end_text in ranges:
            start_local = datetime.combine(local_day, time.fromisoformat(start_text), tzinfo=timezone_local)
            end_local = datetime.combine(local_day, time.fromisoformat(end_text), tzinfo=timezone_local)
            if end_local <= start_local:
                end_local += timedelta(days=1)
            cursor = start_local
            while cursor + timedelta(minutes=slot_minutes) <= end_local:
                slot_end = cursor + timedelta(minutes=slot_minutes)
                if cursor > now_local:
                    date_key = cursor.strftime("%Y-%m-%d")
                    start_key = cursor.strftime("%H:%M")
                    booked = counts.get((date_key, start_key), 0)
                    slots.append(
                        {
                            "key": start_key,
                            "start": start_key,
                            "end": slot_end.strftime("%H:%M"),
                            "appointmentAt": iso(cursor.astimezone(timezone.utc).replace(tzinfo=None)),
                            "booked": booked,
                            "capacity": slot_capacity,
                            "available": is_available and booked < slot_capacity,
                        }
                    )
                cursor = slot_end
        dates.append(
            {
                "key": local_day.isoformat(),
                "date": local_day.isoformat(),
                "available": any(slot["available"] for slot in slots),
                "availabilityLabel": "尚有余号" if any(slot["available"] for slot in slots) else "号源已满",
                "slots": slots,
            }
        )
    return dates


def _require_available_appointment(
    db: Session,
    hospital: HospitalInfo,
    settings: HospitalSettings | None,
    appointment_at: datetime,
) -> None:
    matched = None
    for date_row in _appointment_dates(db, hospital, settings):
        for slot in date_row["slots"]:
            candidate = _parse_appointment(slot["appointmentAt"])
            if candidate and candidate.replace(tzinfo=None) == appointment_at:
                matched = slot
                break
        if matched:
            break
    if matched is None:
        raise HTTPException(status_code=422, detail="预约时间不在医院开放号源内")
    if not matched["available"]:
        raise HTTPException(status_code=409, detail="该时间段号源已满，请选择其他时间")


@router.get("/hospitals")
def list_patient_hospitals(
    _patient: PatientContext = Depends(get_current_patient),
    db: Session = Depends(get_db),
) -> list[dict]:
    hospitals = db.scalars(select(HospitalInfo).order_by(HospitalInfo.hospital_name)).all()
    settings_by_hospital = {
        settings.hospital_id: settings
        for settings in db.scalars(
            select(HospitalSettings).where(
                HospitalSettings.hospital_id.in_([hospital.hospital_id for hospital in hospitals])
            )
        ).all()
    } if hospitals else {}
    grouped: dict[str, dict] = {}
    for row in hospitals:
        settings = settings_by_hospital.get(row.hospital_id)
        institution_name, campus_name = _hospital_name_parts(row.hospital_name)
        available = settings.is_available if settings else True
        details = hospital_dict(row, settings)
        group = grouped.setdefault(
            institution_name,
            {
                **details,
                "id": row.hospital_id,
                "name": institution_name,
                "hospitalName": institution_name,
                "campuses": [],
            },
        )
        if not group.get("coverImageUrl") and details.get("coverImageUrl"):
            group["coverImageUrl"] = details["coverImageUrl"]
            group["coverUrl"] = details["coverImageUrl"]
        if group.get("hospitalLevel") == "未定级" and details["hospitalLevel"] != "未定级":
            group["hospitalLevel"] = details["hospitalLevel"]
        if group.get("positioning") == "综合医疗机构" and details["positioning"] != "综合医疗机构":
            group["positioning"] = details["positioning"]
        group["campuses"].append(
            {
                "id": row.hospital_id,
                "hospitalID": row.hospital_id,
                "name": campus_name,
                "fullName": row.hospital_name,
                "address": row.address,
                "openTime": row.open_time,
                "available": available,
                "hospitalLevel": details["hospitalLevel"],
            }
        )
    for group in grouped.values():
        group["isAvailable"] = any(campus["available"] for campus in group["campuses"])
        group["status"] = "正常开放" if group["isAvailable"] else "暂停开放"
    return list(grouped.values())


@router.get("/hospitals/{hospital_id}/appointment-slots")
def patient_appointment_slots(
    hospital_id: str,
    _patient: PatientContext = Depends(get_current_patient),
    db: Session = Depends(get_db),
) -> dict:
    hospital = db.get(HospitalInfo, hospital_id)
    if hospital is None:
        raise HTTPException(status_code=404, detail="医院不存在")
    settings = get_hospital_settings(db, hospital_id)
    return {
        "hospital": hospital_dict(hospital, settings),
        "dates": _appointment_dates(db, hospital, settings),
    }


def _exam_catalog_dict(exam: ExamInfo, department: DepartmentInfo) -> dict:
    prerequisites = exam.prerequisites or {}
    return {
        "id": exam.item_id,
        "itemID": exam.item_id,
        "name": exam.item_name,
        "itemName": exam.item_name,
        "department": department.dept_name,
        "deptID": department.dept_id,
        "location": department.location,
        "duration": exam.duration,
        "fastingRequired": bool(prerequisites.get("fastingHours", 0)),
        "bladderRequired": bool(
            prerequisites.get("bladderReady", False)
            or prerequisites.get("bladderRequired", False)
            or prerequisites.get("fullBladder", False)
        ),
        "isCritical": exam.is_critical,
    }


def _hospital_exam_rows(db: Session, hospital_id: str) -> list[tuple[ExamInfo, DepartmentInfo]]:
    return db.execute(
        select(ExamInfo, DepartmentInfo)
        .join(DepartmentInfo, DepartmentInfo.dept_id == ExamInfo.dept_id)
        .where(DepartmentInfo.hospital_id == hospital_id, ExamInfo.is_active.is_(True))
        .order_by(DepartmentInfo.dept_name, ExamInfo.item_name)
    ).all()


def _package_catalog_dict(package: PackageInfo, exam_rows: list[tuple[ExamInfo, DepartmentInfo]]) -> dict:
    included = set(package.included_item_ids or [])
    items = [_exam_catalog_dict(exam, department) for exam, department in exam_rows if exam.item_id in included]
    groups: dict[str, list[dict]] = {}
    for item in items:
        groups.setdefault(item["department"], []).append(item)
    return {
        "id": package.package_id,
        "packageID": package.package_id,
        "name": package.package_name,
        "packageName": package.package_name,
        "type": package.package_type,
        "price": package.price,
        "tag": package.tag or f"{len(items)} 项检查",
        "description": package.description,
        "position": package.description,
        "suitable": package.suitable or [],
        "notice": package.notice or ["推荐路线会结合科室开放时间、排队情况和步行距离动态生成。"],
        "checkIds": [item["id"] for item in items],
        "items": items,
        "groups": [
            {"name": name, "countText": f"{len(group_items)}项", "items": group_items}
            for name, group_items in groups.items()
        ],
    }


@router.get("/hospitals/{hospital_id}/catalog")
def patient_catalog(
    hospital_id: str,
    _patient: PatientContext = Depends(get_current_patient),
    db: Session = Depends(get_db),
) -> dict:
    hospital = db.get(HospitalInfo, hospital_id)
    if hospital is None:
        raise HTTPException(status_code=404, detail="医院不存在")
    settings = get_hospital_settings(db, hospital_id)
    exam_rows = _hospital_exam_rows(db, hospital_id)
    active_item_ids = {exam.item_id for exam, _department in exam_rows}
    packages = [
        _package_catalog_dict(package, exam_rows)
        for package in db.scalars(
            select(PackageInfo)
            .where(PackageInfo.hospital_id == hospital_id, PackageInfo.is_published.is_(True))
            .order_by(PackageInfo.package_name)
        ).all()
        if set(package.included_item_ids or [])
        and set(package.included_item_ids or []).issubset(active_item_ids)
    ]
    exams = [_exam_catalog_dict(exam, department) for exam, department in exam_rows]
    departments: dict[str, list[dict]] = {}
    for exam in exams:
        departments.setdefault(exam["department"], []).append(exam)
    return {
        "hospital": hospital_dict(hospital, settings),
        "packages": packages,
        "exams": exams,
        "departments": [{"name": name, "projects": items} for name, items in departments.items()],
    }


def _parse_open_windows(hospital: HospitalInfo, now: datetime) -> tuple[TimeWindow, ...]:
    try:
        ranges = parse_open_time_ranges(hospital.open_time or "")
    except ValueError:
        ranges = (("08:00", "17:00"),)
    return tuple(
        TimeWindow(start, end)
        for start, end in next_daily_windows_utc(
            now,
            ranges,
            weekdays_only="工作日" in (hospital.open_time or ""),
        )
    )


def _allowed_windows(exam: ExamInfo, day_start: datetime, day_end: datetime) -> tuple[TimeWindow, ...]:
    data = exam.allowed_time_slots or {}
    start_text, end_text = data.get("start"), data.get("end")
    if not start_text or not end_text:
        return ()
    try:
        intersections = daily_intersections_utc(day_start, day_end, start_text, end_text)
    except ValueError:
        return ()
    if intersections:
        return tuple(TimeWindow(start, end) for start, end in intersections)
    return (TimeWindow(day_end, day_end + timedelta(minutes=1)),)


def _department_windows(
    department: DepartmentInfo,
    planning_start: datetime,
    planning_end: datetime,
) -> tuple[TimeWindow, ...]:
    try:
        intersections = daily_intersections_utc(
            planning_start,
            planning_end,
            department.open_time_start,
            department.open_time_end,
        )
    except ValueError:
        return ()
    return tuple(TimeWindow(start, end) for start, end in intersections)


def _latest_department_waits(db: Session, hospital_id: str, now: datetime) -> dict[str, int]:
    return {
        department_id: math.ceil(state.estimated_wait_time / 60)
        for department_id, state in system_department_queues(db, hospital_id, now=now).items()
    }


def _travel_matrix(db: Session, hospital_id: str, walk_speed: float) -> TravelTimeMatrix:
    rows = db.execute(
        select(DepartmentDistance)
        .join(DepartmentInfo, DepartmentInfo.dept_id == DepartmentDistance.from_dept_id)
        .where(DepartmentInfo.hospital_id == hospital_id)
    ).scalars().all()
    speed = max(walk_speed, 0.2)
    return TravelTimeMatrix(
        {(row.from_dept_id, row.to_dept_id): max(1, math.ceil(row.distance_meters / speed / 60)) for row in rows},
        default_minutes=3,
    )


def _run_scheduler(
    db: Session,
    user: UserInfo,
    hospital: HospitalInfo,
    exam_rows: list[tuple[ExamInfo, DepartmentInfo]],
    previous_order: tuple[str, ...] = (),
    satisfied_item_ids: set[str] | None = None,
    available_at: datetime | None = None,
    location_id: str = "entrance",
    medical_rules: tuple[MedicalEligibilityRule, ...] = (),
):
    now = utcnow()
    hospital_windows = _parse_open_windows(hospital, max(now, available_at or now))
    planning_start, planning_end = hospital_windows[0].start, hospital_windows[-1].end
    selected_ids = {exam.item_id for exam, _department in exam_rows}
    satisfied_ids = satisfied_item_ids or set()
    try:
        validate_exam_selection(
            selected_ids,
            {exam.item_id: prerequisite_item_ids(exam.prerequisites) for exam, _department in exam_rows},
            {exam.item_id: exam.conflicts or [] for exam, _department in exam_rows},
            satisfied_item_ids=satisfied_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"检查项目组合无效：{exc}") from exc
    waits = _latest_department_waits(db, hospital.hospital_id, now)
    departments: dict[str, DepartmentState] = {}
    for _exam, department in exam_rows:
        service_windows = _department_windows(department, planning_start, planning_end)
        departments[department.dept_id] = DepartmentState(
            id=department.dept_id,
            observed_at=planning_start,
            expected_wait_minutes=waits.get(department.dept_id, 0),
            accepting_patients=department.is_available and bool(service_windows),
            service_windows=service_windows,
            capacity=department.capacity,
        )
    exams = tuple(
        Exam(
            id=exam.item_id,
            department_id=exam.dept_id,
            duration_minutes=exam.duration,
            prerequisites=tuple(
                item_id for item_id in prerequisite_item_ids(exam.prerequisites) if item_id in selected_ids
            ),
            delay_cost_per_minute=float(exam.priority),
            allowed_windows=_allowed_windows(exam, planning_start, planning_end),
            is_critical=exam.is_critical,
        )
        for exam, _department in exam_rows
    )
    patient = PatientState(
        patient_id=user.user_id,
        exams=exams,
        now=planning_start,
        location_id=location_id,
        previous_order=previous_order,
        availability_windows=hospital_windows,
        age_years=_age_from_birth_date(user.birth_date),
        gender=user.gender,
    )
    return build_batch_schedule(
        (patient,),
        departments,
        _travel_matrix(db, hospital.hospital_id, user.walk_speed),
        TimeWindow(planning_start, planning_end),
        config=BatchPlannerConfig(wait_oriented=True),
        medical_rules=medical_rules,
    )


def _require_owned_plan(
    db: Session,
    user_id: str,
    plan_id: str,
    *,
    for_update: bool = False,
) -> ExamPlan:
    query = select(ExamPlan).where(ExamPlan.plan_id == plan_id, ExamPlan.user_id == user_id)
    if for_update:
        query = query.with_for_update()
    plan = db.scalar(query)
    if plan is None:
        raise HTTPException(status_code=404, detail="体检计划不存在")
    return plan


def _queue_by_item(
    db: Session,
    item_ids: list[str],
    detail_rows: list[tuple[PlanExecutionDetail, ExamInfo, DepartmentInfo]] | None = None,
) -> dict[str, ItemQueueState]:
    if not item_ids:
        return {}
    requested_ids = set(item_ids)
    metadata = {
        exam.item_id: (exam, department)
        for _detail, exam, department in (detail_rows or [])
        if exam.item_id in requested_ids
    }
    missing_ids = requested_ids - metadata.keys()
    if missing_ids:
        missing_rows = db.execute(
            select(ExamInfo, DepartmentInfo)
            .join(DepartmentInfo, DepartmentInfo.dept_id == ExamInfo.dept_id)
            .where(ExamInfo.item_id.in_(missing_ids))
        ).all()
        metadata.update({exam.item_id: (exam, department) for exam, department in missing_rows})
    hospital_ids = {department.hospital_id for _exam, department in metadata.values()}
    department_states = {
        hospital_id: system_department_queues(db, hospital_id, now=utcnow())
        for hospital_id in hospital_ids
    }
    result: dict[str, ItemQueueState] = {}
    for item_id, (exam, department) in metadata.items():
        state = department_states[department.hospital_id].get(department.dept_id)
        result[item_id] = ItemQueueState(
            item_id=item_id,
            item_name=exam.item_name,
            department_id=department.dept_id,
            queue_count=state.waiting_count if state else 0,
            active_count=state.active_count if state else 0,
            estimated_wait_time=state.estimated_wait_time if state else 0,
        )
    return result


def _serialize_plan_payload(
    plan: ExamPlan,
    rows: list[tuple[PlanExecutionDetail, ExamInfo, DepartmentInfo]],
    package: PackageInfo | None,
    status_record: UserStatusInfo | None,
    queues: dict[str, ItemQueueState],
    hospital: HospitalInfo | None,
    hospital_settings: HospitalSettings | None,
    *,
    replanned: bool = False,
) -> dict:
    profile_snapshot = dict(status_record.profile_data or {}) if status_record else {}
    if status_record:
        profile_snapshot.setdefault("fasting", "yes" if status_record.fasting_hours >= 8 else "no")
        profile_snapshot.setdefault("bladder", "normal" if status_record.is_bladder_ready else "recentUrination")
    completed_at = max((detail.actual_end for detail, _exam, _department in rows if detail.actual_end), default=plan.generate_time)
    completed = sum(detail.exec_status == "已完成" for detail, _exam, _department in rows)
    first_open = next((index for index, (detail, _exam, _department) in enumerate(rows) if detail.exec_status != "已完成"), len(rows))
    steps = []
    for row_index, (detail, exam, department) in enumerate(rows):
        queue = queues.get(exam.item_id)
        queue_wait = math.ceil(queue.estimated_wait_time / 60) if queue else 0
        queue_ahead = queue.queue_count if queue else 0
        if detail.exec_status == "进行中":
            queue_wait = 0
            queue_ahead = 0
        elif plan.plan_status == "进行中" and row_index == first_open and queue:
            queue_wait = max(0, queue_wait - math.ceil(exam.duration / max(1, department.capacity)))
            queue_ahead = max(0, queue_ahead - 1)
        prerequisites = exam.prerequisites or {}
        state = (
            "done"
            if detail.exec_status == "已完成"
            else "active"
            if detail.exec_status == "进行中"
            else "skipped"
            if detail.exec_status in {"已结束", "已取消"}
            else "pending"
        )
        steps.append(
            {
                "detailID": detail.detail_id,
                "itemID": exam.item_id,
                "step": detail.step_order,
                "title": exam.item_name,
                "department": department.dept_name,
                "items": [{"id": exam.item_id, "name": exam.item_name}],
                "duration": exam.duration,
                "queueWait": queue_wait,
                "queueAhead": queue_ahead,
                "queuePosition": f"前方约 {queue_ahead} 人" if queue_ahead else "当前无需排队",
                "totalDuration": exam.duration + queue_wait,
                "fasting": bool(prerequisites.get("fastingHours", 0)),
                "bladderRequired": bool(
                    prerequisites.get("bladderReady", False)
                    or prerequisites.get("bladderRequired", False)
                    or prerequisites.get("fullBladder", False)
                ),
                "note": "请遵循现场医护人员指引。",
                "status": state,
                "started": state in {"active", "done"},
                "completed": state == "done",
                "estimatedStart": iso(detail.estimated_start),
                "estimatedEnd": iso(detail.estimated_end),
                "navigationTarget": {"department": department.dept_name, "locationText": department.location or "请查看院内指引"},
            }
        )
    return {
        "id": plan.plan_id,
        "planID": plan.plan_id,
        "packageId": package.package_id if package else None,
        "packageName": package.package_name if package else "自选项目",
        "packagePrice": package.price if package else 0,
        "hospitalName": hospital.hospital_name if hospital else "",
        "hospitalCoverUrl": hospital_settings.cover_image_url if hospital_settings else None,
        "date": profile_snapshot.get("appointmentDateLabel") or plan.generate_time.strftime("%Y年%m月%d日"),
        "appointmentAt": profile_snapshot.get("appointmentAt") or iso(plan.appointment_at),
        "completedAt": iso(completed_at),
        "generatedAt": iso(plan.generate_time),
        "totalDuration": plan.total_duration,
        "remainingDuration": sum(step["totalDuration"] for step in steps if step["status"] != "done"),
        "totalSteps": len(steps),
        "completedSteps": completed,
        "currentStepIndex": first_open,
        "progress": round(completed / len(steps) * 100) if steps else 0,
        "planStatus": plan.plan_status,
        "status": plan.plan_status,
        "finished": plan.plan_status in {"已完成", "已结束"},
        "ended": plan.plan_status == "已结束",
        "profileSnapshot": profile_snapshot,
        "waitingHint": "路线由服务端 Scheduler 根据时间窗、排队与步行距离生成。",
        "replanNotice": "已按最新排队情况重新安排后续路线。" if replanned else "",
        "steps": steps,
    }


def _serialize_plan(db: Session, plan: ExamPlan, *, replanned: bool = False) -> dict:
    rows = db.execute(
        select(PlanExecutionDetail, ExamInfo, DepartmentInfo)
        .join(ExamInfo, ExamInfo.item_id == PlanExecutionDetail.item_id)
        .join(DepartmentInfo, DepartmentInfo.dept_id == ExamInfo.dept_id)
        .where(PlanExecutionDetail.plan_id == plan.plan_id)
        .order_by(PlanExecutionDetail.step_order)
    ).all()
    package = db.get(PackageInfo, plan.package_id) if plan.package_id else None
    status_record = db.get(UserStatusInfo, plan.record_id) if plan.record_id else None
    queues = _queue_by_item(db, list(plan.selected_item_ids or []), rows)
    hospital = db.get(HospitalInfo, plan.hospital_id)
    hospital_settings = get_hospital_settings(db, plan.hospital_id) if hospital else None
    return _serialize_plan_payload(
        plan,
        rows,
        package,
        status_record,
        queues,
        hospital,
        hospital_settings,
        replanned=replanned,
    )


def _serialize_plans(db: Session, plans: list[ExamPlan]) -> list[dict]:
    if not plans:
        return []
    plan_ids = [plan.plan_id for plan in plans]
    rows_by_plan: dict[str, list[tuple[PlanExecutionDetail, ExamInfo, DepartmentInfo]]] = {}
    detail_rows = db.execute(
        select(PlanExecutionDetail, ExamInfo, DepartmentInfo)
        .join(ExamInfo, ExamInfo.item_id == PlanExecutionDetail.item_id)
        .join(DepartmentInfo, DepartmentInfo.dept_id == ExamInfo.dept_id)
        .where(PlanExecutionDetail.plan_id.in_(plan_ids))
        .order_by(PlanExecutionDetail.plan_id, PlanExecutionDetail.step_order)
    ).all()
    for row in detail_rows:
        rows_by_plan.setdefault(row[0].plan_id, []).append(row)

    package_ids = {plan.package_id for plan in plans if plan.package_id}
    packages = {
        package.package_id: package
        for package in db.scalars(select(PackageInfo).where(PackageInfo.package_id.in_(package_ids))).all()
    } if package_ids else {}
    record_ids = {plan.record_id for plan in plans if plan.record_id}
    status_records = {
        record.record_id: record
        for record in db.scalars(select(UserStatusInfo).where(UserStatusInfo.record_id.in_(record_ids))).all()
    } if record_ids else {}
    hospital_ids = {plan.hospital_id for plan in plans}
    hospitals = {
        hospital.hospital_id: hospital
        for hospital in db.scalars(select(HospitalInfo).where(HospitalInfo.hospital_id.in_(hospital_ids))).all()
    }
    hospital_settings = {
        settings.hospital_id: settings
        for settings in db.scalars(select(HospitalSettings).where(HospitalSettings.hospital_id.in_(hospital_ids))).all()
    }
    item_ids = list({item_id for plan in plans for item_id in (plan.selected_item_ids or [])})
    queues = _queue_by_item(db, item_ids, detail_rows)
    return [
        _serialize_plan_payload(
            plan,
            rows_by_plan.get(plan.plan_id, []),
            packages.get(plan.package_id),
            status_records.get(plan.record_id),
            queues,
            hospitals.get(plan.hospital_id),
            hospital_settings.get(plan.hospital_id),
        )
        for plan in plans
    ]


@router.post("/plans", status_code=status.HTTP_201_CREATED)
def create_patient_plan(
    payload: PatientPlanCreate,
    request: Request,
    patient: PatientContext = Depends(get_current_patient),
    db: Session = Depends(get_db),
) -> dict:
    requested_at = utcnow()
    hospital = db.get(HospitalInfo, payload.hospitalID)
    if hospital is None:
        raise HTTPException(status_code=404, detail="医院不存在")
    settings = get_hospital_settings(
        db,
        hospital.hospital_id,
        create=payload.appointmentAt is not None,
    )
    if settings and not settings.is_available:
        raise HTTPException(status_code=409, detail="该院区当前暂停开放")
    appointment_at = payload.appointmentAt
    if appointment_at and appointment_at.tzinfo is not None:
        appointment_at = appointment_at.astimezone(timezone.utc).replace(tzinfo=None)
    if appointment_at and appointment_at <= requested_at:
        raise HTTPException(status_code=422, detail="预约时间必须晚于当前时间")
    if appointment_at:
        # Serialize bookings per hospital on MySQL so two simultaneous requests
        # cannot both consume the last remaining place after reading the same count.
        db.flush()
        settings = db.scalar(
            select(HospitalSettings)
            .where(HospitalSettings.hospital_id == hospital.hospital_id)
            .with_for_update()
        )
        _require_available_appointment(db, hospital, settings, appointment_at)
    user_query = select(UserInfo).where(UserInfo.user_id == patient.user_id)
    if appointment_at is None:
        # MySQL row locking serializes the active-plan uniqueness check for this patient.
        user_query = user_query.with_for_update()
    user = db.scalar(user_query)
    if appointment_at is None and db.scalar(
        select(ExamPlan.plan_id).where(
            ExamPlan.user_id == patient.user_id,
            ExamPlan.plan_status == "进行中",
        )
    ):
        raise HTTPException(status_code=409, detail="已有进行中的体检计划")
    package = db.scalar(
        select(PackageInfo).where(
            PackageInfo.package_id == payload.packageID,
            PackageInfo.hospital_id == payload.hospitalID,
            PackageInfo.is_published.is_(True),
        )
    ) if payload.packageID else None
    if payload.packageID and package is None:
        raise HTTPException(status_code=404, detail="体检套餐不存在或已下架")
    item_ids = list(dict.fromkeys(payload.selectedItemIDs or (package.included_item_ids if package else [])))
    rows = _hospital_exam_rows(db, hospital.hospital_id)
    owned = {exam.item_id: (exam, department) for exam, department in rows}
    if not item_ids or any(item_id not in owned for item_id in item_ids):
        raise HTTPException(status_code=422, detail="检查项目不属于所选医院或已停用")
    selected_rows = [owned[item_id] for item_id in item_ids]
    previous_status = _latest_status(db, user.user_id)
    profile_updates = dict(payload.profile or {})
    if appointment_at:
        profile_updates["appointmentAt"] = iso(appointment_at)
    profile = {
        **((previous_status.profile_data or {}) if previous_status else {}),
        **profile_updates,
    }
    preparation_rule = PatientPreparationRule(
        {exam.item_id: dict(exam.prerequisites or {}) for exam, _department in selected_rows},
        profile,
        reference_at=requested_at,
        allow_future_fasting=appointment_at is not None,
        # The product does not define a bladder-preparation duration. Future appointments
        # carry a reminder instead of inventing a medical lead-time requirement here.
        enforce_current_bladder=appointment_at is None,
    )
    unmet = [
        f"{exam.item_name}：{reason}"
        for exam, _department in selected_rows
        if (
            reason := preparation_rule.rejection_for(
                exam.item_id,
                proposed_start=appointment_at,
            )
        )
    ]
    if unmet:
        raise HTTPException(status_code=422, detail=f"体检准备条件未满足：{'；'.join(unmet)}")
    medical_rules: tuple[MedicalEligibilityRule, ...] = (preparation_rule,)
    schedule = _run_scheduler(
        db,
        user,
        hospital,
        selected_rows,
        available_at=appointment_at,
        medical_rules=medical_rules,
    )
    if not schedule.feasible:
        reasons = "; ".join(item.reason for item in schedule.unscheduled[:3])
        raise HTTPException(status_code=422, detail=f"当前无法生成完整计划：{reasons}")
    ordered = sorted(schedule.steps, key=lambda item: item.start_at)
    total_duration = math.ceil((ordered[-1].finish_at - ordered[0].arrival_at).total_seconds() / 60) if ordered else 0
    status_record = UserStatusInfo(
        user_id=user.user_id,
        fasting_hours=(
            8
            if profile_updates.get(
                "fasting",
                "yes" if previous_status and previous_status.fasting_hours >= 8 else "no",
            )
            == "yes"
            else 0
        ),
        is_bladder_ready=(
            profile_updates.get(
                "bladder",
                "normal" if previous_status and previous_status.is_bladder_ready else "recentUrination",
            )
            == "normal"
        ),
        profile_data=profile,
    )
    db.add(status_record)
    db.flush()
    plan = ExamPlan(
        user_id=user.user_id,
        hospital_id=hospital.hospital_id,
        package_id=package.package_id if package else None,
        record_id=status_record.record_id,
        selected_item_ids=item_ids,
        total_duration=total_duration,
        appointment_at=appointment_at,
        plan_status="待执行" if appointment_at else "进行中",
    )
    db.add(plan)
    db.flush()
    if payload.reminderSubscription:
        if appointment_at is None:
            raise HTTPException(status_code=422, detail="只有预约体检可以创建微信提醒")
        try:
            create_plan_reminder(
                db,
                plan=plan,
                hospital=hospital,
                package=package,
                appointment_at=appointment_at,
                template_id=payload.reminderSubscription.templateID,
                headers=request.headers,
            )
        except WechatConfigurationError as exc:
            raise HTTPException(status_code=503, detail=f"微信提醒不可用：{exc}") from exc
    for index, step in enumerate(ordered, 1):
        db.add(
            PlanExecutionDetail(
                plan_id=plan.plan_id,
                item_id=step.exam_id,
                step_order=index,
                estimated_start=step.start_at,
                estimated_end=step.finish_at,
                exec_status="进行中" if index == 1 and appointment_at is None else "待开始",
            )
        )
    db.commit()
    return _serialize_plan(db, plan)


@router.get("/plans/current")
def current_patient_plan(
    patient: PatientContext = Depends(get_current_patient),
    db: Session = Depends(get_db),
) -> dict | None:
    plan = db.scalar(
        select(ExamPlan)
        .where(ExamPlan.user_id == patient.user_id, ExamPlan.plan_status.in_(["待执行", "进行中", "已中断"]))
        .order_by(
            case(
                (ExamPlan.plan_status == "进行中", 0),
                (ExamPlan.plan_status == "已中断", 1),
                else_=2,
            ),
            ExamPlan.generate_time.desc(),
        )
    )
    return _serialize_plan(db, plan) if plan else None


@router.get("/plans")
def list_patient_plans(
    patient: PatientContext = Depends(get_current_patient),
    db: Session = Depends(get_db),
) -> list[dict]:
    plans = db.scalars(
        select(ExamPlan).where(ExamPlan.user_id == patient.user_id).order_by(ExamPlan.generate_time.desc())
    ).all()
    return _serialize_plans(db, plans)


@router.get("/plans/{plan_id}")
def get_patient_plan(
    plan_id: str,
    patient: PatientContext = Depends(get_current_patient),
    db: Session = Depends(get_db),
) -> dict:
    return _serialize_plan(db, _require_owned_plan(db, patient.user_id, plan_id))


def _require_plan_detail(db: Session, plan: ExamPlan, detail_id: str) -> PlanExecutionDetail:
    detail = db.scalar(
        select(PlanExecutionDetail).where(
            PlanExecutionDetail.detail_id == detail_id,
            PlanExecutionDetail.plan_id == plan.plan_id,
        )
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="计划步骤不存在")
    return detail


@router.post("/plans/{plan_id}/steps/{detail_id}/start")
def start_patient_step(
    plan_id: str,
    detail_id: str,
    patient: PatientContext = Depends(get_current_patient),
    db: Session = Depends(get_db),
) -> dict:
    # Serialize starts for one patient so two booked plans cannot become active together.
    db.scalar(select(UserInfo).where(UserInfo.user_id == patient.user_id).with_for_update())
    plan = _require_owned_plan(db, patient.user_id, plan_id, for_update=True)
    if plan.plan_status not in {"待执行", "进行中"}:
        raise HTTPException(status_code=409, detail="当前体检状态不能开始项目")
    other_active_plan = db.scalar(
        select(ExamPlan.plan_id).where(
            ExamPlan.user_id == patient.user_id,
            ExamPlan.plan_status == "进行中",
            ExamPlan.plan_id != plan.plan_id,
        )
    )
    if other_active_plan:
        raise HTTPException(status_code=409, detail="请先完成或结束当前进行中的体检计划")
    detail = _require_plan_detail(db, plan, detail_id)
    if detail.exec_status == "已完成":
        raise HTTPException(status_code=409, detail="该步骤已完成")
    if detail.exec_status not in {"待开始", "进行中"}:
        raise HTTPException(status_code=409, detail="该步骤当前不能开始")
    if detail.exec_status == "进行中":
        return _serialize_plan(db, plan)
    active = db.scalar(
        select(PlanExecutionDetail.detail_id).where(
            PlanExecutionDetail.plan_id == plan.plan_id,
            PlanExecutionDetail.exec_status == "进行中",
            PlanExecutionDetail.detail_id != detail.detail_id,
        )
    )
    if active:
        raise HTTPException(status_code=409, detail="请先完成当前进行中的步骤")
    next_detail_id = db.scalar(
        select(PlanExecutionDetail.detail_id)
        .where(
            PlanExecutionDetail.plan_id == plan.plan_id,
            PlanExecutionDetail.exec_status == "待开始",
        )
        .order_by(PlanExecutionDetail.step_order)
    )
    if detail.exec_status == "待开始" and next_detail_id != detail.detail_id:
        raise HTTPException(status_code=409, detail="请按计划顺序开始下一个项目")
    exam_row = db.execute(
        select(ExamInfo, DepartmentInfo)
        .join(DepartmentInfo, DepartmentInfo.dept_id == ExamInfo.dept_id)
        .where(
            ExamInfo.item_id == detail.item_id,
            DepartmentInfo.hospital_id == plan.hospital_id,
        )
    ).one_or_none()
    if exam_row is None or not exam_row[0].is_active or not exam_row[1].is_available:
        raise HTTPException(status_code=409, detail="该检查项目已停用或所属科室暂停开放")
    preparation_rule = _current_preparation_rule(db, patient.user_id, [exam_row])
    if reason := preparation_rule.rejection_for(detail.item_id):
        raise HTTPException(status_code=409, detail=f"当前准备条件未满足：{reason}")
    detail.exec_status = "进行中"
    detail.actual_start = detail.actual_start or utcnow()
    plan.plan_status = "进行中"
    db.commit()
    return _serialize_plan(db, plan)


@router.post("/plans/{plan_id}/steps/{detail_id}/complete")
def complete_patient_step(
    plan_id: str,
    detail_id: str,
    patient: PatientContext = Depends(get_current_patient),
    db: Session = Depends(get_db),
) -> dict:
    plan = _require_owned_plan(db, patient.user_id, plan_id, for_update=True)
    if plan.plan_status != "进行中":
        raise HTTPException(status_code=409, detail="只有进行中的体检可以完成项目")
    detail = _require_plan_detail(db, plan, detail_id)
    if detail.exec_status == "已完成":
        return _serialize_plan(db, plan)
    if detail.exec_status != "进行中":
        raise HTTPException(status_code=409, detail="该步骤尚未开始")
    now = utcnow()
    detail.actual_start = detail.actual_start or now
    detail.actual_end = now
    detail.exec_status = "已完成"
    next_detail = db.scalar(
        select(PlanExecutionDetail)
        .where(PlanExecutionDetail.plan_id == plan.plan_id, PlanExecutionDetail.exec_status == "待开始")
        .order_by(PlanExecutionDetail.step_order)
    )
    if next_detail is None:
        plan.plan_status = "已完成"
    db.commit()
    return _serialize_plan(db, plan)


@router.post("/plans/{plan_id}/pause")
def pause_patient_plan(
    plan_id: str,
    patient: PatientContext = Depends(get_current_patient),
    db: Session = Depends(get_db),
) -> dict:
    plan = _require_owned_plan(db, patient.user_id, plan_id, for_update=True)
    if plan.plan_status != "进行中":
        raise HTTPException(status_code=409, detail="只有进行中的体检可以中断")
    plan.plan_status = "已中断"
    db.commit()
    return _serialize_plan(db, plan)


@router.post("/plans/{plan_id}/resume")
def resume_patient_plan(
    plan_id: str,
    patient: PatientContext = Depends(get_current_patient),
    db: Session = Depends(get_db),
) -> dict:
    plan = _require_owned_plan(db, patient.user_id, plan_id, for_update=True)
    if plan.plan_status != "已中断":
        raise HTTPException(status_code=409, detail="该体检当前不需要继续")
    plan.plan_status = "进行中"
    return replan_patient_route(plan_id, patient, db)


@router.post("/plans/{plan_id}/finish")
def finish_patient_plan(
    plan_id: str,
    patient: PatientContext = Depends(get_current_patient),
    db: Session = Depends(get_db),
) -> dict:
    plan = _require_owned_plan(db, patient.user_id, plan_id, for_update=True)
    if plan.plan_status in {"已完成", "已结束"}:
        return _serialize_plan(db, plan)
    if plan.plan_status not in {"进行中", "已中断"}:
        raise HTTPException(status_code=409, detail="该体检当前不能结束")
    details = db.scalars(
        select(PlanExecutionDetail).where(
            PlanExecutionDetail.plan_id == plan.plan_id,
            PlanExecutionDetail.exec_status != "已完成",
        )
    ).all()
    now = utcnow()
    for detail in details:
        if detail.exec_status == "进行中":
            detail.actual_end = now
        detail.exec_status = "已结束"
    plan.plan_status = "已结束"
    db.commit()
    return _serialize_plan(db, plan)


@router.post("/plans/{plan_id}/replan")
def replan_patient_route(
    plan_id: str,
    patient: PatientContext = Depends(get_current_patient),
    db: Session = Depends(get_db),
) -> dict:
    plan = _require_owned_plan(db, patient.user_id, plan_id, for_update=True)
    if plan.plan_status not in {"待执行", "进行中", "已中断"}:
        raise HTTPException(status_code=409, detail="当前体检状态不能重新排程")
    pending_details = db.scalars(
        select(PlanExecutionDetail)
        .where(PlanExecutionDetail.plan_id == plan.plan_id, PlanExecutionDetail.exec_status == "待开始")
        .order_by(PlanExecutionDetail.step_order)
    ).all()
    if not pending_details:
        db.commit()
        return _serialize_plan(db, plan, replanned=True)
    pending_ids = [detail.item_id for detail in pending_details]
    fixed_rows = db.execute(
        select(PlanExecutionDetail, ExamInfo)
        .join(ExamInfo, ExamInfo.item_id == PlanExecutionDetail.item_id)
        .where(
            PlanExecutionDetail.plan_id == plan.plan_id,
            PlanExecutionDetail.exec_status != "待开始",
        )
        .order_by(PlanExecutionDetail.step_order)
    ).all()
    satisfied_ids = {detail.item_id for detail, _exam in fixed_rows}
    active_row = next(
        ((detail, exam) for detail, exam in fixed_rows if detail.exec_status == "进行中"),
        None,
    )
    anchor_row = active_row or (fixed_rows[-1] if fixed_rows else None)
    active_available_at = None
    if active_row:
        active_detail, active_exam = active_row
        active_available_at = active_detail.estimated_end or (
            (active_detail.actual_start or utcnow()) + timedelta(minutes=active_exam.duration)
        )
    exam_rows = db.execute(
        select(ExamInfo, DepartmentInfo)
        .join(DepartmentInfo, DepartmentInfo.dept_id == ExamInfo.dept_id)
        .where(ExamInfo.item_id.in_(pending_ids), DepartmentInfo.hospital_id == plan.hospital_id)
    ).all()
    by_id = {exam.item_id: (exam, department) for exam, department in exam_rows}
    if set(by_id) != set(pending_ids):
        raise HTTPException(status_code=409, detail="计划中的检查项目已删除或不再属于当前医院")
    ordered_rows = [by_id[item_id] for item_id in pending_ids]
    unavailable = [
        exam.item_name
        for exam, department in ordered_rows
        if not exam.is_active or not department.is_available
    ]
    if unavailable:
        raise HTTPException(status_code=409, detail=f"以下检查项目当前不可用：{'、'.join(unavailable)}")
    preparation_rule = _current_preparation_rule(db, patient.user_id, ordered_rows)
    unmet = [
        f"{exam.item_name}：{reason}"
        for exam, _department in ordered_rows
        if (reason := preparation_rule.rejection_for(exam.item_id))
    ]
    if unmet:
        raise HTTPException(status_code=409, detail=f"当前准备条件未满足：{'；'.join(unmet)}")
    schedule = _run_scheduler(
        db,
        db.get(UserInfo, patient.user_id),
        db.get(HospitalInfo, plan.hospital_id),
        ordered_rows,
        previous_order=tuple(pending_ids),
        satisfied_item_ids=satisfied_ids,
        available_at=active_available_at,
        location_id=anchor_row[1].dept_id if anchor_row else "entrance",
        medical_rules=(preparation_rule,),
    )
    if not schedule.feasible:
        raise HTTPException(status_code=422, detail="最新排队状态下无法生成完整后续路线")
    fixed_count = db.scalar(
        select(func.count()).select_from(PlanExecutionDetail).where(
            PlanExecutionDetail.plan_id == plan.plan_id,
            PlanExecutionDetail.exec_status != "待开始",
        )
    ) or 0
    detail_by_item = {detail.item_id: detail for detail in pending_details}
    for index, step in enumerate(sorted(schedule.steps, key=lambda item: item.start_at), fixed_count + 1):
        detail = detail_by_item[step.exam_id]
        detail.step_order = index
        detail.estimated_start = step.start_at
        detail.estimated_end = step.finish_at
    db.commit()
    return _serialize_plan(db, plan, replanned=True)


@router.get("/plans/{plan_id}/navigation")
def patient_navigation(
    plan_id: str,
    detailID: str,
    patient: PatientContext = Depends(get_current_patient),
    db: Session = Depends(get_db),
) -> dict:
    plan = _require_owned_plan(db, patient.user_id, plan_id)
    detail = _require_plan_detail(db, plan, detailID)
    target = db.execute(
        select(ExamInfo, DepartmentInfo)
        .join(DepartmentInfo, DepartmentInfo.dept_id == ExamInfo.dept_id)
        .where(ExamInfo.item_id == detail.item_id)
    ).one()
    previous = db.execute(
        select(PlanExecutionDetail, ExamInfo, DepartmentInfo)
        .join(ExamInfo, ExamInfo.item_id == PlanExecutionDetail.item_id)
        .join(DepartmentInfo, DepartmentInfo.dept_id == ExamInfo.dept_id)
        .where(
            PlanExecutionDetail.plan_id == plan.plan_id,
            PlanExecutionDetail.step_order < detail.step_order,
        )
        .order_by(PlanExecutionDetail.step_order.desc())
    ).first()
    from_department = previous.DepartmentInfo if previous else None
    distance = None
    if from_department:
        distance = db.scalar(
            select(DepartmentDistance.distance_meters).where(
                or_(
                    (DepartmentDistance.from_dept_id == from_department.dept_id) & (DepartmentDistance.to_dept_id == target.DepartmentInfo.dept_id),
                    (DepartmentDistance.from_dept_id == target.DepartmentInfo.dept_id) & (DepartmentDistance.to_dept_id == from_department.dept_id),
                )
            )
        )
    map_data = _navigation_map(
        db,
        plan.hospital_id,
        from_department.dept_id if from_department else None,
        target.DepartmentInfo.dept_id,
        from_department.dept_name if from_department else "医院入口",
        target.DepartmentInfo.dept_name,
    )
    speed = max(db.get(UserInfo, patient.user_id).walk_speed, 0.2)
    floor_instruction = "请根据院内标识或咨询工作人员前往目标科室。"
    if map_data:
        from_floor = map_data.get("fromPoint", {}).get("floorKey") if map_data.get("fromPoint") else None
        target_floor = map_data["toPoint"]["floorKey"]
        if from_floor and from_floor != target_floor:
            floor_instruction = f"请先前往 {target_floor}，再按楼层图前往目标科室。"
        elif map_data["routeCoordinates"]:
            floor_instruction = "请沿图中蓝色路线前往绿色终点。"
        else:
            floor_instruction = f"请前往 {target_floor}，并按楼层图中的绿色终点寻找科室。"
    return {
        "fromName": from_department.dept_name if from_department else "医院入口",
        "toName": target.DepartmentInfo.dept_name,
        "location": target.DepartmentInfo.location,
        "distanceMeters": round(distance) if distance is not None else None,
        "durationMinutes": max(1, math.ceil(distance / speed / 60)) if distance is not None else None,
        "floorInstruction": floor_instruction,
        "map": map_data,
    }


def _navigation_map(
    db: Session,
    hospital_id: str,
    from_department_id: str | None,
    to_department_id: str,
    from_name: str,
    to_name: str,
) -> dict | None:
    floors = db.scalars(
        select(HospitalGIS)
        .where(HospitalGIS.hospital_id == hospital_id)
        .order_by(HospitalGIS.floor_key)
    ).all()
    locations: dict[str, tuple[HospitalGIS, list[float]]] = {}
    for floor in floors:
        for feature in floor.geojson.get("features", []):
            properties = feature.get("properties") or {}
            geometry = feature.get("geometry") or {}
            point = _coordinate_pair(geometry.get("coordinates"))
            if properties.get("featureType") == "department" and geometry.get("type") == "Point" and point:
                locations[properties.get("deptID")] = (floor, point)

    target_location = locations.get(to_department_id)
    if target_location is None:
        return None
    target_floor, target_point = target_location
    from_location = locations.get(from_department_id) if from_department_id else None
    route_coordinates: list[list[float]] = []
    from_point = None
    if from_location:
        source_floor, source_coordinates = from_location
        from_point = {
            "departmentID": from_department_id,
            "name": from_name,
            "floorKey": source_floor.floor_key,
            "coordinates": source_coordinates,
        }
        if source_floor.floor_key == target_floor.floor_key:
            route_coordinates = _shortest_geojson_route(
                target_floor.geojson,
                source_coordinates,
                target_point,
            )
    return {
        "floorKey": target_floor.floor_key,
        "version": target_floor.version,
        "geojson": target_floor.geojson,
        "fromPoint": from_point,
        "toPoint": {
            "departmentID": to_department_id,
            "name": to_name,
            "floorKey": target_floor.floor_key,
            "coordinates": target_point,
        },
        "routeCoordinates": route_coordinates,
    }


def _coordinate_pair(value) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    x, y = value[0], value[1]
    if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    if not math.isfinite(x) or not math.isfinite(y):
        return None
    return [float(x), float(y)]


def _point_key(point: list[float]) -> tuple[float, float]:
    return round(point[0], 9), round(point[1], 9)


def _coordinate_distance(first: list[float], second: list[float]) -> float:
    return math.hypot(second[0] - first[0], second[1] - first[1])


def _shortest_geojson_route(
    geojson: dict,
    source: list[float],
    target: list[float],
) -> list[list[float]]:
    graph: dict[tuple[float, float], list[tuple[tuple[float, float], float]]] = {}
    coordinates_by_key: dict[tuple[float, float], list[float]] = {}
    for feature in geojson.get("features", []):
        properties = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        if properties.get("featureType") not in {"corridor", "route"} or geometry.get("type") != "LineString":
            continue
        points = [point for value in geometry.get("coordinates", []) if (point := _coordinate_pair(value))]
        for first, second in zip(points, points[1:]):
            first_key, second_key = _point_key(first), _point_key(second)
            distance = max(_coordinate_distance(first, second), 1e-12)
            coordinates_by_key[first_key] = first
            coordinates_by_key[second_key] = second
            graph.setdefault(first_key, []).append((second_key, distance))
            graph.setdefault(second_key, []).append((first_key, distance))
    if not graph:
        return []

    source_key = min(coordinates_by_key, key=lambda key: _coordinate_distance(source, coordinates_by_key[key]))
    target_key = min(coordinates_by_key, key=lambda key: _coordinate_distance(target, coordinates_by_key[key]))
    distances = {source_key: 0.0}
    previous: dict[tuple[float, float], tuple[float, float]] = {}
    queue = [(0.0, source_key)]
    while queue:
        current_distance, current = heapq.heappop(queue)
        if current == target_key:
            break
        if current_distance > distances.get(current, math.inf):
            continue
        for neighbor, weight in graph.get(current, []):
            candidate = current_distance + weight
            if candidate < distances.get(neighbor, math.inf):
                distances[neighbor] = candidate
                previous[neighbor] = current
                heapq.heappush(queue, (candidate, neighbor))
    if target_key not in distances:
        return []
    keys = [target_key]
    while keys[-1] != source_key:
        keys.append(previous[keys[-1]])
    keys.reverse()
    route = [coordinates_by_key[key] for key in keys]
    if _point_key(source) != source_key:
        route.insert(0, source)
    if _point_key(target) != target_key:
        route.append(target)
    return route
