# 开发说明

## 环境

```bash
python -m venv .venv
python -m pip install -e .
```

需要 CP-SAT 时安装可选依赖：

```bash
python -m pip install -e ".[optimization]"
```

开发 Backend 和管理端时安装：

```bash
python -m pip install -e ".[backend,test]"
DATABASE_URL=sqlite:///./checkup.db uvicorn apps.backend.checkup_backend.main:app --reload
```

## 快速验证

```bash
python -m compileall -q packages/scheduler apps/backend simulation tests
python scripts/audit_miniprogram.py
node scripts/test_miniprogram_runtime.js
python -m unittest discover -s tests -v
python -m simulation.run --v10 --patients 20 --replications 2 --seed 20260824 --scenarios normal_day --output simulation/output/smoke
```

默认 CI 只做编译与快速测试，不运行完整 200 人 × 多 seed benchmark。

## 修改边界

- 正式算法只放在 `packages/scheduler/checkup_scheduler/`。
- 仿真通过安装后的 `checkup_scheduler` 包调用算法。
- Backend/Adapter 负责业务模型转换和数据库访问。
- 不提交 `.env`、凭据、真实患者数据、数据库 dump 或大型仿真输出。
