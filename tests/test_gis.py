"""Spatial regressions, reproducibility and application contract tests for GIS v2."""
import copy
import csv
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

from checkup_scheduler.gis import attach_gis_to_workspace, load_travel_time_matrix_csv

ROOT=Path(__file__).resolve().parents[1]
GEN=ROOT/'gis/generated'
sys.path.insert(0,str(ROOT/'scripts'))
HAS_GIS=all(importlib.util.find_spec(p) for p in ('numpy','shapely','pyproj','PIL'))
if HAS_GIS:
    from shapely.geometry import Point, LineString, Polygon, shape
    from shapely.ops import unary_union
    import build_indoor_gis as gis
    from gis_coordinates import bd09_to_gcj02, gcj02_to_wgs84, wgs84_to_gcj02


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


@unittest.skipUnless(HAS_GIS,'Install the [gis] extra for spatial validation')
class IndoorGisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layers={f:{k:read(GEN/f'{f.lower()}_{k}.geojson')['features'] for k in ('level','spaces','route_nodes','route_edges','openings','pois')} for f in ('1F','2F','3F')}
        cls.nodes={n['id']:n for l in cls.layers.values() for n in l['route_nodes']}

    def test_original_evidence_hashes_dimensions_and_missing_records(self):
        from PIL import Image
        evidence=read(ROOT/'gis/source/evidence.json')['evidence']
        for e in evidence:
            if e.get('path'):
                p=ROOT/'gis/source'/e['path']
                self.assertEqual(gis.digest(p),e['sha256'])
                if e['kind']=='original_raster':
                    with Image.open(p) as im: self.assertEqual(list(im.size),e['dimensions_px'])
            if e['status']=='missing_not_reverified':
                self.assertIsNone(e['sha256']); self.assertTrue(e['reported_sha256'])
        with Image.open(ROOT/'gis/source/images/2F.jpg') as im: self.assertEqual(im.size,(1165,686))

    def test_control_chain_is_not_crs_relabeling_or_accuracy_claim(self):
        matrices,report,controls=gis.registration(ROOT/'gis/source')
        for c in controls:
            raw=c['bd09ll']; wgs=c['wgs84_approx']; gcj=c['gcj02']
            self.assertGreater(abs(raw[0]-wgs[0]),.008)
            self.assertLess(max(abs(a-b) for a,b in zip(wgs84_to_gcj02(*wgs),gcj)),1e-8)
            self.assertGreaterEqual(c['residual_m'],0)
            self.assertIsNotNone(c['leave_one_out_m'])
        self.assertFalse(report['1F']['annotation_transfer']['verified'])
        self.assertLess(report['1F']['fit_rmse'],3)
        self.assertGreater(max(report['1F']['leave_one_out_residuals']),report['1F']['fit_rmse'])
        for f,r in report.items():
            self.assertIsNone(r['absolute_accuracy_m'])
            if f!='1F':
                self.assertGreater(len(r['anchors']),2)
                self.assertGreaterEqual(len(r['checks']),2)
                self.assertLess(r['check_rmse_m'],3)

    def test_every_geometry_valid_and_near_converted_controls(self):
        for floor,layers in self.layers.items():
            for items in layers.values():
                for feat in items:
                    with self.subTest(feature=feat['id']):
                        g=shape(feat['geometry'])
                        self.assertTrue(g.is_valid); self.assertFalse(g.is_empty)
                        x1,y1,x2,y2=g.bounds
                        self.assertTrue(120.082<x1<=x2<120.086)
                        self.assertTrue(30.310<y1<=y2<30.313)
                        self.assertEqual(feat['properties']['status'],gis.STATUS)
                        if g.geom_type=='Polygon': self.assertTrue(g.exterior.is_ccw)

    def test_building_level_room_identity_and_correct_courtyard(self):
        keys=[]
        for l in self.layers.values():
            for s in l['spaces']:
                p=s['properties']
                if p['room_ref']:
                    self.assertIsNotNone(p['building_id'])
                    keys.append((p['building_id'],p['level_id'],p['room_ref']))
        self.assertEqual(len(keys),len(set(keys)))
        rooms_201=[p['properties'] for p in self.layers['2F']['spaces'] if p['properties']['room_ref']=='201']
        self.assertEqual({p['building_id'] for p in rooms_201},{gis.building_id('b1'),gis.building_id('b2')})
        self.assertTrue(all(p['properties']['building_id']==gis.building_id('b1') for p in self.layers['3F']['spaces']))
        courtyard=next(s for s in self.layers['1F']['spaces'] if s['id']=='1f_b2_courtyard')
        self.assertEqual(courtyard['properties']['name'],'内院')
        self.assertEqual(courtyard['properties']['use_type'],'void')

    def test_paths_stay_in_passages_do_not_cross_rooms_and_snap_to_nodes(self):
        for f,l in self.layers.items():
            rooms=[shape(s['properties']['geometry_px']) for s in l['spaces'] if s['properties']['use_type'] not in ('corridor','lobby','entrance')]
            passages=unary_union([shape(s['properties']['geometry_px']) for s in l['spaces'] if s['properties']['use_type'] in ('corridor','lobby','entrance')])
            for e in l['route_edges']:
                p=e['properties']; line=shape(p['geometry_px'])
                self.assertTrue(passages.buffer(2).covers(line),e['id'])
                self.assertFalse(any(line.intersection(r.buffer(-.3)).length>.1 for r in rooms),e['id'])
                a,b=self.nodes[p['source']],self.nodes[p['target']]
                self.assertEqual(a['properties']['floor'],b['properties']['floor'])
                self.assertLess(Point(e['geometry']['coordinates'][0]).distance(shape(a['geometry'])),1e-12)
                self.assertLess(Point(e['geometry']['coordinates'][-1]).distance(shape(b['geometry'])),1e-12)
                self.assertGreater(p['length_m'],0)
                self.assertAlmostEqual(p['walk_seconds'],p['length_m']/p['walking_speed_mps'])

    def test_openings_on_boundary_and_pois_do_not_teleport(self):
        for f,l in self.layers.items():
            spaces={s['id']:s for s in l['spaces']}
            for o in l['openings']:
                p=o['properties']; g=shape(p['geometry_px'])
                boundary=shape(spaces[p['space_id']]['properties']['geometry_px']).boundary
                self.assertLess(g.difference(boundary.buffer(.001)).length,.001)
                self.assertLess(g.centroid.distance(shape(self.nodes[p['route_node_id']]['properties']['geometry_px'])),1e-5)
            for p in l['pois']:
                self.assertTrue(shape(spaces[p['properties']['space_id']]['geometry']).covers(shape(p['geometry'])))
                if p['properties']['scheduler_location_id']: self.assertIn(p['properties']['route_node_id'],self.nodes)

    def test_topology_same_floor_and_vertical_connections(self):
        all_pairs=[]
        for f,l in self.layers.items():
            pairs=[(e['properties']['source'],e['properties']['target']) for e in l['route_edges']]
            self.assertEqual(len(gis.components([n['id'] for n in l['route_nodes']],pairs)),1)
            all_pairs+=pairs
        vertical=read(GEN/'vertical_connections.json')
        self.assertEqual(len(vertical),5)
        self.assertNotIn('vc_1f_2f_b2_n',{v['id'] for v in vertical})
        for v in vertical:
            a,b=(self.nodes[v[k]]['properties'] for k in ('from_node_id','to_node_id'))
            self.assertEqual(a['building_id'],b['building_id'])
            self.assertEqual(abs(int(a['floor'][0])-int(b['floor'][0])),1)
            self.assertEqual(a['kind'],'stair_landing'); self.assertEqual(b['kind'],'stair_landing')
            self.assertEqual(v['accessible'],'no'); self.assertEqual(v['cost_status'],'assumed_not_measured')
            self.assertLess(v['horizontal_offset_m'],5)
            all_pairs.append((v['from_node_id'],v['to_node_id']))
        self.assertEqual(len(gis.components(self.nodes,all_pairs)),1)

    def test_cross_floor_route_retains_vertical_cost_and_edge_ids(self):
        vertical=read(GEN/'vertical_connections.json')
        ids=['n_o_1f_b1_laboratory','n_o_3f_b1_301']
        mappings=[dict(location_id=str(i),route_node_id=n,poi_id='test_'+str(i),evidence_ids=['test_fixture']) for i,n in enumerate(ids)]
        edges=[e for l in self.layers.values() for e in l['route_edges']]
        rows=gis.shortest_routes(self.nodes,edges,vertical,mappings)
        for r in rows:
            self.assertEqual(sum(e.startswith('vc_') for e in r['edge_ids']),2)
            self.assertGreater(r['walk_seconds'],120)
            self.assertEqual(r['travel_minutes'],math.ceil(r['walk_seconds']/60))

    def test_matrix_has_only_evidenced_departments_and_route_costs(self):
        data=read(GEN/'scheduler_travel_times.json')
        self.assertEqual({r['origin'] for r in data['rows']},{'ENTRANCE','laboratory-1f','infusion-1f'})
        costs={e['id']:e['properties']['walk_seconds'] for l in self.layers.values() for e in l['route_edges']}
        for r in data['rows']:
            self.assertAlmostEqual(r['walk_seconds'],sum(costs[e] for e in r['edge_ids']))
            self.assertEqual(r['travel_minutes'],math.ceil(r['walk_seconds']/60))
            self.assertEqual(len(r['node_ids']),len(r['edge_ids'])+1)
            self.assertEqual(len(r['source_bundle_sha256']),64)

    def test_unknown_accessibility_and_no_invented_elevator(self):
        for l in self.layers.values():
            for k in ('spaces','route_nodes','route_edges','openings','pois'):
                for f in l[k]:
                    self.assertIn(f['properties']['accessible'],('unknown','no'))
                    self.assertEqual(f['properties']['access_control'],'unknown')
        for l in self.layers.values():
            self.assertTrue(all(f['properties']['height_m'] is None for f in l['level']))

    def test_generator_rejects_corrupt_image_and_cross_room_shortcut(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source'; source.mkdir()
            (source/'bad.jpg').write_bytes(b'changed evidence')
            gis.write_json(source/'evidence.json',dict(evidence=[dict(id='plan_1f',path='bad.jpg',sha256='0'*64)]))
            with self.assertRaisesRegex(ValueError,'SHA-256 mismatch'):
                gis.build(source=source,output=Path(tmp)/'output')
        spec=read(ROOT/'gis/source/3f_features.json')
        spec['centerlines'].append(dict(id='bad',path_px=[[147,285],[250,285]]))
        matrix=gis.registration(ROOT/'gis/source')[0]['3F']
        _,report=gis.build_floor(spec,matrix)
        self.assertTrue(any('crosses 3f_b1_316' in e for e in report['errors']))
        spec=read(ROOT/'gis/source/3f_features.json')
        spec['openings'][0]['segment_px']=[[80,175],[80,190]]
        _,report=gis.build_floor(spec,matrix)
        self.assertTrue(any('off boundary' in e for e in report['errors']))
        spec=read(ROOT/'gis/source/2f_features.json')
        spec['spaces'].append(copy.deepcopy(next(s for s in spec['spaces'] if s['id']=='2f_b1_201')))
        with self.assertRaisesRegex(ValueError,'duplicate'): gis.build_floor(spec,matrix)

    def test_irregular_330_332_partition_and_no_invented_room_numbers(self):
        rooms={s['properties']['room_ref']:shape(s['properties']['geometry_px']) for s in self.layers['3F']['spaces'] if s['properties']['room_ref']}
        self.assertTrue(rooms['330'].covers(Point(250,510)))
        self.assertFalse(rooms['330'].covers(Point(190,510)))
        self.assertTrue(rooms['332'].covers(Point(190,510)))
        self.assertFalse(rooms['306'].covers(Point(198,185)))
        self.assertNotIn('334',rooms)
        self.assertNotIn('2f_b2_204',{s['id'] for s in self.layers['2F']['spaces']})

    def test_generation_is_idempotent_and_ignores_old_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            first=Path(tmp)/'a'; second=Path(tmp)/'b'
            gis.build(output=first)
            gis.build(output=second)
            self.assertEqual({p.name:gis.digest(p) for p in first.iterdir()},{p.name:gis.digest(p) for p in second.iterdir()})
            (first/'1f_metadata.json').write_text('corrupted previous output',encoding='utf-8')
            gis.build(output=first)
            self.assertEqual(gis.digest(first/'1f_metadata.json'),gis.digest(second/'1f_metadata.json'))

    def test_seed_fk_import_order_and_unknown_contract(self):
        seed=(GEN/'seed.sql').read_text(encoding='utf-8')
        positions=[seed.index('INSERT INTO indoor_gis.'+t+' ') for t in gis.TABLE_ORDER]
        self.assertEqual(positions,sorted(positions))
        deletes=[seed.index('DELETE FROM indoor_gis.'+t+' ') for t in reversed(gis.TABLE_ORDER[2:])]
        self.assertEqual(deletes,sorted(deletes))
        schema=(ROOT/'gis/schema.sql').read_text(encoding='utf-8')
        self.assertIn('UNIQUE(building_id,level_id,room_ref)',schema)
        self.assertNotIn('DEFAULT true',schema.replace('DEFAULT true CHECK(singleton)',''))
        self.assertIn('ST_StartPoint(e.geom)',schema)
        self.assertIn('DEFERRABLE INITIALLY DEFERRED',schema)


class GisAdapterTests(unittest.TestCase):
    def test_explicit_fallback_and_coverage(self):
        m=load_travel_time_matrix_csv(GEN/'scheduler_travel_times.csv',default_minutes=6,required_locations=['infusion-1f','laboratory-1f'])
        self.assertEqual(m.between('infusion-1f','laboratory-1f'),1)
        self.assertEqual(m.between('unknown','laboratory-1f'),6)
        with self.assertRaises(ValueError):
            load_travel_time_matrix_csv(GEN/'scheduler_travel_times.csv',default_minutes=6,required_locations=['BLOOD','laboratory-1f'])
        with self.assertRaises(TypeError): load_travel_time_matrix_csv(GEN/'scheduler_travel_times.csv')
        with self.assertRaises(ValueError): load_travel_time_matrix_csv(GEN/'scheduler_travel_times.csv',default_minutes=6,required_locations=['BLOOD'])

    def test_invalid_and_conflicting_matrix_rows(self):
        cases=['a,b,-1','a,a,1','a,b,1.2',',b,1','a,b,1\na,b,2']
        for rows in cases:
            with self.subTest(rows=rows),tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp)/'m.csv'; p.write_text('origin,destination,travel_minutes\n'+rows+'\n',encoding='utf-8')
                with self.assertRaises(ValueError): load_travel_time_matrix_csv(p,default_minutes=6)

    def test_workspace_copy_preserves_business_data_and_uses_thresholds(self):
        workspace=read(ROOT/'examples/hospitals/zijingang-campus-hospital/workspace.json')
        before=copy.deepcopy(workspace)
        result=attach_gis_to_workspace(workspace,GEN)
        self.assertEqual(workspace,before)
        for key in before:
            if key!='gis': self.assertEqual(before[key],result[key])
        department_features=[f for l in result['gis'] for f in l['geojson']['features'] if f['properties']['featureType']=='department']
        self.assertEqual({f['properties']['departmentKey'] for f in department_features},{'infusion-1f','laboratory-1f'})
        for f in department_features:
            self.assertNotEqual(f['geometry'],f['properties']['poiGeometry'])
            self.assertIn('routeNodeId',f['properties'])
        if importlib.util.find_spec('sqlalchemy') and importlib.util.find_spec('fastapi'):
            from apps.backend.checkup_backend.schemas import WorkspaceImport
            WorkspaceImport.model_validate(result)
            WorkspaceImport.model_validate(read(GEN/'workspace_gis_only.json'))
            from apps.backend.checkup_backend.patient_api import _shortest_geojson_route
            geo=result['gis'][0]['geojson']
            route=_shortest_geojson_route(geo,department_features[0]['geometry']['coordinates'],department_features[1]['geometry']['coordinates'])
            self.assertGreater(len(route),2)
            self.assertEqual(route[0],department_features[0]['geometry']['coordinates'])
            self.assertEqual(route[-1],department_features[1]['geometry']['coordinates'])


if __name__=='__main__': unittest.main()
