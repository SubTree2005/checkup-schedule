from __future__ import annotations

import argparse
import json
from pathlib import Path


def read_feature_collection(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["features"]


def department(
    key: str,
    name: str,
    location: str,
    floor: str,
    anchor: str,
    *,
    start: str = "08:00",
    end: str = "17:00",
    capacity: int = 1,
) -> dict:
    return {
        "key": key,
        "deptName": name,
        "location": location,
        "openTimeStart": start,
        "openTimeEnd": end,
        "capacity": capacity,
        "isAvailable": True,
        "_floor": floor,
        "_anchor": anchor,
    }


def exam(
    key: str,
    department_key: str,
    name: str,
    duration: int,
    *,
    prerequisites: dict | None = None,
    prerequisite_keys: list[str] | None = None,
    priority: int = 3,
    slots: dict | None = None,
    critical: bool = False,
) -> dict:
    return {
        "key": key,
        "departmentKey": department_key,
        "itemName": name,
        "duration": duration,
        "prerequisites": prerequisites or {},
        "prerequisiteItemKeys": prerequisite_keys or [],
        "conflictItemKeys": [],
        "priority": priority,
        "allowedTimeSlots": slots or {},
        "isCritical": critical,
        "isActive": True,
    }


def build_departments() -> list[dict]:
    rows = [
        department("emergency-rescue-1f", "B区急诊抢救室", "1F B区（具体门牌待核）", "1F", "n_emergency_a", start="00:00", end="23:59", capacity=2),
        department("infusion-1f", "输液室", "1F B区（体温、雾化、输液、注射）", "1F", "n_infusion", start="00:00", end="23:59", capacity=8),
        department("pharmacy-1f", "药房", "1F（具体门牌待核）", "1F", "n_pharmacy", capacity=3),
        department("laboratory-1f", "检验科", "1F（血、尿、便检查）", "1F", "n_lab", start="07:30", capacity=4),
        department("radiology-1f", "放射科（MRI、CT、DR）", "1F；检查前先到放射登记窗口登记", "1F", "n_radiology", capacity=2),
        department("health-clinic-101", "保健医疗门诊（101）", "1F 101", "1F", "n_rooms"),
        department("oral-surgery-105", "手术室/口腔种植（105）", "1F 105", "1F", "n_clinics"),
        department("endoscopy-1f", "消化内镜检查", "1F（具体房间待核）", "1F", "space:1f_endoscopy"),
    ]
    second_floor = [
        ("internal-202", "内科1（202）", "202", "n_2f_e_202"),
        ("pediatrics-203", "儿科门诊（203）", "203", "n_2f_c_203"),
        ("internal-204", "内科2（204）", "204", "n_2f_e_204"),
        ("gynecology-205", "妇科1（205）", "205", "n_2f_c_205"),
        ("internal-206", "内科3（206）", "206", "n_2f_e_206"),
        ("expert-207", "专家门诊（207）", "207", "n_2f_c_207"),
        ("internal-208", "内科4（208）", "208", "n_2f_e_208"),
        ("expert-209", "专家门诊（209）", "209", "n_2f_c_209"),
        ("internal-210", "内科5（210）", "210", "n_2f_e_210"),
        ("dressing-211", "换药室（211）", "211", "n_2f_c_211"),
        ("ent-212", "耳鼻喉科（212）", "212", "n_2f_e_212"),
        ("ophthalmology-214", "眼科（214）", "214", "n_2f_e_214"),
        ("dermatology-216", "皮肤科（216）", "216", "n_2f_e_216"),
        ("dermatology-tcm-218", "皮肤科/中医科（218）", "218", "n_2f_e_218"),
        ("ophthalmology-220", "眼科（220）", "220", "n_2f_e_220"),
        ("surgery-224", "外科4（224）", "224", "n_2f_e_224"),
        ("surgery-226", "外科3（226）", "226", "n_2f_e_226"),
        ("surgery-228", "外科2（228）", "228", "n_2f_e_228"),
        ("surgery-230", "外科1（230）", "230", "n_2f_e_230"),
    ]
    rows.extend(department(key, name, f"2F {room}", "2F", anchor) for key, name, room, anchor in second_floor)
    third_floor = [
        ("body-bone-301", "骨密度/人体成分分析室（301）", "301", "n_3f_b1_301"),
        ("checkup-consult-302", "体检咨询室（302）", "302（对面为厕所）", "n_3f_b1_302"),
        ("ultrasound-303", "B超室1（303）", "303", "n_3f_b1_303"),
        ("ultrasound-305", "B超室2（305）", "305", "n_3f_b1_305"),
        ("breath-test-306", "呼气试验室（306）", "306（旁边为抽血处）", "n_3f_b1_306"),
        ("ultrasound-307", "B超室3（307）", "307", "n_3f_b1_307"),
        ("pulmonary-308", "肺功能室（308）", "308", "n_3f_b1_308"),
        ("ultrasound-309", "B超室4（309）", "309", "n_3f_b1_309"),
        ("ecg-310", "心电图室（310）", "310", "n_3f_b1_310"),
        ("laser-311", "激光治疗室2（311）", "311", "n_3f_b1_311"),
        ("gynecology-312", "妇科（312）", "312", "n_3f_b1_312"),
        ("ecg-314", "心电图室1（314）", "314", "n_3f_b1_314"),
        ("general-check-316", "一般检查（316）", "316", "n_3f_b1_316", 3),
        ("internal-check-318", "内科（318）", "318", "n_3f_b1_318"),
        ("surgery-check-320", "外科（320）", "320", "n_3f_b1_320"),
        ("ent-check-322", "耳鼻喉科（322）", "322", "n_3f_b1_322"),
        ("ophthalmology-check-324", "眼科（324）", "324", "n_3f_b1_324"),
        ("laser-326", "激光治疗室3（326）", "326", "n_3f_b1_326"),
        ("laser-328", "激光治疗室4（328）", "328", "n_3f_b1_328"),
        ("laser-330", "激光治疗室5（330）", "330", "n_3f_b1_330"),
        ("laser-clinic-336", "激光诊室（336）", "336", "n_3f_b1_336"),
        ("laser-338", "激光治疗室1（338）", "338", "n_3f_b1_338"),
    ]
    for row in third_floor:
        key, name, room, anchor, *capacity = row
        rows.append(department(key, name, f"3F {room}", "3F", anchor, capacity=capacity[0] if capacity else 1))
    return rows


def build_exams() -> list[dict]:
    morning = {"start": "07:30", "end": "11:30"}
    fasting_morning = {"start": "07:30", "end": "10:30"}
    rows = [
        exam("emergency-care", "emergency-rescue-1f", "急诊抢救处置", 30, priority=10, critical=True),
        exam("temperature-check", "infusion-1f", "体温测量", 3),
        exam("nebulization", "infusion-1f", "雾化治疗", 20),
        exam("infusion-treatment", "infusion-1f", "输液治疗", 45),
        exam("injection-treatment", "infusion-1f", "注射治疗", 10),
        exam("medication-dispensing", "pharmacy-1f", "取药与用药咨询", 5),
        exam("health-consult", "health-clinic-101", "保健医疗门诊咨询", 15),
        exam("oral-implant", "oral-surgery-105", "口腔种植/手术评估", 30),
        exam("gastroscopy", "endoscopy-1f", "胃镜", 25, prerequisites={"fastingHours": 8}, priority=7, critical=True),
        exam("colonoscopy", "endoscopy-1f", "肠镜", 35, prerequisites={"fastingHours": 8}, priority=7, critical=True),
        exam("urine-routine", "laboratory-1f", "尿常规（尿液自动化分析）", 8, priority=6, slots=morning),
        exam("stool-routine", "laboratory-1f", "粪便常规及隐血试验", 10, priority=6, slots=morning),
        exam("lab-package-1", "laboratory-1f", "套餐一生化：肝肾功能基础", 12, prerequisites={"fastingHours": 8}, priority=9, slots=fasting_morning, critical=True),
        exam("lab-package-2", "laboratory-1f", "套餐二血液检查：血常规+肝功能半套", 15, prerequisites={"fastingHours": 8}, priority=9, slots=fasting_morning, critical=True),
        exam("lab-package-3", "laboratory-1f", "套餐三血液检查：血常规+糖脂+肝肾功能", 18, prerequisites={"fastingHours": 8}, priority=9, slots=fasting_morning, critical=True),
        exam("lab-package-4", "laboratory-1f", "套餐四血液检查：糖脂肝肾功能+AFP/CEA", 20, prerequisites={"fastingHours": 8}, priority=9, slots=fasting_morning, critical=True),
        exam("lab-package-5", "laboratory-1f", "套餐五血液检查：糖脂肝肾甲功7项+肿瘤3项", 22, prerequisites={"fastingHours": 8}, priority=9, slots=fasting_morning, critical=True),
        exam("lab-package-6", "laboratory-1f", "套餐六血液检查：综合生化甲功5项+肿瘤标志物", 24, prerequisites={"fastingHours": 8}, priority=9, slots=fasting_morning, critical=True),
        exam("lab-package-7", "laboratory-1f", "套餐七血液检查：全面生化甲功7项+肿瘤标志物", 28, prerequisites={"fastingHours": 8}, priority=9, slots=fasting_morning, critical=True),
        exam("thyroid-seven-option", "laboratory-1f", "甲状腺功能七项（增选）", 12, prerequisites={"fastingHours": 8}, slots=fasting_morning),
        exam("sex-hormone-option", "laboratory-1f", "性激素全套（增选）", 12, slots=morning),
        exam("radiology-registration", "radiology-1f", "放射检查登记", 5, priority=8, critical=True),
        exam("chest-dr-frontal", "radiology-1f", "胸片（DR正位）", 10, prerequisite_keys=["radiology-registration"], priority=7),
        exam("chest-dr-two-view", "radiology-1f", "胸片（DR正侧位）", 12, prerequisite_keys=["radiology-registration"], priority=7),
        exam("low-dose-chest-ct", "radiology-1f", "胸部低剂量CT平扫", 18, prerequisite_keys=["radiology-registration"], priority=8, critical=True),
        exam("mri-exam", "radiology-1f", "MRI检查", 30, prerequisite_keys=["radiology-registration"]),
        exam("ct-exam", "radiology-1f", "CT检查", 20, prerequisite_keys=["radiology-registration"]),
        exam("mammography-option", "radiology-1f", "乳腺钼靶（增选）", 15, prerequisite_keys=["radiology-registration"]),
    ]
    outpatient_projects = [
        ("internal-visit-202", "internal-202", "内科门诊诊疗（202）"),
        ("pediatrics-visit-203", "pediatrics-203", "儿科门诊诊疗（203）"),
        ("internal-visit-204", "internal-204", "内科门诊诊疗（204）"),
        ("gynecology-visit-205", "gynecology-205", "妇科门诊诊疗（205）"),
        ("internal-visit-206", "internal-206", "内科门诊诊疗（206）"),
        ("expert-visit-207", "expert-207", "专家门诊诊疗（207）"),
        ("internal-visit-208", "internal-208", "内科门诊诊疗（208）"),
        ("expert-visit-209", "expert-209", "专家门诊诊疗（209）"),
        ("internal-visit-210", "internal-210", "内科门诊诊疗（210）"),
        ("dressing-change-211", "dressing-211", "换药处置（211）"),
        ("ent-visit-212", "ent-212", "耳鼻喉科门诊诊疗（212）"),
        ("eye-visit-214", "ophthalmology-214", "眼科门诊诊疗（214）"),
        ("dermatology-visit-216", "dermatology-216", "皮肤科门诊诊疗（216）"),
        ("dermatology-tcm-visit-218", "dermatology-tcm-218", "皮肤科/中医科门诊诊疗（218）"),
        ("eye-visit-220", "ophthalmology-220", "眼科门诊诊疗（220）"),
        ("surgery-visit-224", "surgery-224", "外科门诊诊疗（224）"),
        ("surgery-visit-226", "surgery-226", "外科门诊诊疗（226）"),
        ("surgery-visit-228", "surgery-228", "外科门诊诊疗（228）"),
        ("surgery-visit-230", "surgery-230", "外科门诊诊疗（230）"),
    ]
    rows.extend(exam(key, dept, name, 15) for key, dept, name in outpatient_projects)
    rows.extend(
        [
            exam("bone-density", "body-bone-301", "骨密度筛查", 15),
            exam("body-composition", "body-bone-301", "人体成分分析", 10),
            exam("checkup-consultation", "checkup-consult-302", "体检咨询", 10),
            exam("ultrasound-abdomen-basic", "ultrasound-303", "腹部彩超：肝、胆、脾", 15, prerequisites={"fastingHours": 8}, priority=8, slots=morning, critical=True),
            exam("ultrasound-abdomen-full", "ultrasound-305", "腹部彩超：肝、胆、脾、胰、双肾", 18, prerequisites={"fastingHours": 8}, priority=8, slots=morning, critical=True),
            exam("ultrasound-abdomen-thyroid", "ultrasound-307", "腹部及甲状腺彩超", 22, prerequisites={"fastingHours": 8}, priority=8, slots=morning, critical=True),
            exam("ultrasound-package-6", "ultrasound-309", "套餐六彩超：腹部、甲状腺及性别相关项目", 28, prerequisites={"fastingHours": 8, "bladderReady": True}, priority=8, slots=morning, critical=True),
            exam("ultrasound-package-7", "ultrasound-309", "套餐七彩超：颈部血管、甲状腺、腹部及性别相关项目", 35, prerequisites={"fastingHours": 8, "bladderReady": True}, priority=8, slots=morning, critical=True),
            exam("echocardiography-option", "ultrasound-305", "心脏彩超（增选）", 20),
            exam("breath-c14", "breath-test-306", "C14呼气试验", 20, prerequisites={"fastingHours": 4}),
            exam("breath-c13", "breath-test-306", "C13呼气试验", 20, prerequisites={"fastingHours": 4}),
            exam("pulmonary-function", "pulmonary-308", "肺功能检查", 20),
            exam("ecg-routine", "ecg-310", "十二导联常规心电图", 10, priority=6),
            exam("dynamic-ecg-option", "ecg-314", "动态心电图（增选）", 20),
            exam("dynamic-bp-option", "ecg-314", "动态血压（增选）", 15),
            exam("gynecology-basic", "gynecology-312", "妇科常规检查", 15),
            exam("tct-option", "gynecology-312", "TCT（增选）", 15),
            exam("hpv-option", "gynecology-312", "HPV（增选）", 15),
            exam("general-measurements", "general-check-316", "一般项目：身高、体重、血压", 8, priority=7, slots=morning),
            exam("internal-basic", "internal-check-318", "内科常规：心、肺、肝、脾", 10, priority=6),
            exam("surgery-basic", "surgery-check-320", "外科常规：皮肤、淋巴结、甲状腺、胸廓、脊柱、四肢", 12, priority=6),
            exam("surgery-expanded", "surgery-check-320", "外科全面：常规项目、肛门指诊、乳房", 16, priority=6),
            exam("ent-basic", "ent-check-322", "五官科常规检查", 10),
            exam("ent-expanded", "ent-check-322", "五官科全面：鼻、咽喉及耳内镜检查", 15),
            exam("eye-basic", "ophthalmology-check-324", "眼科常规检查", 10),
            exam("eye-fundus", "ophthalmology-check-324", "眼科常规及眼底镜检查", 15),
            exam("laser-treatment-311", "laser-311", "激光治疗（311）", 20),
            exam("laser-treatment-326", "laser-326", "激光治疗（326）", 20),
            exam("laser-treatment-328", "laser-328", "激光治疗（328）", 20),
            exam("laser-treatment-330", "laser-330", "激光治疗（330）", 20),
            exam("laser-consult-336", "laser-clinic-336", "激光门诊评估（336）", 15),
            exam("laser-treatment-338", "laser-338", "激光治疗（338）", 20),
        ]
    )
    return rows


def build_packages() -> list[dict]:
    basic = ["general-measurements", "internal-basic", "surgery-basic", "eye-basic", "ent-basic"]
    official_notice = "项目与标价依据浙江大学校医院官网公开套餐整理，实际可约项目、价格和执行顺序以医院当日确认为准。"

    def package(key: str, name: str, package_type: str, price: int, items: list[str], description: str, suitable: list[str], extra_notice: list[str] | None = None) -> dict:
        return {
            "key": key,
            "packageName": name,
            "packageType": package_type,
            "price": price,
            "tag": "官网套餐",
            "description": description,
            "includedItemKeys": items,
            "defaultDuration": 0,
            "suitable": suitable,
            "notice": [official_notice, "含抽血、生化或腹部B超时请按预约要求空腹。", "放射检查需先到1楼放射登记窗口登记。"] + (extra_notice or []),
            "isPublished": True,
        }

    return [
        package("official-package-1", "校医院体检套餐一", "入学体检/用工体检", 80, basic + ["lab-package-1", "radiology-registration", "chest-dr-frontal"], "一般、内外科、眼科、五官科、肝肾功能基础及胸片正位。", ["入学体检人群", "用工体检人群"]),
        package("official-package-2", "校医院体检套餐二", "用工体检", 120, basic + ["lab-package-2", "urine-routine", "radiology-registration", "chest-dr-frontal"], "在基础临床检查上增加血常规、尿常规和肝功能半套。", ["用工体检人群"]),
        package("official-package-3", "校医院体检套餐三", "用工体检/健康体检", 280, basic + ["lab-package-3", "urine-routine", "ultrasound-abdomen-basic", "ecg-routine", "radiology-registration", "chest-dr-two-view"], "增加糖脂、肝肾功能、腹部彩超、心电图及DR正侧位。", ["用工体检人群", "基础健康体检人群"]),
        package("official-package-4", "校医院体检套餐四", "用工体检/健康体检", 398, basic + ["lab-package-4", "urine-routine", "ultrasound-abdomen-full", "ecg-routine", "radiology-registration", "chest-dr-two-view"], "增加AFP、CEA肿瘤标志物及含胰、双肾的腹部彩超。", ["用工体检人群", "常规健康筛查人群"]),
        package("official-package-5", "校医院体检套餐五", "健康体检", 580, basic + ["lab-package-5", "urine-routine", "ultrasound-abdomen-thyroid", "ecg-routine", "radiology-registration", "chest-dr-two-view"], "包含甲状腺功能7项、三项肿瘤标志物及腹部和甲状腺彩超。", ["较全面健康体检人群"]),
        package("official-package-6", "校医院体检套餐六", "健康体检", 800, ["general-measurements", "internal-basic", "surgery-expanded", "eye-fundus", "ent-expanded", "lab-package-6", "urine-routine", "breath-c14", "radiology-registration", "chest-dr-two-view", "ecg-routine", "ultrasound-package-6"], "全面临床检查、综合生化、肿瘤标志物、C14呼气试验和多部位彩超。", ["全面健康体检人群"], ["性别相关彩超项目应由医院按参检者情况确认。"]),
        package("official-package-7", "校医院体检套餐七", "健康体检", 1350, ["general-measurements", "internal-basic", "surgery-expanded", "eye-fundus", "ent-expanded", "gynecology-basic", "lab-package-7", "urine-routine", "stool-routine", "breath-c13", "radiology-registration", "low-dose-chest-ct", "ecg-routine", "ultrasound-package-7"], "官网最高档套餐，包含全面生化、肿瘤标志物、13C呼气试验、低剂量CT和多部位彩超。", ["高标准全面健康体检人群"], ["妇科及性别相关项目仅适用于相应人群，预约后应由医院确认和调整。"]),
    ]


def geometry_center(geometry: dict) -> list[float]:
    points: list[list[float]] = []

    def collect(value):
        if isinstance(value, list) and len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
            points.append(value[:2])
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(geometry["coordinates"])
    return [sum(point[0] for point in points) / len(points), sum(point[1] for point in points) / len(points)]


def build_gis(gis_root: Path, departments: list[dict]) -> list[dict]:
    result = []
    for floor_slug, floor_key in (("1f", "1F"), ("2f", "2F"), ("3f", "3F")):
        level = read_feature_collection(gis_root / f"{floor_slug}_level.geojson")
        spaces = read_feature_collection(gis_root / f"{floor_slug}_spaces.geojson")
        edges = read_feature_collection(gis_root / f"{floor_slug}_route_edges.geojson")
        nodes = read_feature_collection(gis_root / f"{floor_slug}_route_nodes.geojson")
        node_geometry = {feature["properties"]["id"]: feature["geometry"] for feature in nodes}
        space_geometry = {feature["properties"]["id"]: feature["geometry"] for feature in spaces}
        features = [
            {
                "type": "Feature",
                "properties": {"featureType": "buildingOutline", "floorKey": floor_key},
                "geometry": feature["geometry"],
            }
            for feature in level
        ]
        features.extend(
            {
                "type": "Feature",
                "properties": {
                    "featureType": "room",
                    "roomRef": feature["properties"].get("room_ref") or None,
                },
                "geometry": feature["geometry"],
            }
            for feature in spaces
        )
        features.extend(
            {
                "type": "Feature",
                "properties": {
                    "featureType": "corridor",
                    "distanceMeters": round(float(feature["properties"].get("length_m", 0)), 2),
                },
                "geometry": feature["geometry"],
            }
            for feature in edges
        )
        for row in departments:
            if row["_floor"] != floor_key:
                continue
            anchor = row["_anchor"]
            if anchor.startswith("space:"):
                coordinates = geometry_center(space_geometry[anchor.removeprefix("space:")])
            else:
                coordinates = node_geometry[anchor]["coordinates"]
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "featureType": "department",
                        "departmentKey": row["key"],
                        "name": row["deptName"],
                    },
                    "geometry": {"type": "Point", "coordinates": coordinates},
                }
            )
        result.append({"floorKey": floor_key, "geojson": {"type": "FeatureCollection", "features": features}})
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Zijingang campus hospital workspace example")
    parser.add_argument("--gis-root", type=Path, required=True, help="Directory containing generated 1f/2f/3f GeoJSON files")
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("workspace.json"))
    args = parser.parse_args()

    departments = build_departments()
    workspace = {
        "formatVersion": "1.0",
        "mode": "upsert",
        "hospital": {
            "hospitalName": "浙江大学校医院（紫金港校区）",
            "address": "杭州市余杭塘路866号",
            "openTime": "工作日08:00-12:00,13:30-17:00；急诊24小时",
            "floorMapUrl": None,
        },
        "departments": [{key: value for key, value in row.items() if not key.startswith("_")} for row in departments],
        "exams": build_exams(),
        "packages": build_packages(),
        "gis": build_gis(args.gis_root, departments),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(workspace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
