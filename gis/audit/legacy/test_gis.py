import json
import unittest
from pathlib import Path

from checkup_scheduler.gis import load_travel_time_matrix_csv


ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "gis" / "generated"


class GeneratedIndoorGisTests(unittest.TestCase):
    def test_generated_geojson_is_wgs84_and_nonempty(self):
        expected = {
            "1f_level.geojson": 1,
            "1f_spaces.geojson": 32,
            "1f_pois.geojson": 17,
            "1f_route_nodes.geojson": 28,
            "1f_route_edges.geojson": 28,
        }
        for filename, count in expected.items():
            with self.subTest(filename=filename):
                content = json.loads((GENERATED / filename).read_text(encoding="utf-8"))
                self.assertEqual(content["type"], "FeatureCollection")
                self.assertEqual(len(content["features"]), count)
                coordinates = list(_coordinates(content))
                self.assertTrue(coordinates)
                for longitude, latitude in coordinates:
                    self.assertTrue(119.0 < longitude < 121.0)
                    self.assertTrue(29.0 < latitude < 31.0)

    def test_route_graph_is_connected(self):
        nodes = json.loads(
            (GENERATED / "1f_route_nodes.geojson").read_text(encoding="utf-8")
        )["features"]
        edges = json.loads(
            (GENERATED / "1f_route_edges.geojson").read_text(encoding="utf-8")
        )["features"]
        adjacency = {feature["id"]: set() for feature in nodes}
        for edge in edges:
            source = edge["properties"]["source"]
            target = edge["properties"]["target"]
            self.assertIn(source, adjacency)
            self.assertIn(target, adjacency)
            self.assertGreater(edge["properties"]["length_m"], 0)
            adjacency[source].add(target)
            adjacency[target].add(source)
        visited = set()
        pending = [next(iter(adjacency))]
        while pending:
            node = pending.pop()
            if node in visited:
                continue
            visited.add(node)
            pending.extend(adjacency[node] - visited)
        self.assertEqual(visited, set(adjacency))

    def test_scheduler_matrix_loads(self):
        matrix = load_travel_time_matrix_csv(
            GENERATED / "1f_scheduler_travel_times.csv"
        )
        self.assertEqual(matrix.between("BLOOD", "URINE"), 0)
        self.assertGreaterEqual(matrix.between("LOBBY", "XRAY"), 1)
        self.assertEqual(
            matrix.between("LOBBY", "XRAY"),
            matrix.between("XRAY", "LOBBY"),
        )

    def test_generated_upper_floor_geojson_and_combined_graph(self):
        expected = {
            "2f_level.geojson": 1,
            "2f_spaces.geojson": 34,
            "2f_pois.geojson": 34,
            "2f_route_nodes.geojson": 57,
            "2f_route_edges.geojson": 56,
            "3f_level.geojson": 1,
            "3f_spaces.geojson": 24,
            "3f_pois.geojson": 24,
            "3f_route_nodes.geojson": 36,
            "3f_route_edges.geojson": 35,
        }
        for filename, count in expected.items():
            with self.subTest(filename=filename):
                content = json.loads((GENERATED / filename).read_text(encoding="utf-8"))
                self.assertEqual(len(content["features"]), count)
                for longitude, latitude in _coordinates(content):
                    self.assertTrue(119.0 < longitude < 121.0)
                    self.assertTrue(29.0 < latitude < 31.0)

        nodes = {}
        adjacency = {}
        for floor in ("1f", "2f", "3f"):
            features = json.loads(
                (GENERATED / f"{floor}_route_nodes.geojson").read_text(encoding="utf-8")
            )["features"]
            for feature in features:
                nodes[feature["id"]] = feature
                adjacency[feature["id"]] = set()
            edges = json.loads(
                (GENERATED / f"{floor}_route_edges.geojson").read_text(encoding="utf-8")
            )["features"]
            for edge in edges:
                source = edge["properties"]["source"]
                target = edge["properties"]["target"]
                adjacency[source].add(target)
                adjacency[target].add(source)

        import csv

        connections = []
        for floor in ("2f", "3f"):
            with (GENERATED / f"{floor}_vertical_connections.csv").open(
                encoding="utf-8-sig", newline=""
            ) as handle:
                connections.extend(csv.DictReader(handle))
        self.assertEqual(len(connections), 6)
        for connection in connections:
            source = connection["from_node_id"]
            target = connection["to_node_id"]
            self.assertIn(source, nodes)
            self.assertIn(target, nodes)
            adjacency[source].add(target)
            adjacency[target].add(source)

        visited = set()
        pending = ["n_entrance"]
        while pending:
            node = pending.pop()
            if node in visited:
                continue
            visited.add(node)
            pending.extend(adjacency[node] - visited)
        self.assertEqual(visited, set(nodes))

    def test_2f_registration_accuracy_is_explicit(self):
        metadata = json.loads(
            (GENERATED / "2f_metadata.json").read_text(encoding="utf-8")
        )
        georeferencing = metadata["georeferencing"]
        self.assertLess(georeferencing["alignment_check_rmse_m"], 3.0)
        self.assertGreater(
            georeferencing["estimated_absolute_accuracy_m"],
            georeferencing["rmse_m"],
        )

    def test_building_names_and_3f_registration(self):
        spaces_2f = json.loads(
            (GENERATED / "2f_spaces.geojson").read_text(encoding="utf-8")
        )["features"]
        names = {feature["id"]: feature["properties"]["name"] for feature in spaces_2f}
        buildings = {
            feature["id"]: feature["properties"]["building_id"]
            for feature in spaces_2f
        }
        self.assertEqual(names["2f_w_201"], "201（二号楼）")
        self.assertEqual(names["2f_c_201"], "201（一号楼）")
        self.assertEqual(buildings["2f_w_201"], "zju2_zijingang_b2")
        self.assertEqual(buildings["2f_e_202"], "zju2_zijingang_b1")

        spaces_3f = json.loads(
            (GENERATED / "3f_spaces.geojson").read_text(encoding="utf-8")
        )["features"]
        self.assertTrue(
            all(
                feature["properties"]["building_id"] == "zju2_zijingang_b1"
                for feature in spaces_3f
            )
        )
        metadata = json.loads(
            (GENERATED / "3f_metadata.json").read_text(encoding="utf-8")
        )
        self.assertLess(metadata["georeferencing"]["alignment_check_rmse_m"], 2.0)


def _coordinates(content):
    for feature in content["features"]:
        yield from _geometry_coordinates(feature["geometry"])


def _geometry_coordinates(geometry):
    if geometry["type"] == "Point":
        yield geometry["coordinates"]
        return
    if geometry["type"] == "LineString":
        yield from geometry["coordinates"]
        return
    if geometry["type"] == "Polygon":
        for ring in geometry["coordinates"]:
            yield from ring
        return
    raise AssertionError(f"unexpected geometry type: {geometry['type']}")


if __name__ == "__main__":
    unittest.main()
