import unittest
from pydantic import ValidationError
from apps.backend.checkup_backend.schemas import GISUpload


class GuidanceSchemaTest(unittest.TestCase):
    def payload(self, **props):
        return {'geojson': {'type': 'FeatureCollection', 'features': [{'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [120,30]},
            'properties': {'route_node_id': 'door', 'guidanceFor': ['radiology'], **props}}]}}

    def test_guidance_round_trip_and_invalid_configuration(self):
        payload = self.payload(guidanceOrder=2)
        self.assertEqual(GISUpload(**payload).model_dump(), payload)
        for props in ({'guidanceFor': 'dept'}, {'guidanceFor': [{}]}, {'guidanceFor': ['a','a']},
                      {'guidanceOrder': True}, {'guidanceOrder': -1}, {'route_node_id': None}):
            with self.subTest(props=props), self.assertRaises(ValidationError):
                GISUpload(**self.payload(**props))
