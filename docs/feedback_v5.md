# V5 反馈层迁移说明

V5 曾把实际等待时间和“活动时间”放进同一个反馈对象。V6 根据实际业务含义拆成两条链路：

- `WaitTimingFeedback` → `GlobalWaitFeedbackController` → 科室级 `AdaptiveQueuePredictor`；
- `PersonalActivityFeedback` → `PersonalActivityFeedbackController` → 患者级 `AdaptivePersonalActivityPredictor`。

V5 的 `RobustFeedbackController` 和 `PatientTimingFeedback` 名称仍保留为等待链路的兼容别名，但不再接受 `actual_activity_minutes`。新代码应使用含义明确的 V6 类名。

个人活动、手机加速度传感器和调度接入方式见 `personal_activity_v6.md`；全局等待校准见 `wait_prediction_v4.md`。
