# Device-side protocol notes

What the **charger itself** says to the cloud, captured by pointing a real AOC-C022 at the
[capture server](capture-server/) on a LAN address. This is distinct from the *app*-side protocol
the integration speaks, which is documented in the top-level [README](../README.md).

Everything here was observed on real hardware (firmware `1.0.16`, MCU `1.2.0`) unless marked
otherwise.

## BLE provisioning

Corrections to the assumptions the tooling was originally built on:

| | Assumed | Actually observed |
|---|---|---|
| Advertised name | prefix `7231N` | **`AOC-C022-9a`** — model plus the last byte of the WiFi MAC |
| BLE address | — | WiFi MAC **+1** (WiFi `…:71:9a` → BLE `…:71:9B`) |
| ATT MTU | possibly as low as 23, fragmentation feared | **517** negotiated; the 126-byte SetCloudHost frame fits in one write with room to spare |
| Write characteristic | `07af27a5-…` write-without-response | `07af27a5-…` is **write-with-response only**; writing as write-without-response is silently dropped |

Confirmed as documented by [atc1441](https://github.com/atc1441/AOHi_280W_Charger_Hacking): the
`0x55AA` framing, running-sum checksum, big-endian TX / little-endian RX lengths, the GATT UUIDs,
and the `cmd 0x01` / `0x09` payload field offsets.

**The charger only advertises BLE in pairing mode**, which is reached by a factory reset. There is
no way to reach a provisioned, working charger over BLE, so every provisioning experiment costs a
full reset-and-re-add cycle.

**No command reads the current cloud host back.** `0x02, 0x06, 0x07, 0x08, 0x0A, 0x0B, 0x0D, 0x0E,
0x0F, 0x11` were all probed with an empty payload and none replied. Restoring the factory
endpoints is therefore done by factory reset and re-adding in the app, which rewrites them.

## Cloud bootstrap sequence

On boot the charger works through this chain before it will do anything else. Each response shape
below was confirmed by replaying the charger's own captured credentials against AOHI's real cloud —
guessing at the shapes did not work, and a response padded with extra plausible fields was rejected
outright.

### 1. `POST <host1>/iot1/device/login`

```json
{"clientId":"<32 hex>","clientSecret":"<32 hex>",
 "deviceSn":"<32 char serial>","bizuserId":"<digits>"}
```

The charger carries its own baked-in credentials, unrelated to the account login the app uses.

```json
{"code":0,"msg":"ok","data":{"access_token":"<40 hex>","expires_in":604800,
                             "scope":"all","token_type":"Bearer"}}
```

### 2. `GET <host1>/iot1/device/mqtt/info`

Sent with `Authorization: Bearer <access_token>`.

```json
{"code":0,"msg":"ok","data":{"clientId":"dev_<serial>",
                             "username":"<40 hex>","password":"<40 hex>"}}
```

Note the `dev_<serial>` convention: the charger and the app occupy **different** client-id
namespaces (`dev_…` vs `app_<user_id>`), which is why they never evict each other upstream.

Once satisfied, this is requested once and the result is persisted in flash — it is not re-fetched
on subsequent boots.

### 3. `GET <host1>/iot1/time/second`

```json
{"code":0,"msg":"ok","data":1786808622}
```

A bare Unix timestamp, not an object.

### 4. `POST <host1>/iot1/weather/current`

Same body as `device/login`. This is what populates the `city` and `cityTemp` fields that appear in
the app-side `cmd:3` status payload, and what the charger shows on its screen.

```json
{"code":0,"msg":"ok","data":{"city":"France_Lyon",
  "condition":{"text":"Sunny","icon":"//cdn.weatherapi.com/weather/64x64/day/113.png","code":1000},
  "temp_c":37.5,"temp_f":99.5,"time_zone":"Europe/Paris","zone_offset":7200}}
```

Steps 3 and 4 continue on a loop for as long as the charger is running.

## Transport

The two legs have **different** transport requirements, which cost some confusion to establish:

| Leg | Requirement |
|---|---|
| host1 (REST) | plain `http://` is fine — no TLS, no certificate |
| host2 (MQTT) | **`wss://` is mandatory.** Given `ws://` the charger opens no socket at all — silently, with no connection attempt, which is easily mistaken for "the URL was never stored" |

**A self-signed certificate is accepted.** This contradicts atc1441's note that self-signed certs
are rejected; on firmware 1.0.16 the charger completed a TLS 1.2 handshake (`AES256-SHA256`)
against a self-signed cert carrying an IP SAN, and went straight on to MQTT. **No domain and no
certificate authority are needed** — `openssl req -x509` against your LAN IP is enough.

The `ClientHello` offers only legacy RSA suites and sends **no SNI** (expected for an IP literal),
so the certificate's IP SAN is what matters, not its CN.

## Local control: working

> Implemented as the [AOHI Local Server add-on](https://github.com/louisremi/ha-aohi-local-server).
> What follows is what that add-on has to do, and why.

With host1 on plain HTTP and host2 on TLS, the charger completes the whole chain and connects:

```
TLS handshake OK from 192.168.1.35 (TLSv1.2, AES256-SHA256)
WEBSOCKET OPEN /ws/iot1/
MQTT CONNECT protocol=MQIsdp level=3 keepalive=30s
             clientId="dev_<serial>"
             willTopic="lwt/I4SEASON/<serial>"
```

Note it speaks **MQTT 3.1 (`MQIsdp`, protocol level 3)**, not the 3.1.1 the app uses.

It then subscribes to:

- `dev/I4SEASON/<serial>/command/request` (qos 1) — where commands are sent, as with the cloud
- `dev/I4SEASON/<serial>/weather/reply` (qos 0) — not seen from the app side
- `dev/time/sync` (qos 0) — not seen from the app side

and publishes its LWT plus a continuous stream on `dev/I4SEASON/<serial>/command/reply`, in exactly
the payload format the integration already parses — `cmd:1` presence, `cmd:5` device info, and the
partial `cmd:4` telemetry frames.

## Historical note: why this looked impossible for a while

With host1 set to `http://<lan-ip>:8099` and host2 to `ws://<lan-ip>:8099/ws/`, the charger:

- completed every HTTP step above, repeatedly and happily;
- **never opened a WebSocket, and never made any non-HTTP connection at all** — no TLS
  `ClientHello`, nothing. Verified with a `clientError` handler that logs anything arriving on the
  port that isn't well-formed HTTP: 309 HTTP requests, zero others.

Both conclusions drawn from that at the time turned out to be **wrong**, and are recorded here as a
warning:

- *"Since it is not attempting the connection, this is not a TLS-rejection problem."* — false. It
  was precisely a transport problem: the firmware discards a `ws://` URL without ever opening a
  socket, so "refuses to use it" and "never stored it" produce identical evidence.
- *"host2 was probably never stored, so the 3 × 39-byte offsets must be wrong."* — false. The
  offsets are exactly as documented; host2 was stored correctly the whole time.

This was resolved by provisioning host1, host2 and host3 to **three different ports** in one
reprovision, with raw TCP listeners on the latter two. host2's port immediately received a TLS
`ClientHello`; host3's received nothing. That established in a single reset that host2 is stored
exactly where assumed, that `wss://` is required, and that host3 is unused.

The lesson worth keeping: a silent absence of connections is ambiguous evidence. Distinguishing
"never stored" from "stored but unusable" needed an experiment where the two predict *different*
observations, not more staring at the same log.

## Restoring a charger

Factory reset, then re-add in the AOHI app. The app rewrites the cloud hosts as part of normal
provisioning, so the original URLs never need to be known. Home Assistant entities are keyed on the
serial number and come back by themselves.
