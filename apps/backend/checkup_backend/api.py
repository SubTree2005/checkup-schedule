from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from math import isfinite
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import get_db
from .models import (
    AnomalyReport,
    DepartmentDistance,
    DepartmentInfo,
    DepartmentWaitingStats,
    ExamInfo,
    ExamPlan,
    HospitalAdmin,
    HospitalGIS,
    HospitalInfo,
    PackageInfo,
    PlanExecutionDetail,
    QueueSnapshot,
    UserInfo,
    utcnow,
)
from .schemas import (
    AnomalyCreate,
    AnomalyResolve,
    DepartmentCreate,
    DepartmentUpdate,
    ExamCreate,
    ExamUpdate,
    GISUpload,
    HospitalRegister,
    HospitalUpdate,
    LoginRequest,
    PackageCreate,
    PackageUpdate,
    QueueUpdate,
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
    verify_password,
)
from .serializers import anomaly_dict, department_dict, exam_dict, hospital_dict, iso, package_dict, queue_dict

router = APIRouter(prefix="/api")


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()[:64]
    return request.client.host[:64] if request.client else None


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


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "time": iso(utcnow())}


@router.post("/auth/register", status_code=status.HTTP_201_CREATED)
def register_hospital(
    payload: HospitalRegister,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    if db.scalar(select(UserInfo.user_id).where(UserInfo.phone == payload.phone)):
        raise HTTPException(status_code=409, detail="该手机号已注册")
    hospital = HospitalInfo(
        hospital_name=payload.hospitalName,
        address=payload.address,
        open_time=payload.openTime,
    )
    user = UserInfo(
        phone=payload.phone,
        password=hash_password(payload.password),
        name=payload.adminName,
        role="管理员",
    )
    db.add_all([hospital, user])
    db.flush()
    db.add(HospitalAdmin(user_id=user.user_id, hospital_id=hospital.hospital_id, is_owner=True))
    token = issue_session(db, user.user_id, client_ip(request))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="医院账号注册冲突") from exc
    set_session_cookie(response, token)
    return {
        "user": {"userID": user.user_id, "name": user.name, "phone": user.phone, "role": user.role},
        "hospital": hospital_dict(hospital),
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
    if user is None or membership is None or not verify_password(payload.password, user.password):
        raise HTTPException(status_code=401, detail="手机号或密码错误")
    hospital = db.get(HospitalInfo, membership.hospital_id)
    token = issue_session(db, user.user_id, client_ip(request))
    db.commit()
    set_session_cookie(response, token)
    return {
        "user": {"userID": user.user_id, "name": user.name, "phone": user.phone, "role": user.role},
        "hospital": hospital_dict(hospital),
    }


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    db: Session = Depends(get_db),
) -> Response:
    revoke_session(db, token)
    db.commit()
    clear_session_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/auth/me")
def me(admin: AdminContext = Depends(get_current_admin), db: Session = Depends(get_db)) -> dict:
    hospital = db.get(HospitalInfo, admin.hospital_id)
    return {
        "user": {
            "userID": admin.user_id,
            "name": admin.name,
            "phone": admin.phone,
            "role": "管理员",
            "isOwner": admin.is_owner,
        },
        "hospital": hospital_dict(hospital),
    }


@router.get("/hospital")
def get_hospital(admin: AdminContext = Depends(get_current_admin), db: Session = Depends(get_db)) -> dict:
    return hospital_dict(db.get(HospitalInfo, admin.hospital_id))


@router.patch("/hospital")
def update_hospital(
    payload: HospitalUpdate,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(HospitalInfo, admin.hospital_id)
    fields = {
        "hospitalName": "hospital_name",
        "address": "address",
        "openTime": "open_time",
        "floorMapUrl": "floor_map_url",
    }
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, fields[key], value)
    db.commit()
    return hospital_dict(row)


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
    fields = {
        "deptName": "dept_name",
        "location": "location",
        "openTimeStart": "open_time_start",
        "openTimeEnd": "open_time_end",
        "capacity": "capacity",
        "isAvailable": "is_available",
    }
    for key, value in payload.model_dump(exclude_unset=True).items():
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
    db.commit()
    return exam_dict(row)


