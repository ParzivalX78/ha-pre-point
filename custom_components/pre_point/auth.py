"""PRE Point OAuth authentication and token lifecycle."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import logging
import re
import secrets
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import AUTH_BASE, CLIENT_ID, DOMAIN, REALM, REDIRECT_URI

_LOGGER = logging.getLogger(__name__)
OIDC = f"{AUTH_BASE}/auth/realms/{REALM}/protocol/openid-connect"
AUTH_RETRY_DELAY_SECONDS = 15 * 60


class PREPointError(Exception):
    """General PRE Point API error."""


class PreAuthError(PREPointError):
    """Authentication failure; reauth_required signals manual intervention."""

    def __init__(self, message: str, *, reauth_required: bool = False) -> None:
        super().__init__(message)
        self.reauth_required = reauth_required


class PreInvalidCredentialsError(PreAuthError):
    """Credentials were explicitly rejected; do not retry automatically."""

    def __init__(self, message: str = "PRE odmítlo uživatelské jméno nebo heslo.") -> None:
        super().__init__(message, reauth_required=True)


class PREPointAuth:
    """Own OAuth login, refresh tokens, and bounded retry policy.

    Explicit credential rejection requires manual reauthentication immediately.
    Transient/network/server errors allow exactly one retry after 15 minutes.
    """

    def __init__(
        self, hass: HomeAssistant, entry_id: str | None, username: str, password: str
    ) -> None:
        self.hass = hass
        self.username = username
        self.password = password
        self._token_store: Store | None = (
            Store(hass, 1, f"{DOMAIN}_{entry_id}_auth", private=True)
            if entry_id
            else None
        )
        self._stored_token_loaded = False
        self.session: aiohttp.ClientSession | None = None
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.expiry = 0.0
        self.lock = asyncio.Lock()
        self._auth_retry_at: float | None = None
        self._auth_retry_used = False
        self._auth_locked = False

    async def _http(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            )
        return self.session

    @staticmethod
    def _parse_form(page: str, page_url: str) -> tuple[str, dict[str, str]]:
        form = re.search(r"<form\b([^>]*)>(.*?)</form>", page, re.I | re.S)
        if not form:
            raise PREPointError(
                "Přihlašovací stránka neobsahuje formulář; mohl se změnit login "
                "nebo může být vyžadováno MFA/CAPTCHA."
            )
        attrs, body = form.groups()
        action_match = re.search(r"""action=["']([^"']+)["']""", attrs, re.I)
        action = (
            urljoin(page_url, html.unescape(action_match.group(1)))
            if action_match
            else page_url
        )
        fields: dict[str, str] = {}
        for item in re.finditer(r"<input\b([^>]*)>", body, re.I | re.S):
            raw = item.group(1)
            name = re.search(r"""name=["']([^"']+)["']""", raw, re.I)
            value = re.search(r"""value=["']([^"']*)["']""", raw, re.I)
            if name:
                fields[html.unescape(name.group(1))] = (
                    html.unescape(value.group(1)) if value else ""
                )
        return action, fields

    @staticmethod
    def _looks_like_bad_credentials(page: str) -> bool:
        """Recognize common Keycloak/identity-provider credential error text."""
        text = re.sub(r"<[^>]+>", " ", html.unescape(page)).lower()
        text = re.sub(r"\s+", " ", text)
        patterns = (
            "invalid username or password",
            "invalid user credentials",
            "invalid username",
            "invalid password",
            "incorrect username or password",
            "incorrect password",
            "bad credentials",
            "neplatné uživatelské jméno nebo heslo",
            "nesprávné uživatelské jméno nebo heslo",
        )
        return any(pattern in text for pattern in patterns)

    async def _load_stored_refresh_token(self) -> None:
        """Load a previously stored refresh token once."""
        if self._stored_token_loaded:
            return
        self._stored_token_loaded = True
        if self._token_store is None:
            return
        try:
            stored = await self._token_store.async_load()
        except Exception as err:
            _LOGGER.warning(
                "Could not load stored PRE Point auth state (error type: %s).",
                type(err).__name__,
            )
            return
        if stored and isinstance(stored.get("refresh_token"), str):
            self.refresh_token = stored["refresh_token"]

    async def _save_tokens(self, data: dict) -> None:
        self.access_token = data["access_token"]
        self.refresh_token = data.get("refresh_token", self.refresh_token)
        self.expiry = time.time() + int(data.get("expires_in", 300)) - 30
        self._auth_retry_at = None
        self._auth_retry_used = False
        self._auth_locked = False
        if self.refresh_token and self._token_store is not None:
            try:
                await self._token_store.async_save(
                    {"refresh_token": self.refresh_token}
                )
            except Exception as err:
                _LOGGER.warning(
                    "Could not persist PRE Point refresh token (error type: %s).",
                    type(err).__name__,
                )

    async def _clear_stored_refresh_token(self) -> None:
        self.refresh_token = None
        if self._token_store is None:
            return
        try:
            await self._token_store.async_remove()
        except Exception as err:
            _LOGGER.warning(
                "Could not remove stored PRE Point refresh token (error type: %s).",
                type(err).__name__,
            )

    async def _login(self) -> None:
        http = await self._http()
        verifier = secrets.token_urlsafe(64)[:96]
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        params = {
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": "openid",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        async with http.get(f"{OIDC}/auth?{urlencode(params)}") as resp:
            page = await resp.text()
            page_url = str(resp.url)
            if resp.status >= 500:
                raise PREPointError(
                    f"PRE autorizační server je dočasně nedostupný (HTTP {resp.status})."
                )
            if resp.status >= 400:
                raise PreAuthError(
                    f"Autorizační server PRE vrátil HTTP {resp.status}; "
                    "ověřte stav služby nebo nastavení integrace.",
                    reauth_required=True,
                )

        action, fields = self._parse_form(page, page_url)
        fields["username"] = self.username
        fields["password"] = self.password
        async with http.post(action, data=fields, allow_redirects=False) as resp:
            status = resp.status
            location = resp.headers.get("Location")
            body = await resp.text()

        if self._looks_like_bad_credentials(body):
            raise PreInvalidCredentialsError()

        if status >= 500:
            raise PREPointError(
                f"PRE přihlašovací server je dočasně nedostupný (HTTP {status})."
            )
        if status not in (302, 303, 307, 308) or not location:
            if status in (400, 401, 403):
                raise PreAuthError(
                    f"PRE odmítlo přihlášení (HTTP {status}); je nutné ověřit "
                    "přihlašovací údaje nebo stav účtu.",
                    reauth_required=True,
                )
            raise PREPointError(
                f"Přihlášení nevrátilo OAuth redirect (HTTP {status}); "
                "může být vyžadováno MFA/CAPTCHA nebo se změnil formulář."
            )

        current = urljoin(action, location)
        code = None
        redirect = urlparse(REDIRECT_URI)
        for _ in range(10):
            parsed = urlparse(current)
            if parsed.scheme == redirect.scheme and parsed.netloc == redirect.netloc:
                query = parse_qs(parsed.query)
                if query.get("state", [None])[0] != state:
                    raise PREPointError("OAuth state nesouhlasí; kód byl odmítnut.")
                if query.get("error"):
                    oauth_error = query["error"][0]
                    if oauth_error in ("access_denied", "invalid_grant"):
                        raise PreAuthError(
                            f"PRE vrátilo chybu ověření: {oauth_error}. "
                            "Je nutné ruční přihlášení.",
                            reauth_required=True,
                        )
                    raise PREPointError(f"PRE vrátilo OAuth chybu: {oauth_error}")
                code = query.get("code", [None])[0]
                break
            async with http.get(current, allow_redirects=False) as resp:
                if resp.status >= 500:
                    raise PREPointError(
                        f"PRE server selhal během přesměrování (HTTP {resp.status})."
                    )
                next_url = resp.headers.get("Location")
                if not next_url:
                    raise PREPointError(
                        f"Redirect skončil na {urlparse(current).netloc} "
                        f"(HTTP {resp.status}); autorizační kód nebyl předán."
                    )
            current = urljoin(current, next_url)

        if not code:
            raise PREPointError("OAuth autorizační kód nebyl nalezen.")

        async with http.post(
            f"{OIDC}/token",
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
                "code": code,
                "code_verifier": verifier,
            },
        ) as resp:
            data = await resp.json(content_type=None)
            if resp.status in (400, 401, 403) and data.get("error") in (
                "invalid_grant", "unauthorized_client"
            ):
                raise PreAuthError(
                    "PRE odmítlo ověření přihlašovací relace. Je nutné ruční přihlášení.",
                    reauth_required=True,
                )
            if resp.status >= 500:
                raise PREPointError(
                    f"PRE token server je dočasně nedostupný (HTTP {resp.status})."
                )
            if resp.status >= 400 or not data.get("access_token"):
                raise PREPointError(
                    f"Výměna autorizačního kódu selhala (HTTP {resp.status})."
                )
        await self._save_tokens(data)

    async def _login_with_limited_retry(self) -> None:
        if self._auth_locked:
            raise PreAuthError(
                "Automatické přihlašování PRE Point je zastavené. "
                "Přihlaste se znovu ručně v Home Assistantu.",
                reauth_required=True,
            )

        retrying = self._auth_retry_at is not None
        if retrying and not self._auth_retry_used:
            remaining = self._auth_retry_at - time.monotonic()
            if remaining > 0:
                minutes = max(1, int((remaining + 59) // 60))
                raise PreAuthError(
                    "Přihlášení PRE Point selhalo. Další a poslední automatický "
                    f"pokus proběhne nejdříve za přibližně {minutes} min."
                )
            self._auth_retry_used = True

        try:
            _LOGGER.info("PRE Point: přihlášení heslem")
            await self._login()
        except PreInvalidCredentialsError:
            self._auth_locked = True
            self._auth_retry_at = None
            self._auth_retry_used = False
            _LOGGER.error(
                "PRE Point explicitly rejected credentials. Automatic retries "
                "are disabled; manual reauthentication is required."
            )
            raise
        except PreAuthError as err:
            if err.reauth_required:
                self._auth_locked = True
                self._auth_retry_at = None
                self._auth_retry_used = False
                _LOGGER.error("PRE Point requires manual reauthentication.")
                raise
            await self._schedule_or_lock_retry(err, retrying)
        except (aiohttp.ClientError, asyncio.TimeoutError, PREPointError) as err:
            await self._schedule_or_lock_retry(err, retrying)
        except Exception:
            # Unexpected programming/runtime errors are not treated as a server
            # outage, so they do not trigger repeated credential submissions.
            raise

    async def _schedule_or_lock_retry(self, err: Exception, retrying: bool) -> None:
        if retrying or self._auth_retry_used:
            self._auth_locked = True
            _LOGGER.error(
                "PRE Point transient authentication failure persisted after the "
                "single delayed retry; manual reauthentication is required "
                "(error type: %s).",
                type(err).__name__,
            )
            raise PreAuthError(
                "PRE Point se nepodařilo přihlásit ani při jediném opakovaném "
                "pokusu. Automatické pokusy jsou zastavené; ověřte stav služby "
                "a přihlaste se znovu ručně v Home Assistantu.",
                reauth_required=True,
            ) from None

        self._auth_retry_at = time.monotonic() + AUTH_RETRY_DELAY_SECONDS
        self._auth_retry_used = False
        _LOGGER.warning(
            "PRE Point transient authentication failure. One retry is scheduled "
            "in 15 minutes; no login attempts will be made before then "
            "(error type: %s).",
            type(err).__name__,
        )
        raise PreAuthError(
            "Přihlášení PRE Point dočasně selhalo. Jediný opakovaný pokus proběhne "
            "za 15 minut; do té doby se znovu přihlašovat nebude."
        ) from None

    async def async_get_access_token(self) -> str:
        async with self.lock:
            await self._load_stored_refresh_token()
            if self._auth_locked:
                raise PreAuthError(
                    "Automatické přihlašování je zastavené. Je nutné ruční "
                    "opětovné přihlášení.",
                    reauth_required=True,
                )
            if self.access_token and time.time() < self.expiry:
                return self.access_token

            http = await self._http()
            if self.refresh_token:
                try:
                    async with http.post(
                        f"{OIDC}/token",
                        data={
                            "grant_type": "refresh_token",
                            "client_id": CLIENT_ID,
                            "refresh_token": self.refresh_token,
                        },
                    ) as resp:
                        data = await resp.json(content_type=None)
                        if resp.status < 400 and data.get("access_token"):
                            await self._save_tokens(data)
                            _LOGGER.info("PRE Point: přihlášení obnoveno uloženým tokenem")
                            return self.access_token or ""
                        if resp.status >= 500:
                            raise PREPointError(
                                f"PRE token server je dočasně nedostupný (HTTP {resp.status})."
                            )
                        # A rejected/expired refresh token requires a new login.
                        await self._clear_stored_refresh_token()
                except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                    raise PREPointError(
                        "Síťová chyba při obnově PRE Point tokenu; další pokus "
                        "proběhne při příští aktualizaci."
                    ) from err
                except PREPointError:
                    raise

            return await self._get_token_by_login()

    async def _get_token_by_login(self) -> str:
        await self._login_with_limited_retry()
        if not self.access_token:
            raise PreAuthError("PRE Point nevrátilo access token.")
        return self.access_token

    async def async_forget_stored_tokens(self) -> None:
        """Forget persisted refresh credentials for this config entry."""
        await self._clear_stored_refresh_token()
        self.access_token = None
        self.expiry = 0.0

    async def async_close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()
