"""PRE Point API client and public token-provider interface."""
from __future__ import annotations

from typing import Any, Protocol

import aiohttp

from .auth import PREPointAuth, PREPointError, PreAuthError, PreInvalidCredentialsError
from .const import API_BASE, HISTORY_PERIODS, REQUEST_TIMEOUT, SESSIONS_PATH

__all__ = [
    "PREPointApi",
    "PREPointError",
    "PreAuthError",
    "PreInvalidCredentialsError",
    "PreRateLimitError",
    "PreTokenProvider",
]


class PreTokenProvider(Protocol):
    """Public token interface for other PRE Point integration modules."""

    async def async_get_access_token(self) -> str:
        """Return a valid access token or raise PreAuthError."""
        ...


class PreRateLimitError(PREPointError):
    """The API asked us to slow down (HTTP 429)."""

    def __init__(self, retry_after: int | None = None) -> None:
        super().__init__("PRE API omezilo počet požadavků (HTTP 429).")
        self.retry_after = retry_after


class PREPointApi:
    """PRE Point API client; authentication details live in auth.py."""

    def __init__(self, username: str, password: str) -> None:
        self.auth = PREPointAuth(username, password)

    async def async_get_access_token(self) -> str:
        """Implement PreTokenProvider."""
        return await self.auth.async_get_access_token()

    async def _get(self, path: str, params: dict[str, Any], token: str) -> tuple[int, Any]:
        http = self.auth.session
        if http is None:
            raise PREPointError("HTTP relace PRE Point nebyla inicializována.")
        try:
            async with http.get(
                f"{API_BASE}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                if resp.status == 429:
                    retry_after = resp.headers.get("Retry-After", "")
                    raise PreRateLimitError(int(retry_after) if retry_after.isdigit() else None)
                if resp.status >= 400:
                    return resp.status, None
                return resp.status, await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise PREPointError(
                f"PRE API není dostupné: {err or type(err).__name__}"
            ) from err

    async def async_get_sessions(self, periods: int = HISTORY_PERIODS) -> Any:
        """Fetch the raw charging-session response (current month + `periods` before)."""
        params = {"periodsInPast": periods}
        token = await self.async_get_access_token()
        status, data = await self._get(SESSIONS_PATH, params, token)
        if status == 401:
            # Token rejected: drop it and ask for a fresh one, once.
            self.auth.access_token = None
            self.auth.expiry = 0
            token = await self.async_get_access_token()
            status, data = await self._get(SESSIONS_PATH, params, token)
        if status >= 400:
            raise PREPointError(f"Endpoint historie vrátil HTTP {status}.")
        return data

    async def async_close(self) -> None:
        """Close the HTTP session."""
        await self.auth.async_close()
