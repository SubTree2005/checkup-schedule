# 浙大二院浙大院区（紫金港）室内 GIS v2

**导航级草案，非测绘成果，尚未现场核验。** 以原始 1F/2F/3F 图纸重新描绘；旧版只保存在 `audit/legacy` 用于审计对照，生成器不读取它。完整审计见 [AUDIT.md](AUDIT.md)，可视核验入口为 [generated/preview.html](generated/preview.html)。

当前版本有 120 个空间面（含通道、内院及用途未知区域）、83 个门/开口、204 个路网节点、202 条同层边、100 个 POI、5 条按图推断的楼梯跨层连接。没有推测电梯，也没有二号楼 3F。只有检验科、输液室进入调度科室映射；另提供明确的门诊入口起点 `ENTRANCE`。其余房号不自动绑定科室。

## 目录与唯一数据来源

| 路径 | 内容 |
| --- | --- |
| `source/images/1F.jpg`、`2F.jpg`、`3F.jpg` | 原图字节副本，已复核 SHA-256 和尺寸 |
| `source/evidence.json` | 来源、原路径、实际/仅旧版报告的摘要、缺失证据状态 |
| `source/*f_features.json` | 原始像素坐标中的空间、开口、通道中心线、楼栋及未解决项；人工编辑入口 |
| `source/registration.json` | 六点 BD-09LL、旧标注像素、像素转移假设、楼层锚点和独立检查点 |
| `source/vertical_connections.json` | 跨层连接的依据、禁用项、未知门禁和假设成本 |
| `source/scheduler_mappings.json` | 有证据的映射和逐项排除原因、步速假设 |
| `source/department_key_contract.py` | 当前示例业务 key 的审计快照；不作为科室位置证据、不执行 |
| `generated/*_spaces.geojson` | 概化空间边界；通道面与房间面以 `use_type` 区分 |
| `generated/*_openings.geojson` | 独立的门/开口线，关联空间及门口路由节点 |
| `generated/*_route_*.geojson` | 已在所有相交点分割的门外路网 |
| `generated/*_pois.geojson` | 房间内标签点；`route_node_id` 指向门口，未知门位为 null |
| `generated/control_points.csv`、`metadata.json` | 转换中间坐标、残差、留一法、变换矩阵、输入/代码摘要 |
| `generated/seed.sql`、`import_manifest.json` | 同一事务内按外键顺序导入所有楼层，并更新本院区快照 |
| `generated/scheduler_travel_times.csv`、`.json` | 步行秒、向上取整分钟、沿途 node/edge ID、证据与源包摘要 |
| `generated/workspace_gis_only.json` | 当前应用可导入的纯 GIS 包，不修改医院、科室、项目和套餐 |
| `generated/*_overlay.svg`、`.png`、`*_feature_index.csv` | 原图叠加边界、路线、门和编号；SVG 可悬停看 ID |
| `audit/*_legacy_overlay.png`、`legacy_path_conflicts.csv` | 旧几何的对照图与穿越新描边空间的核验提示 |
| `audit/1f_control_registration.png`、`registered_floors_on_1f.png` | 控制点残差图和跨楼层配准叠加 |

预览中 `?` 表示门位尚未确认、没有接入路网，**不能对这些 POI 做最近节点吸附后假称可达**。B1/B2 分别表示一号楼/二号楼；2F 重复房号同时展示楼栋。内部 n/s/w/e 与“图上/图下”等名称描述图像方向，不声称地理方位。

## 重新生成与验证

在当前 `checkup-schedule` 项目根目录运行：

```powershell
python -m pip install -e ".[gis,backend,test,gis-postgis-test]"
python scripts/build_indoor_gis.py
python scripts/audit_indoor_gis.py
python -m unittest discover -s tests -p "test_gis*.py" -v
```

三个 `build_1f_gis.py` / `build_2f_gis.py` / `build_3f_gis.py` 兼容入口均调用统一生成器，一次生成全部楼层；无需按层执行，也不依赖上一次输出。可以用 `--output DIR` 在空目录验证重复生成。不要编辑 `generated`；源图哈希变化会使构建失败，空间无效、路径穿房、开口离墙和断网也会使构建失败。失败时可看 `validation.json` 与预览定位问题，不应导入旧的剩余输出。

GIS 构建依赖为可选 extra，调度器读取 CSV 只用标准库。当前 Windows 验证使用 Python 3.12、NumPy 2.5.2、Pillow 12.3.0、Shapely 2.1.2、pyproj 3.7.2；pyproj 限定 `<3.8` 以避开本机新版本 DLL 加载失败。重复生成测试比较同一环境中的全部文件字节，不将跨平台浮点尾数一致性冒充可重复精度。

实库测试设置 `GIS_TEST_DSN`，例如在 **开发用 PostgreSQL** 上：

```powershell
$env:GIS_TEST_DSN = "postgresql://tester:password@127.0.0.1:5432/postgres"
python -m unittest discover -s tests -p "test_gis_postgis.py" -v
```

测试账户需要 CREATEDB 权限及可安装 PostGIS 扩展的权限。测试新建随机名称的专用数据库，完成后仅删除这些数据库。未设置 DSN 时会明确跳过实库测试；独立的 `.github/workflows/gis.yml` 配置了 PostGIS 服务并强制执行。测试记录见 [audit/verification.json](audit/verification.json)。

## 坐标与精度边界

