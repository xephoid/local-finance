"""Fresh-directory CLI/HTTPS smoke test. Empty ledger, no external services."""
import http.client
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.parse

APP=Path(__file__).resolve().parents[1]

class SmokeTests(unittest.TestCase):
    def test_fresh_init_authenticated_https_and_no_secret_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'data'
            env={**os.environ,'FINANCE_DATA_DIR':str(root),'FINANCE_BIND':'127.0.0.1','FINANCE_TLS_SANS':'localhost,127.0.0.1'}
            init=subprocess.run([sys.executable,str(APP/'finance.py'),'init'],env=env,capture_output=True,text=True,check=True)
            password=(root/'login_password').read_text().strip()
            self.assertNotIn(password,init.stdout+init.stderr)
            with socket.socket() as probe:
                probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
            env['FINANCE_PORT']=str(port)
            context=ssl.create_default_context(cafile=str(root/'tls.crt'))
            process=subprocess.Popen([sys.executable,str(APP/'finance.py'),'serve'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            def request(method,path,body=None,headers=None):
                connection=http.client.HTTPSConnection('localhost',port,context=context,timeout=2)
                try:
                    connection.request(method,path,body=body,headers=headers or {})
                    response=connection.getresponse()
                    return response.status,dict(response.getheaders()),response.read().decode()
                finally:connection.close()
            try:
                deadline=time.monotonic()+10
                while True:
                    try:
                        status,headers,page=request('GET','/')
                        break
                    except (ConnectionRefusedError,TimeoutError,OSError):
                        if time.monotonic()>deadline:raise
                        time.sleep(.05)
                self.assertEqual(status,200)
                self.assertIn('Private Finance login',page)
                csrf=re.search(r'name="csrf" value="([^"]+)"',page).group(1)
                cookie=headers['Set-Cookie'].split(';',1)[0]
                status,headers,page=request('POST','/login',urllib.parse.urlencode({'csrf':csrf,'password':password}),{'Cookie':cookie,'Content-Type':'application/x-www-form-urlencoded','Origin':f'https://localhost:{port}'})
                self.assertEqual(status,303)
                cookie=headers['Set-Cookie'].split(';',1)[0]
                self.assertIn('Secure',headers['Set-Cookie']);self.assertIn('HttpOnly',headers['Set-Cookie'])
                for path,expected in (('/',200),('/review',200),('/ask',200),('/static/finance.css',200),('/static/materialize.min.css',200),('/data/login_password',404),('/finance.py',404)):
                    status,headers,page=request('GET',path,headers={'Cookie':cookie})
                    self.assertEqual(status,expected,path)
                    self.assertNotIn(password,page)
                status=subprocess.run([sys.executable,str(APP/'finance.py'),'status'],env=env,capture_output=True,text=True,check=True)
                self.assertIn('"sync_state": "never"',status.stdout)
            finally:
                process.terminate()
                stdout,stderr=process.communicate(timeout=5)
                self.assertNotIn(password,stdout+stderr)
                self.assertEqual(stderr,'')

if __name__=='__main__':unittest.main()
