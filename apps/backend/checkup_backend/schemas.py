from __future__ import annotations

import base64
import binascii
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .exam_constraints import validate_exam_selection, validate_prerequisite_graph
from .hospital_time import parse_open_time_ranges

TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
COVER_DATA_PATTERN = re.compile(r"^data:image/(?:jpeg|png|webp);base64,([A-Za-z0-9+/=]+)$")
MAX_COVER_IMAGE_BYTES = 1024 * 1024


def validate_hospital_open_time(value: str) -> str:
    parse_open_time_ranges(value)
    return value


def validate_allowed_time_slots(value: dict[str, Any]) -> dict[str, Any]:
    if not value:
        return {}
    if set(value) != {"start", "end"}:
        raise ValueError('允许时段必须为空对象或只包含 "start" 和 "end"')
    start, end = value["start"], value["end"]
    if not isinstance(start, str) or not TIME_PATTERN.fullmatch(start):
        raise ValueError("允许时段 start 必须为 HH:MM")
    if not isinstance(end, str) or not TIME_PATTERN.fullmatch(end):
        raise ValueError("允许时段 end 必须为 HH:MM")
    if start >= end:
        raise ValueError("允许时段 end 必须晚于 start")
    return {"start": start, "end": end}


def validate_cover_image_url(value: str | None) -> str | None:
    if value in {None, ""}:
        return None
    if value.startswith("https://"):
        if len(value) > 1000:
            raise ValueError("医院图片 URL 过长")
        return value
    match = COVER_DATA_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("医院图片必须为 HTTPS 地址或 JPEG、PNG、WebP 图片数据")
    try:
        decoded = base64.b64decode(match.group(1), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("医院图片数据无效") from exc
    if len(decoded) > MAX_COVER_IMAGE_BYTES:
        raise ValueError("医院图片不能超过 1 MB")
    return value


def validate_avatar_image_url(value: str | None) -> str | None:
    try:
        return validate_cover_image_url(value)
    except ValueError as exc:
        raise ValueError(str(exc).replace("医院图片", "头像")) from exc


class LoginRequest(BaseModel):
    phone: str
    password: str


class PatientAgentMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("消息内容不能为空")
        return normalized


class PatientAgentChatRequest(BaseModel):
    messages: list[PatientAgentMessage] = Field(min_length=1, max_length=20)
    currentPage: str = Field(default="", max_length=200)


class PatientRegister(BaseModel):
    phone: str = Field(min_length=5, max_length=32)
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=100)
    gender: str | None = Field(default=None, max_length=16)
    age: int | None = Field(default=None, ge=1, le=120)
    medicalHistory: str = Field(default="-", max_length=2000)
    allergens: str = Field(default="-", max_length=2000)
    privacyConsent: Literal[True]
    privacyConsentVersion: Literal["v0.3.1-2026-08-31"]


class PatientAccountDelete(BaseModel):
    password: str = Field(min_length=8, max_length=128)


class PatientProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    phone: str | None = Field(default=None, min_length=5, max_length=32)
    gender: str | None = Field(default=None, max_length=16)
    age: int | None = Field(default=None, ge=1, le=120)
    fasting: Literal["yes", "no"] | None = None
    bladder: Literal["normal", "recentUrination"] | None = None
    drinkingWater: Literal["adequate", "notyet"] | None = None
    specialNeed: Literal["none", "has"] | None = None
    booked: Literal["yes", "no"] | None = None
    medicalHistory: str | None = Field(default=None, max_length=2000)
    allergens: str | None = Field(default=None, max_length=2000)
    avatarUrl: str | None = None

    @field_validator("avatarUrl")
    @classmethod
    def validate_avatar(cls, value: str | None) -> str | None:
        return validate_avatar_image_url(value)


class PatientReminderSubscription(BaseModel):
    templateID: str = Field(min_length=1, max_length=200)
    permission: Literal["accept"]


class PatientPlanCreate(BaseModel):
    hospitalID: str
    packageID: str | None = None
    selectedItemIDs: list[str] = Field(default_factory=list)
    appointmentAt: datetime | None = None
    profile: dict[str, Any] = Field(default_factory=dict)
    reminderSubscription: PatientReminderSubscription | None = None

    @model_validator(mode="after")
    def validate_selection(self):
        if not self.packageID and not self.selectedItemIDs:
            raise ValueError("请选择体检套餐或检查项目")
        return self


