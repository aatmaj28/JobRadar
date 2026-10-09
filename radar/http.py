"""One shared HTTP session with light retries."""
from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


class Http:
    def __init__(self, timeout: float = 20.0, retries: int = 2, pool: int = 32):
        session = requests.Session()
        retry = Retry(
            total=retries,
            connect=retries,
            read=retries,
            backoff_factor=0.8,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=None,  # Workday search is a POST and safe to retry
            raise_on_status=False,
            respect_retry_after_header=False,  # never sleep for minutes on one board
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=pool, pool_maxsize=pool)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update(
            {
                "User-Agent": BROWSER_UA,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        self.session = session
        self.timeout = timeout

    def get(self, url: str, **kw):
        kw.setdefault("timeout", self.timeout)
        return self.session.get(url, **kw)

    def post(self, url: str, **kw):
        kw.setdefault("timeout", self.timeout)
        return self.session.post(url, **kw)
