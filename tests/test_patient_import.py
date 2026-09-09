import copy
import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from tests import test_backend_api as fixture
from apps.backend.checkup_backend.models import ExamPlan, HospitalAdmin, PlanExecutionDetail, UserInfo
from apps.backend.checkup_backend.main import ensure_compatible_columns


class PatientImportTest(unittest.TestCase):
    setUp = fixture.BackendAPITest.setUp
    tearDown = fixture.BackendAPITest.tearDown
    register = fixture.BackendAPITest.register
    registration_workspace = fixture.BackendAPITest.registration_workspace

    def payload(self):
        response = self.client.get('/api/demo-patients/import-template')
        self.assertEqual(response.status_code, 200, response.text)
        return {'phone': '18888888888', 'password': 'demo-pass-123', 'bundle': response.json()}

    def test_import_login_reports_and_idempotency(self):
        self.register()
        payload = self.payload()
        self.assertNotIn('phone', payload['bundle'])
        self.assertNotIn('password', json.dumps(payload['bundle']))
        result = self.client.post('/api/demo-patients/import', json=payload)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['importedVisits'], 1)
        self.assertEqual(result.json()['importedReports'], 1)
        duplicate = self.client.post('/api/demo-patients/import', json=payload)
        self.assertEqual(duplicate.json()['skippedVisits'], 1)
        self.assertEqual(duplicate.json()['importedVisits'], 0)
        with TestClient(self.app) as patient:
            login = patient.post('/api/patient/auth/login', json={
                'phone': payload['phone'], 'password': payload['password']})
            self.assertEqual(login.status_code, 200, login.text)
            plans = patient.get('/api/patient/plans')
            self.assertEqual(plans.status_code, 200, plans.text)
            plan = plans.json()[0]
            self.assertEqual(plan['planStatus'], '已完成')
            self.assertTrue(plan['isDemo'])
            self.assertEqual(plan['packageName'], payload['bundle']['visits'][0]['title'])
            self.assertTrue(plan['steps'][0]['reportAvailable'])
            self.assertEqual(plan['steps'][0]['report']['conclusion'], payload['bundle']['visits'][0]['steps'][0]['report']['conclusion'])
            self.assertTrue(plan['steps'][0]['report']['simulated'])
            fetched = patient.get('/api/patient/plans/' + plan['planID'])
            self.assertEqual(fetched.json()['steps'][0]['report'], plan['steps'][0]['report'])

    def test_reimport_refreshes_existing_copy_without_duplicate_visits(self):
        self.register()
        payload = self.payload()
        result = self.client.post('/api/demo-patients/import', json=payload).json()
        with self.app.state.session_factory() as db:
            detail = db.scalar(select(PlanExecutionDetail).where(PlanExecutionDetail.plan_id == result['planIDs'][0]))
            detail.exam_report = {**detail.exam_report, 'conclusion': '模拟体检报告，仅供系统演示。\n旧内容'}
            original_id, original_start = detail.detail_id, detail.actual_start
            db.commit()
        visit = payload['bundle']['visits'][0]
        visit['title'] = '年度健康体检'
        visit['steps'][0]['report']['conclusion'] = '未见明显异常。'
        updated = self.client.post('/api/demo-patients/import', json=payload)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()['updatedVisits'], 1)
        self.assertEqual(updated.json()['importedVisits'], 0)
        with self.app.state.session_factory() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(ExamPlan)), 1)
            detail = db.get(PlanExecutionDetail, original_id)
            self.assertEqual(detail.actual_start, original_start)
            self.assertEqual(detail.exam_report['conclusion'], '未见明显异常。')
            self.assertTrue(detail.exam_report['simulated'])
        again = self.client.post('/api/demo-patients/import', json=payload).json()
        self.assertEqual(again['updatedVisits'], 0)
        self.assertEqual(again['skippedVisits'], 1)
        # Import updates must never overwrite a subsequently issued clinical report.
        with self.app.state.session_factory() as db:
            detail = db.get(PlanExecutionDetail, original_id)
            detail.exam_report = {**detail.exam_report, 'simulated': False}
            db.commit()
        self.assertEqual(self.client.post('/api/demo-patients/import', json=payload).status_code, 409)

    def test_invalid_items_are_atomic_and_existing_account_requires_password(self):
        self.register()
        payload = self.payload()
        invalid = copy.deepcopy(payload)
        invalid['bundle']['visits'][0]['steps'][0]['itemName'] = '不存在的项目'
        result = self.client.post('/api/demo-patients/import', json=invalid)
        self.assertEqual(result.status_code, 400)
        with self.app.state.session_factory() as db:
            self.assertIsNone(db.scalar(select(UserInfo).where(UserInfo.phone == payload['phone'])))
        self.assertEqual(self.client.post('/api/demo-patients/import', json=payload).status_code, 200)
        invalid = copy.deepcopy(payload)
        invalid['password'] = 'wrong-pass-123'
        invalid['bundle']['patient']['name'] = '不应写入'
        self.assertEqual(self.client.post('/api/demo-patients/import', json=invalid).status_code, 409)
        with self.app.state.session_factory() as db:
            user = db.scalar(select(UserInfo).where(UserInfo.phone == payload['phone']))
            self.assertEqual(user.name, payload['bundle']['patient']['name'])

    def test_password_change_revokes_sessions_and_preserves_old_plans(self):
        self.register()
        payload = self.payload()
        self.client.post('/api/demo-patients/import', json=payload)
        with TestClient(self.app) as patient:
            patient.post('/api/patient/auth/login', json={'phone': payload['phone'], 'password': payload['password']})
            self.assertEqual(patient.get('/api/patient/plans').status_code, 200)
            updated = copy.deepcopy(payload)
            updated['currentPassword'] = payload['password']
            updated['password'] = 'changed-pass-123'
            updated['bundle']['visits'][0]['recordKey'] = 'another-visit'
            self.assertEqual(self.client.post('/api/demo-patients/import', json=updated).status_code, 200)
            self.assertEqual(patient.get('/api/patient/plans').status_code, 401)
            self.assertEqual(patient.post('/api/patient/auth/login', json={
                'phone': payload['phone'], 'password': updated['password']}).status_code, 200)
            self.assertEqual(len(patient.get('/api/patient/plans').json()), 2)

    def test_owner_and_hospital_boundaries(self):
        self.assertEqual(self.client.get('/api/demo-patients/import-template').status_code, 401)
        me = self.register()
        payload = self.payload()
        payload['bundle']['hospitalName'] = '其他医院'
        self.assertEqual(self.client.post('/api/demo-patients/import', json=payload).status_code, 400)
        with self.app.state.session_factory() as db:
            owner = db.scalar(select(HospitalAdmin))
            owner.is_owner = False
            db.commit()
        self.assertEqual(self.client.get('/api/demo-patients/import-template').status_code, 403)
        self.assertEqual(self.client.post('/api/demo-patients/import', json=payload).status_code, 403)

    def test_json_rejects_account_fields_duplicate_keys_and_invalid_dates(self):
        self.register()
        payload = self.payload()
        for mutate in [
            lambda b: b.update(phone='18888888888'),
            lambda b: b['patient'].update(password='should-not-be-in-json'),
            lambda b: b['visits'].append(copy.deepcopy(b['visits'][0])),
            lambda b: b['visits'][0]['steps'][0].update(completedAt='2099-01-01T00:00:00+08:00'),
            lambda b: b['visits'][0]['steps'][0].update(startedAt='2024-01-01T00:00:00'),
        ]:
            invalid = copy.deepcopy(payload)
            mutate(invalid['bundle'])
            self.assertEqual(self.client.post('/api/demo-patients/import', json=invalid).status_code, 422)

    def test_school_bundle_uses_real_catalog_and_has_39_reports(self):
        root = Path(__file__).resolve().parents[1]
        workspace = json.loads((root / 'examples/hospitals/zijingang-campus-hospital/workspace.json').read_text(encoding='utf-8'))
        self.register(workspace=workspace)
        bundle = json.loads((root / 'examples/patients/campus-student-demo.json').read_text(encoding='utf-8'))
        result = self.client.post('/api/demo-patients/import', json={
            'phone': '18888888888', 'password': 'demo-pass-123', 'bundle': bundle})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['importedVisits'], 4)
        self.assertEqual(result.json()['importedReports'], 39)
        with self.app.state.session_factory() as db:
            user = db.scalar(select(UserInfo).where(UserInfo.phone == '18888888888'))
            plans = db.scalars(select(ExamPlan).where(ExamPlan.user_id == user.user_id)).all()
            self.assertEqual(len(plans), 4)
            self.assertEqual({p.plan_status for p in plans}, {'已完成'})
            details = db.scalars(select(PlanExecutionDetail).where(PlanExecutionDetail.plan_id.in_([p.plan_id for p in plans]))).all()
            self.assertEqual(len(details), 44)
            self.assertEqual(sum(bool(row.exam_report) for row in details), 39)

    def test_report_column_upgrade_preserves_legacy_table(self):
        # Model-created tables are already upgraded; exercise an old schema separately.
        from apps.backend.checkup_backend.database import build_engine
        engine = build_engine('sqlite:///:memory:')
        try:
            with engine.begin() as db:
                db.execute(text('CREATE TABLE plan_execution_detail (detailID VARCHAR(64) PRIMARY KEY)'))
                db.execute(text("INSERT INTO plan_execution_detail (detailID) VALUES ('old-detail')"))
            ensure_compatible_columns(engine)
            ensure_compatible_columns(engine)
            with engine.connect() as db:
                self.assertEqual(db.execute(text('SELECT detailID, examReport FROM plan_execution_detail')).one(), ('old-detail', None))
        finally:
            engine.dispose()
