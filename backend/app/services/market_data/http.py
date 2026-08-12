"""The one place this application talks to the internet.

Every provider adapter goes through this, for three reasons.

**It is injectable.** An adapter takes a transport; tests pass a recorded one.
That is what makes it possible to test the parsing of a real API response
without a network, an API key, or a service that might be down — and this whole
build has been verified without any of the three.

**It is polite by construction.** A rate limit and a timeout are set per source
rather than left to each adapter to remember, because the failure mode of
forgetting is hammering somebody else's free API.

**It fails in one shape.** Adapters see ``ProviderRequestError`` whatever went
wrong underneath, so the sync engine has one thing to catch and one thing to
record against the source.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

__all__ = [
    "CapabilityDeniedError",
    "HttpxTransport",
    "ProviderRequestError",
    "RecordedTransport",
    "Transport",
]

DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_USER_AGENT = "SlabStack/0.1 (local-first collection tool)"

#: Retried once, after a pause. Everything else is reported as-is: retrying a
#: 401 or a 404 just wastes somebody's rate limit.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class ProviderRequestError(RuntimeError):
    """A request to a provider failed. Carries enough to tell the user why."""

    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class CapabilityDeniedError(ProviderRequestError):
    """The source is up and the credentials work — this *one* thing is not granted.

    Distinct from a failure because it is neither a mistake nor an outage, and
    it must not stop a run. Providers gate parts of themselves behind separate
    approval (eBay's sold data being the case that prompted this), so a source
    can be perfectly healthy and still refuse one of the things it advertises.
    The sync notes it and carries on with whatever else that source can do.
    """


class Transport(Protocol):
    """The seam. Real adapters get HTTP; tests get a dictionary."""

    def get_json(self, url: str, *, params: dict[str, Any], headers: dict[str, str]) -> dict: ...

    def post_form(self, url: str, *, data: dict[str, str], headers: dict[str, str]) -> dict: ...


@dataclass
class HttpxTransport:
    """Real HTTP, rate limited and retried once on a transient failure."""

    timeout: float = DEFAULT_TIMEOUT_SECONDS
    #: Requests per minute. ``None`` means the source did not declare one, in
    #: which case a conservative default applies rather than no limit at all.
    rate_limit_per_minute: int | None = None
    user_agent: str = DEFAULT_USER_AGENT
    _last_request_at: float = field(default=0.0, init=False, repr=False)

    @property
    def _min_interval(self) -> float:
        limit = self.rate_limit_per_minute or 60
        return 60.0 / max(1, limit)

    def _wait_turn(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if self._last_request_at and elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_at = time.monotonic()

    def get_json(self, url: str, *, params: dict[str, Any], headers: dict[str, str]) -> dict:
        return self._request(url, headers=headers, params=params)

    def post_form(self, url: str, *, data: dict[str, str], headers: dict[str, str]) -> dict:
        """A form-encoded POST, which is what OAuth token endpoints take.

        Kept to exactly this shape rather than a general POST: the only reason
        this build ever posts anything is to exchange credentials for a token,
        and a narrow method is one that cannot quietly grow into writing to
        somebody's marketplace account.
        """
        return self._request(url, headers=headers, data=data)

    def _request(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
    ) -> dict:
        merged = {"User-Agent": self.user_agent, "Accept": "application/json", **headers}
        post = data is not None

        for attempt in (1, 2):
            self._wait_turn()
            try:
                if post:
                    response = httpx.post(url, data=data, headers=merged, timeout=self.timeout)
                else:
                    response = httpx.get(url, params=params, headers=merged, timeout=self.timeout)
            except httpx.TimeoutException as exc:
                if attempt == 1:
                    continue
                raise ProviderRequestError(
                    f"{url} timed out after {self.timeout:g}s.", retryable=True
                ) from exc
            except httpx.HTTPError as exc:
                raise ProviderRequestError(f"Could not reach {url}: {exc}") from exc

            if response.status_code in _RETRYABLE_STATUS and attempt == 1:
                # One pause, then one more try. A second failure is reported
                # rather than retried into somebody's rate limit.
                time.sleep(_retry_after(response) or 2.0)
                continue

            if response.status_code >= 400:
                raise ProviderRequestError(
                    _explain(response.status_code, url),
                    status_code=response.status_code,
                    retryable=response.status_code in _RETRYABLE_STATUS,
                )

            try:
                body = response.json()
            except ValueError as exc:
                raise ProviderRequestError(
                    f"{url} returned something that is not JSON."
                ) from exc
            if not isinstance(body, dict):
                raise ProviderRequestError(f"{url} returned {type(body).__name__}, not an object.")
            return body

        raise ProviderRequestError(f"{url} failed twice.", retryable=True)  # pragma: no cover


def _retry_after(response: httpx.Response) -> float | None:
    """Honour the server's own backoff request when it makes one."""
    header = response.headers.get("Retry-After")
    if not header:
        return None
    try:
        return min(float(header), 30.0)
    except ValueError:
        return None


def _explain(status: int, url: str) -> str:
    """Say what to do, not just what broke."""
    if status == 401 or status == 403:
        return (
            f"{url} rejected the request ({status}). Check the API key for this source, and that "
            "the account it belongs to is active."
        )
    if status == 404:
        return f"{url} returned 404 — the card or endpoint does not exist at this provider."
    if status == 429:
        return (
            f"{url} rate-limited the request (429). Lower the source's requests-per-minute, or "
            "add an API key if the provider offers a higher limit with one."
        )
    return f"{url} returned HTTP {status}."


@dataclass
class RecordedTransport:
    """A transport backed by recorded responses, for tests and the demo.

    Keyed by URL so a test reads as "when asked for this, the API says that".
    An unrecorded URL raises rather than returning an empty result, because a
    silently empty response is how a broken adapter passes its tests.
    """

    responses: dict[str, dict]
    calls: list[tuple[str, dict, dict]] = field(default_factory=list)

    def get_json(self, url: str, *, params: dict[str, Any], headers: dict[str, str]) -> dict:
        self.calls.append((url, dict(params), dict(headers)))
        return self._recorded(url)

    def post_form(self, url: str, *, data: dict[str, str], headers: dict[str, str]) -> dict:
        self.calls.append((url, dict(data), dict(headers)))
        return self._recorded(url)

    def _recorded(self, url: str) -> dict:
        if url not in self.responses:
            raise ProviderRequestError(f"No recorded response for {url}.")
        recorded = self.responses[url]
        if isinstance(recorded, Exception):
            # Recording a failure is as important as recording a success: an
            # adapter's behaviour on a 403 is behaviour, and it needs testing.
            raise recorded
        return recorded
