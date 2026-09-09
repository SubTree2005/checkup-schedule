"""Real PostGIS import/rollback tests in a newly-created disposable database.

Set GIS_TEST_DSN to a development PostgreSQL DSN with CREATEDB permission.
No existing database contents are edited; the test drops only its random DB.
"""
import importlib.util
import json
import os
from pathlib import Path
import unittest
import uuid

ROOT=Path(__file__).resolve().parents[1]
DSN=os.environ.get('GIS_TEST_DSN')


@unittest.skipUnless(DSN and importlib.util.find_spec('psycopg'),'GIS_TEST_DSN and [gis-postgis-test] required')
class PostgisImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        cls.psycopg=psycopg
        cls.admin=psycopg.connect(DSN,autocommit=True)
        cls.database='gis_test_'+uuid.uuid4().hex
        cls.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.database)))
        cls.db=psycopg.connect(DSN,dbname=cls.database,autocommit=True)
        cls.schema=(ROOT/'gis/schema.sql').read_text(encoding='utf-8')
        cls.seed=(ROOT/'gis/generated/seed.sql').read_text(encoding='utf-8')
        cls.db.execute(cls.schema)
        cls.db.execute(cls.seed)

    @classmethod
    def tearDownClass(cls):
        from psycopg import sql
        cls.db.close()
        cls.admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(cls.database)))
        cls.admin.close()

    def snapshot(self):
        manifest=json.loads((ROOT/'gis/generated/import_manifest.json').read_text(encoding='utf-8'))
        return {t['table']:self.db.execute('SELECT md5(coalesce(string_agg(row_to_json(t)::text, chr(10) ORDER BY row_to_json(t)::text),\'\')) FROM indoor_gis.'+t['table']+' t').fetchone()[0] for t in manifest['tables']}

    def test_a_import_twice_is_idempotent(self):
        first=self.snapshot()
        self.db.execute(self.schema); self.db.execute(self.seed)
        self.assertEqual(first,self.snapshot())
        manifest=json.loads((ROOT/'gis/generated/import_manifest.json').read_text(encoding='utf-8'))
        for t in manifest['tables']:
            self.assertEqual(self.db.execute('SELECT count(*) FROM indoor_gis.'+t['table']).fetchone()[0],t['rows'])
        self.assertIn('POSTGIS=',self.db.execute('SELECT PostGIS_Full_Version()').fetchone()[0])

    def test_b_orphan_and_duplicate_room_are_rejected(self):
        with self.assertRaises(self.psycopg.Error):
            self.db.execute("UPDATE indoor_gis.path_edge SET source_node_id='not_a_node' WHERE edge_id=(SELECT min(edge_id) FROM indoor_gis.path_edge)")
        with self.assertRaises(self.psycopg.Error):
            self.db.execute("UPDATE indoor_gis.space SET room_ref='201' WHERE space_id='2f_b1_203'")

    def test_c_node_edit_and_wrong_floor_fail_deferred_topology(self):
        with self.assertRaises(self.psycopg.Error):
            self.db.execute("UPDATE indoor_gis.path_node SET geom=ST_Translate(geom,.001,0) WHERE node_id='n_o_3f_b1_301'")
        with self.assertRaises(self.psycopg.Error):
            self.db.execute("UPDATE indoor_gis.vertical_connection SET to_node_id='n_o_2f_b2_stair_n' WHERE connector_id='vc_1f_2f_b1_n'")

    def test_d_geometry_unknown_and_crs_constraints(self):
        with self.assertRaises(self.psycopg.Error):
            self.db.execute("UPDATE indoor_gis.path_node SET accessible='maybe' WHERE node_id='n_o_3f_b1_301'")
        with self.assertRaises(self.psycopg.Error):
            self.db.execute("UPDATE indoor_gis.space SET geom=ST_GeomFromText('POLYGON((0 0,1 1,1 0,0 1,0 0))',4326) WHERE space_id='3f_b1_301'")
        with self.assertRaises(self.psycopg.Error):
            self.db.execute("UPDATE indoor_gis.path_node SET geom=ST_SetSRID(geom,3857) WHERE node_id='n_o_3f_b1_301'")

    def test_e_failed_snapshot_rolls_back(self):
        before=self.snapshot()
        # Remove one parent insert, leaving a real FK-order failure.
        corrupted='\n'.join(line for line in self.seed.splitlines() if not (line.startswith('INSERT INTO indoor_gis.path_node ') and "'n_o_1f_b1_laboratory'" in line))
        try:
            with self.assertRaises(self.psycopg.Error): self.db.execute(corrupted)
        finally:
            self.db.execute('ROLLBACK')
        self.assertEqual(before,self.snapshot())

    def test_z_legacy_schema_is_preserved(self):
        # A separate database exercises the old->new contract, not just a fresh install.
        from psycopg import sql
        name='gis_test_'+uuid.uuid4().hex
        self.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        try:
            with self.psycopg.connect(DSN,dbname=name,autocommit=True) as db:
                db.execute((ROOT/'gis/audit/legacy/gis/schema.sql').read_text(encoding='utf-8'))
                db.execute("INSERT INTO indoor_gis.facility VALUES('legacy','retained')")
                db.execute((ROOT/'gis/migrations/001_preserve_v1.sql').read_text(encoding='utf-8'))
                db.execute(self.schema); db.execute(self.seed)
                self.assertEqual(db.execute("SELECT name FROM indoor_gis_legacy_v1.facility WHERE facility_id='legacy'").fetchone()[0],'retained')
        finally:
            self.admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))


if __name__=='__main__': unittest.main()
