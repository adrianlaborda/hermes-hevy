"""Hevy API client: ``api-key`` header on every request, retries for idempotent calls and a small cache.

The key is read through ``key_provider`` on every request so a rotated key takes effect without a
restart. Responses are cached per key fingerprint in memory only; nothing is written to disk.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Iterator, Mapping, Optional

import httpx

BASE_URL = "https://api.hevyapp.com"
KEY_ENV = "HEVY_API_KEY"
KEY_PAGE = "https://hevy.com/settings?developer"
# Where a user stores the key without a terminal: the plugin's settings form in Hermes Desktop.
KEY_SETTINGS = "Hermes Desktop: Settings > Plugins > hevy > Hevy API key"
KEY_CLI = f"hermes config set {KEY_ENV} <key>"

# GET and PUT are idempotent, so they are retried on server errors too. POST is not (a retried create
# could log the same workout twice), so it is retried only on 429, which Hevy answers before acting.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
POST_RETRY_STATUSES = frozenset({429})
MAX_RETRY_AFTER_S = 10.0
MESSAGE_LIMIT = 200
_MISS = object()


class HevyError(Exception):
    """A failure with a message safe to show the user and the model (no secrets).

    ``status`` is the HTTP status when the failure was an HTTP response."""

    def __init__(self, message: str = "", *, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


class NotConfiguredError(HevyError):
    pass


class AuthError(HevyError):
    pass


class LimitError(HevyError):
    pass


class ValidationError(HevyError):
    pass


class NotFoundError(HevyError):
    pass


class ConflictError(HevyError):
    pass


class RateLimitedError(HevyError):
    pass


class ApiError(HevyError):
    pass


def key_rejected_message(status: int) -> str:
    return (
        f"Hevy rejected the API key (HTTP {status}). It is invalid or revoked, or the account has no "
        f"Hevy Pro subscription (the API needs Pro). Create a key at {KEY_PAGE} and paste it in "
        f"{KEY_SETTINGS}, then select Save settings (from a terminal: `{KEY_CLI}`). Never paste it into a chat."
    )


class HevyClient:
    """Thread-safe; one instance can serve concurrent tool calls.

    Cached responses are keyed by a fingerprint of the key, so two profiles with different keys
    never see each other's data."""

    def __init__(
        self,
        key_provider: Callable[[], Optional[str]],
        base_url: str = BASE_URL,
        *,
        transport: Optional[httpx.BaseTransport] = None,
        timeout: float = 20.0,
        retries: int = 2,
        cache_ttl: float = 60.0,
        cache_size: int = 256,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._key_provider = key_provider
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport,
                                  headers={"User-Agent": "hermes-hevy"})
        self._retries = retries
        self._cache_ttl = cache_ttl
        self._cache_size = cache_size
        self._clock = clock
        self._sleep = sleep
        self._cache: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()
        self._cache_lock = threading.Lock()

    def close(self) -> None:
        self._http.close()

    # -- credentials -------------------------------------------------------------------------

    def _key(self) -> tuple[str, str]:
        key = (self._key_provider() or "").strip()
        if not key:
            raise NotConfiguredError(
                f"No Hevy API key is configured. Create one at {KEY_PAGE} (Hevy Pro needed), paste it in "
                f"{KEY_SETTINGS}, then select Save settings (from a terminal: `{KEY_CLI}`). "
                "Never paste it into a chat."
            )
        return key, hashlib.sha256(f"{self.base_url}\0{key}".encode()).hexdigest()

    def fingerprint(self) -> str:
        return self._key()[1]

    # -- requests ----------------------------------------------------------------------------

    def get(self, path: str, params: Optional[Mapping[str, Any]] = None, *,
            ttl: Optional[float] = None, retries: Optional[int] = None) -> Any:
        """GET JSON from ``path``. Returns None for 204/empty bodies. Cached for ``ttl`` seconds (0 bypasses)."""
        key, fingerprint = self._key()
        query = _clean(params)
        cache_key = (fingerprint, path, tuple(sorted(query.items())))
        ttl = self._cache_ttl if ttl is None else ttl
        cached = self._cache_get(cache_key, ttl)
        if cached is not _MISS:
            return cached
        data = _json(self._request("GET", path, key=key, params=query, retries=retries), path)
        if ttl > 0:
            self._cache_put(cache_key, data)
        return data

    def post(self, path: str, body: dict) -> Any:
        """POST a JSON body. Not retried on server errors; drops the cache whatever the outcome."""
        key, _ = self._key()
        try:
            return _json(self._request("POST", path, key=key, body=body), path)
        finally:
            self.invalidate()

    def put(self, path: str, body: dict) -> Any:
        """PUT a JSON body (retried like GET, as it is idempotent). Drops the cache whatever the outcome."""
        key, _ = self._key()
        try:
            return _json(self._request("PUT", path, key=key, body=body), path)
        finally:
            self.invalidate()

    def paginate(self, path: str, items_key: str, *, page_size: int, params: Optional[Mapping[str, Any]] = None,
                 max_pages: Optional[int] = None, ttl: Optional[float] = None) -> Iterator[dict]:
        """Yield items from ``page=1..`` of a paginated list.

        Stops when ``page >= page_count`` (so a ``page_count`` of 0 stops after the first page), when a page
        has no items, when ``max_pages`` is reached, or on a 404 past page 1 (Hevy's answer past the end)."""
        page = 1
        while max_pages is None or page <= max_pages:
            query = {**(params or {}), "page": page, "pageSize": page_size}
            try:
                data = self.get(path, query, ttl=ttl)
            except NotFoundError:
                if page == 1:
                    raise
                return
            if not isinstance(data, dict):
                return
            items = data.get(items_key) or []
            if not items:
                return
            yield from items
            if page >= int(data.get("page_count") or 0):
                return
            page += 1

    def invalidate(self) -> None:
        """Drop every cached response. Called after writes so the next read sees the change."""
        with self._cache_lock:
            self._cache.clear()

    def _request(self, method: str, path: str, *, key: str, params: Optional[Mapping[str, Any]] = None,
                 body: Optional[dict] = None, retries: Optional[int] = None) -> httpx.Response:
        headers = {"api-key": key, "Accept": "application/json"}
        attempts = 1 + (self._retries if retries is None else retries)
        statuses = POST_RETRY_STATUSES if method == "POST" else RETRY_STATUSES
        attempt = 0
        while True:
            last = attempt + 1 >= attempts
            try:
                response = self._http.request(method, path, params=params, json=body, headers=headers)
            except httpx.HTTPError as exc:
                if last or method == "POST":
                    raise ApiError(f"Could not reach Hevy ({type(exc).__name__}). "
                                   "Check the network and try again.") from None
                delay = _backoff(attempt)
            else:
                if response.status_code not in statuses or last:
                    _raise_for_status(response, secrets=(key,))
                    return response
                delay = _retry_after(response) or _backoff(attempt)
            self._sleep(delay)
            attempt += 1

    # -- cache -------------------------------------------------------------------------------

    def _cache_get(self, key: tuple, ttl: float) -> Any:
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit is None or ttl <= 0 or self._clock() - hit[0] > ttl:
                return _MISS
            self._cache.move_to_end(key)
            return hit[1]

    def _cache_put(self, key: tuple, value: Any) -> None:
        with self._cache_lock:
            self._cache[key] = (self._clock(), value)
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)


