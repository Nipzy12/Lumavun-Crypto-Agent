"""Public HTTPS source boundary: validate syntax, every DNS answer and redirects."""
import ipaddress
import re
import socket
from urllib.parse import urlsplit


def validate_public_url(url):
    if not isinstance(url, str) or not url or len(url) > 2048 or re.search(r'[\x00-\x20\\]', url):
        return False
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ''
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None,443) or parsed.fragment:
            return False
        if not host or host.endswith('.') or '%' in host or host.endswith(('.localhost','.local','.internal','.home','.lan')) or host in ('localhost','metadata.google.internal'):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            # Reject alternate numeric IP encodings and malformed DNS labels.
            return bool('.' in host and not re.fullmatch(r'[0-9xXa-fA-F.]+',host) and all(re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?',part) for part in host.split('.')))
    except (ValueError, TypeError):
        return False


def resolve_public(url):
    if not validate_public_url(url):
        raise ValueError('Unsafe source URL')
    addresses = {row[4][0] for row in socket.getaddrinfo(urlsplit(url).hostname,443,type=socket.SOCK_STREAM)}
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError('Source DNS includes a non-public address')
    return addresses


def check_source_destination(url):
    # Re-check immediately before every request, including robots.txt.
    try:
        resolve_public(url)
    except (OSError, ValueError):
        return False
    return True
