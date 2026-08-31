from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from checkup_scheduler import (
    BatchPlannerConfig,
    DepartmentState,
    Exam,
    PatientState,
    TimeWindow,
    TravelTimeMatrix,
    build_batch_schedule,
)

from .api import client_ip
from .database import get_db
from .exam_constraints import prerequisite_item_ids, validate_exam_selection
from .models import (
    DepartmentDistance,
    DepartmentInfo,
    ExamInfo,
    ExamPlan,
    HospitalAdmin,
    HospitalInfo,
    PackageInfo,
    PlanExecutionDetail,
    QueueSnapshot,
    UserInfo,
    UserSession,
    UserStatusInfo,
    utcnow,
)
from .schemas import LoginRequest, PatientPlanCreate, PatientProfileUpdate, PatientRegister
from .security import hash_password, issue_session, revoke_session, session_digest, verify_password
from .serializers import hospital_dict, iso

router = APIRouter(prefix="/api/patient", tags=["patient-miniprogram"])
PATIENT_SESSION_COOKIE = "checkup_patient_session"
TIME_RANGE_PATTERN = re.compile(r"(\d{2}):(\d{2}).*?(\d{2}):(\d{2})")


@dataclass(frozen=True)
class PatientContext:
    user_id: str
    name: str
    phone: str


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
    db.flush()
    token = issue_session(db, user.user_id, client_ip(request))
    db.commit()
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
    if user is None or is_admin or user.role == "演示患者" or not verify_password(payload.password, user.password):
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


@router.patch("/profile")
def update_profile(
    payload: PatientProfileUpdate,
    patient: PatientContext = Depends(get_current_patient),
    db: Session = Depends(get_db),
) -> dict:
    user = db.get(UserInfo, patient.user_id)
    data = payload.model_dump(exclude_unset=True)
    for field in ("name", "gender", "phone"):
        if field in data:
            setattr(user, field, data[field])
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


@router.get("/hospitals")
def list_patient_hospitals(
    _patient: PatientContext = Depends(get_current_patient),
    db: Session = Depends(get_db),
) -> list[dict]:
    hospitals = db.scalars(select(HospitalInfo).order_by(HospitalInfo.hospital_name)).all()
    return [
        {
            **hospital_dict(row),
            "id": row.hospital_id,
            "name": row.hospital_name,
            "status": "正常开放",
            "campuses": [
                {
                    "id": row.hospital_id,
                    "name": row.hospital_name,
                    "openTime": row.open_time,
                    "available": True,
                }
            ],
        }
        for row in hospitals
    ]


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
        "hospital": hospital_dict(hospital),
        "packages": packages,
        "exams": exams,
        "departments": [{"name": name, "projects": items} for name, items in departments.items()],
    }


def _parse_open_window(hospital: HospitalInfo, now: datetime) -> tuple[datetime, datetime]:
    match = TIME_RANGE_PATTERN.search(hospital.open_time or "")
    start_hour, start_minute, end_hour, end_minute = (8, 0, 17, 0)
    if match:
        start_hour, start_minute, end_hour, end_minute = map(int, match.groups())
    day = now.date()
    start = datetime.combine(day, datetime.min.time()).replace(hour=start_hour, minute=start_minute)
    end = datetime.combine(day, datetime.min.time()).replace(hour=end_hour, minute=end_minute)
    if now >= end:
        start += timedelta(days=1)
        end += timedelta(days=1)
    return max(now, start), end


def _allowed_windows(exam: ExamInfo, day_start: datetime, day_end: datetime) -> tuple[TimeWindow, ...]:
    data = exam.allowed_time_slots or {}
    start_text, end_text = data.get("start"), data.get("end")
    if not start_text or not end_text:
        return ()
    try:
        start_time = datetime.strptime(start_text, "%H:%M").time()
        end_time = datetime.strptime(end_text, "%H:%M").time()
    except ValueError:
        return ()
    start = max(day_start, datetime.combine(day_start.date(), start_time))
    end = min(day_end, datetime.combine(day_start.date(), end_time))
    return (TimeWindow(start, end),) if start < end else ()


