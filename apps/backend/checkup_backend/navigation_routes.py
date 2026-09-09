"""Route over declared door/corridor nodes, retaining every floor transition."""
import heapq
import json
import math
from pathlib import Path
from .navigation_coordinates import is_rectified

LEGACY_PIXEL_NODES = json.loads((Path(__file__).parent / 'data/legacy_pixel_nodes.json').read_text(encoding='utf-8'))


def coordinate(value):
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value[:2]):
        return None
    return list(value[:2])


def enabled(props):
    return props.get('routing_status', 'enabled') in ('enabled', 'draft_enabled', 'verified') and props.get('access_control') not in ('closed', 'restricted', 'no')


def route_via(floors, source, target, waypoints):
    """Join complete legs; keep a single continuous polyline on each floor visit."""
    if waypoints is None:
        return None
    endpoints = [source, *waypoints, target]
    result = {'segments': [], 'walkSeconds': 0, 'horizontalDistanceMeters': 0}
    for index, (start, end) in enumerate(zip(endpoints, endpoints[1:])):
        leg = route_segments(floors, start, end)
        if leg is None:
            return None
        segments = leg['segments']
        if result['segments']:
            previous, following = result['segments'][-1], segments[0]
            if previous['floorKey'] != following['floorKey'] or previous['routeCoordinates'][-1] != following['routeCoordinates'][0]:
                return None
            previous.setdefault('waypoints', []).append({**previous['toPoint'], 'order': index})
            previous['routeCoordinates'].extend(following['routeCoordinates'][1:])
            previous['toPoint'] = following['toPoint']
            previous['transition'] = following['transition']
            segments = segments[1:]
        result['segments'].extend(segments)
        result['walkSeconds'] += leg['walkSeconds']
        result['horizontalDistanceMeters'] += leg['horizontalDistanceMeters']
    for index, segment in enumerate(result['segments']):
        segment['segmentID'] = str(index)
        labels = [segment['fromPoint']['name'], *['途经：' + p['name'] for p in segment.get('waypoints', [])], segment['toPoint']['name']]
        segment['instruction'] = ' → '.join(labels)
    if waypoints:
        result['guidanceNotice'] = '请先依次前往' + '、'.join(p[3] for p in waypoints) + '，再前往目标科室。'
    return result


