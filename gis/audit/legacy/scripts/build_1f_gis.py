"""Build the auditable 1F indoor-GIS draft from pixel-space source data.

The script intentionally uses only the Python standard library.  It writes
PostGIS-ready WGS84 GeoJSON, raw BD-09 control points, a route graph, and the
travel-time matrix consumed by ``TravelTimeMatrix``.
"""

from __future__ import annotations

import csv
import heapq
import json
from math import atan2, ceil, cos, hypot, pi, sin, sqrt
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "gis" / "source" / "1f_features.json"
OUTPUT_DIR = ROOT / "gis" / "generated"
METERS_PER_DEGREE_LATITUDE = 111_320.0
WALKING_SPEED_MPS = 1.15


def solve_3x3(matrix: list[list[float]], values: list[float]) -> list[float]:
    augmented = [row[:] + [value] for row, value in zip(matrix, values)]
    for column in range(3):
        pivot = max(range(column, 3), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise ValueError("控制点不能确定唯一仿射变换")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [item / divisor for item in augmented[column]]
        for row in range(3):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                item - factor * pivot_item
                for item, pivot_item in zip(augmented[row], augmented[column])
            ]
    return [augmented[index][3] for index in range(3)]


def least_squares_3(
    rows: list[tuple[float, float, float]], values: list[float]
) -> list[float]:
    normal = [
        [sum(row[i] * row[j] for row in rows) for j in range(3)]
        for i in range(3)
    ]
    rhs = [sum(row[i] * value for row, value in zip(rows, values)) for i in range(3)]
    return solve_3x3(normal, rhs)


def fit_affine(control_points: list[dict]) -> tuple[list[float], list[float], dict]:
    origin_lon, origin_lat = control_points[0]["bd09"]
    mean_lat = sum(item["bd09"][1] for item in control_points) / len(control_points)
    meters_per_degree_lon = METERS_PER_DEGREE_LATITUDE * cos(mean_lat * pi / 180.0)
    rows = [(item["pixel"][0], item["pixel"][1], 1.0) for item in control_points]
    east = [
        (item["bd09"][0] - origin_lon) * meters_per_degree_lon
        for item in control_points
    ]
    north = [
        (item["bd09"][1] - origin_lat) * METERS_PER_DEGREE_LATITUDE
        for item in control_points
    ]
    east_coefficients = least_squares_3(rows, east)
    north_coefficients = least_squares_3(rows, north)
    residuals = []
    for item, expected_east, expected_north in zip(control_points, east, north):
        x, y = item["pixel"]
        predicted_east = sum(
            coefficient * value
            for coefficient, value in zip(east_coefficients, (x, y, 1.0))
        )
        predicted_north = sum(
            coefficient * value
            for coefficient, value in zip(north_coefficients, (x, y, 1.0))
        )
        residuals.append(hypot(predicted_east - expected_east, predicted_north - expected_north))
    diagnostics = {
        "origin_bd09": [origin_lon, origin_lat],
        "mean_latitude": mean_lat,
        "meters_per_degree_longitude": meters_per_degree_lon,
        "residual_m_by_control_point": {
            str(item["id"]): round(residual, 3)
            for item, residual in zip(control_points, residuals)
        },
        "rmse_m": round(sqrt(sum(value * value for value in residuals) / len(residuals)), 3),
        "max_residual_m": round(max(residuals), 3),
    }
    return east_coefficients, north_coefficients, diagnostics


def transform_pixel(
    pixel: list[float] | tuple[float, float],
    east_coefficients: list[float],
    north_coefficients: list[float],
) -> tuple[float, float]:
    x, y = pixel
    values = (x, y, 1.0)
    return (
        sum(coefficient * value for coefficient, value in zip(east_coefficients, values)),
        sum(coefficient * value for coefficient, value in zip(north_coefficients, values)),
    )


def local_to_bd09(local: tuple[float, float], diagnostics: dict) -> tuple[float, float]:
    origin_lon, origin_lat = diagnostics["origin_bd09"]
    east, north = local
    return (
        origin_lon + east / diagnostics["meters_per_degree_longitude"],
        origin_lat + north / METERS_PER_DEGREE_LATITUDE,
    )


