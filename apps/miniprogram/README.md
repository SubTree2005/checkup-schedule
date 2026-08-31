# 检畅微信小程序

原生微信小程序客户端，通过 Backend API 完成患者注册登录、医院/套餐/项目读取、智能计划生成、执行状态同步、动态重排、历史记录和院内导航。

套餐不是小程序内的静态数据：医院管理员在 Web 管理端的“套餐管理”中选择检查项目并上架，小程序随后通过医院目录接口读取；下架套餐不会继续展示，也不能用于新建体检计划。

## 本地联调

1. 在仓库根目录安装并启动后端：

   ```bash
   python -m pip install -e ".[backend,test]"
   uvicorn apps.backend.checkup_backend.main:app --reload
   ```

2. 在微信开发者工具中导入本目录。开发环境默认请求 `http://127.0.0.1:8000`。
3. 如需切换 API 地址，在开发者工具控制台执行：

   ```js
   wx.setStorageSync('apiBaseUrl', 'https://your-api.example.com')
   ```

## 体验版与正式版

编辑 [`utils/runtime-config.js`](utils/runtime-config.js)，把 `PRODUCTION_API_BASE_URL` 替换为实际 Backend HTTPS 地址：

```js
const PRODUCTION_API_BASE_URL = 'https://api.example-hospital.cn'
```

体验版和正式版不会读取本地 `apiBaseUrl` 覆盖，也不会回退到 localhost；仍保留 `api.example.com` 等示例值时会直接提示未配置，避免把本地地址误发到线上。随后在微信公众平台“开发管理 → 开发设置 → 服务器域名”中，把同一来源加入 `request` 合法域名。

正式提审前还需要：

1. 在微信公众平台完成小程序备案、服务类目和隐私保护指引；
2. 使用实际认证主体替换平台资料中的运营方名称与联系方式；
3. 在体验版真机验证注册授权、套餐读取、计划生成、GIS 导航和账号注销；
4. 为审核人员准备长期有效的普通患者测试账号，不能使用后台演示患者池账号登录患者端。

患者会话保存在本地并作为 Bearer Token 发送；客户端不直接访问数据库，也不包含 Scheduler 实现。注册时必须同意 `v0.3.1-2026-08-31` 版用户协议和隐私政策，Backend 会记录同意版本；“我的 → 账号与隐私”支持密码确认后注销账号。

GIS 导航直接使用医院工作区中上传的 GeoJSON：同楼层时从 `corridor`/`route` 线计算最短路径，跨楼层时显示目标楼层图和换层提示。路网、科室点与现场不一致时必须先在医院工作区中修正数据。
