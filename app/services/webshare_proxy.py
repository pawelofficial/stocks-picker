"""WebShare rotating proxy for yfinance requests.

Builds proxy URLs from WEBSHARE_PROXY_USERNAME / PASSWORD / USER_COUNT.
The username has a -N suffix (e.g. kkbthibh-1 .. kkbthibh-10) which WebShare
uses to assign different exit IPs. We rotate through sub-users per request.
"""

from __future__ import annotations

import itertools
import logging
import os
from typing import List, Optional
from urllib.parse import quote_plus

log = logging.getLogger(__name__)

_WEBSHARE_HOST = "p.webshare.io"
_WEBSHARE_PORT = 80

_PROXY_CACHE: Optional[List[str]] = None
_ROTATOR: Optional[itertools.cycle] = None


def _is_enabled() -> bool:
    return os.environ.get("WEBSHARE_ENABLED", "false").strip().lower() in ("true", "1", "yes")


def _build_proxy_list() -> List[str]:
    """Build proxy URLs from env vars. Each sub-user gets a different exit IP."""
    if not _is_enabled():
        return []

    base_user = os.environ.get("WEBSHARE_PROXY_USERNAME", "").strip()
    password = os.environ.get("WEBSHARE_PROXY_PASSWORD", "").strip()
    count = os.environ.get("WEBSHARE_PROXY_USER_COUNT", "").strip()

    if not all([base_user, password]):
        return []

    # Username already has the -N suffix (e.g. "kkbthibh-1").
    # Strip it to get the base, then generate -1 through -N.
    if "-" in base_user:
        prefix = base_user.rsplit("-", 1)[0]
    else:
        prefix = base_user

    try:
        n = int(count) if count else 1
    except ValueError:
        n = 1

    pw = quote_plus(password)
    urls = []
    for i in range(1, n + 1):
        user = quote_plus(f"{prefix}-{i}")
        urls.append(f"http://{user}:{pw}@{_WEBSHARE_HOST}:{_WEBSHARE_PORT}")

    log.info("WebShare: built %d rotating proxy URL(s) (%s-1..%d)", len(urls), prefix, n)
    return urls


def _ensure_proxies() -> List[str]:
    global _PROXY_CACHE
    if _PROXY_CACHE is None:
        _PROXY_CACHE = _build_proxy_list()
    return _PROXY_CACHE


def get_next_proxy() -> Optional[str]:
    """Return next proxy URL for rotation, or None if WebShare not configured."""
    proxies = _ensure_proxies()
    if not proxies:
        return None

    global _ROTATOR
    if _ROTATOR is None:
        _ROTATOR = itertools.cycle(proxies)
    return next(_ROTATOR)


def is_webshare_configured() -> bool:
    return bool(_ensure_proxies())