def _latest_department_waits(db: Session, hospital_id: str, now: datetime) -> dict[str, int]:
    rows = db.execute(
        select(QueueSnapshot, ExamInfo.dept_id)
        .join(ExamInfo, ExamInfo.item_id == QueueSnapshot.item_id)
        .join(DepartmentInfo, DepartmentInfo.dept_id == ExamInfo.dept_id)
        .where(DepartmentInfo.hospital_id == hospital_id, QueueSnapshot.valid_until > now)
        .order_by(QueueSnapshot.item_id, QueueSnapshot.create_time.desc())
    ).all()
    waits: dict[str, int] = {}
    seen: set[str] = set()
    for snapshot, department_id in rows:
        if snapshot.item_id in seen:
            continue
        seen.add(snapshot.item_id)
        waits[department_id] = max(waits.get(department_id, 0), math.ceil(snapshot.estimated_wait_time / 60))
    return waits


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
):
    now = utcnow()
    planning_start, planning_end = _parse_open_window(hospital, max(now, available_at or now))
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
        departments[department.dept_id] = DepartmentState(
            id=department.dept_id,
            observed_at=planning_start,
            expected_wait_minutes=waits.get(department.dept_id, 0),
            accepting_patients=department.is_available,
            service_windows=(TimeWindow(planning_start, planning_end),),
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
        availability_windows=(TimeWindow(planning_start, planning_end),),
        age_years=_age_from_birth_date(user.birth_date),
        gender=user.gender,
    )
    return build_batch_schedule(
        (patient,),
        departments,
        _travel_matrix(db, hospital.hospital_id, user.walk_speed),
        TimeWindow(planning_start, planning_end),
        config=BatchPlannerConfig(wait_oriented=True),
    )


def _require_owned_plan(db: Session, user_id: str, plan_id: str) -> ExamPlan:
    plan = db.scalar(select(ExamPlan).where(ExamPlan.plan_id == plan_id, ExamPlan.user_id == user_id))
    if plan is None:
        raise HTTPException(status_code=404, detail="体检计划不存在")
    return plan


def _queue_by_item(db: Session, item_ids: list[str]) -> dict[str, QueueSnapshot]:
    if not item_ids:
        return {}
    rows = db.scalars(
        select(QueueSnapshot)
        .where(QueueSnapshot.item_id.in_(item_ids), QueueSnapshot.valid_until > utcnow())
        .order_by(QueueSnapshot.item_id, QueueSnapshot.create_time.desc())
    ).all()
    result: dict[str, QueueSnapshot] = {}
    for row in rows:
        result.setdefault(row.item_id, row)
    return result


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
    profile_snapshot = dict(status_record.profile_data or {}) if status_record else {}
    if status_record:
        profile_snapshot.setdefault("fasting", "yes" if status_record.fasting_hours >= 8 else "no")
        profile_snapshot.setdefault("bladder", "normal" if status_record.is_bladder_ready else "recentUrination")
    selected_ids = list(plan.selected_item_ids or [])
    queues = _queue_by_item(db, selected_ids)
    completed = sum(detail.exec_status == "已完成" for detail, _exam, _department in rows)
    first_open = next((index for index, (detail, _exam, _department) in enumerate(rows) if detail.exec_status != "已完成"), len(rows))
    steps = []
    for detail, exam, department in rows:
        queue = queues.get(exam.item_id)
        queue_wait = math.ceil(queue.estimated_wait_time / 60) if queue else 0
        prerequisites = exam.prerequisites or {}
        state = "done" if detail.exec_status == "已完成" else "active" if detail.exec_status == "进行中" else "pending"
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
                "queuePosition": f"前方约 {queue.queue_count} 人" if queue else "暂无实时数据",
                "totalDuration": exam.duration + queue_wait,
                "fasting": bool(prerequisites.get("fastingHours", 0)),
                "note": "请遵循现场医护人员指引。",
                "status": state,
                "started": state in {"active", "done"},
                "completed": state == "done",
                "estimatedStart": iso(detail.estimated_start),
                "estimatedEnd": iso(detail.estimated_end),
                "navigationTarget": {"department": department.dept_name, "locationText": department.location or "请查看院内指引"},
            }
        )
    hospital = db.get(HospitalInfo, plan.hospital_id)
    return {
        "id": plan.plan_id,
        "planID": plan.plan_id,
        "packageId": package.package_id if package else None,
        "packageName": package.package_name if package else "自选项目",
        "packagePrice": package.price if package else 0,
        "hospitalName": hospital.hospital_name if hospital else "",
        "date": plan.generate_time.strftime("%Y年%m月%d日"),
        "generatedAt": iso(plan.generate_time),
        "totalDuration": plan.total_duration,
        "remainingDuration": sum(step["totalDuration"] for step in steps if step["status"] != "done"),
        "totalSteps": len(steps),
        "completedSteps": completed,
        "currentStepIndex": first_open,
        "progress": round(completed / len(steps) * 100) if steps else 0,
        "planStatus": plan.plan_status,
        "status": plan.plan_status,
        "finished": plan.plan_status == "已完成",
        "profileSnapshot": profile_snapshot,
        "waitingHint": "路线由服务端 Scheduler 根据时间窗、排队与步行距离生成。",
        "replanNotice": "已按最新排队情况重新安排后续路线。" if replanned else "",
        "result": "敬请期待",
        "steps": steps,
    }


