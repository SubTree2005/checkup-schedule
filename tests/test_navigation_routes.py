import unittest
from types import SimpleNamespace

from apps.backend.checkup_backend.navigation_routes import route_segments


class NavigationRoutesTest(unittest.TestCase):
    def setUp(self):
        def floor(key, source, target, points):
            features = [{'geometry': {'type': 'LineString', 'coordinates': points}, 'properties': {
                'featureType': 'corridor', 'source': source, 'target': target,
                'length_m': 10, 'walk_seconds': 10, 'direction': 'bidirectional'}}]
            for node, p in ((source, points[0]), (target, points[-1])):
                features.append({'geometry': {'type': 'Point', 'coordinates': p}, 'properties': {'route_node_id': node, 'name': node}})
            return SimpleNamespace(floor_key=key, version=1, geojson={'features': features, 'verticalConnections': []})
        self.floors = [floor('1F','room-A','stair-A1',[[0,0],[10,0]]),
                       floor('2F','stair-A2','stair-B2',[[0,0],[5,5],[10,0]]),
                       floor('3F','stair-B3','room-B',[[0,0],[10,0]])]
        self.floors[0].geojson['verticalConnections'] = [
            {'from_node_id':'stair-A1','to_node_id':'stair-A2','mode':'stairs','walk_seconds':60,'routing_status':'enabled'},
            {'from_node_id':'stair-B2','to_node_id':'stair-B3','mode':'stairs','walk_seconds':60,'routing_status':'enabled'}]
        self.source = (self.floors[0],[-1,-1],{'route_node_id':'room-A'},'起点')
        self.target = (self.floors[2],[11,1],{'route_node_id':'room-B'},'终点')

    def test_transfer_between_staircases_is_drawn_on_middle_floor(self):
        result = route_segments(self.floors,self.source,self.target)
        segments = result['segments']
        self.assertEqual([s['floorKey'] for s in segments],['1F','2F','3F'])
        self.assertEqual(segments[1]['routeCoordinates'],[[0,0],[5,5],[10,0]])
        self.assertEqual(segments[1]['fromPoint']['name'],'stair-A2')
        self.assertEqual(segments[1]['toPoint']['name'],'stair-B2')
        self.assertEqual(result['walkSeconds'],150)
        self.assertEqual(result['horizontalDistanceMeters'],30)
        # Room-centre coordinates are never snapped through walls.
        self.assertEqual(segments[0]['routeCoordinates'][0],[0,0])
        reverse = route_segments(self.floors,self.target,self.source)
        self.assertEqual([s['floorKey'] for s in reverse['segments']],['3F','2F','1F'])
        self.assertEqual(reverse['segments'][1]['routeCoordinates'],[[10,0],[5,5],[0,0]])

    def test_disabled_disconnected_and_one_way_paths(self):
        self.floors[1].geojson['features'][0]['properties']['routing_status']='disabled'
        self.assertIsNone(route_segments(self.floors,self.source,self.target))
        self.floors[1].geojson['features'][0]['properties']['routing_status']='enabled'
        connector=self.floors[0].geojson['verticalConnections'][0]
        connector['routing_status']='disabled'
        self.assertIsNone(route_segments(self.floors,self.source,self.target))
        connector['routing_status']='enabled'
        connector['direction']='forward'
        self.assertIsNotNone(route_segments(self.floors,self.source,self.target))
        self.assertIsNone(route_segments(self.floors,self.target,self.source))

    def test_missing_anchor_does_not_snap_to_an_unrelated_corridor(self):
        source=(self.floors[0],[0,0],{'route_node_id':'missing'},'起点')
        self.assertIsNone(route_segments(self.floors,source,self.target))


if __name__ == '__main__':
    unittest.main()