@router.patch("/exams/{item_id}")
def update_exam(
    item_id: str,
    payload: ExamUpdate,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    row = require_exam(db, admin.hospital_id, item_id)
    data = payload.model_dump(exclude_unset=True)
    if "deptID" in data:
        require_department(db, admin.hospital_id, data["deptID"])
    if "conflicts" in data:
        if item_id in data["conflicts"]:
            raise HTTPException(status_code=422, detail="项目不能与自身互斥")
        validate_conflicts(db, admin.hospital_id, data["conflicts"])
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
    db.commit()
    return exam_dict(row)


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
    rows = db.execute(
        select(ExamInfo.item_id, ExamInfo.is_active)
        .join(DepartmentInfo, DepartmentInfo.dept_id == ExamInfo.dept_id)
        .where(DepartmentInfo.hospital_id == hospital_id, ExamInfo.item_id.in_(set(item_ids)))
    ).all()
    if {item_id for item_id, _is_active in rows} != set(item_ids):
        raise HTTPException(status_code=422, detail="套餐包含本医院不存在的检查项目")
    if require_active and any(not is_active for _item_id, is_active in rows):
        raise HTTPException(status_code=422, detail="已上架套餐不能包含已停用的检查项目")


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


@router.delete("/packages/{package_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_package(
    package_id: str,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> Response:
    row = require_package(db, admin.hospital_id, package_id)
    if db.scalar(select(func.count()).select_from(ExamPlan).where(ExamPlan.package_id == package_id)):
        raise HTTPException(status_code=409, detail="套餐已有体检计划，请下架套餐以保留历史记录")
    db.delete(row)
    db.commit()
    return Response(status_code=204)


def sync_distances(db: Session, hospital_id: str, geojson: dict) -> None:
    owned_departments = set(
        db.scalars(select(DepartmentInfo.dept_id).where(DepartmentInfo.hospital_id == hospital_id))
    )
    for feature in geojson.get("features", []):
        props = feature.get("properties") or {}
        if props.get("featureType") != "route":
            continue
        from_id, to_id = props.get("fromDeptID"), props.get("toDeptID")
        distance = props.get("distanceMeters")
        if from_id not in owned_departments or to_id not in owned_departments or not isinstance(distance, (int, float)):
            continue
        row = db.scalar(
            select(DepartmentDistance).where(
                DepartmentDistance.from_dept_id == from_id,
                DepartmentDistance.to_dept_id == to_id,
            )
        )
        if row is None:
            db.add(DepartmentDistance(from_dept_id=from_id, to_dept_id=to_id, distance_meters=float(distance)))
        else:
            row.distance_meters = float(distance)


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


@router.post("/imports/workspace")
def import_workspace(
    payload: WorkspaceImport,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db),
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

        active_item_ids = set(
            db.scalars(
                select(ExamInfo.item_id)
                .join(DepartmentInfo, DepartmentInfo.dept_id == ExamInfo.dept_id)
                .where(DepartmentInfo.hospital_id == admin.hospital_id, ExamInfo.is_active.is_(True))
            )
        )
        for package in db.scalars(
            select(PackageInfo).where(
                PackageInfo.hospital_id == admin.hospital_id,
                PackageInfo.is_published.is_(True),
            )
        ):
            if not set(package.included_item_ids or []).issubset(active_item_ids):
                raise HTTPException(
                    status_code=422,
                    detail=f"已上架套餐“{package.package_name}”包含已停用或不存在的检查项目",
                )

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
    rows = db.execute(
        select(QueueSnapshot, ExamInfo.item_name)
        .join(ExamInfo, ExamInfo.item_id == QueueSnapshot.item_id)
        .join(DepartmentInfo, DepartmentInfo.dept_id == ExamInfo.dept_id)
        .where(DepartmentInfo.hospital_id == admin.hospital_id, QueueSnapshot.valid_until > now)
        .order_by(QueueSnapshot.item_id, QueueSnapshot.create_time.desc())
    ).all()
    latest: dict[str, dict] = {}
    for snapshot, item_name in rows:
        latest.setdefault(snapshot.item_id, queue_dict(snapshot, item_name))
    return list(latest.values())


@router.post("/queues", status_code=status.HTTP_201_CREATED)
def update_queue(
    payload: QueueUpdate,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    exam = require_exam(db, admin.hospital_id, payload.itemID)
    row = QueueSnapshot(
        item_id=payload.itemID,
        queue_count=payload.queueCount,
        estimated_wait_time=payload.estimatedWaitTime,
        data_source="manual",
        valid_until=utcnow() + timedelta(minutes=payload.validMinutes),
    )
    db.add(row)
    db.commit()
    return queue_dict(row, exam.item_name)


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
    now = utcnow()
    queue_rows = db.execute(
        select(QueueSnapshot, ExamInfo.dept_id)
        .join(ExamInfo, ExamInfo.item_id == QueueSnapshot.item_id)
        .join(DepartmentInfo, DepartmentInfo.dept_id == ExamInfo.dept_id)
        .where(DepartmentInfo.hospital_id == hospital_id, QueueSnapshot.valid_until > now)
        .order_by(QueueSnapshot.item_id, QueueSnapshot.create_time.desc())
    ).all()
    seen_items: set[str] = set()
    for snapshot, dept_id in queue_rows:
        if snapshot.item_id in seen_items:
            continue
        seen_items.add(snapshot.item_id)
        by_id[dept_id]["queueCount"] += snapshot.queue_count
        by_id[dept_id]["estimatedWaitTime"] = max(
            by_id[dept_id]["estimatedWaitTime"], snapshot.estimated_wait_time
        )
    active_rows = db.execute(
        select(ExamInfo.dept_id, func.count())
        .select_from(PlanExecutionDetail)
        .join(ExamPlan, ExamPlan.plan_id == PlanExecutionDetail.plan_id)
        .join(ExamInfo, ExamInfo.item_id == PlanExecutionDetail.item_id)
        .where(ExamPlan.hospital_id == hospital_id, PlanExecutionDetail.exec_status == "进行中")
        .group_by(ExamInfo.dept_id)
    ).all()
    for dept_id, count in active_rows:
        if dept_id in by_id:
            by_id[dept_id]["activeCount"] = count
    for item in by_id.values():
        item["peopleFlow"] = item["queueCount"] + item["activeCount"]
    return list(by_id.values())


@router.get("/dashboard/summary")
def dashboard_summary(admin: AdminContext = Depends(get_current_admin), db: Session = Depends(get_db)) -> dict:
    today = utcnow().date().isoformat()
    start = datetime.fromisoformat(today)
    end = start + timedelta(days=1)
    departments = db.scalars(select(DepartmentInfo).where(DepartmentInfo.hospital_id == admin.hospital_id)).all()
    unresolved = db.scalar(
        select(func.count())
        .select_from(AnomalyReport)
        .join(DepartmentInfo, DepartmentInfo.dept_id == AnomalyReport.dept_id)
        .where(DepartmentInfo.hospital_id == admin.hospital_id, AnomalyReport.is_resolved.is_(False))
    ) or 0
    plan_counts = dict(
        db.execute(
            select(ExamPlan.plan_status, func.count())
            .where(
                ExamPlan.hospital_id == admin.hospital_id,
                ExamPlan.generate_time >= start,
                ExamPlan.generate_time < end,
            )
            .group_by(ExamPlan.plan_status)
        ).all()
    )
    wait_stats = db.execute(
        select(func.avg(DepartmentWaitingStats.avg_wait_time), func.sum(DepartmentWaitingStats.total_served))
        .join(DepartmentInfo, DepartmentInfo.dept_id == DepartmentWaitingStats.dept_id)
        .where(DepartmentInfo.hospital_id == admin.hospital_id, DepartmentWaitingStats.stat_date == today)
    ).one()
    return {
        "metrics": {
            "todayPlans": sum(plan_counts.values()),
            "inProgressPlans": plan_counts.get("进行中", 0),
            "completedPlans": plan_counts.get("已完成", 0),
            "openDepartments": sum(1 for row in departments if row.is_available),
            "departmentCount": len(departments),
            "unresolvedAnomalies": unresolved,
            "averageWaitSeconds": round(float(wait_stats[0] or 0)),
            "todayServed": int(wait_stats[1] or 0),
        },
        "flow": current_flow(db, admin.hospital_id),
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
