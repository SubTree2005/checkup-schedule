# 演示患者资料导入

在 Web 管理端左侧底部的原演示工具入口（小圆点）打开弹窗，选择“患者资料导入”。“排队模拟”仍在同一个弹窗内。仅医院创建者可以使用这两个功能。

1. 填写手机号和登录密码（至少 8 位）。JSON 不包含手机号、密码或用户 ID，可以用于不同演示账号。
2. 新手机号会创建普通患者账号，可登录小程序。已有账号需验证当前密码：不改密码时，登录密码填写原密码；改密码时，在登录密码填写新密码，并填写当前密码。成功改密码后旧登录会话失效。管理员和固定排队模拟账号不能导入。
3. 点击“下载本院示例”，获得使用当前医院真实项目名称的最小示例；或选择 `examples/patients/campus-student-demo.json`，其中包含四次浙江大学校医院体检、44 个项目和 39 份虚构报告。
4. 文件预览显示患者姓名、体检次数、项目和报告数量。点击“导入演示患者”。
5. 在小程序使用页面中填写的账号登录，查看体检记录、历史体检和项目报告。列表使用 JSON 中的体检标题，报告按 JSON 原文显示，不额外拼接说明；后台保留 `isDemo`、`simulated` 来源标记。数据包不会创建真实预约或订阅提醒，不影响正在执行的计划。

## JSON 格式

根字段仅包含 `formatVersion`（固定 `patient-demo-1.0`）、`simulated`（固定 `true`）、`hospitalName`、`patient` 和 `visits`。`patient` 包含 `name`、`gender`、`age`、`medicalHistory`、`allergens`。接口拒绝未知字段，包括夹带的手机号、密码和用户 ID。

`hospitalName` 必须与当前登录医院完全相同。每次体检包含唯一 `recordKey`、展示标题 `title`、可选 `packageName` 和按时间排列的 `steps`。每个项目通过本院 `itemName` 与可选 `departmentName` 匹配；指定套餐时，项目必须属于该套餐。

项目的 `startedAt`、`completedAt` 和报告的 `reportedAt` 必须带时区，例如 `2026-09-04T08:00:00+08:00`。历史项目不能在未来，检查不能重叠，报告不能早于检查结束。`report: null` 表示检查已完成但没有报告；非空报告包含 `conclusion`、`reportedAt`、`items`。结果行包含 `label`、字符串 `value`、可选 `unit`、`reference`、`status`。

最多 12 次体检，每次最多 50 个项目，每份报告最多 40 行结果，Web 文件上限 2 MB。参考范围和结果均为演示数据，不是诊断依据。

## 保存与重复导入

导入在同一个数据库事务中完成。任何项目、套餐、账号或时间校验失败时，整批不写入。重复导入同一账号、同一医院、相同 `recordKey` 时，更新由本导入器创建的已完成记录的标题、资料和报告，保留记录 ID 与检查时间；未变化的记录跳过。项目结构变化、非导入记录或随后签发的非模拟报告会拒绝更新。要增加不同体检，请使用新的 `recordKey`。患者基本资料会使用本次上传内容更新。

更新已有“林”账号的报告文案：部署新版后端后，在管理端用原账号与密码重新上传 `examples/patients/campus-student-demo.json`。页面应显示“更新 4 次体检”，而不是新增；再进入小程序刷新体检记录。只修改本地 JSON 或重新上传小程序不会更新数据库里的旧报告。

后端新增 `plan_execution_detail.examReport` 可空 JSON 字段，启动时自动补列，现有记录保留且报告默认为空。小程序已有报告页面直接读取后端返回的报告，不需要修改 JSON 到小程序源码中。部署前保留数据库常规备份。

接口：`GET /api/demo-patients/import-template` 下载示例；`POST /api/demo-patients/import` 提交 `{phone, password, currentPassword?, bundle}`。密码只经登录同域接口提交并以哈希保存，不进入 JSON 模板或响应。
