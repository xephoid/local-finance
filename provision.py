#!/usr/bin/env python3
"""Claim a SimpleFIN setup token via a hidden local prompt. Never logs secrets."""
import argparse
import base64
import getpass
import os
from pathlib import Path
import re
import urllib.parse
import urllib.request
from deployment import load_config
from finance import NoRedirect

HOSTS=('bridge.simplefin.org','beta-bridge.simplefin.org')


def claim(request):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with opener.open(request,timeout=30) as response:
        raw=response.read(4097)
        if len(raw)>4096 or not raw:raise ValueError('Invalid claim response')
        return raw


def provision_token(token, output):
    """Do not overwrite/retry. Persist even invalid responses for private recovery."""
    path=Path(output)
    if path.exists() or path.is_symlink():raise ValueError('Credential output already exists')
    try:
        if len(token)>8192:raise ValueError()
        url=base64.b64decode(token,validate=True).decode('ascii')
        parsed=urllib.parse.urlsplit(url)
        if parsed.scheme!='https' or parsed.hostname not in HOSTS or parsed.port not in (None,443) or parsed.username or parsed.password or parsed.query or parsed.fragment or not re.fullmatch(r'/simplefin/claim/[A-Za-z0-9._~-]+',parsed.path):
            raise ValueError()
    except Exception:
        raise ValueError('Invalid setup token') from None
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    st=path.parent.stat()
    if st.st_mode & 0o077 or st.st_uid!=os.getuid():raise ValueError('Private owned output directory required')
    # Reserve before consuming a single-use token; a failure leaves a private
    # marker. Inspect locally before replacing it; never blindly repeat a claim.
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as output_file:
        request=urllib.request.Request(url,data=b'',headers={'User-Agent':'LocalFinance/1.0','Accept':'text/plain'})
        raw=claim(request)
        output_file.write(raw)
        output_file.flush()
        os.fsync(output_file.fileno())
    directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(directory)
    finally:os.close(directory)
    try:
        access=urllib.parse.urlsplit(raw.decode('ascii').strip())
        if access.scheme!='https' or access.hostname not in HOSTS or access.port not in (None,443) or not access.username or not access.password or access.query or access.fragment or not access.path.startswith('/simplefin/'):
            raise ValueError()
    except Exception:
        raise ValueError('Claim saved privately but credential validation failed') from None


def main(argv=None):
    parser=argparse.ArgumentParser(description='Provision SimpleFIN through a hidden local prompt (never pass tokens as arguments).')
    parser.add_argument('--output',default=str(load_config().credential))
    args=parser.parse_args(argv)
    try:
        # getpass must fail rather than echo if no controlling terminal exists.
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('error',getpass.GetPassWarning)
            token=getpass.getpass('SimpleFIN setup token (hidden): ')
        provision_token(token,args.output)
        print('Credential saved privately. No secret was printed.')
        return 0
    except Exception:
        print('Provisioning stopped. Inspect the private output locally before retrying; no secrets printed.')
        return 1


if __name__=='__main__':raise SystemExit(main())
