from __future__ import annotations

import hashlib
import random
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .exam_constraints import prerequisite_item_ids, validate_exam_selection
from .models import (
    DemoPatientProfile,
    DepartmentInfo,
    ExamInfo,
    ExamPlan,
    PackageInfo,
    PlanExecutionDetail,
    UserInfo,
    UserStatusInfo,
    utcnow,
)

DEMO_POOL_SIZE = 100
SURNAMES = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦许何吕施张孔曹严华金魏陶姜"
GIVEN_NAMES = (
    "安然",
    "晨曦",
    "嘉宁",
    "子涵",
    "若溪",
    "宇辰",
    "思远",
    "明哲",
    "欣怡",
    "雨桐",
    "浩然",
    "文博",
    "佳琪",
    "一诺",
    "清扬",
    "知行",
)


def _stable_id(hospital_id: str, resource: str, ordinal: int) -> str:
    return uuid5(NAMESPACE_URL, f"checkup-schedule:{hospital_id}:demo:{resource}:{ordinal}").hex


def _seed_for_hospital(hospital_id: str) -> int:
    return int.from_bytes(hashlib.sha256(hospital_id.encode()).digest()[:8], "big")


def _validate_package_items(package: PackageInfo, exams: dict[str, ExamInfo]) -> list[str]:
    item_ids = [item_id for item_id in package.included_item_ids or [] if item_id in exams]
    try:
        validate_exam_selection(
            item_ids,
            {item_id: prerequisite_item_ids(exams[item_id].prerequisites) for item_id in item_ids},
            {item_id: exams[item_id].conflicts or [] for item_id in item_ids},
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"套餐“{package.package_name}”的项目组合无效：{exc}",
        ) from exc
    return item_ids


def _ordered_package_items(package: PackageInfo, exams: dict[str, ExamInfo], rng: random.Random) -> list[str]:
    remaining = set(_validate_package_items(package, exams))
    ordered: list[str] = []
    while remaining:
        ready = []
        resolved = set(ordered)
        for item_id in remaining:
            prerequisite_ids = set(prerequisite_item_ids(exams[item_id].prerequisites))
            if prerequisite_ids.issubset(resolved):
                ready.append(item_id)
        if not ready:
            raise HTTPException(status_code=422, detail=f"套餐“{package.package_name}”的项目存在循环前置关系")
        ready.sort()
        rng.shuffle(ready)
        item_id = ready[0]
        ordered.append(item_id)
        remaining.remove(item_id)
    return ordered


def prepare_demo_patient_pool(db: Session, hospital_id: str, size: int = DEMO_POOL_SIZE) -> int:
    existing = db.scalar(
        select(func.count()).select_from(DemoPatientProfile).where(DemoPatientProfile.hospital_id == hospital_id)
    ) or 0
    if existing:
        return int(existing)

    exam_rows = db.execute(
        select(ExamInfo)
        .join(DepartmentInfo, DepartmentInfo.dept_id == ExamInfo.dept_id)
        .where(DepartmentInfo.hospital_id == hospital_id, ExamInfo.is_active.is_(True))
    ).scalars().all()
    exams = {row.item_id: row for row in exam_rows}
    packages = db.scalars(
        select(PackageInfo)
        .where(PackageInfo.hospital_id == hospital_id, PackageInfo.is_published.is_(True))
        .order_by(PackageInfo.package_name, PackageInfo.package_id)
    ).all()
    packages = [row for row in packages if row.included_item_ids and set(row.included_item_ids).issubset(exams)]
    if not packages:
        raise HTTPException(status_code=422, detail="完整数据至少需要一个包含有效项目的已上架套餐")
    for package in packages:
        _validate_package_items(package, exams)

    rng = random.Random(_seed_for_hospital(hospital_id))
    current_year = utcnow().year
    profiles = []
    for ordinal in range(1, size + 1):
        gender = "男" if rng.random() < 0.5 else "女"
        age = rng.randint(18, 78)
        month = rng.randint(1, 12)
        day = rng.randint(1, 28)
        name = f"{rng.choice(SURNAMES)}{rng.choice(GIVEN_NAMES)}"
        package = rng.choice(packages)
        item_ids = _ordered_package_items(package, exams, rng)
        walk_speed = round(max(0.72, min(1.58, 1.42 - max(0, age - 45) * 0.011 + rng.uniform(-0.12, 0.12))), 2)
        profile = {
            "demo": True,
            "age": age,
            "fasting": "yes" if rng.random() < 0.82 else "no",
            "bladder": "normal" if rng.random() < 0.7 else "recentUrination",
            "drinkingWater": "adequate" if rng.random() < 0.72 else "notyet",
            "specialNeed": "has" if age >= 65 or rng.random() < 0.08 else "none",
            "booked": "yes",
            "medicalHistory": rng.choice(["", "高血压史", "既往手术史", "糖尿病史", "无特殊病史"]),
            "allergens": rng.choice(["", "无已知过敏", "青霉素过敏"]),
        }
        user = UserInfo(
            user_id=_stable_id(hospital_id, "user", ordinal),
            phone=f"demo-{hospital_id[:16]}-{ordinal:03d}",
            password="disabled-demo-account",
            name=name,
            gender=gender,
            birth_date=f"{current_year - age:04d}-{month:02d}-{day:02d}",
            role="演示患者",
            walk_speed=walk_speed,
        )
        db.add(user)
        profiles.append(
            DemoPatientProfile(
                demo_id=_stable_id(hospital_id, "profile", ordinal),
                hospital_id=hospital_id,
                user_id=user.user_id,
                ordinal=ordinal,
                package_id=package.package_id,
                selected_item_ids=item_ids,
                profile_data=profile,
            )
        )
    db.flush()
    db.add_all(profiles)
    db.flush()
    return size


