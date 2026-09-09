import base64
import json
import random
import unittest
from io import BytesIO

from PIL import Image
from sqlalchemy import select

from apps.backend.checkup_backend.patient_images import patient_image
from apps.backend.checkup_backend.models import UserInfo, HospitalSettings
from tests.test_patient_import import PatientImportTest


class PatientImageTest(PatientImportTest):
    def test_large_images_do_not_inflate_login_and_plan_responses(self):
        self.register()
        payload = self.payload()
        self.client.post('/api/demo-patients/import', json=payload).raise_for_status()
        output = BytesIO()
        Image.frombytes('RGB', (512, 512), random.Random(7).randbytes(512*512*3)).save(output, 'PNG')
        original = 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode()
        self.assertGreater(len(original), 1_048_576)
        with self.app.state.session_factory() as db:
            user = db.scalar(select(UserInfo).where(UserInfo.phone == payload['phone']))
            user.avatar_url = original
            db.scalar(select(HospitalSettings)).cover_image_url = original
            db.commit()
        login = self.client.post('/api/patient/auth/login', json={
            'phone': payload['phone'], 'password': payload['password']})
        self.assertEqual(login.status_code, 200)
        self.assertLess(len(login.content), 20_000)
        headers = {'Authorization': 'Bearer ' + login.json()['token']}
        plans = self.client.get('/api/patient/plans', headers=headers)
        self.assertEqual(plans.status_code, 200)
        self.assertLess(len(plans.content), 30_000)
        self.assertTrue(plans.json()[0]['steps'][0]['reportAvailable'])
        self.assertLess(len(json.dumps(plans.json()*12).encode()), 500_000)
        with self.app.state.session_factory() as db:
            self.assertEqual(db.scalar(select(HospitalSettings)).cover_image_url, original)
        self.assertEqual(patient_image('https://example.com/image.png'), 'https://example.com/image.png')
        self.assertIsNone(patient_image('data:image/png;base64,invalid'))


if __name__ == '__main__':
    unittest.main()
