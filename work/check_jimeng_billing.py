"""Verify Studio billing against simulated receipts without spending provider credits."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID

from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent / 'yingliu-studio'))


with tempfile.TemporaryDirectory(prefix='xuebao-billing-') as folder:
    os.environ.update(APP_DATA_DIR=folder, ADMIN_PASSWORD='billing-test-password')
    from backend import jimeng, main

    with patch.object(main, 'run_jimeng_billing_worker', AsyncMock()), TestClient(main.app) as client, \
         patch.object(main, 'launch_job'):
        client.post('/api/auth/login', json={'username': 'admin', 'password': 'billing-test-password'}).raise_for_status()
        studio_models = client.get('/api/models').json()
        jimeng_model = next(model for model in studio_models if model['model_code'] == 'jimeng-video')
        demo = next(model for model in studio_models if model['model_code'] == 'demo-image')
        assert jimeng_model['billing_mode'] == 'jimeng_actual'
        assert not any(row['model'] == 'jimeng-video' for row in
                       client.get('/api/settings').json()['data']['modelChannel']['modelCosts'])
        user_id = client.get('/api/me').json()['id']
        credit_path = f'/api/admin/users/{user_id}/credits'
        with main.db() as conn:
            conn.execute('UPDATE users SET points=20 WHERE id=?', (user_id,))
            conn.execute('UPDATE models SET cost=5 WHERE id=?', (demo['id'],))

        def balance():
            return client.get('/api/me').json()['points']

        def create(model=jimeng_model):
            return client.post('/api/jobs', json={'model_id': model['id'], 'prompt': 'billing test'})

        def job(record_id):
            result = client.get(f'/api/jobs/{record_id}')
            result.raise_for_status()
            return result.json()

        def reconcile(amounts):
            # Move the clock to each task's next scheduled attempt without sleeping.
            with main.db() as conn:
                conn.execute('UPDATE jobs SET billing_check_at=0')
            lookup = AsyncMock(side_effect=amounts)
            with patch.object(jimeng, 'request_gateway', lookup):
                asyncio.run(main.reconcile_jimeng_charges())
            return lookup

        first = create().json()
        second = create().json()
        assert (first['cost'], first['billing_status'], balance()) == (0, 'pending', 20)
        assert create().status_code == 402
        assert create(demo).status_code == 402

        async def gateway(method, path, payload=None, *, idempotency_key=None):
            if path == '/panel/api/accounts':
                return {'items': [{'enabled': True, 'state': 'ok'}]}
            task_id = str(UUID(first['id']))
            if method == 'POST':
                assert idempotency_key == task_id
                assert job(first['id'])['provider_task_id'] == task_id
                return {'task_id': task_id}
            assert path.endswith(task_id)
            return {'status': 'succeeded', 'data': [{'url': 'https://example.test/verified.mp4'}]}

        with patch.object(jimeng, 'request_gateway', gateway), patch(
            'backend.video_delivery.store_video', AsyncMock(return_value='/api/media/verified.mp4')
        ), patch.object(main, 'preserve_generated_media', AsyncMock(return_value='/api/media/verified.mp4')):
            asyncio.run(main.execute_job(first['id']))

        output = job(first['id'])
        assert (output['status'], output['result_url'], output['billing_status'], balance()) == (
            'completed', '/api/media/verified.mp4', 'pending', 20)
        path = f"/v1/tasks/{output['provider_task_id']}/charge"
        reconcile([{'amount': None}]).assert_awaited_once_with('GET', path)
        reconcile([HTTPException(503, 'temporarily unavailable')]).assert_awaited_once_with('GET', path)
        assert (job(first['id'])['billing_status'], balance()) == ('pending', 20)
        reconcile([{'amount': 24}]).assert_awaited_once_with('GET', path)
        reconcile([]).assert_not_awaited()
        assert (job(first['id'])['charged_points'], job(first['id'])['billing_status'], balance()) == (24, 'settled', -4)
        main.settle_jimeng_charge(first['id'], 24)
        assert balance() == -4 and create().status_code == 402

        # A provider-side failure can still have a verified charge.
        second_provider_id = str(UUID(second['id']))
        with main.db() as conn:
            conn.execute('UPDATE jobs SET provider_task_id=? WHERE id=?', (second_provider_id, second['id']))
        failed = AsyncMock(return_value={'status': 'failure', 'error': {'message': '上游任务失败'}})
        with patch.object(jimeng, 'request_gateway', failed):
            asyncio.run(main.execute_job(second['id']))
        assert failed.await_args_list and all(call.args == ('GET', f'{jimeng.GENERATION_PATH}/{second_provider_id}')
                                             for call in failed.await_args_list)
        reconcile([{'amount': 3}])
        assert (job(second['id'])['status'], job(second['id'])['charged_points'], balance()) == ('failed', 3, -7)
        client.post(credit_path, json={'amount': 2}).raise_for_status()
        assert balance() == -5
        client.post(credit_path, json={'amount': 20}).raise_for_status()
        assert balance() == 15

        free = create().json()['id']
        with main.db() as conn:
            conn.execute('UPDATE jobs SET provider_task_id=? WHERE id=?', (str(UUID(free)), free))
        main.fail_job(free, '上游任务失败')
        reconcile([{'amount': 0}])
        assert (job(free)['billing_status'], job(free)['charged_points'], balance()) == ('settled', 0, 15)

        never_sent = create().json()['id']
        main.fail_job(never_sent, '参考素材不可用')
        assert (job(never_sent)['billing_status'], job(never_sent)['charged_points'], balance()) == ('settled', 0, 15)
        fixed = create(demo).json()['id']
        assert (job(fixed)['charged_points'], job(fixed)['billing_status'], balance()) == (5, '', 10)
        main.fail_job(fixed, '生成失败')
        main.fail_job(fixed, '重复写入失败')
        assert balance() == 15

    with TestClient(main.app) as client:
        client.post('/api/auth/login', json={'username': 'admin', 'password': 'billing-test-password'}).raise_for_status()
        assert (job(first['id'])['charged_points'], balance()) == (24, 15)

print('Studio billing passed for delayed receipts, actual charges, zero, failures, credit adjustments and restart.')
