"""Build the 2F indoor-GIS draft by registering it to the 1F control image."""

from __future__ import annotations

import csv
import json
from math import hypot, sqrt

from build_1f_gis import (
    OUTPUT_DIR,
    ROOT,
    WALKING_SPEED_MPS,
    close_ring,
    feature_collection,
    fit_affine,
    geojson_geometry_sql,
    line_length,
    pixel_to_coordinates,
    sql_text,
    transform_pixel,
)


SOURCE_PATH = ROOT / "gis" / "source" / "2f_features.json"
SOURCE_1F_PATH = ROOT / "gis" / "source" / "1f_features.json"


def building_id_for_2f(identifier: str) -> str:
    if identifier.startswith("2f_w_") or identifier.startswith("n2_wc_"):
        return "zju2_zijingang_b2"
    if identifier in {"n2_stair_wn", "n2_stair_ws"}:
        return "zju2_zijingang_b2"
    return "zju2_zijingang_b1"


def fit_two_point_similarity(anchors: list[dict]) -> dict:
    if len(anchors) != 2:
        raise ValueError("2F 当前要求正好两个楼梯核锚点")
    (x1, y1), (u1, v1) = anchors[0]["pixel_2f"], anchors[0]["pixel_1f_control_image"]
    (x2, y2), (u2, v2) = anchors[1]["pixel_2f"], anchors[1]["pixel_1f_control_image"]
    dx, dy = x2 - x1, y2 - y1
    du, dv = u2 - u1, v2 - v1
    denominator = dx * dx + dy * dy
    if denominator == 0:
        raise ValueError("2F 楼梯核锚点不能重合")
    a = (du * dx + dv * dy) / denominator
    b = (dv * dx - du * dy) / denominator
    c = u1 - a * x1 + b * y1
    d = v1 - b * x1 - a * y1
    return {"a": a, "b": b, "c": c, "d": d, "scale": hypot(a, b)}


def to_1f_pixel(pixel: list[float], similarity: dict) -> list[float]:
    x, y = pixel
    return [
        similarity["a"] * x - similarity["b"] * y + similarity["c"],
        similarity["b"] * x + similarity["a"] * y + similarity["d"],
    ]


def converted_point(pixel_2f: list[float], similarity: dict, transform: tuple, diagnostics: dict) -> dict:
    pixel_1f = to_1f_pixel(pixel_2f, similarity)
    converted = pixel_to_coordinates(pixel_1f, transform, diagnostics)
    converted["pixel_2f"] = [round(value, 3) for value in pixel_2f]
    converted["pixel_1f"] = [round(value, 3) for value in pixel_1f]
    return converted


