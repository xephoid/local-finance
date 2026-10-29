"""Portable distribution policies; no shell tools or network needed."""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]

class DistributionTests(unittest.TestCase):
    def test_distribution_has_allowlisted_context_and_nonroot_persistent_container(self):
        for name in ('README.md','LICENSE','THIRD_PARTY.md','config.example.env','Dockerfile','compose.yaml','.dockerignore','.gitignore','.github/workflows/tests.yml'):
            self.assertTrue((ROOT/name).is_file(),name+' is missing')
        docker=(ROOT/'Dockerfile').read_text()
        self.assertIn('USER 10001:10001',docker)
        self.assertNotIn('COPY .',docker)
        self.assertIn('COPY finance.py assistant_features.py deployment.py notify.py supervise.py provision.py',docker)
        ignore=(ROOT/'.dockerignore').read_text().splitlines()
        self.assertEqual(ignore[0],'**')
        self.assertNotIn('!**',ignore)
        compose=(ROOT/'compose.yaml').read_text()
        self.assertIn('finance-data:/data',compose)
        self.assertIn('${FINANCE_LAN_IP:-127.0.0.1}',compose)
        self.assertIn('read_only: true',compose)
        ci=(ROOT/'.github/workflows/tests.yml').read_text()
        self.assertIn('unittest discover -s tests',ci)
        self.assertIn('unittest test_notify test_supervise',ci)
        self.assertNotIn('secrets.',ci)

if __name__=='__main__':unittest.main()
