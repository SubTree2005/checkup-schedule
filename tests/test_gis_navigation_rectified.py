import copy
import json
import unittest
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from apps.backend.checkup_backend.patient_api import _navigation_map
from apps.backend.checkup_backend.navigation_coordinates import registry_in_pixels, PIXEL_SYSTEM
from apps.backend.checkup_backend.navigation_gis import PIXEL_REGISTRY


class RectifiedNavigationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.original = json.loads((root/'gis/generated/workspace_gis_only.json').read_text(encoding='utf-8'))
        cls.bundle = registry_in_pixels(cls.original)
        for floor in cls.bundle['gis']:
            floor['geojson'].update(coordinateSystem=PIXEL_SYSTEM, rectificationVersion='orthogonal-20260909')

    def setUp(self):
        self.floors = [SimpleNamespace(floor_key=f['floorKey'], geojson=copy.deepcopy(f['geojson']), version=1) for f in self.bundle['gis']]
        self.departments = [SimpleNamespace(dept_id='lab', dept_name='检验科', location='1F（血、尿、便检查）'),
                            SimpleNamespace(dept_id='internal', dept_name='内科（318）', location='3F 318'),
                            SimpleNamespace(dept_id='radio', dept_name='放射科（MRI、CT、DR）', location='1F；先登记')]

    def navigation(self, source='lab', target='internal'):
        db = Mock()
        db.scalars.side_effect = [Mock(all=lambda:self.floors), Mock(all=lambda:self.departments)]
        return _navigation_map(db,'h',source,target,'检验科','内科（318）')

    def assert_lab_route(self):
        result = self.navigation()
        segments = result['segments']
        self.assertEqual([s['floorKey'] for s in segments], ['1F','2F','3F'])
        self.assertEqual(segments[0]['fromPoint']['departmentID'], 'lab')
        self.assertIn('楼梯', segments[0]['toPoint']['name'])
        self.assertIn('楼梯', segments[-1]['fromPoint']['name'])
        self.assertEqual(segments[-1]['toPoint']['departmentID'], 'internal')
        for segment in (segments[0], segments[-1]):
            self.assertGreater(len(segment['routeCoordinates']), 1)
            self.assertEqual(segment['routeCoordinates'][0], segment['fromPoint']['coordinates'])
            self.assertEqual(segment['routeCoordinates'][-1], segment['toPoint']['coordinates'])
        return result

    @unittest.skipUnless(importlib.util.find_spec('shapely'), 'Install [gis] for exporter validation')
    def test_new_export_has_pixel_connectors_and_full_lab_route(self):
        from scripts.export_orthogonal_gis import rectify
        exported, _ = rectify(self.original)
        self.floors = [SimpleNamespace(floor_key=f['floorKey'],geojson=f['geojson'],version=1) for f in exported['gis']]
        result = self.assert_lab_route()
        self.assertGreater(result['walkSeconds'], 120)
        for floor in self.floors:
            for connector in floor.geojson.get('verticalConnections', []):
                self.assertLess(connector['from_coordinates'][1], 0)
                self.assertLess(connector['to_coordinates'][1], 0)

    def test_old_rectified_export_without_connectors(self):
        for floor in self.floors:floor.geojson.pop('verticalConnections', None)
        self.assert_lab_route()

    def test_old_rectified_radiology_and_registration_patches(self):
        for floor in self.floors:floor.geojson.pop('verticalConnections', None)
        features = self.floors[0].geojson['features']
        features[:] = [f for f in features if not any(node in ((f.get('properties') or {}).get('source'), (f.get('properties') or {}).get('target')) for node in ('n_o_1f_b2_radiology', 'n_1f_640_387'))]
        features.append(copy.deepcopy(PIXEL_REGISTRY['originalEdge']))
        for index, feature in enumerate(features):
            for key, original in [('radiologySpace', 'originalRadiologySpace'), ('radiologyPOI','radiology'), ('service','service')]:
                if feature.get('id') == PIXEL_REGISTRY[key]['id']:
                    features[index] = copy.deepcopy(PIXEL_REGISTRY[original])
        before = copy.deepcopy(self.floors[0].geojson)
        result = self.navigation(source='radio')
        self.assertEqual([s['floorKey'] for s in result['segments']], ['1F','2F','3F'])
        result = self.navigation(source='internal',target='radio')
        self.assertEqual([s['floorKey'] for s in result['segments']], ['3F','2F','1F'])
        self.assertIn('综合服务中心', result['guidanceNotice'])
        self.assertEqual(self.floors[0].geojson, before)

    def test_explicit_empty_connectors_and_moved_stairs_do_not_get_bypassed(self):
        for floor in self.floors:floor.geojson['verticalConnections'] = []
        self.assertNotIn('segments', self.navigation())
        for floor in self.floors:
            floor.geojson.pop('verticalConnections')
            for feature in floor.geojson['features']:
                if feature['geometry']['type'] == 'LineString':
                    for point in feature['geometry']['coordinates']:point[0] += 10
        self.assertNotIn('segments', self.navigation())

    def test_rectified_guidance_route_remains_continuous(self):
        result = self.navigation(source='internal', target='radio')
        self.assertEqual([s['floorKey'] for s in result['segments']], ['3F','2F','1F'])
        waypoint = result['waypoints'][0]
        self.assertIn(waypoint['coordinates'], result['routeCoordinates'])
        self.assertNotEqual(waypoint['coordinates'], result['toPoint']['coordinates'])