@router.post("/plans", status_code=status.HTTP_201_CREATED)
def create_patient_plan(
    payload: PatientPlanCreate,
    patient: PatientContext = Depends(get_current_patient),
    db: Session = Depends(get_db),
) -> dict:
    hospital = db.get(HospitalInfo, payload.hospitalID)
    if hospital is None:
        raise HTTPException(status_code=404, detail="医院不存在")
    if db.scalar(
        select(ExamPlan.plan_id).where(
            ExamPlan.user_id == patient.user_id,
            ExamPlan.plan_status.in_(["待执行", "进行中"]),
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
    user = db.get(UserInfo, patient.user_id)
    previous_status = _latest_status(db, user.user_id)
    profile_updates = payload.profile or {}
    profile = {
        **((previous_status.profile_data or {}) if previous_status else {}),
        **profile_updates,
    }
    schedule = _run_scheduler(db, user, hospital, selected_rows)
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
        plan_status="进行中",
    )
    db.add(plan)
    db.flush()
    for index, step in enumerate(ordered, 1):
        db.add(
            PlanExecutionDetail(
                plan_id=plan.plan_id,
                item_id=step.exam_id,
                step_order=index,
                estimated_start=step.start_at,
                estimated_end=step.finish_at,
                exec_status="进行中" if index == 1 else "待开始",
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
        .where(ExamPlan.user_id == patient.user_id, ExamPlan.plan_status.in_(["待执行", "进行中"]))
        .order_by(ExamPlan.generate_time.desc())
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
    return [_serialize_plan(db, plan) for plan in plans]


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
    plan = _require_owned_plan(db, patient.user_id, plan_id)
    detail = _require_plan_detail(db, plan, detail_id)
    if detail.exec_status == "已完成":
        raise HTTPException(status_code=409, detail="该步骤已完成")
    active = db.scalar(
        select(PlanExecutionDetail.detail_id).where(
            PlanExecutionDetail.plan_id == plan.plan_id,
            PlanExecutionDetail.exec_status == "进行中",
            PlanExecutionDetail.detail_id != detail.detail_id,
        )
    )
    if active:
        raise HTTPException(status_code=409, detail="请先完成当前进行中的步骤")
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
    plan = _require_owned_plan(db, patient.user_id, plan_id)
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
    if next_detail:
        next_detail.exec_status = "进行中"
    else:
        plan.plan_status = "已完成"
    db.commit()
    return _serialize_plan(db, plan)


@router.post("/plans/{plan_id}/replan")
def replan_patient_route(
    plan_id: str,
    patient: PatientContext = Depends(get_current_patient),
    db: Session = Depends(get_db),
) -> dict:
    plan = _require_owned_plan(db, patient.user_id, plan_id)
    pending_details = db.scalars(
        select(PlanExecutionDetail)
        .where(PlanExecutionDetail.plan_id == plan.plan_id, PlanExecutionDetail.exec_status == "待开始")
        .order_by(PlanExecutionDetail.step_order)
    ).all()
    if not pending_details:
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
    ordered_rows = [by_id[item_id] for item_id in pending_ids]
    schedule = _run_scheduler(
        db,
        db.get(UserInfo, patient.user_id),
        db.get(HospitalInfo, plan.hospital_id),
        ordered_rows,
        previous_order=tuple(pending_ids),
        satisfied_item_ids=satisfied_ids,
        available_at=active_available_at,
        location_id=anchor_row[1].dept_id if anchor_row else "entrance",
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
    speed = max(db.get(UserInfo, patient.user_id).walk_speed, 0.2)
    return {
        "fromName": from_department.dept_name if from_department else "医院入口",
        "toName": target.DepartmentInfo.dept_name,
        "location": target.DepartmentInfo.location,
        "distanceMeters": round(distance) if distance is not None else None,
        "durationMinutes": max(1, math.ceil(distance / speed / 60)) if distance is not None else None,
    }
