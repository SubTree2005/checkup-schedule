# Scheduler package

`checkup_scheduler/` 是仓库唯一正式 Scheduler 实现。项目发布版本为 0.1.0，当前算法版本为 V10。它包含领域模型、约束排程、批量启发式、Rolling Horizon、等待/移动预测、反馈控制、关键路径、可选 CP-SAT 混合优化和医学规则扩展接口。

Scheduler 只接收已经准备好的患者、检查、科室、资源日历和预测数据，不直接连接 PostgreSQL 或任何业务系统。外部 API/数据库字段与内部稳定模型之间的转换属于未来 Backend/Adapter 层。

从仓库根目录安装并运行：

```bash
python -m pip install -e .
python -c "from checkup_scheduler import RollingHorizonScheduler, build_batch_schedule"
```

OR-Tools 是可选依赖；未安装时生产混合调度可回退到启发式，但明确请求 CP-SAT 的正式对照实验会失败并提示安装 `.[optimization]`。
