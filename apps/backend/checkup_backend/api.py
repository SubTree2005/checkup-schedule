from __future__ import annotations

import os
from copy import deepcopy
from ipaddress import ip_address
from math import isfinite
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import get_db
from .demo_patients import demo_pool_summary, prepare_demo_patient_pool, set_demo_patient_count
from .exam_constraints import prerequisite_item_ids, validate_exam_selection, validate_prerequisite_graph
from .hospital_time import local_day_bounds_utc
from .models import (
    AnomalyReport,
    DepartmentDistance,
    DepartmentInfo,
    DemoPatientProfile,
    ExamInfo,
    ExamPlan,
    HospitalAdmin,
    HospitalGIS,
    HospitalInfo,
    HospitalSettings,
    PackageInfo,
    PlanExecutionDetail,
    UserInfo,
    utcnow,
)
from .schemas import (
    AnomalyCreate,
    AnomalyResolve,
    DepartmentCreate,
    DepartmentUpdate,
    DemoPatientTarget,
    ExamCreate,
    ExamUpdate,
    GISUpload,
    HospitalRegister,
    HospitalUpdate,
    LoginRequest,
    PackageCreate,
    PackageUpdate,
    WorkspaceImport,
)
from .security import (
    AdminContext,
    SESSION_COOKIE,
    clear_session_cookie,
    get_current_admin,
    hash_password,
    issue_session,
    revoke_session,
    set_session_cookie,
    verify_login_password,
)
from .queue_state import system_department_queues, system_item_queues
from .serializers import anomaly_dict, department_dict, exam_dict, hospital_dict, iso, package_dict

router = APIRouter(prefix="/api")


def client_ip(request: Request) -> str | None:
    direct = request.client.host[:64] if request.client else None
    trust_proxy_headers = os.getenv("TRUST_PROXY_HEADERS", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not trust_proxy_headers:
        return direct
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return direct
    try:
        return str(ip_address(forwarded.split(",", 1)[0].strip()))
    except ValueError:
        return direct


def get_hospital_settings(db: Session, hospital_id: str, *, create: bool = False) -> HospitalSettings | None:
    settings = db.get(HospitalSettings, hospital_id)
    if settings is None and create:
        settings = HospitalSettings(hospital_id=hospital_id)
        db.add(settings)
    return settings


def require_department(db: Session, hospital_id: str, dept_id: str) -> DepartmentInfo:
    row = db.scalar(
        select(DepartmentInfo).where(
            DepartmentInfo.dept_id == dept_id,
            DepartmentInfo.hospital_id == hospital_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="科室不存在")
    return row


def require_exam(db: Session, hospital_id: str, item_id: str) -> ExamInfo:
    row = db.scalar(
        select(ExamInfo)
        .join(DepartmentInfo, DepartmentInfo.dept_id == ExamInfo.dept_id)
        .where(ExamInfo.item_id == item_id, DepartmentInfo.hospital_id == hospital_id)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="检查项目不存在")
    return row


def packages_using_item(db: Session, hospital_id: str, item_id: str) -> list[PackageInfo]:
    return [
        package
        for package in db.scalars(select(PackageInfo).where(PackageInfo.hospital_id == hospital_id)).all()
        if item_id in (package.included_item_ids or [])
    ]


def validate_conflicts(db: Session, hospital_id: str, conflicts: list[str]) -> None:
    if not conflicts:
        return
    owned = set(
        db.scalars(
            select(ExamInfo.item_id)
            .join(DepartmentInfo, DepartmentInfo.dept_id == ExamInfo.dept_id)
            .where(DepartmentInfo.hospital_id == hospital_id, ExamInfo.item_id.in_(set(conflicts)))
        )
    )
    if owned != set(conflicts):
        raise HTTPException(status_code=422, detail="互斥项目包含本医院不存在的项目")


def validate_prerequisites(db: Session, hospital_id: str, prerequisites: dict) -> tuple[str, ...]:
    try:
        item_ids = prerequisite_item_ids(prerequisites)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"前置项目格式无效：{exc}") from exc
    if not item_ids:
        return ()
    owned = set(
        db.scalars(
            select(ExamInfo.item_id)
            .join(DepartmentInfo, DepartmentInfo.dept_id == ExamInfo.dept_id)
            .where(DepartmentInfo.hospital_id == hospital_id, ExamInfo.item_id.in_(set(item_ids)))
        )
    )
    if owned != set(item_ids):
        raise HTTPException(status_code=422, detail="前置项目包含本医院不存在的项目")
    return item_ids


def validate_hospital_prerequisite_graph(db: Session, hospital_id: str) -> None:
    rows = db.scalars(
        select(ExamInfo)
        .join(DepartmentInfo, DepartmentInfo.dept_id == ExamInfo.dept_id)
        .where(DepartmentInfo.hospital_id == hospital_id)
    ).all()
    try:
        validate_prerequisite_graph(
            [row.item_id for row in rows],
            {row.item_id: prerequisite_item_ids(row.prerequisites) for row in rows},
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"检查项目前置关系无效：{exc}") from exc


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "time": iso(utcnow())}


@router.get("/auth/register-template")
def get_hospital_registration_template() -> dict:
    template = workspace_import_template()
    template["hospital"] = {
        "hospitalName": "示例医院",
        "address": "请填写医院地址",
        "openTime": "08:00-17:00",
        "floorMapUrl": None,
        "coverImageUrl": None,
        "hospitalLevel": "未定级",
        "positioning": "综合医疗机构",
        "isAvailable": True,
        "appointmentSlotMinutes": 30,
        "appointmentSlotCapacity": 20,
        "appointmentDaysAhead": 7,
    }
    return template