def main() -> None:
    source = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    source_1f = json.loads(SOURCE_1F_PATH.read_text(encoding="utf-8"))
    east_coefficients, north_coefficients, georef_1f = fit_affine(source_1f["control_points"])
    transform = east_coefficients, north_coefficients
    similarity = fit_two_point_similarity(source["registration"]["anchors"])

    check_errors = {}
    for check in source["registration"]["checks"]:
        predicted = to_1f_pixel(check["pixel_2f"], similarity)
        expected = check["pixel_1f_control_image"]
        predicted_local = transform_pixel(predicted, *transform)
        expected_local = transform_pixel(expected, *transform)
        check_errors[check["id"]] = round(
            hypot(predicted_local[0] - expected_local[0], predicted_local[1] - expected_local[1]), 3
        )
    alignment_check_rmse = sqrt(sum(value * value for value in check_errors.values()) / len(check_errors))
    absolute_accuracy = sqrt(georef_1f["rmse_m"] ** 2 + alignment_check_rmse ** 2)
    diagnostics = {
        **georef_1f,
        "registration_method": source["source"]["registration_method"],
        "similarity_2f_to_1f": similarity,
        "check_error_m": check_errors,
        "alignment_check_rmse_m": round(alignment_check_rmse, 3),
        "estimated_absolute_accuracy_m": round(absolute_accuracy, 3),
        "accuracy_note": "western stair checks are low-confidence visual correspondences",
    }

    level_ring = close_ring(source["level_footprint_px"])
    level_converted = [converted_point(point, similarity, transform, diagnostics) for point in level_ring]
    level_geometry = {"type": "Polygon", "coordinates": [[item["wgs84"] for item in level_converted]]}
    level_feature = {
        "type": "Feature",
        "id": source["facility"]["level_id"],
        "properties": {
            **source["facility"],
            "status": source["source"]["status"],
            "accuracy_m": diagnostics["estimated_absolute_accuracy_m"],
            "geometry_role": "coarse_level_envelope_including_non_navigable_roof_void",
        },
        "geometry": level_geometry,
    }

    space_features = []
    poi_features = []
    room_nodes = []
    room_edges = []
    for room in source["rooms"]:
        ring = close_ring(room["polygon_px"])
        converted_ring = [converted_point(point, similarity, transform, diagnostics) for point in ring]
        properties = {
            "id": room["id"],
            "level_id": source["facility"]["level_id"],
            "building_id": building_id_for_2f(room["id"]),
            "name": room["name"],
            "room_ref": room["room_ref"],
            "use_type": "room",
            "confidence": "high",
            "source_method": "manual_raster_trace",
        }
        space_features.append(
            {
                "type": "Feature",
                "id": room["id"],
                "properties": properties,
                "geometry": {"type": "Polygon", "coordinates": [[item["wgs84"] for item in converted_ring]]},
            }
        )
        node_id = "n_" + room["id"]
        door = converted_point(room["door_px"], similarity, transform, diagnostics)
        room_nodes.append(
            {
                "id": node_id,
                "name": room["name"],
                "kind": "destination",
                "accessible": True,
                "building_id": building_id_for_2f(room["id"]),
                **door,
            }
        )
        poi_features.append(
            {
                "type": "Feature",
                "id": "poi_" + room["id"],
                "properties": {
                    "id": "poi_" + room["id"],
                    "level_id": source["facility"]["level_id"],
                    "building_id": building_id_for_2f(room["id"]),
                    "route_node_id": node_id,
                    "name": room["name"],
                    "room_ref": room["room_ref"],
                    "category": "room",
                    "confidence": "high",
                    "accessible": True,
                    "pixel_2f": door["pixel_2f"],
                },
                "geometry": {"type": "Point", "coordinates": door["wgs84"]},
            }
        )
        room_edges.append(
            {
                "id": "e_" + room["id"],
                "source": room["corridor_node"],
                "target": node_id,
                "path_px": None,
                "accessible": True,
            }
        )

    route_nodes = []
    for node in source["corridor_nodes"]:
        route_nodes.append(
            {
                **node,
                "building_id": building_id_for_2f(node["id"]),
                **converted_point(node["pixel"], similarity, transform, diagnostics),
            }
        )
    route_nodes.extend(room_nodes)
    node_by_id = {node["id"]: node for node in route_nodes}

    route_edges = []
    all_edge_sources = [*source["corridor_edges"], *room_edges]
    for edge in all_edge_sources:
        if edge.get("path_px"):
            path_px = edge["path_px"]
        else:
            path_px = [node_by_id[edge["source"]]["pixel_2f"], node_by_id[edge["target"]]["pixel_2f"]]
        converted_path = [converted_point(point, similarity, transform, diagnostics) for point in path_px]
        local_points = [tuple(item["local_m"]) for item in converted_path]
        length_m = line_length(local_points)
        route_edges.append(
            {
                **edge,
                "path_px": path_px,
                "accessible": edge.get("accessible", True),
                "length_m": round(length_m, 3),
                "walk_seconds": round(length_m / WALKING_SPEED_MPS, 1),
                "coordinates_wgs84": [item["wgs84"] for item in converted_path],
                "coordinates_local_m": [item["local_m"] for item in converted_path],
            }
        )

    node_features = []
    for node in route_nodes:
        node_features.append(
            {
                "type": "Feature",
                "id": node["id"],
                "properties": {
                    "id": node["id"],
                    "level_id": source["facility"]["level_id"],
                    "building_id": node.get(
                        "building_id", building_id_for_2f(node["id"])
                    ),
                    "name": node["name"],
                    "kind": node["kind"],
                    "accessible": node.get("accessible", True),
                    "pixel_2f": node["pixel_2f"],
                    "pixel_1f": node["pixel_1f"],
                    "local_m": node["local_m"],
                },
                "geometry": {"type": "Point", "coordinates": node["wgs84"]},
            }
        )
    edge_features = []
    for edge in route_edges:
        edge_features.append(
            {
                "type": "Feature",
                "id": edge["id"],
                "properties": {
                    "id": edge["id"],
                    "level_id": source["facility"]["level_id"],
                    "source": edge["source"],
                    "target": edge["target"],
                    "accessible": edge["accessible"],
                    "length_m": edge["length_m"],
                    "walk_seconds": edge["walk_seconds"],
                    "path_px": edge["path_px"],
                    "coordinates_local_m": edge["coordinates_local_m"],
                },
                "geometry": {"type": "LineString", "coordinates": edge["coordinates_wgs84"]},
            }
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    outputs = {
        "2f_level.geojson": feature_collection([level_feature]),
        "2f_spaces.geojson": feature_collection(space_features),
        "2f_pois.geojson": feature_collection(poi_features),
        "2f_route_nodes.geojson": feature_collection(node_features),
        "2f_route_edges.geojson": feature_collection(edge_features),
    }
    for filename, content in outputs.items():
        (OUTPUT_DIR / filename).write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "2f_metadata.json").write_text(
        json.dumps({"dataset_id": source["dataset_id"], "facility": source["facility"], "source": source["source"], "registration": source["registration"], "georeferencing": diagnostics}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (OUTPUT_DIR / "2f_vertical_connections.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["connector_id", "from_node_id", "to_node_id", "mode", "walk_seconds", "accessible", "confidence"])
        writer.writeheader()
        for connection in source["vertical_connections"]:
            writer.writerow({"connector_id": connection["id"], **{key: value for key, value in connection.items() if key != "id"}})

    sql_lines = [
        "-- Generated by scripts/build_2f_gis.py; import 1F before this file.",
        "BEGIN;",
        f"DELETE FROM indoor_gis.source_document WHERE dataset_id = {sql_text(source['dataset_id'])};",
        "INSERT INTO indoor_gis.source_document (dataset_id, facility_id, level_id, source_filename, source_sha256, coordinate_source, source_crs, status, georef_rmse_m, metadata) VALUES ("
        + ", ".join([sql_text(source["dataset_id"]), sql_text(source["facility"]["id"]), sql_text(source["facility"]["level_id"]), sql_text(source["source"]["floorplan_filename"]), sql_text(source["source"]["floorplan_sha256"]), sql_text("registered to 1F stair cores"), sql_text("derived WGS84"), sql_text(source["source"]["status"]), str(diagnostics["estimated_absolute_accuracy_m"]), sql_text(json.dumps(diagnostics, ensure_ascii=False))])
        + ");",
        "INSERT INTO indoor_gis.facility (facility_id, name) VALUES ("
        + f"{sql_text(source['facility']['id'])}, {sql_text(source['facility']['name'])}) ON CONFLICT (facility_id) DO UPDATE SET name=EXCLUDED.name;",
        "INSERT INTO indoor_gis.building (building_id, facility_id, name) VALUES "
        + f"('zju2_zijingang_b1', {sql_text(source['facility']['id'])}, '一号楼'), "
        + f"('zju2_zijingang_b2', {sql_text(source['facility']['id'])}, '二号楼') "
        + "ON CONFLICT (building_id) DO UPDATE SET name=EXCLUDED.name;",
        "INSERT INTO indoor_gis.level (level_id, facility_id, name, ordinal, height_m, status, accuracy_m, geom) VALUES ("
        + ", ".join([sql_text(source["facility"]["level_id"]), sql_text(source["facility"]["id"]), sql_text(source["facility"]["level_name"]), str(source["facility"]["ordinal"]), str(source["facility"]["height_m"]), sql_text(source["source"]["status"]), str(diagnostics["estimated_absolute_accuracy_m"]), geojson_geometry_sql(level_geometry)])
        + ") ON CONFLICT (level_id) DO UPDATE SET geom=EXCLUDED.geom, status=EXCLUDED.status, accuracy_m=EXCLUDED.accuracy_m;",
    ]
    for feature in space_features:
        props = feature["properties"]
        sql_lines.append(
            "INSERT INTO indoor_gis.space (space_id, level_id, building_id, name, use_type, room_ref, confidence, scheduler_location_id, geom) VALUES ("
            + ", ".join([sql_text(props["id"]), sql_text(props["level_id"]), sql_text(props["building_id"]), sql_text(props["name"]), sql_text(props["use_type"]), sql_text(props["room_ref"]), sql_text(props["confidence"]), "NULL", geojson_geometry_sql(feature["geometry"])])
            + ") ON CONFLICT (space_id) DO UPDATE SET name=EXCLUDED.name, room_ref=EXCLUDED.room_ref, building_id=EXCLUDED.building_id, geom=EXCLUDED.geom;"
        )
    for feature in node_features:
        props = feature["properties"]
        sql_lines.append(
            "INSERT INTO indoor_gis.path_node (node_id, level_id, building_id, name, kind, accessible, geom) VALUES ("
            + ", ".join([sql_text(props["id"]), sql_text(props["level_id"]), sql_text(props["building_id"]), sql_text(props["name"]), sql_text(props["kind"]), sql_text(props["accessible"]), geojson_geometry_sql(feature["geometry"])])
            + ") ON CONFLICT (node_id) DO UPDATE SET name=EXCLUDED.name, kind=EXCLUDED.kind, building_id=EXCLUDED.building_id, accessible=EXCLUDED.accessible, geom=EXCLUDED.geom;"
        )
    for feature in edge_features:
        props = feature["properties"]
        sql_lines.append(
            "INSERT INTO indoor_gis.path_edge (edge_id, level_id, source_node_id, target_node_id, length_m, walk_seconds, accessible, geom) VALUES ("
            + ", ".join([sql_text(props["id"]), sql_text(props["level_id"]), sql_text(props["source"]), sql_text(props["target"]), str(props["length_m"]), str(props["walk_seconds"]), sql_text(props["accessible"]), geojson_geometry_sql(feature["geometry"])])
            + ") ON CONFLICT (edge_id) DO UPDATE SET length_m=EXCLUDED.length_m, walk_seconds=EXCLUDED.walk_seconds, accessible=EXCLUDED.accessible, geom=EXCLUDED.geom;"
        )
    for feature in poi_features:
        props = feature["properties"]
        sql_lines.append(
            "INSERT INTO indoor_gis.poi (poi_id, level_id, building_id, route_node_id, name, category, confidence, scheduler_location_id, accessible, geom) VALUES ("
            + ", ".join([sql_text(props["id"]), sql_text(props["level_id"]), sql_text(props["building_id"]), sql_text(props["route_node_id"]), sql_text(props["name"]), sql_text(props["category"]), sql_text(props["confidence"]), "NULL", sql_text(props["accessible"]), geojson_geometry_sql(feature["geometry"])])
            + ") ON CONFLICT (poi_id) DO UPDATE SET name=EXCLUDED.name, route_node_id=EXCLUDED.route_node_id, building_id=EXCLUDED.building_id, geom=EXCLUDED.geom;"
        )
    for connection in source["vertical_connections"]:
        sql_lines.append(
            "INSERT INTO indoor_gis.vertical_connection (connector_id, from_node_id, to_node_id, mode, walk_seconds, accessible, confidence) VALUES ("
            + ", ".join([sql_text(connection["id"]), sql_text(connection["from_node_id"]), sql_text(connection["to_node_id"]), sql_text(connection["mode"]), str(connection["walk_seconds"]), sql_text(connection["accessible"]), sql_text(connection["confidence"])])
            + ") ON CONFLICT (connector_id) DO UPDATE SET walk_seconds=EXCLUDED.walk_seconds, accessible=EXCLUDED.accessible, confidence=EXCLUDED.confidence;"
        )
    sql_lines.extend(["COMMIT;", ""])
    (OUTPUT_DIR / "2f_seed.sql").write_text("\n".join(sql_lines), encoding="utf-8")
    print(
        f"built {len(space_features)} rooms, {len(poi_features)} POIs, "
        f"{len(node_features)} nodes, {len(edge_features)} edges, "
        f"alignment check RMSE={diagnostics['alignment_check_rmse_m']} m, "
        f"estimated absolute accuracy={diagnostics['estimated_absolute_accuracy_m']} m"
    )


if __name__ == "__main__":
    main()
