# AOHI Smart Charger for Home Assistant

A custom [Home Assistant](https://www.home-assistant.io/) integration for AOHI's WiFi-connected
smart USB chargers (confirmed working with the **AOHI Future AI 280W Smart Desktop Charger**,
model `AOC-C022`).

> **Unofficial project.** This integration was built by reverse-engineering the traffic of the
> official AOHI Android app. It is not affiliated with, endorsed by, or supported by AOHI or
> I4SEASON (the underlying IoT platform vendor). Use at your own risk.

## Features

- Config flow setup (email / password / country — the same credentials as the AOHI app)
- One master **Power** switch per charger
- One switch per USB port (`C1`-`C4`, `A1`-`A2`)
- A **Charging Mode** selector (Turbo / Smart / Custom)
- Sensors:
  - power (W) per USB port
  - total output power (W)
  - device temperature (°C)
  - WiFi signal strength (dBm, diagnostic)
- Serial number and MAC address shown on the device info page
- Automatically picks up additional chargers added to your AOHI account later — no restart
  needed, just pair the new device in the AOHI app and it shows up as a new device in
  Home Assistant within about 30 seconds

## How it works

The AOHI app talks to a cloud backend at `iotservice.iaohi.com`:

1. `POST /iot1/user/login` — plain email/password login, returns a bearer token
2. `GET /iot1/device/list` — lists the chargers on your account
3. `GET /iot1/mqtt/userinfo` — returns credentials for the realtime channel
4. `wss://iotservice.iaohi.com/ws/iot1/` — an **MQTT-over-WebSocket** connection (subprotocol
   `mqtt`) used for both status updates and control, under topics named
   `dev/I4SEASON/<device-serial>/...`

This integration re-implements that same flow using [paho-mqtt](https://pypi.org/project/paho-mqtt/)
for the realtime channel, wired into Home Assistant's `DataUpdateCoordinator` for polling.

## Installation

### HACS (recommended)

1. In HACS, add this repository as a custom repository (category: Integration)
2. Install "AOHI Smart Charger"
3. Restart Home Assistant

### Manual

1. Copy `custom_components/aohi_charger` into your Home Assistant `config/custom_components/`
   directory
2. Restart Home Assistant

## Configuration

Settings → Devices & Services → Add Integration → search for "AOHI Smart Charger", then enter
the same email, password, and country you use in the AOHI mobile app.

## Supported devices

Confirmed on the AOHI Future AI 280W Smart Desktop Charger (`AOC-C022`). Other AOHI/I4SEASON
WiFi devices that share the same app and cloud API may work too, since the port layout is read
dynamically from the device's own status payload — but only 6-port (`C1-C4` + `A1-A2`) desktop
chargers have actually been tested. Please open an issue (ideally with a packet capture) if you
try it on something else.

## Experimental: local control research

The integration above needs AOHI's cloud. [`tools/`](tools/) holds early, **entirely optional**
research towards running without it, based on [atc1441's finding][atc] that the charger can be
told over BLE to talk to a different server:

- a Web Bluetooth tool to repoint the charger (and put it back), and
- a capture server that logs what the charger says, so the protocol can be worked out.

Neither has been tested against real hardware yet, and there's an unresolved question about
whether the firmware will accept an unencrypted local endpoint at all. **Nothing here is required
for normal use** — read [`tools/README.md`](tools/README.md) before touching it.

[atc]: https://github.com/atc1441/AOHi_280W_Charger_Hacking

## Contributing

Issues and pull requests are welcome. If you're reverse-engineering a related AOHI/I4SEASON
device, a WebSocket/MQTT capture of the AOHI app talking to it is the most useful thing you can
attach to an issue.

## License

[MIT](LICENSE)
