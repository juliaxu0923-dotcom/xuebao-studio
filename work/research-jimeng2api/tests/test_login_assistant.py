"""Login must tolerate a slow page without publishing a blank QR screenshot."""
import threading
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from playwright.sync_api import Error as BrowserError, TimeoutError as BrowserTimeout

from app import login_assistant as login
from app import remote_browser, verification


def test_navigation_timeout_keeps_an_already_usable_login_page():
    page = Mock()
    page.goto.side_effect = BrowserTimeout('DOMContentLoaded delayed')
    button = page.get_by_role.return_value.first
    button.is_visible.return_value = True
    login._open_login_page(page, login.LoginSession(id='test'))
    assert page.goto.call_count == 1
    button.wait_for.assert_called_once()


def test_initial_network_failure_is_retried():
    page = Mock()
    page.goto.side_effect = [BrowserError('net::ERR_CONNECTION_RESET'), None]
    page.get_by_role.return_value.first.is_visible.return_value = False
    login._open_login_page(page, login.LoginSession(id='test'))
    assert page.goto.call_count == 2


def test_network_failure_does_not_leak_browser_diagnostics():
    page = Mock()
    page.goto.side_effect = BrowserError('private-proxy-password')
    page.get_by_role.return_value.first.is_visible.return_value = False
    with pytest.raises(login.LoginUnavailable) as error:
        login._open_login_page(page, login.LoginSession(id='test'))
    assert 'private-proxy-password' not in str(error.value)


def test_cancelled_login_does_not_open_more_pages():
    session = login.LoginSession(id='test')
    session.stop.set()
    page = Mock()
    with pytest.raises(login.LoginCancelled):
        login._open_login_page(page, session)
    page.goto.assert_not_called()


def test_blank_popup_is_not_returned_as_a_qr_code():
    page = Mock()
    page.main_frame = page
    page.frames = [page]
    page.locator.return_value.count.return_value = 0
    assert login._qr_png(page) == b''
    page.screenshot.assert_not_called()


def test_unloaded_qr_image_is_not_published():
    page = Mock()
    page.main_frame = page
    page.frames = [page]
    page.locator.return_value.count.return_value = 1
    element = page.locator.return_value.nth.return_value
    element.bounding_box.return_value = {'width': 200, 'height': 200}
    element.evaluate.return_value = False
    assert login._qr_png(page) == b''
    element.screenshot.assert_not_called()
    element.evaluate.return_value = True
    element.screenshot.return_value = b'loaded-qr-image'
    assert login._qr_png(page) == b'loaded-qr-image'


def test_qr_load_is_polled_instead_of_using_a_fixed_delay(monkeypatch):
    page = Mock()
    page.is_closed.return_value = False
    qr = Mock(side_effect=[b'', b'', b'loaded-qr-image'])
    monkeypatch.setattr(login, '_qr_png', qr)
    assert login._wait_for_qr(page, login.LoginSession(id='test')) == b'loaded-qr-image'
    assert page.wait_for_timeout.call_count == 2


def test_qr_wait_can_be_cancelled(monkeypatch):
    session = login.LoginSession(id='test')
    page = Mock()
    page.is_closed.return_value = False
    page.wait_for_timeout.side_effect = lambda _: session.stop.set()
    monkeypatch.setattr(login, '_qr_png', lambda _: b'')
    with pytest.raises(login.LoginCancelled):
        login._wait_for_qr(page, session)


def test_browser_uses_the_api_proxy_without_credentials_in_server_url(monkeypatch):
    monkeypatch.setattr(login, 'settings', SimpleNamespace(http_proxy='http://user:p%40ss@proxy.example:3128'))
    p = Mock()
    login._launch(p)
    assert p.chromium.launch.call_args.kwargs['proxy'] == {
        'server': 'http://proxy.example:3128', 'username': 'user', 'password': 'p@ss'}


def test_session_cookie_is_scoped_to_jimeng():
    context = Mock()
    context.cookies.return_value = [{'name': 'sessionid', 'value': 'jimeng-session'},
                                    {'name': 'sid_guard', 'value': 'x|1700000000|86400|x'}]
    assert login._sid_of(context) == ('jimeng-session', 1700086400)
    context.cookies.assert_called_once_with([login.HOME])


def test_browser_slot_is_shared_until_chromium_closes(client, auth, monkeypatch):
    closing = threading.Event()
    finish_close = threading.Event()

    def close_browser():
        closing.set()
        finish_close.wait(30)

    browser = Mock()
    browser.close.side_effect = close_browser
    monkeypatch.setattr('playwright.sync_api.sync_playwright', lambda: nullcontext())
    monkeypatch.setattr(remote_browser, '_launch', lambda _: browser)
    monkeypatch.setattr(remote_browser.RemoteSession, '_loop',
                        lambda session, *_: session.stop.wait(30))
    monkeypatch.setattr(remote_browser, '_SESSION', None)
    monkeypatch.setattr(verification, '_sessions', {})
    monkeypatch.setattr(verification, 'pending', lambda _: {'id': 'test', 'pending': True})
    monkeypatch.setattr(verification.repo, 'get_account', lambda account_id: {'id': account_id})
    monkeypatch.setattr(verification.repo, 'account_secret', lambda _: 'test-account')

    remote = remote_browser.attach()
    verification_path = '/panel/api/accounts/1/verification'
    try:
        qr = client.post('/panel/api/login/qr/start', headers=auth, json={})
        assert qr.status_code == 409
        assert '即梦浏览器正在使用' in qr.json()['error']['message']
        assert client.post(f'{verification_path}/start', headers=auth,
                           json={'purpose': 'account_page'}).status_code == 409

        remote_browser.stop()
        assert closing.wait(5)
        ticket = remote_browser.issue_ticket()
        with client.websocket_connect('/panel/ws/remote?ticket=' + ticket) as ws:
            assert ws.receive_json()['type'] == 'error'
        assert client.post('/panel/api/login/qr/start', headers=auth, json={}).status_code == 409
    finally:
        remote_browser.stop()
        finish_close.set()
        remote.thread.join(5)

    response = client.post(f'{verification_path}/start', headers=auth,
                           json={'purpose': 'account_page'})
    assert response.status_code == 200
    session_id = response.json()['session_id']
    entry = verification.session(1, session_id)
    try:
        assert entry.verification_account_id == 1
    finally:
        verification.close(1, session_id)
        entry.thread.join(5)