def route_segments(floors, source, target):
    """Endpoints are (floor record, POI coordinates, POI properties, display name)."""
    graph, nodes, labels = {}, {}, {}
    floor_by_key = {f.floor_key: f for f in floors}

    def add(a, b, points, seconds, length, transition=None):
        graph.setdefault(a, []).append((b, points, seconds, length, transition))

    for floor in floors:
        for feature in floor.geojson.get('features', []):
            p, g = feature.get('properties') or {}, feature.get('geometry') or {}
            node_id = p.get('route_node_id') or p.get('routeNodeId')
            if g.get('type') == 'Point' and isinstance(node_id, str) and node_id:
                labels[(floor.floor_key, node_id)] = p.get('name') or '楼梯'
            if g.get('type') != 'LineString' or p.get('featureType') not in ('corridor', 'route') or not enabled(p):
                continue
            points = [coordinate(v) for v in g.get('coordinates', [])]
            if len(points) < 2 or any(v is None for v in points) or not all(isinstance(p.get(k), str) and p[k] for k in ('source','target')):
                continue
            a, b = (floor.floor_key, p['source']), (floor.floor_key, p['target'])
            if any(k in nodes and nodes[k] != v for k, v in ((a, points[0]), (b, points[-1]))):
                continue
            length = p.get('length_m', p.get('distanceMeters'))
            seconds = p.get('walk_seconds')
            if not isinstance(length, (int, float)) or not math.isfinite(length) or length < 0:
                continue
            if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
                seconds = length / 1.15
            nodes[a], nodes[b] = points[0], points[-1]
            if p.get('direction') not in ('reverse', 'backward'):
                add(a, b, points, seconds, length)
            if p.get('direction', 'bidirectional') in ('bidirectional', 'reverse', 'backward'):
                add(b, a, points[::-1], seconds, length)

    connections = []
    declared = any('verticalConnections' in f.geojson for f in floors)
    if declared:
        for floor in floors:
            items = floor.geojson.get('verticalConnections')
            if isinstance(items, list):
                connections.extend(items)
    else:
        # Compatibility for the original GIS-only export, which omitted these
        # connectors. Both endpoint IDs and packaged source coordinates must match.
        connections = json.loads((Path(__file__).parent / 'data/legacy_gis_connections.json').read_text(encoding='utf-8'))
    seen = set()
    for c in connections:
        if not isinstance(c, dict) or not enabled(c) or c.get('mode') != 'stairs':
            continue
        endpoints = []
        for side in ('from', 'to'):
            matches = [k for k in nodes if k[1] == c.get(side + '_node_id') and (not c.get(side + '_floor') or k[0] == c[side + '_floor'])]
            expected = coordinate(c.get(side + '_coordinates'))
            if not declared and len(matches) == 1 and is_rectified(floor_by_key[matches[0][0]].geojson):
                legacy_node = LEGACY_PIXEL_NODES.get(c.get(side + '_node_id'), {})
                if expected != legacy_node.get('source_coordinates'):
                    break
                expected = coordinate(legacy_node.get('coordinates'))
            if len(matches) != 1 or (not declared and expected is None):
                break
            if expected is not None and any(abs(x-y) > 1e-9 for x, y in zip(nodes[matches[0]], expected)):
                break
            endpoints.append(matches[0])
        if len(endpoints) != 2 or endpoints[0][0] == endpoints[1][0]:
            continue
        a, b = endpoints
        if (a, b) in seen:
            continue
        seen.add((a, b))
        seconds = c.get('walk_seconds')
        if not isinstance(seconds, (float, int)) or not math.isfinite(seconds) or seconds <= 0:
            continue
        if c.get('direction') not in ('reverse', 'backward'):
            add(a, b, [], seconds, 0, c)
        if c.get('direction', 'bidirectional') in ('bidirectional', 'reverse', 'backward'):
            add(b, a, [], seconds, 0, c)

    def anchor(endpoint):
        floor, point, props, _name = endpoint
        if not enabled(props):
            return None
        node_id = props.get('route_node_id') or props.get('routeNodeId')
        if node_id:
            if not isinstance(node_id, str):
                return None
            key = (floor.floor_key, node_id)
            return key if key in nodes else None
        matches = [k for k, p in nodes.items() if k[0] == floor.floor_key and coordinate(point) == p]
        return matches[0] if len(matches) == 1 else None

    start, end = anchor(source), anchor(target)
    if start is None or end is None:
        return None
    costs, previous, queue = {start: 0}, {}, [(0, start)]
    while queue:
        cost, current = heapq.heappop(queue)
        if cost != costs[current]:
            continue
        if current == end:
            break
        for edge in graph.get(current, []):
            dest, _points, seconds, _length, _transition = edge
            candidate = cost + seconds
            if candidate < costs.get(dest, math.inf):
                costs[dest], previous[dest] = candidate, (current, edge)
                heapq.heappush(queue, (candidate, dest))
    if end not in costs:
        return None
    edges, current = [], end
    while current != start:
        origin, edge = previous[current]
        edges.append((origin, edge))
        current = origin
    edges.reverse()

    def marker(key, name):
        return {'coordinates': nodes[key], 'floorKey': key[0], 'name': name}

    def segment(key, name):
        floor = floor_by_key[key[0]]
        # Drawing needs geometry, not pixel-registration/audit metadata.
        features = [{'type': 'Feature', 'geometry': f.get('geometry'), 'properties': {'featureType': (f.get('properties') or {}).get('featureType')}} for f in floor.geojson.get('features', [])]
        return {'floorKey': key[0], 'version': floor.version, 'geojson': {'type': 'FeatureCollection', 'features': features},
                'fromPoint': marker(key, name), 'toPoint': None, 'routeCoordinates': [nodes[key]], 'transition': ''}

    segments = [segment(start, source[3])]
    length = 0
    for origin, (dest, points, _seconds, edge_length, transition) in edges:
        length += edge_length
        if transition:
            segments[-1]['toPoint'] = marker(origin, labels.get(origin, '楼梯'))
            segments[-1]['transition'] = f"经{labels.get(origin, '楼梯')}前往 {dest[0]}，从{labels.get(dest, '楼梯')}继续"
            segments.append(segment(dest, labels.get(dest, '楼梯')))
        else:
            segments[-1]['routeCoordinates'].extend(points[1:])
    segments[-1]['toPoint'] = marker(end, target[3])
    for i, s in enumerate(segments):
        s['segmentID'] = str(i)
        s['instruction'] = f"{s['fromPoint']['name']} → {s['toPoint']['name']}"
    return {'segments': segments, 'walkSeconds': costs[end], 'horizontalDistanceMeters': round(length)}
