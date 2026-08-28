# Backend API

FastAPI + SQLAlchemy 后端，提供医院管理员认证、多医院数据隔离、科室与检查项目 CRUD、GIS 版本管理、异常上报、排队快照、人流地图和数据看板接口。

```bash
python -m pip install -e ".[backend]"
DATABASE_URL=sqlite:///./checkup.db uvicorn apps.backend.checkup_backend.main:app --reload
```

生产环境应使用 MySQL，并设置 `COOKIE_SECURE=true`。数据库实体保持《需求分析说明书》第 4.4 节的 15 张业务表，另增加 `hospital_admin`、`user_session` 和 `hospital_gis` 三张支撑表。

管理后台由同一进程从 `apps/admin-web` 提供，客户端始终只访问 Backend API。
