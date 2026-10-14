# Written by Claude Sonnet 5.5 (claude-sonnet-5-5), effort level: medium.
"""Constants for the Chandler Signature integration."""

from homeassistant.const import Platform

DOMAIN = "chandler_signature"

CONF_TOKEN = "token"

# Options (config entry options, edited from the integration's Configure dialog)
CONF_AUTO_SYNC = "auto_sync_clock"
CONF_DRIFT_MINUTES = "drift_minutes"
DEFAULT_AUTO_SYNC = True
DEFAULT_DRIFT_MINUTES = 2  # valve clock this many minutes off triggers a sync
MIN_DRIFT_MINUTES = 1
MAX_DRIFT_MINUTES = 120
AUTO_SYNC_COOLDOWN = 3600  # seconds between automatic sync attempts per valve

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]

AUTH_TIMEOUT = 20  # seconds to connect and authenticate
STARTUP_TIMEOUT = 45  # seconds setup waits for the first data
SILENCE_TIMEOUT = 30  # no notification for this long means the link is dead
RETRY_MIN = 5
RETRY_MAX = 120
