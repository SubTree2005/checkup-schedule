"""Confirmed pre-exam registration destination for the supplied Zijingang plan."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

from .navigation_routes import enabled

REGISTRY = json.loads((Path(__file__).parent / 'data/registration_destination.json').read_text(encoding='utf-8'))
REGISTRATION_NAME = '放射登记（综合服务中心）'
REGISTRATION_NOTICE = '检查前请先沿蓝线到一楼综合服务中心办理放射登记，再按登记处指引前往检查区域。'


def matches(feature, expected):
    p, q = feature.get('properties') or {}, expected['properties']
    return (feature.get('geometry') == expected['geometry']
            and all(p.get(k) == q.get(k) for k in ('id', 'space_id', 'building_id', 'name')))


def registration_target(floors, target):
    """Return copied routing data only for the confirmed map; never write to GIS."""
    floor, point, props = target
    if floor.floor_key != REGISTRY['floorKey'] or not matches({'geometry': {'type': 'Point', 'coordinates': point}, 'properties': props}, REGISTRY['radiology']):
        return floors, target, False
    # An explicit business binding / mapped clinical entrance takes precedence.
    if not enabled(props) or props.get('deptID') or props.get('route_node_id') or props.get('routeNodeId'):
        return floors, target, False
    features = floor.geojson.get('features', [])
    services = [f for f in features if matches(f, REGISTRY['service']) and enabled(f.get('properties') or {})]
    if len(services) != 1:
        return floors, target, False
    service = services[0]
    if not (service['properties'].get('route_node_id') or service['properties'].get('routeNodeId')):
        expected = REGISTRY['originalEdge']
        edges = [f for f in features if f.get('geometry') == expected['geometry']
                 and all((f.get('properties') or {}).get(k) == expected['properties'].get(k)
                         for k in ('source', 'target', 'direction', 'routing_status', 'access_control'))]
        if len(edges) != 1:
            return floors, target, False
        # Preserve administrator closures / one-way edits by requiring the original
        # edge above, and avoid clashing with a partially edited replacement graph.
        if any('n_1f_640_387' in ((f.get('properties') or {}).get('source'), (f.get('properties') or {}).get('target')) for f in features):
            return floors, target, False
        geojson = copy.deepcopy(floor.geojson)
        geojson['features'].pop(features.index(edges[0]))
        geojson['features'].extend(copy.deepcopy(REGISTRY['replacementEdges']))
        service = next(f for f in geojson['features'] if matches(f, REGISTRY['service']))
        service['properties']['route_node_id'] = REGISTRY['routeNodeID']
        updated = SimpleNamespace(floor_key=floor.floor_key, version=floor.version, geojson=geojson)
        floors = [updated if f is floor else f for f in floors]
        floor = updated
    return floors, (floor, service['geometry']['coordinates'], service['properties']), True
