# 微信云托管部署

仓库根目录的 `Dockerfile` 会把 Backend API 和 Web 管理端打包为一个容器。服务读取平台提供的 `PORT` 环境变量并监听 `0.0.0.0`。

## 必需配置

在微信云托管服务中设置：

- 构建目录：仓库根目录；
- Dockerfile：`Dockerfile`；
- 监听端口：`8080`（或与平台 `PORT` 保持一致）；
- 健康检查：`GET /api/health`；
- `DATABASE_URL`：MySQL SQLAlchemy URL；
- `COOKIE_SECURE=true`；
- `HOSPITAL_TIMEZONE=Asia/Shanghai`：医院上传的营业时间、科室开放时间和检查时段使用的 IANA 时区，未设置时默认 `Asia/Shanghai`。

MySQL 示例：

```text
mysql+pymysql://checkup:强密码@内网地址:3306/checkup_schedule?charset=utf8mb4
```

如 Web 与 API 由同一服务访问，不需要设置 `ALLOWED_ORIGINS`。若未来拆分管理端域名，再将允许的完整来源以逗号分隔写入该变量。

## 首次上线

1. 先创建空的 UTF-8 MySQL 数据库和最小权限账号；
2. 在云托管服务配置环境变量，不要把密码提交到仓库；
3. 从仓库根目录构建并部署；
4. 通过 `/api/health` 确认服务正常，再访问首页注册第一家医院；
5. 上传科室信息和楼层 GeoJSON，随后在首页检查地图与人流显示。

当前 MVP 在启动时创建缺失表，不会删除已有表或数据。正式进入持续迭代后，应在第一次破坏性字段变更前引入版本化迁移工具。
