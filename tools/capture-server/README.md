# AOHI capture server

A throwaway diagnostic server that impersonates AOHI's cloud, so we can find out **what the
charger itself says**. It logs every HTTP request and every MQTT-over-WebSocket frame verbatim.

## Why this exists

The integration was built by intercepting the **Android app's** traffic. We have never seen the
**device's** side of the conversation: what it POSTs to the REST host, what credentials it uses
on the broker, or whether it needs a successful registration before it will connect at all.

None of that can be guessed. It has to be captured — which is what this is for. Whatever it
records is the input for building a real local server.

## Usage

```bash
docker compose up --build
```

Then use [the BLE provisioning tool](../aohi-ble-provisioning.html) to point the charger at this
machine:

| Field | Value |
|---|---|
| host1 | `http://<your-lan-ip>:8080` |
| host2 | `ws://<your-lan-ip>:8080/ws/` |

Both must be **≤38 characters**, which a LAN IP comfortably satisfies. Power-cycle the charger
after provisioning, then watch the logs:

```bash
docker compose logs -f
# or
tail -f logs/capture.log
```

To run it without Docker: `npm install && LOG_DIR=./logs node server.js`
(`PORT` defaults to 8080).

## What it does

- **HTTP** — logs the request line, all headers, and the body (hex + text), then answers
  `{"code":0,"msg":"ok","data":{}}`, mirroring the envelope the real AOHI API uses.
- **WebSocket** — logs every frame as a hexdump and decodes the MQTT packet type. `CONNECT` is
  the interesting one: it reveals the device's client id, username and password.
- Replies with the minimum MQTT needed to keep a session alive — `CONNACK`, `SUBACK`, `PUBACK`,
  `PINGRESP` — so the charger keeps talking instead of giving up after the handshake.
- Accepts any path, any credentials, any topic.

Logs are written synchronously, so a `docker compose down` or Ctrl-C can't lose a capture you
might not be able to repeat.

## The open question this is meant to answer

atc1441 reports the firmware requires a **CA-signed certificate** and rejects self-signed ones.
Whether it accepts **plain `http://` / `ws://`** is untested and unknown.

That is the make-or-break question for local control without owning a domain, and this server is
how you answer it:

- **Device connects** → plain transport is fine; local control needs nothing but a LAN IP.
- **Device never connects** → TLS is mandatory, and the next question becomes which CA it trusts.

Either way you learn the answer definitively, and the log tells you exactly how far it got.

### If TLS turns out to be mandatory

A Let's Encrypt certificate is a normal publicly-trusted CA-signed certificate and should qualify.
The awkward parts are the domain and the trust store, not the CA:

- Certificates are issued for **names**, so `192.168.1.100` is out. You need a domain you control,
  a DNS-01 challenge, and an A record pointing at your LAN IP — plus renewal automation.
- Keep the hostname short: the URL field holds **38 characters**, so `wss://aohi.example.com/ws/`
  fits while `wss://aohi.myname.duckdns.org:443/ws/iot1/` does not.
- **Unknown: which roots the device trusts.** Both published firmware dumps contain no certificate
  material at all — the dumped HC32F460 is only the UI/charging MCU, and it reaches the network
  over UART through a **BK7231N** WiFi module whose firmware has never been dumped. The CA bundle
  lives there. Embedded devices often ship a small, frozen bundle, so a valid Let's Encrypt cert
  could still be rejected if ISRG Root X1 isn't in it.
- There is at least **no pinning** (per atc1441), so if one issuer is rejected another may work.

## Warnings

- **Do not expose this to the internet.** It accepts anything from anyone and authenticates
  nothing. It is a debugging aid, not a server.
- Captures will contain credentials and your WiFi SSID — scrub them before sharing.
