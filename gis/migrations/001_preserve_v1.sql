-- Preserve the old schema intact. Never reinterpret old default-true access
-- values as measured facts or mix coarse v1 geometry into the v2 snapshot.
BEGIN;
DO $$ BEGIN
    IF to_regclass('indoor_gis.space') IS NOT NULL
       AND to_regclass('indoor_gis.schema_version') IS NULL THEN
        IF EXISTS(SELECT 1 FROM pg_namespace WHERE nspname='indoor_gis_legacy_v1') THEN
            RAISE EXCEPTION 'indoor_gis_legacy_v1 already exists; refusing to overwrite archive';
        END IF;
        ALTER SCHEMA indoor_gis RENAME TO indoor_gis_legacy_v1;
    END IF;
END $$;
COMMIT;
