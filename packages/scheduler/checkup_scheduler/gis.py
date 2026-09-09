"""File adapters for evidenced GIS data; scheduling algorithms stay independent.

TravelTimeMatrix has a numeric fallback, not an unreachable sentinel. Callers
must explicitly choose that fallback and can require full location coverage.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from collections.abc import Iterable

from .models import TravelTimeMatrix


def load_travel_time_matrix_csv(
    path: str | Path, *, default_minutes: int,
    required_locations: Iterable[str] = (),
) -> TravelTimeMatrix:
    """Load integer minutes, refusing conflicting, invalid or incomplete rows.

``default_minutes`` is caller policy, never a GIS-derived claim. Specifying
``required_locations`` rejects any missing directed pair before scheduling.
Retain the original CSV/JSON for node/edge, evidence and build-hash provenance.
"""
    if isinstance(default_minutes, bool) or not isinstance(default_minutes,int) or default_minutes < 0:
        raise ValueError('default_minutes must be an explicit nonnegative integer')
    edges: dict[tuple[str,str],int]={}
    with Path(path).open(encoding='utf-8-sig',newline='') as handle:
        reader=csv.DictReader(handle)
        missing={'origin','destination','travel_minutes'}-set(reader.fieldnames or ())
        if missing: raise ValueError('Missing travel matrix columns: '+', '.join(sorted(missing)))
        for n,row in enumerate(reader,2):
            a,b=((row.get(k) or '').strip() for k in ('origin','destination'))
            if not a or not b: raise ValueError(f'Row {n}: empty location')
            try: minutes=int(row['travel_minutes'])
            except (ValueError,TypeError) as e: raise ValueError(f'Row {n}: invalid minutes') from e
            if minutes<0 or (a==b and minutes!=0): raise ValueError(f'Row {n}: invalid minutes')
            if row.get('status') and row['status'] not in ('navigation_draft_not_survey','verified'):
                raise ValueError(f'Row {n}: excluded/unverified mapping status')
            if (a,b) in edges and edges[a,b]!=minutes: raise ValueError(f'Row {n}: conflicting pair {a}->{b}')
            if a!=b: edges[a,b]=minutes
    required=set(required_locations)
    missing_locations=required-{location for pair in edges for location in pair}
    if missing_locations: raise ValueError('GIS location missing: '+str(sorted(missing_locations)))
    missing_pairs={(a,b) for a in required for b in required if a!=b}-edges.keys()
    if missing_pairs: raise ValueError('GIS coverage missing: '+str(sorted(missing_pairs)))
    return TravelTimeMatrix(edges,default_minutes=default_minutes)


def attach_gis_to_workspace(workspace: dict, generated_directory: str | Path) -> dict:
    """Return an importable copy with evidenced GIS points and same-floor routes.

Uses only declared keys. Does not alter hospital, department, exam or package
attributes. Unknown locations remain POIs; no nearest-room substitution.
"""
    directory=Path(generated_directory)
    result=json.loads(json.dumps(workspace))
    generated=json.loads((directory/'workspace_gis_only.json').read_text(encoding='utf-8'))
    metadata=json.loads((directory/'metadata.json').read_text(encoding='utf-8'))
    declared={d['key'] for d in result.get('departments',[])}
    mappings={m['poi_id']:m for m in metadata['walking_model']['mappings'] if m['department_key'] in declared}
    nodes={}
    for floor in ('1f','2f','3f'):
        collection=json.loads((directory/f'{floor}_route_nodes.geojson').read_text(encoding='utf-8'))
        nodes.update({n['id']:n for n in collection['features']})
    result['gis']=generated['gis']
    for floor in result['gis']:
        for feature in floor['geojson']['features']:
            if feature['id'] not in mappings: continue
            m=mappings[feature['id']]
            feature['properties'].update(featureType='department',departmentKey=m['department_key'],
                                         poiGeometry=feature['geometry'],routeNodeId=m['route_node_id'])
            feature['geometry']=nodes[m['route_node_id']]['geometry']
    rows=json.loads((directory/'scheduler_travel_times.json').read_text(encoding='utf-8'))['rows']
    for row in rows:
        if row['origin'] not in declared or row['destination'] not in declared: continue
        route_nodes=[nodes[n] for n in row['node_ids']]
        floors={n['properties']['floor'] for n in route_nodes}
        if len(floors)!=1:
            # Current backend only renders same-floor routes. Do not flatten a
            # staircase into a same-floor shortcut or mislabel time as metres.
            continue
        floor=next(f for f in result['gis'] if f['floorKey']==next(iter(floors)))
        floor['geojson']['features'].append(dict(type='Feature',id='route_'+row['origin']+'_'+row['destination'],
            properties=dict(featureType='route',fromDepartmentKey=row['origin'],toDepartmentKey=row['destination'],
                            distanceMeters=row['distance_m'],walkSeconds=row['walk_seconds'],
                            status=row['status'],sourceBundleSha256=row['source_bundle_sha256'],edgeIds=row['edge_ids']),
            geometry=dict(type='LineString',coordinates=[n['geometry']['coordinates'] for n in route_nodes])))
    return result
