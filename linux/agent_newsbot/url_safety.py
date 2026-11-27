"""Validate actionable news URLs independently of identity canonicalization."""
import ipaddress
import re
from urllib.parse import urlsplit, unquote
import idna


def safe_article_url(raw):
    if not isinstance(raw, str): return False
    try:
        if not 1 <= len(raw.encode('utf-8')) <= 8192: return False
    except UnicodeError: return False
    if any(ord(c) <= 32 or ord(c) == 127 or c == '\\' for c in raw):
        return False
    try:
        if re.search(r'%(?![0-9a-fA-F]{2})', raw): return False
        if any(ord(c) < 32 or ord(c) == 127 or c == '\\' for c in unquote(raw, errors='strict')): return False
        p = urlsplit(raw)
        if '%' in p.netloc: return False
        if p.scheme.lower() not in {'http', 'https'} or not p.hostname or p.username is not None or p.password is not None:
            return False
        if p.port is not None and not 1 <= p.port <= 65535: return False
        host = p.hostname
        if ':' in host:
            ipaddress.IPv6Address(host)
        else:
            host = idna.encode(host, uts46=True).decode('ascii')
            if not re.fullmatch(r'[A-Za-z0-9.-]+', host): return False
        return True
    except (ValueError, UnicodeError, idna.IDNAError):
        return False


def require_safe_story(story):
    urls = [story.get('url')] + [s.get('url') for s in story.get('sources', [])]
    if not all(safe_article_url(url) for url in urls):
        raise ValueError('Saved story contains a non-web or invalid source URL; review it before drafting or exporting')