def bd09_to_gcj02(lon: float, lat: float) -> tuple[float, float]:
    x_pi = pi * 3000.0 / 180.0
    x = lon - 0.0065
    y = lat - 0.006
    z = sqrt(x * x + y * y) - 0.00002 * sin(y * x_pi)
    theta = atan2(y, x) - 0.000003 * cos(x * x_pi)
    return z * cos(theta), z * sin(theta)


def _transform_latitude(x: float, y: float) -> float:
    result = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y
    result += 0.2 * sqrt(abs(x))
    result += (20.0 * sin(6.0 * x * pi) + 20.0 * sin(2.0 * x * pi)) * 2.0 / 3.0
    result += (20.0 * sin(y * pi) + 40.0 * sin(y / 3.0 * pi)) * 2.0 / 3.0
    result += (160.0 * sin(y / 12.0 * pi) + 320.0 * sin(y * pi / 30.0)) * 2.0 / 3.0
    return result


def _transform_longitude(x: float, y: float) -> float:
    result = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y
    result += 0.1 * sqrt(abs(x))
    result += (20.0 * sin(6.0 * x * pi) + 20.0 * sin(2.0 * x * pi)) * 2.0 / 3.0
    result += (20.0 * sin(x * pi) + 40.0 * sin(x / 3.0 * pi)) * 2.0 / 3.0
    result += (150.0 * sin(x / 12.0 * pi) + 300.0 * sin(x / 30.0 * pi)) * 2.0 / 3.0
    return result


