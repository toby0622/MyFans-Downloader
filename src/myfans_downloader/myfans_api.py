"""Single source of truth for MyFans API URLs and request policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API_ORIGIN = "https://api.myfans.jp"
API_VERSION = "v2"
DEFAULT_TIMEOUT = (10, 45)


@dataclass(frozen=True)
class ApiEndpoints:
    origin: str = API_ORIGIN
    version: str = API_VERSION

    @property
    def root(self) -> str:
        return f"{self.origin}/api/{self.version}"

    def post(self, post_id: str) -> str:
        return f"{self.root}/posts/{quote(str(post_id), safe='')}"

    @property
    def user_by_username(self) -> str:
        return f"{self.root}/users/show_by_username"

    def user_posts(self, user_id: str, *, back_number: bool = False) -> str:
        collection = "back_number_posts" if back_number else "posts"
        return f"{self.root}/users/{quote(str(user_id), safe='')}/{collection}"


ENDPOINTS = ApiEndpoints()


def build_auth_headers(auth_token: str) -> dict[str, str]:
    token = auth_token.strip()
    if token.lower().startswith("token token="):
        token = token.split("=", 1)[1].strip()
    if not token:
        raise ValueError(
            "Missing authorization token in configuration. Please save your Auth Token in Settings."
        )
    return {
        "authorization": f"Token token={token}",
        "google-ga-data": "event328",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    }


class MyFansApi:
    """Versioned API facade. Remote endpoint changes are isolated here."""

    def __init__(self, auth_token: str):
        self.headers = build_auth_headers(auth_token)
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        retries = Retry(
            total=3,
            connect=3,
            read=2,
            status=3,
            backoff_factor=0.6,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "HEAD"}),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=32, pool_maxsize=32)
        self.session.mount("https://", adapter)

    def _json(self, url: str, **kwargs: Any) -> dict[str, Any]:
        response = self.session.get(url, timeout=DEFAULT_TIMEOUT, **kwargs)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("MyFans API returned an unexpected response.")
        return payload

    def get_user(self, username: str) -> dict[str, Any]:
        return self._json(ENDPOINTS.user_by_username, params={"username": username})

    def get_post(self, post_id: str) -> dict[str, Any]:
        return self._json(ENDPOINTS.post(post_id))

    def get_posts(
        self, user_id: str, page: int, *, back_number: bool = False
    ) -> list[dict[str, Any]]:
        payload = self._json(
            ENDPOINTS.user_posts(user_id, back_number=back_number),
            params={"page": page},
        )
        data = payload.get("data", [])
        if not isinstance(data, list):
            raise TypeError("MyFans API returned an invalid post list.")
        return data

    def close(self) -> None:
        self.session.close()