def _clean(params: Optional[Mapping[str, Any]]) -> dict:
    return {k: v for k, v in (params or {}).items() if v is not None}


def _json(response: httpx.Response, path: str) -> Any:
    if response.status_code == 204 or not response.content.strip():
        return None
    try:
        return response.json()
    except ValueError:
        raise ApiError(f"Hevy returned a response that is not JSON for {path}.") from None


def _backoff(attempt: int) -> float:
    return min(0.5 * 2 ** attempt, 4.0)


def _retry_after(response: httpx.Response) -> Optional[float]:
    try:
        seconds = float(response.headers.get("Retry-After", ""))
    except ValueError:
        return None
    if not 0 <= seconds:  # also rejects NaN
        return None
    return min(seconds, MAX_RETRY_AFTER_S)


def redact(text: str, secrets: tuple) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


def _error_detail(response: httpx.Response, secrets: tuple) -> str:
    """Hevy's own ``message`` (or ``error``) field, if it sent one, trimmed and with the key removed."""
    try:
        body = response.json()
    except ValueError:
        body = None
    message = ""
    if isinstance(body, dict):
        message = str(body.get("message") or body.get("error") or "")
    message = redact(message.strip(), secrets)[:MESSAGE_LIMIT]
    return f" Hevy says: {message}" if message else ""


def _raise_for_status(response: httpx.Response, *, secrets: tuple = ()) -> None:
    status = response.status_code
    if status < 400:
        return
    detail = _error_detail(response, secrets)
    if status == 401:
        raise AuthError(key_rejected_message(401), status=status)
    if status == 403:
        raise LimitError("Hevy refused this request (HTTP 403). The account may need Hevy Pro or may have "
                         "reached a plan limit." + detail, status=status)
    if status == 400:
        raise ValidationError("Hevy rejected the request body or parameters (HTTP 400)." + detail, status=status)
    if status == 404:
        raise NotFoundError("Hevy has no such item (HTTP 404)." + detail, status=status)
    if status == 409:
        raise ConflictError("Hevy reports a conflict (HTTP 409): the item already exists or clashes with "
                            "existing data." + detail, status=status)
    if status == 429:
        raise RateLimitedError("Hevy is rate-limiting requests (HTTP 429). Wait a minute and try again.",
                               status=status)
    if status >= 500:
        raise ApiError(f"Hevy had a server error (HTTP {status}). Try again later." + detail, status=status)
    raise ApiError(f"Hevy rejected the request (HTTP {status})." + detail, status=status)