def wgs84_to_gcj02(lon: float, lat: float) -> tuple[float, float]:
    semi_major = 6378245.0
    eccentricity_squared = 0.006693421622965943
    delta_lat = _transform_latitude(lon - 105.0, lat - 35.0)
    delta_lon = _transform_longitude(lon - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * pi
    magic = 1.0 - eccentricity_squared * sin(rad_lat) ** 2
    sqrt_magic = sqrt(magic)
    delta_lat = (
        delta_lat * 180.0
        / ((semi_major * (1.0 - eccentricity_squared)) / (magic * sqrt_magic) * pi)
    )
    delta_lon = delta_lon * 180.0 / (semi_major / sqrt_magic * cos(rad_lat) * pi)
    return lon + delta_lon, lat + delta_lat


def gcj02_to_wgs84(lon: float, lat: float) -> tuple[float, float]:
    # Fixed-point inversion is deterministic and sub-metre at this footprint scale.
    guess_lon, guess_lat = lon, lat
    for _ in range(8):
        projected_lon, projected_lat = wgs84_to_gcj02(guess_lon, guess_lat)
        guess_lon -= projected_lon - lon
        guess_lat -= projected_lat - lat
    return guess_lon, guess_lat


def pixel_to_coordinates(pixel: list[float], transform: tuple, diagnostics: dict) -> dict:
    east_coefficients, north_coefficients = transform
    local = transform_pixel(pixel, east_coefficients, north_coefficients)
    bd09 = local_to_bd09(local, diagnostics)
    wgs84 = gcj02_to_wgs84(*bd09_to_gcj02(*bd09))
    return {
        "pixel": [round(pixel[0], 3), round(pixel[1], 3)],
        "local_m": [round(local[0], 3), round(local[1], 3)],
        "bd09": [round(bd09[0], 12), round(bd09[1], 12)],
        "wgs84": [round(wgs84[0], 12), round(wgs84[1], 12)],
    }


def feature_collection(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}


def close_ring(points: list[list[float]]) -> list[list[float]]:
    return points if points[0] == points[-1] else points + [points[0]]


def line_length(points: list[tuple[float, float]]) -> float:
    return sum(hypot(x2 - x1, y2 - y1) for (x1, y1), (x2, y2) in zip(points, points[1:]))


def geojson_geometry_sql(geometry: dict) -> str:
    encoded = json.dumps(geometry, ensure_ascii=False, separators=(",", ":")).replace("'", "''")
    return f"ST_SetSRID(ST_GeomFromGeoJSON('{encoded}'), 4326)"


def sql_text(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return "'" + str(value).replace("'", "''") + "'"


def shortest_path_matrix(nodes: list[dict], edges: list[dict], pois: list[dict]) -> list[dict]:
    adjacency: dict[str, list[tuple[str, float]]] = {item["id"]: [] for item in nodes}
    for edge in edges:
        adjacency[edge["source"]].append((edge["target"], edge["walk_seconds"]))
        adjacency[edge["target"]].append((edge["source"], edge["walk_seconds"]))
    scheduler_nodes = {
        poi["scheduler_location_id"]: poi["route_node_id"]
        for poi in pois
        if poi.get("scheduler_location_id")
    }
    rows = []
    for origin, start_node in sorted(scheduler_nodes.items()):
        distances = {start_node: 0.0}
        queue = [(0.0, start_node)]
        while queue:
            current_distance, node = heapq.heappop(queue)
            if current_distance != distances[node]:
                continue
            for neighbor, cost in adjacency[node]:
                candidate = current_distance + cost
                if candidate < distances.get(neighbor, float("inf")):
                    distances[neighbor] = candidate
                    heapq.heappush(queue, (candidate, neighbor))
        for destination, destination_node in sorted(scheduler_nodes.items()):
            if destination == origin:
                continue
            seconds = distances[destination_node]
            rows.append(
                {
                    "origin": origin,
                    "destination": destination,
                    "walk_seconds": round(seconds, 1),
                    "travel_minutes": 0 if seconds == 0 else max(1, ceil(seconds / 60.0)),
                }
            )
    return rows


def main() -> None:
    source = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    east_coefficients, north_coefficients, diagnostics = fit_affine(source["control_points"])
    transform = east_coefficients, north_coefficients
    diagnostics["affine_east_coefficients"] = east_coefficients
    diagnostics["affine_north_coefficients"] = north_coefficients
    diagnostics["wgs84_conversion"] = "BD-09LL -> GCJ-02 -> WGS84, deterministic approximate inverse"
    diagnostics["absolute_accuracy_class"] = "map-derived, non-survey"

    level_ring = close_ring(source["level_footprint_px"])
    level_coords = [pixel_to_coordinates(point, transform, diagnostics) for point in level_ring]
    level_geometry = {
        "type": "Polygon",
        "coordinates": [[item["wgs84"] for item in level_coords]],
    }
    level_feature = {
        "type": "Feature",
        "id": source["facility"]["level_id"],
        "properties": {
            **source["facility"],
            "status": source["source"]["status"],
            "accuracy_m": diagnostics["rmse_m"],
            "geometry_role": "coarse_level_envelope_not_wall_footprint",
        },
        "geometry": level_geometry,
    }

    space_features = []
    for space in source["spaces"]:
        ring = close_ring(space["polygon_px"])
        converted = [pixel_to_coordinates(point, transform, diagnostics) for point in ring]
        properties = {key: value for key, value in space.items() if key != "polygon_px"}
        properties.update({"level_id": source["facility"]["level_id"], "source_method": "manual_raster_trace"})
        space_features.append(
            {
                "type": "Feature",
                "id": space["id"],
                "properties": properties,
                "geometry": {"type": "Polygon", "coordinates": [[item["wgs84"] for item in converted]]},
            }
        )

    route_nodes = []
    node_local = {}
    for node in source["route_nodes"]:
        converted = pixel_to_coordinates(node["pixel"], transform, diagnostics)
        node_local[node["id"]] = tuple(converted["local_m"])
        enriched = {**node, **converted, "level_id": source["facility"]["level_id"]}
        route_nodes.append(enriched)

    route_edges = []
    for edge in source["route_edges"]:
        converted = [pixel_to_coordinates(point, transform, diagnostics) for point in edge["path_px"]]
        local_points = [tuple(item["local_m"]) for item in converted]
        length_m = line_length(local_points)
        route_edges.append(
            {
                **edge,
                "level_id": source["facility"]["level_id"],
                "accessible": edge.get("accessible", True),
                "length_m": round(length_m, 3),
                "walk_seconds": round(length_m / WALKING_SPEED_MPS, 1),
                "coordinates_wgs84": [item["wgs84"] for item in converted],
                "coordinates_local_m": [item["local_m"] for item in converted],
            }
        )

    poi_features = []
    enriched_pois = []
    for poi in source["pois"]:
        converted = pixel_to_coordinates(poi["pixel"], transform, diagnostics)
        enriched = {**poi, **converted, "level_id": source["facility"]["level_id"]}
        enriched_pois.append(enriched)
        properties = {key: value for key, value in enriched.items() if key not in {"pixel", "local_m", "bd09", "wgs84"}}
        properties.update({"pixel": enriched["pixel"], "local_m": enriched["local_m"], "source_crs": "BD-09LL", "source_coordinate": enriched["bd09"]})
        poi_features.append(
            {"type": "Feature", "id": poi["id"], "properties": properties, "geometry": {"type": "Point", "coordinates": enriched["wgs84"]}}
        )

    route_node_features = []
    for node in route_nodes:
        properties = {key: value for key, value in node.items() if key not in {"pixel", "local_m", "bd09", "wgs84"}}
        properties.update({"pixel": node["pixel"], "local_m": node["local_m"]})
        route_node_features.append(
            {"type": "Feature", "id": node["id"], "properties": properties, "geometry": {"type": "Point", "coordinates": node["wgs84"]}}
        )
    route_edge_features = []
    for edge in route_edges:
        properties = {key: value for key, value in edge.items() if key not in {"path_px", "coordinates_wgs84", "coordinates_local_m"}}
        properties.update({"path_px": edge["path_px"], "coordinates_local_m": edge["coordinates_local_m"]})
        route_edge_features.append(
            {"type": "Feature", "id": edge["id"], "properties": properties, "geometry": {"type": "LineString", "coordinates": edge["coordinates_wgs84"]}}
        )

    travel_matrix = shortest_path_matrix(route_nodes, route_edges, enriched_pois)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    outputs = {
        "1f_level.geojson": feature_collection([level_feature]),
        "1f_spaces.geojson": feature_collection(space_features),
        "1f_pois.geojson": feature_collection(poi_features),
        "1f_route_nodes.geojson": feature_collection(route_node_features),
        "1f_route_edges.geojson": feature_collection(route_edge_features),
    }
    for filename, content in outputs.items():
        (OUTPUT_DIR / filename).write_text(
            json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    (OUTPUT_DIR / "1f_metadata.json").write_text(
        json.dumps(
            {"dataset_id": source["dataset_id"], "facility": source["facility"], "source": source["source"], "georeferencing": diagnostics},
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    with (OUTPUT_DIR / "1f_control_points.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["point_id", "pixel_x", "pixel_y", "bd09_lon", "bd09_lat", "residual_m"])
        writer.writeheader()
        for point in source["control_points"]:
            writer.writerow(
                {
                    "point_id": point["id"],
                    "pixel_x": point["pixel"][0],
                    "pixel_y": point["pixel"][1],
                    "bd09_lon": point["bd09"][0],
                    "bd09_lat": point["bd09"][1],
                    "residual_m": diagnostics["residual_m_by_control_point"][str(point["id"])],
                }
            )
    with (OUTPUT_DIR / "1f_scheduler_travel_times.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["origin", "destination", "walk_seconds", "travel_minutes"])
        writer.writeheader()
        writer.writerows(travel_matrix)

    sql_lines = [
        "-- Generated by scripts/build_1f_gis.py; run gis/schema.sql first.",
        "BEGIN;",
        f"DELETE FROM indoor_gis.source_document WHERE dataset_id = {sql_text(source['dataset_id'])};",
        "INSERT INTO indoor_gis.source_document (dataset_id, facility_id, level_id, source_filename, source_sha256, coordinate_source, source_crs, status, georef_rmse_m, metadata)",
        "VALUES ("
        + ", ".join(
            [
                sql_text(source["dataset_id"]), sql_text(source["facility"]["id"]), sql_text(source["facility"]["level_id"]),
                sql_text(source["source"]["floorplan_filename"]), sql_text(source["source"]["floorplan_sha256"]),
                sql_text(source["source"]["coordinate_source"]), sql_text(source["source"]["coordinate_crs"]),
                sql_text(source["source"]["status"]), str(diagnostics["rmse_m"]),
                sql_text(json.dumps(diagnostics, ensure_ascii=False)),
            ]
        )
        + ");",
        "INSERT INTO indoor_gis.facility (facility_id, name) VALUES ("
        + f"{sql_text(source['facility']['id'])}, {sql_text(source['facility']['name'])}) ON CONFLICT (facility_id) DO UPDATE SET name = EXCLUDED.name;",
        "INSERT INTO indoor_gis.level (level_id, facility_id, name, ordinal, height_m, status, accuracy_m, geom) VALUES ("
        + ", ".join(
            [sql_text(source["facility"]["level_id"]), sql_text(source["facility"]["id"]), sql_text(source["facility"]["level_name"]), str(source["facility"]["ordinal"]), str(source["facility"]["height_m"]), sql_text(source["source"]["status"]), str(diagnostics["rmse_m"]), geojson_geometry_sql(level_geometry)]
        )
        + ") ON CONFLICT (level_id) DO UPDATE SET geom = EXCLUDED.geom, status = EXCLUDED.status, accuracy_m = EXCLUDED.accuracy_m;",
    ]
    for feature in space_features:
        props = feature["properties"]
        sql_lines.append(
            "INSERT INTO indoor_gis.space (space_id, level_id, name, use_type, room_ref, confidence, scheduler_location_id, geom) VALUES ("
            + ", ".join(
                [sql_text(props["id"]), sql_text(props["level_id"]), sql_text(props["name"]), sql_text(props["use_type"]), sql_text(props.get("room_ref")), sql_text(props["confidence"]), sql_text(props.get("scheduler_location_id")), geojson_geometry_sql(feature["geometry"])]
            )
            + ") ON CONFLICT (space_id) DO UPDATE SET name=EXCLUDED.name, use_type=EXCLUDED.use_type, confidence=EXCLUDED.confidence, geom=EXCLUDED.geom;"
        )
    for feature in route_node_features:
        props = feature["properties"]
        sql_lines.append(
            "INSERT INTO indoor_gis.path_node (node_id, level_id, name, kind, accessible, geom) VALUES ("
            + ", ".join([sql_text(props["id"]), sql_text(props["level_id"]), sql_text(props["name"]), sql_text(props["kind"]), sql_text(props.get("accessible", True)), geojson_geometry_sql(feature["geometry"])])
            + ") ON CONFLICT (node_id) DO UPDATE SET name=EXCLUDED.name, kind=EXCLUDED.kind, accessible=EXCLUDED.accessible, geom=EXCLUDED.geom;"
        )
    for feature in route_edge_features:
        props = feature["properties"]
        sql_lines.append(
            "INSERT INTO indoor_gis.path_edge (edge_id, level_id, source_node_id, target_node_id, length_m, walk_seconds, accessible, geom) VALUES ("
            + ", ".join([sql_text(props["id"]), sql_text(props["level_id"]), sql_text(props["source"]), sql_text(props["target"]), str(props["length_m"]), str(props["walk_seconds"]), sql_text(props["accessible"]), geojson_geometry_sql(feature["geometry"])])
            + ") ON CONFLICT (edge_id) DO UPDATE SET length_m=EXCLUDED.length_m, walk_seconds=EXCLUDED.walk_seconds, accessible=EXCLUDED.accessible, geom=EXCLUDED.geom;"
        )
    for feature in poi_features:
        props = feature["properties"]
        sql_lines.append(
            "INSERT INTO indoor_gis.poi (poi_id, level_id, route_node_id, name, category, confidence, scheduler_location_id, accessible, geom) VALUES ("
            + ", ".join([sql_text(props["id"]), sql_text(props["level_id"]), sql_text(props["route_node_id"]), sql_text(props["name"]), sql_text(props["category"]), sql_text(props["confidence"]), sql_text(props.get("scheduler_location_id")), sql_text(props.get("accessible", True)), geojson_geometry_sql(feature["geometry"])])
            + ") ON CONFLICT (poi_id) DO UPDATE SET name=EXCLUDED.name, category=EXCLUDED.category, confidence=EXCLUDED.confidence, scheduler_location_id=EXCLUDED.scheduler_location_id, geom=EXCLUDED.geom;"
        )
    sql_lines.extend(["COMMIT;", ""])
    (OUTPUT_DIR / "1f_seed.sql").write_text("\n".join(sql_lines), encoding="utf-8")

    print(
        f"built {len(space_features)} spaces, {len(poi_features)} POIs, "
        f"{len(route_node_features)} nodes, {len(route_edge_features)} edges; "
        f"georef RMSE={diagnostics['rmse_m']} m"
    )


if __name__ == "__main__":
    main()
