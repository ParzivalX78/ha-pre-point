"""PRE Point API client and public token-provider interface."""
from __future__ import annotations

from typing import Protocol

import aiohttp

from .auth import PREPointAuth, PREPointError, PreAuthError, PreInvalidCredentialsError
from .const import API_BASE, SESSIONS_PATH


class PreTokenProvider(Protocol):
    """Public token interface for other PRE Point integration modules."""

    async def async_get_access_token(self) -> str:
        """Return a valid access token or raise PreAuthError."""
        ...


class PREPointApi:
    """PRE Point API client; authentication details live in auth.py."""

    def __init__(self, username: str, password: str) -> None:
        self.auth = PREPointAuth(username, password)
        # Preserve the existing public surface for code that uses api.session.
        self.session: aiohttp.ClientSession | None = None

    async def async_get_access_token(self) -> str:
        """Implement PreTokenProvider for colleague's modules."""
        token = await self.auth.async_get_access_token()
        self.session = self.auth.session
        return token

    async def async_get_sessions(self):
        """Fetch the raw charging-session response."""
        token = await self.async_get_access_token()
        http = self.auth.session
        if http is None:
            raise PREPointError("HTTP relace PRE Point nebyla inicializována.")
        async with http.get(
            f"{API_BASE}{SESSIONS_PATH}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        ) as resp:
            if resp.status == 401:
                self.auth.access_token = None
                self.auth.expiry = 0
                token = await self.async_get_access_token()
                async with http.get(
                    f"{API_BASE}{SESSIONS_PATH}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                    },
                ) as retry_resp:
                    if retry_resp.status >= 400:
                        raise PREPointError(
                            f"Endpoint historie vrátil HTTP {retry_resp.status}."
                        )
                    return await retry_resp.json(content_type=None)
            if resp.status >= 400:
                raise PREPointError(
                    f"Endpoint historie vrátil HTTP {resp.status}."
                )
            return await resp.json(content_type=None)

    async def async_close(self) -> None:
        """Close the HTTP session."""
        await self.auth.async_close()
        self.session = None
