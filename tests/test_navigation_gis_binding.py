import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from apps.backend.checkup_backend.patient_api import _navigation_map
from apps.backend.checkup_backend.navigation_gis import REGISTRY, guidance_waypoints


class NavigationGISBindingTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        bundle = json.loads((root / 'gis/generated/workspace_gis_only.json').read_text(encoding='utf-8'))
        self.floors = [SimpleNamespace(floor_key=f['floorKey'], geojson=f['geojson'], version=2) for f in bundle['gis']]
        self.departments = [
            SimpleNamespace(dept_id='general', dept_name='一般检查（316）', location='3F 316'),
            SimpleNamespace(dept_id='breath', dept_name='呼气试验室（306）', location='3F 306（旁边为抽血处）'),
            SimpleNamespace(dept_id='lab', dept_name='检验科', location='1F（血、尿、便检查）'),
            SimpleNamespace(dept_id='radiology', dept_name='放射科（MRI、CT、DR）', location='1F；检查前先到放射登记窗口登记'),
        ]

    def navigation(self, target='general', source=None):
        db = Mock()
        db.scalars.side_effect = [Mock(all=lambda: self.floors), Mock(all=lambda: self.departments)]
        return _navigation_map(db, 'hospital', source, target, '起点', '终点')

    def test_actual_316_map_and_annotated_address(self):
        result = self.navigation(source='breath')
        self.assertEqual(result['floorKey'], '3F')
        self.assertEqual(result['toPoint']['departmentID'], 'general')
        point = next(f for f in self.floors[2].geojson['features']
                     if f['geometry']['type'] == 'Point' and f['properties'].get('room_ref') == '316')
        door_id = point['properties']['route_node_id']
        door_edge = next(f for f in self.floors[2].geojson['features'] if f['properties'].get('source') == door_id or f['properties'].get('target') == door_id)
        door = door_edge['geometry']['coordinates'][0 if door_edge['properties']['source'] == door_id else -1]
        self.assertEqual(result['toPoint']['coordinates'], door)
        self.assertEqual(result['fromPoint']['departmentID'], 'breath')
        self.assertGreater(len(result['routeCoordinates']), 1)
        self.assertTrue(result['geojson']['features'])

    def test_named_poi_and_cross_floor(self):
        result = self.navigation(source='lab')
        self.assertEqual(result['segments'][0]['fromPoint']['floorKey'], '1F')
        self.assertEqual(result['toPoint']['floorKey'], '3F')
        self.assertEqual([s['floorKey'] for s in result['segments']], ['1F', '2F', '3F'])
        self.assertGreater(len(result['routeCoordinates']), 1)
        self.assertIn('楼梯', result['segments'][0]['toPoint']['name'])
        self.assertIn('楼梯', result['segments'][-1]['fromPoint']['name'])
        self.assertGreater(result['walkSeconds'], 120)

    def test_first_step_starts_at_entrance(self):
        result = self.navigation()
        self.assertEqual(result['segments'][0]['fromPoint']['name'], '门诊入口')
        self.assertEqual([s['floorKey'] for s in result['segments']], ['1F', '2F', '3F'])

    def test_radiology_routes_all_floors_via_registration_to_clinical_door(self):
        result = self.navigation(target='radiology', source='general')
        self.assertEqual([s['floorKey'] for s in result['segments']], ['3F', '2F', '1F'])
        self.assertEqual(result['toPoint']['departmentID'], 'radiology')
        self.assertIn('综合服务中心', result['guidanceNotice'])
        self.assertEqual(result['routeCoordinates'][-1], result['toPoint']['coordinates'])
        approach = next(f for f in REGISTRY['replacementEdges'] if f['properties']['target'] == REGISTRY['routeNodeID'])
        self.assertIn(approach['geometry']['coordinates'][-1], result['routeCoordinates'])
        self.assertEqual(result['waypoints'][0]['coordinates'], approach['geometry']['coordinates'][-1])
        self.assertEqual(result['routeCoordinates'][-2:], REGISTRY['exitApproach']['geometry']['coordinates'])
        self.assertGreater(len(result['segments'][0]['routeCoordinates']), 1)
        self.assertGreater(result['walkSeconds'], 0)

    def legacy_registration_floor(self):
        floor = self.floors[0]
        floor.geojson.pop('verticalConnections', None)
        for f in self.floors[1:]:
            f.geojson.pop('verticalConnections', None)
        features = floor.geojson['features']
        features[:] = [f for f in features if 'n_1f_640_387' not in (f['properties'].get('source'), f['properties'].get('target'))]
        features.append(copy.deepcopy(REGISTRY['originalEdge']))
        service = next(f for f in features if f['properties'].get('space_id') == '1f_b1_service' and f['geometry']['type'] == 'Point')
        service['properties']['route_node_id'] = None
        service['properties'].pop('guidanceFor', None)
        features[:] = [f for f in features if 'n_o_1f_b2_radiology' not in (f['properties'].get('source'), f['properties'].get('target'))]
        for index, feature in enumerate(features):
            if feature.get('id') == REGISTRY['radiologyPOI']['id']:
                features[index] = copy.deepcopy(REGISTRY['radiology'])
            if feature.get('id') == REGISTRY['radiologySpace']['id']:
                features[index] = copy.deepcopy(REGISTRY['originalRadiologySpace'])
        return floor

    def test_old_upload_gets_complete_route_without_mutating_stored_gis(self):
        floor = self.legacy_registration_floor()
        before = copy.deepcopy(floor.geojson)
        result = self.navigation(target='radiology', source='general')
        self.assertEqual([s['floorKey'] for s in result['segments']], ['3F', '2F', '1F'])
        self.assertEqual(result['toPoint']['coordinates'], REGISTRY['exitApproach']['geometry']['coordinates'][-1])
        reverse = self.navigation(source='radiology')
        self.assertEqual([s['floorKey'] for s in reverse['segments']], ['1F', '2F', '3F'])
        self.assertEqual(reverse['segments'][0]['fromPoint']['coordinates'], REGISTRY['exitApproach']['geometry']['coordinates'][-1])
        self.assertEqual(floor.geojson, before)

    def test_registration_from_entrance_and_departure_from_confirmed_exit(self):
        result = self.navigation(target='radiology')
        self.assertEqual(result['segments'][0]['fromPoint']['name'], '门诊入口')
        self.assertEqual(result['toPoint']['departmentID'], 'radiology')
        reverse = self.navigation(source='radiology')
        self.assertEqual([s['floorKey'] for s in reverse['segments']], ['1F', '2F', '3F'])
        self.assertNotIn('guidanceNotice', reverse)
        self.assertEqual(reverse['segments'][0]['routeCoordinates'][0], REGISTRY['exitApproach']['geometry']['coordinates'][-1])

    def test_old_upload_closure_or_changed_geometry_is_not_overridden(self):
        for change in ('closed', 'moved'):
            with self.subTest(change=change):
                self.setUp()
                floor = self.legacy_registration_floor()
                edge = floor.geojson['features'][-1]
                if change == 'closed':
                    edge['properties']['access_control'] = 'closed'
                else:
                    edge['geometry']['coordinates'][0][0] += 0.001
                result = self.navigation(target='radiology', source='general')
                self.assertNotIn('segments', result)
                self.assertIn('routeUnavailableNotice', result)

    def test_explicit_radiology_binding_takes_precedence(self):
        point = next(f for f in self.floors[0].geojson['features'] if f.get('id') == 'poi_1f_b2_radiology')
        point['properties']['deptID'] = 'radiology'
        result = self.navigation(target='radiology', source='general')
        self.assertEqual(result['toPoint']['departmentID'], 'radiology')

    def test_explicit_empty_guidance_and_closed_mandatory_stop(self):
        service = next(f for f in self.floors[0].geojson['features'] if f.get('id') == REGISTRY['service']['id'])
        service['properties']['guidanceFor'] = []
        self.assertNotIn('guidanceNotice', self.navigation(target='radiology'))
        service['properties']['guidanceFor'] = ['radiology']
        self.assertIn('guidanceNotice', self.navigation(target='radiology'))
        service['properties']['access_control'] = 'closed'
        result = self.navigation(target='radiology')
        self.assertNotIn('segments', result)
        self.assertIn('routeUnavailableNotice', result)

    def test_guidance_order_and_binding_are_authored_in_gis(self):
        floor = self.floors[0]
        for id_, order in [('first', 20), ('second', 10)]:
            floor.geojson['features'].append({'id': id_, 'geometry': {'type': 'Point', 'coordinates': [0,0]},
                'properties': {'name': id_, 'guidanceFor': ['other-dept'], 'guidanceOrder': order, 'route_node_id': id_}})
        stops = guidance_waypoints(self.floors, {}, 'other-dept')
        self.assertEqual([s[3] for s in stops], ['second', 'first'])
        self.assertEqual(guidance_waypoints(self.floors, {}, 'unbound-dept'), [])

    def test_unroutable_other_floor_origin_is_not_painted_on_destination_floor(self):
        self.floors[0].geojson['verticalConnections'] = []
        result = self.navigation(source='lab')
        self.assertIsNone(result['fromPoint'])

    def test_existing_gis_upload_uses_verified_legacy_connections(self):
        for f in self.floors:
            f.geojson.pop('verticalConnections', None)
        result = self.navigation(source='lab')
        self.assertEqual([s['floorKey'] for s in result['segments']], ['1F', '2F', '3F'])
        # A different map reusing the IDs cannot use the packaged connectors.
        for f in self.floors:
            for feature in f.geojson['features']:
                if feature['geometry']['type'] == 'LineString':
                    for point in feature['geometry']['coordinates']:
                        point[0] += 0.001
        self.assertNotIn('segments', self.navigation(source='lab'))

    def test_explicit_empty_connectors_disable_legacy_fallback(self):
        self.floors[0].geojson['verticalConnections'] = []
        result = self.navigation(source='lab')
        self.assertNotIn('segments', result)
        self.assertEqual(result['routeCoordinates'], [])

    def test_legacy_connector_registry_matches_generated_evidence(self):
        root = Path(__file__).resolve().parents[1]
        registry = json.loads((root / 'apps/backend/checkup_backend/data/legacy_gis_connections.json').read_text(encoding='utf-8'))
        generated = json.loads((root / 'gis/generated/vertical_connections.json').read_text(encoding='utf-8'))
        self.assertEqual([{k:v for k,v in c.items() if k not in ('from_floor','to_floor','from_coordinates','to_coordinates')} for c in registry], generated)

    def test_missing_and_ambiguous_addresses(self):
        self.departments[0].location = '2F 316'
        self.assertIsNone(self.navigation())
        self.departments[0].location = '3F 316'
        self.departments.append(SimpleNamespace(dept_id='other', dept_name='其他科室', location='3F 316'))
        self.assertIsNone(self.navigation())

    def test_duplicate_building_rooms_and_explicit_binding(self):
        features = self.floors[2].geojson['features']
        point = next(f for f in features if f['geometry']['type'] == 'Point' and f['properties'].get('room_ref') == '316')
        duplicate = copy.deepcopy(point)
        duplicate['properties']['building_id'] = 'another-building'
        features.append(duplicate)
        self.assertIsNone(self.navigation())
        point['properties']['deptID'] = 'general'
        self.assertIsNotNone(self.navigation())
        features.remove(duplicate)
        point['properties']['deptID'] = 'deleted-department'
        self.assertIsNone(self.navigation())


if __name__ == '__main__':
    unittest.main()
