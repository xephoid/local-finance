#!/usr/bin/env python3
"""Script-only local service recovery. Never reads bank data or passwords."""
import fcntl,os,ssl,subprocess,sys,time,urllib.request
from pathlib import Path
from deployment import load_config
ROOT=Path(__file__).resolve().parent
os.umask(0o077)
def healthy():
 try:
  from finance import NoRedirect
  cfg=load_config()
  ctx=ssl.create_default_context(cafile=str(cfg.data/'tls.crt'))
  op=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),urllib.request.HTTPSHandler(context=ctx))
  host='localhost' if cfg.bind in ('0.0.0.0','127.0.0.1','::') else cfg.bind
  host='['+host+']' if ':' in host else host
  with op.open('https://'+host+':'+str(cfg.port)+'/',timeout=5) as r:
   return r.status==200 and b'Private Finance login' in r.read(32768)
 except Exception:return False
def launch_service():
 cfg=load_config()
 flags=[]
 if os.environ.get('FINANCE_SCHEDULER')=='1':flags.append('--scheduler')
 if os.environ.get('FINANCE_MODEL_ENABLED')=='1':flags.append('--model')
 subprocess.Popen([sys.executable,str(ROOT/'finance.py'),'--data',str(cfg.data),'--credential',str(cfg.credential),'serve','--bind',cfg.bind,'--port',str(cfg.port)]+flags,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,close_fds=True)
def ensure(root,probe=healthy,launch=launch_service):
 try:
  with open(root/'service.lock','a') as lock:
   try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BlockingIOError:return 'busy'
   if probe():return 'healthy'
   # Do not repeatedly spawn against an occupied but unhealthy port.
   import socket
   with socket.socket() as s:
    s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    try:s.bind((load_config().bind,load_config().port))
    except OSError:return 'unavailable'
   launch()
   return 'started'
 except Exception:return 'unavailable'
def main():
 state=ensure(load_config().data)
 if state=='started':
  for _ in range(10):
   time.sleep(.5)
   if healthy():return 0
  return 1
 return 0 if state in ('healthy','busy') else 1
if __name__=='__main__':raise SystemExit(main())
