# V9：关键路径、整套完成与安全反馈

## 目标与边界

V9 只修复 V8 暴露出的“局部等待优化损害整套完检”问题，不替换现有预测器、反馈控制器、Rolling Horizon 或仿真引擎。四层继续通过数据契约解耦：

1. 等待预测器输出科室级 `WaitPrediction`；
2. 个人活动预测器输出患者级 `PersonalActivityPrediction`；
3. 调度器只消费预测合同和当前可见状态；
4. 仿真执行引擎独占未来 Ground Truth。

手机端仍可通过 `AccelerometerBatch` 与注入的 `ActivitySensorAdapter` 生成个人活动反馈；等待反馈只按科室全局聚合。

## 下游截止反向传播

对患者的 prerequisite DAG，先由患者、科室、检查和规划窗口的交集计算每个任务自身的最晚完成时刻。然后按逆拓扑序计算：

```text
effective_finish(predecessor)
= min(
    own_finish(predecessor),
    effective_finish(successor)
      - duration(successor)
      - travel(predecessor, successor)
      - safety_buffer
  )
```

多 successor 取最小值；多级依赖自然递归传播。拓扑排序未覆盖全部任务时立即报告循环。无 successor 的任务保持自身截止时间。

调度候选的关键余量为：

```text
critical_slack = effective_latest_finish - predicted_finish
```

紧迫度使用 logistic 曲线，而非小线性权重。高风险候选进入高于普通等待/步行成本的优先层。为了避免低负载时过早改序，实际高风险窗口按当前 `pending_task_count / total_capacity` 从最低 45 分钟平滑放大到最多 180 分钟。

terminal/aggregator 不依赖 `INTERNAL` 名称：当前实现识别“无 successor 且至少有两个 prerequisite”的下游汇聚任务，并把压力传播到全部祖先。

## 启发式与 CP-SAT 目标

启发式的排序层次为：

1. completion risk 高的关键路径；
2. critical slack；
3. 原有 dispatch bucket；
4. 医疗延迟、公平性、步行和旧顺序软成本。

冻结窗口、进行中项目和已完成项目不受关键路径重排影响。所有原硬时间窗与容量规则保持不变。

CP-SAT 仍只做 60–120 分钟 Rolling Horizon 局部 refinement。邻域先选择 critical slack 小和科室冲突高的任务；启发式解作为可行保底与 hint。目标按数量级依次惩罚关键截止违例、关键迟到、患者完工时间和改序偏差。求解失败、超时或没有词典序改善时返回启发式结果。

## 在线反馈安全阀

等待反馈控制器保留去重、硬边界、最小 5 条微批和 MAD winsorization，并新增：

- mean/p90 residual 的单批更新上限；
- predictor 内部 bias 单次 EWMA 位移上限；
- 最近窗口 candidate residual replay；
- Bias 必须改善，且 MAE/RMSE 不得超过配置退化阈值；
- `FeedbackAcceptanceRule` 接口，为完整 production/candidate shadow predictor 预留替换点。

个人活动预测按患者独立维护，默认至少 3 条有效行程才生效，批次中位数后再做 factor update cap。任何个人速度变化都不会更新科室全局等待模型。

## 实验策略与场景

正式 2×2 消融：

- `no_feedback`
- `wait_feedback_only`
- `personal_activity_feedback_only`
- `dual_feedback`

Rolling/CP-SAT 入口：`rolling_heuristic`、`rolling_cp_sat`、`feedback_heuristic`、`feedback_cp_sat`。V8 comparator `rolling_no_feedback` 与 `dynamic_v6` 固定为旧调度/旧反馈语义，避免安全阀污染基线。

压力场景包括：`normal_day`、`morning_peak`、`terminal_bottleneck`、`late_arrival`、`device_breakdown`、`service_slowdown`、`predictor_bias` 和 `patient_interruption`。预测偏差运行 ±10%、±20%、±30%。

## 结果摘要

主实验为 V8 原始 10 seeds × 200 人、common random numbers 配对回放。完整结果见 `final_results/experiment_report.md`。

- V8 rolling：完成率 75.35%，mean wait 117.23，P90/P95 总历时 422.77/451.03。
- V9 无反馈：完成率 75.85%，mean wait 110.39，P90/P95 总历时 426.92/455.22。
- 完成率配对提升 +0.50 pp，95% CI [+0.02, +0.98]。
- mean/P90/P95 wait 配对改善 6.83/11.87/12.02 分钟，CI 均不跨 0。
- P90/P95 总历时点估计各增加约 4.2 分钟，但 CI 跨 0；尾部目标未完全达到。
- 个人活动反馈相对 V9 无反馈完成率近似中性，等待没有改善；等待反馈和双反馈分别损失约 1.10/1.25 pp 完成率。
- V9 无反馈平均 INTERNAL 漏做 48.3 人/seed，V8 rolling 为 49.3；改善很小，终点问题仍存在。

最终 3 seeds × 60 人压力测试中，大部分场景完成率持平，late arrival 提高约 0.56 pp；失败场景为 service slowdown 与 terminal bottleneck，各下降约 0.56 pp。不得把这两个场景隐藏在总体平均中。

## CP-SAT 验证限制

项目声明 `ortools>=9.10`，并新增时间窗、容量、前置关系和 fallback 测试。本次执行环境阻止访问 Python 包源，`python -m pip install -e ".[optimization]"` 未能安装 OR-Tools，因此 4 个 CP-SAT 测试被跳过，CP-SAT 实验没有合法结果。不能据此声称 CP-SAT 有收益；在可联网环境安装 extra 后必须重跑全部测试与 `feedback_cp_sat` 配对实验。

## GitHub 方案核对

- Google OR-Tools 官方 `flexible_job_shop_sat.py` 使用 interval/optional interval、资源 no-overlap、任务顺序与 makespan 目标；V9 的局部 CP-SAT 沿用这些成熟原语，而不是自建求解器：https://github.com/google/or-tools/blob/stable/examples/python/flexible_job_shop_sat.py
- OR-Tools 官方 scheduling recipes 汇总 interval、NoOverlap/Cumulative、precedence 和求解建模注意事项：https://github.com/google/or-tools/blob/stable/ortools/sat/docs/scheduling.md
- River 提供 `learn_one`/progressive validation 等完整在线学习框架，但其 README 也建议先判断在线 ML 是否真的必要。V9 只保留小型 EWMA + guarded residual update，避免在仿真证据不足时引入新框架：https://github.com/online-ml/river
