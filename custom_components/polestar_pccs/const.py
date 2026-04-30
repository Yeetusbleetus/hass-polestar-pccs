"""Constants for polestar_pccs."""

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "polestar_pccs"
ATTRIBUTION = "Data provided by Polestar Connected Car Service"

# Config entry keys
CONF_VIN = "vin"
CONF_TOKENS = "tokens"

# Options keys
CONF_SCAN_INTERVAL = "scan_interval"

# Polling cadence (seconds). The default matches what most cloud-polling car
# integrations ship; the floor of 10 s is enough headroom that a user can dial
# in a fast cadence while charging without hammering the C3 backend with
# sub-second requests.
DEFAULT_SCAN_INTERVAL_SECONDS = 300
MIN_SCAN_INTERVAL_SECONDS = 10

# Polestar ID OAuth (issuer + client lifted from PolestarIdAuthImpl$signIn$2$a.smali
# in the decompiled com.polestar.explore APK).
OAUTH_ISSUER = "https://polestarid.eu.polestar.com"
OAUTH_CLIENT_ID = "lp8dyrd_10"
OAUTH_REDIRECT_URI = "polestar-explore://explore.polestar.com"
OAUTH_SCOPES = ("openid", "profile", "email", "customer:attributes")
