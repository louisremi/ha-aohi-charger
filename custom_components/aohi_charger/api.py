"""API client for the AOHI smart charger cloud service.

Reverse-engineered from the official AOHI Android app (package
com.i4season.desktop_charger). Login and device listing use a plain REST
API; live status and control go over MQTT, tunnelled through a WebSocket
connection to the same host.
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
from collections import defaultdict
from typing import Any
from uuid import uuid4

import paho.mqtt.client as mqtt
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    API_BASE_URL,
    MQTT_HOST,
    MQTT_PORT,
    MQTT_REQUEST_TIMEOUT,
    MQTT_WS_PATH,
)

_LOGGER = logging.getLogger(__name__)


class AohiApiError(Exception):
    """Generic AOHI API error."""


class AohiAuthError(AohiApiError):
    """Authentication with the AOHI cloud failed."""


class AohiApiClient:
    """Wraps AOHI's REST login/device-list calls and the MQTT control channel."""

    def __init__(
        self, hass: HomeAssistant, email: str, password: str, country: str
    ) -> None:
        self._hass = hass
        self._session = async_get_clientsession(hass)
        self._email = email
        self._password = password
        self._country = country

        self.access_token: str | None = None
        self.user_id: int | None = None

        self._mqtt_client: mqtt.Client | None = None
        self._mqtt_connected: asyncio.Event = asyncio.Event()
        self._pending: dict[tuple[str, int], asyncio.Future] = {}
        self._locks: dict[tuple[str, int], asyncio.Lock] = defaultdict(asyncio.Lock)
        self._latest_status: dict[str, dict] = {}
        self._known_sns: set[str] = set()

        # Must not collide with anything else on AOHI's broker. The official app
        # connects as "app_<user_id>", and MQTT requires the broker to evict the
        # existing session whenever a second client claims the same id -- so
        # reusing it made the integration and the phone app disconnect each other
        # in a loop. Random per instance, so two HA installs can't clash either.
        # Sessions are clean, so nothing stale is left behind on reconnect.
        unique = uuid4().hex[:12]
        self._client_id = f"ha-aohi-{unique}"
        # Sent as the "user" field on requests. The device echoes it back on
        # command acknowledgements, which is how we tell our own acks apart from
        # the unsolicited telemetry it publishes on the same cmd number.
        self._user_tag = f"ha_{unique}"

    # -- REST -----------------------------------------------------------

    async def async_login(self) -> None:
        """Authenticate and store the access token."""
        try:
            async with self._session.post(
                f"{API_BASE_URL}/iot1/user/login",
                json={
                    "email": self._email,
                    "password": self._password,
                    "country": self._country,
                },
                headers={"language": "en"},
            ) as resp:
                data = await resp.json(content_type=None)
        except asyncio.TimeoutError as err:
            raise AohiApiError("Timed out contacting AOHI login endpoint") from err

        if data.get("code") != 0:
            raise AohiAuthError(data.get("msg", "login failed"))

        self.access_token = data["data"]["access_token"]
        self.user_id = data["data"]["user_id"]

    def _auth_headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.access_token}", "language": "en"}

    async def async_get_devices(self) -> list[dict[str, Any]]:
        """Return the flat list of devices across all of the account's rooms."""
        if not self.access_token:
            await self.async_login()

        async with self._session.get(
            f"{API_BASE_URL}/iot1/device/list", headers=self._auth_headers()
        ) as resp:
            data = await resp.json(content_type=None)

        if data.get("code") != 0:
            raise AohiApiError(data.get("msg", "failed to list devices"))

        devices: list[dict[str, Any]] = []
        for room in data.get("data") or []:
            devices.extend(room.get("devices", []))
        return devices

    async def async_get_mqtt_credentials(self) -> tuple[str, str]:
        """Fetch the (username, password) pair used to authenticate on the MQTT broker."""
        async with self._session.get(
            f"{API_BASE_URL}/iot1/mqtt/userinfo", headers=self._auth_headers()
        ) as resp:
            data = await resp.json(content_type=None)

        if data.get("code") != 0:
            raise AohiApiError(data.get("msg", "failed to get mqtt credentials"))

        return data["data"]["username"], data["data"]["password"]

    # -- MQTT -------------------------------------------------------------

    async def async_connect_mqtt(self, device_sns: list[str]) -> None:
        """Open the shared MQTT-over-websocket connection and subscribe to device topics."""
        username, password = await self.async_get_mqtt_credentials()
        client_id = self._client_id

        try:
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
                client_id=client_id,
                transport="websockets",
                reconnect_on_failure=True,
            )
        except (AttributeError, TypeError):
            # paho-mqtt < 2.0 has no callback_api_version parameter.
            client = mqtt.Client(
                client_id=client_id, transport="websockets", reconnect_on_failure=True
            )

        client.ws_set_options(path=MQTT_WS_PATH)
        client.username_pw_set(username, password)
        # ssl.create_default_context() reads and parses the system CA bundle
        # from disk, which is a blocking operation the event loop disallows.
        ssl_context = await self._hass.async_add_executor_job(ssl.create_default_context)
        client.tls_set_context(ssl_context)

        loop = self._hass.loop
        self._mqtt_connected.clear()
        self._known_sns = set(device_sns)

        def on_connect(client, userdata, flags, rc):
            if rc != 0:
                _LOGGER.error("AOHI MQTT connect failed: rc=%s", rc)
                return
            # Re-subscribes to every device known so far, including ones
            # discovered after the initial connect (important on reconnect).
            for sn in self._known_sns:
                client.subscribe(f"dev/I4SEASON/{sn}/command/reply")
                client.subscribe(f"dev/I4SEASON/{sn}/notify")
            loop.call_soon_threadsafe(self._mqtt_connected.set)

        def on_disconnect(client, userdata, rc):
            loop.call_soon_threadsafe(self._mqtt_connected.clear)

        def on_message(client, userdata, msg):
            loop.call_soon_threadsafe(self._handle_message, msg.topic, msg.payload)

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message

        await self._hass.async_add_executor_job(
            client.connect, MQTT_HOST, MQTT_PORT, 60
        )
        client.loop_start()
        self._mqtt_client = client

        try:
            await asyncio.wait_for(
                self._mqtt_connected.wait(), timeout=MQTT_REQUEST_TIMEOUT
            )
        except asyncio.TimeoutError as err:
            raise AohiApiError("Timed out connecting to the AOHI MQTT broker") from err

    async def async_subscribe_devices(self, device_sns: list[str]) -> None:
        """Subscribe to topics for devices discovered after the initial connect."""
        if not self._mqtt_client:
            raise AohiApiError("MQTT client not connected")
        for sn in device_sns:
            if sn in self._known_sns:
                continue
            self._known_sns.add(sn)
            self._mqtt_client.subscribe(f"dev/I4SEASON/{sn}/command/reply")
            self._mqtt_client.subscribe(f"dev/I4SEASON/{sn}/notify")

    def _handle_message(self, topic: str, payload: bytes) -> None:
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            return

        # Topic shape: dev/I4SEASON/<sn>/command/reply
        parts = topic.split("/")
        if len(parts) < 3:
            return
        sn = parts[2]
        cmd = data.get("cmd")
        result = data.get("result", {})

        if cmd == 3:
            self._latest_status[sn] = result
        elif cmd == 4:
            # The device also pushes cmd:4 continuously, unprompted, carrying
            # partial state ({"cPorts":[{"name":"C1","power":63}]}). Merge those
            # into the cache so the port payloads we build stay current.
            self._merge_partial_state(sn, result)

        # cmd:4 is ambiguous: it is both the acknowledgement of a control command
        # and the unsolicited telemetry above. Only our own acks echo our user
        # tag (telemetry carries user ""), so without this check a passing
        # telemetry frame would resolve a control request before the device had
        # actually done anything. cmd:3/cmd:5 replies always carry user "",
        # whatever we sent, so they can only be matched on the cmd number.
        if cmd == 4 and data.get("user") != self._user_tag:
            return

        fut = self._pending.pop((sn, cmd), None)
        if fut and not fut.done():
            fut.set_result(data)

    def _merge_partial_state(self, sn: str, result: dict) -> None:
        """Apply a partial telemetry update onto the cached status for one device."""
        status = self._latest_status.setdefault(sn, {})
        for key, value in result.items():
            if key in ("cPorts", "aPorts") and isinstance(value, list):
                ports = {p.get("name"): p for p in status.get(key, [])}
                for update in value:
                    ports.setdefault(update.get("name"), {}).update(update)
                status[key] = list(ports.values())
            else:
                status[key] = value

    async def _async_request(
        self,
        sn: str,
        request_cmd: int,
        reply_cmd: int,
        extra: dict | None = None,
    ) -> dict:
        if not self._mqtt_client:
            raise AohiApiError("MQTT client not connected")

        async with self._locks[(sn, reply_cmd)]:
            loop = self._hass.loop
            fut: asyncio.Future = loop.create_future()
            self._pending[(sn, reply_cmd)] = fut

            payload: dict[str, Any] = {"cmd": request_cmd, "user": self._user_tag}
            if extra:
                payload["data"] = extra

            self._mqtt_client.publish(
                f"dev/I4SEASON/{sn}/command/request", json.dumps(payload)
            )

            try:
                return await asyncio.wait_for(fut, timeout=MQTT_REQUEST_TIMEOUT)
            except asyncio.TimeoutError as err:
                self._pending.pop((sn, reply_cmd), None)
                raise AohiApiError(
                    f"Timed out waiting for AOHI reply to cmd {request_cmd}"
                ) from err

    async def async_get_status(self, sn: str) -> dict:
        """Fetch full status (whole-device + all ports) for one device."""
        reply = await self._async_request(sn, request_cmd=3, reply_cmd=3)
        return reply.get("result", {})

    async def async_get_device_info(self, sn: str) -> dict:
        """Fetch device/WiFi info (vendor, firmware, SSID, RSSI) for one device."""
        reply = await self._async_request(sn, request_cmd=5, reply_cmd=5)
        return reply.get("result", {})

    async def async_set_whole_power(self, sn: str, on: bool) -> None:
        """Turn the entire charger on or off."""
        await self._async_request(
            sn, request_cmd=6, reply_cmd=4, extra={"state": {"poweron": on}}
        )

    async def async_set_mode(self, sn: str, mode: int) -> None:
        """Switch the charger between its Turbo/Smart/Custom charging modes."""
        await self._async_request(
            sn, request_cmd=6, reply_cmd=4, extra={"state": {"mode": mode}}
        )

    async def async_set_port_power(self, sn: str, port_name: str, on: bool) -> None:
        """Turn a single port on or off, mirroring the app's exact payload shape."""
        key = "cPorts" if port_name.startswith("C") else "aPorts"
        current = self._find_port(sn, key, port_name) or {}
        port_state = {
            "name": port_name,
            "voltage": current.get("voltage", 0),
            "current": current.get("current", 0),
            "power": current.get("power", 0),
            "poweron": on,
            "targetPower": 0,
            "pdVersion": None,
        }
        await self._async_request(
            sn, request_cmd=6, reply_cmd=4, extra={"state": {key: [port_state]}}
        )

    def _find_port(self, sn: str, key: str, port_name: str) -> dict | None:
        status = self._latest_status.get(sn, {})
        for port in status.get(key, []):
            if port.get("name") == port_name:
                return port
        return None

    async def async_disconnect(self) -> None:
        """Tear down the MQTT connection."""
        if self._mqtt_client:
            client = self._mqtt_client
            self._mqtt_client = None
            await self._hass.async_add_executor_job(client.disconnect)
            client.loop_stop()
