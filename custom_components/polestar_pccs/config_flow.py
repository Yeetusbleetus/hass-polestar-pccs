"""Config flow for Polestar (PCCS).

Two-step setup:

1. ``user``  — ask for the VIN.
2. ``login`` — render the Polestar ID authorization URL, ask the user to
   sign in and paste the ``polestar-explore://…`` URL their browser fails
   to redirect to. We extract the auth code, exchange it for tokens, and
   create the config entry.

The PKCE verifier and OAuth state are kept in flow-handler instance
attributes between the two steps.

Reauthentication (``reauth`` → ``reauth_confirm``) re-runs the login step
against the existing entry when the stored refresh token dies, updating the
tokens in place so entities and history survive.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    PolestarPccsAuthError,
    PolestarPccsConnectionError,
    build_authorization_url,
    discover_endpoints,
    exchange_code_for_tokens,
    new_pkce,
    new_state,
    parse_redirect_url,
)
from .const import (
    CONF_SCAN_INTERVAL,
    CONF_TOKENS,
    CONF_VIN,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    LOGGER,
    MIN_SCAN_INTERVAL_SECONDS,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

VIN_LENGTH = 17

REDIRECTED_URL_SCHEMA = vol.Schema(
    {
        vol.Required("redirected_url"): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.TEXT,
                multiline=True,
            ),
        ),
    },
)


class PolestarPccsFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Polestar (PCCS)."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> PolestarPccsOptionsFlow:
        """Return the options flow handler."""
        return PolestarPccsOptionsFlow()

    def __init__(self) -> None:
        """Initialize transient flow state."""
        self._vin: str | None = None
        self._code_verifier: str | None = None
        self._state: str | None = None
        self._auth_url: str | None = None
        self._token_endpoint: str | None = None

    async def _async_begin_login(self) -> str | None:
        """Generate PKCE/state and build the authorization URL.

        Returns an error key ("connection") on failure, None on success.
        """
        verifier, challenge = new_pkce()
        state = new_state()
        nonce = new_state()
        self._code_verifier = verifier
        self._state = state

        session = async_get_clientsession(self.hass)
        try:
            discovery = await discover_endpoints(session)
        except PolestarPccsConnectionError as exc:
            LOGGER.error("OIDC discovery failed: %s", exc)
            return "connection"

        self._token_endpoint = discovery["token_endpoint"]
        self._auth_url = build_authorization_url(
            discovery["authorization_endpoint"], state, nonce, challenge
        )
        return None

    async def _async_exchange(self, redirected_url: str) -> dict[str, Any]:
        """Extract the auth code from the pasted URL and trade it for tokens."""
        assert self._token_endpoint is not None  # noqa: S101 — set by _async_begin_login
        assert self._code_verifier is not None  # noqa: S101
        assert self._state is not None  # noqa: S101

        code = parse_redirect_url(redirected_url, self._state)
        session = async_get_clientsession(self.hass)
        return await exchange_code_for_tokens(
            session, self._token_endpoint, code, self._code_verifier
        )

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Step 1: collect the VIN, then build the authorization URL."""
        errors: dict[str, str] = {}

        if user_input is not None:
            vin = user_input[CONF_VIN].strip().upper()
            if len(vin) != VIN_LENGTH:
                errors[CONF_VIN] = "invalid_vin"
            else:
                await self.async_set_unique_id(vin)
                self._abort_if_unique_id_configured()

                self._vin = vin
                error = await self._async_begin_login()
                if error:
                    errors["base"] = error
                else:
                    return await self.async_step_login()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_VIN,
                        default=(user_input or {}).get(CONF_VIN, vol.UNDEFINED),
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.TEXT,
                        ),
                    ),
                },
            ),
            errors=errors,
        )

    async def async_step_login(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Step 2: render the auth URL and consume the redirected URL."""
        errors: dict[str, str] = {}
        assert self._vin is not None  # noqa: S101 — set in step 1
        assert self._auth_url is not None  # noqa: S101

        if user_input is not None:
            try:
                tokens = await self._async_exchange(user_input["redirected_url"])
            except PolestarPccsAuthError as exc:
                LOGGER.warning("Polestar ID auth failed: %s", exc)
                errors["base"] = "auth"
            except PolestarPccsConnectionError as exc:
                LOGGER.error("Polestar ID connection error: %s", exc)
                errors["base"] = "connection"
            else:
                return self.async_create_entry(
                    title=f"Polestar {self._vin[-6:]}",
                    data={
                        CONF_VIN: self._vin,
                        CONF_TOKENS: tokens,
                    },
                )

        return self.async_show_form(
            step_id="login",
            description_placeholders={"auth_url": self._auth_url},
            data_schema=REDIRECTED_URL_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self,
        entry_data: Mapping[str, Any],
    ) -> config_entries.ConfigFlowResult:
        """Start reauth: triggered by ConfigEntryAuthFailed in the coordinator."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Re-run the Polestar ID login and store fresh tokens on the entry."""
        errors: dict[str, str] = {}

        if self._auth_url is None:
            error = await self._async_begin_login()
            if error:
                # Can't even build the login URL — abort; HA re-opens the
                # reauth flow on the next failed poll.
                return self.async_abort(reason="cannot_connect")

        if user_input is not None:
            try:
                tokens = await self._async_exchange(user_input["redirected_url"])
            except PolestarPccsAuthError as exc:
                LOGGER.warning("Polestar ID reauth failed: %s", exc)
                errors["base"] = "auth"
            except PolestarPccsConnectionError as exc:
                LOGGER.error("Polestar ID connection error: %s", exc)
                errors["base"] = "connection"
            else:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates={CONF_TOKENS: tokens},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            description_placeholders={"auth_url": self._auth_url},
            data_schema=REDIRECTED_URL_SCHEMA,
            errors=errors,
        )


class PolestarPccsOptionsFlow(config_entries.OptionsFlow):
    """Options flow for tunable runtime settings (currently: poll cadence)."""

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Show / accept the polling-interval option.

        The integration's update listener calls async_reload_entry whenever
        options change, which rebuilds the coordinator with the new interval —
        no extra plumbing needed here.
        """
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL, default=current
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL_SECONDS,
                            step=1,
                            unit_of_measurement="s",
                            mode=selector.NumberSelectorMode.BOX,
                        ),
                    ),
                },
            ),
        )