@router.post("/auth/register", status_code=status.HTTP_201_CREATED)
def register_hospital(
    payload: HospitalRegister,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    if db.scalar(select(UserInfo.user_id).where(UserInfo.phone == payload.phone)):
        raise HTTPException(status_code=409, detail="该手机号已注册")
    hospital_data = payload.workspace.hospital
    if hospital_data is None:
        raise HTTPException(status_code=422, detail="医院注册必须上传包含 hospital 的完整工作区数据")
    hospital = HospitalInfo(
        hospital_name=hospital_data.hospitalName,
        address=hospital_data.address,
        open_time=hospital_data.openTime,
        floor_map_url=hospital_data.floorMapUrl,
    )
    user = UserInfo(
        phone=payload.phone,
        password=hash_password(payload.password),
        name=payload.adminName,
        role="管理员",
    )
    db.add_all([hospital, user])
    try:
        db.flush()
        settings = HospitalSettings(
            hospital_id=hospital.hospital_id,
            cover_image_url=hospital_data.coverImageUrl,
            hospital_level=hospital_data.hospitalLevel,
            positioning=hospital_data.positioning,
            is_available=hospital_data.isAvailable,
            appointment_slot_minutes=hospital_data.appointmentSlotMinutes,
            appointment_slot_capacity=hospital_data.appointmentSlotCapacity,
            appointment_days_ahead=hospital_data.appointmentDaysAhead,
        )
        db.add(settings)
        db.add(HospitalAdmin(user_id=user.user_id, hospital_id=hospital.hospital_id, is_owner=True))
        admin = AdminContext(
            user_id=user.user_id,
            hospital_id=hospital.hospital_id,
            name=user.name,
            phone=user.phone,
            is_owner=True,
        )
        workspace_result = _apply_workspace_import(payload.workspace, admin, db, commit=False)
        prepared = prepare_demo_patient_pool(db, hospital.hospital_id)
        token = issue_session(db, user.user_id, client_ip(request))
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="医院账号注册冲突") from exc
    set_session_cookie(response, token)
    return {
        "user": {"userID": user.user_id, "name": user.name, "phone": user.phone, "role": user.role},
        "hospital": hospital_dict(hospital, settings),
        "workspaceSummary": workspace_result["summary"],
        "demoPool": {"prepared": prepared, "active": 0},
    }


@router.post("/auth/login")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    user = db.scalar(select(UserInfo).where(UserInfo.phone == payload.phone))
    membership = None if user is None else db.scalar(select(HospitalAdmin).where(HospitalAdmin.user_id == user.user_id))
    eligible_password = user.password if user is not None and membership is not None else None
    if not verify_login_password(payload.password, eligible_password):
        raise HTTPException(status_code=401, detail="手机号或密码错误")
    hospital = db.get(HospitalInfo, membership.hospital_id)
    settings = get_hospital_settings(db, membership.hospital_id)
    token = issue_session(db, user.user_id, client_ip(request))
    db.commit()
    set_session_cookie(response, token)
    return {
        "user": {"userID": user.user_id, "name": user.name, "phone": user.phone, "role": user.role},
        "hospital": hospital_dict(hospital, settings),
    }


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Response:
    bearer_token = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer_token = authorization[7:].strip()
    revoke_session(db, bearer_token or token)
    db.commit()
    clear_session_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/auth/me")
def me(admin: AdminContext = Depends(get_current_admin), db: Session = Depends(get_db)) -> dict:
    hospital = db.get(HospitalInfo, admin.hospital_id)
    settings = get_hospital_settings(db, admin.hospital_id)
    return {
        "user": {
            "userID": admin.user_id,
            "name": admin.name,
            "phone": admin.phone,
            "role": "管理员",
            "isOwner": admin.is_owner,
        },
        "hospital": hospital_dict(hospital, settings),
    }


@router.get("/hospital")
def get_hospital(admin: AdminContext = Depends(get_current_admin), db: Session = Depends(get_db)) -> dict:
    return hospital_dict(
        db.get(HospitalInfo, admin.hospital_id),
        get_hospital_settings(db, admin.hospital_id),
    )


