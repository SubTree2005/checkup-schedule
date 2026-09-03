# Checkup Schedule

Checkup Schedule 是体检检查智能排序项目的 Monorepo。当前项目发布版本为 **0.4.0**，其中 Scheduler 算法为 **V10（等待优先版）**：它保留硬时间窗、前置关系、Rolling Horizon、等待预测与双反馈边界，以预计等待、未来拥堵、长时间停滞、步行和路线稳定性为主要软目标。

当前仓库已经包含经过现有测试与正式仿真使用的 Scheduler V10、Ground Truth 隔离的整日离散事件仿真、多随机种子 paired experiment、回归测试，以及可运行的 Backend API、数据库模型、医院 Web 管理端和患者微信小程序。

当前版本见 [`VERSION`](VERSION)。

## 目录结构

```text
apps/
  miniprogram/       患者微信小程序（登录、选检、排程、执行、记录、导航）
  admin-web/         医院 Web 管理端（看板、套餐上下架、GIS、人流与临时调整）
  backend/           FastAPI、SQLAlchemy、认证与多医院数据隔离
packages/
  scheduler/         唯一正式 Scheduler 实现
simulation/          Ground Truth、医院模型、paired experiment 与输出工具
tests/               快速单元、回归和集成测试
docs/                架构、开发和算法文档
examples/hospitals/  可直接一键导入的医院示例整合包
.github/workflows/   快速 CI
```

依赖方向固定为：`apps/backend -> packages/scheduler <- simulation`。Scheduler 不访问数据库；Backend/Adapter 负责准备患者、医院与资源状态并转换外部字段、类型和单位。

## 本地运行

需要 Python 3.11+。

```bash
python -m venv .venv
python -m pip install -e .
python -m unittest discover -s tests -v
```

核心入口：

```python
from checkup_scheduler import RollingHorizonScheduler, build_batch_schedule
```

如需 CP-SAT 可选后端：

```bash
python -m pip install -e ".[optimization]"
```

## Simulation

正式 V10 实验配置为 10 seeds × 200 人：

```bash
python -m simulation.run --v10 --patients 200 --replications 10 --seed 20260824 --scenarios normal_day --output simulation/output/v10
```

快速 smoke run 可缩小患者数与 replication 数：

```bash
python -m simulation.run --v10 --patients 20 --replications 2 --seed 20260824 --scenarios normal_day --output simulation/output/smoke
```

大型仿真结果默认忽略，不进入 CI。仿真设计和参数见 [`simulation/README.md`](simulation/README.md)。

## Backend、微信小程序与 Web 管理端

本地快速启动使用 SQLite；生产环境使用 MySQL：

```bash
python -m pip install -e ".[backend]"
DATABASE_URL=sqlite:///./checkup.db uvicorn apps.backend.checkup_backend.main:app --reload
```

打开 `http://127.0.0.1:8000` 即可注册医院账号并进入管理后台。注册时必须选择一份包含医院、科室、项目、套餐和 GIS 的完整工作区 JSON；账号、医院数据和 100 人固定演示患者池在同一事务内创建。注册后仍可通过后台重复一键导入更新数据，格式见 [`docs/workspace-import.md`](docs/workspace-import.md)。API 文档位于 `/docs`；生产环境变量和微信云托管说明见 [`docs/deployment-cloudbase.md`](docs/deployment-cloudbase.md)，数据模型与 GIS 格式见 [`docs/backend-api.md`](docs/backend-api.md)。

患者小程序位于 `apps/miniprogram`，使用微信开发者工具直接导入。体检套餐由医院在 Web 管理端上架，小程序按医院动态读取。开发环境默认请求 `http://127.0.0.1:8000`，体验版和正式版必须在 `apps/miniprogram/utils/runtime-config.js` 配置已加入微信公众平台白名单的 HTTPS API。注册会记录隐私政策版本，用户可在小程序内注销账号；院内导航会读取医院 GIS 并绘制楼层与同层推荐路线。配置方式和联调说明见 [`apps/miniprogram/README.md`](apps/miniprogram/README.md)。

### 一键导入数据

上传文件为 UTF-8 JSON，顶层格式如下：

```json
{
  "formatVersion": "1.0",
  "mode": "upsert",
  "hospital": {},
  "departments": [],
  "exams": [],
  "packages": [],
  "gis": []
}
```

`hospital` 可选，用于更新当前医院基本信息；其他数组可一次同时提交，也可只提交需要更新的部分。科室、项目和套餐必须使用稳定且唯一的业务 `key`，项目通过 `departmentKey` 关联科室，套餐通过 `includedItemKeys` 关联项目，GIS 科室点通过 `departmentKey` 关联科室。整包原子校验和写入，相同 `key` 重复上传会更新已有数据，不会删除文件中未出现的记录。

医院注册是例外：注册数据中的五个部分都不能为空，并且至少包含一个已上架套餐。系统会据此预生成该医院专属的 100 名演示患者及固定项目组合。创建者可通过侧边栏底部的隐藏演示入口指定当前纳入人数或全部撤回；未激活患者没有体检计划，不进入看板和人流计算。

可直接试用的完整数据见[紫金港校医院示例整合包](examples/hospitals/zijingang-campus-hospital/README.md)；字段、限制、GeoJSON 属性和接口说明见[上传格式文档](docs/workspace-import.md)。

## 三端结构

```text
微信小程序 ─┐
            ├── Backend API ── Database
Web 后台 ───┘
                    │
                    └── Scheduler
```

客户端不得直接访问数据库。多医院数据隔离及现有业务实体边界见 [`docs/architecture.md`](docs/architecture.md)，开发与验证命令见 [`docs/development.md`](docs/development.md)。
