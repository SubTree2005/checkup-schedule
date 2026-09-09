# 浙大二院浙大院区（紫金港）室内 GIS 初稿

该目录保存从用户提供的 1F、2F 栅格平面图和六个百度地图控制点构建的、可追溯的室内 GIS 初稿。它面向两个用途：

1. 写入 PostgreSQL/PostGIS，维护楼层、功能空间、POI 和患者通行网络；
2. 从通行网络派生 `checkup_scheduler.models.TravelTimeMatrix` 所需的科室间步行分钟数。

## 数据质量边界

- 坐标控制点原始坐标系为 `BD-09LL`；GeoJSON 输出经过 `BD-09LL → GCJ-02 → WGS84` 的确定性近似转换。
- 六点仿射配准 RMSE 约为 2 米，属于地图取点精度，不是测绘成果。
- 房间面和通行线是根据栅格图人工粗描的首版数据；空间边界、门位、门禁及楼梯名称需要现场核验。
- `BLOOD`、`URINE` 到“检验科”的映射只是低置信度候选；`XRAY` 到“放射科”为中置信度映射。
- 楼层几何是粗略包络，不应作为精确建筑外墙或产权边界。

## 文件

- `source/1f_features.json`：像素空间控制点、功能空间、POI 和通行图的唯一人工源数据。
- `schema.sql`：最小 PostGIS 数据契约。
- `generated/1f_level.geojson`：1F 粗略楼层包络。
- `generated/1f_spaces.geojson`：功能空间面。
- `generated/1f_pois.geojson`：兴趣点及调度位置候选。
- `generated/1f_route_nodes.geojson`、`1f_route_edges.geojson`：室内通行网络。
- `generated/1f_scheduler_travel_times.csv`：当前能可靠或候选映射到调度器的位置间步行时间。
- `generated/1f_control_points.csv`：原始控制点及配准残差。
- `generated/1f_metadata.json`：来源、变换系数和质量元数据。
- `generated/1f_seed.sql`：在运行 `schema.sql` 后导入 1F 数据的幂等 SQL。
- `generated/2f_level.geojson`、`2f_spaces.geojson`、`2f_pois.geojson`：2F 楼层、34 个编号房间和对应 POI。
- `generated/2f_route_nodes.geojson`、`2f_route_edges.geojson`：2F 通行网络。
- `generated/2f_vertical_connections.csv`：四组 1F↔2F 楼梯连接。
- `generated/2f_metadata.json`：2F 楼层配准方法、检查点误差和估计绝对精度。
- `generated/2f_seed.sql`：2F 数据及跨层连接的幂等导入 SQL；必须在 1F 之后运行。
- `generated/3f_level.geojson`、`3f_spaces.geojson`、`3f_pois.geojson`：一号楼 3F 楼层、24 个编号房间和 POI。
- `generated/3f_route_nodes.geojson`、`3f_route_edges.geojson`：一号楼 3F 通行网络。
- `generated/3f_vertical_connections.csv`：一号楼两组 2F↔3F 楼梯连接。
- `generated/3f_metadata.json`、`3f_seed.sql`：3F 配准质量与 PostGIS 导入数据。

## 重新生成

在仓库根目录运行：

```powershell
python scripts/build_1f_gis.py
python scripts/build_2f_gis.py
python scripts/build_3f_gis.py
```

生成器只依赖 Python 标准库。人工修正应修改 `source/1f_features.json`，不要直接编辑 `generated/` 文件。

## 导入 PostGIS

```powershell
psql -d your_database -f gis/schema.sql
psql -d your_database -f gis/generated/1f_seed.sql
psql -d your_database -f gis/generated/2f_seed.sql
psql -d your_database -f gis/generated/3f_seed.sql
```

数据库统一保存近似 WGS84（SRID 4326）几何，同时在元数据和控制点 CSV 中保留 BD-09LL 来源，避免把百度坐标误标为 WGS84。

## 接入调度器

```python
from checkup_scheduler.gis import load_travel_time_matrix_csv

travel_times = load_travel_time_matrix_csv(
    "gis/generated/1f_scheduler_travel_times.csv",
    default_minutes=6,
)
```

当前 CSV 只输出图纸上能够建立映射的 `LOBBY`、`BLOOD`、`URINE`、`XRAY`。其余仿真科室不能从这张 1F 图上可靠定位，因此没有用猜测坐标补齐。

## 建筑归属

- 2F 图右侧圈定部分以及 3F 全部房间属于 `zju2_zijingang_b1`（一号楼）。
- 2F 图左侧未圈部分属于 `zju2_zijingang_b2`（二号楼）。
- 2F 原先用于消歧的“西区/中区/东区”仅保留在稳定内部 ID 中；对外名称已改为“一号楼/二号楼 + 房号”。
