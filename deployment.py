"""Explicit environment configuration; no implicit dotenv or agent integration."""
from dataclasses import dataclass
import ipaddress
import os
from pathlib import Path
import re
from urllib.parse import urlsplit


def local_url(value):
    u = urlsplit(value)
    if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in ('', '/'):
        raise ValueError('Invalid local model URL')
    if u.port is not None and not 1 <= u.port <= 65535:
        raise ValueError('Invalid local model port')
    if u.hostname != 'localhost':
        try:
            address = ipaddress.ip_address(u.hostname)
        except ValueError:
            raise ValueError('Use localhost or an explicit private LAN IP for the model') from None
        # Avoid DNS rebinding and special-use/public destinations.
        allowed = address.is_loopback or (address.version == 4 and any(address in ipaddress.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'))) or (address.version == 6 and address in ipaddress.ip_network('fc00::/7'))
        if not allowed:
            raise ValueError('Model must use a private LAN or loopback IP')
    return value.rstrip('/')


def tls_sans(value):
    names = []
    for item in value.split(','):
        item = item.strip()
        try:
            name = 'IP:' + str(ipaddress.ip_address(item))
        except ValueError:
            if not re.fullmatch(r'(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', item) or any(not x or len(x)>63 or x.startswith('-') or x.endswith('-') for x in item.split('.')):
                raise ValueError('Invalid TLS SAN hostname')
            name = 'DNS:' + item
        names.append(name)
    return ','.join(dict.fromkeys(names))


@dataclass(frozen=True)
class Config:
    data: Path
    credential: Path
    ollama_url: str
    model: str
    bind: str
    port: int
    tls_sans: str
    telegram_chat_id: int | None


def load_config():
    data = Path(os.environ.get('FINANCE_DATA_DIR', 'data')).expanduser().resolve()
    credential = Path(os.environ.get('FINANCE_SIMPLEFIN_CREDENTIAL', str(data / 'simplefin_access_url'))).expanduser().resolve()
    port = int(os.environ.get('FINANCE_PORT', '8787'))
    if not 1 <= port <= 65535:
        raise ValueError('Invalid port')
    model = os.environ.get('FINANCE_OLLAMA_MODEL', 'qwen3:8b')
    if not model or len(model)>200 or not re.fullmatch(r'[A-Za-z0-9_.:/-]+',model):
        raise ValueError('Invalid model name')
    recipient = os.environ.get('TELEGRAM_CHAT_ID', '')
    if recipient and (not re.fullmatch(r'-?[0-9]+',recipient) or int(recipient)==0):
        raise ValueError('Invalid Telegram recipient')
    return Config(data, credential, local_url(os.environ.get('FINANCE_OLLAMA_URL', 'http://localhost:11434')), model, os.environ.get('FINANCE_BIND','127.0.0.1'), port, tls_sans(os.environ.get('FINANCE_TLS_SANS','localhost,127.0.0.1')), int(recipient) if recipient else None)
