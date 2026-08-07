"""Constants for the AOHI Smart Charger integration."""

DOMAIN = "aohi_charger"

CONF_COUNTRY = "country"

API_BASE_URL = "https://iotservice.iaohi.com"
MQTT_HOST = "iotservice.iaohi.com"
MQTT_WS_PATH = "/ws/iot1/"
MQTT_PORT = 443

UPDATE_INTERVAL_SECONDS = 30
MQTT_REQUEST_TIMEOUT = 10

CPORTS = ("C1", "C2", "C3", "C4")
APORTS = ("A1", "A2")
ALL_PORTS = CPORTS + APORTS


def signal_new_device(entry_id: str) -> str:
    """Dispatcher signal fired when a new device is discovered under this entry."""
    return f"{DOMAIN}_new_device_{entry_id}"
