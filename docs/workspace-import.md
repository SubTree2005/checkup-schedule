# 医院工作区一键导入格式

医院 Web 管理端的“一键导入”使用一个 UTF-8 JSON 文件，同时新增或更新科室、检查项目、体检套餐和多楼层 GIS。页面中的“下载标准模板”会从 `GET /api/imports/template` 获取最新完整示例。

## 顶层结构

```json
{
  "formatVersion": "1.0",
  "mode": "upsert",
  "departments": [],
  "exams": [],
  "packages": [],
  "gis": []
}
```

所有关联都使用文件内的业务 `key`，不直接填写数据库 UUID。`key` 最长 64 个字符，只能包含英文字母、数字、点、下划线和连字符，并且必须以字母或数字开头。

相同医院、相同资源类型和相同 `key` 会定位到同一条记录，因此同一文件可以安全重复上传。导入采用 `upsert`：文件中出现的记录会新增或更新，未出现的现有记录不会删除。整份文件使用同一数据库事务，任一内容失败时全部回滚。

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

`departmentKey`、`prerequisiteItemKeys` 和 `conflictItemKeys` 必须引用同一文件中已声明的业务 key。医学状态条件继续放在 `prerequisites`；项目前置关系必须使用 `prerequisiteItemKeys`，不要直接填写 `itemIDs`。

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

`defaultDuration` 为 `0` 时自动累加所含检查项目的时长。已上架套餐只能包含处于启用状态的项目；不满足条件时整批导入会失败。

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

两个接口都要求医院管理员登录。导入结果会分别返回科室、项目、套餐和 GIS 的新增数、更新数，以及业务 key 到数据库 ID 的映射。
