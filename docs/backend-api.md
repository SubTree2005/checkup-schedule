# Backend API 与数据库

## 数据库范围

数据库实现《需求分析说明书》第 4.4 节定义的 15 张业务表：

`user_info`、`hospital_info`、`department_info`、`exam_info`、`package_info`、`user_status_info`、`exam_plan`、`plan_execution_detail`、`anomaly_report`、`department_distance`、`user_mobility_profile`、`walk_speed_preset`、`queue_snapshot`、`department_waiting_stats`、`department_resource_calendar`。

为实现管理端增加三张支撑表：

- `hospital_admin`：管理员与医院的归属关系，是多医院隔离的服务端依据；
- `user_session`：只保存随机登录令牌的 SHA-256 摘要、登录 IP 与有效期；
- `hospital_gis`：按医院和楼层保存 GeoJSON、版本号、更新人和更新时间。

密码使用带随机盐的 PBKDF2-SHA256，数据库不保存明文密码。客户端提交的 `hospitalID` 不参与授权判断。

## 主要接口

| 功能 | 接口 |
| --- | --- |
| 注册、登录、当前账号、退出 | `POST /api/auth/register`、`POST /api/auth/login`、`GET /api/auth/me`、`POST /api/auth/logout` |
| 医院资料 | `GET/PATCH /api/hospital` |
| 科室 | `GET/POST /api/departments`、`PATCH/DELETE /api/departments/{deptID}` |
| 检查项目 | `GET/POST /api/exams`、`PATCH/DELETE /api/exams/{itemID}` |
| 体检套餐与上下架 | `GET/POST /api/packages`、`PATCH/DELETE /api/packages/{packageID}` |
| 工作区标准模板与一键导入 | `GET /api/imports/template`、`POST /api/imports/workspace` |
| GIS | `GET /api/gis`、`GET/PUT /api/gis/{floorKey}` |
| 异常 | `GET/POST /api/anomalies`、`POST /api/anomalies/{reportID}/resolve` |
| 排队快照 | `GET/POST /api/queues` |
| 看板与人流地图 | `GET /api/dashboard/summary`、`GET /api/dashboard/map/{floorKey}` |

### 患者微信小程序接口

患者端使用 `/api/patient` 前缀，登录和注册响应会返回 Bearer Token。医院管理员会话与患者会话相互隔离。

| 功能 | 接口 |
| --- | --- |
| 患者注册、登录、当前用户、退出 | `POST /api/patient/auth/register`、`POST /api/patient/auth/login`、`GET /api/patient/auth/me`、`POST /api/patient/auth/logout` |
| 个人资料与近期身体状态 | `PATCH /api/patient/profile` |
| 医院和动态体检目录 | `GET /api/patient/hospitals`、`GET /api/patient/hospitals/{hospitalID}/catalog` |
| 创建、当前、历史和详情 | `POST /api/patient/plans`、`GET /api/patient/plans/current`、`GET /api/patient/plans`、`GET /api/patient/plans/{planID}` |
| 开始、完成和动态重排 | `POST /api/patient/plans/{planID}/steps/{detailID}/start`、`POST /api/patient/plans/{planID}/steps/{detailID}/complete`、`POST /api/patient/plans/{planID}/replan` |
| 院内导航信息 | `GET /api/patient/plans/{planID}/navigation?detailID=...` |

计划接口会在 Backend/Adapter 中把数据库实体转换为 `checkup_scheduler` 领域模型。小程序不包含算法副本，也不直接访问数据库。

套餐由医院管理员在 Web 管理端组合本医院的检查项目，并保存为草稿或上架。患者目录只返回所选医院已上架、且包含项目均处于启用状态的套餐；套餐下架后立即从目录消失，也不能再用旧套餐 ID 创建新计划。历史计划仍保留原套餐关联。

OpenAPI 交互文档在服务启动后的 `/docs`。

科室、项目、套餐和多楼层 GIS 可以通过一份标准 JSON 原子导入；业务 key、字段和 GeoJSON 关联规则见 [`workspace-import.md`](workspace-import.md)。

## GIS GeoJSON 约定

每个楼层上传一个 `FeatureCollection`。支持 `Point`、`LineString`、`Polygon` 和 `MultiPolygon`。

科室点位：

```json
{
  "type": "Feature",
  "properties": {
    "featureType": "department",
    "deptID": "数据库中的科室 ID",
    "name": "超声科"
  },
  "geometry": {"type": "Point", "coordinates": [120.5, 80.2]}
}
```

科室间路线可同步到 `department_distance`：

```json
{
  "type": "Feature",
  "properties": {
    "featureType": "route",
    "fromDeptID": "起点科室 ID",
    "toDeptID": "终点科室 ID",
    "distanceMeters": 72.5
  },
  "geometry": {"type": "LineString", "coordinates": [[10, 20], [30, 35]]}
}
```

地图坐标既可使用真实投影坐标，也可使用院内平面图坐标；同一楼层必须保持同一坐标系。人流量由有效排队快照人数与当前检查中的人数相加得到。
