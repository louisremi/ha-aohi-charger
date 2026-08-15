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

import aiohttp
import paho.mqtt.client as mqtt
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    API_BASE_URL,
    MODE_LOCAL,
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
        self,
        hass: HomeAssistant,
        email: str = "",
        password: str = "",
        country: str = "",
        *,
        mode: str = "cloud",
        host: str = "",
        http_port: int = 0,
        mqtt_port: int = 0,
    ) -> None:
        self._hass = hass
        self._session = async_get_clientsession(hass)
        self._email = email
        self._password = password
        self._country = country

        # Local mode talks to the AOHI Local Server add-on instead of AOHI's cloud
        # (github.com/louisremi/ha-aohi-local-server). The
        # MQTT half of this class is identical either way -- same topics, same
        # payloads -- so only connection setup and discovery branch.
        self._mode = mode
        self._host = host
        self._http_port = http_port
        self._mqtt_port = mqtt_port

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

    @property
    def is_local(self) -> bool:
        """True when this client talks to a local server rather than the cloud."""
        return self._mode == MODE_LOCAL

    async def async_login(self) -> None:
        """Authenticate and store the access token. A no-op in local mode."""
        if self.is_local:
            # The local server authenticates nobody; there is no account.
            self.user_id = 0
            return
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

    async def _async_get_authed(self, path: str, what: str) -> dict:
        """GET an authenticated endpoint, logging in again if the token has gone stale.

        Tokens last 7 days and there is no refresh endpoint, so without the retry
        the integration would break permanently once one expired, until someone
        reloaded it by hand.
        """
        if not self.access_token:
            await self.async_login()

        for attempt in (1, 2):
            try:
                async with self._session.get(
                    f"{API_BASE_URL}{path}", headers=self._auth_headers()
                ) as resp:
                    status = resp.status
                    data = await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
                # Covers network blips and non-JSON bodies (an upstream 502 page,
                # say). Raised as AohiApiError so the coordinator treats it as a
                # normal retryable update failure rather than an unexpected crash.
                raise AohiApiError(f"failed to {what}: {err}") from err

            # An expired token comes back as HTTP 200 with {"code":-1,
            # "msg":"login needed"}, not a 401, so the code check is what
            # actually catches it; the status check is just belt and braces.
            if status not in (401, 403) and data.get("code") == 0:
                return data

            if attempt == 1:
                _LOGGER.debug(
                    "Re-authenticating after failed %s (status %s, body %s)",
                    what, status, data,
                )
                await self.async_login()
                continue

            raise AohiApiError(data.get("msg") or f"failed to {what}")

        raise AohiApiError(f"failed to {what}")   # unreachable, keeps type checkers happy

    async def async_get_devices(self) -> list[dict[str, Any]]:
        """Return every charger this entry can see."""
        if self.is_local:
            return await self._async_get_local_devices()

        data = await self._async_get_authed("/iot1/device/list", "list devices")

        devices: list[dict[str, Any]] = []
        for room in data.get("data") or []:
            devices.extend(room.get("devices", []))
        return devices

    async def _async_get_local_devices(self) -> list[dict[str, Any]]:
        """Ask the local server which chargers have registered with it.

        Chargers self-register when they connect, because their MQTT client id
        is ``dev_<serial>``. The reply carries far less than the cloud's device
        list -- no model, firmware or friendly name -- so those are filled in
        afterwards from the device's own cmd:5 reply.
        """
        url = f"http://{self._host}:{self._http_port}/local/devices"
        try:
            async with self._session.get(url) as resp:
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            raise AohiApiError(f"cannot reach the local server at {url}: {err}") from err

        if data.get("code") != 0:
            raise AohiApiError(data.get("msg") or "local server rejected the request")

        return [
            {"sn": d["sn"], "name": f"AOHI {d['sn'][-6:]}", "model": None, "version": None}
            for d in data.get("data") or []
            if d.get("sn")
        ]

    async def async_enrich_local_devices(self, devices: dict[str, dict[str, Any]]) -> None:
        """Fill in model and firmware from each device's cmd:5 reply.

        Only meaningful in local mode, and only worth doing once at setup, since
        the values are static. Failures are tolerated: a missing model is
        cosmetic, and refusing to set up over it would be worse.
        """
        for sn, device in devices.items():
            try:
                info = await self.async_get_device_info(sn)
            except AohiApiError as err:
                _LOGGER.debug("Could not read device info for %s: %s", sn, err)
                continue
            device["model"] = info.get("p") or device.get("model")
            device["version"] = info.get("ver") or device.get("version")

    async def async_get_mqtt_credentials(self) -> tuple[str, str]:
        """Fetch the (username, password) pair used to authenticate on the MQTT broker."""
        if self.is_local:
            # The local broker accepts anything; send something identifiable.
            return self._client_id, self._client_id
        data = await self._async_get_authed("/iot1/mqtt/userinfo", "get mqtt credentials")
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

        # Local servers use a self-signed certificate -- the charger does not
        # verify it either -- so hostname/CA checks are switched off for them.
        # This is a LAN connection to a server the user configured by hand.
        host = self._host if self.is_local else MQTT_HOST
        port = self._mqtt_port if self.is_local else MQTT_PORT

        client.ws_set_options(path=MQTT_WS_PATH)
        client.username_pw_set(username, password)
        # paho backs off up to 120s between reconnect attempts by default, which
        # is far longer than a poll interval: one dropped connection would leave
        # every entity unavailable for minutes. Keep retries brisk.
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        # ssl.create_default_context() reads and parses the system CA bundle
        # from disk, which is a blocking operation the event loop disallows.
        ssl_context = await self._hass.async_add_executor_job(ssl.create_default_context)
        if self.is_local:
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
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
            # Logged loudly on purpose: a silent drop here is indistinguishable
            # from a device problem, and it is the first thing worth knowing when
            # entities go unavailable. paho reconnects automatically afterwards.
            if rc != 0:
                _LOGGER.warning(
                    "AOHI MQTT connection lost (rc=%s); reconnecting automatically", rc
                )
            else:
                _LOGGER.debug("AOHI MQTT disconnected cleanly")
            loop.call_soon_threadsafe(self._mqtt_connected.clear)

        def on_message(client, userdata, msg):
            loop.call_soon_threadsafe(self._handle_message, msg.topic, msg.payload)

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message

        await self._hass.async_add_executor_job(client.connect, host, port, 60)
        client.loop_start()
        self._mqtt_client = client

        try:
            await asyncio.wait_for(
                self._mqtt_connected.wait(), timeout=MQTT_REQUEST_TIMEOUT
            )
        except asyncio.TimeoutError as err:
            where = f"local broker at {host}:{port}" if self.is_local else "AOHI MQTT broker"
            raise AohiApiError(f"Timed out connecting to the {where}") from err

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

        # paho reconnects on its own, but publishing mid-reconnect drops the
        # message silently and we'd then wait out the full reply timeout and fail
        # the whole update, briefly marking every entity unavailable. Wait for the
        # link to come back instead of firing into a closed socket.
        if not self._mqtt_connected.is_set():
            _LOGGER.debug("MQTT link is down; waiting for reconnect before sending")
            try:
                await asyncio.wait_for(
                    self._mqtt_connected.wait(), timeout=MQTT_REQUEST_TIMEOUT
                )
            except asyncio.TimeoutError as err:
                raise AohiApiError(
                    "MQTT connection is down and did not come back in time"
                ) from err

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
