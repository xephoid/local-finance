#!/usr/bin/env python3
"""Generic-only, one-shot outbox delivery; suitable for cron.

At-least-once: a crash after Telegram accepts a message but before SQLite commits
can duplicate that generic notice on the next run. Never acknowledge before the
provider confirms delivery. All cooperating invocations use the same file lock.
No model, shell, redirects, environment proxies, or background process is used.
"""
import argparse
import fcntl
import http.client
import json
import os
import re
import shlex
import sqlite3
import sys
from pathlib import Path

from finance import GENERIC_NOTICE
from deployment import load_config

_TOKEN = re.compile(r'[0-9]+:[A-Za-z0-9_-]+', re.ASCII)


def load_token(env_file=None):
    """Read only the named setting; parse quotes without shell interpolation."""
    try:
        token = os.environ.get('TELEGRAM_BOT_TOKEN')
        if token is None:
            if env_file is None:raise ValueError()
            matches = []
            with open(env_file, encoding='utf-8') as source:
                for line in source:
                    match = re.match(r'^\s*(?:export\s+)?TELEGRAM_BOT_TOKEN\s*=(.*)$', line)
                    if match:
                        parts = shlex.split(match.group(1), comments=True, posix=True)
                        if len(parts) != 1:
                            raise ValueError()
                        matches.append(parts[0])
            if len(matches) != 1:
                raise ValueError()
            token = matches[0]
        if not _TOKEN.fullmatch(token):
            raise ValueError()
        return token
    except Exception:
        raise ValueError('Notification configuration unavailable') from None


def send_telegram(token, text):
    """Single HTTPS attempt. HTTP 429 remains pending until a later cron run."""
    connection = None
    try:
        if not _TOKEN.fullmatch(token) or text != GENERIC_NOTICE:
            raise ValueError()
        chat_id = load_config().telegram_chat_id
        if chat_id is None:raise ValueError()
        # http.client neither reads proxy environment variables nor follows redirects.
        connection = http.client.HTTPSConnection('api.telegram.org', timeout=30)
        body = json.dumps({'chat_id': chat_id, 'text': GENERIC_NOTICE}).encode('utf-8')
        connection.request('POST', '/bot' + token + '/sendMessage', body=body,
                           headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError()
        raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError()
        return json.loads(raw)
    except Exception:
        raise ValueError('Notification transport unavailable') from None
    finally:
        if connection is not None:
            connection.close()


def deliver(dbpath, *, sender=None, token_loader=None, status_only=False):
    """Return an allowlisted operational state; never return/log provider data."""
    db = None
    lockfd = None
    try:
        recipient = load_config().telegram_chat_id
        if not status_only and sender is None and token_loader is None and (recipient is None or not os.environ.get('TELEGRAM_BOT_TOKEN')):
            return 'disabled'
        # Resolve to make symlink aliases share the same cooperative lock.
        path = Path(dbpath).resolve(strict=True)
        lockfd = os.open(str(path) + '.notify.lock',
                         os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 'busy'
        # Do not construct Store: delivery must not initialize or query ledger tables.
        db = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=20)
        rows = db.execute('SELECT id, message FROM outbox WHERE delivered=0').fetchall()
        if not rows:
            return 'idle'
        if any(type(message) is not str or message != GENERIC_NOTICE for _, message in rows):
            return 'failed'
        if status_only:
            return 'pending'
        result = (sender or send_telegram)((token_loader or load_token)(), GENERIC_NOTICE)
        if not isinstance(result, dict) or result.get('ok') is not True:
            return 'failed'
        message = result.get('result')
        if not isinstance(message, dict) or not isinstance(message.get('chat'), dict):
            return 'failed'
        chat_id = message['chat'].get('id')
        message_id = message.get('message_id')
        if type(chat_id) is not int or recipient is None or chat_id != recipient or type(message_id) is not int or message_id <= 0:
            return 'failed'
        # One transaction, exact pre-send snapshot only; later rows stay pending.
        with db:
            db.executemany('UPDATE outbox SET delivered=1 WHERE id=? AND delivered=0',
                           [(row[0],) for row in rows])
        return 'sent'
    except Exception:
        return 'failed'
    finally:
        if db is not None:
            db.close()
        if lockfd is not None:
            os.close(lockfd)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Deliver generic notifications without an agent.')
    parser.add_argument('--data', default=str(load_config().data),
                        help='Directory containing an existing ledger.sqlite3')
    parser.add_argument('--status', action='store_true', help='Check safe state only; never send')
    args = parser.parse_args(argv)
    state = deliver(Path(args.data) / 'ledger.sqlite3', status_only=args.status)
    if state == 'failed':
        print('Notification delivery deferred.', file=sys.stderr)
        return 1
    if args.status:
        print('Notification state: ' + state)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
