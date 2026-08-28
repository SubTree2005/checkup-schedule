# 架构原则

## 组件边界

```text
微信小程序 ─┐
            ├── Backend API ── Database
Web 后台 ───┘
                    │
                    └── packages/scheduler
```

客户端只访问 Backend API，不直接连接数据库。Backend 负责认证、权限、持久化、事务、审计以及外部业务模型到 Scheduler 内部稳定模型的字段、类型和单位转换。Scheduler 保持纯领域逻辑，不读取 PostgreSQL。

`simulation/` 与 Backend 使用同一份 `packages/scheduler/checkup_scheduler`，禁止复制第二份 Scheduler。

## 规划闭环

系统沿用现有闭环：规划 → 执行 → 反馈 → 再规划。后续业务实体包括：

`user_info`、`hospital_info`、`department_info`、`exam_info`、`package_info`、`user_status_info`、`exam_plan`、`plan_execution_detail`、`anomaly_report`、`department_distance`、`user_mobility_profile`、`walk_speed_preset`、`queue_snapshot`、`department_waiting_stats`、`department_resource_calendar`。

`plan_execution_detail.itemID` 关联 `exam_info`。资源记录表正式名称是 `department_resource_calendar`。当前仓库不另建重复数据模型，也不为了对齐这些外部名称而改动已验证的 Scheduler 内部字段。

## 多医院隔离

多医院账号遵循：登录用户 → Backend 根据认证上下文确定 `hospitalID` → 数据库查询与写入按 `hospitalID` 隔离。

Backend 不得无条件信任客户端提交的 `hospitalID`；必须以服务端认证和授权结果为准。当前管理员登录后由 `hospital_admin` 解析所属医院，所有科室、项目、GIS、排队和异常查询均强制按该医院过滤；越权访问统一表现为资源不存在。

## 管理端部署边界

Web 管理端作为静态资源由 FastAPI 同源提供，登录凭据使用 HttpOnly、SameSite Cookie。生产环境数据库使用 MySQL，SQLite 仅用于本地开发和自动化测试。容器同时提供 `/api/*` 与管理页面，减少微信云托管的服务数量和跨域配置。
