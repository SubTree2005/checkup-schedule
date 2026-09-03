# Backend API 与数据库

## 数据库范围

数据库实现《需求分析说明书》第 4.4 节定义的 15 张业务表：

`user_info`、`hospital_info`、`department_info`、`exam_info`、`package_info`、`user_status_info`、`exam_plan`、`plan_execution_detail`、`anomaly_report`、`department_distance`、`user_mobility_profile`、`walk_speed_preset`、`queue_snapshot`、`department_waiting_stats`、`department_resource_calendar`。支撑表另含 `hospital_admin`、`user_session`、`user_consent`、`hospital_gis`、`demo_patient_profile` 和 `wechat_reminder`。

为管理、认证、隐私同意、患者端医院设置和 GIS 增加六张支撑表：

- `hospital_admin`：管理员与医院的归属关系，是多医院隔离的服务端依据；
- `hospital_settings`：保存患者端医院图片、开放状态以及预约时段、号源容量和可预约天数；
- `user_session`：只保存随机登录令牌的 SHA-256 摘要、登录 IP 与有效期；
- `user_consent`：保存患者同意的隐私政策版本、时间和请求 IP，作为注册授权审计记录；
- `hospital_gis`：按医院和楼层保存 GeoJSON、版本号、更新人和更新时间。
- `demo_patient_profile`：保存每家医院注册时固定生成的 100 人演示患者池、套餐项目组合和当前激活计划引用。
- `wechat_reminder`：保存患者一次性订阅对应的 OpenID、模板、发送时间、消息快照、派发状态、重试次数和错误信息。

密码使用带随机盐的 PBKDF2-SHA256，数据库不保存明文密码。客户端提交的 `hospitalID` 不参与授权判断。

## 主要接口

| 功能 | 接口 |
| --- | --- |
| 注册、登录、当前账号、退出 | `POST /api/auth/register`、`POST /api/auth/login`、`GET /api/auth/me`、`POST /api/auth/logout` |
| 注册数据模板 | `GET /api/auth/register-template` |
| 医院资料、患者端图片与预约规则 | `GET/PATCH /api/hospital` |
| 科室 | `GET/POST /api/departments`、`PATCH/DELETE /api/departments/{deptID}` |
| 检查项目 | `GET/POST /api/exams`、`PATCH/DELETE /api/exams/{itemID}` |
| 体检套餐与上下架 | `GET/POST /api/packages`、`PATCH/DELETE /api/packages/{packageID}` |
| 工作区标准模板与一键导入 | `GET /api/imports/template`、`POST /api/imports/workspace` |
| GIS | `GET /api/gis`、`GET/PUT /api/gis/{floorKey}` |
| 异常 | `GET/POST /api/anomalies`、`POST /api/anomalies/{reportID}/resolve` |
| 排队快照 | `GET/POST /api/queues` |
| 看板与人流地图 | `GET /api/dashboard/summary`、`GET /api/dashboard/map/{floorKey}` |
| 演示患者池（仅创建者） | `GET /api/demo-patients`、`POST/DELETE /api/demo-patients/active` |

### 患者微信小程序接口

患者端使用 `/api/patient` 前缀，登录和注册响应会返回 Bearer Token。医院管理员会话与患者会话相互隔离。

| 功能 | 接口 |
| --- | --- |
| 患者注册、登录、当前用户、退出 | `POST /api/patient/auth/register`、`POST /api/patient/auth/login`、`GET /api/patient/auth/me`、`POST /api/patient/auth/logout` |
| 密码确认后注销患者账号 | `DELETE /api/patient/account` |
| 个人资料与近期身体状态 | `PATCH /api/patient/profile` |
| 医院、院区、预约号源和动态体检目录 | `GET /api/patient/hospitals`、`GET /api/patient/hospitals/{hospitalID}/appointment-slots`、`GET /api/patient/hospitals/{hospitalID}/catalog` |
| 创建、当前、历史和详情 | `POST /api/patient/plans`、`GET /api/patient/plans/current`、`GET /api/patient/plans`、`GET /api/patient/plans/{planID}` |
| 开始、完成、中断、继续、结束和动态重排 | `POST /api/patient/plans/{planID}/steps/{detailID}/start`、`POST /api/patient/plans/{planID}/steps/{detailID}/complete`、`POST /api/patient/plans/{planID}/pause`、`POST /api/patient/plans/{planID}/resume`、`POST /api/patient/plans/{planID}/finish`、`POST /api/patient/plans/{planID}/replan` |
| 院内导航信息与楼层 GIS 路线 | `GET /api/patient/plans/{planID}/navigation?detailID=...` |
| 微信提醒能力与本人提醒记录 | `GET /api/patient/reminders/config`、`GET /api/patient/reminders` |
| AI 助手模型状态与问答 | `GET /api/patient/agent/status`、`POST /api/patient/agent/chat` |
| 受保护的到期提醒派发 | `POST /api/internal/reminders/dispatch`，请求头必须携带 `X-Reminder-Dispatch-Token` |

