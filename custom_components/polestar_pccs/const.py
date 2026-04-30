"""Constants for polestar_pccs."""

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "polestar_pccs"
ATTRIBUTION = "Data provided by Polestar Connected Car Service"

# Config entry keys
CONF_VIN = "vin"
CONF_TOKENS = "tokens"

# Polestar ID OAuth (issuer + client lifted from PolestarIdAuthImpl$signIn$2$a.smali
# in the decompiled com.polestar.explore APK).
OAUTH_ISSUER = "https://polestarid.eu.polestar.com"
OAUTH_CLIENT_ID = "lp8dyrd_10"
OAUTH_REDIRECT_URI = "polestar-explore://explore.polestar.com"
OAUTH_SCOPES = ("openid", "profile", "email", "customer:attributes")
