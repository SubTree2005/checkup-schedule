"""Reproduce legacy comparisons and registration review images; never feeds the build."""
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import LineString, Polygon

from build_indoor_gis import ROOT, SOURCE, read, digest, write_json, write_csv, registration, apply


def run():
    output=ROOT/'gis/audit'; legacy=output/'legacy/gis/source'
    summary={}; crossings=[]
    for floor in ('1f','2f','3f'):
        old=read(legacy/f'{floor}_features.json')
        new=read(SOURCE/f'{floor}_features.json')
        scale=np.array([1330/2136,746/1197]) if floor=='1f' else np.ones(2)
        def pixel(p): return (np.array(p)*scale).tolist()
        if floor=='1f':
            rooms=old['spaces']; lines=[(e['id'],[pixel(p) for p in e['path_px']]) for e in old['route_edges']]
        else:
            rooms=old['rooms']; nodes={n['id']:n['pixel'] for n in old['corridor_nodes']}
            lines=[(e['id'],e.get('path_px') or [nodes[e['source']],nodes[e['target']]]) for e in old['corridor_edges']]
            lines += [('e_'+r['id'],[nodes[r['corridor_node']],r['door_px']]) for r in rooms]
        blocked=[(s['id'],Polygon(s['polygon_px']).buffer(-.3)) for s in new['spaces'] if s['use_type'] not in ('corridor','lobby','stairs')]
        for eid,points in lines:
            line=LineString(points)
            for sid,poly in blocked:
                length=line.intersection(poly).length
                if length>3: crossings.append(dict(floor=floor,legacy_edge_id=eid,new_space_id=sid,intrusion_length_px=round(length,3)))
        with Image.open(SOURCE/new['image']) as source_im:
            im=source_im.convert('RGB').resize((source_im.width*2,source_im.height*2))
        d=ImageDraw.Draw(im)
        for r in rooms:
            pts=[pixel(p) for p in r['polygon_px']]; pts.append(pts[0])
            d.line([(x*2,y*2) for x,y in pts],fill='#006cba',width=2)
        for eid,pts in lines:
            d.line([(x*2,y*2) for x,y in pts],fill='#c3006f',width=3)
        im.save(output/f'{floor}_legacy_overlay.png')
        summary[floor]=dict(legacy_spaces=len(rooms),legacy_edges=len(lines),
            flagged_legacy_edges=len({c['legacy_edge_id'] for c in crossings if c['floor']==floor}),
            comparison_note='Flags are against the new manually reviewed partitions; review images, not an independent survey verdict')
    write_csv(output/'legacy_path_conflicts.csv',crossings,['floor','legacy_edge_id','new_space_id','intrusion_length_px'])
    snapshots={str(p.relative_to(output/'legacy')).replace('\\','/'):digest(p) for p in sorted((output/'legacy').rglob('*')) if p.is_file() and '__pycache__' not in p.parts}
    write_json(output/'legacy_inventory.json',dict(original_root='C:/Users/SubTree/Documents/ChatGPT/checkup-schedule',files_sha256=snapshots,comparisons=summary))
    matrices,reg,controls=registration(SOURCE)
    with Image.open(SOURCE/'images/1F.jpg') as raw: im=raw.convert('RGB').resize((2660,1492))
    d=ImageDraw.Draw(im)
    for c in controls:
        x,y=c['pixel_1f']; x*=2; y*=2
        observed=np.array(c['utm51n_m'])
        projected_px=(observed-matrices['1F'][2]) @ np.linalg.inv(matrices['1F'][:2])
        u,v=projected_px*2
        d.line((x,y,u,v),fill='#e23d00',width=4)
        d.ellipse((x-7,y-7,x+7,y+7),outline='#0068aa',width=3)
        d.text((x+9,y-16),f"CP{c['id']} | residual {c['residual_m']:.2f} m",fill='#b40000',stroke_width=1,stroke_fill='white')
    d.text((10,10),'Inherited annotation pixels, NOT reverified. Vectors: fit residuals, NOT absolute accuracy.',fill='#a30000')
    im.save(output/'1f_control_registration.png')
    with Image.open(SOURCE/'images/1F.jpg') as raw: im=raw.convert('RGB').resize((2660,1492))
    d=ImageDraw.Draw(im)
    inv=np.linalg.inv(matrices['1F'][:2])
    for floor,color in [('2F','#cc2500'),('3F','#007d40')]:
        spec=read(SOURCE/f'{floor.lower()}_features.json')
        for s in spec['spaces']:
            if s['use_type'] in ('corridor','lobby'): continue
            pts=[]
            for p in s['polygon_px']+[s['polygon_px'][0]]:
                xy=(apply(matrices[floor],p)-matrices['1F'][2]) @ inv
                pts.append(tuple(xy*2))
            d.line(pts,fill=color,width=2)
    d.text((10,10),'1F raster + registered 2F (red) / 3F (green); check structural alignment, NOT identical floor partitions.',fill='#333333')
    im.save(output/'registered_floors_on_1f.png')
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__': run()
