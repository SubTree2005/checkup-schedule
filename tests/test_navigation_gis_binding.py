import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from apps.backend.checkup_backend.patient_api import _navigation_map


class NavigationGISBindingTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        bundle = json.loads((root / 'gis/generated/workspace_gis_only.json').read_text(encoding='utf-8'))
        self.floors = [SimpleNamespace(floor_key=f['floorKey'], geojson=f['geojson'], version=2) for f in bundle['gis']]
        self.departments = [
            SimpleNamespace(dept_id='general', dept_name='一般检查（316）', location='3F 316'),
            SimpleNamespace(dept_id='breath', dept_name='呼气试验室（306）', location='3F 306（旁边为抽血处）'),
            SimpleNamespace(dept_id='lab', dept_name='检验科', location='1F（血、尿、便检查）'),
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
        self.assertEqual(result['toPoint']['coordinates'], point['geometry']['coordinates'])
        self.assertEqual(result['fromPoint']['departmentID'], 'breath')
        self.assertEqual(result['routeCoordinates'], [])
        self.assertTrue(result['geojson']['features'])

    def test_named_poi_and_cross_floor(self):
        result = self.navigation(source='lab')
        self.assertEqual(result['fromPoint']['floorKey'], '1F')
        self.assertEqual(result['toPoint']['floorKey'], '3F')
        self.assertEqual(result['routeCoordinates'], [])

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
