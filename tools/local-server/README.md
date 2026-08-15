# AOHI local server

Runs AOHI chargers **entirely on your own network** — no cloud, no account, no internet.

> Experimental. Getting a charger here requires a factory reset and BLE reprovisioning, and it
> will no longer work with the official AOHI app until you reset it back. The cloud integration
> in this repository does not need any of this.

## Why it works

The charger talks to two configurable endpoints, both settable over BLE
([details](../PROTOCOL.md)):

| | | |
|---|---|---|
| host1 | REST bootstrap | plain `http://` is fine |
| host2 | MQTT over WebSocket | **`wss://` required** — plain `ws://` is silently ignored |

The firmware does **not verify the certificate**, so a self-signed one is enough. No domain, no
certificate authority. This server generates its own on first run.

## Run it

```bash
LAN_IP=192.168.1.166 docker compose up --build
```

`LAN_IP` must be the address your chargers can reach, and it is baked into the certificate's
IP SAN. Then provision a charger with the
[BLE tool](../aohi-ble-provisioning.html):

| Field | Value |
|---|---|
| host1 | `http://<LAN_IP>:8099` |
| host2 | `wss://<LAN_IP>:8098/ws/` |

Both must be ≤38 characters, which a LAN IP satisfies comfortably.

## What it provides

- **The four bootstrap endpoints** the charger requires before it will connect to a broker:
  `device/login`, `device/mqtt/info`, `time/second`, `weather/current`.
- **An MQTT broker** over TLS WebSocket that actually routes between clients, speaking both
  MQTT 3.1 (the charger) and 3.1.1 (Home Assistant).
- **Last-will handling**, so a charger dropping off announces itself offline.
- **`GET /local/devices`** — chargers self-register when they connect, since their client id is
  `dev_<serial>`. This replaces the cloud's `device/list` for discovery.

Weather is served from static values (`WEATHER_CITY`, `WEATHER_TZ`, `WEATHER_OFFSET`); it only
feeds the charger's own display.

## Talking to it

Topics and payloads are unchanged from the cloud, so anything that speaks the AOHI protocol works:

```
dev/I4SEASON/<serial>/command/request   publish commands here
dev/I4SEASON/<serial>/command/reply     status and telemetry arrive here
lwt/I4SEASON/<serial>                   presence
```

Connect as a normal MQTT client to `wss://<LAN_IP>:8098/ws/` with certificate verification
disabled. Credentials are not checked.

## Returning a charger to the cloud

Factory reset it and re-add it in the AOHI app; the app rewrites the endpoints itself.
