"""Export a rectified indoor schematic without changing audited geographic source files."""
import copy,json,math
from pathlib import Path
from shapely.geometry import shape,mapping,Point,LineString,Polygon
from shapely.affinity import scale
from apps.backend.checkup_backend.schemas import WorkspaceImport

ROOT=Path(__file__).resolve().parents[1]
data=json.loads((ROOT/'gis/generated/workspace_gis_only.json').read_text(encoding='utf-8'))
changed=[]
for floor in data['gis']:
 fs=floor['geojson']['features']
 geometries={f['properties']['id']:shape(f['properties']['geometry_px']) for f in fs}
 spaces=[f for f in fs if f['properties'].get('use_type')]
 for f in spaces:
  p=f['properties']; sid=p['id']; poly=geometries[sid]
  if p.get('use_type') in ('corridor','lobby','stairs','void'):continue
  points=list(poly.exterior.coords)
  diagonal=any(abs(a[0]-b[0])>.01 and abs(a[1]-b[1])>.01 for a,b in zip(points,points[1:]))
  if not diagonal:continue
  if sid=='1f_b1_laboratory':
   corrected=Polygon([(x,708 if y==718 else y) for x,y in points])
  else:
   base=poly.minimum_rotated_rectangle
   corrected=base
   factor=1
   others=[geometries[o['properties']['id']] for o in spaces if o is not f]
   while any(corrected.intersection(o).area>.1 for o in others):
    factor-=.005
    assert factor>.4
    corrected=scale(base,xfact=factor,yfact=factor)
  assert corrected.is_valid
  geometries[sid]=corrected
  changed.append(sid)
  p['rectification']='right-angle schematic; not a surveyed room boundary'
  for opening in fs:
   op=opening['properties']
   if op.get('space_id')!=sid or geometries[op['id']].geom_type!='LineString':continue
   old=geometries[op['id']]; centre=old.interpolate(.5,normalized=True)
   sides=[LineString([a,b]) for a,b in zip(list(corrected.exterior.coords),list(corrected.exterior.coords)[1:])]
   side=min(sides,key=lambda s:s.distance(centre))
   half=min(old.length/2,side.length/4)
   pos=max(half,min(side.length-half,side.project(centre)))
   door=LineString([side.interpolate(pos-half),side.interpolate(pos+half)])
   geometries[op['id']]=door
   node_id=op.get('route_node_id'); new_point=door.interpolate(.5,normalized=True)
   if node_id:
    geometries[node_id]=new_point
    for edge in fs:
     ep=edge['properties']; eg=geometries[ep['id']]
     if eg.geom_type!='LineString' or node_id not in (ep.get('source'),ep.get('target')):continue
     coords=list(eg.coords)
     coords[0 if ep.get('source')==node_id else -1]=tuple(new_point.coords[0])
     new_edge=LineString(coords)
     if eg.length and ep.get('length_m'):
      ratio=new_edge.length/eg.length
      ep['length_m']*=ratio
      if ep.get('walk_seconds'):ep['walk_seconds']*=ratio
     geometries[ep['id']]=new_edge
   assert corrected.boundary.distance(door)<.001
  for poi in fs:
   pp=poi['properties']; geo=geometries[pp['id']]
   if pp.get('space_id')==sid and geo.geom_type=='Point' and not corrected.covers(geo):geometries[pp['id']]=corrected.representative_point()
 # All layers share the original pixel plane per floor: remove affine skew together.
 def flip(coords):
  if isinstance(coords[0],(int,float)):return [round(coords[0],6),round(-coords[1],6)]
  return [flip(c) for c in coords]
 for f in fs:
  p=f['properties']; geo=geometries[p['id']]
  p['source_geometry_wgs84']=f['geometry']
  p['geometry_px']=mapping(geo)
  f['geometry']={'type':geo.geom_type,'coordinates':flip(mapping(geo)['coordinates'])}
  assert geo.is_valid
 floor['geojson']['coordinateSystem']='floor-local image pixels, x right, y up; schematic, not geographic'
 floor['geojson']['rectificationVersion']='orthogonal-20260909'
 for f in spaces:
  if f['properties'].get('use_type') in ('corridor','lobby','stairs','void'):continue
  coords=list(geometries[f['properties']['id']].exterior.coords)[:-1]
  for i,b in enumerate(coords):
   a,c=coords[i-1],coords[(i+1)%len(coords)]
   u=(a[0]-b[0],a[1]-b[1]);v=(c[0]-b[0],c[1]-b[1])
   norm=math.hypot(*u)*math.hypot(*v)
   if norm:
    cosine=abs((u[0]*v[0]+u[1]*v[1])/norm)
    assert cosine<1e-6 or abs(cosine-1)<1e-6,(f['properties']['id'],cosine)
WorkspaceImport.model_validate(data)
target=ROOT.parent/'GIS-直角优化版-整合导入-20260909.json'
target.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('Validated floors:',[(f['floorKey'],len(f['geojson']['features'])) for f in data['gis']])
print('Rectified:',changed)
print('Output:',target.name,target.stat().st_size)
