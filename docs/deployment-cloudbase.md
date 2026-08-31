# 微信云托管部署

仓库根目录的 `Dockerfile` 会把 Backend API 和 Web 管理端打包为一个容器。服务读取平台提供的 `PORT` 环境变量并监听 `0.0.0.0`。

## 必需配置

在微信云托管服务中设置：

- 构建目录：仓库根目录；
- Dockerfile：`Dockerfile`；
- 监听端口：`8080`（或与平台 `PORT` 保持一致）；
- 健康检查：`GET /api/health`；
- 微信云托管 MySQL 环境变量：`MYSQL_ADDRESS`、`MYSQL_DATABASE`、`MYSQL_USERNAME`、`MYSQL_PASSWORD`；
- `COOKIE_SECURE=true`；
- `HOSPITAL_TIMEZONE=Asia/Shanghai`：医院上传的营业时间、科室开放时间和检查时段使用的 IANA 时区，未设置时默认 `Asia/Shanghai`。

微信云托管示例：

```text
MYSQL_ADDRESS=10.0.0.1:3306
MYSQL_DATABASE=checkup_schedule
MYSQL_USERNAME=checkup_app
MYSQL_PASSWORD=控制台中设置的密码
```

后端会安全处理密码中的特殊字符。其他托管平台仍可直接设置 `DATABASE_URL`，格式为
`mysql+pymysql://用户名:密码@地址:3306/数据库?charset=utf8mb4`；若两种方式同时存在，优先使用 `DATABASE_URL`。

如 Web 与 API 由同一服务访问，不需要设置 `ALLOWED_ORIGINS`。若未来拆分管理端域名，再将允许的完整来源以逗号分隔写入该变量。

## 首次上线

1. 先创建空的 UTF-8 MySQL 数据库和最小权限账号；
2. 在云托管服务配置环境变量，不要把密码提交到仓库；
3. 从仓库根目录构建并部署；
4. 通过 `/api/health` 确认服务正常，再访问首页注册第一家医院；
5. 上传科室信息和楼层 GeoJSON，随后在首页检查地图与人流显示。
6. 把云托管环境 ID 和服务名写入 `apps/miniprogram/utils/runtime-config.js` 的 `PRODUCTION_CLOUD_CONTAINER`；小程序通过 `wx.cloud.callContainer` 访问，不需要配置 `request` 合法域名；
7. 创建普通患者测试账号，在体验版真机完成注册、计划、导航和注销验收。

当前 MVP 在启动时创建缺失表（包括隐私同意记录表 `user_consent`），不会删除已有表或数据。正式进入持续迭代后，应在第一次破坏性字段变更前引入版本化迁移工具。生产数据库应开启自动备份，数据库账号只授予业务所需权限，并在发布前验证一次备份恢复流程。
