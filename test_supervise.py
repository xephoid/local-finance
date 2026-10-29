"""Synthetic supervisor tests; no bank/credential reads or real processes."""
import unittest,tempfile
from pathlib import Path
import supervise
from unittest.mock import patch
# Synthetic socket isolation keeps tests independent of occupied service ports.
class FakeSocket:
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def bind(self,*args):pass
 def setsockopt(self,*args):pass
class SupervisorTests(unittest.TestCase):
 def test_healthy_service_is_not_started(self):
  with tempfile.TemporaryDirectory() as p, patch('socket.socket',return_value=FakeSocket()):
   calls=[]
   self.assertEqual(supervise.ensure(Path(p),probe=lambda:True,launch=lambda:calls.append(1)), 'healthy')
   self.assertEqual(calls,[])
 def test_unhealthy_service_starts_once_under_lock(self):
  with tempfile.TemporaryDirectory() as p, patch('socket.socket',return_value=FakeSocket()):
   calls=[]
   self.assertEqual(supervise.ensure(Path(p),probe=lambda:False,launch=lambda:calls.append(1)), 'started')
   self.assertEqual(calls,[1])
 def test_restart_probe_reuses_recently_closed_port(self):
  import socket
  fake=FakeSocket()
  fake.setsockopt=__import__('unittest.mock',fromlist=['Mock']).Mock()
  with tempfile.TemporaryDirectory() as p, patch('socket.socket',return_value=fake):
   self.assertEqual(supervise.ensure(Path(p),probe=lambda:False,launch=lambda:None),'started')
  fake.setsockopt.assert_called_once_with(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
 def test_launch_failure_is_generic(self):
  with tempfile.TemporaryDirectory() as p, patch('socket.socket',return_value=FakeSocket()):
   def fail():raise RuntimeError('SYNTHETIC_SECRET')
   self.assertEqual(supervise.ensure(Path(p),probe=lambda:False,launch=fail),'unavailable')
if __name__=='__main__': unittest.main()
