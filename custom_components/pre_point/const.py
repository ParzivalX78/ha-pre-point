"""Constants for the PRE Point integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "pre_point"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"

# Sign-in (used by auth.py).
AUTH_BASE = "https://auth-smatrics.verbund.com"
REALM = "pre-b2c"
CLIENT_ID = "mobile-app-cli"
REDIRECT_URI = "cz.premobilita.auth://login"

# Charging history of the account.
API_BASE = "https://b2c-service.smatrics-api.com"
SESSIONS_PATH = "/api/v1/customers/customer/sessions"
# Billing months the history covers besides the current one (same as the app).
HISTORY_PERIODS = 11

# Public charger map. The API key is the one the public website hands to every
# visitor; it is read from the page at runtime instead of being stored here.
POI_BASE = "https://poi-service.smatrics-api.com"
POI_KEY_PAGE = "https://chargepre.smatrics.com/cs/"
POI_OPERATOR = "CZ*PRE"

REQUEST_TIMEOUT = 30

CONF_LOCATIONS = "locations"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_RADIUS = "radius"
CONF_QUERY = "query"
CONF_PRICE_AC = "price_ac"
CONF_PRICE_DC = "price_dc"
CONF_PRICE_UFC = "price_ufc"
CONF_MONTHLY_FEE = "monthly_fee"
CONF_RECALCULATE = "recalculate"

# PRE Point "Start" tariff (CZK incl. VAT); editable in the options.
DEFAULT_PRICE_AC = 9.0
DEFAULT_PRICE_DC = 12.0
DEFAULT_PRICE_UFC = 14.0
DEFAULT_MONTHLY_FEE = 0.0
CURRENCY = "CZK"
# Power limit between the DC and UFC tariff bands.
DC_MAX_KW = 149.0

DEFAULT_RADIUS_KM = 15
DEFAULT_SCAN_INTERVAL = 600
MIN_SCAN_INTERVAL = 300
MAX_SCAN_INTERVAL = 3600
MAX_BACKOFF = 3600

# The history changes a few times a month and shows up about an hour after
# charging ends; station details (names, power) change even more rarely.
HISTORY_INTERVAL = timedelta(hours=1)
LOCATION_DETAILS_INTERVAL = timedelta(hours=24)

EVSE_STATES = [
    "available",
    "charging",
    "reserved",
    "blocked",
    "out_of_order",
    "unavailable",
    "unknown",
]
