"""One-time provisioning tests with synthetic setup/access credentials, no network."""
import base64
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

CLAIM='https://bridge.simplefin.org/simplefin/claim/synthetic-token'
ACCESS=b'https://fixture-user:fixture-pass@bridge.simplefin.org/simplefin/access/synthetic\n'

class ProvisionTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('provision'),'secure provisioning CLI missing')
        import provision
        return provision

    def test_hidden_prompt_durable_private_output_and_no_secret_stdout(self):
        provision=self.module()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'credentials'/'access'
            token=base64.b64encode(CLAIM.encode()).decode()
            output=io.StringIO()
            with mock.patch.object(provision.getpass,'getpass',return_value=token) as prompt, mock.patch.object(provision,'claim',return_value=ACCESS) as claim, mock.patch.dict(os.environ,{'FINANCE_SIMPLEFIN_CREDENTIAL':str(path)}), contextlib.redirect_stdout(output):
                self.assertEqual(provision.main([]),0)
            prompt.assert_called_once()
            request=claim.call_args.args[0]
            self.assertEqual(request.full_url,CLAIM)
            self.assertEqual(request.data,b'')
            self.assertEqual(request.get_header('User-agent'),'LocalFinance/1.0')
            self.assertEqual(path.read_bytes(),ACCESS)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777,0o700)
            self.assertNotIn(token,output.getvalue());self.assertNotIn('fixture-pass',output.getvalue())
            with mock.patch.object(provision,'claim') as network:
                with self.assertRaises(ValueError):provision.provision_token(token,path)
                network.assert_not_called()

    def test_unapproved_claim_urls_never_contact_network(self):
        provision=self.module()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(provision,'claim') as network:
            for url in ('http://bridge.simplefin.org/simplefin/claim/token','https://evil.example/simplefin/claim/token','https://bridge.simplefin.org.evil.example/simplefin/claim/token','https://bridge.simplefin.org:444/simplefin/claim/token','https://u:p@bridge.simplefin.org/simplefin/claim/token','https://bridge.simplefin.org/other','https://bridge.simplefin.org/simplefin/claim/token?x=1'):
                token=base64.b64encode(url.encode()).decode()
                with self.subTest(url=url),self.assertRaises(ValueError):
                    provision.provision_token(token,Path(tmp)/'access')
            network.assert_not_called()

    def test_invalid_returned_access_is_saved_privately_but_fails_closed(self):
        provision=self.module()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(provision,'claim',return_value=b'https://unapproved.example/secret'):
            path=Path(tmp)/'access'
            with self.assertRaises(ValueError):provision.provision_token(base64.b64encode(CLAIM.encode()).decode(),path)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertEqual(path.read_bytes(),b'https://unapproved.example/secret')

    def test_transport_has_no_proxies_redirects_and_bounded_response(self):
        provision=self.module()
        import finance
        with mock.patch.object(provision.urllib.request,'build_opener') as factory:
            factory.return_value.open.return_value.__enter__.return_value.read.return_value=ACCESS
            provision.claim(provision.urllib.request.Request(CLAIM,data=b''))
            self.assertEqual(factory.call_args.args[0].proxies,{})
            self.assertIsInstance(factory.call_args.args[1],finance.NoRedirect)
            factory.return_value.open.return_value.__enter__.return_value.read.assert_called_once_with(4097)

if __name__=='__main__':unittest.main()
