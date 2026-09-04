from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def new_id() -> str:
    return uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class UserInfo(Base):
    __tablename__ = "user_info"

    user_id: Mapped[str] = mapped_column("userID", String(64), primary_key=True, default=new_id)
    phone: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    password: Mapped[str] = mapped_column(String(512))
    name: Mapped[str] = mapped_column(String(100))
    gender: Mapped[str | None] = mapped_column(String(16), nullable=True)
    birth_date: Mapped[str | None] = mapped_column("birthDate", String(10), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(
        "avatarUrl",
        Text().with_variant(LONGTEXT(), "mysql"),
        nullable=True,
    )
    role: Mapped[str] = mapped_column(String(20), default="普通用户")
    walk_speed: Mapped[float] = mapped_column("walkSpeed", Float, default=1.2)
    walk_version: Mapped[int] = mapped_column("walkVersion", Integer, default=1)
    create_time: Mapped[datetime] = mapped_column("createTime", DateTime, default=utcnow)


class HospitalInfo(Base):
    __tablename__ = "hospital_info"

    hospital_id: Mapped[str] = mapped_column("hospitalID", String(64), primary_key=True, default=new_id)
    hospital_name: Mapped[str] = mapped_column("hospitalName", String(200))
    address: Mapped[str] = mapped_column(String(500), default="")
    open_time: Mapped[str] = mapped_column("openTime", String(100), default="08:00-17:00")
    floor_map_url: Mapped[str | None] = mapped_column("floorMapUrl", String(1000), nullable=True)


class HospitalSettings(Base):
    __tablename__ = "hospital_settings"

    hospital_id: Mapped[str] = mapped_column(
        "hospitalID",
        ForeignKey("hospital_info.hospitalID", ondelete="CASCADE"),
        primary_key=True,
    )
    cover_image_url: Mapped[str | None] = mapped_column(
        "coverImageUrl",
        Text().with_variant(LONGTEXT(), "mysql"),
        nullable=True,
    )
    hospital_level: Mapped[str] = mapped_column("hospitalLevel", String(50), default="未定级")
    positioning: Mapped[str] = mapped_column("positioning", String(100), default="综合医疗机构")
    is_available: Mapped[bool] = mapped_column("isAvailable", Boolean, default=True)
    appointment_slot_minutes: Mapped[int] = mapped_column("appointmentSlotMinutes", Integer, default=30)
    appointment_slot_capacity: Mapped[int] = mapped_column("appointmentSlotCapacity", Integer, default=20)
    appointment_days_ahead: Mapped[int] = mapped_column("appointmentDaysAhead", Integer, default=7)
    update_time: Mapped[datetime] = mapped_column("updateTime", DateTime, default=utcnow, onupdate=utcnow)


class HospitalAdmin(Base):
    __tablename__ = "hospital_admin"

    membership_id: Mapped[str] = mapped_column("membershipID", String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column("userID", ForeignKey("user_info.userID", ondelete="CASCADE"), unique=True)
    hospital_id: Mapped[str] = mapped_column("hospitalID", ForeignKey("hospital_info.hospitalID", ondelete="CASCADE"), index=True)
    is_owner: Mapped[bool] = mapped_column("isOwner", Boolean, default=False)
    create_time: Mapped[datetime] = mapped_column("createTime", DateTime, default=utcnow)


class UserSession(Base):
    __tablename__ = "user_session"

    session_id: Mapped[str] = mapped_column("sessionID", String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column("userID", ForeignKey("user_info.userID", ondelete="CASCADE"), index=True)
    login_time: Mapped[datetime] = mapped_column("loginTime", DateTime, default=utcnow)
    login_ip: Mapped[str | None] = mapped_column("loginIP", String(64), nullable=True)
    expires_at: Mapped[datetime] = mapped_column("expiresAt", DateTime, index=True)


class UserConsent(Base):
    __tablename__ = "user_consent"
    __table_args__ = (UniqueConstraint("userID", "policyVersion", name="uq_user_consent_version"),)

    consent_id: Mapped[str] = mapped_column("consentID", String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        "userID", ForeignKey("user_info.userID", ondelete="CASCADE"), index=True
    )
    policy_version: Mapped[str] = mapped_column("policyVersion", String(64))
    accepted_at: Mapped[datetime] = mapped_column("acceptedAt", DateTime, default=utcnow)
    accepted_ip: Mapped[str | None] = mapped_column("acceptedIP", String(64), nullable=True)


class DepartmentInfo(Base):
    __tablename__ = "department_info"
    __table_args__ = (UniqueConstraint("hospitalID", "deptName", name="uq_department_hospital_name"),)

    dept_id: Mapped[str] = mapped_column("deptID", String(64), primary_key=True, default=new_id)
    hospital_id: Mapped[str] = mapped_column("hospitalID", ForeignKey("hospital_info.hospitalID", ondelete="CASCADE"), index=True)
    dept_name: Mapped[str] = mapped_column("deptName", String(200))
    location: Mapped[str] = mapped_column(String(500), default="")
    open_time_start: Mapped[str] = mapped_column("openTimeStart", String(5), default="08:00")
    open_time_end: Mapped[str] = mapped_column("openTimeEnd", String(5), default="17:00")
    capacity: Mapped[int] = mapped_column(Integer, default=1)
    is_available: Mapped[bool] = mapped_column("isAvailable", Boolean, default=True)


class ExamInfo(Base):
    __tablename__ = "exam_info"

    item_id: Mapped[str] = mapped_column("itemID", String(64), primary_key=True, default=new_id)
    dept_id: Mapped[str] = mapped_column("deptID", ForeignKey("department_info.deptID", ondelete="RESTRICT"), index=True)
    item_name: Mapped[str] = mapped_column("itemName", String(200))
    duration: Mapped[int] = mapped_column(Integer)
    prerequisites: Mapped[dict] = mapped_column(JSON, default=dict)
    conflicts: Mapped[list] = mapped_column(JSON, default=list)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    allowed_time_slots: Mapped[dict] = mapped_column("allowedTimeSlots", JSON, default=dict)
    is_critical: Mapped[bool] = mapped_column("isCritical", Boolean, default=False)
    is_active: Mapped[bool] = mapped_column("isActive", Boolean, default=True)


class PackageInfo(Base):
    __tablename__ = "package_info"

    package_id: Mapped[str] = mapped_column("packageID", String(64), primary_key=True, default=new_id)
    hospital_id: Mapped[str] = mapped_column("hospitalID", ForeignKey("hospital_info.hospitalID", ondelete="CASCADE"), index=True)
    package_name: Mapped[str] = mapped_column("packageName", String(200))
    package_type: Mapped[str] = mapped_column("packageType", String(100), default="健康体检")
    price: Mapped[float] = mapped_column(Float, default=0)
    tag: Mapped[str] = mapped_column(String(100), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    included_item_ids: Mapped[list] = mapped_column("includedItemIDs", JSON, default=list)
    default_duration: Mapped[int] = mapped_column("defaultDuration", Integer, default=0)
    suitable: Mapped[list] = mapped_column(JSON, default=list)
    notice: Mapped[list] = mapped_column(JSON, default=list)
    is_published: Mapped[bool] = mapped_column("isPublished", Boolean, default=False, index=True)
    create_time: Mapped[datetime] = mapped_column("createTime", DateTime, default=utcnow)
    update_time: Mapped[datetime] = mapped_column("updateTime", DateTime, default=utcnow, onupdate=utcnow)


class UserStatusInfo(Base):
    __tablename__ = "user_status_info"

    record_id: Mapped[str] = mapped_column("recordID", String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column("userID", ForeignKey("user_info.userID", ondelete="CASCADE"), index=True)
    fasting_hours: Mapped[float] = mapped_column("fastingHours", Float, default=0)
    is_bladder_ready: Mapped[bool] = mapped_column("isBladderReady", Boolean, default=False)
    blood_pressure: Mapped[float | None] = mapped_column("bloodPressure", Float, nullable=True)
    profile_data: Mapped[dict] = mapped_column("profileData", JSON, default=dict)
    create_time: Mapped[datetime] = mapped_column("createTime", DateTime, default=utcnow)


class ExamPlan(Base):
    __tablename__ = "exam_plan"

    plan_id: Mapped[str] = mapped_column("planID", String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column("userID", ForeignKey("user_info.userID", ondelete="RESTRICT"), index=True)
    hospital_id: Mapped[str] = mapped_column("hospitalID", ForeignKey("hospital_info.hospitalID", ondelete="RESTRICT"), index=True)
    package_id: Mapped[str | None] = mapped_column("packageID", ForeignKey("package_info.packageID", ondelete="SET NULL"), nullable=True)
    record_id: Mapped[str | None] = mapped_column("recordID", ForeignKey("user_status_info.recordID", ondelete="SET NULL"), nullable=True)
    selected_item_ids: Mapped[list] = mapped_column("selectedItemIDs", JSON, default=list)
    total_duration: Mapped[int] = mapped_column("totalDuration", Integer, default=0)
    appointment_at: Mapped[datetime | None] = mapped_column("appointmentAt", DateTime, nullable=True)
    generate_time: Mapped[datetime] = mapped_column("generateTime", DateTime, default=utcnow)
    plan_status: Mapped[str] = mapped_column("planStatus", String(20), default="待执行")


class WechatReminder(Base):
    __tablename__ = "wechat_reminder"
    __table_args__ = (
        UniqueConstraint("planID", "templateID", name="uq_wechat_reminder_plan_template"),
    )

    reminder_id: Mapped[str] = mapped_column("reminderID", String(64), primary_key=True, default=new_id)
    plan_id: Mapped[str] = mapped_column(
        "planID", ForeignKey("exam_plan.planID", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        "userID", ForeignKey("user_info.userID", ondelete="CASCADE"), index=True
    )
    open_id: Mapped[str] = mapped_column("openID", String(128))
    app_id: Mapped[str] = mapped_column("appID", String(64))
    template_id: Mapped[str] = mapped_column("templateID", String(200))
    scheduled_at: Mapped[datetime] = mapped_column("scheduledAt", DateTime, index=True)
    next_attempt_at: Mapped[datetime] = mapped_column("nextAttemptAt", DateTime, index=True)
    message_data: Mapped[dict] = mapped_column("messageData", JSON, default=dict)
    page: Mapped[str] = mapped_column(String(300), default="pages/record/record")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column("attemptCount", Integer, default=0)
    last_error: Mapped[str | None] = mapped_column("lastError", String(1000), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column("lastAttemptAt", DateTime, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column("sentAt", DateTime, nullable=True)
    create_time: Mapped[datetime] = mapped_column("createTime", DateTime, default=utcnow)
    update_time: Mapped[datetime] = mapped_column("updateTime", DateTime, default=utcnow, onupdate=utcnow)


class DemoPatientProfile(Base):
    __tablename__ = "demo_patient_profile"
    __table_args__ = (
        UniqueConstraint("hospitalID", "ordinal", name="uq_demo_patient_hospital_ordinal"),
        UniqueConstraint("userID", name="uq_demo_patient_user"),
    )

    demo_id: Mapped[str] = mapped_column("demoID", String(64), primary_key=True, default=new_id)
    hospital_id: Mapped[str] = mapped_column(
        "hospitalID", ForeignKey("hospital_info.hospitalID", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        "userID", ForeignKey("user_info.userID", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    package_id: Mapped[str | None] = mapped_column(
        "packageID", ForeignKey("package_info.packageID", ondelete="SET NULL"), nullable=True
    )
    selected_item_ids: Mapped[list] = mapped_column("selectedItemIDs", JSON, default=list)
    profile_data: Mapped[dict] = mapped_column("profileData", JSON, default=dict)
    active_plan_id: Mapped[str | None] = mapped_column(
        "activePlanID", ForeignKey("exam_plan.planID", ondelete="SET NULL"), nullable=True
    )
    active_record_id: Mapped[str | None] = mapped_column(
        "activeRecordID", ForeignKey("user_status_info.recordID", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column("isActive", Boolean, default=False, index=True)
    activated_at: Mapped[datetime | None] = mapped_column("activatedAt", DateTime, nullable=True)
    create_time: Mapped[datetime] = mapped_column("createTime", DateTime, default=utcnow)


class PlanExecutionDetail(Base):
    __tablename__ = "plan_execution_detail"

    detail_id: Mapped[str] = mapped_column("detailID", String(64), primary_key=True, default=new_id)
    plan_id: Mapped[str] = mapped_column("planID", ForeignKey("exam_plan.planID", ondelete="CASCADE"), index=True)
    item_id: Mapped[str] = mapped_column("itemID", ForeignKey("exam_info.itemID", ondelete="RESTRICT"), index=True)
    step_order: Mapped[int] = mapped_column("stepOrder", Integer)
    estimated_start: Mapped[datetime | None] = mapped_column("estimatedStart", DateTime, nullable=True)
    estimated_end: Mapped[datetime | None] = mapped_column("estimatedEnd", DateTime, nullable=True)
    actual_start: Mapped[datetime | None] = mapped_column("actualStart", DateTime, nullable=True)
    actual_end: Mapped[datetime | None] = mapped_column("actualEnd", DateTime, nullable=True)
    exec_status: Mapped[str] = mapped_column("execStatus", String(20), default="待开始")
    actual_wait_time: Mapped[int | None] = mapped_column("actualWaitTime", Integer, nullable=True)
    actual_travel_time: Mapped[int | None] = mapped_column("actualTravelTime", Integer, nullable=True)


class AnomalyReport(Base):
    __tablename__ = "anomaly_report"

    report_id: Mapped[str] = mapped_column("reportID", String(64), primary_key=True, default=new_id)
    dept_id: Mapped[str] = mapped_column("deptID", ForeignKey("department_info.deptID", ondelete="RESTRICT"), index=True)
    reporter_id: Mapped[str] = mapped_column("reporterID", ForeignKey("user_info.userID", ondelete="RESTRICT"))
    anomaly_type: Mapped[str] = mapped_column("anomalyType", String(30))
    description: Mapped[str] = mapped_column(Text, default="")
    report_time: Mapped[datetime] = mapped_column("reportTime", DateTime, default=utcnow, index=True)
    is_resolved: Mapped[bool] = mapped_column("isResolved", Boolean, default=False, index=True)


class DepartmentDistance(Base):
    __tablename__ = "department_distance"
    __table_args__ = (UniqueConstraint("fromDeptID", "toDeptID", name="uq_department_distance_pair"),)

    distance_id: Mapped[str] = mapped_column("distanceID", String(64), primary_key=True, default=new_id)
    from_dept_id: Mapped[str] = mapped_column("fromDeptID", ForeignKey("department_info.deptID", ondelete="CASCADE"), index=True)
    to_dept_id: Mapped[str] = mapped_column("toDeptID", ForeignKey("department_info.deptID", ondelete="CASCADE"), index=True)
    distance_meters: Mapped[float] = mapped_column("distanceMeters", Float)
    update_time: Mapped[datetime] = mapped_column("updateTime", DateTime, default=utcnow, onupdate=utcnow)


class UserMobilityProfile(Base):
    __tablename__ = "user_mobility_profile"

    record_id: Mapped[str] = mapped_column("recordID", String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column("userID", ForeignKey("user_info.userID", ondelete="CASCADE"), index=True)
    from_dept_id: Mapped[str] = mapped_column("fromDeptID", ForeignKey("department_info.deptID", ondelete="RESTRICT"))
    to_dept_id: Mapped[str] = mapped_column("toDeptID", ForeignKey("department_info.deptID", ondelete="RESTRICT"))
    walk_distance: Mapped[float] = mapped_column("walkDistance", Float)
    walk_duration: Mapped[int] = mapped_column("walkDuration", Integer)
    walk_speed: Mapped[float] = mapped_column("walkSpeed", Float)
    previous_speed: Mapped[float] = mapped_column("previousSpeed", Float)
    updated_speed: Mapped[float] = mapped_column("updatedSpeed", Float)
    effective_samples: Mapped[int] = mapped_column("effectiveSamples", Integer)
    confidence: Mapped[float] = mapped_column(Float)
    total_distance: Mapped[float] = mapped_column("totalDistance", Float)
    total_trips: Mapped[int] = mapped_column("totalTrips", Integer)
    version: Mapped[int] = mapped_column(Integer)
    create_time: Mapped[datetime] = mapped_column("createTime", DateTime, default=utcnow)


class WalkSpeedPreset(Base):
    __tablename__ = "walk_speed_preset"
    __table_args__ = (UniqueConstraint("ageGroup", "gender", name="uq_walk_speed_group_gender"),)

    preset_id: Mapped[str] = mapped_column("presetID", String(64), primary_key=True, default=new_id)
    age_group: Mapped[str] = mapped_column("ageGroup", String(20))
    gender: Mapped[str] = mapped_column(String(16))
    default_speed: Mapped[float] = mapped_column("defaultSpeed", Float)


class QueueSnapshot(Base):
    __tablename__ = "queue_snapshot"

    snapshot_id: Mapped[str] = mapped_column("snapshotID", String(64), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column("itemID", ForeignKey("exam_info.itemID", ondelete="CASCADE"), index=True)
    queue_count: Mapped[int] = mapped_column("queueCount", Integer)
    estimated_wait_time: Mapped[int] = mapped_column("estimatedWaitTime", Integer)
    data_source: Mapped[str] = mapped_column("dataSource", String(20), default="manual")
    valid_until: Mapped[datetime] = mapped_column("validUntil", DateTime, index=True)
    create_time: Mapped[datetime] = mapped_column("createTime", DateTime, default=utcnow, index=True)


class DepartmentWaitingStats(Base):
    __tablename__ = "department_waiting_stats"
    __table_args__ = (UniqueConstraint("deptID", "statDate", name="uq_wait_stats_dept_date"),)

    stat_id: Mapped[str] = mapped_column("statID", String(64), primary_key=True, default=new_id)
    dept_id: Mapped[str] = mapped_column("deptID", ForeignKey("department_info.deptID", ondelete="CASCADE"), index=True)
    stat_date: Mapped[str] = mapped_column("statDate", String(10), index=True)
    avg_wait_time: Mapped[int] = mapped_column("avgWaitTime", Integer, default=0)
    p90_wait_time: Mapped[int] = mapped_column("p90WaitTime", Integer, default=0)
    total_served: Mapped[int] = mapped_column("totalServed", Integer, default=0)
    update_time: Mapped[datetime] = mapped_column("updateTime", DateTime, default=utcnow, onupdate=utcnow)


class DepartmentResourceCalendar(Base):
    __tablename__ = "department_resource_calendar"

    calendar_id: Mapped[str] = mapped_column("calendarID", String(64), primary_key=True, default=new_id)
    dept_id: Mapped[str] = mapped_column("deptID", ForeignKey("department_info.deptID", ondelete="CASCADE"), index=True)
    resource_slot: Mapped[int] = mapped_column("resourceSlot", Integer)
    date: Mapped[str] = mapped_column(String(10), index=True)
    time_slot_start: Mapped[datetime] = mapped_column("timeSlotStart", DateTime)
    time_slot_end: Mapped[datetime] = mapped_column("timeSlotEnd", DateTime)
    plan_id: Mapped[str | None] = mapped_column("planID", ForeignKey("exam_plan.planID", ondelete="SET NULL"), nullable=True)
    detail_id: Mapped[str | None] = mapped_column("detailID", ForeignKey("plan_execution_detail.detailID", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="空闲")
    create_time: Mapped[datetime] = mapped_column("createTime", DateTime, default=utcnow)
    update_time: Mapped[datetime] = mapped_column("updateTime", DateTime, default=utcnow, onupdate=utcnow)


class HospitalGIS(Base):
    __tablename__ = "hospital_gis"
    __table_args__ = (UniqueConstraint("hospitalID", "floorKey", name="uq_hospital_gis_floor"),)

    gis_id: Mapped[str] = mapped_column("gisID", String(64), primary_key=True, default=new_id)
    hospital_id: Mapped[str] = mapped_column("hospitalID", ForeignKey("hospital_info.hospitalID", ondelete="CASCADE"), index=True)
    floor_key: Mapped[str] = mapped_column("floorKey", String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    geojson: Mapped[dict] = mapped_column(JSON)
    updated_by: Mapped[str] = mapped_column("updatedBy", ForeignKey("user_info.userID", ondelete="RESTRICT"))
    update_time: Mapped[datetime] = mapped_column("updateTime", DateTime, default=utcnow, onupdate=utcnow)


Index("ix_plan_hospital_status", ExamPlan.hospital_id, ExamPlan.plan_status)
Index(
    "ix_plan_hospital_appointment_status",
    ExamPlan.hospital_id,
    ExamPlan.appointment_at,
    ExamPlan.plan_status,
)
Index("ix_resource_dept_date", DepartmentResourceCalendar.dept_id, DepartmentResourceCalendar.date)
Index("ix_wechat_reminder_due", WechatReminder.status, WechatReminder.next_attempt_at)
Index(
    "ix_queue_snapshot_item_valid_created",
    QueueSnapshot.item_id,
    QueueSnapshot.valid_until,
    QueueSnapshot.create_time,
)
