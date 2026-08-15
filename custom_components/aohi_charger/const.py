"""Constants for the AOHI Smart Charger integration."""

DOMAIN = "aohi_charger"

CONF_COUNTRY = "country"

# A config entry is either a cloud account or a local server; the two are
# separate entries so one charger can stay on the cloud while another runs
# locally.
CONF_MODE = "mode"
MODE_CLOUD = "cloud"
MODE_LOCAL = "local"

CONF_HOST = "host"
CONF_HTTP_PORT = "http_port"
CONF_MQTT_PORT = "mqtt_port"

DEFAULT_HTTP_PORT = 8099
DEFAULT_MQTT_PORT = 8098

API_BASE_URL = "https://iotservice.iaohi.com"
MQTT_HOST = "iotservice.iaohi.com"
MQTT_WS_PATH = "/ws/iot1/"
MQTT_PORT = 443

UPDATE_INTERVAL_SECONDS = 30
MQTT_REQUEST_TIMEOUT = 10

CPORTS = ("C1", "C2", "C3", "C4")
APORTS = ("A1", "A2")
ALL_PORTS = CPORTS + APORTS

# Charging mode, as shown in the app's mode picker on the device screen.
# The exact behavioral difference between them isn't documented by AOHI;
# these are just the labels the app itself uses.
MODES = {1: "Turbo", 2: "Smart", 3: "Custom"}


def signal_new_device(entry_id: str) -> str:
    """Dispatcher signal fired when a new device is discovered under this entry."""
    return f"{DOMAIN}_new_device_{entry_id}"