def demo_pool_summary(db: Session, hospital_id: str, changed: int = 0) -> dict:
    rows = db.execute(
        select(DemoPatientProfile, UserInfo)
        .join(UserInfo, UserInfo.user_id == DemoPatientProfile.user_id)
        .where(DemoPatientProfile.hospital_id == hospital_id)
        .order_by(DemoPatientProfile.ordinal)
    ).all()
    active = [(profile, user) for profile, user in rows if profile.is_active]
    return {
        "prepared": len(rows),
        "active": len(active),
        "inactive": len(rows) - len(active),
        "changed": changed,
        "activePatients": [
            {"ordinal": profile.ordinal, "name": user.name, "planID": profile.active_plan_id}
            for profile, user in active[:8]
        ],
    }


def _deactivate_profiles(db: Session, profiles: list[DemoPatientProfile]) -> None:
    plans = [db.get(ExamPlan, row.active_plan_id) for row in profiles if row.active_plan_id]
    records = [db.get(UserStatusInfo, row.active_record_id) for row in profiles if row.active_record_id]
    for row in profiles:
        row.active_plan_id = None
        row.active_record_id = None
        row.is_active = False
        row.activated_at = None
    db.flush()
    for plan in plans:
        if plan is not None:
            db.delete(plan)
    db.flush()
    for record in records:
        if record is not None:
            db.delete(record)


def _activate_profiles(db: Session, hospital_id: str, profiles: list[DemoPatientProfile]) -> None:
    hospital_exam_rows = db.execute(
        select(ExamInfo)
        .join(DepartmentInfo, DepartmentInfo.dept_id == ExamInfo.dept_id)
        .where(DepartmentInfo.hospital_id == hospital_id, ExamInfo.is_active.is_(True))
    ).scalars().all()
    exams = {row.item_id: row for row in hospital_exam_rows}
    now = utcnow()
    for profile in profiles:
        item_ids = list(profile.selected_item_ids or [])
        package = db.get(PackageInfo, profile.package_id) if profile.package_id else None
        if package is None or package.hospital_id != hospital_id or not item_ids or any(item_id not in exams for item_id in item_ids):
            raise HTTPException(status_code=409, detail="演示患者池与当前套餐项目不一致，请使用注册时上传的数据")
        record = UserStatusInfo(
            record_id=_stable_id(hospital_id, "active-record", profile.ordinal),
            user_id=profile.user_id,
            fasting_hours=8 if profile.profile_data.get("fasting") == "yes" else 0,
            is_bladder_ready=profile.profile_data.get("bladder") == "normal",
            profile_data={**profile.profile_data, "demoPoolID": profile.demo_id},
        )
        plan = ExamPlan(
            plan_id=_stable_id(hospital_id, "active-plan", profile.ordinal),
            user_id=profile.user_id,
            hospital_id=hospital_id,
            package_id=package.package_id,
            record_id=record.record_id,
            selected_item_ids=item_ids,
            total_duration=sum(exams[item_id].duration for item_id in item_ids),
            plan_status="进行中",
        )
        db.add(record)
        db.flush()
        db.add(plan)
        db.flush()
        cursor = now
        for index, item_id in enumerate(item_ids, 1):
            finish = cursor + timedelta(minutes=exams[item_id].duration)
            db.add(
                PlanExecutionDetail(
                    detail_id=_stable_id(hospital_id, f"active-detail-{profile.ordinal}", index),
                    plan_id=plan.plan_id,
                    item_id=item_id,
                    step_order=index,
                    estimated_start=cursor,
                    estimated_end=finish,
                    exec_status="进行中" if index == 1 else "待开始",
                )
            )
            cursor = finish
        profile.active_plan_id = plan.plan_id
        profile.active_record_id = record.record_id
        profile.is_active = True
        profile.activated_at = now


def set_demo_patient_count(db: Session, hospital_id: str, target: int) -> dict:
    profiles = db.scalars(
        select(DemoPatientProfile)
        .where(DemoPatientProfile.hospital_id == hospital_id)
        .order_by(DemoPatientProfile.ordinal)
    ).all()
    if len(profiles) != DEMO_POOL_SIZE:
        raise HTTPException(status_code=409, detail="该医院没有完整的 100 人演示患者池，请重新注册并上传完整数据")
    active = [row for row in profiles if row.is_active]
    changed = 0
    if len(active) > target:
        removing = sorted(active, key=lambda row: row.ordinal, reverse=True)[: len(active) - target]
        _deactivate_profiles(db, removing)
        changed += len(removing)
    elif len(active) < target:
        adding = [row for row in profiles if not row.is_active][: target - len(active)]
        _activate_profiles(db, hospital_id, adding)
        changed += len(adding)
    db.flush()
    return demo_pool_summary(db, hospital_id, changed)
