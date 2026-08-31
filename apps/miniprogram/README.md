# 检畅微信小程序

原生微信小程序客户端，通过 Backend API 完成患者注册登录、医院/套餐/项目读取、智能计划生成、执行状态同步、动态重排、历史记录和院内导航。

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

正式发布前，需要在微信公众平台配置合法 HTTPS request 域名。患者会话保存在本地并作为 Bearer Token 发送；客户端不直接访问数据库，也不包含 Scheduler 实现。
