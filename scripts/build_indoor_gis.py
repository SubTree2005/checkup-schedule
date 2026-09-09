"""Build all floors from audited raster-pixel JSON. No generated-file inputs.

Run from any directory: python scripts/build_indoor_gis.py [--output DIR].
All vector topology is checked in original pixels and distances use UTM 51N.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import heapq
import html
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from pyproj import Transformer
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, mapping, shape
from shapely.geometry.polygon import orient
from shapely.ops import transform, unary_union

from gis_coordinates import bd09_to_gcj02, gcj02_to_wgs84, wgs84_to_gcj02

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'gis/source'
FACILITY = 'zju2_zijingang'
STATUS = 'navigation_draft_not_survey'
TO_METRIC = Transformer.from_crs(4326, 32651, always_xy=True)
TO_WGS = Transformer.from_crs(32651, 4326, always_xy=True)
TABLE_ORDER = ['facility', 'building', 'level', 'source_document', 'space',
               'path_node', 'opening', 'path_edge', 'poi', 'vertical_connection']


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def write_csv(path, rows, fields):
    with Path(path).open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(row[k], ensure_ascii=False, separators=(',', ':')) if isinstance(row.get(k), (dict, list)) else row.get(k) for k in fields})


def fit(source, target, method='affine'):
    """Centred least squares avoids large-coordinate normal equations."""
    src, dst = np.array(source, float), np.array(target, float)
    sm, tm = src.mean(axis=0), dst.mean(axis=0)
    if method == 'affine':
        linear, _, rank, _ = np.linalg.lstsq(src-sm, dst-tm, rcond=None)
        if rank != 2:
            raise ValueError('Registration anchors are collinear')
    else:
        x, y = (src-sm).T
        u, v = (dst-tm).T
        den = float(x@x+y@y)
        if den < 1e-8:
            raise ValueError('Coincident registration anchors')
        a, b = (x@u+y@v)/den, (x@v-y@u)/den
        linear = np.array([[a,b],[-b,a]])
    matrix = np.vstack([linear, tm-sm@linear])
    return matrix


def apply(matrix, pixel):
    return np.append(pixel, 1.) @ matrix


def diagnostics(src, dst, matrix):
    errors = [float(np.linalg.norm(apply(matrix,s)-t)) for s,t in zip(src,dst)]
    loo = []
    if len(src) >= 4:
        for i in range(len(src)):
            remaining = [j for j in range(len(src)) if i != j]
            try:
                fitted = fit([src[j] for j in remaining], [dst[j] for j in remaining])
                loo.append(float(np.linalg.norm(apply(fitted,src[i])-dst[i])))
            except ValueError:
                loo.append(None)
    singular = np.linalg.svd(matrix[:2], compute_uv=False)
    return dict(matrix=matrix.tolist(), residuals=errors,
                fit_rmse=float(np.sqrt(np.mean(np.square(errors)))),
                max_residual=max(errors), leave_one_out_residuals=loo,
                axis_scale_ratio=float(max(singular)/min(singular)))


def registration(source):
    spec = read(source/'registration.json')
    raw = spec['control_points']
    rows = []
    for c in raw:
        gcj = bd09_to_gcj02(*c['bd09ll'])
        wgs = gcj02_to_wgs84(*gcj)
        utm = TO_METRIC.transform(*wgs)
        rows.append({**c, 'gcj02': list(gcj), 'wgs84_approx': wgs, 'utm51n_m': list(utm)})
    src, dst = [c['pixel_1f'] for c in rows], [c['utm51n_m'] for c in rows]
    base = fit(src, dst)
    transforms = {'1F': base}
    report = {'1F': {**diagnostics(src,dst,base), 'residual_unit':'metres', 'confidence':'low',
                     'absolute_accuracy_m':None, 'annotation_transfer':spec['annotation_transfer'],
                     'fit_is_not_independent_accuracy':True}}
    for i, c in enumerate(rows):
        c['fitted_utm51n_m'] = apply(base,c['pixel_1f']).tolist()
        c['residual_m'] = report['1F']['residuals'][i]
        c['leave_one_out_m'] = report['1F']['leave_one_out_residuals'][i]
    for floor, reg in spec['upper_floors'].items():
        target = reg['target_floor']
        src = [a['source'] for a in reg['anchors']]
        dst = [a['target'] for a in reg['anchors']]
        local = fit(src,dst,reg['method'])
        augmented = np.column_stack([local, [0,0,1]])
        transforms[floor] = augmented @ transforms[target]
        checks = []
        for c in reg['checks']:
            predicted_px = apply(local,c['source'])
            delta = apply(transforms[target],predicted_px)-apply(transforms[target],c['target'])
            checks.append({**c, 'predicted_target_px':predicted_px.tolist(), 'residual_m':float(np.linalg.norm(delta))})
        similarity = fit(src,dst,'similarity')
        report[floor] = {**reg, **diagnostics(src,dst,local), 'residual_unit':'target_floor_pixels',
                         'similarity_comparison':diagnostics(src,dst,similarity),
                         'checks':checks, 'check_rmse_m':float(np.sqrt(np.mean([c['residual_m']**2 for c in checks]))),
                         'pixel_to_utm51n_matrix':transforms[floor].tolist(),
                         'absolute_accuracy_m':None, 'absolute_confidence':'low',
                         'uncertainty_note':'inherits missing control annotation and approximate CRS conversion; semantic stair checks are not surveyed checkpoints'}
    return transforms, report, rows


def level_id(building, floor):
    return f'{FACILITY}_{building or "shared"}_{floor.lower()}'


def building_id(building):
    return f'{FACILITY}_{building}' if building else None


def feature(identifier, geom, props, pixel=None):
    if geom.geom_type=='Polygon': geom=orient(geom,sign=1.)
    elif geom.geom_type=='MultiPolygon': geom=MultiPolygon([orient(p,sign=1.) for p in geom.geoms])
    return dict(type='Feature', id=identifier,
                properties={'id':identifier,'status':STATUS, **props, **({'geometry_px':mapping(pixel)} if pixel is not None else {})},
                geometry=mapping(geom))


def metric_geom(geom, matrix):
    def convert(x,y,z=None):
        return matrix[0,0]*np.asarray(x)+matrix[1,0]*np.asarray(y)+matrix[2,0], matrix[0,1]*np.asarray(x)+matrix[1,1]*np.asarray(y)+matrix[2,1]
    return transform(convert, geom)


def geographic(geom, matrix):
    return transform(TO_WGS.transform, metric_geom(geom,matrix))


def point_key(p):
    return tuple(round(float(x),6) for x in p)


def components(node_ids, edges):
    adj = {n:set() for n in node_ids}
    for a,b in edges:
        adj[a].add(b); adj[b].add(a)
    groups=[]
    todo=set(adj)
    while todo:
        start=min(todo); seen=set(); stack=[start]
        while stack:
            n=stack.pop()
            if n in seen: continue
            seen.add(n); stack.extend(adj[n]-seen)
        todo-=seen; groups.append(sorted(seen))
    return groups


def build_floor(spec, matrix, walking_speed_mps=1.15):
    floor=spec['floor']
    for name in ('spaces','openings','centerlines'):
        identifiers=[s['id'] for s in spec[name]]
        if len(identifiers)!=len(set(identifiers)): raise ValueError(f'{floor}: duplicate {name} ID')
    room_keys=[(s['building_id'],s['room_ref']) for s in spec['spaces'] if s['room_ref']]
    if any(b is None for b,r in room_keys) or len(room_keys)!=len(set(room_keys)):
        raise ValueError(f'{floor}: ambiguous building + level + room_ref')
    layers={k:[] for k in ('level','spaces','openings','route_nodes','route_edges','pois')}
    spaces={s['id']:s for s in spec['spaces']}
    polygons={s['id']:Polygon(s['polygon_px']) for s in spec['spaces']}
    errors=[]
    for key, poly in polygons.items():
        if not poly.is_valid or poly.is_empty or poly.area <= 0:
            errors.append(f'{key}: invalid polygon')
    occupied = [(k,g) for k,g in polygons.items() if spaces[k]['use_type'] not in ('corridor','lobby','entrance','void')]
    for i,(a,pa) in enumerate(occupied):
        for b,pb in occupied[i+1:]:
            if pa.intersection(pb).area > 1:
                errors.append(f'{a} overlaps {b}: {pa.intersection(pb).area:.1f}px2')
    # Passages are separately traced envelopes minus visible occupied interiors.
    passage_union=unary_union([g for k,g in polygons.items() if spaces[k]['use_type'] in ('corridor','lobby','entrance')])
    blocked=unary_union([g for _,g in occupied]+[g for k,g in polygons.items() if spaces[k]['use_type']=='void'])
    free=passage_union.difference(blocked)
    levels=[]
    for l in spec['levels']:
        poly=Polygon(l['polygon_px'],l['holes_px'])
        if not poly.is_valid: errors.append(f'{floor} {l["building_id"]}: invalid level')
        levels.append((l['building_id'],poly))
    # Shared circulation gets its own level, with explicit unknown building.
    shared=unary_union([g for k,g in polygons.items() if spaces[k]['building_id'] is None])
    if not shared.is_empty:
        levels=[(b,g) for b,g in levels if b is not None]+[(None,shared)]
    for b,poly in levels:
        layers['level'].append(feature(level_id(b,floor),geographic(poly,matrix),
            dict(level_id=level_id(b,floor),facility_id=FACILITY,building_id=building_id(b),name=floor,
                 ordinal=int(floor[0]),height_m=None,geometry_role='generalized_occupied_floor_extent_not_wall_survey',
                 absolute_accuracy_m=None,building_confidence='medium' if floor=='1F' else 'high'),poly))
    def building_at(point):
        for b,poly in levels:
            if b and poly.buffer(.1).covers(Point(point)): return b
        return None
    for k,s in spaces.items():
        poly=polygons[k]
        if s['use_type'] in ('corridor','lobby','entrance'):
            poly=poly.difference(blocked)
        props={key:value for key,value in s.items() if key != 'polygon_px'}
        props.update(level_id=level_id(s['building_id'],floor),building_id=building_id(s['building_id']),floor=floor)
        layers['spaces'].append(feature(k,geographic(poly,matrix),props,poly))
    opening_nodes={}
    opening_by_space={}
    lines=[LineString(c['path_px']) for c in spec['centerlines']]
    source_centerlines=unary_union(lines)
    for o in spec['openings']:
        poly=polygons[o['space_id']]
        raw=[Point(p) for p in o['segment_px']]
        snapped=[poly.boundary.interpolate(poly.boundary.project(p)) for p in raw]
        snap=max(p.distance(q) for p,q in zip(raw,snapped))
        if snap>3: errors.append(f'{o["id"]}: opening off boundary by {snap:.2f}px')
        seg=LineString([p.coords[0] for p in snapped])
        mid=seg.interpolate(.5,normalized=True)
        if mid.distance(poly.boundary)>0.01: errors.append(f'{o["id"]}: opening straddles corner')
        approach=list(o['approach_px']); approach[-1]=list(mid.coords[0])
        if source_centerlines.distance(Point(approach[0]))>0.01:
            errors.append(f'{o["id"]}: approach start is not on authored centerline')
        lines.append(LineString(approach))
        node='n_'+o['id']
        opening_nodes[point_key(mid.coords[0])]=(node,o)
        opening_by_space[o['space_id']]=node
        s=spaces[o['space_id']]
        props={k:v for k,v in o.items() if k not in ('segment_px','approach_px','status')}
        props['observation_status']=o['status']
        props.update(level_id=level_id(s['building_id'],floor),building_id=building_id(s['building_id']),
                     route_node_id=node,floor=floor,snap_distance_px=snap,raw_segment_px=o['segment_px'])
        layers['openings'].append(feature(o['id'],geographic(seg,matrix),props,seg))
    noded=unary_union(lines)
    parts=list(noded.geoms) if hasattr(noded,'geoms') else [noded]
    segments=[]
    for part in parts:
        for a,b in zip(part.coords, list(part.coords)[1:]):
            if Point(a).distance(Point(b)) > 1e-5: segments.append((point_key(a),point_key(b)))
    nodes={}
    def node_for(p):
        if p in nodes: return nodes[p]
        o=opening_nodes.get(p)
        b=spaces[o[1]['space_id']]['building_id'] if o else building_at(p)
        node=o[0] if o else f'n_{floor.lower()}_'+('_'.join(f'{v:g}'.replace('.','p') for v in p))
        kind='stair_landing' if o and o[1]['kind']=='stair_gate' else 'entrance' if o and spaces[o[1]['space_id']]['use_type']=='entrance' else 'door' if o else 'junction'
        nodes[p]=node
        layers['route_nodes'].append(feature(node,geographic(Point(p),matrix),
            dict(level_id=level_id(b,floor),building_id=building_id(b),floor=floor,kind=kind,name=node,
                 accessible='no' if kind=='stair_landing' else 'unknown',access_control='unknown'),Point(p)))
        return node
    # A two-pixel raster tolerance is stated explicitly; room interiors remain prohibited.
    for i,(a,b) in enumerate(sorted(set(tuple(sorted(s)) for s in segments))):
        line=LineString([a,b]); source_node,target_node=node_for(a),node_for(b)
        if not free.buffer(2).covers(line):
            errors.append(f'{floor} path {a}->{b} leaves traced patient passage')
        for sid,room_poly in occupied:
            if line.intersection(room_poly.buffer(-0.3)).length > .1:
                errors.append(f'{floor} path {a}->{b} crosses {sid}')
        ba,bb=building_at(a),building_at(b)
        length=metric_geom(line,matrix).length
        eid=f'e_{floor.lower()}_{i:04d}'
        layers['route_edges'].append(feature(eid,geographic(line,matrix),
            dict(level_id=level_id(ba if ba==bb else None,floor),floor=floor,source=source_node,target=target_node,
                 length_m=length,walk_seconds=length/walking_speed_mps,accessible='unknown',access_control='unknown',
                 direction='bidirectional',routing_status='draft_enabled',evidence_ids=[f'plan_{floor.lower()}'],
                 cost_status='assumed_not_measured',walking_speed_mps=walking_speed_mps),line))
    for k,s in spaces.items():
        if s['use_type'] in ('corridor','lobby','void'): continue
        point=polygons[k].representative_point()
        layers['pois'].append(feature('poi_'+k,geographic(point,matrix),
            dict(space_id=k,level_id=level_id(s['building_id'],floor),building_id=building_id(s['building_id']),
                 floor=floor,name=s['name'],category=s['use_type'],room_ref=s['room_ref'],
                 route_node_id=opening_by_space.get(k),scheduler_location_id=None,confidence=s['label_confidence'],
                 accessible='unknown',access_control='unknown',evidence_ids=s['evidence_ids']),point))
    groups=components([n['id'] for n in layers['route_nodes']],[(e['properties']['source'],e['properties']['target']) for e in layers['route_edges']])
    if len(groups)!=1: errors.append(f'{floor}: {len(groups)} same-floor graph components {[(len(g),g[0]) for g in groups]}')
    return layers, dict(floor=floor,components=groups,errors=errors,
                        passage_tolerance_px=2,room_intrusion_tolerance_px=.3,
                        unresolved=spec['unresolved'],unrouted_pois=[p['id'] for p in layers['pois'] if p['properties']['route_node_id'] is None])


def shortest_routes(nodes, edges, vertical, mappings):
    adjacency=defaultdict(list)
    for edge in edges:
        p=edge['properties']; a,b=p['source'],p['target']
        for s,t in [(a,b),(b,a)]: adjacency[s].append((t,p['walk_seconds'],edge['id'],p['length_m']))
    for c in vertical:
        if c['routing_status'] != 'draft_enabled': continue
        for s,t in [(c['from_node_id'],c['to_node_id']),(c['to_node_id'],c['from_node_id'])]:
            adjacency[s].append((t,c['walk_seconds'],c['id'],0.))
    output=[]
    for origin in sorted(mappings,key=lambda m:m['location_id']):
        start=origin['route_node_id']; dist={start:0.}; prev={}; queue=[(0.,start)]
        while queue:
            cost,n=heapq.heappop(queue)
            if cost != dist[n]: continue
            for t,w,eid,length in sorted(adjacency[n]):
                new=cost+w
                if new < dist.get(t,math.inf):
                    dist[t]=new; prev[t]=(n,eid,length); heapq.heappush(queue,(new,t))
        for dest in sorted(mappings,key=lambda m:m['location_id']):
            if dest['location_id']==origin['location_id']: continue
            end=dest['route_node_id']
            if end not in dist: raise ValueError('Mapped department unreachable: '+dest['location_id'])
            path_nodes=[end]; path_edges=[]; length=0.
            while path_nodes[-1]!=start:
                p,e,l=prev[path_nodes[-1]]; path_edges.append(e); path_nodes.append(p); length+=l
            path_nodes.reverse(); path_edges.reverse()
            output.append(dict(origin=origin['location_id'],destination=dest['location_id'],walk_seconds=dist[end],
                               travel_minutes=math.ceil(dist[end]/60),distance_m=length,
                               origin_poi_id=origin['poi_id'],destination_poi_id=dest['poi_id'],
                               node_ids=path_nodes,edge_ids=path_edges,profile='ambulatory_draft',
                               mapping_evidence_ids=sorted(set(origin['evidence_ids']+dest['evidence_ids'])),
                               status=STATUS))
    return output


def svg_preview(spec,layers,source,output):
    with Image.open(source/spec['image']) as raw:
        im=raw.convert('RGB')
    w,h=im.size
    pieces=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}">',
            f'<image href="../source/{spec["image"]}" width="{w}" height="{h}"/>']
    for layer,color in [('spaces','#168ab7'),('route_edges','#d200a8'),('openings','#ff7900'),('route_nodes','#b400a0')]:
        pieces.append(f'<g id="{layer}">')
        for feat in layers[layer]:
            g=shape(feat['properties']['geometry_px']); title=html.escape(feat['id']+' '+feat['properties'].get('name',''))
            if g.is_empty: continue
            parts=list(g.geoms) if hasattr(g,'geoms') else [g]
            for p in parts:
                if p.geom_type=='Polygon':
                    coords=' '.join(f'{x},{y}' for x,y in p.exterior.coords)
                    pieces.append(f'<polygon points="{coords}" fill="{color}" fill-opacity=".055" stroke="{color}" stroke-width=".7"><title>{title}</title></polygon>')
                elif p.geom_type=='LineString':
                    coords=' '.join(f'{x},{y}' for x,y in p.coords)
                    pieces.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="{2 if layer=="openings" else 1.2}"><title>{title}</title></polyline>')
                elif p.geom_type=='Point':
                    pieces.append(f'<circle cx="{p.x}" cy="{p.y}" r="1.5" fill="{color}"><title>{title}</title></circle>')
        pieces.append('</g>')
    pieces.append('<g id="labels" font-family="sans-serif" font-size="8" fill="#006087" stroke="white" stroke-width="2" paint-order="stroke">')
    for i,p in enumerate(layers['pois'],1):
        g=shape(p['properties']['geometry_px']); ref=p['properties']['room_ref'] or f'P{i}'
        if spec['floor']=='2F' and p['properties']['room_ref']: ref=p['properties']['building_id'].rsplit('_',1)[1].upper()+'-'+ref
        if not p['properties']['route_node_id']: ref='?'+ref
        color='#006087' if p['properties']['route_node_id'] else '#a32900'
        pieces.append(f'<text x="{g.x-8}" y="{g.y-8}" fill="{color}">{html.escape(ref)}<title>{html.escape(p["id"])}</title></text>')
    pieces.append('</g></svg>')
    (output/f'{spec["floor"].lower()}_overlay.svg').write_text('\n'.join(pieces),encoding='utf-8')
    # Raster preview is drawn from the very same pixel geometries, not rescaled GPS.
    preview=im.convert('RGB').resize((w*2,h*2))
    d=ImageDraw.Draw(preview)
    for layer,color in [('spaces','#168ab7'),('route_edges','#d200a8'),('openings','#f07800')]:
        for feat in layers[layer]:
            geom=shape(feat['properties']['geometry_px'])
            for g in list(geom.geoms) if hasattr(geom,'geoms') else [geom]:
                coords=list(g.exterior.coords) if g.geom_type=='Polygon' else list(g.coords) if g.geom_type=='LineString' else []
                if coords: d.line([(x*2,y*2) for x,y in coords],fill=color,width=2 if layer=='spaces' else 3)
    for p in layers['route_nodes']:
        x,y=shape(p['properties']['geometry_px']).coords[0]
        d.ellipse((x*2-2,y*2-2,x*2+2,y*2+2),fill='#b00090')
    for i,p in enumerate(layers['pois'],1):
        g=shape(p['properties']['geometry_px']); text=p['properties']['room_ref'] or f'P{i}'
        if spec['floor']=='2F' and p['properties']['room_ref']: text=p['properties']['building_id'].rsplit('_',1)[1].upper()+'-'+text
        if not p['properties']['route_node_id']: text='?'+text
        d.text((g.x*2-16,g.y*2-19),text,fill='#004c89' if p['properties']['route_node_id'] else '#a32900',stroke_width=1,stroke_fill='white')
    preview.save(output/f'{spec["floor"].lower()}_overlay.png')
    write_csv(output/f'{spec["floor"].lower()}_feature_index.csv',
        [dict(preview_label=p['properties']['room_ref'] or f'P{i}',poi_id=p['id'],
              building_id=p['properties']['building_id'],level_id=p['properties']['level_id'],
              name=p['properties']['name'],route_node_id=p['properties']['route_node_id'],
              access_control=p['properties']['access_control']) for i,p in enumerate(layers['pois'],1)],
        ['preview_label','poi_id','building_id','level_id','name','route_node_id','access_control'])


def sql_value(value):
    if value is None: return 'NULL'
    if isinstance(value,bool): return 'TRUE' if value else 'FALSE'
    if isinstance(value,(int,float)): return str(value)
    if isinstance(value,(list,dict)):
        return "'"+json.dumps(value,ensure_ascii=False,separators=(',',':')).replace("'","''")+"'::jsonb"
    return "'"+str(value).replace("'","''")+"'"


def seed_sql(layers_by_floor,vertical,evidence,metadata):
    tables={k:[] for k in TABLE_ORDER}
    tables['facility']=[dict(facility_id=FACILITY,name=evidence['facility_name'],status=STATUS)]
    tables['building']=[dict(building_id=building_id(b),facility_id=FACILITY,name=name) for b,name in [('b1','一号楼'),('b2','二号楼')]]
    for floor,layers in layers_by_floor.items():
        for feat in layers['level']:
            p=feat['properties']
            tables['level'].append(dict(level_id=feat['id'],facility_id=FACILITY,building_id=p['building_id'],name=floor,ordinal=int(floor[0]),height_m=None,status=STATUS,accuracy_m=None,metadata=p,geom=feat['geometry']))
    for e in evidence['evidence']:
        tables['source_document'].append(dict(evidence_id=FACILITY+':'+e['id'],facility_id=FACILITY,source_uri=e.get('original_path'),sha256=e.get('sha256'),status=e['status'],metadata=e))
    tables['source_document'].append(dict(evidence_id=FACILITY+':registration',facility_id=FACILITY,
        source_uri='gis/source/registration.json',sha256=metadata['source_files_sha256']['registration.json'],
        status=STATUS,metadata=metadata))
    for floor,layers in layers_by_floor.items():
        for layer,table,key in [('spaces','space','space_id'),('route_nodes','path_node','node_id'),('openings','opening','opening_id'),('route_edges','path_edge','edge_id'),('pois','poi','poi_id')]:
            for f in layers[layer]:
                p=f['properties']; row={key:f['id'],'level_id':p['level_id']}
                if table != 'path_edge': row['building_id']=p['building_id']
                if table=='space':
                    row.update(name=p['name'],use_type=p['use_type'],room_ref=p['room_ref'],confidence=p['geometry_confidence'])
                elif table=='path_node': row.update(name=p['name'],kind=p['kind'])
                elif table=='opening': row.update(space_id=p['space_id'],route_node_id=p['route_node_id'],kind=p['kind'],confidence=p['confidence'])
                elif table=='path_edge': row.update(source_node_id=p['source'],target_node_id=p['target'],length_m=p['length_m'],walk_seconds=p['walk_seconds'],routing_status=p['routing_status'])
                elif table=='poi': row.update(space_id=p['space_id'],route_node_id=p['route_node_id'],name=p['name'],category=p['category'],confidence=p['confidence'],scheduler_location_id=p['scheduler_location_id'])
                row.update(accessible=p.get('accessible','unknown'),access_control=p.get('access_control','unknown'),metadata=p,geom=f['geometry'])
                tables[table].append(row)
    tables['vertical_connection']=[dict(connector_id=c['id'],from_node_id=c['from_node_id'],to_node_id=c['to_node_id'],mode=c['mode'],walk_seconds=c['walk_seconds'],accessible=c['accessible'],access_control=c['access_control'],confidence=c['confidence'],routing_status=c['routing_status'],metadata=c) for c in vertical]
    lines=['-- Navigation draft, not survey. Generated from source; do not edit.', 'BEGIN;',
           "SELECT pg_advisory_xact_lock(hashtext('zju2_zijingang_indoor_gis_v2'));",
           "DO $$ BEGIN IF (SELECT version FROM indoor_gis.schema_version WHERE singleton) <> 2 THEN RAISE EXCEPTION 'Expected GIS schema v2'; END IF; END $$;"]
    # Replace this facility snapshot in reverse FK order so removed features do
    # not linger after a source correction. Other facilities are untouched.
    for table in reversed(TABLE_ORDER[2:]):
        if table=='vertical_connection': where="from_node_id IN (SELECT n.node_id FROM indoor_gis.path_node n JOIN indoor_gis.level l USING(level_id) WHERE l.facility_id="+sql_value(FACILITY)+')'
        elif table=='source_document': where='facility_id='+sql_value(FACILITY)
        elif table=='level': where='facility_id='+sql_value(FACILITY)
        else: where='level_id IN (SELECT level_id FROM indoor_gis.level WHERE facility_id='+sql_value(FACILITY)+')'
        lines.append(f'DELETE FROM indoor_gis.{table} WHERE {where};')
    for table,rows in tables.items():
        for row in rows:
            values=[]
            for key,value in row.items():
                if key=='geom': values.append('ST_SetSRID(ST_GeomFromGeoJSON('+sql_value(json.dumps(value,separators=(',',':')))+'),4326)')
                else: values.append(sql_value(value))
            primary=list(row)[0]
            update=', '.join(f'{k}=EXCLUDED.{k}' for k in row if k != primary)
            lines.append(f'INSERT INTO indoor_gis.{table} ({", ".join(row)}) VALUES ({", ".join(values)}) ON CONFLICT ({primary}) DO UPDATE SET {update};')
    lines.append('SET CONSTRAINTS ALL IMMEDIATE;')
    lines.append('COMMIT;')
    return '\n'.join(lines)+'\n',tables


def build(source=SOURCE, output=ROOT/'gis/generated', *, previews=True):
    source,output=Path(source),Path(output); output.mkdir(parents=True,exist_ok=True)
    evidence=read(source/'evidence.json')
    for e in evidence['evidence']:
        if e.get('path') and digest(source/e['path']) != e['sha256']:
            raise ValueError('Evidence SHA-256 mismatch: '+e['id'])
    matrices,reg,control=registration(source)
    specs={f:read(source/f'{f.lower()}_features.json') for f in ('1F','2F','3F')}
    mapping_spec=read(source/'scheduler_mappings.json')
    speed=mapping_spec['walking_speed_mps']
    if isinstance(speed,bool) or not isinstance(speed,(int,float)) or not math.isfinite(speed) or speed<=0:
        raise ValueError('Walking speed must be finite and positive')
    layers={}; checks={}
    for f,spec in specs.items():
        layers[f],checks[f]=build_floor(spec,matrices[f],speed)
        if previews: svg_preview(spec,layers[f],source,output)
    errors=[e for c in checks.values() for e in c['errors']]
    # Emit the pixel audit even on failure, so inaccurate traces are reviewable.
    write_json(output/'validation.json',dict(status='failed' if errors else 'passed',floors=checks,errors=errors))
    if errors: raise ValueError('\n'.join(errors))
    nodes={n['id']:n for l in layers.values() for n in l['route_nodes']}
    openings={o['id']:o for l in layers.values() for o in l['openings']}
    pois={p['id']:p for l in layers.values() for p in l['pois']}
    edges=[e for l in layers.values() for e in l['route_edges']]
    vertical=read(source/'vertical_connections.json')['connections']
    for c in vertical:
        start,end=openings[c['from_opening']],openings[c['to_opening']]
        c.update(from_node_id=start['properties']['route_node_id'],to_node_id=end['properties']['route_node_id'])
        a,b=nodes[c['from_node_id']],nodes[c['to_node_id']]
        pa,pb=a['properties'],b['properties']
        if pa['kind']!='stair_landing' or pb['kind']!='stair_landing' or pa['building_id']!=pb['building_id'] or abs(int(pa['floor'][0])-int(pb['floor'][0]))!=1:
            raise ValueError('Invalid vertical correspondence '+c['id'])
        am=Point(TO_METRIC.transform(*a['geometry']['coordinates'])); bm=Point(TO_METRIC.transform(*b['geometry']['coordinates']))
        c['horizontal_offset_m']=am.distance(bm)
        if c['horizontal_offset_m']>5: raise ValueError('Stair landing registration offset >5m: '+c['id'])
    graph_pairs=[(e['properties']['source'],e['properties']['target']) for e in edges]+[(v['from_node_id'],v['to_node_id']) for v in vertical]
    combined=components(nodes,graph_pairs)
    if len(combined)!=1: raise ValueError('Combined graph disconnected')
    mappings=mapping_spec['mappings']
    for m in mappings:
        poi=pois[m['poi_id']]; p=poi['properties']
        if m['status']!='evidenced_label' or not p['route_node_id']: raise ValueError('Unproven or unreachable scheduler mapping')
        p['scheduler_location_id']=m['location_id']; m['route_node_id']=p['route_node_id']
    matrix=shortest_routes(nodes,edges,vertical,mappings)
    hashes={str(p.relative_to(source)).replace('\\','/'):digest(p) for p in sorted(source.rglob('*')) if p.is_file()}
    build_id=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()
    for row in matrix: row['source_bundle_sha256']=build_id
    metadata=dict(schema_version=2,status=STATUS,facility_id=FACILITY,output_crs='EPSG:4326 approximate WGS84',
                  conversion_chain=['BD-09LL raw user coordinates','GCJ-02 approximate inverse','WGS84 approximate inverse','EPSG:32651 metric fit','original floor pixels via audited affine registration','EPSG:4326 export'],
                  conversion_note='Numerical inverse of engineering formula, not official Baidu inverse; no surveyed accuracy claim',
                  source_bundle_sha256=build_id,source_files_sha256=hashes,
                  generation_code_sha256={p.name:digest(p) for p in [Path(__file__),Path(__file__).with_name('gis_coordinates.py')]},
                  registration=reg,evidence=evidence['evidence'],walking_model=mapping_spec,
                  vertical_cost_model='60 seconds per floor, assumed range 40–90; floor heights unknown',
                  import_order=TABLE_ORDER,missing_original_evidence=['control_annotation','building_circle'])
    for f,l in layers.items():
        for name,feats in l.items(): write_json(output/f'{f.lower()}_{name}.geojson',dict(type='FeatureCollection',status=STATUS,features=feats))
        write_json(output/f'{f.lower()}_metadata.json',dict(status=STATUS,source_image=specs[f]['image'],registration=reg[f],validation=checks[f]))
    write_json(output/'metadata.json',metadata)
    write_json(output/'vertical_connections.json',vertical)
    write_csv(output/'control_points.csv',control,list(control[0]))
    write_csv(output/'scheduler_travel_times.csv',matrix,list(matrix[0]) if matrix else ['origin','destination','travel_minutes'])
    write_json(output/'scheduler_travel_times.json',dict(status=STATUS,profile='ambulatory_draft',rows=matrix))
    write_json(output/'validation.json',dict(status='passed',floors=checks,errors=[],combined_components=len(combined),
                                            vertical_connections=vertical,wheelchair_network_status='unknown; no verified accessible route',
                                            unmapped_locations=mapping_spec['excluded']))
    seed,tables=seed_sql(layers,vertical,evidence,metadata)
    (output/'seed.sql').write_text(seed,encoding='utf-8')
    write_json(output/'import_manifest.json',dict(schema_version=2,tables=[dict(table=t,rows=len(tables[t])) for t in TABLE_ORDER],transactional=True,facility_snapshot=FACILITY))
    workspace=[]
    for f,l in layers.items():
        features=[]
        for name,kind in [('level','buildingOutline'),('spaces','room'),('route_edges','corridor'),('openings','opening'),('pois','poi')]:
            for feat in l[name]:
                p={**feat['properties'],'featureType':kind,'floorKey':f}
                if name=='spaces' and p['use_type'] in ('corridor','lobby'): p['featureType']='passageArea'
                if name=='pois' and p['scheduler_location_id']: p['evidencedDepartmentKey']=p['scheduler_location_id']
                if name=='route_edges': p['distanceMeters']=p['length_m']
                features.append({**feat,'properties':p})
        connections = [c for c in vertical if nodes[c['from_node_id']]['properties']['floor'] == f]
        workspace.append(dict(floorKey=f,geojson=dict(type='FeatureCollection',features=features,verticalConnections=connections)))
    # Safe standalone display import has no operational department mutations.
    write_json(output/'workspace_gis_only.json',dict(formatVersion='1.0',mode='upsert',gis=workspace))
    if previews: preview_index(output)
    return dict(layers=layers,vertical=vertical,matrix=matrix,metadata=metadata)


def preview_index(output):
    (output/'preview.html').write_text('''<!doctype html><html lang="zh"><meta charset="utf-8"><title>紫金港室内 GIS 核验</title>
<style>body{font-family:system-ui;margin:24px;background:#f4f6f7;color:#17252e}header{position:sticky;top:0;background:white;padding:12px;z-index:1}button,a{margin:8px}object{width:100%;background:white}p{max-width:1000px}.warning{color:#9b3d00}</style>
<header><b>浙大二院浙大院区（紫金港） · 导航级草案 / 非测绘成果</b><br>
<button onclick="show('1f')">1F</button><button onclick="show('2f')">2F</button><button onclick="show('3f')">3F</button>
<label><input type="checkbox" checked onchange="toggle('spaces',this.checked)">空间边界</label>
<label><input type="checkbox" checked onchange="toggle('route_edges',this.checked);toggle('route_nodes',this.checked)">路网</label>
<label><input type="checkbox" checked onchange="toggle('openings',this.checked)">门/开口</label>
<label><input type="checkbox" checked onchange="toggle('labels',this.checked)">编号</label></header>
<p>蓝色：可见分隔的概化边界；紫红色：门外路线与节点；橙色：门/开口。编号前 ? 表示门位未确认、尚未接入路网。B1/B2 分别为一号楼/二号楼。悬停查看稳定 ID，P 编号可查各楼层 feature_index.csv。原图红色箭头是疏散标识，不代表单向患者路线。</p>
<p class="warning">控制点标注原图和蓝圈原图缺失；继承像素配准未复核。空缺门位、科室、门禁、电梯与无障碍均待核验。楼梯连接按图推断，不能宣称已现场确认。</p>
<object id="map" type="image/svg+xml" data="1f_overlay.svg"></object>
<p><a href="validation.json">核验结果与未解决项</a><a href="metadata.json">坐标链、残差与证据</a><a href="scheduler_travel_times.json">可追溯步行矩阵</a></p>
<script>function show(f){document.getElementById('map').data=f+'_overlay.svg';document.querySelectorAll('input').forEach(x=>x.checked=true)}function toggle(id,on){const d=document.getElementById('map').contentDocument;const g=d&&d.getElementById(id);if(g)g.style.display=on?'':'none'}</script></html>''',encoding='utf-8')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,default=SOURCE)
    p.add_argument('--output',type=Path,default=ROOT/'gis/generated')
    p.add_argument('--no-previews',action='store_true')
    args=p.parse_args()
    result=build(args.source,args.output,previews=not args.no_previews)
    print(json.dumps({f:{k:len(v) for k,v in l.items()} for f,l in result['layers'].items()},ensure_ascii=False))


if __name__=='__main__': main()
