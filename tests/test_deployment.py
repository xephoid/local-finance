"""Deployment tests use temporary files and synthetic transports only."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


class DeploymentTests(unittest.TestCase):
    def test_environment_config_overrides_and_safe_defaults(self):
        self.assertIsNotNone(importlib.util.find_spec('deployment'), 'deployment configuration module missing')
        import deployment
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg = deployment.load_config()
            self.assertEqual(cfg.bind, '127.0.0.1')
            self.assertEqual(cfg.ollama_url, 'http://localhost:11434')
            self.assertEqual(cfg.model, 'qwen3:8b')
            self.assertIsNone(cfg.telegram_chat_id)
            self.assertEqual(cfg.credential, cfg.data / 'simplefin_access_url')
        with mock.patch.dict(os.environ, {'FINANCE_DATA_DIR':'/synthetic/data', 'FINANCE_SIMPLEFIN_CREDENTIAL':'/synthetic/key', 'FINANCE_OLLAMA_URL':'http://10.20.30.40:11434/', 'FINANCE_OLLAMA_MODEL':'local-model', 'FINANCE_BIND':'0.0.0.0', 'FINANCE_PORT':'9443', 'FINANCE_TLS_SANS':'localhost,finance.example.test,10.20.30.41', 'TELEGRAM_CHAT_ID':'123456'}, clear=True):
            cfg = deployment.load_config()
            self.assertEqual((str(cfg.data),str(cfg.credential)),('/synthetic/data','/synthetic/key'))
            self.assertEqual((cfg.ollama_url,cfg.model),('http://10.20.30.40:11434','local-model'))
            self.assertEqual((cfg.bind,cfg.port),('0.0.0.0',9443))
            self.assertEqual(cfg.tls_sans, 'DNS:localhost,DNS:finance.example.test,IP:10.20.30.41')
            self.assertEqual(cfg.telegram_chat_id,123456)

    def test_cloud_metadata_blocks_both_prompt_paths_before_financial_request(self):
        import finance
        import assistant_features
        payload = {'question':'synthetic private question','categories':[]}
        for metadata in ({'remote_host':'https://cloud.example','details':{}}, {'remote_model':'cloud-model','details':{}}, {'details':{'family':'cloud'}}, {'capabilities':['completion','cloud']}, {}, {'details':{}}, {'details':{'family':'qwen'},'capabilities':['completion'],'model_info':{'general.architecture':'qwen'},'remote_host':None}):
            for request in (lambda: finance.model_request('synthetic private merchant'), lambda: assistant_features.plan_request(payload)):
                calls=[]
                def fetch(req, **kwargs):
                    calls.append(req)
                    return metadata if req.full_url.endswith('/api/show') else {'message':{'content':'{}'}}
                with mock.patch.object(finance,'fetch_json',side_effect=fetch):
                    with self.assertRaises(ValueError):
                        request()
                self.assertTrue(calls)
                self.assertTrue(all(r.full_url.endswith('/api/show') for r in calls))
                self.assertNotIn('synthetic private', str([r.data for r in calls]))

    def test_local_metadata_allows_configured_lan_model_for_both_paths(self):
        import finance
        import assistant_features
        calls=[]
        metadata={'details':{'family':'qwen'},'capabilities':['completion'],'model_info':{'general.architecture':'qwen'}}
        def fetch(req,**kwargs):
            calls.append(req)
            return metadata if req.full_url.endswith('/api/show') else {'message':{'content':'{}'}}
        with mock.patch.dict(os.environ,{'FINANCE_OLLAMA_URL':'http://10.20.30.40:11434','FINANCE_OLLAMA_MODEL':'fixture:local'}), mock.patch.object(finance,'fetch_json',side_effect=fetch):
            self.assertEqual(finance.model_request('fixture'),'{}')
            self.assertEqual(assistant_features.plan_request({'question':'fixture','categories':[]}),'{}')
        self.assertEqual([r.full_url for r in calls], ['http://10.20.30.40:11434/api/show','http://10.20.30.40:11434/api/chat']*2)
        self.assertTrue(all(json.loads(r.data)['model']=='fixture:local' for r in calls))

    def test_model_url_rejects_remote_or_ambiguous_destinations(self):
        import deployment
        for url in ('https://ollama.com', 'http://example.test:11434', 'http://169.254.169.254','http://8.8.8.8','http://localhost/path','http://user:pass@localhost','http://localhost?x=1','ftp://localhost','http://localhost:0'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                deployment.local_url(url)

    def test_notifications_disabled_without_explicit_token_and_recipient(self):
        import notify
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch('builtins.open', side_effect=AssertionError('No implicit secret file reads')), mock.patch.object(notify.http.client,'HTTPSConnection') as network:
            self.assertEqual(notify.deliver('/not/accessed.sqlite3'),'disabled')
            with self.assertRaises(ValueError):
                notify.load_token()
            network.assert_not_called()
        with mock.patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN':'123:synthetic'}, clear=True), mock.patch.object(notify.http.client,'HTTPSConnection') as network:
            self.assertEqual(notify.deliver('/not/accessed.sqlite3'),'disabled')
            network.assert_not_called()

    def test_cli_init_uses_config_data_and_tls_sans_without_printing_password(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'ledger'
            env={**os.environ,'FINANCE_DATA_DIR':str(root),'FINANCE_TLS_SANS':'localhost,finance.example.test,10.20.30.41'}
            run=subprocess.run([sys.executable,'finance.py','init'],env=env,capture_output=True,text=True,check=True)
            self.assertTrue((root/'login_password').exists())
            password=(root/'login_password').read_text().strip()
            self.assertNotIn(password,run.stdout+run.stderr)
            self.assertEqual(root.stat().st_mode & 0o777,0o700)
            for name in ('auth.json','login_password','tls.key','tls.crt','ledger.sqlite3'):
                self.assertEqual((root/name).stat().st_mode & 0o777,0o600)
            cert=subprocess.run(['openssl','x509','-in',str(root/'tls.crt'),'-noout','-ext','subjectAltName'],capture_output=True,text=True,check=True).stdout
            self.assertIn('DNS:finance.example.test',cert)
            self.assertIn('IP Address:10.20.30.41',cert)

    def test_supervisor_uses_configured_paths_bind_port_and_no_automatic_network_jobs(self):
        import supervise
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ,{'FINANCE_DATA_DIR':tmp,'FINANCE_PORT':'9443','FINANCE_BIND':'127.0.0.1'}):
            with mock.patch.object(supervise.subprocess,'Popen') as launch:
                supervise.launch_service()
            command=launch.call_args.args[0]
            self.assertIn(tmp,command)
            self.assertIn('9443',command)
            self.assertIn('127.0.0.1',command)
            self.assertNotIn('--scheduler',command)
            self.assertNotIn('--model',command)
            fake=mock.MagicMock()
            with mock.patch('socket.socket',return_value=fake):
                self.assertEqual(supervise.ensure(Path(tmp),probe=lambda:False,launch=lambda:None),'started')
            fake.__enter__.return_value.bind.assert_called_once_with(('127.0.0.1',9443))

    def test_configured_tls_hostname_is_accepted_by_https_host_guard(self):
        import finance
        import http.client
        import ssl
        import threading
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {'FINANCE_TLS_SANS':'localhost,127.0.0.1,finance.example.test'}):
            store=finance.Store(tmp);finance.initialize(store)
            server=finance.make_server(store,'127.0.0.1',0)
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            try:
                context=ssl.create_default_context(cafile=str(store.root/'tls.crt'))
                conn=http.client.HTTPSConnection('localhost',server.server_port,context=context,timeout=5)
                try:
                    conn.request('GET','/',headers={'Host':'finance.example.test:'+str(server.server_port)})
                    response=conn.getresponse();body=response.read()
                    self.assertEqual(response.status,200)
                    self.assertIn(b'Private Finance login',body)
                finally:conn.close()
            finally:server.shutdown();server.server_close();worker.join()

    def test_metadata_transport_failure_never_sends_prompt_or_falls_back(self):
        import finance
        import assistant_features
        for request in (lambda:finance.model_request('private synthetic merchant'),lambda:assistant_features.plan_request({'question':'private synthetic question','categories':[]})):
            for error in (TimeoutError('synthetic'),ValueError('Redirect refused')):
                with mock.patch.object(finance,'fetch_json',side_effect=error) as transport:
                    with self.assertRaises((ValueError,TimeoutError)):request()
                self.assertEqual(transport.call_count,1)
                self.assertTrue(transport.call_args.args[0].full_url.endswith('/api/show'))
                self.assertNotIn('private synthetic',transport.call_args.args[0].data.decode())

    def test_supervisor_health_probe_uses_own_port_tls_and_no_redirects_or_proxies(self):
        import finance
        import supervise
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ,{'FINANCE_DATA_DIR':tmp,'FINANCE_PORT':'9443','FINANCE_BIND':'127.0.0.1'}),mock.patch.object(supervise.ssl,'create_default_context') as context,mock.patch.object(supervise.urllib.request,'build_opener') as factory:
            response=factory.return_value.open.return_value.__enter__.return_value
            response.status=200;response.read.return_value=b'Private Finance login'
            self.assertTrue(supervise.healthy())
            context.assert_called_once_with(cafile=str(Path(tmp)/'tls.crt'))
            self.assertEqual(factory.call_args.args[0].proxies,{})
            self.assertIsInstance(factory.call_args.args[1],finance.NoRedirect)
            factory.return_value.open.assert_called_once_with('https://localhost:9443/',timeout=5)


if __name__ == '__main__':
    unittest.main()
