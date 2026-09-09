"""Build the Building 1 3F indoor-GIS draft through the 2F/1F registration chain."""

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
from build_2f_gis import fit_two_point_similarity, to_1f_pixel


SOURCE_PATH = ROOT / "gis" / "source" / "3f_features.json"
SOURCE_2F_PATH = ROOT / "gis" / "source" / "2f_features.json"
SOURCE_1F_PATH = ROOT / "gis" / "source" / "1f_features.json"


def to_2f_pixel(pixel: list[float], similarity: dict) -> list[float]:
    return to_1f_pixel(pixel, similarity)


def converted_point(
    pixel_3f: list[float],
    similarity_3f_2f: dict,
    similarity_2f_1f: dict,
    transform_1f: tuple,
    diagnostics: dict,
) -> dict:
    pixel_2f = to_2f_pixel(pixel_3f, similarity_3f_2f)
    pixel_1f = to_1f_pixel(pixel_2f, similarity_2f_1f)
    converted = pixel_to_coordinates(pixel_1f, transform_1f, diagnostics)
    converted["pixel_3f"] = [round(value, 3) for value in pixel_3f]
    converted["pixel_2f"] = [round(value, 3) for value in pixel_2f]
    converted["pixel_1f"] = [round(value, 3) for value in pixel_1f]
    return converted


