from __future__ import annotations

from datetime import datetime

from .models import (
    AnomalyReport,
    DepartmentInfo,
    ExamInfo,
    HospitalInfo,
    HospitalSettings,
    PackageInfo,
)


def iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") + "Z" if value else None


def hospital_dict(row: HospitalInfo, settings: HospitalSettings | None = None) -> dict:
    cover_image_url = settings.cover_image_url if settings else None
    is_available = settings.is_available if settings else True
    slot_minutes = settings.appointment_slot_minutes if settings else 30
    slot_capacity = settings.appointment_slot_capacity if settings else 20
    days_ahead = settings.appointment_days_ahead if settings else 7
    hospital_level = settings.hospital_level if settings else "未定级"
    positioning = settings.positioning if settings else "综合医疗机构"
    return {
        "hospitalID": row.hospital_id,
        "hospitalName": row.hospital_name,
        "address": row.address,
        "openTime": row.open_time,
        "floorMapUrl": row.floor_map_url,
        "coverImageUrl": cover_image_url,
        "coverUrl": cover_image_url,
        "hospitalLevel": hospital_level,
        "positioning": positioning,
        "isAvailable": is_available,
        "status": "正常开放" if is_available else "暂停开放",
        "appointmentSlotMinutes": slot_minutes,
        "appointmentSlotCapacity": slot_capacity,
        "appointmentDaysAhead": days_ahead,
        "appointmentPolicy": {
            "slotMinutes": slot_minutes,
            "slotCapacity": slot_capacity,
            "daysAhead": days_ahead,
        },
    }


def department_dict(row: DepartmentInfo) -> dict:
    return {
        "deptID": row.dept_id,
        "hospitalID": row.hospital_id,
        "deptName": row.dept_name,
        "location": row.location,
        "openTimeStart": row.open_time_start,
        "openTimeEnd": row.open_time_end,
        "capacity": row.capacity,
        "isAvailable": row.is_available,
    }


def exam_dict(row: ExamInfo) -> dict:
    return {
        "itemID": row.item_id,
        "deptID": row.dept_id,
        "itemName": row.item_name,
        "duration": row.duration,
        "prerequisites": row.prerequisites,
        "conflicts": row.conflicts,
        "priority": row.priority,
        "allowedTimeSlots": row.allowed_time_slots,
        "isCritical": row.is_critical,
        "isActive": row.is_active,
    }


def package_dict(row: PackageInfo) -> dict:
    return {
        "packageID": row.package_id,
        "hospitalID": row.hospital_id,
        "packageName": row.package_name,
        "packageType": row.package_type,
        "price": row.price,
        "tag": row.tag,
        "description": row.description,
        "includedItemIDs": row.included_item_ids,
        "defaultDuration": row.default_duration,
        "suitable": row.suitable,
        "notice": row.notice,
        "isPublished": row.is_published,
        "createTime": iso(row.create_time),
        "updateTime": iso(row.update_time),
    }


def anomaly_dict(row: AnomalyReport, department_name: str | None = None) -> dict:
    return {
        "reportID": row.report_id,
        "deptID": row.dept_id,
        "deptName": department_name,
        "reporterID": row.reporter_id,
        "anomalyType": row.anomaly_type,
        "description": row.description,
        "reportTime": iso(row.report_time),
        "isResolved": row.is_resolved,
    }
