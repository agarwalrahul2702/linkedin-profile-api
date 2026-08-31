"""
Thin async client around LinkedIn's internal "Voyager" API.

Voyager is the private JSON API LinkedIn's own web frontend calls after you're
logged in (linkedin.com/voyager/api/...). It is undocumented and unofficial;
this client mimics the requests a logged-in browser session makes — directly
over HTTP, with no browser involved — using existing session cookies for auth.
"""

import json
import re
from typing import Any, Optional
from urllib.parse import unquote, urlsplit

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from . import config


class LinkedInError(Exception):
    """Base class for expected LinkedIn client failures."""


class LinkedInAuthError(LinkedInError):
    """Session cookies are missing, expired, or rejected by LinkedIn."""


class LinkedInNotFoundError(LinkedInError):
    """Profile does not exist or is not accessible with the current session."""


class LinkedInRateLimitError(LinkedInError):
    """LinkedIn is throttling/blocking this session or IP."""


class LinkedInUpstreamError(LinkedInError):
    """LinkedIn or the network failed in a way the caller can retry later."""


class _TransientLinkedInError(LinkedInUpstreamError):
    """Internal marker for failures that should be retried automatically."""


_PUBLIC_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9_-]{1,100}")


def extract_public_identifier(profile_url: str) -> str:
    """
    Pull the public identifier (the '/in/<this-part>/') out of a LinkedIn
    profile URL. Raises ValueError if the URL doesn't look like a profile URL.
    """
    value = profile_url.strip()
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid LinkedIn profile URL.") from exc

    hostname = (parsed.hostname or "").lower().rstrip(".")
    is_linkedin_host = hostname == "linkedin.com" or hostname.endswith(".linkedin.com")
    if (
        parsed.scheme.lower() != "https"
        or not is_linkedin_host
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise ValueError(
            "Expected an HTTPS LinkedIn profile URL such as "
            "https://www.linkedin.com/in/some-name/."
        )

    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 2 or path_parts[0].lower() != "in":
        raise ValueError(
            "Could not find a LinkedIn public identifier. Expected a /in/<identifier>/ URL."
        )

    public_identifier = unquote(path_parts[1])
    if not _PUBLIC_IDENTIFIER_RE.fullmatch(public_identifier):
        raise ValueError(
            "The LinkedIn public identifier contains unsupported characters."
        )
    return public_identifier


class LinkedInClient:
    """
    Async client. Meant to be created once (e.g. at app startup) and reused
    across requests via app.state, since it holds a pooled httpx.AsyncClient.
    """

    def __init__(
        self,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        *,
        li_at: Optional[str] = None,
        jsessionid: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        session_token = config.LI_AT if li_at is None else li_at.strip()
        if not session_token:
            raise LinkedInAuthError(
                "LI_AT is not configured. Set it in your "
                "environment (see .env.example) before calling the API."
            )

        cookies = {"li_at": session_token}
        headers = {
            "x-restli-protocol-version": "2.0.0",
            "x-li-lang": "en_US",
            "accept": "application/json",
            "user-agent": user_agent or config.LINKEDIN_USER_AGENT,
        }
        configured_jsessionid = config.JSESSIONID if jsessionid is None else jsessionid
        self._csrf_token = configured_jsessionid.strip().strip('"').strip("'")
        if self._csrf_token:
            # Voyager expects the ajax token value as the CSRF header. Config
            # accepts values copied with or without surrounding quotes.
            cookies["JSESSIONID"] = f'"{self._csrf_token}"'
            headers["csrf-token"] = self._csrf_token

        self._client = httpx.AsyncClient(
            base_url=config.VOYAGER_BASE_URL,
            cookies=cookies,
            headers=headers,
            timeout=config.REQUEST_TIMEOUT_SECONDS,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "LinkedInClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def _ensure_csrf_token(self) -> None:
        """Get LinkedIn's non-auth JSESSIONID cookie when only li_at was supplied."""
        if self._csrf_token:
            return
        try:
            # Send this bootstrap request without li_at. LinkedIn's classic
            # login page issues JSESSIONID before authentication; the existing
            # li_at remains in the client's jar for subsequent Voyager calls.
            response = await self._client.get(
                f"{config.LINKEDIN_BASE_URL}/checkpoint/lg/login",
                headers={
                    "accept": (
                        "text/html,application/xhtml+xml,application/xml;q=0.9,"
                        "*/*;q=0.8"
                    ),
                    "cookie": "",
                },
            )
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise _TransientLinkedInError(
                "Could not bootstrap LinkedIn's CSRF cookie."
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise LinkedInUpstreamError(
                "LinkedIn rejected the CSRF bootstrap request."
            ) from exc

        values = [
            cookie.value
            for cookie in self._client.cookies.jar
            if cookie.name == "JSESSIONID"
        ]
        if not values:
            raise LinkedInUpstreamError(
                "LinkedIn did not issue the expected JSESSIONID CSRF cookie."
            )
        self._csrf_token = values[-1].strip().strip('"').strip("'")
        self._client.headers["csrf-token"] = self._csrf_token

    @retry(
        retry=retry_if_exception_type(_TransientLinkedInError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    async def get_raw_profile(self, public_identifier: str) -> dict:
        """
        Hit the decorated identity/dash/profiles endpoint, which returns the
        profile and related entities as standard or normalized JSON. Transient failures
        (timeouts, 5xx) are retried with exponential backoff; auth/not-found/
        rate-limit failures are not, since retrying won't help.
        """
        await self._ensure_csrf_token()

        decoration_ids = (config.PROFILE_DECORATION_ID,) + tuple(
            decoration_id
            for decoration_id in config.PROFILE_DECORATION_FALLBACK_IDS
            if decoration_id != config.PROFILE_DECORATION_ID
        )
        response: Optional[httpx.Response] = None
        attempted_decorations = []

        for index, decoration_id in enumerate(decoration_ids):
            attempted_decorations.append(decoration_id)
            # The current -109 decoration is standard JSON (elements[]). The
            # older -101 fallback is normally exposed as normalized JSON
            # (data + included[]), so request its native representation.
            accept = (
                "application/vnd.linkedin.normalized+json+2.1"
                if decoration_id.endswith("-101")
                else "application/json"
            )
            try:
                response = await self._client.get(
                    "/identity/dash/profiles",
                    params={
                        "q": "memberIdentity",
                        "memberIdentity": public_identifier,
                        "decorationId": decoration_id,
                    },
                    headers={"accept": accept},
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise _TransientLinkedInError("Could not reach LinkedIn.") from exc

            has_fallback = index + 1 < len(decoration_ids)
            if response.status_code in (400, 410) and has_fallback:
                continue
            break

        if response is None:  # Defensive; config always supplies a primary ID.
            raise LinkedInUpstreamError("No LinkedIn profile decoration is configured.")

        if response.status_code in (302, 401):
            raise LinkedInAuthError(
                f"LinkedIn rejected the session (HTTP {response.status_code}). "
                "The li_at session is likely expired or invalid."
            )
        if response.status_code == 403:
            health = await self.check_session_health()
            if health["valid"]:
                raise LinkedInNotFoundError(
                    f"Profile '{public_identifier}' is not visible to this session."
                )
            raise LinkedInAuthError(
                "LinkedIn rejected the session (HTTP 403). The li_at session "
                "is likely expired or invalid."
            )
        if response.status_code == 404:
            raise LinkedInNotFoundError(
                f"No profile found for identifier '{public_identifier}'."
            )
        if response.status_code in (400, 410):
            versions = ", ".join(attempted_decorations)
            raise LinkedInUpstreamError(
                "LinkedIn rejected all configured profile schema versions "
                f"(HTTP {response.status_code}; tried {versions}). "
                "The Voyager decoration IDs likely changed."
            )
        if response.status_code == 429:
            raise LinkedInRateLimitError(
                "LinkedIn is rate-limiting/throttling this session. Back off "
                "and retry later; avoid high request volume from one account."
            )
        if response.status_code >= 500:
            raise _TransientLinkedInError(
                f"LinkedIn returned HTTP {response.status_code}."
            )

        if response.status_code >= 400:
            raise LinkedInUpstreamError(
                f"LinkedIn returned an unexpected HTTP {response.status_code}."
            )

        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise LinkedInUpstreamError("LinkedIn returned malformed JSON.") from exc
        if not isinstance(payload, dict):
            raise LinkedInUpstreamError(
                "LinkedIn returned an unexpected response shape."
            )
        data = payload.get("data")
        if isinstance(data, dict) and data.get("status") in (404, 410):
            raise LinkedInNotFoundError(
                f"No profile found for identifier '{public_identifier}'."
            )
        return payload

    async def check_session_health(self) -> dict:
        """
        Lightweight call to confirm the configured session cookies are still
        valid, without doing a full profile fetch. Useful to surface "your
        cookies expired" clearly instead of failing mysteriously mid-request.
        """
        try:
            await self._ensure_csrf_token()
            response = await self._client.get("/me")
        except LinkedInError as exc:
            return {"valid": False, "detail": str(exc)}
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            return {"valid": False, "detail": f"Request failed: {exc}"}

        if response.status_code == 200:
            return {"valid": True, "detail": "Session is active."}
        if response.status_code in (302, 401, 403):
            return {
                "valid": False,
                "detail": f"HTTP {response.status_code} — cookies are likely expired or invalid.",
            }
        return {
            "valid": False,
            "detail": f"Unexpected HTTP {response.status_code} from LinkedIn.",
        }