@router.patch("/hospital")
def update_hospital(
    payload: HospitalUpdate,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(HospitalInfo, admin.hospital_id)
    data = payload.model_dump(exclude_unset=True)
    non_nullable = {
        "hospitalName",
        "address",
        "openTime",
        "hospitalLevel",
        "positioning",
        "isAvailable",
        "appointmentSlotMinutes",
        "appointmentSlotCapacity",
        "appointmentDaysAhead",
    }
    if any(data.get(key) is None for key in non_nullable & data.keys()):
        raise HTTPException(status_code=422, detail="医院必填字段不能设为空值")
    fields = {
        "hospitalName": "hospital_name",
        "address": "address",
        "openTime": "open_time",
        "floorMapUrl": "floor_map_url",
    }
    settings_fields = {
        "coverImageUrl": "cover_image_url",
        "hospitalLevel": "hospital_level",
        "positioning": "positioning",
        "isAvailable": "is_available",
        "appointmentSlotMinutes": "appointment_slot_minutes",
        "appointmentSlotCapacity": "appointment_slot_capacity",
        "appointmentDaysAhead": "appointment_days_ahead",
    }
    settings = get_hospital_settings(db, admin.hospital_id, create=True)
    for key, value in data.items():
        if key in fields:
            setattr(row, fields[key], value)
        else:
            setattr(settings, settings_fields[key], value)
    db.commit()
    return hospital_dict(row, settings)


@router.get("/departments")
def list_departments(admin: AdminContext = Depends(get_current_admin), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(DepartmentInfo)
        .where(DepartmentInfo.hospital_id == admin.hospital_id)
        .order_by(DepartmentInfo.dept_name)
    ).all()
    return [department_dict(row) for row in rows]


@router.post("/departments", status_code=status.HTTP_201_CREATED)
def create_department(
    payload: DepartmentCreate,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    row = DepartmentInfo(
        hospital_id=admin.hospital_id,
        dept_name=payload.deptName,
        location=payload.location,
        open_time_start=payload.openTimeStart,
        open_time_end=payload.openTimeEnd,
        capacity=payload.capacity,
        is_available=payload.isAvailable,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="本医院已有同名科室") from exc
    return department_dict(row)


@router.patch("/departments/{dept_id}")
def update_department(
    dept_id: str,
    payload: DepartmentUpdate,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    row = require_department(db, admin.hospital_id, dept_id)
    data = payload.model_dump(exclude_unset=True)
    if any(value is None for value in data.values()):
        raise HTTPException(status_code=422, detail="科室字段不能设为空值")
    fields = {
        "deptName": "dept_name",
        "location": "location",
        "openTimeStart": "open_time_start",
        "openTimeEnd": "open_time_end",
        "capacity": "capacity",
        "isAvailable": "is_available",
    }
    for key, value in data.items():
        setattr(row, fields[key], value)
    if row.open_time_start >= row.open_time_end:
        raise HTTPException(status_code=422, detail="开放结束时间必须晚于开始时间")
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="本医院已有同名科室") from exc
    return department_dict(row)


@router.delete("/departments/{dept_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_department(
    dept_id: str,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> Response:
    row = require_department(db, admin.hospital_id, dept_id)
    if db.scalar(select(func.count()).select_from(ExamInfo).where(ExamInfo.dept_id == dept_id)):
        raise HTTPException(status_code=409, detail="科室仍有检查项目，请先停用项目或调整归属")
    db.delete(row)
    db.commit()
    return Response(status_code=204)


@router.get("/exams")
def list_exams(admin: AdminContext = Depends(get_current_admin), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(ExamInfo)
        .join(DepartmentInfo, DepartmentInfo.dept_id == ExamInfo.dept_id)
        .where(DepartmentInfo.hospital_id == admin.hospital_id)
        .order_by(ExamInfo.item_name)
    ).all()
    return [exam_dict(row) for row in rows]


@router.post("/exams", status_code=status.HTTP_201_CREATED)
def create_exam(
    payload: ExamCreate,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    require_department(db, admin.hospital_id, payload.deptID)
    validate_conflicts(db, admin.hospital_id, payload.conflicts)
    prerequisite_ids = validate_prerequisites(db, admin.hospital_id, payload.prerequisites)
    overlap = set(prerequisite_ids) & set(payload.conflicts)
    if overlap:
        raise HTTPException(status_code=422, detail=f"项目不能同时前置和互斥 {sorted(overlap)}")
    row = ExamInfo(
        dept_id=payload.deptID,
        item_name=payload.itemName,
        duration=payload.duration,
        prerequisites=payload.prerequisites,
        conflicts=payload.conflicts,
        priority=payload.priority,
        allowed_time_slots=payload.allowedTimeSlots,
        is_critical=payload.isCritical,
        is_active=payload.isActive,
    )
    db.add(row)
    try:
        db.flush()
        validate_hospital_prerequisite_graph(db, admin.hospital_id)
        db.commit()
        return exam_dict(row)
    except HTTPException:
        db.rollback()
        raise


@router.patch("/exams/{item_id}")
def update_exam(
    item_id: str,
    payload: ExamUpdate,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    row = require_exam(db, admin.hospital_id, item_id)
    data = payload.model_dump(exclude_unset=True)
    if any(value is None for value in data.values()):
        raise HTTPException(status_code=422, detail="检查项目字段不能设为空值")
    if "deptID" in data:
        require_department(db, admin.hospital_id, data["deptID"])
    if "conflicts" in data:
        if item_id in data["conflicts"]:
            raise HTTPException(status_code=422, detail="项目不能与自身互斥")
        validate_conflicts(db, admin.hospital_id, data["conflicts"])
    next_prerequisites = data.get("prerequisites", row.prerequisites)
    next_conflicts = data.get("conflicts", row.conflicts)
    prerequisite_ids = validate_prerequisites(db, admin.hospital_id, next_prerequisites)
    if item_id in prerequisite_ids:
        raise HTTPException(status_code=422, detail="项目不能以自身作为前置项目")
    overlap = set(prerequisite_ids) & set(next_conflicts or [])
    if overlap:
        raise HTTPException(status_code=422, detail=f"项目不能同时前置和互斥 {sorted(overlap)}")
    if data.get("isActive") is False:
        published_packages = [
            package for package in packages_using_item(db, admin.hospital_id, item_id) if package.is_published
        ]
        if published_packages:
            raise HTTPException(status_code=409, detail="项目仍被已上架套餐使用，请先下架或调整套餐")
    fields = {
        "deptID": "dept_id",
        "itemName": "item_name",
        "duration": "duration",
        "prerequisites": "prerequisites",
        "conflicts": "conflicts",
        "priority": "priority",
        "allowedTimeSlots": "allowed_time_slots",
        "isCritical": "is_critical",
        "isActive": "is_active",
    }
    for key, value in data.items():
        setattr(row, fields[key], value)
    try:
        db.flush()
        validate_hospital_prerequisite_graph(db, admin.hospital_id)
        for package in packages_using_item(db, admin.hospital_id, item_id):
            validate_package_items(
                db,
                admin.hospital_id,
                package.included_item_ids,
                require_active=package.is_published,
            )
        db.commit()
        return exam_dict(row)
    except HTTPException:
        db.rollback()
        raise


@router.delete("/exams/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_exam(
    item_id: str,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> Response:
    row = require_exam(db, admin.hospital_id, item_id)
    if db.scalar(select(func.count()).select_from(PlanExecutionDetail).where(PlanExecutionDetail.item_id == item_id)):
        raise HTTPException(status_code=409, detail="项目已有执行记录，请改为停用以保留历史数据")
    if packages_using_item(db, admin.hospital_id, item_id):
        raise HTTPException(status_code=409, detail="项目仍被体检套餐使用，请先调整套餐")
    db.delete(row)
    db.commit()
    return Response(status_code=204)


def require_package(db: Session, hospital_id: str, package_id: str) -> PackageInfo:
    row = db.scalar(
        select(PackageInfo).where(
            PackageInfo.package_id == package_id,
            PackageInfo.hospital_id == hospital_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="体检套餐不存在")
    return row


def validate_package_items(
    db: Session,
    hospital_id: str,
    item_ids: list[str],
    *,
    require_active: bool = False,
) -> None:
    rows = db.scalars(
        select(ExamInfo)
        .join(DepartmentInfo, DepartmentInfo.dept_id == ExamInfo.dept_id)
        .where(DepartmentInfo.hospital_id == hospital_id, ExamInfo.item_id.in_(set(item_ids)))
    ).all()
    validate_package_item_rows(item_ids, rows, require_active=require_active)


def validate_package_item_rows(
    item_ids: list[str],
    rows: list[ExamInfo],
    *,
    require_active: bool = False,
) -> None:
    if {row.item_id for row in rows} != set(item_ids):
        raise HTTPException(status_code=422, detail="套餐包含本医院不存在的检查项目")
    if require_active and any(not row.is_active for row in rows):
        raise HTTPException(status_code=422, detail="已上架套餐不能包含已停用的检查项目")
    try:
        validate_exam_selection(
            item_ids,
            {row.item_id: prerequisite_item_ids(row.prerequisites) for row in rows},
            {row.item_id: row.conflicts or [] for row in rows},
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"套餐项目组合无效：{exc}") from exc


@router.get("/packages")
def list_packages(admin: AdminContext = Depends(get_current_admin), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(PackageInfo)
        .where(PackageInfo.hospital_id == admin.hospital_id)
        .order_by(PackageInfo.create_time.desc())
    ).all()
    return [package_dict(row) for row in rows]


@router.post("/packages", status_code=status.HTTP_201_CREATED)
def create_package(
    payload: PackageCreate,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    item_ids = list(dict.fromkeys(payload.includedItemIDs))
    validate_package_items(db, admin.hospital_id, item_ids, require_active=payload.isPublished)
    duration = payload.defaultDuration or sum(
        db.scalars(select(ExamInfo.duration).where(ExamInfo.item_id.in_(item_ids)))
    )
    row = PackageInfo(
        hospital_id=admin.hospital_id,
        package_name=payload.packageName,
        package_type=payload.packageType,
        price=payload.price,
        tag=payload.tag,
        description=payload.description,
        included_item_ids=item_ids,
        default_duration=duration,
        suitable=payload.suitable,
        notice=payload.notice,
        is_published=payload.isPublished,
    )
    db.add(row)
    db.commit()
    return package_dict(row)


@router.patch("/packages/{package_id}")
def update_package(
    package_id: str,
    payload: PackageUpdate,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    row = require_package(db, admin.hospital_id, package_id)
    data = payload.model_dump(exclude_unset=True)
    if any(value is None for value in data.values()):
        raise HTTPException(status_code=422, detail="套餐字段不能设为空值")
    if "includedItemIDs" in data:
        data["includedItemIDs"] = list(dict.fromkeys(data["includedItemIDs"]))
    next_item_ids = data.get("includedItemIDs", row.included_item_ids)
    next_published = data.get("isPublished", row.is_published)
    validate_package_items(db, admin.hospital_id, next_item_ids, require_active=next_published)
    fields = {
        "packageName": "package_name",
        "packageType": "package_type",
        "price": "price",
        "tag": "tag",
        "description": "description",
        "includedItemIDs": "included_item_ids",
        "defaultDuration": "default_duration",
        "suitable": "suitable",
        "notice": "notice",
        "isPublished": "is_published",
    }
    for key, value in data.items():
        setattr(row, fields[key], value)
    if ("includedItemIDs" in data and "defaultDuration" not in data) or data.get("defaultDuration") == 0:
        row.default_duration = sum(
            db.scalars(select(ExamInfo.duration).where(ExamInfo.item_id.in_(row.included_item_ids)))
        )
    row.update_time = utcnow()
    db.commit()
    return package_dict(row)


def require_owner(admin: AdminContext) -> None:
    if not admin.is_owner:
        raise HTTPException(status_code=403, detail="仅医院创建者可使用演示患者工具")


@router.get("/demo-patients")
def get_demo_patients(admin: AdminContext = Depends(get_current_admin), db: Session = Depends(get_db)) -> dict:
    require_owner(admin)
    return demo_pool_summary(db, admin.hospital_id)


@router.post("/demo-patients/active")
def activate_demo_patients(
    payload: DemoPatientTarget,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    require_owner(admin)
    try:
        result = set_demo_patient_count(db, admin.hospital_id, payload.count)
        db.commit()
        return result
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="演示患者激活失败，请检查患者池与当前数据") from exc


@router.delete("/demo-patients/active")
def withdraw_demo_patients(
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    require_owner(admin)
    try:
        result = set_demo_patient_count(db, admin.hospital_id, 0)
        db.commit()
        return result
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="演示患者撤回失败，请稍后重试") from exc


@router.delete("/packages/{package_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_package(
    package_id: str,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> Response:
    row = require_package(db, admin.hospital_id, package_id)
    if db.scalar(select(func.count()).select_from(ExamPlan).where(ExamPlan.package_id == package_id)):
        raise HTTPException(status_code=409, detail="套餐已有体检计划，请下架套餐以保留历史记录")
    if db.scalar(
        select(func.count()).select_from(DemoPatientProfile).where(DemoPatientProfile.package_id == package_id)
    ):
        raise HTTPException(status_code=409, detail="套餐已用于注册时生成的演示患者池，请改为下架")
    db.delete(row)
    db.commit()
    return Response(status_code=204)


def _gis_route_distances(db: Session, hospital_id: str, geojson: dict) -> list[tuple[str, str, float]]:
    owned_departments = set(
        db.scalars(select(DepartmentInfo.dept_id).where(DepartmentInfo.hospital_id == hospital_id))
    )
    routes: list[tuple[str, str, float]] = []
    for feature in geojson.get("features", []):
        props = feature.get("properties") or {}
        if props.get("featureType") == "department":
            if props.get("deptID") not in owned_departments:
                raise HTTPException(status_code=422, detail="GIS 科室点位引用了本医院不存在的科室")
            continue
        if props.get("featureType") != "route":
            continue
        from_id, to_id = props.get("fromDeptID"), props.get("toDeptID")
        distance = props.get("distanceMeters")
        if from_id not in owned_departments or to_id not in owned_departments:
            raise HTTPException(status_code=422, detail="GIS 路线引用了本医院不存在的科室")
        if from_id == to_id:
            raise HTTPException(status_code=422, detail="GIS 路线起点和终点不能相同")
        if (
            not isinstance(distance, (int, float))
            or isinstance(distance, bool)
            or not isfinite(distance)
            or distance < 0
        ):
            raise HTTPException(status_code=422, detail="GIS 路线 distanceMeters 必须是非负有限数值")
        routes.append((from_id, to_id, float(distance)))
    return routes


def sync_distances(db: Session, hospital_id: str, geojson: dict) -> None:
    for from_id, to_id, distance in _gis_route_distances(db, hospital_id, geojson):
        row = db.scalar(
            select(DepartmentDistance).where(
                DepartmentDistance.from_dept_id == from_id,
                DepartmentDistance.to_dept_id == to_id,
            )
        )
        if row is None:
            db.add(DepartmentDistance(from_dept_id=from_id, to_dept_id=to_id, distance_meters=distance))
        else:
            row.distance_meters = distance


def workspace_import_id(hospital_id: str, resource: str, key: str) -> str:
    return uuid5(NAMESPACE_URL, f"checkup-schedule:{hospital_id}:{resource}:{key}").hex


def resolve_workspace_geojson(
    floor_key: str,
    geojson: dict,
    department_ids: dict[str, str],
    department_names: dict[str, str],
) -> dict:
    resolved = deepcopy(geojson)
    for feature in resolved.get("features", []):
        properties = feature.setdefault("properties", {})
        if properties.get("featureType") == "department":
            department_key = properties.pop("departmentKey")
            properties["deptID"] = department_ids[department_key]
            properties.setdefault("name", department_names[department_key])
        elif properties.get("featureType") == "route":
            from_key = properties.pop("fromDepartmentKey")
            to_key = properties.pop("toDepartmentKey")
            distance = properties.get("distanceMeters")
            if (
                not isinstance(distance, (int, float))
                or isinstance(distance, bool)
                or not isfinite(distance)
                or distance < 0
            ):
                raise HTTPException(status_code=422, detail=f"GIS {floor_key} 的路线 distanceMeters 必须是非负数")
            properties["fromDeptID"] = department_ids[from_key]
            properties["toDeptID"] = department_ids[to_key]
    return resolved


def workspace_import_template() -> dict:
    return {
        "formatVersion": "1.0",
        "mode": "upsert",
        "departments": [
            {
                "key": "ultrasound",
                "deptName": "超声科",
                "location": "1F-A12",
                "openTimeStart": "08:00",
                "openTimeEnd": "17:00",
                "capacity": 2,
                "isAvailable": True,
            },
            {
                "key": "laboratory",
                "deptName": "检验科",
                "location": "1F-B06",
                "openTimeStart": "07:30",
                "openTimeEnd": "16:30",
                "capacity": 4,
                "isAvailable": True,
            },
        ],
        "exams": [
            {
                "key": "blood-routine",
                "departmentKey": "laboratory",
                "itemName": "血常规",
                "duration": 8,
                "prerequisites": {"fastingHours": 8},
                "prerequisiteItemKeys": [],
                "conflictItemKeys": [],
                "priority": 8,
                "allowedTimeSlots": {"start": "07:30", "end": "11:00"},
                "isCritical": True,
                "isActive": True,
            },
            {
                "key": "abdominal-ultrasound",
                "departmentKey": "ultrasound",
                "itemName": "腹部超声",
                "duration": 15,
                "prerequisites": {"fastingHours": 8},
                "prerequisiteItemKeys": ["blood-routine"],
                "conflictItemKeys": [],
                "priority": 6,
                "allowedTimeSlots": {"start": "08:00", "end": "11:30"},
                "isCritical": True,
                "isActive": True,
            },
        ],
        "packages": [
            {
                "key": "basic",
                "packageName": "基础体检套餐",
                "packageType": "健康体检",
                "price": 399,
                "tag": "热门",
                "description": "覆盖常规检验与腹部超声",
                "includedItemKeys": ["blood-routine", "abdominal-ultrasound"],
                "defaultDuration": 0,
                "suitable": ["18 岁以上人群"],
                "notice": ["检查前保持空腹"],
                "isPublished": True,
            },
            {
                "key": "lab-only",
                "packageName": "基础检验套餐",
                "packageType": "专项体检",
                "price": 99,
                "tag": "快捷",
                "description": "基础实验室检查",
                "includedItemKeys": ["blood-routine"],
                "defaultDuration": 0,
                "suitable": [],
                "notice": ["检查前保持空腹"],
                "isPublished": False,
            },
        ],
        "gis": [
            {
                "floorKey": "1F",
                "geojson": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "properties": {"featureType": "department", "departmentKey": "ultrasound"},
                            "geometry": {"type": "Point", "coordinates": [20, 30]},
                        },
                        {
                            "type": "Feature",
                            "properties": {"featureType": "department", "departmentKey": "laboratory"},
                            "geometry": {"type": "Point", "coordinates": [80, 30]},
                        },
                        {
                            "type": "Feature",
                            "properties": {
                                "featureType": "route",
                                "fromDepartmentKey": "laboratory",
                                "toDepartmentKey": "ultrasound",
                                "distanceMeters": 60,
                            },
                            "geometry": {"type": "LineString", "coordinates": [[80, 30], [20, 30]]},
                        },
                    ],
                },
            }
        ],
    }


@router.get("/imports/template")
def get_workspace_import_template(_admin: AdminContext = Depends(get_current_admin)) -> dict:
    return workspace_import_template()


def _apply_workspace_import(
    payload: WorkspaceImport,
    admin: AdminContext,
    db: Session,
    *,
    commit: bool = True,
) -> dict:
    department_ids = {
        row.key: workspace_import_id(admin.hospital_id, "department", row.key) for row in payload.departments
    }
    item_ids = {row.key: workspace_import_id(admin.hospital_id, "exam", row.key) for row in payload.exams}
    package_ids = {
        row.key: workspace_import_id(admin.hospital_id, "package", row.key) for row in payload.packages
    }
    summary = {
        "hospital": {"updated": 0},
        "departments": {"created": 0, "updated": 0},
        "exams": {"created": 0, "updated": 0},
        "packages": {"created": 0, "updated": 0},
        "gis": {"created": 0, "updated": 0},
    }
    try:
        if payload.hospital is not None:
            hospital = db.get(HospitalInfo, admin.hospital_id)
            hospital.hospital_name = payload.hospital.hospitalName
            hospital.address = payload.hospital.address
            hospital.open_time = payload.hospital.openTime
            hospital.floor_map_url = payload.hospital.floorMapUrl
            settings = get_hospital_settings(db, admin.hospital_id, create=True)
            settings.cover_image_url = payload.hospital.coverImageUrl
            settings.hospital_level = payload.hospital.hospitalLevel
            settings.positioning = payload.hospital.positioning
            settings.is_available = payload.hospital.isAvailable
            settings.appointment_slot_minutes = payload.hospital.appointmentSlotMinutes
            settings.appointment_slot_capacity = payload.hospital.appointmentSlotCapacity
            settings.appointment_days_ahead = payload.hospital.appointmentDaysAhead
            summary["hospital"]["updated"] = 1

        for imported in payload.departments:
            row = db.get(DepartmentInfo, department_ids[imported.key])
            if row is None:
                row = DepartmentInfo(dept_id=department_ids[imported.key], hospital_id=admin.hospital_id)
                db.add(row)
                summary["departments"]["created"] += 1
            else:
                if row.hospital_id != admin.hospital_id:
                    raise HTTPException(status_code=409, detail=f"科室 key 冲突：{imported.key}")
                summary["departments"]["updated"] += 1
            row.dept_name = imported.deptName
            row.location = imported.location
            row.open_time_start = imported.openTimeStart
            row.open_time_end = imported.openTimeEnd
            row.capacity = imported.capacity
            row.is_available = imported.isAvailable
        db.flush()

        for imported in payload.exams:
            row = db.get(ExamInfo, item_ids[imported.key])
            if row is None:
                row = ExamInfo(item_id=item_ids[imported.key])
                db.add(row)
                summary["exams"]["created"] += 1
            else:
                current_department = db.get(DepartmentInfo, row.dept_id)
                if current_department is None or current_department.hospital_id != admin.hospital_id:
                    raise HTTPException(status_code=409, detail=f"检查项目 key 冲突：{imported.key}")
                summary["exams"]["updated"] += 1
            prerequisites = dict(imported.prerequisites)
            if imported.prerequisiteItemKeys:
                prerequisites["itemIDs"] = [item_ids[key] for key in imported.prerequisiteItemKeys]
            row.dept_id = department_ids[imported.departmentKey]
            row.item_name = imported.itemName
            row.duration = imported.duration
            row.prerequisites = prerequisites
            row.conflicts = [item_ids[key] for key in imported.conflictItemKeys]
            row.priority = imported.priority
            row.allowed_time_slots = imported.allowedTimeSlots
            row.is_critical = imported.isCritical
            row.is_active = imported.isActive
        db.flush()

        duration_by_key = {row.key: row.duration for row in payload.exams}
        for imported in payload.packages:
            row = db.get(PackageInfo, package_ids[imported.key])
            if row is None:
                row = PackageInfo(package_id=package_ids[imported.key], hospital_id=admin.hospital_id)
                db.add(row)
                summary["packages"]["created"] += 1
            else:
                if row.hospital_id != admin.hospital_id:
                    raise HTTPException(status_code=409, detail=f"套餐 key 冲突：{imported.key}")
                summary["packages"]["updated"] += 1
            row.package_name = imported.packageName
            row.package_type = imported.packageType
            row.price = imported.price
            row.tag = imported.tag
            row.description = imported.description
            row.included_item_ids = [item_ids[key] for key in imported.includedItemKeys]
            row.default_duration = imported.defaultDuration or sum(
                duration_by_key[key] for key in imported.includedItemKeys
            )
            row.suitable = imported.suitable
            row.notice = imported.notice
            row.is_published = imported.isPublished
            row.update_time = utcnow()
        db.flush()

        hospital_exam_rows = db.scalars(
            select(ExamInfo)
            .join(DepartmentInfo, DepartmentInfo.dept_id == ExamInfo.dept_id)
            .where(DepartmentInfo.hospital_id == admin.hospital_id)
        ).all()
        exams_by_id = {exam.item_id: exam for exam in hospital_exam_rows}
        for package in db.scalars(
            select(PackageInfo).where(PackageInfo.hospital_id == admin.hospital_id)
        ):
            package_item_ids = list(package.included_item_ids or [])
            package_exams = [exams_by_id[item_id] for item_id in package_item_ids if item_id in exams_by_id]
            try:
                validate_package_item_rows(
                    package_item_ids,
                    package_exams,
                    require_active=package.is_published,
                )
            except HTTPException as exc:
                raise HTTPException(
                    status_code=exc.status_code,
                    detail=f"套餐“{package.package_name}”的项目组合无效：{exc.detail}",
                ) from exc

        department_names = {row.key: row.deptName for row in payload.departments}
        gis_versions: dict[str, int] = {}
        for imported in payload.gis:
            geojson = resolve_workspace_geojson(
                imported.floorKey,
                imported.geojson,
                department_ids,
                department_names,
            )
            row = db.scalar(
                select(HospitalGIS).where(
                    HospitalGIS.hospital_id == admin.hospital_id,
                    HospitalGIS.floor_key == imported.floorKey,
                )
            )
            if row is None:
                row = HospitalGIS(
                    hospital_id=admin.hospital_id,
                    floor_key=imported.floorKey,
                    geojson=geojson,
                    updated_by=admin.user_id,
                )
                db.add(row)
                summary["gis"]["created"] += 1
            else:
                row.geojson = geojson
                row.version += 1
                row.updated_by = admin.user_id
                row.update_time = utcnow()
                summary["gis"]["updated"] += 1
            sync_distances(db, admin.hospital_id, geojson)
            db.flush()
            gis_versions[imported.floorKey] = row.version
        if commit:
            db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="导入内容与现有数据冲突，请检查科室名称和业务 key") from exc

    return {
        "formatVersion": payload.formatVersion,
        "mode": payload.mode,
        "summary": summary,
        "ids": {"departments": department_ids, "exams": item_ids, "packages": package_ids},
        "gisVersions": gis_versions,
    }


@router.post("/imports/workspace")
def import_workspace(
    payload: WorkspaceImport,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    return _apply_workspace_import(payload, admin, db)


@router.get("/gis")
def list_gis(admin: AdminContext = Depends(get_current_admin), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(HospitalGIS)
        .where(HospitalGIS.hospital_id == admin.hospital_id)
        .order_by(HospitalGIS.floor_key)
    ).all()
    return [
        {"floorKey": row.floor_key, "version": row.version, "updateTime": iso(row.update_time)}
        for row in rows
    ]


@router.get("/gis/{floor_key}")
def get_gis(
    floor_key: str,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    row = db.scalar(
        select(HospitalGIS).where(
            HospitalGIS.hospital_id == admin.hospital_id,
            HospitalGIS.floor_key == floor_key,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="该楼层尚未上传 GIS")
    return {
        "floorKey": row.floor_key,
        "version": row.version,
        "geojson": row.geojson,
        "updateTime": iso(row.update_time),
    }


@router.put("/gis/{floor_key}")
def put_gis(
    floor_key: str,
    payload: GISUpload,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    if not floor_key.strip() or len(floor_key) > 64:
        raise HTTPException(status_code=422, detail="楼层标识不能为空且最长 64 字符")
    row = db.scalar(
        select(HospitalGIS).where(
            HospitalGIS.hospital_id == admin.hospital_id,
            HospitalGIS.floor_key == floor_key,
        )
    )
    if row is None:
        row = HospitalGIS(
            hospital_id=admin.hospital_id,
            floor_key=floor_key,
            geojson=payload.geojson,
            updated_by=admin.user_id,
        )
        db.add(row)
    else:
        row.geojson = payload.geojson
        row.version += 1
        row.updated_by = admin.user_id
        row.update_time = utcnow()
    sync_distances(db, admin.hospital_id, payload.geojson)
    db.commit()
    return {"floorKey": row.floor_key, "version": row.version, "updateTime": iso(row.update_time)}


@router.get("/anomalies")
def list_anomalies(
    resolved: bool | None = None,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    query = (
        select(AnomalyReport, DepartmentInfo.dept_name)
        .join(DepartmentInfo, DepartmentInfo.dept_id == AnomalyReport.dept_id)
        .where(DepartmentInfo.hospital_id == admin.hospital_id)
        .order_by(AnomalyReport.report_time.desc())
    )
    if resolved is not None:
        query = query.where(AnomalyReport.is_resolved == resolved)
    return [anomaly_dict(row, name) for row, name in db.execute(query).all()]


@router.post("/anomalies", status_code=status.HTTP_201_CREATED)
def create_anomaly(
    payload: AnomalyCreate,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    department = require_department(db, admin.hospital_id, payload.deptID)
    row = AnomalyReport(
        dept_id=payload.deptID,
        reporter_id=admin.user_id,
        anomaly_type=payload.anomalyType,
        description=payload.description,
    )
    if payload.anomalyType in {"科室关闭", "设备故障"}:
        department.is_available = False
    db.add(row)
    db.commit()
    return anomaly_dict(row, department.dept_name)


@router.post("/anomalies/{report_id}/resolve")
def resolve_anomaly(
    report_id: str,
    payload: AnomalyResolve,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    result = db.execute(
        select(AnomalyReport, DepartmentInfo)
        .join(DepartmentInfo, DepartmentInfo.dept_id == AnomalyReport.dept_id)
        .where(AnomalyReport.report_id == report_id, DepartmentInfo.hospital_id == admin.hospital_id)
    ).one_or_none()
    if result is None:
        raise HTTPException(status_code=404, detail="异常记录不存在")
    anomaly, department = result
    anomaly.is_resolved = True
    if payload.reopenDepartment:
        remaining = db.scalar(
            select(func.count())
            .select_from(AnomalyReport)
            .where(
                AnomalyReport.dept_id == department.dept_id,
                AnomalyReport.is_resolved.is_(False),
                AnomalyReport.report_id != report_id,
                AnomalyReport.anomaly_type.in_(["科室关闭", "设备故障"]),
            )
        )
        if not remaining:
            department.is_available = True
    db.commit()
    return anomaly_dict(anomaly, department.dept_name)


@router.get("/queues")
def list_queues(admin: AdminContext = Depends(get_current_admin), db: Session = Depends(get_db)) -> list[dict]:
    now = utcnow()
    rows = system_item_queues(db, admin.hospital_id, now=now)
    return [
        {
            "snapshotID": None,
            "itemID": row.item_id,
            "itemName": row.item_name,
            "queueCount": row.queue_count,
            "activeCount": row.active_count,
            "estimatedWaitTime": row.estimated_wait_time,
            "dataSource": "system",
            "validUntil": None,
            "createTime": iso(now),
        }
        for row in sorted(rows.values(), key=lambda item: (item.item_name, item.item_id))
    ]


def current_flow(db: Session, hospital_id: str) -> list[dict]:
    departments = db.scalars(
        select(DepartmentInfo).where(DepartmentInfo.hospital_id == hospital_id).order_by(DepartmentInfo.dept_name)
    ).all()
    by_id = {
        row.dept_id: {
            "deptID": row.dept_id,
            "deptName": row.dept_name,
            "location": row.location,
            "isAvailable": row.is_available,
            "queueCount": 0,
            "activeCount": 0,
            "estimatedWaitTime": 0,
            "peopleFlow": 0,
        }
        for row in departments
    }
    for dept_id, state in system_department_queues(db, hospital_id).items():
        if dept_id in by_id:
            by_id[dept_id]["queueCount"] = state.waiting_count
            by_id[dept_id]["activeCount"] = state.active_count
            by_id[dept_id]["estimatedWaitTime"] = state.estimated_wait_time
    for item in by_id.values():
        item["peopleFlow"] = item["queueCount"] + item["activeCount"]
    return list(by_id.values())


@router.get("/plans")
def list_hospital_plans(
    date_scope: str = Query(default="today", alias="date", pattern="^(today|all)$"),
    plan_status: str = Query(default="all", alias="status", max_length=20),
    query: str = Query(default="", max_length=100),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    allowed_statuses = {"all", "待执行", "进行中", "已中断", "已完成", "已结束"}
    if plan_status not in allowed_statuses:
        raise HTTPException(status_code=422, detail="计划状态筛选值无效")

    service_time = func.coalesce(ExamPlan.appointment_at, ExamPlan.generate_time)
    filters = [ExamPlan.hospital_id == admin.hospital_id]
    if date_scope == "today":
        start, end = local_day_bounds_utc(utcnow())
        filters.extend([service_time >= start, service_time < end])
    if plan_status != "all":
        filters.append(ExamPlan.plan_status == plan_status)
    clean_query = query.strip()
    if clean_query:
        filters.append(
            or_(
                ExamPlan.plan_id.contains(clean_query, autoescape=True),
                UserInfo.name.contains(clean_query, autoescape=True),
                UserInfo.phone.contains(clean_query, autoescape=True),
            )
        )

    total = db.scalar(
        select(func.count())
        .select_from(ExamPlan)
        .join(UserInfo, UserInfo.user_id == ExamPlan.user_id)
        .where(*filters)
    ) or 0
    rows = db.execute(
        select(ExamPlan, UserInfo, PackageInfo)
        .join(UserInfo, UserInfo.user_id == ExamPlan.user_id)
        .outerjoin(PackageInfo, PackageInfo.package_id == ExamPlan.package_id)
        .where(*filters)
        .order_by(service_time.desc(), ExamPlan.generate_time.desc(), ExamPlan.plan_id)
        .limit(limit)
        .offset(offset)
    ).all()

    plan_ids = [plan.plan_id for plan, _user, _package in rows]
    details_by_plan: dict[str, list[tuple[PlanExecutionDetail, ExamInfo, DepartmentInfo]]] = {}
    if plan_ids:
        detail_rows = db.execute(
            select(PlanExecutionDetail, ExamInfo, DepartmentInfo)
            .join(ExamInfo, ExamInfo.item_id == PlanExecutionDetail.item_id)
            .join(DepartmentInfo, DepartmentInfo.dept_id == ExamInfo.dept_id)
            .where(PlanExecutionDetail.plan_id.in_(plan_ids))
            .order_by(PlanExecutionDetail.plan_id, PlanExecutionDetail.step_order)
        ).all()
        for detail_row in detail_rows:
            details_by_plan.setdefault(detail_row[0].plan_id, []).append(detail_row)

    items = []
    for plan, user, package in rows:
        details = details_by_plan.get(plan.plan_id, [])
        completed = sum(detail.exec_status == "已完成" for detail, _exam, _department in details)
        current = next(
            (row for row in details if row[0].exec_status == "进行中"),
            next((row for row in details if row[0].exec_status == "待开始"), None),
        )
        items.append(
            {
                "planID": plan.plan_id,
                "patient": {
                    "userID": user.user_id,
                    "name": user.name,
                    "phone": user.phone,
                },
                "packageName": package.package_name if package else "自选项目",
                "appointmentAt": iso(plan.appointment_at),
                "serviceAt": iso(plan.appointment_at or plan.generate_time),
                "generatedAt": iso(plan.generate_time),
                "status": plan.plan_status,
                "completedSteps": completed,
                "totalSteps": len(details),
                "progress": round(completed / len(details) * 100) if details else 0,
                "currentStep": (
                    {
                        "detailID": current[0].detail_id,
                        "itemID": current[1].item_id,
                        "itemName": current[1].item_name,
                        "department": current[2].dept_name,
                        "status": current[0].exec_status,
                        "estimatedStart": iso(current[0].estimated_start),
                    }
                    if current
                    else None
                ),
            }
        )
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "date": date_scope,
    }


@router.get("/dashboard/summary")
def dashboard_summary(admin: AdminContext = Depends(get_current_admin), db: Session = Depends(get_db)) -> dict:
    now = utcnow()
    start, end = local_day_bounds_utc(now)
    departments = db.scalars(select(DepartmentInfo).where(DepartmentInfo.hospital_id == admin.hospital_id)).all()
    unresolved = db.scalar(
        select(func.count())
        .select_from(AnomalyReport)
        .join(DepartmentInfo, DepartmentInfo.dept_id == AnomalyReport.dept_id)
        .where(DepartmentInfo.hospital_id == admin.hospital_id, AnomalyReport.is_resolved.is_(False))
    ) or 0
    service_time = func.coalesce(ExamPlan.appointment_at, ExamPlan.generate_time)
    plan_counts = dict(
        db.execute(
            select(ExamPlan.plan_status, func.count())
            .where(
                ExamPlan.hospital_id == admin.hospital_id,
                service_time >= start,
                service_time < end,
            )
            .group_by(ExamPlan.plan_status)
        ).all()
    )
    flow = current_flow(db, admin.hospital_id)
    active_flow = [row for row in flow if row["peopleFlow"] > 0]
    today_served = db.scalar(
        select(func.count())
        .select_from(PlanExecutionDetail)
        .join(ExamPlan, ExamPlan.plan_id == PlanExecutionDetail.plan_id)
        .where(
            ExamPlan.hospital_id == admin.hospital_id,
            PlanExecutionDetail.actual_end >= start,
            PlanExecutionDetail.actual_end < end,
        )
    ) or 0
    return {
        "metrics": {
            "todayPlans": sum(plan_counts.values()),
            "inProgressPlans": plan_counts.get("进行中", 0),
            "completedPlans": plan_counts.get("已完成", 0),
            "openDepartments": sum(1 for row in departments if row.is_available),
            "departmentCount": len(departments),
            "unresolvedAnomalies": unresolved,
            "averageWaitSeconds": round(
                sum(row["estimatedWaitTime"] for row in active_flow) / len(active_flow)
            ) if active_flow else 0,
            "todayServed": int(today_served),
        },
        "flow": flow,
        "generatedAt": iso(utcnow()),
    }


@router.get("/dashboard/map/{floor_key}")
def dashboard_map(
    floor_key: str,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    gis = db.scalar(
        select(HospitalGIS).where(
            HospitalGIS.hospital_id == admin.hospital_id,
            HospitalGIS.floor_key == floor_key,
        )
    )
    if gis is None:
        raise HTTPException(status_code=404, detail="该楼层尚未上传 GIS")
    return {
        "floorKey": floor_key,
        "version": gis.version,
        "geojson": gis.geojson,
        "flow": current_flow(db, admin.hospital_id),
        "generatedAt": iso(utcnow()),
    }
