-- v2: navigation draft, not a cadastral or measured wall model.
-- Existing v1 installations: run migrations/001_preserve_v1.sql first.
BEGIN;
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS indoor_gis;
DO $$ BEGIN
    IF to_regclass('indoor_gis.space') IS NOT NULL
       AND to_regclass('indoor_gis.schema_version') IS NULL THEN
        RAISE EXCEPTION 'Legacy GIS detected: run migrations/001_preserve_v1.sql first';
    END IF;
END $$;
CREATE TABLE IF NOT EXISTS indoor_gis.schema_version (
    singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
    version integer NOT NULL CHECK(version=2)
);
INSERT INTO indoor_gis.schema_version VALUES(true,2) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS indoor_gis.facility (
    facility_id text PRIMARY KEY,
    name text NOT NULL,
    status text NOT NULL CHECK(status='navigation_draft_not_survey')
);
CREATE TABLE IF NOT EXISTS indoor_gis.building (
    building_id text PRIMARY KEY,
    facility_id text NOT NULL REFERENCES indoor_gis.facility,
    name text NOT NULL,
    UNIQUE(building_id,facility_id)
);
CREATE TABLE IF NOT EXISTS indoor_gis.level (
    level_id text PRIMARY KEY,
    facility_id text NOT NULL REFERENCES indoor_gis.facility,
    building_id text,
    name text NOT NULL,
    ordinal integer NOT NULL,
    height_m double precision,
    status text NOT NULL,
    accuracy_m double precision CHECK(accuracy_m>=0 AND accuracy_m<'Infinity'::float8),
    metadata jsonb NOT NULL DEFAULT '{}',
    geom geometry(Geometry,4326) NOT NULL,
    FOREIGN KEY(building_id,facility_id) REFERENCES indoor_gis.building(building_id,facility_id),
    UNIQUE(level_id,building_id),
    CHECK(ST_GeometryType(geom) IN ('ST_Polygon','ST_MultiPolygon') AND NOT ST_IsEmpty(geom) AND ST_IsValid(geom))
);
CREATE UNIQUE INDEX IF NOT EXISTS one_floor_per_building ON indoor_gis.level(building_id,ordinal) WHERE building_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS one_shared_floor_per_facility ON indoor_gis.level(facility_id,ordinal) WHERE building_id IS NULL;
CREATE TABLE IF NOT EXISTS indoor_gis.source_document (
    evidence_id text PRIMARY KEY,
    facility_id text NOT NULL REFERENCES indoor_gis.facility,
    source_uri text,
    sha256 text CHECK(sha256 ~ '^[0-9a-f]{64}$'),
    status text NOT NULL,
    metadata jsonb NOT NULL,
    CHECK(status<>'missing_not_reverified' OR sha256 IS NULL)
);
CREATE TABLE IF NOT EXISTS indoor_gis.space (
    space_id text PRIMARY KEY,
    level_id text NOT NULL REFERENCES indoor_gis.level,
    building_id text,
    name text NOT NULL,
    use_type text NOT NULL,
    room_ref text,
    confidence text NOT NULL CHECK(confidence IN ('high','medium','low')),
    accessible text NOT NULL DEFAULT 'unknown' CHECK(accessible IN ('yes','no','unknown')),
    access_control text NOT NULL DEFAULT 'unknown' CHECK(access_control IN ('public','restricted','unknown')),
    metadata jsonb NOT NULL,
    geom geometry(Geometry,4326) NOT NULL,
    FOREIGN KEY(level_id,building_id) REFERENCES indoor_gis.level(level_id,building_id),
    UNIQUE(space_id,level_id),
    UNIQUE(building_id,level_id,room_ref),
    CHECK(room_ref IS NULL OR (building_id IS NOT NULL AND length(trim(room_ref))>0)),
    CHECK(ST_GeometryType(geom) IN ('ST_Polygon','ST_MultiPolygon') AND NOT ST_IsEmpty(geom) AND ST_IsValid(geom))
);
CREATE TABLE IF NOT EXISTS indoor_gis.path_node (
    node_id text PRIMARY KEY,
    level_id text NOT NULL REFERENCES indoor_gis.level,
    building_id text,
    name text NOT NULL,
    kind text NOT NULL,
    accessible text NOT NULL DEFAULT 'unknown' CHECK(accessible IN ('yes','no','unknown')),
    access_control text NOT NULL DEFAULT 'unknown' CHECK(access_control IN ('public','restricted','unknown')),
    metadata jsonb NOT NULL,
    geom geometry(Point,4326) NOT NULL CHECK(NOT ST_IsEmpty(geom) AND ST_IsValid(geom)),
    FOREIGN KEY(level_id,building_id) REFERENCES indoor_gis.level(level_id,building_id),
    UNIQUE(node_id,level_id)
);
CREATE TABLE IF NOT EXISTS indoor_gis.opening (
    opening_id text PRIMARY KEY,
    level_id text NOT NULL REFERENCES indoor_gis.level,
    building_id text,
    space_id text NOT NULL,
    route_node_id text NOT NULL,
    kind text NOT NULL CHECK(kind IN ('door','opening','stair_gate')),
    confidence text NOT NULL CHECK(confidence IN ('high','medium','low')),
    accessible text NOT NULL DEFAULT 'unknown' CHECK(accessible IN ('yes','no','unknown')),
    access_control text NOT NULL DEFAULT 'unknown' CHECK(access_control IN ('public','restricted','unknown')),
    metadata jsonb NOT NULL,
    geom geometry(LineString,4326) NOT NULL CHECK(NOT ST_IsEmpty(geom) AND ST_IsValid(geom) AND ST_Length(geom)>0),
    FOREIGN KEY(level_id,building_id) REFERENCES indoor_gis.level(level_id,building_id),
    FOREIGN KEY(space_id,level_id) REFERENCES indoor_gis.space(space_id,level_id),
    FOREIGN KEY(route_node_id,level_id) REFERENCES indoor_gis.path_node(node_id,level_id)
);
CREATE TABLE IF NOT EXISTS indoor_gis.path_edge (
    edge_id text PRIMARY KEY,
    level_id text NOT NULL REFERENCES indoor_gis.level,
    source_node_id text NOT NULL REFERENCES indoor_gis.path_node,
    target_node_id text NOT NULL REFERENCES indoor_gis.path_node,
    length_m double precision NOT NULL CHECK(length_m>0 AND length_m<'Infinity'::float8),
    walk_seconds double precision NOT NULL CHECK(walk_seconds>0 AND walk_seconds<'Infinity'::float8),
    routing_status text NOT NULL CHECK(routing_status IN ('draft_enabled','verified','disabled')),
    accessible text NOT NULL DEFAULT 'unknown' CHECK(accessible IN ('yes','no','unknown')),
    access_control text NOT NULL DEFAULT 'unknown' CHECK(access_control IN ('public','restricted','unknown')),
    metadata jsonb NOT NULL,
    geom geometry(LineString,4326) NOT NULL CHECK(NOT ST_IsEmpty(geom) AND ST_IsValid(geom)),
    CHECK(source_node_id<>target_node_id)
);
CREATE TABLE IF NOT EXISTS indoor_gis.poi (
    poi_id text PRIMARY KEY,
    level_id text NOT NULL REFERENCES indoor_gis.level,
    building_id text,
    space_id text NOT NULL,
    route_node_id text,
    name text NOT NULL,
    category text NOT NULL,
    confidence text NOT NULL CHECK(confidence IN ('high','medium','low')),
    scheduler_location_id text,
    accessible text NOT NULL DEFAULT 'unknown' CHECK(accessible IN ('yes','no','unknown')),
    access_control text NOT NULL DEFAULT 'unknown' CHECK(access_control IN ('public','restricted','unknown')),
    metadata jsonb NOT NULL,
    geom geometry(Point,4326) NOT NULL CHECK(NOT ST_IsEmpty(geom) AND ST_IsValid(geom)),
    FOREIGN KEY(level_id,building_id) REFERENCES indoor_gis.level(level_id,building_id),
    FOREIGN KEY(space_id,level_id) REFERENCES indoor_gis.space(space_id,level_id),
    FOREIGN KEY(route_node_id,level_id) REFERENCES indoor_gis.path_node(node_id,level_id),
    CHECK(scheduler_location_id IS NULL OR route_node_id IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS indoor_gis.vertical_connection (
    connector_id text PRIMARY KEY,
    from_node_id text NOT NULL REFERENCES indoor_gis.path_node,
    to_node_id text NOT NULL REFERENCES indoor_gis.path_node,
    mode text NOT NULL CHECK(mode IN ('stairs','elevator','escalator','ramp')),
    walk_seconds double precision NOT NULL CHECK(walk_seconds>0 AND walk_seconds<'Infinity'::float8),
    confidence text NOT NULL CHECK(confidence IN ('high','medium','low')),
    routing_status text NOT NULL CHECK(routing_status IN ('draft_enabled','verified','disabled')),
    accessible text NOT NULL DEFAULT 'unknown' CHECK(accessible IN ('yes','no','unknown')),
    access_control text NOT NULL DEFAULT 'unknown' CHECK(access_control IN ('public','restricted','unknown')),
    metadata jsonb NOT NULL,
    CHECK(from_node_id<>to_node_id),
    CHECK(mode<>'stairs' OR accessible='no')
);

-- Deferred validation checks the final snapshot, including node edits. FK order
-- stays immediate: parent records must exist before child inserts.
CREATE OR REPLACE FUNCTION indoor_gis.validate_topology() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM indoor_gis.path_edge e
        JOIN indoor_gis.path_node a ON a.node_id=e.source_node_id
        JOIN indoor_gis.path_node b ON b.node_id=e.target_node_id
        JOIN indoor_gis.level la ON la.level_id=a.level_id
        JOIN indoor_gis.level lb ON lb.level_id=b.level_id
        JOIN indoor_gis.level le ON le.level_id=e.level_id
        WHERE la.ordinal<>lb.ordinal OR la.ordinal<>le.ordinal
           OR la.facility_id<>lb.facility_id OR la.facility_id<>le.facility_id
           OR NOT ST_DWithin(ST_StartPoint(e.geom)::geography,a.geom::geography,0.02)
           OR NOT ST_DWithin(ST_EndPoint(e.geom)::geography,b.geom::geography,0.02)
    ) THEN RAISE EXCEPTION 'Invalid same-floor edge or detached endpoint'; END IF;
    IF EXISTS (
        SELECT 1 FROM indoor_gis.vertical_connection v
        JOIN indoor_gis.path_node a ON a.node_id=v.from_node_id
        JOIN indoor_gis.path_node b ON b.node_id=v.to_node_id
        JOIN indoor_gis.level la ON la.level_id=a.level_id
        JOIN indoor_gis.level lb ON lb.level_id=b.level_id
        WHERE la.facility_id<>lb.facility_id OR a.building_id IS DISTINCT FROM b.building_id
           OR a.building_id IS NULL OR abs(la.ordinal-lb.ordinal)<>1
           OR (v.mode='stairs' AND (a.kind<>'stair_landing' OR b.kind<>'stair_landing'))
           OR NOT ST_DWithin(a.geom::geography,b.geom::geography,5)
    ) THEN RAISE EXCEPTION 'Invalid vertical connection'; END IF;
    IF EXISTS (
        SELECT 1 FROM indoor_gis.opening o
        JOIN indoor_gis.space s ON s.space_id=o.space_id
        JOIN indoor_gis.path_node n ON n.node_id=o.route_node_id
        WHERE o.building_id IS DISTINCT FROM s.building_id
           OR o.building_id IS DISTINCT FROM n.building_id
           OR NOT ST_DWithin(ST_Centroid(o.geom)::geography,n.geom::geography,0.02)
           OR NOT ST_CoveredBy(ST_Transform(o.geom,32651),ST_Buffer(ST_Boundary(ST_Transform(s.geom,32651)),0.05))
    ) THEN RAISE EXCEPTION 'Opening detached from space boundary or route node'; END IF;
    IF EXISTS (
        SELECT 1 FROM indoor_gis.poi p JOIN indoor_gis.space s ON s.space_id=p.space_id
        WHERE p.building_id IS DISTINCT FROM s.building_id OR NOT ST_Covers(s.geom,p.geom)
    ) THEN RAISE EXCEPTION 'POI outside its space'; END IF;
    RETURN NULL;
END $$;
DO $$ DECLARE t text; BEGIN
    FOREACH t IN ARRAY ARRAY['level','space','path_node','opening','path_edge','poi','vertical_connection'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='validate_indoor_topology' AND tgrelid=('indoor_gis.'||t)::regclass) THEN
            EXECUTE format('CREATE CONSTRAINT TRIGGER validate_indoor_topology AFTER INSERT OR UPDATE OR DELETE ON indoor_gis.%I DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION indoor_gis.validate_topology()',t);
        END IF;
    END LOOP;
END $$;
CREATE INDEX IF NOT EXISTS space_geom_gix ON indoor_gis.space USING gist(geom);
CREATE INDEX IF NOT EXISTS level_geom_gix ON indoor_gis.level USING gist(geom);
CREATE INDEX IF NOT EXISTS path_node_geom_gix ON indoor_gis.path_node USING gist(geom);
CREATE INDEX IF NOT EXISTS path_edge_geom_gix ON indoor_gis.path_edge USING gist(geom);
CREATE INDEX IF NOT EXISTS opening_geom_gix ON indoor_gis.opening USING gist(geom);
CREATE INDEX IF NOT EXISTS poi_geom_gix ON indoor_gis.poi USING gist(geom);
CREATE OR REPLACE VIEW indoor_gis.scheduler_locations AS
SELECT l.facility_id,p.scheduler_location_id,p.poi_id,p.route_node_id,p.name,p.level_id,p.building_id,p.confidence,p.metadata,p.geom
FROM indoor_gis.poi p JOIN indoor_gis.level l USING(level_id)
WHERE scheduler_location_id IS NOT NULL;
COMMIT;
