"""Local assets and accessible finance UI; synthetic stores only."""
import http.client
from html.parser import HTMLParser
import pathlib
import ssl
import tempfile
import threading
import unittest
import finance

APP = pathlib.Path(finance.__file__).parent

class UIParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.selects = []
        self.checkboxes = []
        self.tables = []
        self.sections = 0
        self.expect_span = False
        self.checkbox_spans = 0
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'select':
            self.selects.append((attrs, 'label' in self.stack))
        if tag == 'input' and attrs.get('type') == 'checkbox':
            self.checkboxes.append('label' in self.stack)
            self.expect_span = True
        elif self.expect_span:
            if tag == 'span': self.checkbox_spans += 1
            self.expect_span = False
        if tag == 'table': self.tables.append('div' in self.stack)
        if tag == 'section': self.sections += 1
        if tag not in ('input', 'meta', 'link', 'br', 'hr'):
            self.stack.append(tag)
    def handle_endtag(self, tag):
        if tag in self.stack:
            self.stack = self.stack[:len(self.stack)-1-self.stack[::-1].index(tag)]

class UITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = finance.Store(self.tmp.name)

    def server(self):
        finance.initialize(self.store)
        server = finance.make_server(self.store, '127.0.0.1', 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def get(self, server, path, host=None):
        conn = http.client.HTTPSConnection('127.0.0.1', server.server_port,
            context=ssl._create_unverified_context(), timeout=3)
        try:
            conn.request('GET', path, headers={'Host':host} if host else {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_only_allowlisted_public_css_is_served_without_session(self):
        server = self.server()
        for name in ('materialize.min.css', 'finance.css'):
            with self.subTest(name=name):
                status, headers, body = self.get(server, '/static/' + name)
                self.assertEqual(status, 200)
                self.assertEqual(headers['Content-Type'], 'text/css; charset=utf-8')
                self.assertEqual(body, (APP / 'static' / name).read_bytes())
                self.assertNotIn('Set-Cookie', headers)
                self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        for path in ('/static/../finance.py', '/static/%2e%2e/finance.py',
                     '/static/%66inance.css', '/static/finance.css/extra',
                     '/static//finance.css', '/static/LICENSE.materialize',
                     '/private/auth.json', '/finance.py'):
            with self.subTest(path=path):
                self.assertEqual(self.get(server, path)[0], 404)
        self.assertEqual(self.get(server, '/static/finance.css', 'example.com')[0], 403)

    def test_html_links_local_styles_and_preserves_strict_security(self):
        status, headers, body = self.get(self.server(), '/')
        page = body.decode()
        self.assertEqual(status, 200)
        self.assertIn('<html lang="en">', page)
        self.assertIn('<main', page)
        self.assertIn('login-card', page)
        self.assertIn('href="/static/materialize.min.css"', page)
        self.assertIn('href="/static/finance.css"', page)
        policy = headers['Content-Security-Policy']
        for directive in ("default-src 'none'", "style-src 'self'", "form-action 'self'", "frame-ancestors 'none'", "base-uri 'none'"):
            self.assertIn(directive, policy)
        self.assertNotIn('unsafe-inline', policy)
        self.assertEqual(headers['Referrer-Policy'], 'same-origin')
        self.assertNotIn('<style', page)
        self.assertNotIn('<script', page)
        self.assertNotIn('https://', page)

    def test_dashboard_native_controls_scroll_tables_and_readable_sections(self):
        self.store.ingest({'accounts':[{'id':'synthetic', 'name':'Synthetic account',
            'currency':'USD', 'balance':'1200.00', 'transactions':[{'id':'synthetic-t',
            'posted':1700000000, 'amount':'-12.34', 'description':'Synthetic merchant'}]}]})
        page = finance.dashboard(self.store, 'synthetic-csrf')
        parser = UIParser()
        parser.feed(page)
        self.assertGreaterEqual(len(parser.selects), 5)
        for attrs, labeled in parser.selects:
            self.assertIn('browser-default', attrs.get('class', '').split())
            self.assertTrue(labeled, 'select must have visible enclosing label')
        self.assertEqual(len(parser.checkboxes), 4)
        self.assertTrue(all(parser.checkboxes))
        self.assertEqual(parser.checkbox_spans, len(parser.checkboxes))
        self.assertTrue(parser.tables)
        self.assertTrue(all(parser.tables), 'tables need horizontal scroll containers')
        self.assertGreaterEqual(parser.sections, 8)
        self.assertIn('USD -12.34', page)
        css = (APP / 'static' / 'finance.css').read_text()
        for feature in ('overflow-x: auto', 'tabular-nums', ':focus-visible', '@media',
                        '.login-card', 'max-width', 'input[type="checkbox"]'):
            self.assertIn(feature, css)
        for name in ('finance.css', 'materialize.min.css'):
            source = (APP / 'static' / name).read_text()
            self.assertNotIn('@import', source)
            self.assertNotIn('url(', source.lower())
        self.assertIn('MIT', (APP / 'static' / 'LICENSE.materialize').read_text())

if __name__ == '__main__':
    unittest.main()
