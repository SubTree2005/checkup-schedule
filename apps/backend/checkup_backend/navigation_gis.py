"""Evidence-scoped compatibility and GIS-authored mandatory guidance stops."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

from .navigation_routes import enabled

REGISTRY = json.loads((Path(__file__).parent / 'data/registration_destination.json').read_text(encoding='utf-8'))


def matches(feature, expected):
    p, q = feature.get('properties') or {}, expected['properties']
    return (feature.get('geometry') == expected['geometry']
            and all(p.get(k) == q.get(k) for k in ('id', 'space_id', 'building_id', 'name')))


def matching_edge(features, expected):
    edges = [f for f in features if f.get('geometry') == expected['geometry']
             and all((f.get('properties') or {}).get(k) == expected['properties'].get(k)
                     for k in ('source', 'target', 'direction', 'routing_status', 'access_control'))]
    return edges[0] if len(edges) == 1 and enabled(edges[0]['properties']) else None


def prepare_navigation_floors(floors):
    """Copy known legacy geometry only; explicit edits and stored GIS stay intact."""
    result = []
    for floor in floors:
        if floor.floor_key != REGISTRY['floorKey']:
            result.append(floor)
            continue
        geojson = copy.deepcopy(floor.geojson)
        features = geojson.get('features', [])
        services = [f for f in features if matches(f, REGISTRY['service'])]
        radiology = [f for f in features if matches(f, REGISTRY['radiology']) or matches(f, REGISTRY['radiologyPOI'])]
        if len(services) != 1 or len(radiology) != 1:
            result.append(floor)
            continue
        service, target = services[0], radiology[0]
        props = service['properties']
        edge = matching_edge(features, REGISTRY['originalEdge'])
        if (enabled(props) and not (props.get('route_node_id') or props.get('routeNodeId') or props.get('deptID')) and edge
                and not any('n_1f_640_387' in ((f.get('properties') or {}).get('source'), (f.get('properties') or {}).get('target')) for f in features)):
            features.remove(edge)
            features.extend(copy.deepcopy(REGISTRY['replacementEdges']))
            props['route_node_id'] = REGISTRY['routeNodeID']
        # Explicit [] disables the old map's supplied registration rule.
        props.setdefault('guidanceFor', [REGISTRY['radiology']['id']])
        props.setdefault('guidanceOrder', 0)
        props = target['properties']
        old_spaces = [f for f in features if f.get('id') == REGISTRY['originalRadiologySpace']['id'] and f.get('geometry') == REGISTRY['originalRadiologySpace']['geometry']]
        if (enabled(props) and not (props.get('route_node_id') or props.get('routeNodeId') or props.get('deptID'))
                and len(old_spaces) == 1 and matching_edge(features, REGISTRY['exitEdge'])
                and not any('n_o_1f_b2_radiology' in ((f.get('properties') or {}).get('source'), (f.get('properties') or {}).get('target')) for f in features)):
            features[features.index(old_spaces[0])] = copy.deepcopy(REGISTRY['radiologySpace'])
            features.append(copy.deepcopy(REGISTRY['exitApproach']))
            props['route_node_id'] = 'n_o_1f_b2_radiology'
        result.append(SimpleNamespace(floor_key=floor.floor_key, version=floor.version, geojson=geojson))
    return result


def guidance_waypoints(floors, target_props, department_id):
    """guidanceFor binds to a target POI id or deptID within the same hospital."""
    identifiers = {department_id, target_props.get('id')}
    points = []
    for floor in floors:
        for feature in floor.geojson.get('features', []):
            props, geometry = feature.get('properties') or {}, feature.get('geometry') or {}
            bindings = props.get('guidanceFor', [])
            if not isinstance(bindings, list) or not any(isinstance(b, str) and b in identifiers for b in bindings):
                continue
            # An unavailable mandatory stop invalidates the route, never silently
            # bypass it and advertise an incomplete path as successful.
            if geometry.get('type') != 'Point' or not enabled(props) or not (props.get('route_node_id') or props.get('routeNodeId')):
                return None
            order = props.get('guidanceOrder', 0)
            if not isinstance(order, int) or isinstance(order, bool):
                return None
            points.append((order, floor.floor_key, str(props.get('id') or feature.get('id') or ''),
                           (floor, geometry.get('coordinates'), props, props.get('name') or '导诊点')))
            if len(points) > 20:
                return None
    return [p[3] for p in sorted(points, key=lambda p: p[:3])]
