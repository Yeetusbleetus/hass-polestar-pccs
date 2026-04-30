"""Polestar ID OAuth2 + PKCE client.

Ports src/auth.ts from the polestar-mvp reference client. The flow is:

1. Hit ``$ISSUER/.well-known/openid-configuration`` to discover the
   authorization and token endpoints.
2. Generate a PKCE verifier/challenge and a random state, build the
   authorization URL, and have the user complete login in a browser.
3. The browser fails to follow the ``polestar-explore://`` redirect; the user
   pastes that URL back. Extract ``code`` (verify ``state``) and POST it to
   the token endpoint together with the verifier.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp
import async_timeout

from .const import (
    OAUTH_CLIENT_ID,
    OAUTH_ISSUER,
    OAUTH_REDIRECT_URI,
    OAUTH_SCOPES,
)

if TYPE_CHECKING:
    pass


class PolestarPccsAuthError(Exception):
    """Authentication failed (bad credentials, mismatched state, etc.)."""


class PolestarPccsConnectionError(Exception):
    """Network error talking to Polestar ID."""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def new_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for a fresh PKCE pair."""
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def new_state() -> str:
    """Return a fresh random OAuth `state` (also reused for `nonce`)."""
    return _b64url(secrets.token_bytes(16))


async def discover_endpoints(session: aiohttp.ClientSession) -> dict[str, str]:
    """Return the OIDC discovery document (authorization_endpoint, token_endpoint, …)."""
    url = f"{OAUTH_ISSUER}/.well-known/openid-configuration"
    try:
        async with async_timeout.timeout(10):
            response = await session.get(url)
            response.raise_for_status()
            return await response.json()
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise PolestarPccsConnectionError(f"OIDC discovery failed: {exc}") from exc


def build_authorization_url(
    authorization_endpoint: str,
    state: str,
    nonce: str,
    code_challenge: str,
) -> str:
    """Build the authorization URL the user must open in a browser."""
    params = {
        "response_type": "code",
        "client_id": OAUTH_CLIENT_ID,
        "redirect_uri": OAUTH_REDIRECT_URI,
        "scope": " ".join(OAUTH_SCOPES),
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "login",
        "cookie_banner": "disable",
    }
    return f"{authorization_endpoint}?{urlencode(params)}"


def parse_redirect_url(redirected_url: str, expected_state: str) -> str:
    """Validate the polestar-explore://… URL and return the auth `code`.

    Raises PolestarPccsAuthError on missing code or state mismatch.
    """
    parsed = urlparse(redirected_url.strip())
    qs = parse_qs(parsed.query)
    code = qs.get("code", [None])[0]
    returned_state = qs.get("state", [None])[0]
    if not code:
        raise PolestarPccsAuthError("no `code` in redirected URL")
    if returned_state != expected_state:
        raise PolestarPccsAuthError("OAuth state mismatch")
    return code


async def exchange_code_for_tokens(
    session: aiohttp.ClientSession,
    token_endpoint: str,
    code: str,
    code_verifier: str,
) -> dict:
    """Exchange the auth code for tokens. Returns a normalized token dict."""
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": OAUTH_REDIRECT_URI,
        "client_id": OAUTH_CLIENT_ID,
        "code_verifier": code_verifier,
    }
    try:
        async with async_timeout.timeout(15):
            response = await session.post(
                token_endpoint,
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if response.status in (400, 401, 403):
                text = await response.text()
                raise PolestarPccsAuthError(
                    f"token exchange failed: {response.status} {text}"
                )
            response.raise_for_status()
            tokens = await response.json()
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise PolestarPccsConnectionError(f"token exchange failed: {exc}") from exc

    return _normalize(tokens)


async def refresh_tokens(
    session: aiohttp.ClientSession,
    token_endpoint: str,
    refresh_token: str,
) -> dict:
    """Refresh the access token. Returns a normalized token dict."""
    body = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": OAUTH_CLIENT_ID,
    }
    try:
        async with async_timeout.timeout(15):
            response = await session.post(
                token_endpoint,
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if response.status in (400, 401, 403):
                text = await response.text()
                raise PolestarPccsAuthError(
                    f"token refresh failed: {response.status} {text}"
                )
            response.raise_for_status()
            tokens = await response.json()
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise PolestarPccsConnectionError(f"token refresh failed: {exc}") from exc

    return _normalize(tokens, fallback_refresh_token=refresh_token)


def _normalize(raw: dict, *, fallback_refresh_token: str | None = None) -> dict:
    """Convert a raw token response into our stored shape (with `expires_at`)."""
    expires_at = int(time.time()) + int(raw["expires_in"]) - 30
    return {
        "access_token": raw["access_token"],
        "refresh_token": raw.get("refresh_token") or fallback_refresh_token,
        "id_token": raw.get("id_token"),
        "token_type": raw.get("token_type", "Bearer"),
        "expires_at": expires_at,
    }