1. 用户六点坐标原样保存为 **BD-09LL**。原图像素按图左上为原点、x 向右、y 向下、整数为像素中心。
2. 旧标注图记录为 2136×1197，1F 原图为 1330×746。目前标注图缺失，暂按无裁剪、无留白的像素中心缩放转移：`(p + 0.5) * target_size / source_size - 0.5`。此关系明确为 `verified=false`，不能宣称已复核控制点落点。
3. 六点依次经过 `BD-09LL → GCJ-02 → 近似 WGS84 → EPSG:32651`，在米制坐标中拟合 1F 像素仿射。输出再转换到 EPSG:4326。经纬度顺序固定为 lng、lat。
4. 2F→1F、3F→2F 使用一号楼四个可见外墙角点拟合仿射，另外使用楼梯核检查；保留相似变换对比、轴尺度比、每点残差及逐层矩阵。两处梯核不能既拟合又被当成独立验证。
5. 1F 拟合 RMSE **1.983 m**、最大拟合残差 **3.064 m**，留一法最大偏差 **11.387 m**。2F/3F 独立语义检查点 RMSE 分别约 **1.358 m / 0.171 m**。这些都是内部诊断，**不代表绝对定位精度**；`absolute_accuracy_m` 和数据库 `accuracy_m` 均为 null。

BD/GCJ 逆转换是本地工程近似公式，不是百度官方提供的 WGS84 逆转换；迭代收敛只说明数值一致，不能证明地理真实性。上层共享 1F 的控制点系统误差，不能把相关误差简单平方相加后报告为实测精度。全局地理定位置信度为 low。

## 数据契约与 PostGIS 导入

`facility → building → level` 保留院区和楼栋归属。每栋每层各有 level，跨楼栋连廊用 building_id=null 的共享 level；不强行归给一号楼。`level.geom` 为概化楼面范围，`space.geom` 为可见分隔的概化多边形，均不代表精确墙厚或产权外墙。

房间身份强制为 `building_id + level_id + room_ref`。空间、POI、节点及门均有独立稳定 ID，显示名称不是主键。`accessible` 是 `yes/no/unknown`；门禁是 `public/restricted/unknown`。现在普通通道的无障碍、全部门禁均 unknown，楼梯对轮椅为 no。高度未测量，`height_m=null`。

```powershell
psql -X -v ON_ERROR_STOP=1 -d your_database -f gis/import.sql
```

统一入口先运行非破坏迁移，再建 v2 schema，最后导入单个三层 seed 事务。旧 `indoor_gis` 如为 v1，会整体重命名为 `indoor_gis_legacy_v1`；旧数据不会被包装成新事实。若同名历史归档已存在，迁移明确报错。新 seed 按外键逆序清除**本院区**旧快照后按父→子顺序写入，避免源数据删除后数据库还留着旧路径；其他院区不受影响。旧 schema 的视图与权限依赖仍绑定归档对象，使用方需要切换到 v2。

导入表序：facility、building、level、source_document、space、path_node、opening、path_edge、poi、vertical_connection。开口与节点关联要求同层；同层边检查端点贴合、同院区同楼层序号；跨层边检查楼栋、相邻层、楼梯节点类型与水平偏差。约束延迟到事务末尾检查最终快照，节点单独移动也不能绕过检查。

## 接入 checkup-schedule

应用数据库仍遵循现有 Backend 的 SQLite/MySQL 契约；这里的 PostGIS 是独立空间数据源，不修改调度算法或强行更换业务数据库。

`workspace_gis_only.json` 可在已注册医院的工作区导入界面加载楼层图。它不含业务 `department` 特征，避免不经确认地覆盖科室映射。要把**现有真实医院工作区**中的已声明科室 key 与图纸证据连接，生成一份可审阅副本：

```powershell
python scripts/attach_indoor_gis.py path/to/your_workspace.json --output path/to/reviewed_workspace.json
```

适配器只绑定已声明且有证据的 `laboratory-1f` / `infusion-1f`，保留输入医院、科室、项目、套餐属性；门口坐标用于导航，室内 POI 原坐标保存在属性中。同层路线带 `distanceMeters`，可被现有 Backend 同步为科室间距离。当前 Backend 的患者导航只支持同层图，跨层路径保存在完整 GIS 图中，适配器不会把楼梯压成同层线。原示例医院名为“浙江大学校医院（紫金港校区）”，与本任务命名不同，不能据示例文件假定机构等价；原示例文件未被覆盖。

```python
from checkup_scheduler.gis import load_travel_time_matrix_csv

matrix = load_travel_time_matrix_csv(
    "gis/generated/scheduler_travel_times.csv",
    default_minutes=6,  # 调用方保底策略，不是 GIS 测量结果
    required_locations=["ENTRANCE", "laboratory-1f", "infusion-1f"],
)
```

目前矩阵有 3 个位置、6 个有向位置对。步速 1.15 m/s 为假设，秒数按路径各段长度计算，分钟按 `ceil(seconds/60)`。跨层图暂用每层 60 s、范围 40–90 s 的明确假设；并非楼梯实测时间。CSV/JSON 保留沿途节点、边、POI、映射证据、源包摘要，缺少的科室不生成伪行。`required_locations` 可让覆盖缺失立即报错；现有 TravelTimeMatrix 的数值 fallback 不能代表不可达或已测得时间。

此版本只提供 `ambulatory_draft`；未知无障碍属性不能生成轮椅路线。`validation.json` 列出未接入 POI、被排除的科室、待核验连接。恢复控制点标注图、提供科室位置表和核验门禁/电梯后，应修改 `source` 并重建，再进入人工现场验收。
