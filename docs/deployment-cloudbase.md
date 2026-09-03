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
- `CHATANYWHERE_API_KEY`：AI 助手服务端访问凭据，只能配置为云托管密钥型环境变量，不能写入小程序或仓库；默认接口为 `https://api.chatanywhere.tech/v1/chat/completions`，默认模型为 `deepseek-v4-flash`。

## 微信订阅提醒

先在微信公众平台“订阅消息”中选用一次性模板，再按该模板的实际字段配置云托管环境变量：

```text
WECHAT_APP_ID=小程序AppID
WECHAT_APP_SECRET=小程序AppSecret
WECHAT_REMINDER_TEMPLATE_ID=订阅消息模板ID
WECHAT_REMINDER_DATA_TEMPLATE={"thing1":{"value":"{hospital}"},"time2":{"value":"{appointment}"},"thing3":{"value":"{package}"}}
WECHAT_REMINDER_PAGE=pages/record/record
WECHAT_MINIPROGRAM_STATE=trial
WECHAT_REMINDER_LANG=zh_CN
WECHAT_TRUST_CLOUDBASE_IDENTITY=true
REMINDER_DISPATCH_TOKEN=至少32字节的随机字符串
```

`WECHAT_REMINDER_DATA_TEMPLATE` 的键必须与微信公众平台中该模板的字段完全一致；上面的 `thing1/time2/thing3` 仅为格式示例。可使用 `{hospital}`、`{appointment}`、`{package}` 和 `{preparation}` 占位符。AppSecret 与派发令牌只能放在后端环境变量，不能写入小程序代码或仓库。体验版使用 `trial`，正式版改为 `formal`。

服务启动后，`GET /api/patient/reminders/config` 只有在上述发送配置和云托管身份透传都有效时才返回可用。小程序通过 `wx.cloud.callContainer` 访问时，网关会注入 `x-wx-openid` 与 `x-wx-appid`；普通 HTTP 请求不能代替这条可信身份链路。

最后在 CloudBase 定时任务中每分钟调用一次：

```http
POST /api/internal/reminders/dispatch
X-Reminder-Dispatch-Token: 与 REMINDER_DISPATCH_TOKEN 相同
```

可使用同环境的定时云函数，Cron 为 `0 * * * * * *`（七段表达式），也可使用能安全保存派发令牌的外部调度器。任务可重复调用：接口只领取到期且未发送的记录，失败后按 1、5、30 分钟退避，最多尝试 3 次。配置完成后先用体验版创建一个数分钟后的测试预约，并在 `GET /api/patient/reminders` 或数据库 `wechat_reminder` 表确认状态从 `pending` 变为 `sent`。

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

当前 MVP 在启动时创建缺失表（包括隐私同意记录表 `user_consent` 和提醒表 `wechat_reminder`），不会删除已有表或数据。正式进入持续迭代后，应在第一次破坏性字段变更前引入版本化迁移工具。生产数据库应开启自动备份，数据库账号只授予业务所需权限，并在发布前验证一次备份恢复流程。

## 管理员密码应急重置

生产环境没有通过公网开放免认证的“忘记密码”接口。需要应急重置时，在可信本机运行：

```powershell
python scripts/generate_admin_password_hash.py --phone 13800000000 --sql-output build/reset-admin-password.sql
```

脚本会隐藏输入并要求二次确认，然后生成完整事务 SQL。由数据库管理员在目标数据库中执行该文件，确认返回的 `stored_length` 为 `90`。生成文件只包含不可逆 PBKDF2 哈希，但仍应在执行完成后删除；不要把明文密码、哈希或 SQL 文件提交到 Git。
