# Written by Claude Sonnet 5.5 (claude-sonnet-5-5), effort level: medium.
"""Constants for the Chandler Signature integration."""

from homeassistant.const import Platform

DOMAIN = "chandler_signature"

CONF_TOKEN = "token"

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]

AUTH_TIMEOUT = 20  # seconds to connect and authenticate
STARTUP_TIMEOUT = 45  # seconds setup waits for the first data
SILENCE_TIMEOUT = 30  # no notification for this long means the link is dead
RETRY_MIN = 5
RETRY_MAX = 120
