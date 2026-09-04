# Backend API

FastAPI + SQLAlchemy 后端，提供医院管理员认证、多医院数据隔离、患者计划查询、科室与检查项目 CRUD、体检套餐管理及上下架、GIS 版本管理、异常上报、系统内排队估算、人流地图和数据看板接口。

医院工作区可通过标准 JSON 一次导入科室、项目、多个套餐和多楼层 GIS。管理端可直接下载模板，详细格式见 [`../../docs/workspace-import.md`](../../docs/workspace-import.md)。

```bash
python -m pip install -e ".[backend]"
DATABASE_URL=sqlite:///./checkup.db uvicorn apps.backend.checkup_backend.main:app --reload
```

生产环境应使用 MySQL，并设置 `COOKIE_SECURE=true`；医院位于中国大陆时保持 `HOSPITAL_TIMEZONE=Asia/Shanghai`，其他地区填写对应 IANA 时区。数据库实体保持《需求分析说明书》第 4.4 节的 15 张业务表，另增加 `hospital_admin`、`hospital_settings`、`user_session`、`user_consent`、`hospital_gis`、`demo_patient_profile` 和 `wechat_reminder` 七张支撑表。患者注册必须提交当前隐私政策版本，注销账号时会校验密码并删除患者侧业务数据。

小程序 AI 助手统一通过后端代理访问模型服务。部署时以密钥型环境变量设置 `CHATANYWHERE_API_KEY`，不要把真实密钥写进小程序、源码或镜像；服务地址与模型默认分别为 `https://api.chatanywhere.tech/v1/chat/completions` 和 `deepseek-v4-flash`，可通过 `.env.example` 中的同名变量覆盖。患者可在当前设备选择自定义模型，并可选填自己的访问密钥；该密钥只随 HTTPS 请求转发，不写入服务端数据库或响应内容。

管理后台由同一进程从 `apps/admin-web` 提供，客户端始终只访问 Backend API。
