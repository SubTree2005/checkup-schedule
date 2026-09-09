import os
import io
import json
import unittest
from datetime import timedelta
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests import test_backend_api as fixture
from apps.backend.checkup_backend.agent_api import _post_chatanywhere, _run_agent_job
from apps.backend.checkup_backend.models import PatientAgentJob, utcnow


class AgentJobsTest(unittest.TestCase):
    setUp = fixture.BackendAPITest.setUp
    tearDown = fixture.BackendAPITest.tearDown

    def test_upstream_http_request_keeps_correct_request_type(self):
        raw = io.BytesIO(b'{"choices":[{"message":{"content":"OK"}}]}')
        raw.headers = {}
        with patch('apps.backend.checkup_backend.agent_api.urlopen', return_value=raw) as send:
            result = _post_chatanywhere('https://example.test/v1/chat/completions', 'test-key',
                {'model': 'deepseek-v4-flash', 'messages': [{'role': 'user', 'content': 'hello'}]})
            request = send.call_args.args[0]
            self.assertEqual(request.get_method(), 'POST')
            self.assertEqual(request.get_header('Authorization'), 'Bearer test-key')
            self.assertEqual(json.loads(request.data)['model'], 'deepseek-v4-flash')
            self.assertEqual(result['choices'][0]['message']['content'], 'OK')

    def login(self, client=None, phone='13900000881'):
        client = client or self.client
        response = client.post('/api/patient/auth/register', json={
            'phone': phone, 'password': 'patient-pass-123', 'name': '异步测试',
            'privacyConsent': True, 'privacyConsentVersion': 'v0.3.1-2026-08-31'})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def submit(self, request_id='request-test-001'):
        return self.client.post('/api/patient/agent/jobs', json={
            'requestID': request_id, 'messages': [{'role': 'user', 'content': 'hello'}]})

    def test_submission_returns_before_generation_and_deduplicates(self):
        self.login()
        with patch.dict(os.environ, {'CHATANYWHERE_API_KEY': 'test-key'}), patch(
            'fastapi.BackgroundTasks.add_task') as schedule, patch(
            'apps.backend.checkup_backend.agent_api._post_chatanywhere',
            return_value={'choices': [{'message': {'content': '回答完成'}}]}) as upstream:
            response = self.submit()
            self.assertEqual(response.status_code, 202, response.text)
            job_id = response.json()['jobID']
            self.assertEqual(response.json()['status'], 'pending')
            upstream.assert_not_called()
            duplicate = self.submit()
            self.assertEqual(duplicate.json()['jobID'], job_id)
            self.assertEqual(schedule.call_count, 1)
            self.assertEqual(self.submit('another-request').status_code, 409)
            _, *args = schedule.call_args.args
            _run_agent_job(*args)
            result = self.client.get('/api/patient/agent/jobs/' + job_id)
            self.assertEqual(result.json()['reply'], '回答完成')
            self.assertEqual(self.submit().json()['status'], 'completed')
            self.assertEqual(upstream.call_count, 1)
            with self.app.state.session_factory() as db:
                row = db.get(PatientAgentJob, job_id)
                self.assertFalse(hasattr(row, 'api_key'))
                self.assertFalse(hasattr(row, 'messages'))

    def test_jobs_are_patient_scoped_and_shared_between_instances(self):
        identity = self.login()
        with patch.dict(os.environ, {'CHATANYWHERE_API_KEY': 'test-key'}), patch('fastapi.BackgroundTasks.add_task'):
            job_id = self.submit().json()['jobID']
        with TestClient(self.app) as other:
            self.login(other, '13900000882')
            self.assertEqual(other.get('/api/patient/agent/jobs/' + job_id).status_code, 404)
            self.assertEqual(other.delete('/api/patient/agent/jobs/' + job_id).status_code, 404)
        from apps.backend.checkup_backend.main import create_app
        second = create_app(database_url=f'sqlite:///{self.temp_dir.name}/test.db')
        with TestClient(second) as another_instance:
            result = another_instance.get('/api/patient/agent/jobs/' + job_id,
                headers={'Authorization': 'Bearer ' + identity['token']})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['status'], 'pending')

    def test_cancel_and_expiry_win_over_late_results(self):
        self.login()
        with patch.dict(os.environ, {'CHATANYWHERE_API_KEY': 'test-key'}), patch(
            'fastapi.BackgroundTasks.add_task') as schedule:
            job_id = self.submit().json()['jobID']
            _, *args = schedule.call_args.args
            def late_reply(*unused):
                self.assertEqual(self.client.delete('/api/patient/agent/jobs/' + job_id).status_code, 204)
                return {'reply': '不能写入', 'model': 'test'}
            with patch('apps.backend.checkup_backend.agent_api.chat_with_patient_agent', side_effect=late_reply):
                _run_agent_job(*args)
            result = self.client.get('/api/patient/agent/jobs/' + job_id).json()
            self.assertEqual(result['status'], 'cancelled')
            self.assertNotIn('reply', result)
            new_id = self.submit('replacement-job').json()['jobID']
            with self.app.state.session_factory() as db:
                db.get(PatientAgentJob, new_id).created_at = utcnow() - timedelta(seconds=111)
                db.commit()
            self.assertEqual(self.client.get('/api/patient/agent/jobs/' + new_id).json()['status'], 'failed')
            with self.app.state.session_factory() as db:
                db.get(PatientAgentJob, job_id).expires_at = utcnow() - timedelta(seconds=1)
                db.commit()
            self.assertEqual(self.client.get('/api/patient/agent/jobs/' + job_id).status_code, 404)

    def test_worker_errors_and_missing_configuration_are_actionable(self):
        self.assertEqual(self.submit().status_code, 401)
        self.login()
        with patch.dict(os.environ, {'CHATANYWHERE_API_KEY': ''}):
            self.assertEqual(self.submit().status_code, 503)
        with patch.dict(os.environ, {'CHATANYWHERE_API_KEY': 'test-key'}), patch(
            'fastapi.BackgroundTasks.add_task') as schedule:
            job_id = self.submit().json()['jobID']
            _, *args = schedule.call_args.args
            with patch('apps.backend.checkup_backend.agent_api.chat_with_patient_agent',
                       side_effect=HTTPException(502, 'AI 服务请求失败（429）')):
                _run_agent_job(*args)
            result = self.client.get('/api/patient/agent/jobs/' + job_id).json()
            self.assertEqual(result['status'], 'failed')
            self.assertIn('429', result['error'])

    def test_account_deletion_removes_jobs(self):
        self.login()
        with patch.dict(os.environ, {'CHATANYWHERE_API_KEY': 'test-key'}), patch('fastapi.BackgroundTasks.add_task'):
            job_id = self.submit().json()['jobID']
        result = self.client.request('DELETE', '/api/patient/account', json={'password': 'patient-pass-123'})
        self.assertEqual(result.status_code, 204, result.text)
        with self.app.state.session_factory() as db:
            self.assertIsNone(db.get(PatientAgentJob, job_id))
