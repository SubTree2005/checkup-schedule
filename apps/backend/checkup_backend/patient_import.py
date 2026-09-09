"""Owner-only import of explicitly fictional patient histories and reports."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .api import require_owner
from .database import get_db
from .models import (DepartmentInfo, ExamInfo, ExamPlan, HospitalAdmin, HospitalInfo,
                     PackageInfo, PlanExecutionDetail, UserInfo, UserSession, UserStatusInfo, utcnow)
from .security import AdminContext, get_current_admin, hash_password, verify_login_password
from .serializers import iso

router = APIRouter(prefix="/api/demo-patients", tags=["demo-patients"])


class ImportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DemoResult(ImportModel):
    label: str = Field(min_length=1, max_length=100)
    value: str = Field(max_length=300)
    unit: str = Field(default="", max_length=40)
    reference: str = Field(default="", max_length=120)
    status: Literal["正常", "偏高", "偏低", "提示", ""] = ""


class DemoReport(ImportModel):
    conclusion: str = Field(min_length=1, max_length=3000)
    reportedAt: datetime
    items: list[DemoResult] = Field(default_factory=list, max_length=40)

    @field_validator("reportedAt")
    @classmethod
    def utc_time(cls, value):
        if value.tzinfo is None:
            raise ValueError("报告时间必须包含时区，例如 +08:00")
        return value.astimezone(timezone.utc).replace(tzinfo=None)


class DemoStep(ImportModel):
    itemName: str = Field(min_length=1, max_length=200)
    departmentName: str | None = Field(default=None, max_length=200)
    startedAt: datetime
    completedAt: datetime
    report: DemoReport | None = None

    @field_validator("startedAt", "completedAt")
    @classmethod
    def utc_time(cls, value):
        if value.tzinfo is None:
            raise ValueError("检查时间必须包含时区，例如 +08:00")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    @model_validator(mode="after")
    def chronological(self):
        if self.completedAt <= self.startedAt:
            raise ValueError("检查结束时间必须晚于开始时间")
        if self.report and self.report.reportedAt < self.completedAt:
            raise ValueError("报告时间不能早于检查结束时间")
        return self


class DemoVisit(ImportModel):
    recordKey: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=100)
    packageName: str | None = Field(default=None, max_length=200)
    steps: list[DemoStep] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def chronological(self):
        for left, right in zip(self.steps, self.steps[1:]):
            if right.startedAt < left.completedAt:
                raise ValueError("同次体检项目不能重叠，请按时间顺序填写")
        return self


class DemoPatient(ImportModel):
    name: str = Field(min_length=1, max_length=100)
    gender: Literal["男", "女", "未填写"] = "未填写"
    age: int = Field(ge=1, le=120)
    medicalHistory: str = Field(default="无（模拟资料）", max_length=2000)
    allergens: str = Field(default="无（模拟资料）", max_length=2000)


class DemoBundle(ImportModel):
    formatVersion: Literal["patient-demo-1.0"]
    simulated: Literal[True]
    hospitalName: str = Field(min_length=1, max_length=200)
    patient: DemoPatient
    visits: list[DemoVisit] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def unique_keys(self):
        if len({visit.recordKey for visit in self.visits}) != len(self.visits):
            raise ValueError("recordKey 不能重复")
        now = utcnow()
        for visit in self.visits:
            for step in visit.steps:
                if step.completedAt > now or (step.report and step.report.reportedAt > now):
                    raise ValueError("历史体检和已发布报告不能使用未来时间")
        return self


class DemoImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone: str = Field(pattern=r"^1[0-9]{10}$")
    password: str = Field(min_length=8, max_length=128)
    currentPassword: str | None = Field(default=None, min_length=1, max_length=128)
    bundle: DemoBundle


def stable_id(hospital_id: str, user_id: str, kind: str, key: str) -> str:
    return uuid5(NAMESPACE_URL, f"checkup:patient-demo:v1:{hospital_id}:{user_id}:{kind}:{key}").hex


def import_patient_bundle(db: Session, hospital_id: str, payload: DemoImportRequest) -> dict:
    hospital = db.get(HospitalInfo, hospital_id)
    bundle = payload.bundle
    if not hospital or bundle.hospitalName != hospital.hospital_name:
        raise HTTPException(400, "数据包医院名称与当前登录医院不一致，请下载本院示例后填写")
    # Resolve every item before modifying any account; names never cross hospital boundaries.
    exams = db.execute(select(ExamInfo, DepartmentInfo).join(DepartmentInfo).where(
        DepartmentInfo.hospital_id == hospital_id)).all()
    packages = db.scalars(select(PackageInfo).where(PackageInfo.hospital_id == hospital_id)).all()
    resolved = []
    for visit in bundle.visits:
        package = None
        if visit.packageName:
            matches = [row for row in packages if row.package_name == visit.packageName]
            if len(matches) != 1:
                raise HTTPException(400, f"套餐不存在或重名：{visit.packageName}")
            package = matches[0]
        visit_exams = []
        for step in visit.steps:
            matches = [exam for exam, department in exams if exam.item_name == step.itemName and (
                step.departmentName is None or step.departmentName == department.dept_name)]
            if len(matches) != 1:
                raise HTTPException(400, f"本院项目不存在或重名，请核对项目及科室：{step.itemName}")
            visit_exams.append(matches[0])
        if len({exam.item_id for exam in visit_exams}) != len(visit_exams):
            raise HTTPException(400, "同次体检不能重复包含同一项目")
        if package and any(exam.item_id not in package.included_item_ids for exam in visit_exams):
            raise HTTPException(400, "体检项目不属于指定套餐")
        resolved.append((visit, package, visit_exams))

    user = db.scalar(select(UserInfo).where(UserInfo.phone == payload.phone).with_for_update())
    created_user = user is None
    if user:
        if user.role != "普通用户" or db.scalar(select(HospitalAdmin).where(HospitalAdmin.user_id == user.user_id)):
            raise HTTPException(409, "该手机号属于管理员或排队模拟账号，不能导入")
        if not verify_login_password(payload.currentPassword or payload.password, user.password):
            raise HTTPException(409, "手机号已存在，请填写该患者的当前密码以验证归属；不会覆盖未知账号")
        if not verify_login_password(payload.password, user.password):
            user.password = hash_password(payload.password)
            db.execute(delete(UserSession).where(UserSession.user_id == user.user_id))
    else:
        user = UserInfo(phone=payload.phone, password=hash_password(payload.password),
                        name=bundle.patient.name, role="普通用户")
        db.add(user)
        db.flush()

    user.name = bundle.patient.name
    user.gender = bundle.patient.gender
    user.birth_date = f"{utcnow().year - bundle.patient.age}-01-01"
    imported = skipped = reports = updated = 0
    plan_ids = []
    for visit, package, visit_exams in resolved:
        plan_id = stable_id(hospital_id, user.user_id, "plan", visit.recordKey)
        plan_ids.append(plan_id)
        existing = db.get(ExamPlan, plan_id)
        if existing:
            # Only refresh records created by this importer, after account verification.
            profile = db.get(UserStatusInfo, existing.record_id)
            details = db.scalars(select(PlanExecutionDetail).where(
                PlanExecutionDetail.plan_id == plan_id).order_by(PlanExecutionDetail.step_order)).all()
            if (existing.user_id != user.user_id or existing.hospital_id != hospital_id or
                    not profile or not (profile.profile_data or {}).get('demoImport') or
                    existing.plan_status != '已完成' or
                    any(d.exam_report and not d.exam_report.get('simulated') for d in details) or
                    [d.item_id for d in details] != [e.item_id for e in visit_exams]):
                raise HTTPException(409, "该历史记录的项目结构已变化，请使用新的 recordKey")
            changed = False
            snapshot = {**profile.profile_data, "medicalHistory": bundle.patient.medicalHistory,
                        "allergens": bundle.patient.allergens, "demoVisitTitle": visit.title}
            if snapshot != profile.profile_data:
                profile.profile_data = snapshot
                changed = True
            for detail, step in zip(details, visit.steps):
                report = imported_report(step.report)
                if detail.exam_report != report:
                    detail.exam_report = report
                    changed = True
                    reports += bool(report)
            if changed:
                updated += 1
            else:
                skipped += 1
            continue
        start, end = visit.steps[0].startedAt, visit.steps[-1].completedAt
        record_id = stable_id(hospital_id, user.user_id, "profile", visit.recordKey)
        db.add(UserStatusInfo(record_id=record_id, user_id=user.user_id, create_time=start,
            profile_data={"medicalHistory": bundle.patient.medicalHistory, "allergens": bundle.patient.allergens,
                          "demoImport": True, "demoVisitTitle": visit.title}))
        db.flush()
        db.add(ExamPlan(plan_id=plan_id, user_id=user.user_id, hospital_id=hospital_id,
            package_id=package.package_id if package else None, record_id=record_id,
            selected_item_ids=[exam.item_id for exam in visit_exams], generate_time=start,
            appointment_at=start, total_duration=int((end - start).total_seconds() / 60), plan_status="已完成"))
        db.flush()
        for index, (step, exam) in enumerate(zip(visit.steps, visit_exams), 1):
            report = imported_report(step.report)
            reports += bool(report)
            db.add(PlanExecutionDetail(detail_id=stable_id(hospital_id, user.user_id, f"step-{visit.recordKey}", str(index)),
                plan_id=plan_id, item_id=exam.item_id, step_order=index, estimated_start=step.startedAt,
                estimated_end=step.completedAt, actual_start=step.startedAt, actual_end=step.completedAt,
                exec_status="已完成", actual_wait_time=0, actual_travel_time=0, exam_report=report))
        imported += 1
    # A separate current profile avoids treating an old exam snapshot as today's preparation.
    profile_id = stable_id(hospital_id, user.user_id, "profile", "current")
    profile = db.get(UserStatusInfo, profile_id)
    if profile is None:
        profile = UserStatusInfo(record_id=profile_id, user_id=user.user_id)
        db.add(profile)
    profile.create_time = utcnow()
    profile.profile_data = {"medicalHistory": bundle.patient.medicalHistory, "allergens": bundle.patient.allergens,
                           "simulated": True, "fasting": "no", "bladder": "recentUrination"}
    db.flush()
    return {"phone": user.phone, "userID": user.user_id, "createdPatient": created_user,
            "importedVisits": imported, "updatedVisits": updated, "skippedVisits": skipped, "importedReports": reports, "planIDs": plan_ids}


def imported_report(report: DemoReport | None) -> dict | None:
    if report is None:
        return None
    return {**report.model_dump(exclude={"reportedAt"}), "reportedAt": iso(report.reportedAt),
            "simulated": True, "status": "published"}


@router.post("/import")
def import_patient(payload: DemoImportRequest, admin: AdminContext = Depends(get_current_admin),
                   db: Session = Depends(get_db)) -> dict:
    require_owner(admin)
    try:
        result = import_patient_bundle(db, admin.hospital_id, payload)
        db.commit()
        return result
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "导入冲突，未写入本次数据，请刷新后重试") from exc


@router.get("/import-template")
def patient_template(admin: AdminContext = Depends(get_current_admin), db: Session = Depends(get_db)) -> dict:
    require_owner(admin)
    hospital = db.get(HospitalInfo, admin.hospital_id)
    rows = db.execute(select(ExamInfo, DepartmentInfo).join(DepartmentInfo).where(
        DepartmentInfo.hospital_id == admin.hospital_id).order_by(ExamInfo.item_name).limit(6)).all()
    if not rows:
        raise HTTPException(400, "请先导入医院检查项目，再下载患者示例")
    cursor = (utcnow() - timedelta(days=7)).replace(hour=1, minute=0, second=0, microsecond=0)
    steps = []
    for exam, department in rows:
        end = cursor + timedelta(minutes=max(1, exam.duration))
        steps.append({"itemName": exam.item_name, "departmentName": department.dept_name,
            "startedAt": iso(cursor), "completedAt": iso(end), "report": {
                "conclusion": "演示项目已完成，以下为虚构结果。", "reportedAt": iso(end + timedelta(hours=3)),
                "items": [{"label": "示例结果", "value": "未见明显异常", "status": "正常"}]}})
        cursor = end + timedelta(minutes=5)
    return {"formatVersion": "patient-demo-1.0", "simulated": True, "hospitalName": hospital.hospital_name,
        "patient": {"name": "林同学（演示）", "gender": "男", "age": 21,
        "medicalHistory": "无（虚构资料）", "allergens": "无（虚构资料）"},
        "visits": [{"recordKey": "example-visit-01", "title": "学期健康体检（演示）", "steps": steps}]}
