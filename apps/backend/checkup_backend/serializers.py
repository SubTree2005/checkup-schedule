from __future__ import annotations

from datetime import datetime

from .models import AnomalyReport, DepartmentInfo, ExamInfo, HospitalInfo, QueueSnapshot


def iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") + "Z" if value else None


def hospital_dict(row: HospitalInfo) -> dict:
    return {
        "hospitalID": row.hospital_id,
        "hospitalName": row.hospital_name,
        "address": row.address,
        "openTime": row.open_time,
        "floorMapUrl": row.floor_map_url,
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


def queue_dict(row: QueueSnapshot, item_name: str | None = None) -> dict:
    return {
        "snapshotID": row.snapshot_id,
        "itemID": row.item_id,
        "itemName": item_name,
        "queueCount": row.queue_count,
        "estimatedWaitTime": row.estimated_wait_time,
        "dataSource": row.data_source,
        "validUntil": iso(row.valid_until),
        "createTime": iso(row.create_time),
    }