def main() -> None:
    source = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    source_2f = json.loads(SOURCE_2F_PATH.read_text(encoding="utf-8"))
    source_1f = json.loads(SOURCE_1F_PATH.read_text(encoding="utf-8"))
    east_coefficients, north_coefficients, georef_1f = fit_affine(source_1f["control_points"])
    transform_1f = east_coefficients, north_coefficients
    similarity_2f_1f = fit_two_point_similarity(source_2f["registration"]["anchors"])
    anchors_3f = [
        {
            "pixel_2f": anchor["pixel_3f"],
            "pixel_1f_control_image": anchor["pixel_2f"],
        }
        for anchor in source["registration"]["anchors"]
    ]
    similarity_3f_2f = fit_two_point_similarity(anchors_3f)

    check_errors = {}
    for check in source["registration"]["checks"]:
        predicted_2f = to_2f_pixel(check["pixel_3f"], similarity_3f_2f)
        expected_2f = check["pixel_2f"]
        predicted_1f = to_1f_pixel(predicted_2f, similarity_2f_1f)
        expected_1f = to_1f_pixel(expected_2f, similarity_2f_1f)
        predicted_local = transform_pixel(predicted_1f, *transform_1f)
        expected_local = transform_pixel(expected_1f, *transform_1f)
        check_errors[check["id"]] = round(
            hypot(predicted_local[0] - expected_local[0], predicted_local[1] - expected_local[1]), 3
        )
    alignment_check_rmse = sqrt(sum(value * value for value in check_errors.values()) / len(check_errors))
    metadata_2f = json.loads((OUTPUT_DIR / "2f_metadata.json").read_text(encoding="utf-8"))
    base_accuracy = metadata_2f["georeferencing"]["estimated_absolute_accuracy_m"]
    absolute_accuracy = sqrt(base_accuracy * base_accuracy + alignment_check_rmse * alignment_check_rmse)
    diagnostics = {
        **georef_1f,
        "registration_method": source["source"]["registration_method"],
        "similarity_3f_to_2f": similarity_3f_2f,
        "similarity_2f_to_1f": similarity_2f_1f,
        "check_error_m": check_errors,
        "alignment_check_rmse_m": round(alignment_check_rmse, 3),
        "base_2f_absolute_accuracy_m": base_accuracy,
        "estimated_absolute_accuracy_m": round(absolute_accuracy, 3),
    }

    convert = lambda point: converted_point(
        point, similarity_3f_2f, similarity_2f_1f, transform_1f, diagnostics
    )
    level_ring = close_ring(source["level_footprint_px"])
    level_converted = [convert(point) for point in level_ring]
    level_geometry = {"type": "Polygon", "coordinates": [[item["wgs84"] for item in level_converted]]}
    level_feature = {
        "type": "Feature",
        "id": source["facility"]["level_id"],
        "properties": {
            **source["facility"],
            "status": source["source"]["status"],
            "accuracy_m": diagnostics["estimated_absolute_accuracy_m"],
            "geometry_role": "Building 1 coarse occupied-floor envelope",
        },
        "geometry": level_geometry,
    }

    space_features = []
    poi_features = []
    room_nodes = []
    room_edges = []
    building_id = source["facility"]["building_id"]
    level_id = source["facility"]["level_id"]
    for room in source["rooms"]:
        ring = close_ring(room["polygon_px"])
        converted_ring = [convert(point) for point in ring]
        properties = {
            "id": room["id"], "level_id": level_id, "building_id": building_id,
            "name": room["name"], "room_ref": room["room_ref"], "use_type": "room",
            "confidence": "high", "source_method": "manual_raster_trace",
        }
        space_features.append(
            {"type": "Feature", "id": room["id"], "properties": properties,
             "geometry": {"type": "Polygon", "coordinates": [[item["wgs84"] for item in converted_ring]]}}
        )
        node_id = "n_" + room["id"]
        door = convert(room["door_px"])
        room_nodes.append(
            {"id": node_id, "name": room["name"], "kind": "destination",
             "accessible": True, "building_id": building_id, **door}
        )
        poi_features.append(
            {"type": "Feature", "id": "poi_" + room["id"],
             "properties": {"id": "poi_" + room["id"], "level_id": level_id,
                            "building_id": building_id, "route_node_id": node_id,
                            "name": room["name"], "room_ref": room["room_ref"],
                            "category": "room", "confidence": "high", "accessible": True,
                            "pixel_3f": door["pixel_3f"]},
             "geometry": {"type": "Point", "coordinates": door["wgs84"]}}
        )
        room_edges.append(
            {"id": "e_" + room["id"], "source": room["corridor_node"],
             "target": node_id, "path_px": None, "accessible": True}
        )

    route_nodes = [
        {**node, "building_id": building_id, **convert(node["pixel"])}
        for node in source["corridor_nodes"]
    ]
    route_nodes.extend(room_nodes)
    node_by_id = {node["id"]: node for node in route_nodes}
    route_edges = []
    for edge in [*source["corridor_edges"], *room_edges]:
        path_px = edge.get("path_px") or [
            node_by_id[edge["source"]]["pixel_3f"],
            node_by_id[edge["target"]]["pixel_3f"],
        ]
        converted_path = [convert(point) for point in path_px]
        local_points = [tuple(item["local_m"]) for item in converted_path]
        length_m = line_length(local_points)
        route_edges.append(
            {**edge, "path_px": path_px, "accessible": edge.get("accessible", True),
             "length_m": round(length_m, 3), "walk_seconds": round(length_m / WALKING_SPEED_MPS, 1),
             "coordinates_wgs84": [item["wgs84"] for item in converted_path],
             "coordinates_local_m": [item["local_m"] for item in converted_path]}
        )

    node_features = [
        {"type": "Feature", "id": node["id"],
         "properties": {"id": node["id"], "level_id": level_id, "building_id": building_id,
                        "name": node["name"], "kind": node["kind"],
                        "accessible": node.get("accessible", True), "pixel_3f": node["pixel_3f"],
                        "pixel_2f": node["pixel_2f"], "local_m": node["local_m"]},
         "geometry": {"type": "Point", "coordinates": node["wgs84"]}}
        for node in route_nodes
    ]
    edge_features = [
        {"type": "Feature", "id": edge["id"],
         "properties": {"id": edge["id"], "level_id": level_id, "source": edge["source"],
                        "target": edge["target"], "accessible": edge["accessible"],
                        "length_m": edge["length_m"], "walk_seconds": edge["walk_seconds"],
                        "path_px": edge["path_px"], "coordinates_local_m": edge["coordinates_local_m"]},
         "geometry": {"type": "LineString", "coordinates": edge["coordinates_wgs84"]}}
        for edge in route_edges
    ]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for filename, content in {
        "3f_level.geojson": feature_collection([level_feature]),
        "3f_spaces.geojson": feature_collection(space_features),
        "3f_pois.geojson": feature_collection(poi_features),
        "3f_route_nodes.geojson": feature_collection(node_features),
        "3f_route_edges.geojson": feature_collection(edge_features),
    }.items():
        (OUTPUT_DIR / filename).write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "3f_metadata.json").write_text(
        json.dumps({"dataset_id": source["dataset_id"], "facility": source["facility"],
                    "source": source["source"], "registration": source["registration"],
                    "georeferencing": diagnostics}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (OUTPUT_DIR / "3f_vertical_connections.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["connector_id", "from_node_id", "to_node_id", "mode", "walk_seconds", "accessible", "confidence"])
        writer.writeheader()
        for connection in source["vertical_connections"]:
            writer.writerow({"connector_id": connection["id"], **{key: value for key, value in connection.items() if key != "id"}})

    sql_lines = [
        "-- Generated by scripts/build_3f_gis.py; import 1F and 2F first.", "BEGIN;",
        f"DELETE FROM indoor_gis.source_document WHERE dataset_id = {sql_text(source['dataset_id'])};",
        "INSERT INTO indoor_gis.source_document (dataset_id, facility_id, level_id, source_filename, source_sha256, coordinate_source, source_crs, status, georef_rmse_m, metadata) VALUES ("
        + ", ".join([sql_text(source["dataset_id"]), sql_text(source["facility"]["id"]), sql_text(level_id),
                     sql_text(source["source"]["floorplan_filename"]), sql_text(source["source"]["floorplan_sha256"]),
                     sql_text("registered to Building 1 2F stair cores"), sql_text("derived WGS84"),
                     sql_text(source["source"]["status"]), str(diagnostics["estimated_absolute_accuracy_m"]),
                     sql_text(json.dumps(diagnostics, ensure_ascii=False))]) + ");",
        "INSERT INTO indoor_gis.facility (facility_id, name) VALUES ("
        + f"{sql_text(source['facility']['id'])}, {sql_text(source['facility']['name'])}) ON CONFLICT (facility_id) DO UPDATE SET name=EXCLUDED.name;",
        "INSERT INTO indoor_gis.building (building_id, facility_id, name) VALUES ("
        + f"{sql_text(building_id)}, {sql_text(source['facility']['id'])}, {sql_text(source['facility']['building_name'])}) "
        + "ON CONFLICT (building_id) DO UPDATE SET name=EXCLUDED.name;",
        "INSERT INTO indoor_gis.level (level_id, facility_id, name, ordinal, height_m, status, accuracy_m, geom) VALUES ("
        + ", ".join([sql_text(level_id), sql_text(source["facility"]["id"]), sql_text(source["facility"]["level_name"]),
                     str(source["facility"]["ordinal"]), str(source["facility"]["height_m"]),
                     sql_text(source["source"]["status"]), str(diagnostics["estimated_absolute_accuracy_m"]),
                     geojson_geometry_sql(level_geometry)])
        + ") ON CONFLICT (level_id) DO UPDATE SET geom=EXCLUDED.geom, status=EXCLUDED.status, accuracy_m=EXCLUDED.accuracy_m;",
    ]
    for feature in space_features:
        props = feature["properties"]
        sql_lines.append(
            "INSERT INTO indoor_gis.space (space_id, level_id, building_id, name, use_type, room_ref, confidence, scheduler_location_id, geom) VALUES ("
            + ", ".join([sql_text(props["id"]), sql_text(level_id), sql_text(building_id), sql_text(props["name"]),
                         sql_text("room"), sql_text(props["room_ref"]), sql_text("high"), "NULL",
                         geojson_geometry_sql(feature["geometry"])])
            + ") ON CONFLICT (space_id) DO UPDATE SET name=EXCLUDED.name, room_ref=EXCLUDED.room_ref, building_id=EXCLUDED.building_id, geom=EXCLUDED.geom;"
        )
    for feature in node_features:
        props = feature["properties"]
        sql_lines.append(
            "INSERT INTO indoor_gis.path_node (node_id, level_id, building_id, name, kind, accessible, geom) VALUES ("
            + ", ".join([sql_text(props["id"]), sql_text(level_id), sql_text(building_id), sql_text(props["name"]),
                         sql_text(props["kind"]), sql_text(props["accessible"]), geojson_geometry_sql(feature["geometry"])])
            + ") ON CONFLICT (node_id) DO UPDATE SET name=EXCLUDED.name, kind=EXCLUDED.kind, building_id=EXCLUDED.building_id, geom=EXCLUDED.geom;"
        )
    for feature in edge_features:
        props = feature["properties"]
        sql_lines.append(
            "INSERT INTO indoor_gis.path_edge (edge_id, level_id, source_node_id, target_node_id, length_m, walk_seconds, accessible, geom) VALUES ("
            + ", ".join([sql_text(props["id"]), sql_text(level_id), sql_text(props["source"]), sql_text(props["target"]),
                         str(props["length_m"]), str(props["walk_seconds"]), sql_text(props["accessible"]),
                         geojson_geometry_sql(feature["geometry"])])
            + ") ON CONFLICT (edge_id) DO UPDATE SET length_m=EXCLUDED.length_m, walk_seconds=EXCLUDED.walk_seconds, geom=EXCLUDED.geom;"
        )
    for feature in poi_features:
        props = feature["properties"]
        sql_lines.append(
            "INSERT INTO indoor_gis.poi (poi_id, level_id, building_id, route_node_id, name, category, confidence, scheduler_location_id, accessible, geom) VALUES ("
            + ", ".join([sql_text(props["id"]), sql_text(level_id), sql_text(building_id), sql_text(props["route_node_id"]),
                         sql_text(props["name"]), sql_text("room"), sql_text("high"), "NULL", "TRUE",
                         geojson_geometry_sql(feature["geometry"])])
            + ") ON CONFLICT (poi_id) DO UPDATE SET name=EXCLUDED.name, route_node_id=EXCLUDED.route_node_id, building_id=EXCLUDED.building_id, geom=EXCLUDED.geom;"
        )
    for connection in source["vertical_connections"]:
        sql_lines.append(
            "INSERT INTO indoor_gis.vertical_connection (connector_id, from_node_id, to_node_id, mode, walk_seconds, accessible, confidence) VALUES ("
            + ", ".join([sql_text(connection["id"]), sql_text(connection["from_node_id"]), sql_text(connection["to_node_id"]),
                         sql_text(connection["mode"]), str(connection["walk_seconds"]), sql_text(connection["accessible"]),
                         sql_text(connection["confidence"])])
            + ") ON CONFLICT (connector_id) DO UPDATE SET walk_seconds=EXCLUDED.walk_seconds, accessible=EXCLUDED.accessible, confidence=EXCLUDED.confidence;"
        )
    sql_lines.extend(["COMMIT;", ""])
    (OUTPUT_DIR / "3f_seed.sql").write_text("\n".join(sql_lines), encoding="utf-8")
    print(
        f"built {len(space_features)} rooms, {len(poi_features)} POIs, {len(node_features)} nodes, "
        f"{len(edge_features)} edges; alignment check RMSE={diagnostics['alignment_check_rmse_m']} m, "
        f"estimated absolute accuracy={diagnostics['estimated_absolute_accuracy_m']} m"
    )


if __name__ == "__main__":
    main()
