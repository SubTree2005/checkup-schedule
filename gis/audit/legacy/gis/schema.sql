CREATE EXTENSION IF NOT EXISTS postgis;

CREATE SCHEMA IF NOT EXISTS indoor_gis;

CREATE TABLE IF NOT EXISTS indoor_gis.facility (
    facility_id text PRIMARY KEY,
    name text NOT NULL
);

CREATE TABLE IF NOT EXISTS indoor_gis.building (
    building_id text PRIMARY KEY,
    facility_id text NOT NULL REFERENCES indoor_gis.facility(facility_id),
    name text NOT NULL
);

CREATE TABLE IF NOT EXISTS indoor_gis.level (
    level_id text PRIMARY KEY,
    facility_id text NOT NULL REFERENCES indoor_gis.facility(facility_id),
    name text NOT NULL,
    ordinal integer NOT NULL,
    height_m double precision,
    status text NOT NULL,
    accuracy_m double precision,
    geom geometry(Polygon, 4326) NOT NULL
);

CREATE TABLE IF NOT EXISTS indoor_gis.source_document (
    dataset_id text PRIMARY KEY,
    facility_id text NOT NULL,
    level_id text NOT NULL,
    source_filename text NOT NULL,
    source_sha256 text NOT NULL,
    coordinate_source text NOT NULL,
    source_crs text NOT NULL,
    status text NOT NULL,
    georef_rmse_m double precision,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS indoor_gis.space (
    space_id text PRIMARY KEY,
    level_id text NOT NULL REFERENCES indoor_gis.level(level_id),
    building_id text REFERENCES indoor_gis.building(building_id),
    name text NOT NULL,
    use_type text NOT NULL,
    room_ref text,
    confidence text NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
    scheduler_location_id text,
    geom geometry(Polygon, 4326) NOT NULL
);

CREATE TABLE IF NOT EXISTS indoor_gis.path_node (
    node_id text PRIMARY KEY,
    level_id text NOT NULL REFERENCES indoor_gis.level(level_id),
    building_id text REFERENCES indoor_gis.building(building_id),
    name text NOT NULL,
    kind text NOT NULL,
    accessible boolean NOT NULL DEFAULT true,
    geom geometry(Point, 4326) NOT NULL
);

CREATE TABLE IF NOT EXISTS indoor_gis.path_edge (
    edge_id text PRIMARY KEY,
    level_id text NOT NULL REFERENCES indoor_gis.level(level_id),
    source_node_id text NOT NULL REFERENCES indoor_gis.path_node(node_id),
    target_node_id text NOT NULL REFERENCES indoor_gis.path_node(node_id),
    length_m double precision NOT NULL CHECK (length_m > 0),
    walk_seconds double precision NOT NULL CHECK (walk_seconds > 0),
    accessible boolean NOT NULL DEFAULT true,
    geom geometry(LineString, 4326) NOT NULL,
    CHECK (source_node_id <> target_node_id)
);

CREATE TABLE IF NOT EXISTS indoor_gis.poi (
    poi_id text PRIMARY KEY,
    level_id text NOT NULL REFERENCES indoor_gis.level(level_id),
    building_id text REFERENCES indoor_gis.building(building_id),
    route_node_id text REFERENCES indoor_gis.path_node(node_id),
    name text NOT NULL,
    category text NOT NULL,
    confidence text NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
    scheduler_location_id text,
    accessible boolean NOT NULL DEFAULT true,
    geom geometry(Point, 4326) NOT NULL
);

CREATE TABLE IF NOT EXISTS indoor_gis.vertical_connection (
    connector_id text PRIMARY KEY,
    from_node_id text NOT NULL REFERENCES indoor_gis.path_node(node_id),
    to_node_id text NOT NULL REFERENCES indoor_gis.path_node(node_id),
    mode text NOT NULL CHECK (mode IN ('stairs', 'elevator', 'escalator', 'ramp')),
    walk_seconds double precision NOT NULL CHECK (walk_seconds > 0),
    accessible boolean NOT NULL DEFAULT true,
    confidence text NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
    CHECK (from_node_id <> to_node_id)
);

ALTER TABLE indoor_gis.space
    ADD COLUMN IF NOT EXISTS building_id text REFERENCES indoor_gis.building(building_id);
ALTER TABLE indoor_gis.path_node
    ADD COLUMN IF NOT EXISTS building_id text REFERENCES indoor_gis.building(building_id);
ALTER TABLE indoor_gis.poi
    ADD COLUMN IF NOT EXISTS building_id text REFERENCES indoor_gis.building(building_id);

CREATE INDEX IF NOT EXISTS level_geom_gix ON indoor_gis.level USING gist (geom);
CREATE INDEX IF NOT EXISTS space_geom_gix ON indoor_gis.space USING gist (geom);
CREATE INDEX IF NOT EXISTS path_node_geom_gix ON indoor_gis.path_node USING gist (geom);
CREATE INDEX IF NOT EXISTS path_edge_geom_gix ON indoor_gis.path_edge USING gist (geom);
CREATE INDEX IF NOT EXISTS poi_geom_gix ON indoor_gis.poi USING gist (geom);
CREATE INDEX IF NOT EXISTS poi_scheduler_location_idx
    ON indoor_gis.poi (scheduler_location_id)
    WHERE scheduler_location_id IS NOT NULL;

CREATE OR REPLACE VIEW indoor_gis.scheduler_locations AS
SELECT
    scheduler_location_id,
    poi_id,
    route_node_id,
    name,
    confidence,
    geom
FROM indoor_gis.poi
WHERE scheduler_location_id IS NOT NULL;
