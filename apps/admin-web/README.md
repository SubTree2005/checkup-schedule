# Admin Web

医院 Web 管理端由原生 HTML、CSS 与 JavaScript 实现，无需单独的 Node 构建步骤，由 Backend 同源提供。

主要功能：

- 医院账号注册与登录；
- 首页数据看板、GIS 楼层切换和人流热度展示；
- GeoJSON 上传与版本更新；
- 科室和检查项目维护；
- 科室关闭、设备故障、极度拥挤上报与恢复；
- 手工更新排队人数和预计等待时间。

地图中的科室 Point 通过 `properties.deptID` 与数据库科室关联。具体格式见 `docs/backend-api.md`。
