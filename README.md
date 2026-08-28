# Checkup Schedule

Checkup Schedule 是体检检查智能排序项目的 Monorepo。当前项目发布版本为 **0.2.0**，其中 Scheduler 算法为 **V10（等待优先版）**：它保留硬时间窗、前置关系、Rolling Horizon、等待预测与双反馈边界，以预计等待、未来拥堵、长时间停滞、步行和路线稳定性为主要软目标。

当前仓库已经包含经过现有测试与正式仿真使用的 Scheduler V10、Ground Truth 隔离的整日离散事件仿真、多随机种子 paired experiment、回归测试，以及可运行的 Backend API、数据库模型和医院 Web 管理端。微信小程序仍为待开发骨架。

当前版本见 [`VERSION`](VERSION)。

## 目录结构

```text
apps/
  miniprogram/       微信小程序骨架
  admin-web/         医院 Web 管理端（看板、GIS、人流、基础信息与临时调整）
  backend/           FastAPI、SQLAlchemy、认证与多医院数据隔离
packages/
  scheduler/         唯一正式 Scheduler 实现
simulation/          Ground Truth、医院模型、paired experiment 与输出工具
tests/               快速单元、回归和集成测试
docs/                架构、开发和算法文档
.github/workflows/   快速 CI
```

依赖方向固定为：`apps/backend -> packages/scheduler <- simulation`。Scheduler 不访问数据库；未来由 Backend/Adapter 准备患者、医院与资源状态并转换外部字段、类型和单位。

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

## Backend 与 Web 管理端

本地快速启动使用 SQLite；生产环境使用 MySQL：

```bash
python -m pip install -e ".[backend]"
DATABASE_URL=sqlite:///./checkup.db uvicorn apps.backend.checkup_backend.main:app --reload
```

打开 `http://127.0.0.1:8000` 即可注册医院账号并进入管理后台，API 文档位于 `/docs`。生产环境变量和微信云托管说明见 [`docs/deployment-cloudbase.md`](docs/deployment-cloudbase.md)，数据模型与 GIS 格式见 [`docs/backend-api.md`](docs/backend-api.md)。

## 三端结构

```text
微信小程序 ─┐
            ├── Backend API ── Database
Web 后台 ───┘
                    │
                    └── Scheduler
```

客户端不得直接访问数据库。多医院数据隔离及现有业务实体边界见 [`docs/architecture.md`](docs/architecture.md)，开发与验证命令见 [`docs/development.md`](docs/development.md)。
