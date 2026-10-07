"""Constants of the Household Assistant integration."""
DOMAIN = "household_assistant"
VERSION = "1.0.0"                       # manifest.json's version

CONF_VOICE_USER = "voice_user"          # the HA user a question without one (a voice satellite) is asked as
CONF_TIMEOUT = "timeout"                # seconds to wait for the answer
DEFAULT_TIMEOUT = 300
ACK_SECONDS = 15                        # the app takes a question at once, or it isn't there
