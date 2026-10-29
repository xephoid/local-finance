"""Synthetic-only notification tests; no private files or network."""
import contextlib
import io
import pathlib
import tempfile
import unittest
from unittest import mock

import finance
import notify


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict('os.environ', {'TELEGRAM_CHAT_ID':'123456'})
        env.start();self.addCleanup(env.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = finance.Store(self.tmp.name)

    def pending(self, message=finance.GENERIC_NOTICE):
        with self.store.connect() as db:
            return db.execute('INSERT INTO outbox(message) VALUES(?)', (message,)).lastrowid

    def flags(self):
        return self.store.rows('SELECT id,delivered FROM outbox ORDER BY id')

    def good(self):
        return {'ok': True, 'result': {'chat': {'id': 123456}, 'message_id': 23}}

    def test_coalesce_ack_only_after_confirmation_exact_snapshot(self):
        first, second = self.pending(), self.pending()
        calls = []
        def send(token, text):
            calls.append((token, text))
            self.assertEqual([r['delivered'] for r in self.flags()], [0, 0])
            self.pending()  # Arrived during send: must remain pending.
            return self.good()
        self.assertEqual(notify.deliver(self.store.dbpath, sender=send,
                                       token_loader=lambda: '123:synthetic'), 'sent')
        self.assertEqual(calls, [('123:synthetic', finance.GENERIC_NOTICE)])
        self.assertEqual(self.flags(), [{'id': first, 'delivered': 1},
                                       {'id': second, 'delivered': 1},
                                       {'id': second + 1, 'delivered': 0}])

    def test_any_non_generic_message_blocks_entire_batch(self):
        self.pending()
        self.pending('synthetic confidential text')
        sender = mock.Mock(return_value=self.good())
        self.assertEqual(notify.deliver(self.store.dbpath, sender=sender,
                                       token_loader=lambda: '123:synthetic'), 'failed')
        sender.assert_not_called()
        self.assertEqual([r['delivered'] for r in self.flags()], [0, 0])

    def test_invalid_confirmation_never_acknowledged(self):
        self.pending()
        responses = [None, {}, {'ok': False}, {'ok': 1, 'result': self.good()['result']},
                     {'ok': True, 'result': {'chat': {'id': 7}, 'message_id': 23}},
                     {'ok': True, 'result': {'chat': {'id': 123456}, 'message_id': 0}},
                     {'ok': True, 'result': {'chat': {'id': 123456}, 'message_id': True}},
                     {'ok': True, 'result': {'chat': {'id': '123456'}, 'message_id': 23}}]
        for response in responses:
            with self.subTest(response=response):
                self.assertEqual(notify.deliver(self.store.dbpath,
                    sender=lambda *args: response, token_loader=lambda: '123:synthetic'), 'failed')
                self.assertEqual(self.flags()[0]['delivered'], 0)

    def test_send_failure_is_sanitized_and_not_acknowledged(self):
        self.pending()
        sender = mock.Mock(side_effect=RuntimeError('SECRET RESPONSE TOKEN URL'))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(notify.deliver(self.store.dbpath, sender=sender,
                token_loader=lambda: '123:synthetic'), 'failed')
        self.assertEqual(out.getvalue() + err.getvalue(), '')
        self.assertEqual(self.flags()[0]['delivered'], 0)

    def test_http_429_leaves_snapshot_pending_single_attempt(self):
        self.pending()
        conn = mock.MagicMock()
        conn.getresponse.return_value.status = 429
        with mock.patch.object(notify.http.client, 'HTTPSConnection', return_value=conn):
            self.assertEqual(notify.deliver(self.store.dbpath,
                token_loader=lambda: '123:synthetic'), 'failed')
        self.assertEqual(conn.request.call_count, 1)
        self.assertEqual(self.flags()[0]['delivered'], 0)

    def test_ack_failure_rolls_back_entire_snapshot(self):
        self.pending()
        second = self.pending()
        with self.store.connect() as db:
            db.execute('CREATE TRIGGER synthetic_fail BEFORE UPDATE ON outbox WHEN OLD.id=' +
                       str(second) + " BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
        self.assertEqual(notify.deliver(self.store.dbpath, sender=lambda *a: self.good(),
                         token_loader=lambda: '123:synthetic'), 'failed')
        self.assertEqual([r['delivered'] for r in self.flags()], [0, 0])

    def test_idle_does_not_load_credentials(self):
        loader = mock.Mock(side_effect=AssertionError('must not load'))
        self.assertEqual(notify.deliver(self.store.dbpath, sender=mock.Mock(),
                                       token_loader=loader), 'idle')
        loader.assert_not_called()

    def test_cross_process_lock_prevents_send(self):
        import fcntl
        import subprocess
        import sys
        self.pending()
        with open(str(self.store.dbpath) + '.notify.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = subprocess.run([sys.executable, '-c',
                'import notify,sys; print(notify.deliver(sys.argv[1], sender=lambda *a: 1/0, token_loader=lambda: "123:synthetic"))',
                str(self.store.dbpath)], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, 'busy\n')
        self.assertEqual(self.flags()[0]['delivered'], 0)

    def test_status_never_sends_or_loads_secrets(self):
        self.pending()
        sender, loader = mock.Mock(), mock.Mock()
        self.assertEqual(notify.deliver(self.store.dbpath, sender=sender,
            token_loader=loader, status_only=True), 'pending')
        sender.assert_not_called()
        loader.assert_not_called()

    def test_cli_silent_success_generic_failure_status_only(self):
        for state, status, code, stdout, stderr in [
            ('sent', False, 0, '', ''), ('idle', False, 0, '', ''),
            ('busy', False, 0, '', ''), ('failed', False, 1, '', 'Notification delivery deferred.\n'),
            ('pending', True, 0, 'Notification state: pending\n', '')]:
            with self.subTest(state=state):
                out, err = io.StringIO(), io.StringIO()
                with mock.patch.object(notify, 'deliver', return_value=state), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    self.assertEqual(notify.main(['--status'] if status else []), code)
                self.assertEqual((out.getvalue(), err.getvalue()), (stdout, stderr))

    def test_missing_database_fails_without_creation(self):
        path = pathlib.Path(self.tmp.name) / 'absent.sqlite3'
        self.assertEqual(notify.deliver(path, sender=mock.Mock(), token_loader=mock.Mock()), 'failed')
        self.assertFalse(path.exists())


class CredentialTests(unittest.TestCase):
    def test_env_precedence_no_file_read(self):
        with mock.patch.dict('os.environ', {'TELEGRAM_BOT_TOKEN': '123:synthetic'}, clear=True):
            self.assertEqual(notify.load_token('/not/read'), '123:synthetic')

    def test_safe_quoted_file_only_needed_key_no_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'synthetic.env'
            path.write_text('OTHER=\"unterminated\nexport TELEGRAM_BOT_TOKEN = \'123:synthetic\' # comment\n')
            with mock.patch.dict('os.environ', {}, clear=True):
                self.assertEqual(notify.load_token(path), '123:synthetic')
                for bad in ['$(echo leak)', '123:synthetic;echo', '123:synthetic extra', '']:
                    path.write_text('TELEGRAM_BOT_TOKEN=' + bad + '\n')
                    with self.assertRaisesRegex(ValueError, '^Notification configuration unavailable$'):
                        notify.load_token(path)


class TransportTests(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict('os.environ', {'TELEGRAM_CHAT_ID':'123456'})
        env.start();self.addCleanup(env.stop)
    def test_fixed_https_endpoint_body_no_redirects_or_proxies(self):
        import json
        conn = mock.MagicMock()
        conn.getresponse.return_value.status = 200
        conn.getresponse.return_value.read.return_value = b'{"ok":true}'
        with mock.patch.object(notify.http.client, 'HTTPSConnection', return_value=conn) as factory:
            self.assertEqual(notify.send_telegram('123:synthetic', finance.GENERIC_NOTICE), {'ok': True})
        self.assertEqual(factory.call_args.args, ('api.telegram.org',))
        args, kwargs = conn.request.call_args
        self.assertEqual(args[:2], ('POST', '/bot123:synthetic/sendMessage'))
        self.assertEqual(json.loads(kwargs['body']), {'chat_id': 123456, 'text': finance.GENERIC_NOTICE})
        conn.close.assert_called_once()

    def test_http_429_redirects_and_malformed_json_fail_safely(self):
        for status, body in [(429, b'SECRET'), (302, b'SECRET'), (200, b'SECRET')]:
            conn = mock.MagicMock()
            conn.getresponse.return_value.status = status
            conn.getresponse.return_value.read.return_value = body
            with mock.patch.object(notify.http.client, 'HTTPSConnection', return_value=conn):
                with self.assertRaisesRegex(ValueError, '^Notification transport unavailable$'):
                    notify.send_telegram('123:synthetic', finance.GENERIC_NOTICE)
            self.assertEqual(conn.request.call_count, 1)
            conn.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
