"""Constants of the Household Assistant integration."""
DOMAIN = "household_assistant"
VERSION = "1.0.2"                       # manifest.json's version

CONF_VOICE_USER = "voice_user"          # the HA user a question without one (a voice satellite) is asked as
CONF_VOICE_PERSON_ONLY = "voice_person_only"   # voice satellites: only tools about that person (no household-wide)
CONF_SIGNING_KEY = "signing_key"        # the household apps' app-messages signing key ("" = unsigned)
CONF_TIMEOUT = "timeout"                # seconds to wait for the answer
DEFAULT_TIMEOUT = 300
ACK_SECONDS = 15                        # the app takes a question at once, or it isn't there
HELLO_EVERY = 60                        # seconds: answer the app's hello at most this often
RECORDER_ISSUE = "recorder_keeps_messages"