class HospitalUpdate(BaseModel):
    hospitalName: str | None = Field(default=None, min_length=2, max_length=200)
    address: str | None = Field(default=None, max_length=500)
    openTime: str | None = Field(default=None, max_length=100)
    floorMapUrl: str | None = Field(default=None, max_length=1000)
    coverImageUrl: str | None = None
    hospitalLevel: str | None = Field(default=None, min_length=1, max_length=50)
    positioning: str | None = Field(default=None, min_length=1, max_length=100)
    isAvailable: bool | None = None
    appointmentSlotMinutes: int | None = Field(default=None, ge=15, le=240)
    appointmentSlotCapacity: int | None = Field(default=None, ge=1, le=1000)
    appointmentDaysAhead: int | None = Field(default=None, ge=1, le=60)

    @field_validator("openTime")
    @classmethod
    def validate_open_time(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("openTime 不能为 null")
        return validate_hospital_open_time(value)

    @field_validator("coverImageUrl")
    @classmethod
    def validate_cover(cls, value: str | None) -> str | None:
        return validate_cover_image_url(value)


class DepartmentCreate(BaseModel):
    deptName: str = Field(min_length=1, max_length=200)
    location: str = Field(default="", max_length=500)
    openTimeStart: str = "08:00"
    openTimeEnd: str = "17:00"
    capacity: int = Field(default=1, ge=1, le=1000)
    isAvailable: bool = True

    @field_validator("openTimeStart", "openTimeEnd")
    @classmethod
    def validate_time(cls, value: str) -> str:
        if not TIME_PATTERN.match(value):
            raise ValueError("时间必须为 HH:MM")
        return value

    @model_validator(mode="after")
    def validate_window(self):
        if self.openTimeStart >= self.openTimeEnd:
            raise ValueError("开放结束时间必须晚于开始时间")
        return self


class DepartmentUpdate(BaseModel):
    deptName: str | None = Field(default=None, min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=500)
    openTimeStart: str | None = None
    openTimeEnd: str | None = None
    capacity: int | None = Field(default=None, ge=1, le=1000)
    isAvailable: bool | None = None

    @field_validator("openTimeStart", "openTimeEnd")
    @classmethod
    def validate_time(cls, value: str | None) -> str | None:
        if value is not None and not TIME_PATTERN.match(value):
            raise ValueError("时间必须为 HH:MM")
        return value


class ExamCreate(BaseModel):
    deptID: str
    itemName: str = Field(min_length=1, max_length=200)
    duration: int = Field(ge=1, le=1440)
    prerequisites: dict[str, Any] = Field(default_factory=dict)
    conflicts: list[str] = Field(default_factory=list)
    priority: int = Field(default=0, ge=0, le=100)
    allowedTimeSlots: dict[str, Any] = Field(default_factory=dict)
    isCritical: bool = False
    isActive: bool = True

    @field_validator("allowedTimeSlots")
    @classmethod
    def validate_time_slots(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_allowed_time_slots(value)


class ExamUpdate(BaseModel):
    deptID: str | None = None
    itemName: str | None = Field(default=None, min_length=1, max_length=200)
    duration: int | None = Field(default=None, ge=1, le=1440)
    prerequisites: dict[str, Any] | None = None
    conflicts: list[str] | None = None
    priority: int | None = Field(default=None, ge=0, le=100)
    allowedTimeSlots: dict[str, Any] | None = None
    isCritical: bool | None = None
    isActive: bool | None = None

    @field_validator("allowedTimeSlots")
    @classmethod
    def validate_time_slots(cls, value: dict[str, Any] | None) -> dict[str, Any]:
        if value is None:
            raise ValueError("清空允许时段请使用空对象 {}")
        return validate_allowed_time_slots(value)


class PackageCreate(BaseModel):
    packageName: str = Field(min_length=1, max_length=200)
    packageType: str = Field(default="健康体检", min_length=1, max_length=100)
    price: float = Field(default=0, ge=0, le=1_000_000)
    tag: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=5000)
    includedItemIDs: list[str] = Field(min_length=1)
    defaultDuration: int = Field(default=0, ge=0, le=100_000)
    suitable: list[str] = Field(default_factory=list, max_length=100)
    notice: list[str] = Field(default_factory=list, max_length=100)
    isPublished: bool = False


class PackageUpdate(BaseModel):
    packageName: str | None = Field(default=None, min_length=1, max_length=200)
    packageType: str | None = Field(default=None, min_length=1, max_length=100)
    price: float | None = Field(default=None, ge=0, le=1_000_000)
    tag: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    includedItemIDs: list[str] | None = Field(default=None, min_length=1)
    defaultDuration: int | None = Field(default=None, ge=0, le=100_000)
    suitable: list[str] | None = Field(default=None, max_length=100)
    notice: list[str] | None = Field(default=None, max_length=100)
    isPublished: bool | None = None


class GISUpload(BaseModel):
    geojson: dict[str, Any]

    @field_validator("geojson")
    @classmethod
    def validate_geojson(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("type") != "FeatureCollection" or not isinstance(value.get("features"), list):
            raise ValueError("GIS 必须是 GeoJSON FeatureCollection")
        allowed = {"Point", "LineString", "Polygon", "MultiPolygon"}
        for feature in value["features"]:
            if not isinstance(feature, dict):
                raise ValueError("GIS feature 必须是对象")
            geometry = feature.get("geometry") or {}
            properties = feature.get("properties") or {}
            if not isinstance(geometry, dict) or not isinstance(properties, dict):
                raise ValueError("GIS geometry 和 properties 必须是对象")
            if feature.get("type") != "Feature" or geometry.get("type") not in allowed:
                raise ValueError("GIS 仅支持 Point、LineString、Polygon 和 MultiPolygon")
            if "coordinates" not in geometry:
                raise ValueError("GIS geometry 缺少 coordinates")
        return value


class WorkspaceDepartment(DepartmentCreate):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class WorkspaceHospital(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hospitalName: str = Field(min_length=2, max_length=200)
    address: str = Field(default="", max_length=500)
    openTime: str = Field(default="08:00-17:00", max_length=100)
    floorMapUrl: str | None = Field(default=None, max_length=1000)
    coverImageUrl: str | None = None
    hospitalLevel: str = Field(default="未定级", min_length=1, max_length=50)
    positioning: str = Field(default="综合医疗机构", min_length=1, max_length=100)
    isAvailable: bool = True
    appointmentSlotMinutes: int = Field(default=30, ge=15, le=240)
    appointmentSlotCapacity: int = Field(default=20, ge=1, le=1000)
    appointmentDaysAhead: int = Field(default=7, ge=1, le=60)

    @field_validator("openTime")
    @classmethod
    def validate_open_time(cls, value: str) -> str:
        return validate_hospital_open_time(value)

    @field_validator("coverImageUrl")
    @classmethod
    def validate_cover(cls, value: str | None) -> str | None:
        return validate_cover_image_url(value)


class WorkspaceExam(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    departmentKey: str = Field(min_length=1, max_length=64)
    itemName: str = Field(min_length=1, max_length=200)
    duration: int = Field(ge=1, le=1440)
    prerequisites: dict[str, Any] = Field(default_factory=dict)
    prerequisiteItemKeys: list[str] = Field(default_factory=list, max_length=100)
    conflictItemKeys: list[str] = Field(default_factory=list, max_length=100)
    priority: int = Field(default=0, ge=0, le=100)
    allowedTimeSlots: dict[str, Any] = Field(default_factory=dict)
    isCritical: bool = False
    isActive: bool = True

    @field_validator("allowedTimeSlots")
    @classmethod
    def validate_time_slots(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_allowed_time_slots(value)


class WorkspacePackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    packageName: str = Field(min_length=1, max_length=200)
    packageType: str = Field(default="健康体检", min_length=1, max_length=100)
    price: float = Field(default=0, ge=0, le=1_000_000)
    tag: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=5000)
    includedItemKeys: list[str] = Field(min_length=1, max_length=500)
    defaultDuration: int = Field(default=0, ge=0, le=100_000)
    suitable: list[str] = Field(default_factory=list, max_length=100)
    notice: list[str] = Field(default_factory=list, max_length=100)
    isPublished: bool = False


class WorkspaceGIS(GISUpload):
    model_config = ConfigDict(extra="forbid")

    floorKey: str = Field(min_length=1, max_length=64)

    @field_validator("floorKey")
    @classmethod
    def validate_floor_key(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("楼层标识不能为空")
        return value


class WorkspaceImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    formatVersion: Literal["1.0"]
    mode: Literal["upsert"] = "upsert"
    hospital: WorkspaceHospital | None = None
    departments: list[WorkspaceDepartment] = Field(default_factory=list, max_length=500)
    exams: list[WorkspaceExam] = Field(default_factory=list, max_length=5000)
    packages: list[WorkspacePackage] = Field(default_factory=list, max_length=500)
    gis: list[WorkspaceGIS] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_manifest(self):
        collections = {
            "科室": [row.key for row in self.departments],
            "检查项目": [row.key for row in self.exams],
            "套餐": [row.key for row in self.packages],
            "GIS 楼层": [row.floorKey for row in self.gis],
        }
        for label, keys in collections.items():
            if len(keys) != len(set(keys)):
                raise ValueError(f"{label}包含重复 key")
        if self.hospital is None and not any(collections.values()):
            raise ValueError("导入文件至少需要包含一类数据")

        department_keys = set(collections["科室"])
        item_keys = set(collections["检查项目"])
        prerequisites_by_item: dict[str, set[str]] = {}
        conflicts_by_item: dict[str, set[str]] = {}
        for exam in self.exams:
            if exam.departmentKey not in department_keys:
                raise ValueError(f"检查项目 {exam.key} 引用了未声明的科室 {exam.departmentKey}")
            if "itemIDs" in exam.prerequisites:
                raise ValueError(f"检查项目 {exam.key} 请使用 prerequisiteItemKeys，不要直接填写 itemIDs")
            if len(exam.prerequisiteItemKeys) != len(set(exam.prerequisiteItemKeys)):
                raise ValueError(f"检查项目 {exam.key} 的 prerequisiteItemKeys 包含重复项")
            if len(exam.conflictItemKeys) != len(set(exam.conflictItemKeys)):
                raise ValueError(f"检查项目 {exam.key} 的 conflictItemKeys 包含重复项")
            references = set(exam.prerequisiteItemKeys) | set(exam.conflictItemKeys)
            missing = references - item_keys
            if missing:
                raise ValueError(f"检查项目 {exam.key} 引用了未声明的项目 {sorted(missing)}")
            if exam.key in references:
                raise ValueError(f"检查项目 {exam.key} 不能引用自身作为前置或互斥项目")
            overlap = set(exam.prerequisiteItemKeys) & set(exam.conflictItemKeys)
            if overlap:
                raise ValueError(f"检查项目 {exam.key} 不能同时前置和互斥项目 {sorted(overlap)}")
            prerequisites_by_item[exam.key] = set(exam.prerequisiteItemKeys)
            conflicts_by_item[exam.key] = set(exam.conflictItemKeys)
        try:
            validate_prerequisite_graph(item_keys, prerequisites_by_item)
        except ValueError as exc:
            raise ValueError(f"检查项目前置关系无效：{exc}") from exc
        for package in self.packages:
            if len(package.includedItemKeys) != len(set(package.includedItemKeys)):
                raise ValueError(f"套餐 {package.key} 的 includedItemKeys 包含重复项")
            missing = set(package.includedItemKeys) - item_keys
            if missing:
                raise ValueError(f"套餐 {package.key} 引用了未声明的项目 {sorted(missing)}")
            try:
                validate_exam_selection(
                    package.includedItemKeys,
                    prerequisites_by_item,
                    conflicts_by_item,
                )
            except ValueError as exc:
                raise ValueError(f"套餐 {package.key} 的项目组合无效：{exc}") from exc
        for floor in self.gis:
            for feature in floor.geojson.get("features", []):
                properties = feature.get("properties") or {}
                if properties.get("featureType") == "department":
                    key = properties.get("departmentKey")
                    if key not in department_keys:
                        raise ValueError(f"GIS {floor.floorKey} 引用了未声明的科室 {key}")
                if properties.get("featureType") == "route":
                    route_keys = {properties.get("fromDepartmentKey"), properties.get("toDepartmentKey")}
                    if None in route_keys or not route_keys.issubset(department_keys):
                        raise ValueError(f"GIS {floor.floorKey} 的路线引用了未声明的科室")
        return self


class HospitalRegister(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=5, max_length=32)
    password: str = Field(min_length=8, max_length=128)
    adminName: str = Field(min_length=1, max_length=100)
    workspace: WorkspaceImport

    @model_validator(mode="after")
    def validate_complete_workspace(self):
        missing = []
        if self.workspace.hospital is None:
            missing.append("hospital")
        if not self.workspace.departments:
            missing.append("departments")
        if not self.workspace.exams:
            missing.append("exams")
        if not self.workspace.packages:
            missing.append("packages")
        if not self.workspace.gis:
            missing.append("gis")
        if missing:
            raise ValueError(f"医院注册必须上传完整工作区数据，缺少：{', '.join(missing)}")
        if not any(package.isPublished for package in self.workspace.packages):
            raise ValueError("医院注册数据至少需要一个已上架套餐，用于准备演示患者池")
        return self


class DemoPatientTarget(BaseModel):
    count: int = Field(ge=1, le=100)


class AnomalyCreate(BaseModel):
    deptID: str
    anomalyType: Literal["科室关闭", "设备故障", "极度拥挤"]
    description: str = Field(default="", max_length=2000)


class AnomalyResolve(BaseModel):
    reopenDepartment: bool = True


class QueueUpdate(BaseModel):
    itemID: str
    queueCount: int = Field(ge=0, le=100000)
    estimatedWaitTime: int = Field(ge=0, le=86400)
    validMinutes: int = Field(default=30, ge=1, le=1440)

