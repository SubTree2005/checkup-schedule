# Simulation

这里保留现有最新版整日仿真，不属于普通单元测试：

- `ground_truth.py`：提前采样并隔离隐藏现实；
- `engine.py`：200 人场景、患者生成、医院资源与一分钟离散事件执行；
- `experiments.py`：多策略、多 seed、common-random-numbers paired experiment 与指标；
- `experiment_io.py` / `io.py`：manifest、CSV、JSONL、trajectory 和报告输出；
- `run.py`：命令行入口。

仿真直接 import `checkup_scheduler` 正式包，不包含 Scheduler 副本。

快速运行：

```bash
python -m pip install -e .
python -m simulation.run --v10 --patients 20 --replications 2 --seed 20260824 --scenarios normal_day --output simulation/output/smoke
```

正式 normal-day 复现实验：

```bash
python -m simulation.run --v10 --patients 200 --replications 10 --seed 20260824 --scenarios normal_day --output simulation/output/v10
```

输出目录被 `.gitignore` 排除。完整 200 人 × 多 seed 仿真不作为默认 CI 步骤。
