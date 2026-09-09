import json
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from tests.test_patient_import import PatientImportTest


class AgentRecordsTest(unittest.TestCase):
    setUp = PatientImportTest.setUp
    tearDown = PatientImportTest.tearDown
    register = PatientImportTest.register
    registration_workspace = PatientImportTest.registration_workspace
    payload = PatientImportTest.payload

    def test_records_are_owned_and_sent_to_model(self):
        self.register()
        payload = self.payload()
        self.client.post('/api/demo-patients/import', json=payload).raise_for_status()
        with TestClient(self.app) as patient:
            self.assertEqual(patient.get('/api/patient/agent/records').status_code, 401)
            patient.post('/api/patient/auth/login', json={'phone': payload['phone'], 'password': payload['password']}).raise_for_status()
            records = patient.get('/api/patient/agent/records').json()
            self.assertTrue(records['visits'][0]['simulated'])
            self.assertTrue(records['visits'][0]['exams'][0]['report'])
            self.assertNotIn(payload['phone'], json.dumps(records))
            with patch('apps.backend.checkup_backend.agent_api._post_chatanywhere', return_value={'choices':[{'message':{'content':'报告摘要'}}]}) as upstream:
                response = patient.post('/api/patient/agent/jobs', json={'requestID':'records-test-001', 'apiKey':'test-only-key', 'messages':[{'role':'user','content':'总结历史体检报告'}]})
                self.assertEqual(response.status_code, 202)
                messages = upstream.call_args.args[2]['messages']
                self.assertIn('exams', messages[1]['content'])
                self.assertIn('report', messages[1]['content'])
        with TestClient(self.app) as other:
            other.post('/api/patient/auth/register', json={'phone':'13900000999','password':'patient-pass-123','name':'其他患者', 'privacyConsent':True, 'privacyConsentVersion':'v0.3.1-2026-08-31'}).raise_for_status()
            self.assertEqual(other.get('/api/patient/agent/records').json()['visits'], [])
