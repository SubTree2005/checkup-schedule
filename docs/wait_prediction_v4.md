# V4 等待时间预测算法

## GitHub 现有方案结论

调研到的公开实现主要有三类：

| 代表项目 | 方法 | 可借鉴点 | 不直接采用的原因 |
| --- | --- | --- | --- |
| [Hospital-ED-Triage-Waiting-Time-Predictions](https://github.com/dlon450/Hospital-ED-Triage-Waiting-Time-Predictions) | NHAMCS 数据、随机森林/朴素贝叶斯 | 用真实历史就诊特征训练 | 把等待分钟离散成分类，主要是离线 notebook；没有实时队列、多设备和风险区间 |
| [emergency_department_wait_time_prediction](https://github.com/jack-milligan/emergency_department_wait_time_prediction) | 患者量、人员量、病情等级的线性回归 | 特征和 MAE/R² 评估清晰 | 数据和目标均为人工公式生成，不能证明真实场景效果 |
| [PED_pred](https://github.com/gu-yaowen/PED_pred) | LightGBM、XGBoost、随机森林等回归 | 最接近后续有历史数据时的模型比较框架 | 依赖旧、训练脚本与在线推理未拆分，也不输出调度需要的 p90 |
| [ER-Flow-Sim](https://github.com/SaashaJoshi/ER-Flow-Sim) 等 SimPy 患者流项目 | 离散事件仿真 | 能准确表达并行资源和排队过程 | 适合容量规划和离线评估，不负责从现场完成事件持续学习参数 |

因此 V4 没有照搬某个仓库，而是组合它们真正适用于本项目的部分：用队列仿真表达多设备，用在线统计解决冷启动，用回放指标验收，并继续保留外部机器学习模型的替换接口。

## 输入与口径

一次预测面向“现在加入科室队尾的患者”。

- `queued_patients`：当前等候、尚未开始检查的人数，不含正在检查者和目标患者；
- `capacity`：此刻实际可并行工作的设备/检查位数量，不是科室名义总数；
- `in_service_remaining_minutes`：各正在检查项目的预计剩余时间，数量不得超过容量；
- `recent_service_minutes`：近期已完成项目的实际服务耗时；
- `queued_service_minutes`：可选；若现场已知队列中每个项目的预计耗时，应按 FCFS 顺序完整提供；
- `operational_delay_minutes`：换床、消毒、设备准备等对目标患者确定存在的固定延迟。

首版默认队列规则是 FCFS。如果某科室有急诊插队、预约号和现场号混排，应由现场系统先把目标患者前方的有效队列及其项目耗时算入快照，而不是让预测器猜测优先级规则。

## 算法

### 1. 服务耗时在线学习

每完成一个项目，调用：

```python
predictor.observe_service_completion(
    department_id="ct",
    duration_minutes=16,
    observed_at=finished_at,
)
```

预测器维护两级 EWMA 均值和方差：

1. 科室全局统计；
2. `科室 × 工作日/周末 × 时段` 局部统计。

局部样本少时向科室全局均值收缩，避免刚上线时某个时段只出现一个异常长项目就把预测拉偏。快照中的近期耗时再以最高 60% 权重修正，以跟随当天临时变慢或变快。

### 2. 多设备 FCFS 工作量模拟

设每台设备距离空闲还有 `r_i` 分钟。容量大于正在工作设备数时，其余设备的 `r_i=0`。对队列中每个项目，依次分配给最早空闲设备：

```text
server = argmin(r)
r[server] += service_duration
```

所有前方项目分配后，目标患者等待时间为 `min(r) + operational_delay`。未知耗时场景下，`mean_minutes` 取多次模拟等待的平均值。

这个过程能正确处理两台设备分别剩余 1 分钟和 100 分钟的情况：新患者只需等 1 分钟，而旧版“总剩余工作量÷2”会误报 50.5 分钟。

### 3. p90 风险上界

未知的前方项目耗时按在线学习得到的正值对数正态分布抽样，重复进行多设备队列模拟。随机种子由快照内容稳定生成，因此同一模型状态和同一快照的输出完全可复现。

p90 还包含随历史样本增加而衰减的冷启动不确定度。若队列耗时已经全部明确，或没有未知的前方项目，不再无故增加服务时长不确定度。

### 4. 实际结果校准

低层预测接口仍支持在可信的设备/HIS 完成事件后直接调用：

```python
predictor.observe_wait_outcome(prediction, actual_wait_minutes=23)
```

患者端等待反馈不应逐条调用此低层接口；V6 应先通过 `GlobalWaitFeedbackController` 做科室级微批聚合和异常截断。个人移动活动则进入独立的 `PersonalActivityFeedbackController`，不能写入等待预测器，详见 `personal_activity_v6.md`。预测器保存平均等待偏差以及 `实际等待 - 当时 p90` 的滚动残差，样本达到阈值后校准后续 mean 和 p90。调度默认使用 p90；均值用于显示和评估。

## 状态持久化

`export_state()` 返回只包含字典、列表、数值和字符串的结构，可 JSON 序列化后存数据库。进程启动时用 `AdaptiveQueuePredictor.from_state(...)` 恢复。建议每批完成事件后事务性保存，并保留 `model_version`、更新时间和数据版本。

预测器不直接连接数据库，避免把算法内核绑定到腾讯云、MySQL 或具体后端。后端只需负责读取完成事件、构造快照、保存预测结果和状态。

## 历史回放与上线门槛

必须按日期顺序回放，先预测、再用当天已发生的结果更新模型，不能随机打乱后让未来数据泄漏到过去。`evaluate_wait_predictions(...)` 输出：

- 均值预测 MAE；
- RMSE；
- 平均误差（正数表示整体高估，负数表示低估）；
- p90 覆盖率。

首轮建议同时比较三个基线：固定科室平均值、V4 在线预测器、后续 LightGBM/XGBoost。只有在跨日期测试集上稳定优于固定平均值，并且 p90 覆盖率接近目标值时，才让预测直接影响正式路线；此前可先影子运行并记录误差。

## 后续机器学习升级

有足够数据后，可以训练两个独立的分位数模型：一个预测中位/均值，一个直接预测 p90。推荐特征包括科室、工作日类型、分钟时段、有效容量、前方人数、各设备剩余时间的最小值/最大值/总和、近期服务耗时统计、设备停机和预约负荷。

外部模型只需实现：

```python
class WaitTimePredictor(Protocol):
    def predict(self, snapshot: QueueSnapshot) -> WaitPrediction: ...
```

因此替换模型时不需要修改 Rolling Horizon、启发式排序或 CP-SAT。