计划接口会在 Backend/Adapter 中把数据库实体转换为 `checkup_scheduler` 领域模型。小程序不包含算法副本，也不直接访问数据库。

AI 问答必须经过患者登录鉴权，并由后端使用部署环境中的模型密钥转发。小程序只提交最近 20 条会话消息和当前页面标识，不接收模型密钥；页面跳转使用客户端固定白名单操作卡片，模型回复本身不能执行预约、取消、修改数据或任意路由。

预约创建请求只有在 `reminderSubscription.permission=accept`、模板 ID 与服务端一致，并且请求由微信云托管注入匹配 AppID 的 `x-wx-openid` 时才创建提醒。用户拒绝订阅时客户端不会提交该字段。提醒默认安排在预约前一天 20:00；如果已错过该时点，改为预约前 1 小时，仍已错过则立即进入待派发队列。派发失败会延迟重试，最多 3 次，所有结果均可从提醒记录接口审计。

套餐由医院管理员在 Web 管理端组合本医院的检查项目，并保存为草稿或上架。患者目录只返回所选医院已上架、且包含项目均处于启用状态的套餐；套餐下架后立即从目录消失，也不能再用旧套餐 ID 创建新计划。历史计划仍保留原套餐关联。

医院管理端的“医院设置”维护患者端实际读取的院区名称、地址、开放时间、图片与号源规则。医院名采用“机构名（院区名）”时，患者端会将同机构的多个医院账号组合为一个医院卡片，但每个院区仍保留独立 `hospitalID`、目录、号源和计划数据。预约页只展示服务端生成的真实时段；创建预约时服务端再次校验开放状态、时段边界与容量，不能通过伪造 `appointmentAt` 绕过。

工作区导入、检查项目编辑、套餐创建或更新、演示患者池准备及患者自选项目共用同一组约束校验：前置关系必须无环，套餐或计划必须包含全部前置项目，并拒绝互斥项目组合。患者端不会再静默丢弃未选择的前置关系。

OpenAPI 交互文档在服务启动后的 `/docs`。

科室、项目、套餐和多楼层 GIS 可以通过一份标准 JSON 原子导入；业务 key、字段和 GeoJSON 关联规则见 [`workspace-import.md`](workspace-import.md)。

医院注册请求必须在 `workspace` 字段中携带完整工作区，注册、导入和 100 人演示池生成使用同一事务。演示患者平时只有固定资料和项目组合；设置当前人数后才创建当天 `exam_plan` 与执行明细，撤回会删除这些运行记录但保留原患者池，因而再次激活不会重新随机。

普通患者创建计划时，服务端会在排程成功后创建 `user_status_info` 并写入该计划的 `recordID`。计划详情和历史列表中的 `profileSnapshot` 来自这条固定记录；患者之后修改资料或创建新计划，不会改变旧计划的状态快照。旧数据若没有 `recordID`，该字段返回空对象。

医院 `openTime`、科室 `openTimeStart/openTimeEnd`、检查 `allowedTimeSlots` 和按日统计均按 `HOSPITAL_TIMEZONE` 解释，默认 `Asia/Shanghai`。服务端在进入 Scheduler 和写入数据库前转换为无时区 UTC 时间，API 的时间戳继续以 `Z` 输出。医院开放时间可包含多个 `HH:MM-HH:MM` 时段（例如 `工作日08:00-12:00,13:30-17:00`）；含“工作日”时，闭诊后生成的计划会自动跳到下一个工作日。患者排程会取医院、科室和检查三层时间窗的交集；交集不足以完成项目时返回不可排程，而不会越过午休或闭诊时间。

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
