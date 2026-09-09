"""Coordinate contract used by the historical rectified GIS exporter."""
import copy

PIXEL_SYSTEM = 'floor-local image pixels, x right, y up; schematic, not geographic'


def is_rectified(geojson):
    return geojson.get('coordinateSystem') == PIXEL_SYSTEM and geojson.get('rectificationVersion') == 'orthogonal-20260909'


def flip_pixels(coords):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], 6), round(-coords[1], 6)]
    return [flip_pixels(c) for c in coords]


def registry_in_pixels(registry):
    result = copy.deepcopy(registry)
    def convert(value):
        if isinstance(value, dict):
            geometry = (value.get('properties') or {}).get('geometry_px')
            if value.get('type') == 'Feature' and geometry:
                value['geometry'] = {'type': geometry['type'], 'coordinates': flip_pixels(geometry['coordinates'])}
            else:
                for item in value.values():
                    convert(item)
        elif isinstance(value, list):
            for item in value:
                convert(item)
    convert(result)
    return result
