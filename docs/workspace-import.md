# 医院工作区一键导入格式

医院 Web 管理端的“一键导入”使用一个 UTF-8 JSON 文件，同时新增或更新科室、检查项目、体检套餐和多楼层 GIS。页面中的“下载标准模板”会从 `GET /api/imports/template` 获取最新完整示例。

可直接上传的实际示例见[紫金港校医院示例整合包](../examples/hospitals/zijingang-campus-hospital/README.md)。

## 顶层结构

```json
{
  "formatVersion": "1.0",
  "mode": "upsert",
  "hospital": {
    "hospitalName": "医院名称",
    "address": "医院地址",
    "openTime": "08:00-17:00",
    "floorMapUrl": null
  },
  "departments": [],
  "exams": [],
  "packages": [],
  "gis": []
}
```

所有关联都使用文件内的业务 `key`，不直接填写数据库 UUID。`key` 最长 64 个字符，只能包含英文字母、数字、点、下划线和连字符，并且必须以字母或数字开头。

相同医院、相同资源类型和相同 `key` 会定位到同一条记录，因此同一文件可以安全重复上传。导入采用 `upsert`：文件中出现的记录会新增或更新，未出现的现有记录不会删除。整份文件使用同一数据库事务，任一内容失败时全部回滚。

`hospital` 为可选段；填写后会更新当前登录医院账号的名称、地址、开放时间和楼层图地址，不会创建另一家医院。

`openTime` 至少包含一个二十四小时制 `HH:MM-HH:MM` 时段，也可以填写多个时段，例如 `工作日08:00-12:00,13:30-17:00`。含“工作日”时排程会跳过周六、周日；全天开放可填写 `全天开放` 或 `24小时`。描述中的其他文字仅用于展示，不会自动推断法定节假日。

## 注册时的完整数据要求

医院注册页面直接选择同一格式的 JSON，并把它作为 `POST /api/auth/register` 的 `workspace` 字段提交。此时 `hospital`、`departments`、`exams`、`packages` 和 `gis` 五个部分都必须非空，并且至少要有一个 `isPublished: true` 的套餐。可在注册页通过 `GET /api/auth/register-template` 下载注册模板。

注册、完整数据导入和 100 人演示患者池生成是同一数据库事务，任何一项失败都不会留下半成品医院账号。注册完成后的普通一键更新仍允许只提交需要更新的部分，`hospital` 也仍是可选段。

## 科室 departments

```json
{
  "key": "ultrasound",
  "deptName": "超声科",
  "location": "1F-A12",
  "openTimeStart": "08:00",
  "openTimeEnd": "17:00",
  "capacity": 2,
  "isAvailable": true
}
```

## 检查项目 exams

```json
{
  "key": "abdominal-ultrasound",
  "departmentKey": "ultrasound",
  "itemName": "腹部超声",
  "duration": 15,
  "prerequisites": {"fastingHours": 8},
  "prerequisiteItemKeys": ["blood-routine"],
  "conflictItemKeys": [],
  "priority": 6,
  "allowedTimeSlots": {"start": "08:00", "end": "11:30"},
  "isCritical": true,
  "isActive": true
}
```

`departmentKey`、`prerequisiteItemKeys` 和 `conflictItemKeys` 必须引用同一文件中已声明的业务 key。医学状态条件继续放在 `prerequisites`；项目前置关系必须使用 `prerequisiteItemKeys`，不要直接填写 `itemIDs`。全部项目的前置关系必须是无环图，整包校验会拒绝直接或间接循环依赖。

`openTimeStart`、`openTimeEnd` 以及 `allowedTimeSlots.start/end` 必须使用补零后的 `HH:MM`。`allowedTimeSlots` 只能是空对象 `{}`（不限项目时段）或同时包含 `start`、`end`；结束时间必须晚于开始时间。

## 套餐 packages

```json
{
  "key": "basic",
  "packageName": "基础体检套餐",
  "packageType": "健康体检",
  "price": 399,
  "tag": "热门",
  "description": "覆盖常规检验与腹部超声",
  "includedItemKeys": ["blood-routine", "abdominal-ultrasound"],
  "defaultDuration": 0,
  "suitable": ["18 岁以上人群"],
  "notice": ["检查前保持空腹"],
  "isPublished": true
}
```

`defaultDuration` 为 `0` 时自动累加所含检查项目的时长。每个套餐必须包含其项目所需的全部前置项目，也不能同时包含任一对互斥项目；已上架套餐还只能包含处于启用状态的项目。不满足任一条件时整批导入会失败。

## GIS gis

每个数组元素对应一个楼层，`geojson` 使用标准 `FeatureCollection`。科室点位和路线同样通过业务 key 关联：

```json
{
  "floorKey": "1F",
  "geojson": {
    "type": "FeatureCollection",
    "features": [
      {
        "type": "Feature",
        "properties": {
          "featureType": "department",
          "departmentKey": "ultrasound"
        },
        "geometry": {"type": "Point", "coordinates": [20, 30]}
      },
      {
        "type": "Feature",
        "properties": {
          "featureType": "route",
          "fromDepartmentKey": "laboratory",
          "toDepartmentKey": "ultrasound",
          "distanceMeters": 60
        },
        "geometry": {
          "type": "LineString",
          "coordinates": [[80, 30], [20, 30]]
        }
      }
    ]
  }
}
```

导入后，Backend 会把 `departmentKey`、`fromDepartmentKey` 和 `toDepartmentKey` 转换为实际数据库 ID，并同步路线距离。支持 `Point`、`LineString`、`Polygon` 和 `MultiPolygon`。

## 接口

| 功能 | 接口 |
| --- | --- |
| 下载完整标准模板 | `GET /api/imports/template` |
| 校验并一键导入 | `POST /api/imports/workspace` |
| 下载医院注册模板 | `GET /api/auth/register-template` |

普通导入的两个接口要求医院管理员登录；注册模板可在登录前下载。导入结果会返回医院信息是否更新，分别返回科室、项目、套餐和 GIS 的新增数、更新数，并提供业务 key 到数据库 ID 的映射。
